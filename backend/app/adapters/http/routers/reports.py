from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.reports import SQLReportLayoutRepositoryProvider
from ....application.reports.commands import (
    create_report_layout as create_report_layout_command,
    deactivate_report_layout,
    update_report_layout as update_report_layout_command,
)
from ....application.reports.queries import (
    get_report_layout_version as get_report_layout_version_query,
    list_report_layout_versions,
    list_report_layouts as list_report_layouts_query,
)
from ....domains.reports.models import (
    InvalidReportLayoutError,
    ReportLayoutAuditContext,
    ReportLayoutError,
    ReportLayoutNotFoundError,
    ReportLayoutVersionNotFoundError,
    SystemReportLayoutDeactivationError,
)
from ....modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ....schemas.api import ReportLayoutPayload
from ....schemas.reports import (
    ReportLayoutCatalogResponse,
    ReportLayoutDeactivationResponse,
    ReportLayoutSavedResponse,
    ReportLayoutVersionDetailResponse,
    ReportLayoutVersionSummaryResponse,
)


router = APIRouter()


def _active(request: Request):
    return lambda: request.state.principal


def _manage(request: Request):
    return lambda: require_permission(request, SYSTEM_CATALOG_MANAGE)


def _audit(request: Request) -> ReportLayoutAuditContext:
    principal = request.state.principal
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "method": request.method,
        "path": request.url.path,
        "request_id": getattr(request.state, "request_id", str(uuid4())),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }


def _raise_mapped(error: ReportLayoutError) -> NoReturn:
    if isinstance(error, InvalidReportLayoutError):
        raise HTTPException(422, error.message) from error
    if isinstance(error, ReportLayoutNotFoundError):
        raise HTTPException(404, error.message) from error
    if isinstance(error, ReportLayoutVersionNotFoundError):
        raise HTTPException(404, error.message) from error
    if isinstance(error, SystemReportLayoutDeactivationError):
        raise HTTPException(409, error.message) from error
    raise error


@router.get(
    "/api/report-layouts",
    response_model=list[ReportLayoutCatalogResponse],
    operation_id="list_report_layouts_api_report_layouts_get",
)
def list_report_layouts(request: Request) -> list[dict[str, Any]]:
    return list_report_layouts_query(
        _active(request),
        SQLReportLayoutRepositoryProvider(),
    )


@router.post(
    "/api/report-layouts",
    response_model=ReportLayoutSavedResponse,
    status_code=201,
    operation_id="create_report_layout_api_report_layouts_post",
)
def create_report_layout(payload: ReportLayoutPayload, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    try:
        return create_report_layout_command(
            name=payload.name,
            description=payload.description,
            definition=payload.definition,
            actor_name=principal.display_name,
            audit=_audit(request),
            authorize=_manage(request),
            repository_provider=SQLReportLayoutRepositoryProvider(),
        )
    except ReportLayoutError as error:
        _raise_mapped(error)


@router.put(
    "/api/report-layouts/{layout_id}",
    response_model=ReportLayoutSavedResponse,
    operation_id="update_report_layout_api_report_layouts__layout_id__put",
)
def update_report_layout(
    layout_id: str,
    payload: ReportLayoutPayload,
    request: Request,
) -> dict[str, Any]:
    principal = request.state.principal
    try:
        return update_report_layout_command(
            layout_id=layout_id,
            name=payload.name,
            description=payload.description,
            definition=payload.definition,
            actor_name=principal.display_name,
            audit=_audit(request),
            authorize=_manage(request),
            repository_provider=SQLReportLayoutRepositoryProvider(),
        )
    except ReportLayoutError as error:
        _raise_mapped(error)


@router.get(
    "/api/report-layouts/{layout_id}/versions",
    response_model=list[ReportLayoutVersionSummaryResponse],
    operation_id="get_report_layout_versions_api_report_layouts__layout_id__versions_get",
)
def get_report_layout_versions(layout_id: str, request: Request) -> list[dict[str, Any]]:
    return list_report_layout_versions(
        layout_id,
        _active(request),
        SQLReportLayoutRepositoryProvider(),
    )


@router.get(
    "/api/report-layouts/{layout_id}/versions/{version}",
    response_model=ReportLayoutVersionDetailResponse,
    operation_id=(
        "get_report_layout_version_api_report_layouts__layout_id__versions__version__get"
    ),
)
def get_report_layout_version(
    layout_id: str,
    version: int,
    request: Request,
) -> dict[str, Any]:
    try:
        return get_report_layout_version_query(
            layout_id,
            version,
            _active(request),
            SQLReportLayoutRepositoryProvider(),
        )
    except ReportLayoutError as error:
        _raise_mapped(error)


@router.delete(
    "/api/report-layouts/{layout_id}",
    response_model=ReportLayoutDeactivationResponse,
    operation_id="delete_report_layout_api_report_layouts__layout_id__delete",
)
def delete_report_layout(layout_id: str, request: Request) -> dict[str, str]:
    try:
        return deactivate_report_layout(
            layout_id=layout_id,
            authorize=_manage(request),
            repository_provider=SQLReportLayoutRepositoryProvider(),
        )
    except ReportLayoutError as error:
        _raise_mapped(error)
