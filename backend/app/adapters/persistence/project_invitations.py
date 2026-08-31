"""SQL adapters for the project-directory and invitation application ports."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...config import database_settings
from ...database_connection import ConnectionLike, connect, rows
from ...domains.project_invitations.models import (
    DirectoryEmployee,
    InvitationStatus,
    PersistedProjectInvitation,
    ProjectInvitationAuditRecord,
    ProjectNotFoundError,
    ProjectRole,
)


AuthorizationCallback = Callable[[str, ConnectionLike], None]
ConnectionProvider = Callable[[], Any]
_LOCK_WHITELIST = frozenset({"users", "project_invitations", "project_memberships"})


def _project_exists(connection: ConnectionLike, project_id: str) -> bool:
    return bool(connection.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone())


def _one(result: Any) -> PersistedProjectInvitation | None:
    values = cast(list[PersistedProjectInvitation], rows(result))
    return values[0] if values else None


def _write_audit(connection: ConnectionLike, audit: ProjectInvitationAuditRecord) -> None:
    connection.execute(
        """
        INSERT INTO audit_events
        (id, occurred_at, user_id, username, role, action, method, path, status_code,
         request_id, client_ip, user_agent, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(uuid4()), datetime.now(timezone.utc).replace(tzinfo=None), audit.get("user_id"),
            audit.get("username"), audit.get("role"), audit["action"], audit["method"],
            audit["path"], audit["status_code"], audit["request_id"], audit.get("client_ip"),
            audit["user_agent"], json.dumps(audit.get("detail", {}), ensure_ascii=False),
        ],
    )


class SQLProjectInvitationReader:
    def __init__(self, connection: ConnectionLike, authorize: AuthorizationCallback) -> None:
        self._connection, self._authorize = connection, authorize

    def project_exists(self, project_id: str) -> bool:
        return _project_exists(self._connection, project_id)

    def authorize_create(self, project_id: str) -> None:
        self._authorize(project_id, self._connection)

    def list_invitations(self, project_id: str) -> list[PersistedProjectInvitation]:
        return cast(
            list[PersistedProjectInvitation],
            rows(
                self._connection.execute(
                    "SELECT * FROM project_invitations WHERE project_id=? ORDER BY invited_at DESC",
                    [project_id],
                )
            ),
        )


