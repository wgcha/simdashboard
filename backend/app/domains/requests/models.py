from __future__ import annotations

from typing import Any, TypedDict


class StoredAnalysisRequest(TypedDict, total=False):
    id: str
    project_id: str
    title: str
    status: str
    owner: str | None
    owner_user_id: str | None
    requested_at: Any
    due_at: Any
    overall_note: str | None


class RequestAssignee(TypedDict):
    user_id: str
    display_name: str


class RequestReassignmentAudit(TypedDict):
    user_id: str | None
    username: str | None
    role: str | None
    method: str
    path: str
    status_code: int
    request_id: str
    client_ip: str | None
    user_agent: str


class RequestNotFoundError(Exception):
    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(request_id)


class RequestAssignmentError(Exception):
    """Transport-neutral representation of the legacy assignee validation."""

    def __init__(self, detail: Any) -> None:
        self.code = detail.get("code") if isinstance(detail, dict) else "REQUEST_ASSIGNMENT_INVALID"
        self.detail = detail
        super().__init__(str(detail))
