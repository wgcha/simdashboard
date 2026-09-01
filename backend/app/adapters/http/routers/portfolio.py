from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Query, Response

from ....adapters.persistence.portfolio import SQLPortfolioRepositoryProvider
from ....application.portfolio.queries import get_portfolio_overview as get_portfolio_overview_query
from ....domains.portfolio.policies import portfolio_csv


router = APIRouter()


@router.get("/api/portfolio/overview")
def get_portfolio_overview(
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: str | None = None,
    analysis_type: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    return get_portfolio_overview_query(
        SQLPortfolioRepositoryProvider(),
        date_from=date_from,
        date_to=date_to,
        project_id=project_id,
        analysis_type=analysis_type,
        status=status,
        search=search,
    )


@router.get("/api/portfolio/export.csv")
def export_portfolio_csv(
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: str | None = None,
    analysis_type: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=120),
) -> Response:
    payload = get_portfolio_overview_query(
        SQLPortfolioRepositoryProvider(),
        date_from=date_from,
        date_to=date_to,
        project_id=project_id,
        analysis_type=analysis_type,
        status=status,
        search=search,
    )
    return Response(
        portfolio_csv(payload["records"]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="analysis-portfolio.csv"'},
    )
