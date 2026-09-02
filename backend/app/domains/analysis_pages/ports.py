from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Callable, Protocol


class AnalysisPageRepository(Protocol):
    def load_case_context(self, load_case_id: str) -> tuple[str, str] | None: ...

    def authorize(self, callback: Callable[[object], None]) -> None: ...

    def list_analysis_pages(
        self,
        load_case_id: str,
        *,
        include_private: bool,
        include_archived: bool,
    ) -> list[dict[str, Any]]: ...


class AnalysisPageCommandRepository(AnalysisPageRepository, Protocol):
    """One-open-connection persistence contract for analysis-page commands."""

    def authorize_resource(self, callback: Callable[[object], None]) -> None: ...

    def page_name_exists(self, load_case_id: str, name: str, exclude_id: str | None = None) -> bool: ...

    def next_custom_display_order(self, load_case_id: str) -> int: ...

    def insert_analysis_page(
        self,
        dashboard_id: str,
        project_id: str,
        request_id: str,
        load_case_id: str,
        definition: dict[str, Any],
        occurred_at: datetime,
    ) -> None: ...

    def get_analysis_page(self, dashboard_id: str) -> dict[str, Any] | None: ...

    def get_analysis_page_for_delete(self, dashboard_id: str) -> tuple[str | None, dict[str, Any]] | None: ...

    def write_dashboard_definition(
        self,
        dashboard_id: str,
        definition: dict[str, Any],
        created_by: str,
    ) -> tuple[int, datetime]: ...

    def reorderable_analysis_pages(self, load_case_id: str) -> list[dict[str, Any]]: ...

    def begin_transaction(self) -> None: ...
    def commit_transaction(self) -> None: ...
    def rollback_transaction(self) -> None: ...
    def delete_analysis_page_records(self, dashboard_id: str) -> None: ...


class AnalysisPageRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[AnalysisPageRepository]: ...


class AnalysisPageCommandRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[AnalysisPageCommandRepository]: ...
