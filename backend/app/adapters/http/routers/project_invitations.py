from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from ....adapters.directory.project_invitations import EmployeeDirectoryGateway
from ....adapters.persistence.project_invitations import (
    SQLInvitationAuditWriterProvider,
    SQLInvitationCancelUnitOfWorkProvider,
    SQLInvitationCompleteUnitOfWorkProvider,
    SQLInvitationCreateUnitOfWorkProvider,
    SQLProjectInvitationReaderProvider,
)
from ....application.project_invitations.invitations import (
    cancel_invitation as cancel_invitation_command,
    complete_invitation as complete_invitation_command,
    create_invitation as create_invitation_command,
    list_invitations as list_invitations_query,
    search_directory as search_directory_query,
)
from ....database_connection import ConnectionLike
from ....domains.project_invitations.models import (
    AlreadyProjectMemberError,
    DirectoryEmployeeNotFoundError,
    DirectoryQueryTooShortError,
    DirectoryUnavailableError,
    InactiveEmployeeError,
    InvitationAccountNotReadyError,
    InvitationAlreadyExistsError,
    InvitationAlreadyFinalError,
    InvitationNotFoundError,
    ProjectInvitationAuditContext,
    ProjectInvitationError,
    ProjectNotFoundError,
)
from ....modules.access_control import PROJECT_INVITATION_CREATE, PROJECT_MEMBER_MANAGE, require_permission
from ....schemas.access_control import InvitationCreate


router = APIRouter()


def _authorize(
    request: Request, permission: str
) -> Callable[[str, ConnectionLike], None]:
    def authorize(project_id: str, connection: ConnectionLike) -> None:
        require_permission(request, permission, project_id, conn=connection)

    return authorize


def _audit(request: Request) -> ProjectInvitationAuditContext:
    principal = request.state.principal
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "method": request.method,
        "path": request.url.path,
        "request_id": getattr(request.state, "request_id", str(uuid4())),
        "client_ip": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent", "")[:500],
    }


def _raise_mapped(error: ProjectInvitationError) -> NoReturn:
    if isinstance(error, ProjectNotFoundError):
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.") from error
    if isinstance(error, DirectoryQueryTooShortError):
        raise HTTPException(422, {"code": error.code}) from error
    if isinstance(error, DirectoryUnavailableError):
        raise HTTPException(503, {"code": error.code, "message": str(error)}) from error
    if isinstance(error, DirectoryEmployeeNotFoundError):
        raise HTTPException(404, {"code": error.code}) from error
    if isinstance(error, InactiveEmployeeError):
        raise HTTPException(422, {"code": error.code}) from error
    if isinstance(error, AlreadyProjectMemberError):
        raise HTTPException(409, {"code": error.code}) from error
    if isinstance(error, InvitationAlreadyExistsError):
        raise HTTPException(409, {"code": error.code}) from error
    if isinstance(error, InvitationNotFoundError):
        raise HTTPException(404, "초대를 찾을 수 없습니다.") from error
    if isinstance(error, InvitationAlreadyFinalError):
        detail: dict[str, Any] = {"code": error.code}
        if error.status is not None:
            detail["status"] = error.status
        raise HTTPException(409, detail) from error
    if isinstance(error, InvitationAccountNotReadyError):
        detail = {"code": error.code}
        if error.status is not None:
            detail["status"] = error.status
        raise HTTPException(409, detail) from error
    raise error


@router.get("/api/projects/{project_id}/directory/employees")
def search_directory_employees(
    project_id: str,
    request: Request,
    q: str = Query(min_length=2, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict[str, Any]]:
    try:
        return search_directory_query(
            project_id, q, limit, EmployeeDirectoryGateway(), _audit(request),
            SQLProjectInvitationReaderProvider(_authorize(request, PROJECT_INVITATION_CREATE)),
            SQLInvitationAuditWriterProvider(),
        )
    except ProjectInvitationError as error:
        _raise_mapped(error)


@router.get("/api/projects/{project_id}/invitations")
def list_project_invitations(project_id: str, request: Request) -> list[dict[str, Any]]:
    try:
        return list_invitations_query(
            project_id, SQLProjectInvitationReaderProvider(_authorize(request, PROJECT_INVITATION_CREATE))
        )
    except ProjectInvitationError as error:
        _raise_mapped(error)


@router.post("/api/projects/{project_id}/invitations", status_code=201)
def create_project_invitation(project_id: str, payload: InvitationCreate, request: Request) -> dict[str, Any]:
    try:
        return create_invitation_command(
            project_id, payload.employee_id, payload.desired_role, request.state.principal.user_id,
            _audit(request), EmployeeDirectoryGateway(),
            SQLProjectInvitationReaderProvider(_authorize(request, PROJECT_INVITATION_CREATE)),
            SQLInvitationCreateUnitOfWorkProvider(_authorize(request, PROJECT_INVITATION_CREATE)),
        )
    except ProjectInvitationError as error:
        _raise_mapped(error)


@router.post("/api/projects/{project_id}/invitations/{invitation_id}/complete")
def complete_project_invitation(project_id: str, invitation_id: str, request: Request) -> dict[str, Any]:
    try:
        return complete_invitation_command(
            project_id, invitation_id, request.state.principal.user_id, _audit(request),
            SQLInvitationCompleteUnitOfWorkProvider(_authorize(request, PROJECT_MEMBER_MANAGE)),
        )
    except ProjectInvitationError as error:
        _raise_mapped(error)


@router.delete("/api/projects/{project_id}/invitations/{invitation_id}")
def cancel_project_invitation(project_id: str, invitation_id: str, request: Request) -> dict[str, Any]:
    try:
        return cancel_invitation_command(
            project_id, invitation_id, request.state.principal.user_id, _audit(request),
            SQLInvitationCancelUnitOfWorkProvider(_authorize(request, PROJECT_INVITATION_CREATE)),
        )
    except ProjectInvitationError as error:
        _raise_mapped(error)
