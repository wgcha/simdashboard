from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...database_connection import ConnectionLike, connect, rows
from ...domains.import_schemas.models import (
    ImportSchema,
    ImportSchemaAuditRecord,
    ImportSchemaDefinition,
    ImportSchemaStored,
)
from ...domains.import_schemas.ports import ImportSchemaRepository


class SQLImportSchemaRepository:
    """SQL persistence adapter for import schemas and their version history."""

    def __init__(
        self,
        connection: ConnectionLike,
        *,
        authorize: Callable[[ConnectionLike], None] | None = None,
        audit_writer: Callable[[ImportSchemaAuditRecord, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection = connection
        self._authorize = authorize
        self._audit_writer = audit_writer

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN TRANSACTION")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def authorize_mutation(self) -> None:
        if self._authorize is not None:
            self._authorize(self._connection)

    def list_active(self) -> list[ImportSchema]:
        items = rows(
            self._connection.execute(
                "SELECT * FROM import_schemas WHERE is_active=true ORDER BY updated_at DESC"
            )
        )
        for item in items:
            item["definition"] = _json_value(item.pop("definition_json"))
        return cast(list[ImportSchema], items)

    def get_active(self, schema_id: str) -> ImportSchemaStored | None:
        existing = self._connection.execute(
            "SELECT definition_json, created_at FROM import_schemas WHERE id=? AND is_active=true",
            [schema_id],
        ).fetchone()
        if not existing:
            return None
        return {"definition": _json_value(existing[0]) or {}, "created_at": existing[1]}

    def insert_active(
        self,
        schema_id: str,
        name: str,
        description: str,
        definition: ImportSchemaDefinition,
        occurred_at: datetime,
        actor_name: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO import_schemas
                (id, name, description, definition_json, is_active, created_at, updated_at, updated_by)
            VALUES (?, ?, ?, ?, true, ?, ?, ?)
            """,
            [schema_id, name, description, _encoded(definition), occurred_at, occurred_at, actor_name],
        )

    def update_active(
        self,
        schema_id: str,
        name: str,
        description: str,
        definition: ImportSchemaDefinition,
        occurred_at: datetime,
        actor_name: str,
    ) -> None:
        self._connection.execute(
            """
            UPDATE import_schemas
            SET name=?, description=?, definition_json=?, updated_at=?, updated_by=?
            WHERE id=?
            """,
            [name, description, _encoded(definition), occurred_at, actor_name, schema_id],
        )

    def insert_version(
        self,
        schema_id: str,
        version: int,
        definition: ImportSchemaDefinition,
        occurred_at: datetime,
        actor_name: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO import_schema_versions
                (schema_id, version, definition_json, created_at, updated_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            [schema_id, version, _encoded(definition), occurred_at, actor_name],
        )

    def active_usage_count(self, schema_id: str) -> int:
        result = self._connection.execute(
            "SELECT count(*) FROM folder_import_jobs WHERE schema_id=? AND status IN ('RUNNING','COMPLETED')",
            [schema_id],
        ).fetchone()
        return int(result[0]) if result else 0

    def deactivate(self, schema_id: str, occurred_at: datetime) -> None:
        self._connection.execute(
            "UPDATE import_schemas SET is_active=false, updated_at=? WHERE id=?",
            [occurred_at, schema_id],
        )

    def add_audit(self, audit: ImportSchemaAuditRecord) -> None:
        if self._audit_writer is not None:
            self._audit_writer(audit, self._connection)
            return
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
                audit["user_id"],
                audit["username"],
                audit["role"],
                audit["action"],
                audit["method"],
                audit["path"],
                audit["status_code"],
                audit["request_id"],
                audit["client_ip"],
                audit["user_agent"],
                _encoded(audit["detail"]),
            ],
        )


class SQLImportSchemaRepositoryProvider:
    def __init__(
        self,
        connection_provider: Callable[[], Any] = connect,
        *,
        authorize: Callable[[ConnectionLike], None] | None = None,
        audit_writer: Callable[[ImportSchemaAuditRecord, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection_provider = connection_provider
        self._authorize = authorize
        self._audit_writer = audit_writer

    @contextmanager
    def __call__(self) -> Iterator[ImportSchemaRepository]:
        with self._connection_provider() as connection:
            yield SQLImportSchemaRepository(
                connection,
                authorize=self._authorize,
                audit_writer=self._audit_writer,
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
