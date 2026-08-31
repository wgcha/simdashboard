from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...config import database_settings
from ...database_connection import ConnectionLike, connect, rows
from ...domains.project_memberships.models import (
    PersistedProjectMembership,
    ProjectMemberListItem,
    ProjectMembershipAuditRecord,
    ProjectMembershipRole,
    ProjectNotFoundError,
)
from ...domains.project_memberships.ports import (
    ProjectMembershipReader,
    ProjectMembershipUnitOfWork,
)


AuthorizationCallback = Callable[[str, ConnectionLike], bool]
ConnectionProvider = Callable[[], Any]
_SAFE_LOCK_TABLES = frozenset({"users", "project_memberships"})


def _project_exists(connection: ConnectionLike, project_id: str) -> bool:
    return bool(connection.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone())


class SQLProjectMembershipReader(ProjectMembershipReader):
    def __init__(self, connection: ConnectionLike, authorize: AuthorizationCallback) -> None:
        self._connection = connection
        self._authorize = authorize

    def project_exists(self, project_id: str) -> bool:
        return _project_exists(self._connection, project_id)

    def authorize_manage(self, project_id: str) -> None:
        self._authorize(project_id, self._connection)

    def list_members(self, project_id: str) -> list[ProjectMemberListItem]:
        return cast(
            list[ProjectMemberListItem],
            rows(
                self._connection.execute(
                    """
                    SELECT memberships.project_id, memberships.user_id, memberships.role,
                           memberships.created_at, memberships.updated_at,
                           users.username, users.display_name, users.employee_id,
                           users.department, users.job_title, users.account_status
                    FROM project_memberships memberships
                    JOIN users ON users.id=memberships.user_id
                    WHERE memberships.project_id=? ORDER BY users.display_name, users.id
                    """,
                    [project_id],
                )
            ),
        )


class SQLProjectMembershipReaderProvider:
    def __init__(
        self,
        authorize: AuthorizationCallback,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        self._authorize = authorize
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ProjectMembershipReader]:
        with self._connection_provider() as connection:
            yield SQLProjectMembershipReader(connection, self._authorize)


class SQLProjectMembershipUnitOfWork(ProjectMembershipUnitOfWork):
    def __init__(self, connection: ConnectionLike, authorize: AuthorizationCallback) -> None:
        self._connection = connection
        self._authorize = authorize

    def authorize_manage(self, project_id: str) -> bool:
        return self._authorize(project_id, self._connection)

    def find_active_user(self, user_id: str) -> bool | None:
        user = self._connection.execute(
            "SELECT account_status FROM users WHERE id=?", [user_id]
        ).fetchone()
        if not user:
            return None
        return user[0] == "ACTIVE"

    def membership_exists(self, project_id: str, user_id: str) -> bool:
        return bool(
            self._connection.execute(
                "SELECT 1 FROM project_memberships WHERE project_id=? AND user_id=?",
                [project_id, user_id],
            ).fetchone()
        )

    def add_membership(
        self,
        membership_id: str,
        project_id: str,
        user_id: str,
        role: ProjectMembershipRole,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO project_memberships
                (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [membership_id, project_id, user_id, role, actor_id, occurred_at, actor_id, occurred_at],
        )

    def find_membership(self, project_id: str, user_id: str) -> PersistedProjectMembership | None:
        stored = cast(
            list[PersistedProjectMembership],
            rows(
                self._connection.execute(
                    "SELECT * FROM project_memberships WHERE project_id=? AND user_id=?",
                    [project_id, user_id],
                )
            ),
        )
        return stored[0] if stored else None

    def count_admins(self, project_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT count(*) FROM project_memberships WHERE project_id=? AND role='admin'",
                [project_id],
            ).fetchone()[0]
        )

    def update_role(
        self,
        project_id: str,
        user_id: str,
        role: ProjectMembershipRole,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            "UPDATE project_memberships SET role=?, updated_by=?, updated_at=? WHERE project_id=? AND user_id=?",
            [role, actor_id, occurred_at, project_id, user_id],
        )

    def count_open_work_items(self, project_id: str, user_id: str) -> int:
        return int(
            self._connection.execute(
                """
                SELECT count(*) FROM request_work_items items
                JOIN analysis_requests requests ON requests.id=items.request_id
                WHERE requests.project_id=? AND items.owner_user_id=? AND items.status <> 'COMPLETED'
                """,
                [project_id, user_id],
            ).fetchone()[0]
        )

    def delete(self, project_id: str, user_id: str) -> None:
        self._connection.execute(
            "DELETE FROM project_memberships WHERE project_id=? AND user_id=?",
            [project_id, user_id],
        )

    def add_audit(self, audit: ProjectMembershipAuditRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events
            (id, occurred_at, user_id, username, role, action, method, path, status_code, request_id, client_ip, user_agent, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid4()),
                datetime.now(timezone.utc).replace(tzinfo=None),
                audit.get("user_id"),
                audit.get("username"),
                audit.get("role"),
                audit["action"],
                audit["method"],
                audit["path"],
                audit["status_code"],
                audit["request_id"],
                audit.get("client_ip"),
                audit["user_agent"],
                json.dumps(audit.get("detail", {}), ensure_ascii=False),
            ],
        )


class SQLProjectMembershipUnitOfWorkProvider:
    def __init__(
        self,
        authorize: AuthorizationCallback,
        locked_tables: tuple[str, ...],
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        if not locked_tables or any(table not in _SAFE_LOCK_TABLES for table in locked_tables):
            raise RuntimeError("Unexpected table lock requested")
        self._authorize = authorize
        self._locked_tables = locked_tables
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self, project_id: str) -> Iterator[ProjectMembershipUnitOfWork]:
        with self._connection_provider() as connection:
            if not _project_exists(connection, project_id):
                raise ProjectNotFoundError(project_id)
            connection.execute("BEGIN TRANSACTION")
            if database_settings().backend == "postgresql":
                connection.execute(
                    f"LOCK TABLE {', '.join(self._locked_tables)} IN SHARE ROW EXCLUSIVE MODE"
                )
            try:
                yield SQLProjectMembershipUnitOfWork(connection, self._authorize)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
