from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict


AccountStatus = Literal["PENDING", "ACTIVE", "SUSPENDED"]


class UserAdministrationAuditContext(TypedDict):
    user_id: str
    username: str
    role: str
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class UserAdministrationAuditRecord(UserAdministrationAuditContext):
    action: Literal["ACCOUNT_STATUS_CHANGED", "GLOBAL_ADMIN_CHANGED"]
    status_code: int
    detail: dict[str, Any]


class UserAdministrationError(Exception):
    """Base error raised by user-administration use cases."""


class UserNotFoundError(UserAdministrationError):
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        super().__init__(user_id)


class StaleUserVersionError(UserAdministrationError):
    code = "STALE_USER_VERSION"


class LastGlobalAdminProtectedError(UserAdministrationError):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class GlobalAdminMustBeActiveError(UserAdministrationError):
    code = "GLOBAL_ADMIN_MUST_BE_ACTIVE"


class UserAccount(TypedDict):
    id: str
    username: str
    display_name: str
    employee_id: str | None
    email: str | None
    department: str | None
    job_title: str | None
    account_status: AccountStatus
    is_global_admin: bool
    approved_by: str | None
    approved_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
