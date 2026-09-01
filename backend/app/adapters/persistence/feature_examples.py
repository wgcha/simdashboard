from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...database_connection import ConnectionLike, connect
from ...domains.feature_examples.ports import FeatureExamplesRepository


class SQLFeatureExamplesRepository:
    """SQL read adapter for the established per-example data profiles."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def data_profile(self, load_case_id: str) -> dict[str, int]:
        counts = self._connection.execute(
            """
                    SELECT count(DISTINCT r.id), count(DISTINCT s.id), count(DISTINCT ts.variable_key),
                           count(DISTINCT c.id), count(DISTINCT m.id), count(DISTINCT a.id)
                    FROM load_cases lc
                    LEFT JOIN analysis_runs r ON r.load_case_id=lc.id
                    LEFT JOIN scalar_results s ON s.analysis_run_id=r.id
                    LEFT JOIN time_series_results ts ON ts.analysis_run_id=r.id
                    LEFT JOIN curve_results c ON c.analysis_run_id=r.id
                    LEFT JOIN media_assets m ON m.analysis_run_id=r.id
                    LEFT JOIN review_annotations a ON a.analysis_run_id=r.id
                    WHERE lc.id=?
                    """,
            [load_case_id],
        ).fetchone()
        return dict(zip(["runs", "scalars", "series", "curves", "media", "reviews"], counts))


class SQLFeatureExamplesRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[FeatureExamplesRepository]:
        with self._connection_provider() as connection:
            yield SQLFeatureExamplesRepository(connection)
