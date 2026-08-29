"""Framework-neutral workbench commands."""

from __future__ import annotations

from collections.abc import Callable

from ...domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeResolutionRead,
    RequestWorkPlanImmutableError,
    WorkItemLifecycleResult,
    WorkItemNotFoundError,
    WorkItemNotInProgressError,
    WorkItemNotReadyError,
    WorkItemPrerequisiteIncompleteError,
    WorkItemProgressCommand,
    WorkItemProgressNotMonotonicError,
    WorkItemStartCommand,
)
from ...domains.workbench.ports import (
    WorkbenchRequestTypeAssignmentCommandPort,
    WorkbenchWorkItemProgressCommandPort,
    WorkbenchWorkItemStartCommandPort,
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
