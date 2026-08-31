from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict


WorkspaceLayoutKind = Literal["portfolio", "workflow"]


class WorkspaceLayout(TypedDict):
    project_id: str
    layout_kind: WorkspaceLayoutKind
    version: int
    definition: dict[str, Any]
    updated_by: str
    updated_at: datetime


class WorkspaceLayoutVersion(TypedDict):
    project_id: str
    layout_kind: WorkspaceLayoutKind
    version: int
    created_by: str
    created_at: datetime
    is_valid: bool


class WorkspaceLayoutAuditContext(TypedDict):
    user_id: str | None
    username: str | None
    role: str | None
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class WorkspaceLayoutAuditRecord(WorkspaceLayoutAuditContext):
    action: str
    status_code: int
    detail: NotRequired[dict[str, Any]]
