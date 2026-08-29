from __future__ import annotations

from dataclasses import dataclass
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


class WorkPlanMonitoringSummaryRead(TypedDict):
    """Canonical monitoring projection shared by workbench and portfolio reads."""

    status: str
    progress: int
    current_step: str | None
    current_step_id: str | None
    completed_count: int | None
    total_count: int | None
    work_plan: dict[str, Any] | None
    steps: list[dict[str, Any]]
    latest_demo_run: dict[str, Any] | None
    request_type_assignment: dict[str, Any] | None


RequestWorkPlanReadStatus = Literal["FOUND", "REQUEST_NOT_FOUND", "WORK_PLAN_NOT_FOUND"]


class RequestWorkPlanRead(TypedDict):
    """Transport-neutral outcome for the request work-plan read."""

    status: RequestWorkPlanReadStatus
    summary: WorkPlanMonitoringSummaryRead | None


RequestTypeAssignmentSource = Literal["ADMIN", "USER"]


@dataclass(frozen=True)
class RequestTypeAssignmentCommand:
    """Actor-owned inputs for assigning an immutable request-type version."""

    request_type_id: str
    request_type_version: int
    source: RequestTypeAssignmentSource
    decided_by: str


class RequestWorkPlanImmutableError(Exception):
    """The request already has its immutable work-plan snapshot."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(request_id)


class RequestTypeAssignmentLockedError(Exception):
    """A non-admin actor attempted to replace an admin-fixed assignment."""


class RequestTypeAssignmentTargetNotFoundError(Exception):
    """The request or active immutable request-type version does not exist."""
