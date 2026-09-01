from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.workflow_queries.ports import WorkflowQueriesRepository
from ...services.request_monitoring import request_monitoring_summary


class SQLWorkflowQueriesRepository:
    """SQL adapter preserving the existing workflow-monitoring read order."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def analysis_request(self, request_id: str) -> dict[str, Any] | None:
        requests = rows(self._connection.execute("SELECT * FROM analysis_requests WHERE id = ?", [request_id]))
        return requests[0] if requests else None

    def workflow_requests(self) -> list[dict[str, Any]]:
        return rows(
            self._connection.execute(
                """
                SELECT ar.*, p.name AS project_name, p.product_name,
                       COALESCE(min(lc.analysis_type), 'UNASSIGNED') AS category,
                       COALESCE(min(lc.name), '하중 경우 미지정') AS load_case_name
                FROM analysis_requests ar
                JOIN projects p ON p.id = ar.project_id
                LEFT JOIN load_cases lc ON lc.request_id = ar.id
                GROUP BY ar.id, ar.project_id, ar.title, ar.status, ar.owner, ar.owner_user_id, ar.requested_at,
                         ar.due_at, ar.overall_note, p.name, p.product_name
                ORDER BY ar.requested_at DESC
                """
            )
        )

    def monitoring_summary(self, request_id: str) -> dict[str, Any]:
        return request_monitoring_summary(self._connection, request_id)


class SQLWorkflowQueriesRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[WorkflowQueriesRepository]:
        with self._connection_provider() as connection:
            yield SQLWorkflowQueriesRepository(connection)
