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


@dataclass(frozen=True)
class WorkItemProgressCommand:
    """Actor-owned progress update for an already-started work item."""

    progress: int
    updated_by: str


@dataclass(frozen=True)
class WorkItemLifecycleState:
    """Minimal persisted state shared by work-item lifecycle transitions."""

    id: str
    request_id: str
    status: str
    progress: int
    sequence_no: int


@dataclass(frozen=True)
class WorkItemLifecycleResult:
    """Canonical request monitoring projection after a lifecycle command."""

    request_id: str
    summary: WorkPlanMonitoringSummaryRead
    changed: bool


class WorkItemNotFoundError(Exception):
    """The requested work item does not exist."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(item_id)


class WorkItemNotInProgressError(Exception):
    """Manual progress is valid only while the item is running."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(item_id)


class WorkItemProgressNotMonotonicError(Exception):
    """A progress update attempted to decrease an existing value."""

    def __init__(self, *, current: int, requested: int) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"{current}->{requested}")


@dataclass(frozen=True)
class WorkItemStartCommand:
    """Principal-owned request to start the next ready work item."""

    started_by: str


class WorkItemNotReadyError(Exception):
    """The requested item is not the ready item eligible to start."""

    def __init__(self, *, item_id: str, current_item_id: str | None) -> None:
        self.item_id = item_id
        self.current_item_id = current_item_id
        super().__init__(item_id)


class WorkItemPrerequisiteIncompleteError(Exception):
    """An earlier work item remains incomplete."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(item_id)


@dataclass(frozen=True)
class WorkItemCompleteCommand:
    """Principal-owned request to complete the current running work item."""

    completed_by: str
    demo_run_id: str | None


class WorkItemNotStartedError(Exception):
    """The current READY item must be started before it can complete."""

    def __init__(self, *, item_id: str, current_item_id: str | None) -> None:
        self.item_id = item_id
        self.current_item_id = current_item_id
        super().__init__(item_id)


class WorkItemNotCurrentError(Exception):
    """Only the current running work item can be completed."""

    def __init__(self, *, item_id: str, current_item_id: str | None) -> None:
        self.item_id = item_id
        self.current_item_id = current_item_id
        super().__init__(item_id)


class DemoRunInvalidError(Exception):
    """The requested demo run is absent, belongs to another request, or did not succeed."""

    def __init__(self, *, item_id: str, demo_run_id: str) -> None:
        self.item_id = item_id
        self.demo_run_id = demo_run_id
        super().__init__(demo_run_id)


@dataclass(frozen=True)
class WorkItemReassignmentCommand:
    """Target user identifier for changing an unfinished work item's owner."""

    owner_user_id: str


@dataclass(frozen=True)
class WorkItemReassignmentState:
    """Existing owner and project context needed by the reassignment flow."""

    id: str
    request_id: str
    project_id: str
    status: str
    owner_user_id: str | None


@dataclass(frozen=True)
class ProjectAssigneeRead:
    """Canonical active project member resolved for ownership."""

    user_id: str
    display_name: str


class WorkItemReassignmentFinalError(Exception):
    """Completed work items cannot be assigned again."""

    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        super().__init__(item_id)


class WorkItemAssigneeMembershipRequiredError(Exception):
    """The requested assignee is not a valid member of the work item's project."""

    def __init__(self, *, project_id: str, owner_user_id: str) -> None:
        self.project_id = project_id
        self.owner_user_id = owner_user_id
        super().__init__(owner_user_id)


class WorkItemAssigneeAccountNotActiveError(Exception):
    """The requested project member is not active."""

    def __init__(self, *, project_id: str, owner_user_id: str) -> None:
        self.project_id = project_id
        self.owner_user_id = owner_user_id
        super().__init__(owner_user_id)


@dataclass(frozen=True)
class BatchDispatchCommand:
    batch_profile_id: str | None
    idempotency_key: str
    created_by: str


@dataclass(frozen=True)
class BatchDispatchContext:
    """Immutable values fixed before the first batch-dispatch transaction."""

    work_item: dict[str, Any]
    profile: dict[str, Any]
    attempt_id: str
    started_at: datetime
    profile_snapshot_json: str
    initial_command_preview: str


@dataclass(frozen=True)
class BatchDispatchPreflight:
    """Safe, validated preview returned by batch-profile preflight."""

    command_preview: str
    working_directory_preview: str


@dataclass(frozen=True)
class BatchDispatchResult:
    """The already-projected demo run returned by a dispatch or replay."""

    run: dict[str, object]


class BatchDispatchError(Exception):
    def __init__(self, code: str, detail: dict[str, Any]):
        self.code = code
        self.detail = detail
        super().__init__(code)


class BatchAttemptAlreadyRejectedError(BatchDispatchError):
    def __init__(self, attempt: dict[str, Any]):
        super().__init__("BATCH_ATTEMPT_ALREADY_REJECTED", {"attempt": attempt})


class BatchWorkItemNotInProgressError(BatchDispatchError):
    def __init__(self, item_id: str):
        super().__init__("WORK_ITEM_NOT_IN_PROGRESS", {"item_id": item_id})


class BatchProfileTaskMismatchError(BatchDispatchError):
    def __init__(self, task_type_id: str, task_type_version: int):
        super().__init__(
            "BATCH_PROFILE_TASK_MISMATCH",
            {"task_type_id": task_type_id, "task_type_version": task_type_version},
        )


class BatchProfileNotConfiguredError(BatchDispatchError):
    def __init__(self, task_type_id: str, task_type_version: int):
        super().__init__(
            "BATCH_PROFILE_NOT_CONFIGURED",
            {"task_type_id": task_type_id, "task_type_version": task_type_version},
        )


class BatchPreflightRejectedError(BatchDispatchError):
    def __init__(self, code: str, message: str, attempt_id: str):
        super().__init__(code, {"message": message, "attempt_id": attempt_id})


class BatchPreflightFailedError(ValueError):
    """A profile failed validation before a runner record was created."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class BatchAttemptInsertConflictError(Exception):
    """The database won the idempotency-key race during phase-one insert."""


class BatchDemoRunValidationError(ValueError):
    """The DEMO_ONLY runner rejected the current work-item projection."""
