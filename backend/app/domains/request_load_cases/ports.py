from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class RequestLoadCasesRepository(Protocol):
    def list_load_cases(self, request_id: str) -> list[dict[str, Any]]: ...


class RequestLoadCasesRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[RequestLoadCasesRepository]: ...
