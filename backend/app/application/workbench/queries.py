"""Framework-neutral immutable workbench reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from ...domains.workbench.models import (
    RequestResultLayoutRead,
    RequestResultLayoutSnapshotRead,
    RequestResultLayoutNotFoundError,
    ResultLayoutLoadCaseNotFoundError,
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    RequestWorkPlanRead,
    TaskTypeVersionRead,
)
from ...domains.workbench.ports import (
    WorkbenchCatalogQueryPort,
    WorkbenchRequestWorkPlanQueryPort,
    WorkbenchRequestTypeResolutionQueryPort,
    WorkbenchRequestResultLayoutQueryPort,
)


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


def resolve_workbench_request_type(
    query: WorkbenchRequestTypeResolutionQueryPort,
    request_id: str,
) -> RequestTypeResolutionRead:
    """Resolve through the established repository query and decision sequence."""
    return query.request_type_resolution(request_id)


def get_workbench_request_work_plan(
    query: WorkbenchRequestWorkPlanQueryPort,
    request_id: str,
) -> RequestWorkPlanRead:
    """Preserve the legacy existence check before the canonical monitoring read."""
    if not query.analysis_request_exists(request_id):
        return {"status": "REQUEST_NOT_FOUND", "summary": None}
    summary = query.request_monitoring_summary(request_id)
    if not summary["work_plan"]:
        return {"status": "WORK_PLAN_NOT_FOUND", "summary": None}
    return {"status": "FOUND", "summary": summary}


def get_workbench_request_result_layout(
    query: WorkbenchRequestResultLayoutQueryPort,
    request_id: str,
    *,
    load_case_id: str | None,
    authorize: Callable[[str], None],
) -> RequestResultLayoutRead:
    """Preserve request scope, authorization, ownership, snapshot, binding order."""
    context = query.request_result_layout_context(request_id)
    if context is None:
        raise RequestResultLayoutNotFoundError(request_id)

    authorize(context.project_id)
    if load_case_id and not query.load_case_belongs_to_request(request_id, load_case_id):
        raise ResultLayoutLoadCaseNotFoundError(load_case_id)

    snapshot = query.result_layout_snapshot(request_id)
    if not snapshot:
        return {
            "request_id": request_id,
            "status": "UNCONFIGURED",
            "message": "이 의뢰에는 결과 화면 구성이 지정되지 않았습니다.",
        }

    result = cast(RequestResultLayoutSnapshotRead, dict(snapshot))
    result["bindings"] = query.result_layout_bindings(request_id, load_case_id)
    # No load-case/result heuristic is allowed here. Only a deliberately
    # migrated LEGACY_ASSIGNED snapshot may retain its old domain route.
    if result.get("snapshot_reason") == "LEGACY_ASSIGNED":
        result["compatibility"] = {"route_kind": "DOMAIN", "renderer": "LEGACY_DOMAIN"}
    return result
