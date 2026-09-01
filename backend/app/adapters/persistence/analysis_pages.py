from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...database import json_value, rows
from ...database_connection import ConnectionLike, connect
from ...domains.analysis_pages.policies import sort_analysis_pages, summarize_analysis_page
from ...domains.analysis_pages.ports import AnalysisPageRepository


class SQLAnalysisPageRepository:
    """Analysis-page read adapter bound to one already-open connection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def load_case_context(self, load_case_id: str) -> tuple[Any, ...] | None:
        context = self._connection.execute(
            """
            SELECT ar.project_id, lc.request_id
            FROM load_cases lc
            JOIN analysis_requests ar ON ar.id = lc.request_id
            WHERE lc.id = ?
            """,
            [load_case_id],
        ).fetchone()
        return tuple(context) if context else None

    def authorize(self, callback: Callable[[object], None]) -> None:
        callback(self._connection)

    def list_analysis_pages(
        self,
        load_case_id: str,
        *,
        include_private: bool,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        stored = rows(
            self._connection.execute(
                """
                SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at
                FROM dashboards
                WHERE load_case_id = ? OR id IN ('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')
                """,
                [load_case_id],
            )
        )
        result: list[dict[str, Any]] = []
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            summary = summarize_analysis_page(
                item,
                definition,
                load_case_id=load_case_id,
                include_private=include_private,
                include_archived=include_archived,
            )
            if summary is not None:
                result.append(summary)
        return sort_analysis_pages(result)


class SQLAnalysisPageRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[AnalysisPageRepository]:
        with self._connection_provider() as connection:
            yield SQLAnalysisPageRepository(connection)
