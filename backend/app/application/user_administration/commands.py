from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from ...domains.user_administration.models import (
    AccountStatus,
    GlobalAdminMustBeActiveError,
    LastGlobalAdminProtectedError,
    StaleUserVersionError,
    UserAccount,
    UserAdministrationAuditContext,
    UserNotFoundError,
)
from ...domains.user_administration.ports import UserAdministrationUnitOfWorkProvider


Clock = Callable[[], datetime]


def update_account_status(
    user_id: str,
    account_status: AccountStatus,
    expected_updated_at: datetime,
    reason: str,
    actor_id: str,
    audit: UserAdministrationAuditContext,
    unit_of_work_provider: UserAdministrationUnitOfWorkProvider,
    clock: Clock | None = None,
) -> UserAccount:
    now = (clock or utc_now)()
    with unit_of_work_provider() as unit_of_work:
        unit_of_work.authorize_user_approval()
        current = unit_of_work.find_user_for_status(user_id)
        if current is None:
            raise UserNotFoundError(user_id)
        old_status, is_global_admin, employee_id, updated_at = current
        _assert_expected_timestamp(updated_at, expected_updated_at)
        if is_global_admin and old_status == "ACTIVE" and account_status != "ACTIVE":
            if unit_of_work.count_active_global_admins() <= 1:
                raise LastGlobalAdminProtectedError("마지막 전역 관리자는 중지할 수 없습니다.")
        unit_of_work.update_account_status(user_id, account_status, actor_id, now)
        if account_status == "ACTIVE" and employee_id:
            unit_of_work.ready_pending_invitations(employee_id, user_id, actor_id, now)
        unit_of_work.add_audit(
            {
                **audit,
                "action": "ACCOUNT_STATUS_CHANGED",
                "status_code": 200,
                "detail": {
                    "target_user_id": user_id,
                    "old_status": old_status,
                    "new_status": account_status,
                    "reason": reason,
                },
            }
        )
        result = unit_of_work.find_user(user_id)
        if result is None:
            raise UserNotFoundError(user_id)
        return result


def update_global_admin(
    user_id: str,
    is_global_admin: bool,
    expected_updated_at: datetime,
    reason: str,
    audit: UserAdministrationAuditContext,
    unit_of_work_provider: UserAdministrationUnitOfWorkProvider,
    clock: Clock | None = None,
) -> UserAccount:
    now = (clock or utc_now)()
    with unit_of_work_provider() as unit_of_work:
        unit_of_work.authorize_user_approval()
        current = unit_of_work.find_user_for_global_admin(user_id)
        if current is None:
            raise UserNotFoundError(user_id)
        old_value, account_status, updated_at = current
        _assert_expected_timestamp(updated_at, expected_updated_at)
        if is_global_admin and account_status != "ACTIVE":
            raise GlobalAdminMustBeActiveError()
        if old_value and not is_global_admin and account_status == "ACTIVE":
            if unit_of_work.count_active_global_admins() <= 1:
                raise LastGlobalAdminProtectedError("마지막 전역 관리자 권한은 해제할 수 없습니다.")
        unit_of_work.update_global_admin(user_id, is_global_admin, now)
        unit_of_work.add_audit(
            {
                **audit,
                "action": "GLOBAL_ADMIN_CHANGED",
                "status_code": 200,
                "detail": {
                    "target_user_id": user_id,
                    "old_value": bool(old_value),
                    "new_value": is_global_admin,
                    "reason": reason,
                },
            }
        )
        result = unit_of_work.find_user(user_id)
        if result is None:
            raise UserNotFoundError(user_id)
        return result


def _assert_expected_timestamp(actual: datetime, expected: datetime) -> None:
    if normalize_timestamp(actual) != normalize_timestamp(expected):
        raise StaleUserVersionError()


def normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
