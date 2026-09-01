from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol


class WorkflowQueriesRepository(Protocol):
    def analysis_request(self, request_id: str) -> dict[str, Any] | None: ...

    def workflow_requests(self) -> list[dict[str, Any]]: ...

    def monitoring_summary(self, request_id: str) -> dict[str, Any]: ...


class WorkflowQueriesRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[WorkflowQueriesRepository]: ...
