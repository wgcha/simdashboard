from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Callable, Protocol


class RequestLoadCasesRepository(Protocol):
    def list_load_cases(self, request_id: str) -> list[dict[str, Any]]: ...


class RequestLoadCasesRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[RequestLoadCasesRepository]: ...


class RequestLoadCaseWriteRepository(Protocol):
    def authorize_resource(self, callback: Callable[[object], None]) -> None: ...

    def request_exists(self, request_id: str) -> bool: ...

    def insert_load_case(
        self,
        load_case_id: str,
        request_id: str,
        name: str,
        analysis_type: str,
        parameters: dict[str, Any],
        created_at: datetime,
    ) -> None: ...


class RequestLoadCaseWriteRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[RequestLoadCaseWriteRepository]: ...
