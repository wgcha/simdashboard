from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....adapters.persistence.results import SQLAnalysisRunSummaryRepositoryProvider
from ....application.results.queries import list_analysis_runs as list_analysis_runs_query
from ....modules.access_control import PROJECT_DATA_VIEW, require_permission


router = APIRouter()


@router.get("/api/load-cases/{load_case_id}/runs")
def list_analysis_runs(load_case_id: str, request: Request) -> list[dict[str, Any]]:
    return list_analysis_runs_query(
        load_case_id,
        lambda: require_permission(request, PROJECT_DATA_VIEW),
        SQLAnalysisRunSummaryRepositoryProvider(),
    )
