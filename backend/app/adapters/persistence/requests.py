from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...database_connection import ConnectionLike, connect, rows
from ...domains.requests.models import (
    RequestAssignee,
    RequestReassignmentAudit,
    StoredAnalysisRequest,
)
from ...domains.requests.ports import RequestQueryRepository, RequestReassignmentUnitOfWork


AuthorizationCallback = Callable[[str, ConnectionLike], object]
RequestQueryAuthorizationCallback = Callable[[str, ConnectionLike], bool]
AssigneeResolver = Callable[[str, str, ConnectionLike], RequestAssignee]


class SQLRequestQueryRepository:
    """SQL adapter for the project-scoped request listing."""

    def __init__(self, connection: ConnectionLike, authorize: RequestQueryAuthorizationCallback) -> None:
        self._connection = connection
        self._authorize = authorize

    def authorize_project(self, project_id: str) -> bool:
        return bool(self._authorize(project_id, self._connection))

    def list_requests(self, project_id: str) -> list[StoredAnalysisRequest]:
        # The application query invokes authorize_project first on this same
        # connection, before reaching this SQL statement.
        return cast(
            list[StoredAnalysisRequest],
            rows(
                self._connection.execute(
                    "SELECT * FROM analysis_requests WHERE project_id = ? ORDER BY requested_at DESC",
                    [project_id],
                )
            ),
        )


class SQLRequestQueryRepositoryProvider:
    def __init__(
        self,
        authorize: RequestQueryAuthorizationCallback,
        connection_provider: Callable[[], Any] = connect,
    ) -> None:
        self._authorize = authorize
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[RequestQueryRepository]:
        with self._connection_provider() as connection:
            yield SQLRequestQueryRepository(connection, self._authorize)


class SQLRequestReassignmentUnitOfWork:
    def __init__(
        self,
        connection: ConnectionLike,
        authorize: AuthorizationCallback,
        resolve_assignee: AssigneeResolver,
    ) -> None:
        self._connection = connection
        self._authorize = authorize
        self._resolve_assignee = resolve_assignee

    def find_request(self, request_id: str) -> StoredAnalysisRequest | None:
        stored = cast(
            list[StoredAnalysisRequest],
            rows(self._connection.execute("SELECT * FROM analysis_requests WHERE id=?", [request_id])),
        )
        return stored[0] if stored else None

    def authorize_request_edit(self, project_id: str) -> object:
        return self._authorize(project_id, self._connection)

    def resolve_assignee(self, project_id: str, owner_user_id: str) -> RequestAssignee:
        return self._resolve_assignee(project_id, owner_user_id, self._connection)

    def update_assignee(self, request_id: str, assignee: RequestAssignee) -> None:
        self._connection.execute(
            "UPDATE analysis_requests SET owner=?, owner_user_id=? WHERE id=?",
            [assignee["display_name"], assignee["user_id"], request_id],
        )

    def add_reassignment_audit(
        self,
        audit: RequestReassignmentAudit,
        request: StoredAnalysisRequest,
        assignee: RequestAssignee,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events
                (id, occurred_at, user_id, username, role, action, method, path,
                 status_code, request_id, client_ip, user_agent, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid4()),
                datetime.now(timezone.utc).replace(tzinfo=None),
                audit["user_id"],
                audit["username"],
                audit["role"],
                "REQUEST_ASSIGNEE_CHANGED",
                audit["method"],
                audit["path"],
                audit["status_code"],
                audit["request_id"],
                audit["client_ip"],
                audit["user_agent"],
                json.dumps(
                    {
                        "project_id": request["project_id"],
                        "request_id": request["id"],
                        "old_owner_user_id": request.get("owner_user_id"),
                        "new_owner_user_id": assignee["user_id"],
                    },
                    ensure_ascii=False,
                ),
            ],
        )


class SQLRequestReassignmentUnitOfWorkProvider:
    def __init__(
        self,
        authorize: AuthorizationCallback,
        resolve_assignee: AssigneeResolver,
        connection_provider: Callable[[], Any] = connect,
    ) -> None:
        self._authorize = authorize
        self._resolve_assignee = resolve_assignee
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[RequestReassignmentUnitOfWork]:
        with self._connection_provider() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                yield SQLRequestReassignmentUnitOfWork(connection, self._authorize, self._resolve_assignee)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
