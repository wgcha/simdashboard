from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Query

from ....adapters.persistence.analysis_insights import SQLAnalysisInsightsRepositoryProvider
from ....application.analysis_insights.queries import (
    compare_analysis_runs as compare_analysis_runs_query,
    get_analysis_run_trust as get_analysis_run_trust_query,
)
from ....domains.analysis_insights.errors import AnalysisInsightsError


router = APIRouter()


def _raise_mapped(error: AnalysisInsightsError) -> NoReturn:
    status = 404 if isinstance(error, LookupError) else 422
    raise HTTPException(status, error.message) from error


@router.get("/api/load-cases/{load_case_id}/run-comparison")
def compare_analysis_runs(
    load_case_id: str,
    baseline_run_id: str = Query(min_length=3, max_length=120),
    target_run_id: str = Query(min_length=3, max_length=120),
    variable_key: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    try:
        return compare_analysis_runs_query(
            load_case_id,
            baseline_run_id,
            target_run_id,
            variable_key,
            SQLAnalysisInsightsRepositoryProvider(),
        )
    except AnalysisInsightsError as error:
        _raise_mapped(error)


@router.get("/api/analysis-runs/{run_id}/trust")
def get_analysis_run_trust(run_id: str) -> dict[str, Any]:
    try:
        return get_analysis_run_trust_query(run_id, SQLAnalysisInsightsRepositoryProvider())
    except AnalysisInsightsError as error:
        _raise_mapped(error)
