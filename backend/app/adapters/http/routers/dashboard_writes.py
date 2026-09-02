from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.dashboard_writes import SQLDashboardWriteRepositoryProvider
from ....application.dashboard_writes.commands import (
    clone_dashboard as clone_dashboard_command,
    delete_dashboard_version as delete_dashboard_version_command,
    restore_dashboard as restore_dashboard_command,
    save_dashboard as save_dashboard_command,
)
from ....domains.dashboard_writes.errors import (
    AnalysisPageUserEditError,
    DashboardIdMismatchError,
    DashboardWriteError,
    LastDashboardHistoryError,
    LiveDashboardVersionError,
    PublishedEmptyRestoreError,
    SystemDashboardVersionError,
)
from ....modules.access_control import DASHBOARD_EDIT, require_resource_permission
from ....schemas.api import DashboardClone, DashboardDefinition


save_router = APIRouter()
history_router = APIRouter()


def _validate_dashboard_definition(definition: dict[str, object]) -> dict[str, object]:
    return DashboardDefinition.model_validate(definition).model_dump()


def _raise_mapped(error: DashboardWriteError) -> NoReturn:
    if isinstance(error, DashboardIdMismatchError):
        status_code = 400
    elif isinstance(
        error,
        (
            AnalysisPageUserEditError,
            SystemDashboardVersionError,
            LiveDashboardVersionError,
            LastDashboardHistoryError,
        ),
    ):
        status_code = 409
    elif isinstance(error, PublishedEmptyRestoreError):
        status_code = 422
    else:
        status_code = 404
    raise HTTPException(status_code, str(error)) from error


@save_router.put("/api/dashboards/{dashboard_id}")
def save_dashboard(dashboard_id: str, definition: DashboardDefinition, request: Request) -> dict[str, Any]:
    try:
        return save_dashboard_command(
            dashboard_id=dashboard_id,
            definition=definition.model_dump(),
            authorize=lambda resource_id, connection: require_resource_permission(
                request,
                DASHBOARD_EDIT,
                "dashboard",
                resource_id,
                conn=connection,
            ),
            repository_provider=SQLDashboardWriteRepositoryProvider(),
        )
    except DashboardWriteError as error:
        _raise_mapped(error)


@history_router.delete("/api/dashboards/{dashboard_id}/versions/{version}")
def delete_dashboard_version(dashboard_id: str, version: int, request: Request) -> dict[str, Any]:
    try:
        return delete_dashboard_version_command(
            dashboard_id=dashboard_id,
            version=version,
            authorize=lambda resource_id, connection: require_resource_permission(
                request,
                DASHBOARD_EDIT,
                "dashboard",
                resource_id,
                conn=connection,
            ),
            repository_provider=SQLDashboardWriteRepositoryProvider(),
        )
    except DashboardWriteError as error:
        _raise_mapped(error)


@history_router.post("/api/dashboards/{dashboard_id}/clone", status_code=201)
def clone_dashboard(dashboard_id: str, payload: DashboardClone, request: Request) -> dict[str, Any]:
    try:
        return clone_dashboard_command(
            dashboard_id=dashboard_id,
            name=payload.name,
            description=payload.description,
            principal_user_id=request.state.principal.user_id,
            authorize=lambda resource_id, connection: require_resource_permission(
                request,
                DASHBOARD_EDIT,
                "dashboard",
                resource_id,
                conn=connection,
            ),
            repository_provider=SQLDashboardWriteRepositoryProvider(),
        )
    except DashboardWriteError as error:
        _raise_mapped(error)


@history_router.post("/api/dashboards/{dashboard_id}/restore/{version}")
def restore_dashboard(dashboard_id: str, version: int, request: Request) -> dict[str, Any]:
    try:
        return restore_dashboard_command(
            dashboard_id=dashboard_id,
            version=version,
            authorize=lambda resource_id, connection: require_resource_permission(
                request,
                DASHBOARD_EDIT,
                "dashboard",
                resource_id,
                conn=connection,
            ),
            repository_provider=SQLDashboardWriteRepositoryProvider(),
            validate_definition=_validate_dashboard_definition,
        )
    except DashboardWriteError as error:
        _raise_mapped(error)
