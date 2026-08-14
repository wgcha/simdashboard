from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....adapters.persistence.reports import SQLReportLayoutRepositoryProvider
from ....application.reports.queries import list_report_layouts as list_report_layouts_query


router = APIRouter()


@router.get("/api/report-layouts")
def list_report_layouts(request: Request) -> list[dict[str, Any]]:
    # SecurityMiddleware authenticates every /api path. Accessing the principal
    # here keeps that boundary explicit without changing legacy permissions.
    return list_report_layouts_query(
        lambda: request.state.principal,
        SQLReportLayoutRepositoryProvider(),
    )
