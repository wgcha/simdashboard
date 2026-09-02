from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.load_case_overview.ports import LoadCaseOverviewRepository


class SQLLoadCaseOverviewRepository:
    """SQL projection adapter for one load case and its selected run."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def load_case(self, load_case_id: str) -> dict[str, Any] | None:
        stored = rows(
            self._connection.execute(
                """
                SELECT lc.*, ar.id AS request_id, ar.title AS request_title,
                       p.id AS project_id, p.name AS project_name, p.product_name
                FROM load_cases lc
                JOIN analysis_requests ar ON ar.id = lc.request_id
                JOIN projects p ON p.id = ar.project_id
                WHERE lc.id = ?
                """,
                [load_case_id],
            )
        )
        return stored[0] if stored else None

    def selected_run(self, load_case_id: str, run_id: str | None) -> str | None:
        run = self._connection.execute(
            "SELECT id FROM analysis_runs WHERE load_case_id = ? AND (? IS NULL OR id = ?) ORDER BY run_no DESC LIMIT 1",
            [load_case_id, run_id, run_id],
        ).fetchone()
        return run[0] if run else None

    def run_projection(self, load_case_id: str, run_id: str) -> dict[str, Any]:
        scalar_results = rows(
            self._connection.execute(
                """
                SELECT sr.*, COALESCE(vd.result_group, 'CUSTOM') AS result_group
                FROM scalar_results sr
                LEFT JOIN variable_definitions vd
                  ON vd.load_case_id = ? AND vd.variable_key = sr.variable_key
                WHERE sr.analysis_run_id = ? ORDER BY sr.display_name
                """,
                [load_case_id, run_id],
            )
        )
        time_series = rows(
            self._connection.execute(
                """
                SELECT ts.*, COALESCE(vd.result_group, 'CUSTOM') AS result_group
                FROM time_series_results ts
                LEFT JOIN variable_definitions vd
                  ON vd.load_case_id = ? AND vd.variable_key = ts.variable_key
                WHERE ts.analysis_run_id = ? ORDER BY ts.time_value, ts.variable_key
                """,
                [load_case_id, run_id],
            )
        )
        curves = rows(
            self._connection.execute(
                """
                SELECT cr.*, COALESCE(vd.result_group, 'CUSTOM') AS result_group
                FROM curve_results cr
                LEFT JOIN variable_definitions vd
                  ON vd.load_case_id = ? AND vd.variable_key = cr.variable_key
                WHERE cr.analysis_run_id = ? ORDER BY cr.display_name, cr.series_key
                """,
                [load_case_id, run_id],
            )
        )
        result_locations = rows(
            self._connection.execute(
                "SELECT * FROM result_locations WHERE analysis_run_id = ? ORDER BY variable_key", [run_id]
            )
        )
        notes = rows(
            self._connection.execute(
                "SELECT * FROM qualitative_notes WHERE analysis_run_id = ? ORDER BY created_at DESC", [run_id]
            )
        )
        media = rows(self._connection.execute("SELECT * FROM media_assets WHERE analysis_run_id = ?", [run_id]))
        template = rows(
            self._connection.execute(
                """
                SELECT te.* FROM template_executions te
                JOIN analysis_runs run ON run.template_execution_id = te.id
                WHERE run.id = ?
                """,
                [run_id],
            )
        )
        return {
            "scalar_results": scalar_results,
            "time_series": time_series,
            "curves": curves,
            "result_locations": result_locations,
            "notes": notes,
            "media": media,
            "template": template,
        }


class SQLLoadCaseOverviewRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[LoadCaseOverviewRepository]:
        with self._connection_provider() as connection:
            yield SQLLoadCaseOverviewRepository(connection)
