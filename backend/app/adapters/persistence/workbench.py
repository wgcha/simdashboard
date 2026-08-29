"""SQL adapter for immutable workbench catalog reads."""

from __future__ import annotations

from typing import Any, cast

from ...database_connection import ConnectionLike
from ...domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeAssignmentLockedError,
    RequestTypeAssignmentTargetNotFoundError,
    RequestTypeResolution,
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    TaskTypeVersionRead,
    WorkPlanMonitoringSummaryRead,
)
from ...repositories.workbench import WorkbenchRepository
from ...services.request_monitoring import request_monitoring_summary


def _task_type_read(item: dict[str, Any]) -> TaskTypeVersionRead:
    return {
        "id": str(item["id"]),
        "version": int(item["version"]),
        "kind": str(item["kind"]),
        "display_name": str(item["display_name"]),
        "description": str(item["description"]),
        "supports_standalone": bool(item["supports_standalone"]),
        "input_artifact_types": list(item["input_artifact_types"]),
        "output_artifact_types": list(item["output_artifact_types"]),
        "parameter_schema": dict(item["parameter_schema"]),
        "demo_artifact_url": str(item["demo_artifact_url"]),
        "is_active": bool(item["is_active"]),
        "created_at": item["created_at"],
    }


def _request_type_read(item: dict[str, Any]) -> RequestTypeVersionRead:
    return {
        "id": str(item["id"]),
        "version": int(item["version"]),
        "display_name": str(item["display_name"]),
        "description": str(item["description"]),
        "allowed_task_types": list(item["allowed_task_types"]),
        "default_workflow": dict(item["default_workflow"]),
        "match_rules": dict(item["match_rules"]),
        "is_active": bool(item["is_active"]),
        "created_at": item["created_at"],
    }


class SQLWorkbenchCatalogQuery:
    """Adapt the established workbench repository catalog facade to a read port."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._repository = WorkbenchRepository(connection)

    def list_task_types(self, *, all_versions: bool) -> list[TaskTypeVersionRead]:
        return [_task_type_read(item) for item in self._repository.list_task_types(all_versions=all_versions)]

    def list_request_types(self, *, all_versions: bool) -> list[RequestTypeVersionRead]:
        return [_request_type_read(item) for item in self._repository.list_request_types(all_versions=all_versions)]


class SQLWorkbenchRequestTypeResolutionQuery:
    """Adapt the repository's existing context/assignment/rule query sequence."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._repository = WorkbenchRepository(connection)

    def request_type_resolution(self, request_id: str) -> RequestTypeResolutionRead:
        return _request_type_resolution_read(self._repository.request_type_resolution(request_id))


def _request_type_resolution_read(stored: dict[str, Any]) -> RequestTypeResolutionRead:
    request_type = stored["request_type"]
    candidates = stored["candidates"]
    return {
        "resolution": cast(RequestTypeResolution, stored["resolution"]),
        "request_id": str(stored["request_id"]),
        "source": str(stored["source"]) if stored["source"] is not None else None,
        "reason": str(stored["reason"]),
        "request_type": _request_type_read(request_type) if request_type is not None else None,
        "candidates": [_request_type_read(item) for item in candidates],
        "decided_by": str(stored["decided_by"]) if stored["decided_by"] is not None else None,
        "decided_at": stored["decided_at"],
    }


def _work_plan_monitoring_summary_read(stored: dict[str, Any]) -> WorkPlanMonitoringSummaryRead:
    return {
        "status": str(stored["status"]),
        "progress": int(stored["progress"]),
        "current_step": str(stored["current_step"]) if stored["current_step"] is not None else None,
        "current_step_id": str(stored["current_step_id"]) if stored["current_step_id"] is not None else None,
        "completed_count": int(stored["completed_count"]) if stored["completed_count"] is not None else None,
        "total_count": int(stored["total_count"]) if stored["total_count"] is not None else None,
        "work_plan": dict(stored["work_plan"]) if stored["work_plan"] is not None else None,
        "steps": [dict(item) for item in stored["steps"]],
        "latest_demo_run": dict(stored["latest_demo_run"]) if stored["latest_demo_run"] is not None else None,
        "request_type_assignment": (
            dict(stored["request_type_assignment"])
            if stored["request_type_assignment"] is not None
            else None
        ),
    }


class SQLWorkbenchRequestWorkPlanQuery:
    """Adapt the existing request check and canonical monitoring projection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection
        self._repository = WorkbenchRepository(connection)

    def analysis_request_exists(self, request_id: str) -> bool:
        return self._repository.analysis_request_exists(request_id)

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        return _work_plan_monitoring_summary_read(request_monitoring_summary(self._connection, request_id))


class SQLWorkbenchRequestTypeAssignmentCommand:
    """Delegate the existing assignment SQL and resolution flow on one connection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._repository = WorkbenchRepository(connection)

    def has_work_plan(self, request_id: str) -> bool:
        return bool(self._repository.work_plan(request_id))

    def assign_request_type(
        self,
        request_id: str,
        command: RequestTypeAssignmentCommand,
    ) -> RequestTypeResolutionRead:
        try:
            stored = self._repository.assign_request_type(
                request_id,
                command.request_type_id,
                command.request_type_version,
                command.source,
                command.decided_by,
            )
        except PermissionError as exc:
            raise RequestTypeAssignmentLockedError() from exc
        except LookupError as exc:
            raise RequestTypeAssignmentTargetNotFoundError() from exc
        return _request_type_resolution_read(stored)
