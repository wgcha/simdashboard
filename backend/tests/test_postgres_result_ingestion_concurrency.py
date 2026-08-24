from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier, Thread
from typing import Any
from uuid import uuid4

import pytest

from app.adapters.persistence.result_ingestion import SQLResultIngestionUnitOfWorkProvider
from app.application.results.commands import ingest_result_bundle, utc_identifier
from app.config import database_settings
from app.database_connection import connect


pytestmark = pytest.mark.postgres_integration


@pytest.fixture(autouse=True)
def postgres_profile_enabled() -> None:
    enabled = (
        os.getenv("ANALYSIS_TEST_POSTGRES") == "1"
        and os.getenv("ANALYSIS_DB_BACKEND", "").strip().lower() == "postgresql"
    )
    if not enabled:
        pytest.skip("requires ANALYSIS_TEST_POSTGRES=1 and ANALYSIS_DB_BACKEND=postgresql")
    pool = database_settings().postgres_pool
    request_capacity = pool.request_pool_size + pool.request_max_overflow
    if request_capacity < 2:
        pytest.skip(
            "requires at least two independent request-pool connections "
            f"(POSTGRES_REQUEST_POOL_SIZE + POSTGRES_REQUEST_MAX_OVERFLOW = {request_capacity})"
        )


@pytest.fixture
def ingestion_target() -> Iterator[dict[str, str]]:
    suffix = uuid4().hex
    target = {
        "project_id": f"postgres-ingestion-project-{suffix}",
        "request_id": f"postgres-ingestion-request-{suffix}",
        "load_case_id": f"postgres-ingestion-loadcase-{suffix}",
        "source_type": "POSTGRES_CANONICAL_INGESTION_CONCURRENCY",
        "source_name": f"postgres-ingestion/{suffix}/source-a.json",
        "alternate_source_name": f"postgres-ingestion/{suffix}/source-b.json",
        "source_checksum": f"sha256:{suffix}",
        "alternate_source_checksum": f"sha256:alternate-{suffix}",
    }
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with connect() as connection:
            connection.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
                [target["project_id"], "PostgreSQL ingestion concurrency", "pytest", "isolated integration target", now],
            )
            connection.execute(
                """
                INSERT INTO analysis_requests
                    (id, project_id, title, status, owner, requested_at, due_at, overall_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    target["request_id"],
                    target["project_id"],
                    "Canonical ingestion concurrency",
                    "READY",
                    "pytest",
                    now,
                    None,
                    "isolated integration target",
                ],
            )
            connection.execute(
                "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
                [target["load_case_id"], target["request_id"], "concurrent import", "GENERIC", "READY", "{}", now],
            )
        yield target
    finally:
        _cleanup_ingestion_target(target)


def _cleanup_ingestion_target(target: dict[str, str]) -> None:
    """Remove only the rows owned by this target, in foreign-key order."""
    with connect() as connection:
        run_ids = "SELECT id FROM analysis_runs WHERE load_case_id=?"
        curve_ids = f"SELECT id FROM curve_results WHERE analysis_run_id IN ({run_ids})"
        for table, column in (
            ("curve_points", "curve_id"),
            ("result_locations", "analysis_run_id"),
            ("qualitative_notes", "analysis_run_id"),
            ("media_assets", "analysis_run_id"),
            ("time_series_results", "analysis_run_id"),
            ("scalar_results", "analysis_run_id"),
            ("curve_results", "analysis_run_id"),
            ("analysis_run_metadata", "analysis_run_id"),
        ):
            statement = (
                f"DELETE FROM {table} WHERE {column} IN ({curve_ids})"
                if table == "curve_points"
                else f"DELETE FROM {table} WHERE {column} IN ({run_ids})"
            )
            connection.execute(statement, [target["load_case_id"]])
        connection.execute("DELETE FROM folder_import_jobs WHERE load_case_id=?", [target["load_case_id"]])
        connection.execute("DELETE FROM variable_definitions WHERE load_case_id=?", [target["load_case_id"]])
        connection.execute("DELETE FROM analysis_runs WHERE load_case_id=?", [target["load_case_id"]])
        connection.execute(
            """
            DELETE FROM canonical_result_ingestion_sources
            WHERE source_type=?
              AND ((source_name=? AND source_checksum=?) OR (source_name=? AND source_checksum=?))
            """,
            [
                target["source_type"],
                target["source_name"],
                target["source_checksum"],
                target["alternate_source_name"],
                target["alternate_source_checksum"],
            ],
        )
        connection.execute("DELETE FROM request_steps WHERE request_id=?", [target["request_id"]])
        connection.execute("DELETE FROM load_cases WHERE id=?", [target["load_case_id"]])
        connection.execute("DELETE FROM analysis_requests WHERE id=?", [target["request_id"]])
        connection.execute("DELETE FROM projects WHERE id=?", [target["project_id"]])


def _command(target: dict[str, str], source_name: str) -> dict[str, Any]:
    checksum = (
        target["alternate_source_checksum"]
        if source_name == target["alternate_source_name"]
        else target["source_checksum"]
    )
    return {
        "project_id": target["project_id"],
        "request_id": target["request_id"],
        "load_case_id": target["load_case_id"],
        "source_type": target["source_type"],
        "source_name": source_name,
        "source_checksum": checksum,
        "parser_version": "postgres-concurrency-test-v1",
        "parsed": {
            "schema_id": "postgres-canonical-ingestion-concurrency",
            "schema_version": 1,
            "solver": "pytest",
            "scalars": [
                {
                    "variable_key": "concurrent_scalar",
                    "display_name": "Concurrent scalar",
                    "data_type": "FLOAT",
                    "value": 12.5,
                    "unit": "MPa",
                    "threshold": 75.0,
                    "result_group": "CUSTOM",
                    "source_file": source_name,
                }
            ],
            "curves": [],
            "media": [],
            "note": None,
        },
        "actor": "pytest",
        "metadata": {"integration_target": target["load_case_id"]},
    }


def _concurrent_ingestions(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    barrier = Barrier(len(commands))
    outcomes: list[dict[str, Any] | None] = [None] * len(commands)
    failures: list[BaseException | None] = [None] * len(commands)

    @contextmanager
    def independent_connection() -> Iterator[Any]:
        with connect() as connection:
            barrier.wait(timeout=15)
            yield connection

    def ingest(index: int, command: dict[str, Any]) -> None:
        try:
            provider = SQLResultIngestionUnitOfWorkProvider(
                utc_identifier,
                connection_provider=independent_connection,
            )
            outcomes[index] = ingest_result_bundle(
                command,
                provider,
                lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                utc_identifier,
            )
        except BaseException as exc:  # Propagate worker failures in the test thread.
            failures[index] = exc

    threads = [Thread(target=ingest, args=(index, command)) for index, command in enumerate(commands)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads), "concurrent canonical ingestion did not finish"
    assert failures == [None] * len(commands)
    completed_outcomes = [outcome for outcome in outcomes if outcome is not None]
    assert len(completed_outcomes) == len(commands)
    return completed_outcomes


def test_postgres_identical_source_identity_creates_one_canonical_run(ingestion_target: dict[str, str]) -> None:
    outcomes = _concurrent_ingestions(
        [_command(ingestion_target, ingestion_target["source_name"])] * 2
    )

    assert sorted(outcome["status"] for outcome in outcomes) == ["IMPORTED", "SKIPPED"]
    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM analysis_runs WHERE load_case_id=?", [ingestion_target["load_case_id"]]
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT count(*) FROM analysis_run_metadata
            WHERE source_type=? AND source_name=? AND source_checksum=?
            """,
            [ingestion_target["source_type"], ingestion_target["source_name"], ingestion_target["source_checksum"]],
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT count(*) FROM scalar_results result
            JOIN analysis_runs run ON run.id=result.analysis_run_id
            WHERE run.load_case_id=? AND result.variable_key='concurrent_scalar'
            """,
            [ingestion_target["load_case_id"]],
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT count(*) FROM canonical_result_ingestion_sources
            WHERE source_type=? AND source_name=? AND source_checksum=?
            """,
            [ingestion_target["source_type"], ingestion_target["source_name"], ingestion_target["source_checksum"]],
        ).fetchone()[0] == 1
        statuses = connection.execute(
            "SELECT status FROM folder_import_jobs WHERE load_case_id=? ORDER BY status",
            [ingestion_target["load_case_id"]],
        ).fetchall()
    assert [row[0] for row in statuses] == ["COMPLETED", "SKIPPED"]


