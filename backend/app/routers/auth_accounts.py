from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from ..adapters.http.auth_validation import PrivateInputRoute

from ..config import security_settings
from ..database_connection import connect
from ..schemas.auth_accounts import (
    PasswordChangePayload,
    PasswordChangeResponse,
    RegistrationPayload,
    RegistrationResponse,
)
from ..security import Principal, auth_setup_state, write_audit_event
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
    with connect() as conn:
        try:
            conn.execute("BEGIN TRANSACTION")
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
            conn.execute("COMMIT")
        except UsernameAlreadyRegisteredError as exc:
            conn.execute("ROLLBACK")
            write_audit_event(
                request=request,
                principal=None,
                status_code=409,
                action="ACCOUNT_REGISTRATION_CONFLICT",
                detail={"username": payload.username},
            )
            raise HTTPException(409, "이미 사용 중인 사용자 이름입니다.") from exc
        except Exception:
            conn.execute("ROLLBACK")
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
