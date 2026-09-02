from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.feature_examples.ports import DataProfile, FeatureExamplesRepository


class SQLFeatureExamplesRepository:
    """SQL read adapter for the established per-example data profiles."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def data_profiles(self, load_case_ids: Sequence[str]) -> Mapping[str, DataProfile]:
        unique_ids = list(dict.fromkeys(load_case_ids))
        if not unique_ids:
            return {}
        placeholders = ", ".join("?" for _ in unique_ids)
        result_rows = rows(self._connection.execute(
            f"""
            SELECT lc.id AS load_case_id,
                   count(DISTINCT r.id) AS runs, count(DISTINCT s.id) AS scalars,
                   count(DISTINCT ts.variable_key) AS series, count(DISTINCT c.id) AS curves,
                   count(DISTINCT m.id) AS media, count(DISTINCT a.id) AS reviews
            FROM load_cases lc
            LEFT JOIN analysis_runs r ON r.load_case_id=lc.id
            LEFT JOIN scalar_results s ON s.analysis_run_id=r.id
            LEFT JOIN time_series_results ts ON ts.analysis_run_id=r.id
            LEFT JOIN curve_results c ON c.analysis_run_id=r.id
            LEFT JOIN media_assets m ON m.analysis_run_id=r.id
            LEFT JOIN review_annotations a ON a.analysis_run_id=r.id
            WHERE lc.id IN ({placeholders})
            GROUP BY lc.id
            ORDER BY lc.id
            """,
            unique_ids,
        ))
        return {
            row["load_case_id"]: {
                "runs": row["runs"],
                "scalars": row["scalars"],
                "series": row["series"],
                "curves": row["curves"],
                "media": row["media"],
                "reviews": row["reviews"],
            }
            for row in result_rows
        }


class SQLFeatureExamplesRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[FeatureExamplesRepository]:
        with self._connection_provider() as connection:
            yield SQLFeatureExamplesRepository(connection)
