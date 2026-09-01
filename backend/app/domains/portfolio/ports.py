from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date
from typing import Any, Protocol


class PortfolioRepository(Protocol):
    def overview(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        project_id: str | None = None,
        analysis_type: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]: ...


class PortfolioRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[PortfolioRepository]: ...
