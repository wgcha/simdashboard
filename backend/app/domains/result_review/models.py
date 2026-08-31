from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict


ReviewStatus = Literal["OPEN", "IN_REVIEW", "RESOLVED"]
ReviewEntityType = Literal["NODE", "ELEMENT"]


class ReviewItem(TypedDict):
    id: str
    bookmark_id: str
    analysis_run_id: str
    variable_key: str | None
    title: str
    time_value: float | None
    entity_type: ReviewEntityType | None
    entity_id: str | None
    body: str
    review_status: ReviewStatus
    created_by: str
    created_at: datetime
    updated_at: datetime


class CurrentReviewItem(TypedDict):
    analysis_run_id: str
    body: str
    review_status: ReviewStatus


class ResultReviewAuditContext(TypedDict):
    user_id: str | None
    username: str | None
    role: str | None
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class ResultReviewAuditRecord(ResultReviewAuditContext):
    action: str
    status_code: int
    detail: NotRequired[dict[str, Any]]
