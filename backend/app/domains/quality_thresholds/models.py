from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class QualityThreshold(TypedDict):
    criterion_key: str
    project_id: str
    analysis_key: str
    label: str
    threshold_double: float
    unit: str
    updated_by: str
    updated_at: Any


class QualityThresholdCriterion(TypedDict):
    project_id: str
    unit: str
    threshold_double: float


class QualityThresholdAuditContext(TypedDict):
    user_id: str | None
    username: str | None
    role: str | None
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class QualityThresholdAuditRecord(QualityThresholdAuditContext):
    status_code: int
    action: str
    detail: NotRequired[dict[str, Any]]