class SQLProjectInvitationReaderProvider:
    def __init__(
        self,
        authorize: AuthorizationCallback,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        self._authorize, self._connection_provider = authorize, connection_provider

    @contextmanager
    def __call__(self) -> Iterator[SQLProjectInvitationReader]:
        with self._connection_provider() as connection:
            yield SQLProjectInvitationReader(connection, self._authorize)


class SQLInvitationAuditWriter:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def add_audit(self, audit: ProjectInvitationAuditRecord) -> None:
        _write_audit(self._connection, audit)


class SQLInvitationAuditWriterProvider:
    def __init__(self, connection_provider: ConnectionProvider = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[SQLInvitationAuditWriter]:
        with self._connection_provider() as connection:
            yield SQLInvitationAuditWriter(connection)


class SQLProjectInvitationUnitOfWork:
    def __init__(self, connection: ConnectionLike, authorize: AuthorizationCallback) -> None:
        self._connection, self._authorize = connection, authorize

    def authorize_create(self, project_id: str) -> None:
        self._authorize(project_id, self._connection)

    def authorize_manage(self, project_id: str) -> None:
        self._authorize(project_id, self._connection)

    def user_for_employee(self, employee_id: str) -> tuple[str, str] | None:
        found = self._connection.execute(
            "SELECT id, account_status FROM users WHERE employee_id=?", [employee_id]
        ).fetchone()
        return (str(found[0]), str(found[1])) if found else None

    def membership_exists(self, project_id: str, user_id: str) -> bool:
        return bool(
            self._connection.execute(
                "SELECT 1 FROM project_memberships WHERE project_id=? AND user_id=?",
                [project_id, user_id],
            ).fetchone()
        )

    def open_invitation_exists(self, project_id: str, employee_id: str) -> bool:
        return bool(self._connection.execute(
            """SELECT 1 FROM project_invitations WHERE project_id=? AND employee_id=?
               AND status IN ('PENDING_ACCOUNT', 'PENDING_APPROVAL', 'READY')""", [project_id, employee_id]
        ).fetchone())

    def add_invitation(
        self,
        invitation_id: str,
        project_id: str,
        employee: DirectoryEmployee,
        desired_role: ProjectRole,
        status: InvitationStatus,
        resolved_user_id: str | None,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """INSERT INTO project_invitations
               (id, project_id, employee_id, display_name_snapshot, department_snapshot,
                desired_role, status, resolved_user_id, invited_by, invited_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [invitation_id, project_id, employee["employee_id"], employee["display_name"], employee.get("department"), desired_role, status, resolved_user_id, actor_id, occurred_at],
        )

    def find_invitation(self, invitation_id: str, project_id: str | None = None) -> PersistedProjectInvitation | None:
        if project_id is None:
            return _one(self._connection.execute("SELECT * FROM project_invitations WHERE id=?", [invitation_id]))
        return _one(self._connection.execute("SELECT * FROM project_invitations WHERE id=? AND project_id=?", [invitation_id, project_id]))

    def add_membership(
        self,
        membership_id: str,
        project_id: str,
        user_id: str,
        role: ProjectRole,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """INSERT INTO project_memberships
               (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [membership_id, project_id, user_id, role, actor_id, occurred_at, actor_id, occurred_at],
        )

    def complete_invitation(
        self,
        invitation_id: str,
        project_id: str,
        user_id: str,
        actor_id: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """UPDATE project_invitations
               SET status='COMPLETED', resolved_user_id=?, resolved_by=?, resolved_at=?
               WHERE id=? AND project_id=?""",
            [user_id, actor_id, occurred_at, invitation_id, project_id],
        )

    def cancel_invitation(self, invitation_id: str, actor_id: str, occurred_at: datetime) -> None:
        self._connection.execute("UPDATE project_invitations SET status='CANCELLED', cancelled_by=?, cancelled_at=? WHERE id=?", [actor_id, occurred_at, invitation_id])

    def add_audit(self, audit: ProjectInvitationAuditRecord) -> None:
        _write_audit(self._connection, audit)


class _SQLProjectInvitationUnitOfWorkProvider:
    """Provider with an explicit, testable preflight/begin policy."""

    def __init__(
        self,
        authorize: AuthorizationCallback,
        locked_tables: tuple[str, ...],
        *,
        preflight_project: bool,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        if not locked_tables or any(table not in _LOCK_WHITELIST for table in locked_tables):
            raise RuntimeError("Unexpected table lock requested")
        self._authorize, self._locked_tables = authorize, locked_tables
        self._preflight_project, self._connection_provider = preflight_project, connection_provider

    @contextmanager
    def __call__(self, project_id: str) -> Iterator[SQLProjectInvitationUnitOfWork]:
        with self._connection_provider() as connection:
            # Complete/cancel intentionally verify the project on this connection before BEGIN.
            if self._preflight_project and not _project_exists(connection, project_id):
                raise ProjectNotFoundError(project_id)
            connection.execute("BEGIN TRANSACTION")
            if database_settings().backend == "postgresql":
                connection.execute(
                    f"LOCK TABLE {', '.join(self._locked_tables)} IN SHARE ROW EXCLUSIVE MODE"
                )
            try:
                yield SQLProjectInvitationUnitOfWork(connection, self._authorize)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise


class SQLInvitationCreateUnitOfWorkProvider(_SQLProjectInvitationUnitOfWorkProvider):
    """Create's project/auth preflight was already closed before this transaction."""

    def __init__(self, authorize: AuthorizationCallback, connection_provider: ConnectionProvider = connect) -> None:
        super().__init__(authorize, ("users", "project_invitations", "project_memberships"), preflight_project=False, connection_provider=connection_provider)


class SQLInvitationCompleteUnitOfWorkProvider(_SQLProjectInvitationUnitOfWorkProvider):
    def __init__(self, authorize: AuthorizationCallback, connection_provider: ConnectionProvider = connect) -> None:
        super().__init__(authorize, ("users", "project_invitations", "project_memberships"), preflight_project=True, connection_provider=connection_provider)


class SQLInvitationCancelUnitOfWorkProvider(_SQLProjectInvitationUnitOfWorkProvider):
    def __init__(self, authorize: AuthorizationCallback, connection_provider: ConnectionProvider = connect) -> None:
        super().__init__(authorize, ("project_invitations",), preflight_project=True, connection_provider=connection_provider)
