from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.persistence.analysis_pages import SQLAnalysisPageRepositoryProvider
from ....application.analysis_pages.queries import (
    list_admin_analysis_pages,
    list_public_analysis_pages,
)
from ....domains.analysis_pages.errors import AnalysisPageError
from ....modules.access_control import DASHBOARD_EDIT, require_permission
from ....schemas.api import AnalysisPageSummary


router = APIRouter()


def _raise_mapped(error: AnalysisPageError) -> NoReturn:
    raise HTTPException(404, str(error)) from error


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
