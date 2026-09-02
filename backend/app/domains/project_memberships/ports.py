from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .models import (
    PersistedProjectMembership,
    ProjectMemberListItem,
    ProjectMembershipAuditRecord,
    ProjectMembershipRole,
)


class ProjectMembershipReader(Protocol):
    """Read-side contract for the project membership listing."""

    def project_exists(self, project_id: str) -> bool: ...

    def authorize_manage(self, project_id: str) -> None: ...

    def list_members(self, project_id: str) -> list[ProjectMemberListItem]: ...


class ProjectMembershipUnitOfWork(Protocol):
    """Atomic persistence and authorization contract for membership mutations."""

    def authorize_manage(self, project_id: str) -> bool: ...

    def find_active_user(self, user_id: str) -> bool | None: ...

    def membership_exists(self, project_id: str, user_id: str) -> bool: ...

    def add_membership(
        self,
        membership_id: str,
        project_id: str,
        user_id: str,
        role: ProjectMembershipRole,
        actor_id: str,
        occurred_at: datetime,
    ) -> None: ...

    def find_membership(self, project_id: str, user_id: str) -> PersistedProjectMembership | None: ...

    def count_admins(self, project_id: str) -> int: ...

    def update_role(
        self,
        project_id: str,
        user_id: str,
        role: ProjectMembershipRole,
        actor_id: str,
        occurred_at: datetime,
    ) -> None: ...

    def count_open_work_items(self, project_id: str, user_id: str) -> int: ...

    def delete(self, project_id: str, user_id: str) -> None: ...

    def add_audit(self, audit: ProjectMembershipAuditRecord) -> None: ...


ProjectMembershipReaderProvider = Callable[
    [], AbstractContextManager[ProjectMembershipReader]
]
ProjectMembershipUnitOfWorkProvider = Callable[
    [str], AbstractContextManager[ProjectMembershipUnitOfWork]
]
