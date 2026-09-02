from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class LoadCaseOverviewRepository(Protocol):
    def load_case(self, load_case_id: str) -> dict[str, Any] | None: ...

    def selected_run(self, load_case_id: str, run_id: str | None) -> str | None: ...

    def run_projection(self, load_case_id: str, run_id: str) -> dict[str, Any]: ...


class LoadCaseOverviewRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[LoadCaseOverviewRepository]: ...
