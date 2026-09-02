from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol

from .models import (
    ReportLayout,
    ReportLayoutAuditRecord,
    ReportLayoutState,
    ReportLayoutVersion,
    ReportLayoutVersionSummary,
)


class ReportLayoutRepository(Protocol):
    def list_active_layouts(self) -> list[ReportLayout]: ...

    def get_active_state(self, layout_id: str) -> ReportLayoutState | None: ...

    def list_versions(self, layout_id: str) -> list[ReportLayoutVersionSummary]: ...

    def get_version(self, layout_id: str, version: int) -> ReportLayoutVersion | None: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def insert_layout(
        self,
        *,
        layout_id: str,
        name: str,
        description: str,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def update_layout(
        self,
        *,
        layout_id: str,
        name: str,
        description: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def insert_version(
        self,
        *,
        layout_id: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def add_audit(self, audit: ReportLayoutAuditRecord) -> None: ...

    def deactivate(self, layout_id: str, occurred_at: datetime) -> None: ...
