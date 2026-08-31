from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict


ProjectMembershipRole = Literal["general", "power", "admin"]


AccountStatus = Literal["PENDING", "ACTIVE", "SUSPENDED"]


class PersistedProjectMembership(TypedDict):
    """The exact raw project_memberships row returned by mutation endpoints."""

    id: str
    project_id: str
    user_id: str
    role: ProjectMembershipRole
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class ProjectMemberListItem(TypedDict):
    """The enriched, ordered row returned by the membership listing endpoint."""

    project_id: str
    user_id: str
    role: ProjectMembershipRole
    created_at: datetime
    updated_at: datetime
    username: str
    display_name: str
    employee_id: str | None
    department: str | None
    job_title: str | None
    account_status: AccountStatus


class ProjectMembershipAuditContext(TypedDict):
    """Request and actor metadata supplied by the HTTP adapter."""

    user_id: str
    username: str
    role: str
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class ProjectMembershipAuditRecord(ProjectMembershipAuditContext):
    """The business audit event persisted inside the membership transaction."""

    action: Literal[
        "PROJECT_MEMBERSHIP_CREATED",
        "PROJECT_MEMBERSHIP_ROLE_CHANGED",
        "PROJECT_MEMBERSHIP_REMOVED",
    ]
    status_code: int
    detail: dict[str, Any]


class ProjectMembershipError(Exception):
    """Base class for errors raised by membership use cases."""


class ProjectNotFoundError(ProjectMembershipError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(project_id)


class ProjectMemberNotFoundError(ProjectMembershipError):
    def __init__(self, project_id: str, user_id: str) -> None:
        self.project_id = project_id
        self.user_id = user_id
        super().__init__(project_id, user_id)


class UserNotFoundError(ProjectMembershipError):
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        super().__init__(user_id)


class InactiveProjectMemberError(ProjectMembershipError):
    code = "PROJECT_MEMBER_MUST_BE_ACTIVE"


class AlreadyProjectMemberError(ProjectMembershipError):
    code = "ALREADY_PROJECT_MEMBER"


class StaleUserVersionError(ProjectMembershipError):
    code = "STALE_USER_VERSION"


class LastProjectAdminProtectedError(ProjectMembershipError):
    code = "LAST_PROJECT_ADMIN_PROTECTED"


class UserHasOpenWorkItemsError(ProjectMembershipError):
    code = "USER_HAS_OPEN_WORK_ITEMS"

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(count)
