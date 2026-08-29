from __future__ import annotations

from typing import Protocol

from .models import (
    RequestTypeAssignmentCommand,
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    TaskTypeVersionRead,
    WorkItemLifecycleState,
    WorkItemProgressCommand,
    WorkItemStartCommand,
    WorkPlanMonitoringSummaryRead,
)


class WorkbenchCatalogQueryPort(Protocol):
    """Read port for immutable task and request type catalogs."""

    def list_task_types(self, *, all_versions: bool) -> list[TaskTypeVersionRead]: ...

    def list_request_types(self, *, all_versions: bool) -> list[RequestTypeVersionRead]: ...


class WorkbenchRequestTypeResolutionQueryPort(Protocol):
    """Read port for resolving one request to its immutable type version."""

    def request_type_resolution(self, request_id: str) -> RequestTypeResolutionRead: ...


class WorkbenchRequestWorkPlanQueryPort(Protocol):
    """Read port for request existence and its canonical monitoring projection."""

    def analysis_request_exists(self, request_id: str) -> bool: ...

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...


class WorkbenchRequestTypeAssignmentCommandPort(Protocol):
    """Command port for the existing request-type assignment persistence flow."""

    def has_work_plan(self, request_id: str) -> bool: ...

    def assign_request_type(
        self,
        request_id: str,
        command: RequestTypeAssignmentCommand,
    ) -> RequestTypeResolutionRead: ...


class WorkbenchWorkItemProgressCommandPort(Protocol):
    """Same-connection persistence operations for progress updates."""

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None: ...

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...

    def update_work_item_progress(
        self,
        item_id: str,
        command: WorkItemProgressCommand,
    ) -> None: ...

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...


class WorkbenchWorkItemStartCommandPort(Protocol):
    """Same-connection persistence operations for the start transition."""

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None: ...

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...

    def current_work_item_id(self, request_id: str) -> str | None: ...

    def incomplete_prior_count(self, request_id: str, sequence_no: int) -> int: ...

    def start_work_item(self, item_id: str, command: WorkItemStartCommand) -> None: ...

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...
