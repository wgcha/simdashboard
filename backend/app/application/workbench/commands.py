"""Framework-neutral workbench commands."""

from __future__ import annotations

from ...domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeResolutionRead,
    RequestWorkPlanImmutableError,
)
from ...domains.workbench.ports import WorkbenchRequestTypeAssignmentCommandPort


def assign_workbench_request_type(
    command_port: WorkbenchRequestTypeAssignmentCommandPort,
    request_id: str,
    command: RequestTypeAssignmentCommand,
) -> RequestTypeResolutionRead:
    """Guard immutable plans before delegating the legacy assignment command."""
    if command_port.has_work_plan(request_id):
        raise RequestWorkPlanImmutableError(request_id)
    return command_port.assign_request_type(request_id, command)
