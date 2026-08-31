from __future__ import annotations

from datetime import datetime
from typing import Any, NotRequired, TypedDict


class ReportLayout(TypedDict):
    """The existing report-layout read response, expressed without an HTTP DTO."""

    id: str
    name: str
    description: str | None
    version: int
    is_system: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    updated_by: str
    definition: Any


class ReportLayoutState(TypedDict):
    version: int
    is_system: bool


class SavedReportLayout(TypedDict):
    id: str
    name: str
    description: str
    version: int
    definition: dict[str, Any]
    is_system: bool
    updated_at: datetime
    updated_by: str


class ReportLayoutVersionSummary(TypedDict):
    layout_id: str
    version: int
    created_by: str
    created_at: datetime
    is_valid: bool


class ReportLayoutVersion(TypedDict):
    layout_id: str
    version: int
    definition: Any
    created_by: str
    created_at: datetime


class ReportLayoutAuditContext(TypedDict):
    user_id: str | None
    username: str | None
    role: str | None
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class ReportLayoutAuditRecord(ReportLayoutAuditContext):
    action: str
    status_code: int
    detail: NotRequired[dict[str, Any]]


class ReportLayoutError(Exception):
    """Base error for report-layout policies and use cases."""


class InvalidReportLayoutError(ReportLayoutError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReportLayoutNotFoundError(ReportLayoutError):
    message = "보고서 레이아웃을 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ReportLayoutVersionNotFoundError(ReportLayoutError):
    message = "보고서 레이아웃 버전을 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class SystemReportLayoutDeactivationError(ReportLayoutError):
    message = "기본 레이아웃은 삭제할 수 없습니다. 수정하면 새 버전으로 보존됩니다."

    def __init__(self) -> None:
        super().__init__(self.message)
