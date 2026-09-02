from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from ...database_connection import ConnectionLike, connect, rows
from ...domains.project_assignees.models import ProjectAssigneeCandidate
from ...domains.project_assignees.ports import ProjectAssigneeReader


AuthorizationCallback = Callable[[str, ConnectionLike], None]
ConnectionProvider = Callable[[], Any]


class SQLProjectAssigneeReader(ProjectAssigneeReader):
    def __init__(self, connection: ConnectionLike, authorize: AuthorizationCallback) -> None:
        self._connection = connection
        self._authorize = authorize

    def project_exists(self, project_id: str) -> bool:
        return bool(
            self._connection.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone()
        )

    def authorize_request_edit(self, project_id: str) -> None:
        self._authorize(project_id, self._connection)

    def list_candidates(
        self,
        project_id: str,
        search_term: str | None,
        exclude_local_admin: bool,
    ) -> list[ProjectAssigneeCandidate]:
        query = """
            SELECT users.id AS user_id, users.display_name, users.employee_id,
                   users.department, users.job_title, memberships.role
            FROM project_memberships memberships
            JOIN users ON users.id=memberships.user_id
            WHERE memberships.project_id=? AND users.account_status='ACTIVE'
        """
        parameters: list[Any] = [project_id]
        if exclude_local_admin:
            query += " AND users.id <> 'local-admin'"
        if search_term is not None:
            query += (
                " AND (lower(users.display_name) LIKE ? "
                "OR lower(COALESCE(users.employee_id, '')) LIKE ?)"
            )
            pattern = f"%{search_term}%"
            parameters.extend([pattern, pattern])
        query += " ORDER BY users.display_name, users.id LIMIT 50"
        return cast(list[ProjectAssigneeCandidate], rows(self._connection.execute(query, parameters)))


class SQLProjectAssigneeReaderProvider:
    def __init__(
        self,
        authorize: AuthorizationCallback,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        self._authorize = authorize
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ProjectAssigneeReader]:
        with self._connection_provider() as connection:
            yield SQLProjectAssigneeReader(connection, self._authorize)
