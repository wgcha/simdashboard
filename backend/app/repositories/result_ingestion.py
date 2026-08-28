from __future__ import annotations

from typing import Any

from ..repositories.variable_catalog import VariableCatalogRepository


class ResultIngestionRepository:
    """Read-only persistence facade for result-ingestion request context."""

    def __init__(self, conn: Any):
        self.conn = conn

    def get_load_case_context(self, load_case_id: str) -> Any:
        return self.conn.execute(
            """
            SELECT lc.id, lc.request_id, ar.project_id
            FROM load_cases lc
            JOIN analysis_requests ar ON ar.id = lc.request_id
            WHERE lc.id = ?
            """,
            [load_case_id],
        ).fetchone()

    def get_quality_threshold(self, project_id: Any, criterion_key: str, default: float) -> float:
        row = self.conn.execute(
            "SELECT threshold_double FROM quality_thresholds WHERE project_id = ? AND criterion_key = ?",
            [project_id, criterion_key],
        ).fetchone()
        return float(row[0]) if row else default

    def list_catalog(self, load_case_id: str) -> dict[str, dict[str, Any]]:
        return {
            item["id"]: item
            for item in VariableCatalogRepository(self.conn).list_for_load_case(load_case_id)
        }
