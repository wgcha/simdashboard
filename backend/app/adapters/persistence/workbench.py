"""SQL adapter for immutable workbench catalog reads."""

from __future__ import annotations

from typing import Any, cast

from ...database_connection import ConnectionLike
from ...domains.workbench.models import (
    RequestTypeResolution,
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    TaskTypeVersionRead,
)
from ...repositories.workbench import WorkbenchRepository


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
        stored = self._repository.request_type_resolution(request_id)
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
