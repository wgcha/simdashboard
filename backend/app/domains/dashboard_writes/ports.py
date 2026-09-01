from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Callable, Protocol


class DashboardWriteRepository(Protocol):
    def authorize_resource(self, callback: Callable[[object], None]) -> None: ...

    def get_dashboard(self, dashboard_id: str) -> tuple[Any, Any] | None: ...

    def write_dashboard_definition(
        self, dashboard_id: str, definition: dict[str, Any], created_by: str
    ) -> tuple[int, datetime]: ...

    def get_dashboard_version_for_delete(self, dashboard_id: str, version: int) -> tuple[Any] | None: ...

    def count_valid_history(self, dashboard_id: str, current_version: int) -> int: ...

    def invalidate_dashboard_version(self, dashboard_id: str, version: int) -> None: ...

    def get_clone_source(self, dashboard_id: str) -> tuple[Any, ...] | None: ...

    def insert_cloned_dashboard(
        self,
        dashboard_id: str,
        project_id: str,
        request_id: str | None,
        load_case_id: str | None,
        definition: dict[str, Any],
        principal_user_id: str,
        occurred_at: datetime,
    ) -> None: ...

    def get_valid_dashboard_version(self, dashboard_id: str, version: int) -> tuple[Any] | None: ...


class DashboardWriteRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[DashboardWriteRepository]: ...
