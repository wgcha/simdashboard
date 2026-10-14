from __future__ import annotations

from typing import Any

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from ..adapters.http.auth_validation import PrivateInputRoute

from ..config import security_settings
from ..database_connection import connect
from ..repositories.auth_accounts import registration_transaction
from ..schemas.auth_accounts import (
    PasswordChangePayload,
    PasswordChangeResponse,
    RegistrationPayload,
    RegistrationResponse,
)
from ..modules.access_control import SYSTEM_USER_APPROVE, require_permission
from ..security import Principal, auth_setup_state, write_audit_event
from ..schemas.auth_limits import password_policy_problem
from ..services.auth_accounts import (
    PasswordChangeResult,
    UsernameAlreadyRegisteredError,
    change_password,
    registration_attempt_limiter,
    register_password_account,
)


router = APIRouter(tags=["auth"], route_class=PrivateInputRoute)


def _password_auth_required() -> None:
    if security_settings().auth_mode != "password":
        raise HTTPException(409, "현재 인증 모드에서는 비밀번호 계정을 사용할 수 없습니다.")
    if auth_setup_state()[0]:
        raise HTTPException(503, "서버 계정 설정이 필요합니다.")


@router.post("/api/auth/register", status_code=201, response_model=RegistrationResponse)
def register(payload: RegistrationPayload, request: Request) -> dict[str, Any]:
    _password_auth_required()
    client_key = request.client.host if request.client else "unknown"
    if not registration_attempt_limiter.allow(client_key):
        write_audit_event(
            request=request,
            principal=None,
            status_code=429,
            action="ACCOUNT_REGISTRATION_RATE_LIMITED",
        )
        raise HTTPException(429, "회원가입 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.", headers={"Retry-After": "60"})
    problem = password_policy_problem(payload.password, payload.username)
    if problem:
        raise HTTPException(422, {"code": "PASSWORD_POLICY", "message": problem})
    with connect() as conn:
        try:
            with registration_transaction(conn):
                account = register_password_account(
                    conn,
                    username=payload.username,
                    display_name=payload.display_name,
                    password=payload.password,
                )
                audit_principal = Principal(
                    user_id=account.user_id,
                    username=account.username,
                    display_name=account.display_name,
                    account_status="PENDING",
                    is_global_admin=False,
                    employee_id=None,
                )
                write_audit_event(
                    request=request,
                    principal=audit_principal,
                    status_code=201,
                    action="ACCOUNT_CREATED_PENDING",
                    connection=conn,
                )
        except UsernameAlreadyRegisteredError as exc:
            write_audit_event(
                request=request,
                principal=None,
                status_code=409,
                action="ACCOUNT_REGISTRATION_CONFLICT",
                detail={"username": payload.username},
            )
            raise HTTPException(409, "이미 사용 중인 사용자 이름입니다.") from exc
        except Exception:
            raise
    return {
        "user_id": account.user_id,
        "username": account.username,
        "account_status": "PENDING",
        "message": "관리자 승인 후 로그인할 수 있습니다.",
    }


@router.post("/api/auth/password", response_model=PasswordChangeResponse)
def update_password(payload: PasswordChangePayload, request: Request) -> dict[str, bool]:
    _password_auth_required()
    principal = request.state.principal
    problem = password_policy_problem(payload.new_password, principal.username)
    if not problem and payload.new_password == payload.current_password:
        problem = "새 비밀번호는 현재 비밀번호와 달라야 합니다."
    if problem:
        raise HTTPException(422, {"code": "PASSWORD_POLICY", "message": problem})
    with connect() as conn:
        result = change_password(
            conn,
            user_id=principal.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    if result is PasswordChangeResult.NOT_PASSWORD_ACCOUNT:
        raise HTTPException(409, "비밀번호 로그인 계정에서만 비밀번호를 변경할 수 있습니다.")
    if result is PasswordChangeResult.CURRENT_PASSWORD_INVALID:
        write_audit_event(
            request=request,
            principal=principal,
            status_code=400,
            action="PASSWORD_CHANGE_FAILED",
        )
        raise HTTPException(400, "현재 비밀번호가 올바르지 않습니다.")
    if result is PasswordChangeResult.CONCURRENT_CHANGE:
        raise HTTPException(409, "비밀번호가 이미 변경되었습니다. 현재 비밀번호를 확인한 뒤 다시 시도해 주세요.")
    write_audit_event(request=request, principal=principal, status_code=200, action="PASSWORD_CHANGED")
    return {"ok": True}


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_updated_at: datetime
    reason: str = Field(min_length=2, max_length=500)


@router.post("/api/admin/users/{user_id}/password-reset")
def reset_password(user_id: str, payload: PasswordResetRequest, request: Request, response: Response) -> dict[str, Any]:
    """[비밀번호 초기화] (2026-10-14): a one-time temporary password, returned once, and a forced change.

    Only the hash is stored; the temporary password is never logged or audited. The account must
    set a new password (policy) before any other API works (``PASSWORD_CHANGE_REQUIRED``).
    """
    from ..services.auth_accounts import PasswordResetResult, reset_password_by_admin, temporary_password

    _password_auth_required()
    reason = payload.reason.strip()
    if len(reason) < 2:
        raise HTTPException(422, {"code": "PASSWORD_RESET_REASON_REQUIRED", "message": "초기화 사유를 2자 이상 입력하세요."})
    temporary = temporary_password()
    with connect() as conn:
        require_permission(request, SYSTEM_USER_APPROVE, conn=conn)
        if user_id == request.state.principal.user_id:
            raise HTTPException(409, {"code": "PASSWORD_RESET_SELF", "message": "자기 계정은 [계정 비밀번호 변경]에서 바꾸세요."})
        target = conn.execute("SELECT username, account_status FROM users WHERE id=?", [user_id]).fetchone()
        if target is not None and target[1] == "PENDING":
            raise HTTPException(409, {"code": "PASSWORD_RESET_PENDING", "message": "승인 대기 계정은 초기화할 수 없습니다. 먼저 승인하세요."})
        result, updated_at = reset_password_by_admin(conn, user_id=user_id, expected_updated_at=payload.expected_updated_at,
                                                     new_password=temporary)
        if result is PasswordResetResult.RESET:
            write_audit_event(request=request, principal=request.state.principal, status_code=200,
                              action="PASSWORD_RESET_BY_ADMIN",
                              detail={"target_user_id": user_id, "target_username": target[0] if target else None,
                                      "reason": reason}, connection=conn)
    if result is PasswordResetResult.NOT_FOUND:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    if result is PasswordResetResult.NOT_PASSWORD_ACCOUNT:
        raise HTTPException(409, {"code": "NOT_PASSWORD_ACCOUNT", "message": "비밀번호 로그인 계정이 아닙니다(SSO 계정)."})
    if result is PasswordResetResult.STALE:
        raise HTTPException(409, {"code": "STALE_USER_VERSION", "message": "사용자 정보가 다른 관리자에 의해 변경되었습니다. 새로고침 후 다시 시도하세요."})
    response.headers["Cache-Control"] = "no-store"
    return {"user_id": user_id, "temporary_password": temporary, "must_change_password": True,
            "updated_at": updated_at.isoformat() if updated_at else None}
