from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.responses import JSONResponse, RedirectResponse
from ..adapters.http.auth_validation import PrivateInputRoute

from ..config import security_settings
from ..modules.access_control import AUDIT_VIEW, COMPANY_PERMISSIONS, SYSTEM_USER_APPROVE, require_permission
from ..database import json_value
from ..database_connection import connect, rows
from ..schemas.api import LoginPayload
from ..schemas.auth_accounts import AuthStatusResponse
from ..security import Principal, SESSION_COOKIE, auth_setup_state, authenticate_credentials, create_access_token, write_audit_event
from ..services.oidc_service import (
    OIDC_FLOW_TTL_SECONDS,
    OidcProtocolError,
    authorization_url,
    create_flow,
    decode_flow_cookie,
    encode_flow_cookie,
    exchange_code,
    fetch_discovery,
    validate_id_token,
)


router = APIRouter(route_class=PrivateInputRoute)
OIDC_FLOW_COOKIE = "analysis_canvas_oidc_flow"


def _oidc_error(request: Request, error: OidcProtocolError, principal: Principal | None = None) -> JSONResponse:
    try:
        write_audit_event(
            request=request,
            principal=principal,
            status_code=error.status_code,
            action="OIDC_LOGIN_FAILED",
            detail={"code": error.code},
        )
    except Exception:
        pass
    response = JSONResponse(
        {"detail": {"code": error.code, "message": error.message}},
        status_code=error.status_code,
    )
    response.delete_cookie(OIDC_FLOW_COOKIE, path="/api/auth/oidc", httponly=True, samesite="lax")
    return response


def _claim(claims: dict[str, Any], name: str, *, required: bool = False, max_length: int = 255) -> str | None:
    value = claims.get(name)
    normalized = str(value).strip()[:max_length] if value is not None else ""
    if required and not normalized:
        raise OidcProtocolError("OIDC_REQUIRED_CLAIM_MISSING", f"필수 OIDC claim({name})이 없습니다.")
    return normalized or None


