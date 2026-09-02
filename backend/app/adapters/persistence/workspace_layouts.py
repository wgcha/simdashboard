from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...database_connection import ConnectionLike, connect, rows
from ...domains.workspace_layouts.models import (
    WorkspaceLayout,
    WorkspaceLayoutAuditRecord,
    WorkspaceLayoutVersion,
)
from ...domains.workspace_layouts.ports import WorkspaceLayoutRepository


class SQLWorkspaceLayoutRepository:
    """SQL adapter for per-project workspace-layout state and history."""

    def __init__(
        self,
        connection: ConnectionLike,
        *,
        authorize_read: Callable[[str, ConnectionLike], None] | None = None,
        authorize_write: Callable[[str, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection = connection
        self._authorize_read = authorize_read
        self._authorize_write = authorize_write

    def project_exists(self, project_id: str) -> bool:
        return bool(self._connection.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone())

    def authorize_read(self, project_id: str) -> None:
        if self._authorize_read is not None:
            self._authorize_read(project_id, self._connection)

    def authorize_write(self, project_id: str) -> None:
        if self._authorize_write is not None:
            self._authorize_write(project_id, self._connection)

    def get_layout(self, project_id: str, layout_kind: str) -> WorkspaceLayout | None:
        stored = self._connection.execute(
            """
            SELECT project_id, layout_kind, version, definition_json, updated_by, updated_at
            FROM project_workspace_layouts WHERE project_id=? AND layout_kind=?
            """,
            [project_id, layout_kind],
        ).fetchone()
        if not stored:
            return None
        return {
            "project_id": stored[0],
            "layout_kind": stored[1],
            "version": stored[2],
            "definition": _json_value(stored[3]),
            "updated_by": stored[4],
            "updated_at": stored[5],
        }

    def get_layout_version(self, project_id: str, layout_kind: str) -> int | None:
        stored = self._connection.execute(
            "SELECT version FROM project_workspace_layouts WHERE project_id=? AND layout_kind=?",
            [project_id, layout_kind],
        ).fetchone()
        return int(stored[0]) if stored else None

    def list_versions(self, project_id: str, layout_kind: str) -> list[WorkspaceLayoutVersion]:
        return cast(
            list[WorkspaceLayoutVersion],
            rows(
                self._connection.execute(
                    """
                    SELECT project_id, layout_kind, version, created_by, created_at, is_valid
                    FROM project_workspace_layout_versions
                    WHERE project_id=? AND layout_kind=? ORDER BY version DESC
                    """,
                    [project_id, layout_kind],
                )
            ),
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN TRANSACTION")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    def update_layout(
        self,
        project_id: str,
        layout_kind: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            UPDATE project_workspace_layouts
            SET version=?, definition_json=?, updated_by=?, updated_at=?
            WHERE project_id=? AND layout_kind=?
            """,
            [version, _encoded(definition), actor_name, occurred_at, project_id, layout_kind],
        )

    def insert_version(
        self,
        project_id: str,
        layout_kind: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO project_workspace_layout_versions
                (project_id, layout_kind, version, definition_json, created_by, created_at, is_valid)
            VALUES (?, ?, ?, ?, ?, ?, true)
            """,
            [project_id, layout_kind, version, _encoded(definition), actor_name, occurred_at],
        )

    def add_audit(self, audit: WorkspaceLayoutAuditRecord) -> None:
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
                _encoded(audit.get("detail", {})),
            ],
        )


class SQLWorkspaceLayoutRepositoryProvider:
    def __init__(
        self,
        connection_provider: Callable[[], Any] = connect,
        *,
        authorize_read: Callable[[str, ConnectionLike], None] | None = None,
        authorize_write: Callable[[str, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection_provider = connection_provider
        self._authorize_read = authorize_read
        self._authorize_write = authorize_write

    @contextmanager
    def __call__(self) -> Iterator[WorkspaceLayoutRepository]:
        with self._connection_provider() as connection:
            yield SQLWorkspaceLayoutRepository(
                connection,
                authorize_read=self._authorize_read,
                authorize_write=self._authorize_write,
            )


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
