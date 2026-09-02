from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.project_memberships import (
    SQLProjectMembershipReaderProvider,
    SQLProjectMembershipUnitOfWorkProvider,
)
from ....application.project_memberships.memberships import (
    change_member_role as change_member_role_command,
    create_member as create_member_command,
    list_members as list_members_query,
    remove_member as remove_member_command,
)
from ....database_connection import ConnectionLike
from ....domains.project_memberships.models import (
    AlreadyProjectMemberError,
    InactiveProjectMemberError,
    LastProjectAdminProtectedError,
    ProjectMemberNotFoundError,
    ProjectMembershipAuditContext,
    ProjectMembershipError,
    ProjectNotFoundError,
    StaleUserVersionError,
    UserHasOpenWorkItemsError,
    UserNotFoundError,
)
from ....modules.access_control import PROJECT_MEMBER_MANAGE, require_permission
from ....schemas.access_control import ProjectMemberCreate, ProjectMemberUpdate


router = APIRouter()


def _authorize(request: Request):
    def authorize(project_id: str, connection: ConnectionLike) -> bool:
        context = require_permission(request, PROJECT_MEMBER_MANAGE, project_id, conn=connection)
        return bool(context.principal.is_global_admin)

    return authorize


def _audit(request: Request) -> ProjectMembershipAuditContext:
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


def _raise_mapped(error: ProjectMembershipError) -> NoReturn:
    if isinstance(error, ProjectNotFoundError):
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.") from error
    if isinstance(error, ProjectMemberNotFoundError):
        raise HTTPException(404, "프로젝트 멤버를 찾을 수 없습니다.") from error
    if isinstance(error, UserNotFoundError):
        raise HTTPException(404, "사용자를 찾을 수 없습니다.") from error
    if isinstance(error, InactiveProjectMemberError):
        raise HTTPException(409, {"code": "PROJECT_MEMBER_MUST_BE_ACTIVE"}) from error
    if isinstance(error, AlreadyProjectMemberError):
        raise HTTPException(409, {"code": "ALREADY_PROJECT_MEMBER"}) from error
    if isinstance(error, StaleUserVersionError):
        raise HTTPException(
            409,
            {"code": "STALE_USER_VERSION", "message": "사용자 정보가 다른 관리자에 의해 변경되었습니다."},
        ) from error
    if isinstance(error, LastProjectAdminProtectedError):
        raise HTTPException(409, {"code": "LAST_PROJECT_ADMIN_PROTECTED"}) from error
    if isinstance(error, UserHasOpenWorkItemsError):
        raise HTTPException(409, {"code": "USER_HAS_OPEN_WORK_ITEMS", "count": error.count}) from error
    raise error


@router.get("/api/projects/{project_id}/members")
def list_project_members(project_id: str, request: Request) -> list[dict[str, Any]]:
    try:
        return list_members_query(project_id, SQLProjectMembershipReaderProvider(_authorize(request)))
    except ProjectMembershipError as error:
        _raise_mapped(error)


@router.post("/api/projects/{project_id}/members", status_code=201)
def create_project_member(
    project_id: str, payload: ProjectMemberCreate, request: Request
) -> dict[str, Any]:
    try:
        return create_member_command(
            project_id,
            payload.user_id,
            payload.role,
            request.state.principal.user_id,
            _audit(request),
            SQLProjectMembershipUnitOfWorkProvider(
                _authorize(request), ("users", "project_memberships")
            ),
        )
    except ProjectMembershipError as error:
        _raise_mapped(error)


@router.patch("/api/projects/{project_id}/members/{user_id}")
def update_project_member(
    project_id: str, user_id: str, payload: ProjectMemberUpdate, request: Request
) -> dict[str, Any]:
    try:
        return change_member_role_command(
            project_id,
            user_id,
            payload.role,
            request.state.principal.user_id,
            _audit(request),
            SQLProjectMembershipUnitOfWorkProvider(_authorize(request), ("project_memberships",)),
            expected_updated_at=payload.expected_updated_at,
        )
    except ProjectMembershipError as error:
        _raise_mapped(error)


@router.delete("/api/projects/{project_id}/members/{user_id}")
def delete_project_member(project_id: str, user_id: str, request: Request) -> dict[str, Any]:
    try:
        return remove_member_command(
            project_id,
            user_id,
            request.state.principal.user_id,
            _audit(request),
            SQLProjectMembershipUnitOfWorkProvider(_authorize(request), ("project_memberships",)),
        )
    except ProjectMembershipError as error:
        _raise_mapped(error)
