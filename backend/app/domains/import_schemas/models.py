from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


ImportSchemaDefinition = dict[str, Any]
ImportSchema = dict[str, Any]


class ImportSchemaStored(TypedDict):
    definition: ImportSchemaDefinition
    created_at: datetime


class ImportSchemaAuditContext(TypedDict):
    user_id: str | None
    username: str | None
    role: str | None
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class ImportSchemaAuditRecord(ImportSchemaAuditContext):
    status_code: int
    action: str
    detail: dict[str, Any]
