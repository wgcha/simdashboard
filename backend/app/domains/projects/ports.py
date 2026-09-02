from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import CreateProjectCommand, PersistedProjectAuditRecord, Project


class ProjectRepository(Protocol):
    """Read-side project persistence contract."""

    def list_projects(self) -> list[Project]: ...


class ProjectUnitOfWork(Protocol):
    """Atomic write contract for project creation."""

    def add_project(self, project_id: str, command: CreateProjectCommand, created_at: datetime) -> None: ...
    def add_product_information(self, project_id: str, command: CreateProjectCommand) -> None: ...
    def add_quality_thresholds(self, project_id: str, created_at: datetime) -> None: ...
    def add_workspace_layouts(self, project_id: str) -> None: ...
    def add_admin_membership(
        self,
        membership_id: str,
        project_id: str,
        command: CreateProjectCommand,
        created_at: datetime,
    ) -> None: ...
    def add_audit_event(self, record: PersistedProjectAuditRecord) -> None: ...
