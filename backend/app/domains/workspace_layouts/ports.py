from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Protocol

from .models import WorkspaceLayout, WorkspaceLayoutAuditRecord, WorkspaceLayoutVersion


class WorkspaceLayoutRepository(Protocol):
    def project_exists(self, project_id: str) -> bool: ...

    def authorize_read(self, project_id: str) -> None: ...

    def authorize_write(self, project_id: str) -> None: ...

    def get_layout(self, project_id: str, layout_kind: str) -> WorkspaceLayout | None: ...

    def get_layout_version(self, project_id: str, layout_kind: str) -> int | None: ...

    def list_versions(self, project_id: str, layout_kind: str) -> list[WorkspaceLayoutVersion]: ...

    def transaction(self) -> AbstractContextManager[None]: ...

    def update_layout(
        self,
        project_id: str,
        layout_kind: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def insert_version(
        self,
        project_id: str,
        layout_kind: str,
        version: int,
        definition: dict[str, Any],
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def add_audit(self, audit: WorkspaceLayoutAuditRecord) -> None: ...
