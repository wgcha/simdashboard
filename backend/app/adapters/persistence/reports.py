from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...database_connection import ConnectionLike, connect, rows
from ...domains.reports.models import (
    ReportLayout,
    ReportLayoutAuditRecord,
    ReportLayoutState,
    ReportLayoutVersion,
    ReportLayoutVersionSummary,
)
from ...domains.reports.ports import ReportLayoutRepository


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class SQLReportLayoutRepository:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def list_active_layouts(self) -> list[ReportLayout]:
        items = rows(
            self._connection.execute(
                "SELECT * FROM report_layouts WHERE is_active=true "
                "ORDER BY is_system DESC, updated_at DESC, name"
            )
        )
        layouts: list[ReportLayout] = []
        for item in items:
            definition = _json_value(item.pop("definition_json")) or {}
            definition.update(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "description": item.get("description") or "",
                    "version": item["version"],
                }
            )
            layouts.append(cast(ReportLayout, {**item, "definition": definition}))
        return layouts

    def get_active_state(self, layout_id: str) -> ReportLayoutState | None:
        stored = self._connection.execute(
            "SELECT version, is_system FROM report_layouts WHERE id=? AND is_active=true",
            [layout_id],
        ).fetchone()
        if not stored:
            return None
        return {"version": int(stored[0]), "is_system": bool(stored[1])}

    def list_versions(self, layout_id: str) -> list[ReportLayoutVersionSummary]:
        return cast(
            list[ReportLayoutVersionSummary],
            rows(
                self._connection.execute(
                    "SELECT layout_id, version, created_by, created_at, is_valid "
                    "FROM report_layout_versions WHERE layout_id=? ORDER BY version DESC",
                    [layout_id],
                )
            ),
        )

    def get_version(self, layout_id: str, version: int) -> ReportLayoutVersion | None:
        stored = self._connection.execute(
            "SELECT definition_json, created_by, created_at FROM report_layout_versions "
            "WHERE layout_id=? AND version=? AND is_valid=true",
            [layout_id, version],
        ).fetchone()
        if not stored:
            return None
        return {
            "layout_id": layout_id,
            "version": version,
            "definition": _json_value(stored[0]),
            "created_by": stored[1],
            "created_at": stored[2],
        }

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN TRANSACTION")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise

    def insert_layout(
        self,
        *,
        layout_id: str,
        name: str,
        description: str,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO report_layouts VALUES (?, ?, ?, 1, ?, false, true, ?, ?, ?)",
            [
                layout_id,
                name,
                description,
                _encoded(definition),
                occurred_at,
                occurred_at,
                actor_name,
            ],
        )

    def update_layout(
        self,
        *,
        layout_id: str,
        name: str,
        description: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            "UPDATE report_layouts SET name=?, description=?, version=?, definition_json=?, "
            "updated_at=?, updated_by=? WHERE id=?",
            [
                name,
                description,
                version,
                _encoded(definition),
                occurred_at,
                actor_name,
                layout_id,
            ],
        )

    def insert_version(
        self,
        *,
        layout_id: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO report_layout_versions VALUES (?, ?, ?, ?, ?, true)",
            [layout_id, version, _encoded(definition), actor_name, occurred_at],
        )

    def add_audit(self, audit: ReportLayoutAuditRecord) -> None:
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

    def deactivate(self, layout_id: str, occurred_at: datetime) -> None:
        self._connection.execute(
            "UPDATE report_layouts SET is_active=false, updated_at=? WHERE id=?",
            [occurred_at, layout_id],
        )


class SQLReportLayoutRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ReportLayoutRepository]:
        with self._connection_provider() as connection:
            yield SQLReportLayoutRepository(connection)


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
