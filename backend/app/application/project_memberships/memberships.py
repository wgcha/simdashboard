from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from ...domains.project_memberships.models import (
    AlreadyProjectMemberError,
    InactiveProjectMemberError,
    LastProjectAdminProtectedError,
    ProjectMemberNotFoundError,
    PersistedProjectMembership,
    ProjectMemberListItem,
    ProjectMembershipAuditContext,
    ProjectMembershipAuditRecord,
    ProjectMembershipRole,
    ProjectNotFoundError,
    StaleUserVersionError,
    UserHasOpenWorkItemsError,
    UserNotFoundError,
)
from ...domains.project_memberships.ports import (
    ProjectMembershipReaderProvider,
    ProjectMembershipUnitOfWorkProvider,
)

IdFactory = Callable[[str, int], str]
Clock = Callable[[], datetime]


def list_members(
    project_id: str,
    reader_provider: ProjectMembershipReaderProvider,
) -> list[ProjectMemberListItem]:
    with reader_provider() as reader:
        if not reader.project_exists(project_id):
            raise ProjectNotFoundError(project_id)
        reader.authorize_manage(project_id)
        return reader.list_members(project_id)


def create_member(
    project_id: str,
    user_id: str,
    role: ProjectMembershipRole,
    actor_id: str,
    audit: ProjectMembershipAuditContext,
    unit_of_work_provider: ProjectMembershipUnitOfWorkProvider,
    id_factory: IdFactory | None = None,
    clock: Clock | None = None,
) -> PersistedProjectMembership:
    make_id = id_factory or (lambda prefix, length: f"{prefix}-{uuid4().hex[:length]}")
    now = (clock or utc_now)()
    with unit_of_work_provider(project_id) as unit_of_work:
        unit_of_work.authorize_manage(project_id)
        active = unit_of_work.find_active_user(user_id)
        if active is None:
            raise UserNotFoundError(user_id)
        if active is False:
            raise InactiveProjectMemberError()
        if unit_of_work.membership_exists(project_id, user_id):
            raise AlreadyProjectMemberError()
        unit_of_work.add_membership(
            make_id("membership", 16), project_id, user_id, role, actor_id, now
        )
        audit_record: ProjectMembershipAuditRecord = {
            **audit,
            "action": "PROJECT_MEMBERSHIP_CREATED",
            "status_code": 201,
            "detail": {"target_user_id": user_id, "project_id": project_id, "new_role": role},
        }
        unit_of_work.add_audit(audit_record)
        result = unit_of_work.find_membership(project_id, user_id)
        if result is None:
            raise RuntimeError("project membership disappeared after creation")
        return result


def change_member_role(
    project_id: str,
    user_id: str,
    role: ProjectMembershipRole,
    actor_id: str,
    audit: ProjectMembershipAuditContext,
    unit_of_work_provider: ProjectMembershipUnitOfWorkProvider,
    expected_updated_at: datetime | None = None,
    clock: Clock | None = None,
) -> PersistedProjectMembership:
    now = (clock or utc_now)()
    with unit_of_work_provider(project_id) as unit_of_work:
        is_global_admin = bool(unit_of_work.authorize_manage(project_id))
        current = unit_of_work.find_membership(project_id, user_id)
        if current is None:
            raise ProjectMemberNotFoundError(project_id, user_id)
        if (
            expected_updated_at is not None
            and normalize_timestamp(current["updated_at"])
            != normalize_timestamp(expected_updated_at)
        ):
            raise StaleUserVersionError()
        if (
            current["role"] == "admin"
            and role != "admin"
            and not is_global_admin
            and unit_of_work.count_admins(project_id) <= 1
        ):
            raise LastProjectAdminProtectedError()
        unit_of_work.update_role(project_id, user_id, role, actor_id, now)
        audit_record: ProjectMembershipAuditRecord = {
            **audit,
            "action": "PROJECT_MEMBERSHIP_ROLE_CHANGED",
            "status_code": 200,
            "detail": {"target_user_id": user_id, "project_id": project_id, "old_role": current["role"], "new_role": role},
        }
        unit_of_work.add_audit(audit_record)
        result = unit_of_work.find_membership(project_id, user_id)
        if result is None:
            raise RuntimeError("project membership disappeared after role change")
        return result


def remove_member(
    project_id: str,
    user_id: str,
    actor_id: str,
    audit: ProjectMembershipAuditContext,
    unit_of_work_provider: ProjectMembershipUnitOfWorkProvider,
) -> dict[str, str]:
    with unit_of_work_provider(project_id) as unit_of_work:
        is_global_admin = bool(unit_of_work.authorize_manage(project_id))
        current = unit_of_work.find_membership(project_id, user_id)
        if current is None:
            raise ProjectMemberNotFoundError(project_id, user_id)
        if (
            current["role"] == "admin"
            and not is_global_admin
            and unit_of_work.count_admins(project_id) <= 1
        ):
            raise LastProjectAdminProtectedError()
        open_items = unit_of_work.count_open_work_items(project_id, user_id)
        if open_items:
            raise UserHasOpenWorkItemsError(open_items)
        unit_of_work.delete(project_id, user_id)
        audit_record: ProjectMembershipAuditRecord = {
            **audit,
            "action": "PROJECT_MEMBERSHIP_REMOVED",
            "status_code": 200,
            "detail": {"target_user_id": user_id, "project_id": project_id, "old_role": current["role"]},
        }
        unit_of_work.add_audit(audit_record)
        return {"status": "removed", "project_id": project_id, "user_id": user_id}


def normalize_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
