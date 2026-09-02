from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .models import (
    DirectoryEmployee,
    InvitationStatus,
    PersistedProjectInvitation,
    ProjectInvitationAuditRecord,
    ProjectRole,
)


class ProjectInvitationPreflight(Protocol):
    """Read-only project then authorization preflight, in that exact order."""

    def project_exists(self, project_id: str) -> bool: ...

    def authorize_create(self, project_id: str) -> None: ...


class ProjectInvitationReader(ProjectInvitationPreflight, Protocol):
    def list_invitations(self, project_id: str) -> list[PersistedProjectInvitation]: ...


class InvitationAuditWriter(Protocol):
    def add_audit(self, audit: ProjectInvitationAuditRecord) -> None: ...


class DirectoryGateway(Protocol):
    def search(self, query: str, limit: int) -> list[DirectoryEmployee]: ...

    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None: ...


class InvitationCreateUnitOfWork(Protocol):
    """Begins/locks before yielding and reauthorizes after the lock."""

    def authorize_create(self, project_id: str) -> None: ...

    def user_for_employee(self, employee_id: str) -> tuple[str, str] | None: ...

    def membership_exists(self, project_id: str, user_id: str) -> bool: ...

    def open_invitation_exists(self, project_id: str, employee_id: str) -> bool: ...

    def add_invitation(
        self,
        invitation_id: str,
        project_id: str,
        employee: DirectoryEmployee,
        desired_role: ProjectRole,
        status: InvitationStatus,
        resolved_user_id: str | None,
        actor_id: str,
        occurred_at: datetime,
    ) -> None: ...

    def find_invitation(self, invitation_id: str) -> PersistedProjectInvitation | None: ...

    def add_audit(self, audit: ProjectInvitationAuditRecord) -> None: ...


class InvitationCompleteUnitOfWork(Protocol):
    """Performs project preflight before BEGIN, then locks all mutation tables."""

    def authorize_manage(self, project_id: str) -> None: ...

    def find_invitation(
        self, invitation_id: str, project_id: str
    ) -> PersistedProjectInvitation | None: ...

    def user_for_employee(self, employee_id: str) -> tuple[str, str] | None: ...

    def membership_exists(self, project_id: str, user_id: str) -> bool: ...

    def add_membership(
        self,
        membership_id: str,
        project_id: str,
        user_id: str,
        role: ProjectRole,
        actor_id: str,
        occurred_at: datetime,
    ) -> None: ...

    def complete_invitation(
        self,
        invitation_id: str,
        project_id: str,
        user_id: str,
        actor_id: str,
        occurred_at: datetime,
    ) -> None: ...

    def add_audit(self, audit: ProjectInvitationAuditRecord) -> None: ...


class InvitationCancelUnitOfWork(Protocol):
    """Performs project preflight before BEGIN and reauthorizes after its lock."""

    def authorize_create(self, project_id: str) -> None: ...

    def find_invitation(
        self, invitation_id: str, project_id: str
    ) -> PersistedProjectInvitation | None: ...

    def cancel_invitation(self, invitation_id: str, actor_id: str, occurred_at: datetime) -> None: ...

    def add_audit(self, audit: ProjectInvitationAuditRecord) -> None: ...


ProjectInvitationReaderProvider = Callable[[], AbstractContextManager[ProjectInvitationReader]]
InvitationAuditWriterProvider = Callable[[], AbstractContextManager[InvitationAuditWriter]]
InvitationCreateUnitOfWorkProvider = Callable[[str], AbstractContextManager[InvitationCreateUnitOfWork]]
InvitationCompleteUnitOfWorkProvider = Callable[[str], AbstractContextManager[InvitationCompleteUnitOfWork]]
InvitationCancelUnitOfWorkProvider = Callable[[str], AbstractContextManager[InvitationCancelUnitOfWork]]