def _upsert_oidc_user(request: Request, claims: dict[str, Any]) -> tuple[Principal, bool]:
    settings = security_settings()
    issuer = str(settings.oidc_issuer_url).rstrip("/")
    subject = _claim(claims, "sub", required=True)
    username = _claim(claims, settings.oidc_username_claim, required=True, max_length=120)
    display_name = _claim(claims, settings.oidc_display_name_claim, required=True, max_length=200)
    employee_id = _claim(claims, settings.oidc_employee_id_claim, max_length=120)
    department = _claim(claims, settings.oidc_department_claim, max_length=200)
    job_title = _claim(claims, settings.oidc_job_title_claim, max_length=200)
    email = _claim(claims, "email", max_length=254)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id, username, display_name, account_status, is_global_admin,
                   employee_id, legacy_role, is_active
            FROM users WHERE oidc_issuer=? AND oidc_subject=?
            """,
            [issuer, subject],
        ).fetchone()
        created = False
        if existing is None:
            conflicts = conn.execute(
                """
                SELECT id FROM users
                WHERE lower(username)=lower(?)
                   OR (? IS NOT NULL AND employee_id=?)
                LIMIT 1
                """,
                [username, employee_id, employee_id],
            ).fetchone()
            if conflicts:
                write_audit_event(
                    request=request,
                    principal=None,
                    status_code=409,
                    action="OIDC_LOGIN_FAILED",
                    detail={"code": "OIDC_IDENTITY_CONFLICT"},
                    connection=conn,
                )
                raise OidcProtocolError(
                    "OIDC_IDENTITY_CONFLICT",
                    "동일 임직원 ID 또는 사용자 이름이 다른 인증 계정에 연결되어 있습니다.",
                    409,
                )
            user_id = f"user-{uuid4().hex}"
            conn.execute(
                """
                INSERT INTO users
                    (id, username, password_hash, display_name, legacy_role, is_active,
                     created_at, updated_at, employee_id, email, department, job_title,
                     oidc_issuer, oidc_subject, account_status, is_global_admin, last_login_at)
                VALUES (?, ?, NULL, ?, NULL, true, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', false, ?)
                """,
                [
                    user_id,
                    username.lower(),
                    display_name,
                    now,
                    now,
                    employee_id,
                    email,
                    department,
                    job_title,
                    issuer,
                    subject,
                    now,
                ],
            )
            existing = (user_id, username.lower(), display_name, "PENDING", False, employee_id, None, True)
            created = True
        else:
            conn.execute(
                """
                UPDATE users
                SET username=?, display_name=?, employee_id=?, email=?, department=?, job_title=?,
                    last_login_at=?, updated_at=?
                WHERE id=?
                """,
                [username.lower(), display_name, employee_id, email, department, job_title, now, now, existing[0]],
            )
            existing = (
                existing[0],
                username.lower(),
                display_name,
                existing[3],
                existing[4],
                employee_id,
                existing[6],
                existing[7],
            )

        account_status = existing[3]
        if not existing[7] and account_status == "ACTIVE":
            account_status = "SUSPENDED"
        principal = Principal(
            user_id=existing[0],
            username=existing[1],
            display_name=existing[2],
            account_status=account_status,
            is_global_admin=bool(existing[4]),
            employee_id=existing[5],
            legacy_role=existing[6] if existing[6] in {"viewer", "editor", "admin"} else None,
        )
        if principal.account_status == "SUSPENDED":
            write_audit_event(
                request=request,
                principal=principal,
                status_code=403,
                action="OIDC_LOGIN_FAILED",
                detail={"code": "AUTH_ACCOUNT_SUSPENDED"},
                connection=conn,
            )
            raise OidcProtocolError("AUTH_ACCOUNT_SUSPENDED", "중지된 계정입니다.", 403)
        if created:
            write_audit_event(
                request=request,
                principal=principal,
                status_code=201,
                action="ACCOUNT_CREATED_PENDING",
                connection=conn,
            )
        write_audit_event(
            request=request,
            principal=principal,
            status_code=200,
            action="OIDC_LOGIN_SUCCEEDED",
            connection=conn,
        )
    return principal, created


@router.get("/api/auth/status", response_model=AuthStatusResponse)
def auth_status() -> AuthStatusResponse:
    settings = security_settings()
    setup_required, setup_reason = auth_setup_state()
    return AuthStatusResponse(
        mode=settings.auth_mode,
        authentication_required=settings.auth_mode != "disabled" or setup_required,
        registration_enabled=settings.auth_mode == "password" and not setup_required,
        setup_required=setup_required,
        setup_reason=setup_reason,
        oidc_start_url="/api/auth/oidc/start" if settings.auth_mode == "oidc" else None,
    )


@router.get("/api/auth/oidc/start")
async def oidc_start(request: Request) -> Response:
    settings = security_settings()
    if settings.auth_mode != "oidc":
        raise HTTPException(409, "현재 인증 모드에서는 OIDC 로그인을 사용할 수 없습니다.")
    try:
        discovery = await fetch_discovery(settings)
        flow = create_flow()
        response = RedirectResponse(authorization_url(settings, discovery, flow), status_code=302)
        response.set_cookie(
            OIDC_FLOW_COOKIE,
            encode_flow_cookie(flow, str(settings.secret_key)),
            max_age=OIDC_FLOW_TTL_SECONDS,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/api/auth/oidc",
        )
        return response
    except OidcProtocolError as error:
        return _oidc_error(request, error)


@router.get("/api/auth/oidc/callback")
async def oidc_callback(request: Request, code: str | None = None, state: str | None = None) -> Response:
    settings = security_settings()
    if settings.auth_mode != "oidc":
        raise HTTPException(409, "현재 인증 모드에서는 OIDC 로그인을 사용할 수 없습니다.")
    try:
        cookie = request.cookies.get(OIDC_FLOW_COOKIE, "")
        if not cookie or not code or not state:
            raise OidcProtocolError("OIDC_CALLBACK_INVALID", "OIDC callback 요청이 완전하지 않습니다.")
        flow = decode_flow_cookie(cookie, str(settings.secret_key))
        if not hmac.compare_digest(flow.state, state):
            raise OidcProtocolError("OIDC_STATE_MISMATCH", "OIDC state가 일치하지 않습니다.")
        discovery = await fetch_discovery(settings)
        id_token = await exchange_code(settings, discovery, code=code, code_verifier=flow.code_verifier)
        claims = await validate_id_token(settings, discovery, id_token=id_token, expected_nonce=flow.nonce)
        principal, _ = _upsert_oidc_user(request, claims)
        token, _ = create_access_token(principal)
        response = RedirectResponse(settings.oidc_login_success_url, status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=settings.token_ttl_minutes * 60,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/",
        )
        response.delete_cookie(OIDC_FLOW_COOKIE, path="/api/auth/oidc", httponly=True, samesite="lax")
        return response
    except OidcProtocolError as error:
        return _oidc_error(request, error)


@router.post("/api/auth/login")
def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    if security_settings().auth_mode != "password":
        raise HTTPException(409, "현재 실행 모드에서는 로그인이 필요하지 않습니다.")
    if auth_setup_state()[0]:
        raise HTTPException(503, "서버 계정 설정이 필요합니다.")
    principal = authenticate_credentials(payload.username, payload.password)
    if not principal:
        write_audit_event(request=request, principal=None, status_code=401, action="LOGIN_FAILED", detail={"username": payload.username.strip().lower()})
        raise HTTPException(401, "아이디·비밀번호와 계정 승인 상태를 확인해 주세요.", headers={"WWW-Authenticate": "Bearer"})
    token, expires_at = create_access_token(principal)
    settings = security_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.token_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    request.state.principal = principal
    write_audit_event(request=request, principal=principal, status_code=200, action="LOGIN_SUCCEEDED")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {
            "id": principal.user_id,
            "username": principal.username,
            "display_name": principal.display_name,
            "employee_id": principal.employee_id,
            "account_status": principal.account_status,
            "is_global_admin": principal.is_global_admin,
            "role": principal.role,
        },
    }


@router.post("/api/auth/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return {"status": "ok"}


@router.get("/api/auth/me")
def current_user(request: Request) -> dict[str, Any]:
    principal = request.state.principal
    memberships: list[dict[str, str]] = []
    if principal.user_id != "local-admin":
        with connect() as conn:
            memberships = rows(
                conn.execute(
                    "SELECT project_id, role FROM project_memberships WHERE user_id=? ORDER BY project_id",
                    [principal.user_id],
                )
            )
    return {
        "id": principal.user_id,
        "username": principal.username,
        "display_name": principal.display_name,
        "employee_id": principal.employee_id,
        "account_status": principal.account_status,
        "is_global_admin": principal.is_global_admin,
        "memberships": memberships,
        "company_permissions": sorted(COMPANY_PERMISSIONS),
        # One-release compatibility field; frontends use the new fields above.
        "role": principal.role,
    }


@router.get("/api/admin/users")
def list_users(
    request: Request,
    q: str | None = Query(default=None, max_length=80),
    status: Literal["PENDING", "ACTIVE", "SUSPENDED"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=120),
) -> list[dict[str, Any]]:
    require_permission(request, SYSTEM_USER_APPROVE)
    with connect() as conn:
        clauses = ["id <> 'local-admin'"] if security_settings().auth_mode != "disabled" else []
        parameters: list[Any] = []
        if q and q.strip():
            clauses.append(
                "(lower(username) LIKE ? OR lower(display_name) LIKE ? OR lower(COALESCE(employee_id, '')) LIKE ?)"
            )
            pattern = f"%{q.strip().lower()}%"
            parameters.extend([pattern, pattern, pattern])
        if status:
            clauses.append("account_status=?")
            parameters.append(status)
        if cursor:
            clauses.append("id>?")
            parameters.append(cursor)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        return rows(
            conn.execute(
                f"""
                SELECT id, username, display_name, employee_id, email, department, job_title,
                       account_status, is_global_admin, legacy_role, is_active,
                       approved_by, approved_at, last_login_at, created_at, updated_at
                FROM users {where} ORDER BY id LIMIT ?
                """,
                parameters,
            )
        )


@router.get("/api/audit-events")
def audit_events(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    require_permission(request, AUDIT_VIEW)
    with connect() as conn:
        items = rows(conn.execute("SELECT * FROM audit_events ORDER BY occurred_at DESC LIMIT ?", [limit]))
    for item in items:
        item["detail_json"] = json_value(item.get("detail_json")) or {}
    return items
