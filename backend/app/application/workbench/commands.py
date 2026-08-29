"""Framework-neutral workbench commands."""

from __future__ import annotations

from collections.abc import Callable

from ...domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeResolutionRead,
    RequestWorkPlanImmutableError,
    WorkItemNotFoundError,
    WorkItemNotInProgressError,
    WorkItemProgressCommand,
    WorkItemProgressNotMonotonicError,
    WorkItemProgressResult,
)
from ...domains.workbench.ports import (
    WorkbenchRequestTypeAssignmentCommandPort,
    WorkbenchWorkItemProgressCommandPort,
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
) -> WorkItemProgressResult:
    """Preserve the work-item read, authorization, audit, and sync order."""
    item = command_port.work_item(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize()
    audit()
    if item.status != "IN_PROGRESS":
        raise WorkItemNotInProgressError(item_id)
    if command.progress == item.progress:
        return WorkItemProgressResult(
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
    return WorkItemProgressResult(
        request_id=item.request_id,
        summary=command_port.sync_request_status(item.request_id),
        changed=True,
    )
