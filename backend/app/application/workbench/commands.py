"""Framework-neutral workbench commands."""

from __future__ import annotations

from collections.abc import Callable

from ...domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeResolutionRead,
    RequestWorkPlanImmutableError,
    DemoRunInvalidError,
    WorkItemCompleteCommand,
    WorkItemLifecycleResult,
    WorkItemNotFoundError,
    WorkItemNotInProgressError,
    WorkItemNotCurrentError,
    WorkItemNotReadyError,
    WorkItemNotStartedError,
    WorkItemPrerequisiteIncompleteError,
    WorkItemProgressCommand,
    WorkItemProgressNotMonotonicError,
    WorkItemStartCommand,
    WorkItemReassignmentCommand,
    WorkItemReassignmentFinalError,
    WorkItemReassignmentState,
    ProjectAssigneeRead,
)
from ...domains.workbench.ports import (
    WorkbenchRequestTypeAssignmentCommandPort,
    WorkbenchWorkItemCompleteCommandPort,
    WorkbenchWorkItemProgressCommandPort,
    WorkbenchWorkItemStartCommandPort,
    WorkbenchWorkItemReassignmentCommandPort,
)


def assign_workbench_request_type(
    command_port: WorkbenchRequestTypeAssignmentCommandPort,
    request_id: str,
    command: RequestTypeAssignmentCommand,
) -> RequestTypeResolutionRead:
    """Guard immutable plans before delegating the legacy assignment command."""
    if command_port.has_work_plan(request_id):
        raise RequestWorkPlanImmutableError(request_id)
    return command_port.assign_request_type(request_id, command)


def update_workbench_work_item_progress(
    command_port: WorkbenchWorkItemProgressCommandPort,
    item_id: str,
    command: WorkItemProgressCommand,
    *,
    authorize: Callable[[], None],
    audit: Callable[[], None],
) -> WorkItemLifecycleResult:
    """Preserve the work-item read, authorization, audit, and sync order."""
    item = command_port.work_item(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize()
    audit()
    if item.status != "IN_PROGRESS":
        raise WorkItemNotInProgressError(item_id)
    if command.progress == item.progress:
        return WorkItemLifecycleResult(
            request_id=item.request_id,
            summary=command_port.request_monitoring_summary(item.request_id),
            changed=False,
        )
    if command.progress < item.progress:
        raise WorkItemProgressNotMonotonicError(
            current=item.progress,
            requested=command.progress,
        )

    command_port.update_work_item_progress(item_id, command)
    return WorkItemLifecycleResult(
        request_id=item.request_id,
        summary=command_port.sync_request_status(item.request_id),
        changed=True,
    )


def start_workbench_work_item(
    command_port: WorkbenchWorkItemStartCommandPort,
    item_id: str,
    command: WorkItemStartCommand,
    *,
    authorize: Callable[[], None],
    audit: Callable[[], None],
) -> WorkItemLifecycleResult:
    """Preserve the existing sequential ready-to-running start transition."""
    item = command_port.work_item(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize()
    audit()
    request_id = item.request_id
    if item.status in {"IN_PROGRESS", "COMPLETED"}:
        return WorkItemLifecycleResult(
            request_id=request_id,
            summary=command_port.request_monitoring_summary(request_id),
            changed=False,
        )

    current_item_id = command_port.current_work_item_id(request_id)
    if item.status != "READY" or current_item_id != item_id:
        raise WorkItemNotReadyError(item_id=item_id, current_item_id=current_item_id)
    if command_port.incomplete_prior_count(request_id, item.sequence_no):
        raise WorkItemPrerequisiteIncompleteError(item_id)

    command_port.start_work_item(item_id, command)
    return WorkItemLifecycleResult(
        request_id=request_id,
        summary=command_port.sync_request_status(request_id),
        changed=True,
    )


def complete_workbench_work_item(
    command_port: WorkbenchWorkItemCompleteCommandPort,
    item_id: str,
    command: WorkItemCompleteCommand,
    *,
    authorize: Callable[[], None],
    audit: Callable[[], None],
) -> WorkItemLifecycleResult:
    """Preserve the current-item, demo-run, completion, and next-ready sequence."""
    item = command_port.work_item(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize()
    audit()
    request_id = item.request_id
    if item.status == "COMPLETED":
        return WorkItemLifecycleResult(
            request_id=request_id,
            summary=command_port.request_monitoring_summary(request_id),
            changed=False,
        )

    current_item_id = command_port.current_work_item_id(request_id)
    if item.status == "READY" and current_item_id == item_id:
        raise WorkItemNotStartedError(item_id=item_id, current_item_id=current_item_id)
    if current_item_id != item_id:
        raise WorkItemNotCurrentError(item_id=item_id, current_item_id=current_item_id)
    if command_port.incomplete_prior_count(request_id, item.sequence_no):
        raise WorkItemPrerequisiteIncompleteError(item_id)
    if command.demo_run_id and not command_port.demo_run_is_succeeded_for_request(command.demo_run_id, request_id):
        raise DemoRunInvalidError(item_id=item_id, demo_run_id=command.demo_run_id)

    command_port.complete_work_item(item_id, command)
    next_item_id = command_port.next_waiting_work_item_id(request_id, item.sequence_no)
    if next_item_id:
        command_port.mark_work_item_ready(next_item_id)
    return WorkItemLifecycleResult(
        request_id=request_id,
        summary=command_port.sync_request_status(request_id),
        changed=True,
    )


def reassign_workbench_work_item(
    command_port: WorkbenchWorkItemReassignmentCommandPort,
    item_id: str,
    command: WorkItemReassignmentCommand,
    *,
    authorize: Callable[[WorkItemReassignmentState], None],
    audit: Callable[[WorkItemReassignmentState, ProjectAssigneeRead], None],
) -> dict[str, object]:
    """Preserve reassignment read, permission, owner-resolution, audit, and response order."""
    item = command_port.reassignment_state(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize(item)
    if item.status == "COMPLETED":
        raise WorkItemReassignmentFinalError(item_id)
    assignee = command_port.resolve_project_assignee(item.project_id, command.owner_user_id)
    command_port.update_work_item_assignee(item_id, assignee)
    audit(item, assignee)
    return command_port.reassigned_work_item(item_id)
