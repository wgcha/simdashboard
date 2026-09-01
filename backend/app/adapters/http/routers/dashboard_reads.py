from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.dashboard_reads import SQLDashboardReadRepositoryProvider
from ....application.dashboard_reads.queries import (
    get_dashboard as get_dashboard_query,
    get_dashboard_version as get_dashboard_version_query,
    get_dashboard_versions as get_dashboard_versions_query,
    list_dashboards as list_dashboards_query,
)
from ....domains.dashboard_reads.errors import (
    DashboardNotFoundError,
    DashboardVersionNotFoundError,
)
from ....modules.access_control import DASHBOARD_EDIT, has_permission, require_permission, require_resource_permission


router = APIRouter()
version_router = APIRouter()


def _dashboard_not_found(error: DashboardNotFoundError | DashboardVersionNotFoundError) -> NoReturn:
    if isinstance(error, DashboardVersionNotFoundError):
        raise HTTPException(404, "대시보드 버전을 찾을 수 없습니다.") from error
    raise HTTPException(404, "대시보드를 찾을 수 없습니다.") from error


def _project_authorizer(request: Request):
    return lambda project_id, connection: require_permission(request, DASHBOARD_EDIT, project_id, conn=connection)


def _project_permission_check(request: Request):
    return lambda project_id, connection: has_permission(request, DASHBOARD_EDIT, project_id, conn=connection)


def _resource_authorizer(request: Request):
    return lambda dashboard_id, connection: require_resource_permission(
        request,
        DASHBOARD_EDIT,
        "dashboard",
        dashboard_id,
        conn=connection,
    )


@router.get("/api/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str, request: Request) -> dict[str, Any]:
    try:
        return get_dashboard_query(
            dashboard_id,
            SQLDashboardReadRepositoryProvider(),
            _project_authorizer(request),
        )
    except (DashboardNotFoundError, DashboardVersionNotFoundError) as error:
        _dashboard_not_found(error)


@router.get("/api/dashboards")
def list_dashboards(request: Request, project_id: str | None = None) -> list[dict[str, Any]]:
    return list_dashboards_query(
        SQLDashboardReadRepositoryProvider(),
        project_id,
        _project_permission_check(request),
    )


@version_router.get("/api/dashboards/{dashboard_id}/versions")
def get_dashboard_versions(
    dashboard_id: str,
    request: Request,
    include_invalid: bool = False,
) -> list[dict[str, Any]]:
    try:
        return get_dashboard_versions_query(
            dashboard_id,
            SQLDashboardReadRepositoryProvider(),
            include_invalid,
            _resource_authorizer(request),
        )
    except (DashboardNotFoundError, DashboardVersionNotFoundError) as error:
        _dashboard_not_found(error)


@version_router.get("/api/dashboards/{dashboard_id}/versions/{version}")
def get_dashboard_version(
    dashboard_id: str,
    version: int,
    request: Request,
    include_invalid: bool = False,
) -> dict[str, Any]:
    try:
        return get_dashboard_version_query(
            dashboard_id,
            version,
            SQLDashboardReadRepositoryProvider(),
            include_invalid,
            _resource_authorizer(request),
        )
    except (DashboardNotFoundError, DashboardVersionNotFoundError) as error:
        _dashboard_not_found(error)
