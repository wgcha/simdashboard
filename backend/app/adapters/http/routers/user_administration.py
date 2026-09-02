from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from ....adapters.persistence.user_administration import (
    SQLAccountStatusUnitOfWorkProvider,
    SQLGlobalAdminUnitOfWorkProvider,
)
from ....application.user_administration.commands import (
    update_account_status as update_account_status_command,
    update_global_admin as update_global_admin_command,
)
from ....database_connection import ConnectionLike
from ....domains.user_administration.models import (
    GlobalAdminMustBeActiveError,
    LastGlobalAdminProtectedError,
    StaleUserVersionError,
    UserAdministrationAuditContext,
    UserAdministrationError,
    UserNotFoundError,
)
from ....modules.access_control import SYSTEM_USER_APPROVE, require_permission
from ....schemas.access_control import AccountStatusUpdate, GlobalAdminUpdate


router = APIRouter()


def _authorize(request: Request) -> Callable[[ConnectionLike], None]:
    def authorize(connection: ConnectionLike) -> None:
        require_permission(request, SYSTEM_USER_APPROVE, conn=connection)

    return authorize


def _audit(request: Request) -> UserAdministrationAuditContext:
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


def _raise_mapped(error: UserAdministrationError) -> NoReturn:
    if isinstance(error, UserNotFoundError):
        raise HTTPException(404, "사용자를 찾을 수 없습니다.") from error
    if isinstance(error, StaleUserVersionError):
        raise HTTPException(
            409,
            {"code": "STALE_USER_VERSION", "message": "사용자 정보가 다른 관리자에 의해 변경되었습니다."},
        ) from error
    if isinstance(error, LastGlobalAdminProtectedError):
        raise HTTPException(
            409, {"code": "LAST_GLOBAL_ADMIN_PROTECTED", "message": error.message}
        ) from error
    if isinstance(error, GlobalAdminMustBeActiveError):
        raise HTTPException(422, {"code": "GLOBAL_ADMIN_MUST_BE_ACTIVE"}) from error
    raise error


@router.patch("/api/admin/users/{user_id}/status")
def update_account_status(
    user_id: str, payload: AccountStatusUpdate, request: Request
) -> dict[str, Any]:
    try:
        return update_account_status_command(
            user_id,
            payload.account_status,
            payload.expected_updated_at,
            payload.reason,
            request.state.principal.user_id,
            _audit(request),
            SQLAccountStatusUnitOfWorkProvider(_authorize(request)),
        )
    except UserAdministrationError as error:
        _raise_mapped(error)


@router.patch("/api/admin/users/{user_id}/global-admin")
def update_global_admin(
    user_id: str, payload: GlobalAdminUpdate, request: Request
) -> dict[str, Any]:
    try:
        return update_global_admin_command(
            user_id,
            payload.is_global_admin,
            payload.expected_updated_at,
            payload.reason,
            _audit(request),
            SQLGlobalAdminUnitOfWorkProvider(_authorize(request)),
        )
    except UserAdministrationError as error:
        _raise_mapped(error)