def test_postgres_distinct_sources_allocate_unique_run_numbers(ingestion_target: dict[str, str]) -> None:
    outcomes = _concurrent_ingestions(
        [
            _command(ingestion_target, ingestion_target["source_name"]),
            _command(ingestion_target, ingestion_target["alternate_source_name"]),
        ]
    )

    assert [outcome["status"] for outcome in outcomes] == ["IMPORTED", "IMPORTED"]
    with connect() as connection:
        run_numbers = connection.execute(
            "SELECT run_no FROM analysis_runs WHERE load_case_id=? ORDER BY run_no",
            [ingestion_target["load_case_id"]],
        ).fetchall()
        assert connection.execute(
            """
            SELECT count(*) FROM analysis_run_metadata
            WHERE source_type=?
              AND ((source_name=? AND source_checksum=?) OR (source_name=? AND source_checksum=?))
            """,
            [
                ingestion_target["source_type"],
                ingestion_target["source_name"],
                ingestion_target["source_checksum"],
                ingestion_target["alternate_source_name"],
                ingestion_target["alternate_source_checksum"],
            ],
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT count(*) FROM canonical_result_ingestion_sources
            WHERE source_type=?
              AND ((source_name=? AND source_checksum=?) OR (source_name=? AND source_checksum=?))
            """,
            [
                ingestion_target["source_type"],
                ingestion_target["source_name"],
                ingestion_target["source_checksum"],
                ingestion_target["alternate_source_name"],
                ingestion_target["alternate_source_checksum"],
            ],
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT count(*) FROM scalar_results result
            JOIN analysis_runs analysis_run ON analysis_run.id=result.analysis_run_id
            WHERE analysis_run.load_case_id=? AND result.variable_key='concurrent_scalar'
            """,
            [ingestion_target["load_case_id"]],
        ).fetchone()[0] == 2
        job_statuses = connection.execute(
            "SELECT status FROM folder_import_jobs WHERE load_case_id=? ORDER BY status",
            [ingestion_target["load_case_id"]],
        ).fetchall()
    assert [row[0] for row in run_numbers] == [1, 2]
    assert [row[0] for row in job_statuses] == ["COMPLETED", "COMPLETED"]
