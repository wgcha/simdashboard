from __future__ import annotations

from typing import Protocol

from .models import (
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    TaskTypeVersionRead,
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
