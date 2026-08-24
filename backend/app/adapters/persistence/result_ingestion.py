"""SQL adapter for the atomic canonical result-ingestion write port."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ...database_connection import ConnectionLike, connect
from ...domains.results.models import ResultIngestionCommand
from ...domains.results.ports import ResultIngestionUnitOfWork
from ...media_policy import validate_media_metadata
from ...repositories.variable_catalog import VariableCatalogRepository
from ...services.media_storage_service import attach_stored_media, store_file
from ...services.request_monitoring import sync_request_status


AuthorizationCallback = Callable[[ResultIngestionCommand, ConnectionLike], object]


def _allow_ingestion(
    _command: ResultIngestionCommand,
    _connection: ConnectionLike,
) -> None:
    """Default authorization for non-HTTP callers already checked at their boundary."""


class SQLResultIngestionUnitOfWork(ResultIngestionUnitOfWork):
    def __init__(
        self,
        connection: ConnectionLike,
        id_factory: Callable[[str], str],
        authorize: AuthorizationCallback = _allow_ingestion,
    ) -> None:
        self._connection = connection
        self._id_factory = id_factory
        self._authorize = authorize

    def source_completed(self, command: ResultIngestionCommand) -> bool:
        return self._connection.execute(
            """
            SELECT 1 FROM analysis_run_metadata
            WHERE source_type=? AND source_name=? AND source_checksum=?
            LIMIT 1
            """,
            [command["source_type"], command["source_name"], command["source_checksum"]],
        ).fetchone() is not None

    def lock_load_case_ingestion(self, load_case_id: str) -> None:
        """Serialize PostgreSQL canonical imports before allocating ``run_no``.

        ``analysis_runs.run_no`` is allocated by ``max(run_no) + 1``.  The
        transaction-scoped advisory lock protects that existing allocation
        contract for distinct sources of the same load case without changing
        DuckDB's local-development behavior.
        """
        if getattr(self._connection, "backend", "duckdb") != "postgresql":
            return
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
            [f"simdashboard:canonical-result-ingestion:load-case:{load_case_id}"],
        )

    def claim_source_identity(
        self,
        command: ResultIngestionCommand,
        claimed_at: datetime,
    ) -> bool:
        """Reserve a PostgreSQL source identity for this transaction.

        DuckDB intentionally retains its existing preflight-only behavior.  In
        PostgreSQL the primary key serializes competing canonical imports; a
        failed ingestion rolls the reservation back with the rest of the UoW.
        """
        if command["source_checksum"] is None or getattr(self._connection, "backend", "duckdb") != "postgresql":
            return True
        return self._connection.execute(
            """
            INSERT INTO canonical_result_ingestion_sources
                (source_type, source_name, source_checksum, claimed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            RETURNING source_checksum
            """,
            [
                command["source_type"],
                command["source_name"],
                command["source_checksum"],
                claimed_at,
            ],
        ).fetchone() is not None

    def validate_target(self, command: ResultIngestionCommand) -> None:
        actual = self._connection.execute(
            """
            SELECT ar.project_id, lc.request_id
            FROM load_cases lc JOIN analysis_requests ar ON ar.id=lc.request_id
            WHERE lc.id=?
            """,
            [command["load_case_id"]],
        ).fetchone()
        if (
            actual is None
            or str(actual[0]) != command["project_id"]
            or str(actual[1]) != command["request_id"]
        ):
            raise ValueError(
                "결과 bundle의 project_id, request_id, load_case_id 연결이 존재하지 않거나 일치하지 않습니다."
            )

    def authorize(self, command: ResultIngestionCommand) -> None:
        self._authorize(command, self._connection)

    def next_run_no(self, load_case_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT coalesce(max(run_no), 0) + 1 FROM analysis_runs WHERE load_case_id=?",
                [load_case_id],
            ).fetchone()[0]
        )

    def add_skipped_job(
        self,
        job_id: str,
        command: ResultIngestionCommand,
        summary: dict[str, Any],
        created_at: datetime,
    ) -> None:
        parsed = command["parsed"]
        self._connection.execute(
            "INSERT INTO folder_import_jobs VALUES (?, ?, NULL, ?, ?, ?, 'SKIPPED', ?, ?)",
            [
                job_id,
                command["load_case_id"],
                parsed["schema_id"],
                parsed["schema_version"],
                command["source_name"],
                json.dumps(summary, ensure_ascii=False),
                created_at,
            ],
        )

    def add_running_job(
        self,
        job_id: str,
        run_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None:
        parsed = command["parsed"]
        self._connection.execute(
            "INSERT INTO folder_import_jobs VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', NULL, ?)",
            [
                job_id,
                command["load_case_id"],
                run_id,
                parsed["schema_id"],
                parsed["schema_version"],
                command["source_name"],
                created_at,
            ],
        )

    def ensure_catalog(self, command: ResultIngestionCommand) -> None:
        parsed = command["parsed"]
        catalog = VariableCatalogRepository(self._connection)
        descriptions = {
            "scalars": f"{command['actor']} 결과 적재",
            "curves": f"{command['actor']} 커브 적재",
            "media": f"{command['actor']} 미디어 적재",
        }
        for item in parsed["scalars"]:
            self._ensure_variable(
                catalog,
                command["load_case_id"],
                item,
                item["data_type"],
                item["unit"] or "-",
                item["threshold"],
                descriptions["scalars"],
                command["actor"],
            )
        for item in parsed["curves"]:
            self._ensure_variable(
                catalog,
                command["load_case_id"],
                item,
                item.get("catalog_data_type", "CURVE"),
                item["y_unit"] or "-",
                None,
                descriptions["curves"],
                command["actor"],
            )
        for item in parsed["media"]:
            self._ensure_variable(
                catalog,
                command["load_case_id"],
                item,
                item["asset_type"],
                "-",
                None,
                descriptions["media"],
                command["actor"],
            )

    def _ensure_variable(
        self,
        catalog: VariableCatalogRepository,
        load_case_id: str,
        item: dict[str, Any],
        data_type: str,
        unit: str,
        threshold: Any,
        description: str,
        actor: str,
    ) -> None:
        if catalog.get(load_case_id, item["variable_key"], include_inactive=True):
            return
        catalog.create(
            load_case_id,
            {
                "variable_key": item["variable_key"],
                "display_name": item["display_name"],
                "data_type": data_type,
                "unit": unit,
                "description": f"{description}: {item['source_file']}",
                "threshold": threshold,
                "result_group": item["result_group"],
                "updated_by": actor,
            },
        )

    def add_run(
        self,
        run_id: str,
        run_no: int,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                command["load_case_id"],
                None,
                run_no,
                command["parsed"]["solver"],
                "COMPLETED",
                created_at,
                created_at,
            ],
        )

    def add_metadata(
        self,
        run_id: str,
        job_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None:
        parsed = command["parsed"]
        metadata = {
            "job_id": job_id,
            "project_id": command["project_id"],
            "request_id": command["request_id"],
            **command["metadata"],
        }
        self._connection.execute(
            """
            INSERT INTO analysis_run_metadata
                (analysis_run_id, source_type, source_name, source_checksum, schema_id, schema_version,
                 parser_version, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                command["source_type"],
                command["source_name"],
                command["source_checksum"],
                parsed["schema_id"],
                parsed["schema_version"],
                command["parser_version"],
                json.dumps(metadata, ensure_ascii=False),
                created_at,
            ],
        )

    def add_results(
        self,
        run_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None:
        parsed = command["parsed"]
        for item in parsed["scalars"]:
            value_double = item["value"] if item["data_type"] == "FLOAT" else None
            value_integer = item["value"] if item["data_type"] == "INTEGER" else None
            value_text = str(item["value"]) if item["data_type"] not in {"FLOAT", "INTEGER"} else None
            threshold = float(item["threshold"]) if item["threshold"] is not None else None
            if threshold is not None and item["data_type"] in {"FLOAT", "INTEGER"}:
                verdict = "FAIL" if float(item["value"]) >= threshold else "PASS"
            elif item["data_type"] == "VERDICT":
                verdict = str(item["value"])
            else:
                verdict = None
            self._connection.execute(
                "INSERT INTO scalar_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    self._id_factory("scalar"),
                    run_id,
                    item["variable_key"],
                    item["display_name"],
                    value_double,
                    value_integer,
                    value_text,
                    item["unit"],
                    threshold,
                    verdict,
                ],
            )
        for item in parsed["curves"]:
            curve_id = self._id_factory("curve")
            self._connection.execute(
                "INSERT INTO curve_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    curve_id,
                    run_id,
                    item["variable_key"],
                    item["display_name"],
                    item["series_key"],
                    item["x_label"],
                    item["x_unit"],
                    item["y_label"],
                    item["y_unit"],
                    len(item["points"]),
                    item["source_file"],
                    item["source_checksum"],
                    created_at,
                ],
            )
            for index, point in enumerate(item["points"]):
                self._connection.execute(
                    "INSERT INTO curve_points VALUES (?, ?, ?, ?)",
                    [curve_id, index, point["x"], point["y"]],
                )
                self._connection.execute(
                    "INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        run_id,
                        item["variable_key"],
                        item["display_name"],
                        point["x"],
                        point["y"],
                        item["x_unit"],
                        item["y_unit"],
                    ],
                )
        for item in parsed.get("locations", []):
            self._connection.execute(
                "INSERT INTO result_locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    item["variable_key"],
                    item["entity_type"],
                    item["entity_id"],
                    item["x"],
                    item["y"],
                    item["z"],
                    item["time"],
                    item["time_unit"],
                    item["method"],
                ],
            )
        for item in parsed["media"]:
            path = Path(item["path"])
            validate_media_metadata(
                "CONTOUR_IMAGE" if item["asset_type"] == "IMAGE" else item["asset_type"],
                path.name,
                path.stat().st_size,
            )
            asset_id = self._id_factory("media")
            self._connection.execute(
                """
                INSERT INTO media_assets
                    (id, analysis_run_id, asset_type, title, file_path, mime_type,
                     file_size, checksum, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    asset_id,
                    run_id,
                    item["asset_type"],
                    item["display_name"],
                    f"imports/{run_id}/{path.name}",
                    item["mime_type"],
                    path.stat().st_size,
                    item["source_checksum"],
                    json.dumps(
                        {
                            "variable_key": item["variable_key"],
                            "source_file": item["source_file"],
                        }
                    ),
                ],
            )
            stored = store_file(
                self._connection,
                path,
                filename=path.name,
                mime_type=item["mime_type"],
                asset_type=item["asset_type"],
            )
            attach_stored_media(self._connection, asset_id, stored)
        if parsed["note"]:
            self._connection.execute(
                "INSERT INTO qualitative_notes VALUES (?, ?, ?, ?, ?)",
                [self._id_factory("note"), run_id, command["actor"], parsed["note"], created_at],
            )

    def complete_job(self, job_id: str, run_id: str, summary: dict[str, Any]) -> None:
        self._connection.execute(
            "UPDATE folder_import_jobs SET status='COMPLETED', summary_json=?, analysis_run_id=? WHERE id=?",
            [json.dumps(summary, ensure_ascii=False), run_id, job_id],
        )

    def sync_status(self, command: ResultIngestionCommand, completed_at: datetime) -> None:
        self._connection.execute(
            "UPDATE load_cases SET status='COMPLETED' WHERE id=?",
            [command["load_case_id"]],
        )
        self._connection.execute(
            "UPDATE analysis_requests SET status='IN_PROGRESS' WHERE id=?",
            [command["request_id"]],
        )
        self._connection.execute(
            """
            UPDATE request_steps
            SET status='COMPLETED', progress=100, actual_end=?
            WHERE request_id=? AND name IN ('해석 실행', '후처리 작업')
            """,
            [completed_at, command["request_id"]],
        )
        self._connection.execute(
            """
            UPDATE request_steps
            SET status='IN_PROGRESS', progress=greatest(progress, 20),
                actual_start=coalesce(actual_start, ?)
            WHERE request_id=? AND name='결과 검토'
            """,
            [completed_at, command["request_id"]],
        )
        sync_request_status(self._connection, command["request_id"])


class SQLResultIngestionUnitOfWorkProvider:
    def __init__(
        self,
        id_factory: Callable[[str], str],
        authorize: AuthorizationCallback = _allow_ingestion,
        connection_provider: Callable[[], Any] = connect,
    ) -> None:
        self._id_factory = id_factory
        self._authorize = authorize
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ResultIngestionUnitOfWork]:
        with self._connection_provider() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                yield SQLResultIngestionUnitOfWork(
                    connection,
                    self._id_factory,
                    self._authorize,
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise


def bound_result_ingestion_unit_of_work(
    connection: ConnectionLike,
    id_factory: Callable[[str], str],
    authorize: AuthorizationCallback = _allow_ingestion,
) -> Callable[[], Any]:
    """Return a non-owning UoW provider for an already-open transaction.

    HTTP callers sometimes need to keep audit writes and ingestion writes in
    one transaction.  The regular provider owns its connection and transaction
    for folder imports; this provider only scopes the UoW object and leaves
    BEGIN/COMMIT/ROLLBACK to the caller.
    """

    @contextmanager
    def provider() -> Iterator[ResultIngestionUnitOfWork]:
        yield SQLResultIngestionUnitOfWork(connection, id_factory, authorize)

    return provider


@contextmanager
def bound_result_ingestion_transaction(
    connection: ConnectionLike,
    id_factory: Callable[[str], str],
    authorize: AuthorizationCallback = _allow_ingestion,
) -> Iterator[Callable[[], Any]]:
    """Scope a canonical UoW provider to the caller's existing connection.

    This helper owns only the transaction boundaries on the supplied
    connection; the UoW provider itself remains non-owning.  Callers may make
    audit writes through that same connection before the commit.
    """

    connection.execute("BEGIN TRANSACTION")
    try:
        yield bound_result_ingestion_unit_of_work(connection, id_factory, authorize)
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
