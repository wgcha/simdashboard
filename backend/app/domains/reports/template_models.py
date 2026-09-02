from __future__ import annotations

from datetime import datetime
from typing import Any, NotRequired, TypedDict


class ReportTemplate(TypedDict):
    id: str
    name: str
    filename: str
    slide_count: int
    definition: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    updated_by: str


class StoredReportTemplate(TypedDict):
    id: str
    file_path: str


class ReportTemplateInspection(TypedDict):
    slide_width: int
    slide_height: int
    slide_count: int
    placeholders: list[dict[str, Any]]


class ReportTemplateAuditContext(TypedDict):
    user_id: str | None
    username: str | None
    role: str | None
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class ReportTemplateAuditRecord(ReportTemplateAuditContext):
    action: str
    status_code: int
    detail: NotRequired[dict[str, Any]]


class ReportTemplateError(Exception):
    """Base error for the native PowerPoint-template vertical slice."""


class ReportTemplateNotFoundError(ReportTemplateError):
    message = "PPTX 템플릿을 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ReportTemplateFileMissingError(ReportTemplateError):
    message = "PPTX 템플릿 파일이 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidReportTemplateError(ReportTemplateError):
    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UnsafeReportTemplateFileError(ReportTemplateError):
    """A database path did not resolve to one managed template file."""


class ReportTemplateFileCollisionError(ReportTemplateError):
    """A generated id collided with an existing managed file."""
