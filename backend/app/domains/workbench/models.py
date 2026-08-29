from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict


class TaskTypeVersionRead(TypedDict):
    """Decoded public task-type version returned by the catalog read."""

    id: str
    version: int
    kind: str
    display_name: str
    description: str
    supports_standalone: bool
    input_artifact_types: list[Any]
    output_artifact_types: list[Any]
    parameter_schema: dict[str, Any]
    demo_artifact_url: str
    is_active: bool
    created_at: datetime


class RequestTypeVersionRead(TypedDict):
    """Decoded public request-type version returned by the catalog read."""

    id: str
    version: int
    display_name: str
    description: str
    allowed_task_types: list[dict[str, Any]]
    default_workflow: dict[str, Any]
    match_rules: dict[str, Any]
    is_active: bool
    created_at: datetime


RequestTypeResolution = Literal["ASSIGNED", "RECOMMENDED", "REVIEW_REQUIRED", "USER_SELECTION"]


class RequestTypeResolutionRead(TypedDict):
    """The existing four-state request-type resolution projection."""

    resolution: RequestTypeResolution
    request_id: str
    source: str | None
    reason: str
    request_type: RequestTypeVersionRead | None
    candidates: list[RequestTypeVersionRead]
    decided_by: str | None
    decided_at: datetime | None
