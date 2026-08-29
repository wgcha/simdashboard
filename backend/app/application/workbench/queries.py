"""Framework-neutral immutable workbench catalog reads."""

from __future__ import annotations

from ...domains.workbench.models import RequestTypeVersionRead, TaskTypeVersionRead
from ...domains.workbench.ports import WorkbenchCatalogQueryPort


def list_workbench_task_types(
    query: WorkbenchCatalogQueryPort,
    *,
    all_versions: bool,
) -> list[TaskTypeVersionRead]:
    """Return the existing active/latest or full task-type catalog."""
    return query.list_task_types(all_versions=all_versions)


def list_workbench_request_types(
    query: WorkbenchCatalogQueryPort,
    *,
    all_versions: bool,
) -> list[RequestTypeVersionRead]:
    """Return the existing active/latest or full request-type catalog."""
    return query.list_request_types(all_versions=all_versions)
