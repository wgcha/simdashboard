"""Persistence adapter for the result-import history read model.

The ingestion workflow owns writes to ``folder_import_jobs``.  This adapter is
intentionally read-only: it joins the immutable V2 source-version ledger only
to enrich the operator-facing history view.
"""

from __future__ import annotations

from typing import Any, Literal

from ...database_connection import ConnectionLike, rows


ImportJobStatus = Literal["RUNNING", "COMPLETED", "SKIPPED", "REJECTED", "FAILED"]
IMPORT_JOB_STATUSES: frozenset[str] = frozenset(
    {"RUNNING", "COMPLETED", "SKIPPED", "REJECTED", "FAILED"}
)
MASTER_FOLDER_SOURCE_TYPE = "MASTER_FOLDER_REFRESH"


class SQLResultImportHistoryRepository:
    """Read import jobs without coupling HTTP handlers to SQL details."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def list_for_load_case(
        self,
        load_case_id: str,
        *,
        status: ImportJobStatus | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        clauses = ["jobs.load_case_id=?"]
        parameters: list[Any] = [load_case_id]
        if status is not None:
            clauses.append("jobs.status=?")
            parameters.append(status)
        where_clause = " AND ".join(clauses)
        item_parameters = [*parameters, limit, offset]
        items = [
            self._serialize(row)
            for row in rows(
                self._connection.execute(
                    f"""
                    SELECT jobs.id, jobs.load_case_id, jobs.status, jobs.source_type,
                           jobs.source_folder, jobs.source_checksum, jobs.source_run_id,
                           jobs.conflict_policy, jobs.outcome_reason, jobs.analysis_run_id,
                           jobs.replaced_analysis_run_id, jobs.created_at, jobs.completed_at,
                           versions.source_revision
                    FROM folder_import_jobs jobs
                    LEFT JOIN canonical_result_ingestion_source_versions versions
                      ON versions.analysis_run_id=jobs.analysis_run_id
                    WHERE {where_clause}
                    ORDER BY jobs.created_at DESC, jobs.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    item_parameters,
                )
            )
        ]
        total = int(
            self._connection.execute(
                f"SELECT count(*) FROM folder_import_jobs jobs WHERE {where_clause}",
                parameters,
            ).fetchone()[0]
        )
        count_rows = self._connection.execute(
            """
            SELECT status, count(*)
            FROM folder_import_jobs
            WHERE load_case_id=?
            GROUP BY status
            """,
            [load_case_id],
        ).fetchall()
        counts = {known_status: 0 for known_status in sorted(IMPORT_JOB_STATUSES)}
        counts.update({str(job_status): int(count) for job_status, count in count_rows})
        return {"items": items, "total": total, "counts": counts}

    def get(self, job_id: str) -> dict[str, Any] | None:
        records = rows(
            self._connection.execute(
                """
                SELECT jobs.id, jobs.load_case_id, jobs.status, jobs.source_type,
                       jobs.source_folder, load_cases.request_id,
                       analysis_requests.project_id
                FROM folder_import_jobs jobs
                JOIN load_cases ON load_cases.id=jobs.load_case_id
                JOIN analysis_requests ON analysis_requests.id=load_cases.request_id
                WHERE jobs.id=?
                """,
                [job_id],
            )
        )
        return records[0] if records else None

    @staticmethod
    def _serialize(row: dict[str, Any]) -> dict[str, Any]:
        status = str(row["status"])
        reason = str(row["outcome_reason"]) if row.get("outcome_reason") is not None else None
        return {
            "id": str(row["id"]),
            "load_case_id": str(row["load_case_id"]),
            "status": status,
            "source_type": row.get("source_type"),
            "source_folder": str(row["source_folder"]),
            "source_checksum": row.get("source_checksum"),
            "source_run_id": row.get("source_run_id"),
            "conflict_policy": row.get("conflict_policy"),
            "outcome_reason": reason,
            "operation": _operation_for(status, reason),
            "analysis_run_id": row.get("analysis_run_id"),
            "replaced_analysis_run_id": row.get("replaced_analysis_run_id"),
            "source_revision": row.get("source_revision"),
            "created_at": row.get("created_at"),
            "completed_at": row.get("completed_at"),
            "retryable": _is_retryable(row),
        }


def _operation_for(status: str, reason: str | None) -> str | None:
    """Derive a stable UI operation from persisted terminal facts only."""
    if status == "REJECTED" or reason in {"SOURCE_RUN_CHANGED_REJECTED", "SOURCE_RUN_CONFLICT"}:
        return "REJECTED"
    if status == "SKIPPED" or reason in {"IDENTICAL_COMPLETED", "SOURCE_RUN_CHANGED_SKIPPED"}:
        return "NOOP"
    if reason == "SOURCE_RUN_REPLACED":
        return "REPLACED"
    if status == "COMPLETED":
        return "CREATED"
    return None


def _is_retryable(row: dict[str, Any]) -> bool:
    source_folder = row.get("source_folder")
    return (
        row.get("source_type") == MASTER_FOLDER_SOURCE_TYPE
        and row.get("status") in {"FAILED", "REJECTED"}
        and _is_manifest_relative_path(source_folder)
    )


def _is_manifest_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    parts = value.split("/")
    return parts[-1] == "manifest.json" and all(part not in {"", ".", ".."} for part in parts)
