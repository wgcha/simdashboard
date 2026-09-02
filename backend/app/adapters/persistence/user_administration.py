"""SQL persistence for project-independent user-account administration."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...config import database_settings
from ...database_connection import ConnectionLike, connect, rows
from ...domains.user_administration.models import (
    AccountStatus,
    UserAccount,
    UserAdministrationAuditRecord,
)
from ...domains.user_administration.ports import UserAdministrationUnitOfWork


AuthorizationCallback = Callable[[ConnectionLike], None]
ConnectionProvider = Callable[[], Any]
_SAFE_LOCK_TABLES = frozenset({"users", "project_invitations"})


def _user_item(connection: ConnectionLike, user_id: str) -> UserAccount | None:
    result = cast(
        list[UserAccount],
        rows(
            connection.execute(
                """
                SELECT id, username, display_name, employee_id, email, department, job_title,
                       account_status, is_global_admin, approved_by, approved_at, last_login_at,
                       created_at, updated_at
                FROM users WHERE id=?
                """,
                [user_id],
            )
        ),
    )
    return result[0] if result else None


class SQLUserAdministrationUnitOfWork(UserAdministrationUnitOfWork):
    def __init__(self, connection: ConnectionLike, authorize: AuthorizationCallback) -> None:
        self._connection = connection
        self._authorize = authorize

    def authorize_user_approval(self) -> None:
        self._authorize(self._connection)

    def find_user_for_status(
        self, user_id: str
    ) -> tuple[AccountStatus, bool, str | None, datetime] | None:
        current = self._connection.execute(
            "SELECT account_status, is_global_admin, employee_id, updated_at FROM users WHERE id=?",
            [user_id],
        ).fetchone()
        if not current:
            return None
        return cast(AccountStatus, current[0]), bool(current[1]), current[2], current[3]

    def find_user_for_global_admin(self, user_id: str) -> tuple[bool, AccountStatus, datetime] | None:
        current = self._connection.execute(
            "SELECT is_global_admin, account_status, updated_at FROM users WHERE id=?", [user_id]
        ).fetchone()
        if not current:
            return None
        return bool(current[0]), cast(AccountStatus, current[1]), current[2]

    def count_active_global_admins(self) -> int:
        return int(
            self._connection.execute(
                "SELECT count(*) FROM users WHERE is_global_admin=true AND account_status='ACTIVE'"
            ).fetchone()[0]
        )

    def update_account_status(
        self, user_id: str, account_status: AccountStatus, actor_id: str, occurred_at: datetime
    ) -> None:
        approved_by = actor_id if account_status == "ACTIVE" else None
        approved_at = occurred_at if account_status == "ACTIVE" else None
        self._connection.execute(
            """
            UPDATE users
            SET account_status=?, is_active=?, approved_by=COALESCE(?, approved_by),
                approved_at=COALESCE(?, approved_at), updated_at=?
            WHERE id=?
            """,
            [
                account_status,
                account_status != "SUSPENDED",
                approved_by,
                approved_at,
                occurred_at,
                user_id,
            ],
        )

    def ready_pending_invitations(
        self, employee_id: str, user_id: str, actor_id: str, occurred_at: datetime
    ) -> None:
        self._connection.execute(
            """
            UPDATE project_invitations
            SET status='READY', resolved_user_id=?, resolved_by=?, resolved_at=?
            WHERE employee_id=? AND status IN ('PENDING_ACCOUNT', 'PENDING_APPROVAL')
            """,
            [user_id, actor_id, occurred_at, employee_id],
        )

    def update_global_admin(self, user_id: str, is_global_admin: bool, occurred_at: datetime) -> None:
        self._connection.execute(
            "UPDATE users SET is_global_admin=?, updated_at=? WHERE id=?",
            [is_global_admin, occurred_at, user_id],
        )

    def add_audit(self, audit: UserAdministrationAuditRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events
            (id, occurred_at, user_id, username, role, action, method, path, status_code,
             request_id, client_ip, user_agent, detail_json)
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

    def find_user(self, user_id: str) -> UserAccount | None:
        return _user_item(self._connection, user_id)


class _SQLUserAdministrationUnitOfWorkProvider:
    """Starts and locks the transaction before the command reauthorizes it."""

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
    def __call__(self) -> Iterator[SQLUserAdministrationUnitOfWork]:
        with self._connection_provider() as connection:
            connection.execute("BEGIN TRANSACTION")
            if database_settings().backend == "postgresql":
                connection.execute(
                    f"LOCK TABLE {', '.join(self._locked_tables)} IN SHARE ROW EXCLUSIVE MODE"
                )
            try:
                yield SQLUserAdministrationUnitOfWork(connection, self._authorize)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise


class SQLAccountStatusUnitOfWorkProvider(_SQLUserAdministrationUnitOfWorkProvider):
    """The status mutation always coordinates users and project invitations."""

    def __init__(
        self,
        authorize: AuthorizationCallback,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        super().__init__(authorize, ("users", "project_invitations"), connection_provider)


class SQLGlobalAdminUnitOfWorkProvider(_SQLUserAdministrationUnitOfWorkProvider):
    """The global-admin mutation only needs the users lock."""

    def __init__(
        self,
        authorize: AuthorizationCallback,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        super().__init__(authorize, ("users",), connection_provider)
