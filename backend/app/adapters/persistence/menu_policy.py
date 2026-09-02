"""SQL adapters for menu-policy reads, snapshots, and locked mutations."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...config import database_settings
from ...database import json_value
from ...database_connection import ConnectionLike, connect, rows
from ...domains.menu_policy.models import (
    MenuPolicy,
    MenuPolicyAuditRecord,
    MenuPolicyVersion,
    MenuPolicyVersionSummary,
    MenuVisibility,
)
from ...domains.menu_policy.ports import MenuPolicyReader, MenuPolicyUnitOfWork


AuthorizationCallback = Callable[[ConnectionLike], None]
ConnectionProvider = Callable[[], Any]
_SAFE_LOCK_TABLES = ("menu_policy_state", "role_menu_policies")


class SQLMenuPolicyReader(MenuPolicyReader):
    def __init__(self, connection: ConnectionLike, authorize: AuthorizationCallback) -> None:
        self._connection = connection
        self._authorize = authorize

    def authorize_manage(self) -> None:
        self._authorize(self._connection)

    def get_policy(self) -> MenuPolicy | None:
        state = self._connection.execute(
            "SELECT version, updated_by, updated_at FROM menu_policy_state WHERE id='global'"
        ).fetchone()
        if not state:
            return None
        stored = rows(
            self._connection.execute(
                """
                SELECT definitions.id, definitions.label, definitions.required_permission,
                       definitions.context_kind, definitions.sequence_no, definitions.is_policy_editable,
                       policies.role, policies.is_visible
                FROM menu_definitions definitions
                JOIN role_menu_policies policies ON policies.menu_id=definitions.id
                WHERE definitions.is_active=true
                ORDER BY definitions.sequence_no, definitions.id, policies.role
                """
            )
        )
        items: dict[str, dict[str, Any]] = {}
        for row in stored:
            item = items.setdefault(
                row["id"],
                {
                    "id": row["id"],
                    "label": row["label"],
                    "required_permission": row["required_permission"],
                    "context_kind": row["context_kind"],
                    "sequence_no": row["sequence_no"],
                    "is_policy_editable": bool(row["is_policy_editable"]),
                    "visibility": {},
                },
            )
            item["visibility"][row["role"]] = bool(row["is_visible"])
        return {
            "version": int(state[0]),
            "updated_by": state[1],
            "updated_at": state[2],
            "menus": list(items.values()),
        }

    def list_versions(self) -> list[MenuPolicyVersionSummary]:
        return cast(
            list[MenuPolicyVersionSummary],
            rows(
                self._connection.execute(
                    """
                    SELECT version, created_by, created_at, source_version, change_note
                    FROM menu_policy_versions ORDER BY version DESC
                    """
                )
            ),
        )

    def get_version(self, version: int) -> MenuPolicyVersion | None:
        row = self._connection.execute(
            """
            SELECT version, definition_json, created_by, created_at, source_version, change_note
            FROM menu_policy_versions WHERE version=?
            """,
            [version],
        ).fetchone()
        if not row:
            return None
        return {
            "version": int(row[0]),
            "visibility": cast(MenuVisibility, json_value(row[1]) or {}),
            "created_by": row[2],
            "created_at": row[3],
            "source_version": row[4],
            "change_note": row[5],
        }


class SQLMenuPolicyReaderProvider:
    def __init__(
        self,
        authorize: AuthorizationCallback,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        self._authorize = authorize
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[MenuPolicyReader]:
        with self._connection_provider() as connection:
            yield SQLMenuPolicyReader(connection, self._authorize)


class SQLMenuPolicyUnitOfWork(SQLMenuPolicyReader):
    def write_snapshot(
        self,
        *,
        version: int,
        visibility: MenuVisibility,
        actor_id: str,
        occurred_at: datetime,
        source_version: int | None,
        change_note: str,
    ) -> None:
        for role, role_visibility in visibility.items():
            for menu_id, is_visible in role_visibility.items():
                self._connection.execute(
                    """
                    UPDATE role_menu_policies
                    SET is_visible=?, policy_version=?, updated_by=?, updated_at=?
                    WHERE role=? AND menu_id=?
                    """,
                    [is_visible, version, actor_id, occurred_at, role, menu_id],
                )
        self._connection.execute(
            "UPDATE menu_policy_state SET version=?, updated_by=?, updated_at=? WHERE id='global'",
            [version, actor_id, occurred_at],
        )
        self._connection.execute(
            """
            INSERT INTO menu_policy_versions
                (version, definition_json, created_by, created_at, source_version, change_note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                version,
                json.dumps(visibility, ensure_ascii=False, sort_keys=True),
                actor_id,
                occurred_at,
                source_version,
                change_note,
            ],
        )

    def add_audit(self, audit: MenuPolicyAuditRecord) -> None:
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


class SQLMenuPolicyUnitOfWorkProvider:
    """Starts fixed provider locks before the use case reauthorizes the caller."""

    def __init__(
        self,
        authorize: AuthorizationCallback,
        connection_provider: ConnectionProvider = connect,
    ) -> None:
        self._authorize = authorize
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[MenuPolicyUnitOfWork]:
        with self._connection_provider() as connection:
            connection.execute("BEGIN TRANSACTION")
            if database_settings().backend == "postgresql":
                connection.execute(
                    f"LOCK TABLE {', '.join(_SAFE_LOCK_TABLES)} IN SHARE ROW EXCLUSIVE MODE"
                )
            try:
                yield SQLMenuPolicyUnitOfWork(connection, self._authorize)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
