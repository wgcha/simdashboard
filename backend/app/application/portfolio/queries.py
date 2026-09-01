from __future__ import annotations

from datetime import date
from typing import Any

from ...domains.portfolio.ports import PortfolioRepositoryProvider


def get_portfolio_overview(
    repository_provider: PortfolioRepositoryProvider,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: str | None = None,
    analysis_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    with repository_provider() as repository:
        return repository.overview(
            date_from=date_from,
            date_to=date_to,
            project_id=project_id,
            analysis_type=analysis_type,
            status=status,
            search=search,
        )
