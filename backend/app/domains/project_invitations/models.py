from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict


ProjectRole = Literal["general", "power", "admin"]
EmploymentStatus = Literal["ACTIVE", "INACTIVE"]
InvitationStatus = Literal[
    "PENDING_ACCOUNT", "PENDING_APPROVAL", "READY", "COMPLETED", "CANCELLED"
]


class DirectoryEmployee(TypedDict):
    employee_id: str
    display_name: str
    department: str | None
    job_title: str | None
    email: str | None
    employment_status: EmploymentStatus


class PersistedProjectInvitation(TypedDict):
    """The raw invitation row returned by the existing API."""

    id: str
    project_id: str
    employee_id: str
    display_name_snapshot: str
    department_snapshot: str | None
    desired_role: ProjectRole
    status: InvitationStatus
    resolved_user_id: str | None
    invited_by: str
    invited_at: datetime
    resolved_by: str | None
    resolved_at: datetime | None
    cancelled_by: str | None
    cancelled_at: datetime | None


class ProjectInvitationAuditContext(TypedDict):
    user_id: str
    username: str
    role: str
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class ProjectInvitationAuditRecord(ProjectInvitationAuditContext):
    action: Literal[
        "DIRECTORY_SEARCHED", "PROJECT_INVITATION_CREATED", "PROJECT_INVITATION_STATUS_CHANGED"
    ]
    status_code: int
    detail: dict[str, Any]


class ProjectInvitationError(Exception):
    """Base error used by the invitation application boundary."""


class ProjectNotFoundError(ProjectInvitationError):
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        super().__init__(project_id)


class DirectoryQueryTooShortError(ProjectInvitationError):
    code = "DIRECTORY_QUERY_TOO_SHORT"


class DirectoryUnavailableError(ProjectInvitationError):
    code = "DIRECTORY_UNAVAILABLE"


class DirectoryEmployeeNotFoundError(ProjectInvitationError):
    code = "DIRECTORY_EMPLOYEE_NOT_FOUND"


class InactiveEmployeeError(ProjectInvitationError):
    code = "INACTIVE_EMPLOYEE"


class InvitationAlreadyExistsError(ProjectInvitationError):
    code = "INVITATION_ALREADY_EXISTS"


class AlreadyProjectMemberError(ProjectInvitationError):
    code = "ALREADY_PROJECT_MEMBER"


class InvitationNotFoundError(ProjectInvitationError):
    pass


class InvitationAlreadyFinalError(ProjectInvitationError):
    code = "INVITATION_ALREADY_FINAL"

    def __init__(self, status: InvitationStatus | None = None) -> None:
        self.status = status
        super().__init__(status)


class InvitationAccountNotReadyError(ProjectInvitationError):
    code = "INVITATION_ACCOUNT_NOT_READY"

    def __init__(self, status: InvitationStatus | None = None) -> None:
        self.status = status
        super().__init__(status)
