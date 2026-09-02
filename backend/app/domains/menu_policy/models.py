from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict


MenuPolicyRole = Literal["general", "power", "admin"]
MenuVisibility = dict[MenuPolicyRole, dict[str, bool]]


class MenuPolicy(TypedDict):
    version: int
    updated_by: str
    updated_at: datetime
    menus: list[dict[str, Any]]


class MenuPolicyVersionSummary(TypedDict):
    version: int
    created_by: str
    created_at: datetime
    source_version: int | None
    change_note: str | None


class MenuPolicyVersion(MenuPolicyVersionSummary):
    visibility: MenuVisibility


class MenuPolicyAuditContext(TypedDict):
    user_id: str
    username: str
    role: str
    method: str
    path: str
    request_id: str
    client_ip: str | None
    user_agent: str


class MenuPolicyAuditRecord(MenuPolicyAuditContext):
    action: Literal["MENU_POLICY_UPDATED", "MENU_POLICY_RESTORED"]
    status_code: int
    detail: dict[str, Any]


class MenuPolicyError(Exception):
    """Base error raised by menu-policy use cases."""


class MenuPolicyUnavailableError(MenuPolicyError):
    code = "MENU_POLICY_UNAVAILABLE"


class MenuPolicyIncompleteError(MenuPolicyError):
    code = "MENU_POLICY_INCOMPLETE"


class UnknownMenuPolicyRoleError(MenuPolicyError):
    code = "UNKNOWN_MENU_POLICY_ROLE"

    def __init__(self, roles: list[str]) -> None:
        self.roles = roles
        super().__init__(*roles)


class UnknownMenuIdError(MenuPolicyError):
    code = "UNKNOWN_MENU_ID"

    def __init__(self, menu_ids: list[str]) -> None:
        self.menu_ids = menu_ids
        super().__init__(*menu_ids)


class MenuPolicyLockedError(MenuPolicyError):
    code = "MENU_POLICY_LOCKED"

    def __init__(self, menu_id: str) -> None:
        self.menu_id = menu_id
        super().__init__(menu_id)


class MenuPermissionMismatchError(MenuPolicyError):
    code = "MENU_PERMISSION_MISMATCH"

    def __init__(self, role: str, menu_id: str, required_permission: str) -> None:
        self.role = role
        self.menu_id = menu_id
        self.required_permission = required_permission
        super().__init__(role, menu_id, required_permission)


class StaleMenuPolicyVersionError(MenuPolicyError):
    code = "STALE_POLICY_VERSION"

    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__(current_version)


class MenuPolicyVersionNotFoundError(MenuPolicyError):
    message = "메뉴 정책 버전을 찾을 수 없습니다."
