from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.persistence.analysis_pages import SQLAnalysisPageRepositoryProvider
from ....adapters.persistence.analysis_pages import SQLAnalysisPageCommandRepositoryProvider
from ....application.analysis_pages.commands import (
    create_analysis_page,
    delete_analysis_page,
    reorder_analysis_pages,
    update_analysis_page,
)
from ....application.analysis_pages.queries import (
    list_admin_analysis_pages,
    list_public_analysis_pages,
)
from ....domains.analysis_pages.errors import (
    AnalysisPageCommandError,
    AnalysisPageError,
    AnalysisPageLoadCaseMismatchError,
    AnalysisPageNameConflictError,
    AnalysisPageNotFoundError,
    AnalysisPageNotManageableError,
    CustomAnalysisPageDeletionError,
    SystemAnalysisPageLifecycleError,
)
from ....modules.access_control import DASHBOARD_EDIT, require_permission
from ....modules.access_control import require_resource_permission
from ....schemas.api import AnalysisPageCreate, AnalysisPageOrderUpdate, AnalysisPageSummary, AnalysisPageUpdate, DashboardDefinition


router = APIRouter()


def _validate_dashboard_definition(definition: dict[str, object]) -> dict[str, object]:
    return DashboardDefinition.model_validate(definition).model_dump()


def _raise_mapped(error: AnalysisPageError) -> NoReturn:
    if isinstance(error, (AnalysisPageNotFoundError, AnalysisPageNotManageableError)):
        status_code = 404
    elif isinstance(
        error,
        (
            AnalysisPageNameConflictError,
            SystemAnalysisPageLifecycleError,
            CustomAnalysisPageDeletionError,
            AnalysisPageLoadCaseMismatchError,
        ),
    ):
        status_code = 409
    elif isinstance(error, AnalysisPageCommandError):
        status_code = 422
    else:
        status_code = 404
    raise HTTPException(status_code, str(error)) from error


@router.get("/api/dashboard-pages", response_model=list[AnalysisPageSummary])
def list_public_dashboard_pages(load_case_id: str = Query(min_length=1, max_length=120)) -> list[dict[str, Any]]:
    try:
        return list_public_analysis_pages(load_case_id, SQLAnalysisPageRepositoryProvider())
    except AnalysisPageError as error:
        _raise_mapped(error)


@router.get("/api/admin/dashboard-pages", response_model=list[AnalysisPageSummary])
def list_admin_dashboard_pages(
    request: Request,
    load_case_id: str = Query(min_length=1, max_length=120),
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    try:
        return list_admin_analysis_pages(
            load_case_id,
            include_archived,
            lambda project_id, connection: require_permission(request, DASHBOARD_EDIT, project_id, conn=connection),
            SQLAnalysisPageRepositoryProvider(),
        )
    except AnalysisPageError as error:
        _raise_mapped(error)


@router.post("/api/admin/dashboard-pages", response_model=DashboardDefinition, status_code=201)
def create_dashboard_page(payload: AnalysisPageCreate, request: Request) -> dict[str, Any]:
    try:
        return create_analysis_page(
            load_case_id=payload.load_case_id,
            name=payload.name,
            description=payload.description,
            authorize=lambda project_id, connection: require_permission(request, DASHBOARD_EDIT, project_id, conn=connection),
            repository_provider=SQLAnalysisPageCommandRepositoryProvider(),
            validate_definition=_validate_dashboard_definition,
        )
    except AnalysisPageError as error:
        _raise_mapped(error)


@router.patch("/api/admin/dashboard-pages/{dashboard_id}", response_model=DashboardDefinition)
def update_dashboard_page(
    dashboard_id: str,
    payload: AnalysisPageUpdate,
    request: Request,
) -> dict[str, Any]:
    try:
        return update_analysis_page(
            dashboard_id=dashboard_id,
            name=payload.name,
            description=payload.description,
            status=payload.status,
            authorize=lambda resource_id, connection: require_resource_permission(
                request,
                DASHBOARD_EDIT,
                "dashboard",
                resource_id,
                conn=connection,
            ),
            repository_provider=SQLAnalysisPageCommandRepositoryProvider(),
            validate_definition=_validate_dashboard_definition,
        )
    except AnalysisPageError as error:
        _raise_mapped(error)


@router.delete("/api/admin/dashboard-pages/{dashboard_id}")
def delete_dashboard_page(
    dashboard_id: str,
    request: Request,
    load_case_id: str = Query(min_length=1, max_length=120),
) -> dict[str, str]:
    try:
        return delete_analysis_page(
            dashboard_id=dashboard_id,
            load_case_id=load_case_id,
            authorize=lambda resource_id, connection: require_resource_permission(
                request,
                DASHBOARD_EDIT,
                "dashboard",
                resource_id,
                conn=connection,
            ),
            repository_provider=SQLAnalysisPageCommandRepositoryProvider(),
        )
    except AnalysisPageError as error:
        _raise_mapped(error)


@router.put("/api/admin/dashboard-pages/order", response_model=list[AnalysisPageSummary])
def reorder_dashboard_pages(payload: AnalysisPageOrderUpdate, request: Request) -> list[dict[str, Any]]:
    try:
        return reorder_analysis_pages(
            load_case_id=payload.load_case_id,
            page_ids=payload.page_ids,
            authorize=lambda project_id, connection: require_permission(request, DASHBOARD_EDIT, project_id, conn=connection),
            repository_provider=SQLAnalysisPageCommandRepositoryProvider(),
            validate_definition=_validate_dashboard_definition,
        )
    except AnalysisPageError as error:
        _raise_mapped(error)
