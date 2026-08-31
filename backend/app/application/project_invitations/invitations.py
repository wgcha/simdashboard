from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from ...domains.project_invitations.models import (
    AlreadyProjectMemberError,
    DirectoryEmployee,
    DirectoryEmployeeNotFoundError,
    DirectoryQueryTooShortError,
    InactiveEmployeeError,
    InvitationAccountNotReadyError,
    InvitationAlreadyExistsError,
    InvitationAlreadyFinalError,
    InvitationNotFoundError,
    InvitationStatus,
    PersistedProjectInvitation,
    ProjectInvitationAuditContext,
    ProjectInvitationAuditRecord,
    ProjectNotFoundError,
    ProjectRole,
)
from ...domains.project_invitations.ports import (
    DirectoryGateway,
    InvitationAuditWriterProvider,
    InvitationCancelUnitOfWorkProvider,
    InvitationCompleteUnitOfWorkProvider,
    InvitationCreateUnitOfWorkProvider,
    ProjectInvitationPreflight,
    ProjectInvitationReaderProvider,
)


Clock = Callable[[], datetime]
IdFactory = Callable[[str, int], str]


def list_invitations(project_id: str, reader_provider: ProjectInvitationReaderProvider) -> list[PersistedProjectInvitation]:
    with reader_provider() as reader:
        _preflight_create(reader, project_id)
        return reader.list_invitations(project_id)


def search_directory(
    project_id: str, query: str, limit: int, directory: DirectoryGateway,
    audit: ProjectInvitationAuditContext, reader_provider: ProjectInvitationReaderProvider,
    audit_writer_provider: InvitationAuditWriterProvider,
) -> list[DirectoryEmployee]:
    normalized = query.strip()
    if len(normalized) < 2:
        raise DirectoryQueryTooShortError()
    with reader_provider() as reader:
        _preflight_create(reader, project_id)
    items = directory.search(normalized, limit)
    record: ProjectInvitationAuditRecord = {
        **audit, "action": "DIRECTORY_SEARCHED", "status_code": 200,
        "detail": {"project_id": project_id, "result_count": len(items)},
    }
    with audit_writer_provider() as writer:
        writer.add_audit(record)
    return items


def create_invitation(
    project_id: str, employee_id: str, desired_role: ProjectRole, actor_id: str,
    audit: ProjectInvitationAuditContext, directory: DirectoryGateway,
    reader_provider: ProjectInvitationReaderProvider, unit_of_work_provider: InvitationCreateUnitOfWorkProvider,
    id_factory: IdFactory | None = None, clock: Clock | None = None,
) -> PersistedProjectInvitation:
    with reader_provider() as reader:
        _preflight_create(reader, project_id)
    employee = directory.get_by_employee_id(employee_id.strip())
    if employee is None:
        raise DirectoryEmployeeNotFoundError()
    if employee["employment_status"] != "ACTIVE":
        raise InactiveEmployeeError()
    make_id = id_factory or _new_identifier
    now = (clock or utc_now)()
    with unit_of_work_provider(project_id) as unit_of_work:
        unit_of_work.authorize_create(project_id)
        user = unit_of_work.user_for_employee(employee["employee_id"])
        if user and unit_of_work.membership_exists(project_id, user[0]):
            raise AlreadyProjectMemberError()
        if unit_of_work.open_invitation_exists(project_id, employee["employee_id"]):
            raise InvitationAlreadyExistsError()
        status = cast(
            InvitationStatus,
            "READY"
            if user and user[1] == "ACTIVE"
            else "PENDING_APPROVAL"
            if user
            else "PENDING_ACCOUNT",
        )
        invitation_id = make_id("invitation", 16)
        unit_of_work.add_invitation(
            invitation_id,
            project_id,
            employee,
            desired_role,
            status,
            user[0] if user else None,
            actor_id,
            now,
        )
        unit_of_work.add_audit({
            **audit, "action": "PROJECT_INVITATION_CREATED", "status_code": 201,
            "detail": {"project_id": project_id, "target_employee_id": masked_employee_id(employee["employee_id"])},
        })
        result = unit_of_work.find_invitation(invitation_id)
        if result is None:
            raise RuntimeError("project invitation disappeared after creation")
        return result


def complete_invitation(
    project_id: str, invitation_id: str, actor_id: str, audit: ProjectInvitationAuditContext,
    unit_of_work_provider: InvitationCompleteUnitOfWorkProvider,
    id_factory: IdFactory | None = None, clock: Clock | None = None,
) -> PersistedProjectInvitation:
    make_id = id_factory or _new_identifier
    now = (clock or utc_now)()
    with unit_of_work_provider(project_id) as unit_of_work:
        unit_of_work.authorize_manage(project_id)
        invitation = unit_of_work.find_invitation(invitation_id, project_id)
        if invitation is None:
            raise InvitationNotFoundError()
        status = invitation["status"]
        if status != "READY":
            if status in {"COMPLETED", "CANCELLED"}:
                raise InvitationAlreadyFinalError(status)
            raise InvitationAccountNotReadyError(status)
        user = unit_of_work.user_for_employee(invitation["employee_id"])
        if user is None or user[1] != "ACTIVE":
            raise InvitationAccountNotReadyError()
        if unit_of_work.membership_exists(project_id, user[0]):
            raise AlreadyProjectMemberError()
        unit_of_work.add_membership(make_id("membership", 16), project_id, user[0], invitation["desired_role"], actor_id, now)
        unit_of_work.complete_invitation(invitation_id, project_id, user[0], actor_id, now)
        unit_of_work.add_audit({
            **audit, "action": "PROJECT_INVITATION_STATUS_CHANGED", "status_code": 200,
            "detail": {"project_id": project_id, "target_user_id": user[0], "old_status": status, "new_status": "COMPLETED"},
        })
        result = unit_of_work.find_invitation(invitation_id, project_id)
        if result is None:
            raise RuntimeError("project invitation disappeared after completion")
        return result


def cancel_invitation(
    project_id: str, invitation_id: str, actor_id: str, audit: ProjectInvitationAuditContext,
    unit_of_work_provider: InvitationCancelUnitOfWorkProvider, clock: Clock | None = None,
) -> dict[str, str]:
    now = (clock or utc_now)()
    with unit_of_work_provider(project_id) as unit_of_work:
        unit_of_work.authorize_create(project_id)
        current = unit_of_work.find_invitation(invitation_id, project_id)
        if current is None:
            raise InvitationNotFoundError()
        if current["status"] in {"COMPLETED", "CANCELLED"}:
            raise InvitationAlreadyFinalError()
        unit_of_work.cancel_invitation(invitation_id, actor_id, now)
        unit_of_work.add_audit({
            **audit, "action": "PROJECT_INVITATION_STATUS_CHANGED", "status_code": 200,
            "detail": {"project_id": project_id, "old_status": current["status"], "new_status": "CANCELLED"},
        })
        return {"status": "CANCELLED", "id": invitation_id}


def _preflight_create(reader: ProjectInvitationPreflight, project_id: str) -> None:
    if not reader.project_exists(project_id):
        raise ProjectNotFoundError(project_id)
    reader.authorize_create(project_id)


def masked_employee_id(employee_id: str) -> str:
    return "***" if len(employee_id) <= 3 else employee_id[:2] + "*" * (len(employee_id) - 3) + employee_id[-1]


def _new_identifier(prefix: str, length: int) -> str:
    return f"{prefix}-{uuid4().hex[:length]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
