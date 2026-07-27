from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..config import security_settings
from ..database import json_value
from ..database_connection import connect, rows
from ..schemas.api import LoginPayload, UserAccessUpdate
from ..security import SESSION_COOKIE, authenticate_credentials, create_access_token, write_audit_event


router = APIRouter()


@router.get("/api/auth/status")
def auth_status() -> dict[str, Any]:
    settings = security_settings()
    return {"mode": settings.auth_mode, "authentication_required": settings.auth_mode != "disabled"}


@router.post("/api/auth/login")
def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    if security_settings().auth_mode != "password":
        raise HTTPException(409, "현재 실행 모드에서는 로그인이 필요하지 않습니다.")
    principal = authenticate_credentials(payload.username, payload.password)
    if not principal:
        write_audit_event(request=request, principal=None, status_code=401, action="LOGIN_FAILED", detail={"username": payload.username.strip().lower()})
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.", headers={"WWW-Authenticate": "Bearer"})
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
        "user": {"id": principal.user_id, "username": principal.username, "display_name": principal.display_name, "role": principal.role},
    }


@router.post("/api/auth/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return {"status": "ok"}


@router.get("/api/auth/me")
def current_user(request: Request) -> dict[str, str]:
    principal = request.state.principal
    return {"id": principal.user_id, "username": principal.username, "display_name": principal.display_name, "role": principal.role}


@router.get("/api/admin/users")
def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT id, username, display_name, role, is_active, created_at, updated_at FROM users ORDER BY username"))


@router.patch("/api/admin/users/{user_id}")
def update_user_access(user_id: str, payload: UserAccessUpdate) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE id=?", [user_id]).fetchone()
        if not existing:
            raise HTTPException(404, "사용자를 찾을 수 없습니다.")
        conn.execute("UPDATE users SET role=?, is_active=?, updated_at=? WHERE id=?", [payload.role, payload.is_active, now, user_id])
        row = conn.execute("SELECT id, username, display_name, role, is_active, created_at, updated_at FROM users WHERE id=?", [user_id])
        return rows(row)[0]


@router.get("/api/audit-events")
def audit_events(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    with connect() as conn:
        items = rows(conn.execute("SELECT * FROM audit_events ORDER BY occurred_at DESC LIMIT ?", [limit]))
    for item in items:
        item["detail_json"] = json_value(item.get("detail_json")) or {}
    return items
