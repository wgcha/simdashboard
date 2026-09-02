from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.analysis_insights.ports import AnalysisInsightsRepository
from .result_keys import run_result_keys


class SQLAnalysisInsightsRepository:
    """SQL read adapter for comparison and trust projections."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def comparison_runs(self, load_case_id: str, baseline_run_id: str, target_run_id: str) -> list[dict[str, Any]]:
        return rows(
            self._connection.execute(
                "SELECT * FROM analysis_runs WHERE load_case_id=? AND id IN (?, ?) ORDER BY run_no",
                [load_case_id, baseline_run_id, target_run_id],
            )
        )

    def comparison_scalars(self, baseline_run_id: str, target_run_id: str) -> list[dict[str, Any]]:
        return rows(
            self._connection.execute(
                "SELECT * FROM scalar_results WHERE analysis_run_id IN (?, ?)",
                [baseline_run_id, target_run_id],
            )
        )

    def comparison_series(self, baseline_run_id: str, target_run_id: str) -> list[dict[str, Any]]:
        return rows(
            self._connection.execute(
                """
                SELECT variable_key, min(display_name) AS display_name, min(value_unit) AS value_unit,
                       count(DISTINCT analysis_run_id) AS run_count
                FROM time_series_results WHERE analysis_run_id IN (?, ?)
                GROUP BY variable_key HAVING count(DISTINCT analysis_run_id)=2 ORDER BY variable_key
                """,
                [baseline_run_id, target_run_id],
            )
        )

    def comparison_points(self, baseline_run_id: str, target_run_id: str, variable_key: str) -> list[dict[str, Any]]:
        return rows(
            self._connection.execute(
                "SELECT analysis_run_id, time_value, value, time_unit, value_unit FROM time_series_results WHERE analysis_run_id IN (?, ?) AND variable_key=? ORDER BY time_value",
                [baseline_run_id, target_run_id, variable_key],
            )
        )

    def trust_run(self, run_id: str) -> dict[str, Any] | None:
        stored = rows(self._connection.execute("SELECT * FROM analysis_runs WHERE id=?", [run_id]))
        return stored[0] if stored else None

    def latest_run_id(self, load_case_id: str) -> str | None:
        latest = self._connection.execute(
            "SELECT id FROM analysis_runs WHERE load_case_id=? ORDER BY run_no DESC LIMIT 1",
            [load_case_id],
        ).fetchone()
        return latest[0] if latest else None

    def run_metadata(self, run_id: str) -> dict[str, Any] | None:
        metadata = rows(
            self._connection.execute("SELECT * FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id])
        )
        return metadata[0] if metadata else None

    def import_job(self, run_id: str) -> dict[str, Any] | None:
        jobs = rows(
            self._connection.execute(
                "SELECT * FROM folder_import_jobs WHERE analysis_run_id=? ORDER BY created_at DESC LIMIT 1",
                [run_id],
            )
        )
        return jobs[0] if jobs else None

    def result_counts(self, run_id: str) -> dict[str, int]:
        return {
            "scalar": self._connection.execute(
                "SELECT count(*) FROM scalar_results WHERE analysis_run_id=?", [run_id]
            ).fetchone()[0],
            "time_series": self._connection.execute(
                "SELECT count(*) FROM time_series_results WHERE analysis_run_id=?", [run_id]
            ).fetchone()[0],
            "curve": self._connection.execute(
                "SELECT count(*) FROM curve_results WHERE analysis_run_id=?", [run_id]
            ).fetchone()[0],
            "media": self._connection.execute(
                "SELECT count(*) FROM media_assets WHERE analysis_run_id=?", [run_id]
            ).fetchone()[0],
            "location": self._connection.execute(
                "SELECT count(*) FROM result_locations WHERE analysis_run_id=?", [run_id]
            ).fetchone()[0],
        }

    def result_keys(self, run_id: str) -> set[str]:
        return run_result_keys(self._connection, run_id)

    def active_catalog(self, load_case_id: str) -> dict[str, dict[str, Any]]:
        catalog_rows = rows(
            self._connection.execute(
                "SELECT variable_key, display_name, unit FROM variable_definitions WHERE load_case_id=? AND is_active=true",
                [load_case_id],
            )
        )
        return {item["variable_key"]: item for item in catalog_rows}

    def result_unit_rows(self, run_id: str) -> list[tuple[Any, Any]]:
        return self._connection.execute(
            """
            SELECT variable_key, unit FROM scalar_results WHERE analysis_run_id=?
            UNION SELECT variable_key, value_unit FROM time_series_results WHERE analysis_run_id=?
            UNION SELECT variable_key, y_unit FROM curve_results WHERE analysis_run_id=?
            """,
            [run_id, run_id, run_id],
        ).fetchall()

    def validations(self, run_id: str) -> list[dict[str, Any]]:
        return rows(
            self._connection.execute(
                "SELECT validation_type, verdict, created_at FROM validations WHERE analysis_run_id=? ORDER BY created_at DESC",
                [run_id],
            )
        )


class SQLAnalysisInsightsRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[AnalysisInsightsRepository]:
        with self._connection_provider() as connection:
            yield SQLAnalysisInsightsRepository(connection)
