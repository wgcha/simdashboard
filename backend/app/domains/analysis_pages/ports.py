from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Callable, Protocol


class AnalysisPageRepository(Protocol):
    def load_case_context(self, load_case_id: str) -> tuple[Any, ...] | None: ...

    def authorize(self, callback: Callable[[object], None]) -> None: ...

    def list_analysis_pages(
        self,
        load_case_id: str,
        *,
        include_private: bool,
        include_archived: bool,
    ) -> list[dict[str, Any]]: ...


class AnalysisPageRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[AnalysisPageRepository]: ...
