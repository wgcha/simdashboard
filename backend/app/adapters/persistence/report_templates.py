from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...database_connection import ConnectionLike, connect, rows
from ...domains.reports.template_models import (
    ReportTemplate,
    ReportTemplateAuditRecord,
    StoredReportTemplate,
)
from ...domains.reports.template_ports import ReportTemplateRepository


class SQLReportTemplateRepository:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def list_active_templates(self) -> list[ReportTemplate]:
        items = rows(
            self._connection.execute(
                "SELECT * FROM report_template_assets WHERE is_active=true "
                "ORDER BY updated_at DESC, name"
            )
        )
        templates: list[ReportTemplate] = []
        for item in items:
            definition = _json_value(item.pop("definition_json")) or {}
            item.pop("file_path", None)
            templates.append(cast(ReportTemplate, {**item, "definition": definition}))
        return templates

    def get_active_template(self, template_id: str) -> StoredReportTemplate | None:
        stored = self._connection.execute(
            "SELECT id, file_path FROM report_template_assets WHERE id=? AND is_active=true",
            [template_id],
        ).fetchone()
        if not stored:
            return None
        return {"id": stored[0], "file_path": stored[1]}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        begun = False
        try:
            self._connection.execute("BEGIN TRANSACTION")
            begun = True
            yield
            self._connection.execute("COMMIT")
        except Exception:
            if begun:
                try:
                    self._connection.execute("ROLLBACK")
                except Exception:
                    # Preserve the original failure so callers can reliably
                    # perform file restoration/cleanup.
                    pass
            raise

    def insert_template(
        self,
        *,
        template_id: str,
        name: str,
        filename: str,
        file_path: str,
        slide_count: int,
        definition: dict[str, object],
        actor_name: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO report_template_assets VALUES (?, ?, ?, ?, ?, ?, true, ?, ?, ?)",
            [
                template_id,
                name,
                filename,
                file_path,
                slide_count,
                json.dumps(definition, ensure_ascii=False),
                occurred_at,
                occurred_at,
                actor_name,
            ],
        )

    def add_upload_audit(self, audit: ReportTemplateAuditRecord) -> None:
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

    def deactivate(self, template_id: str, occurred_at: datetime) -> None:
        self._connection.execute(
            "UPDATE report_template_assets SET is_active=false, updated_at=? WHERE id=?",
            [occurred_at, template_id],
        )


class SQLReportTemplateRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ReportTemplateRepository]:
        with self._connection_provider() as connection:
            yield SQLReportTemplateRepository(connection)


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
