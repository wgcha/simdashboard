from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .config import security_settings
from .modules.access_control import AccountStatus
from .database_connection import ConnectionLike, connect
from .schemas.auth_limits import PASSWORD_MIN_LENGTH


Role = Literal["viewer", "editor", "admin"]
ROLE_LEVEL: dict[str, int] = {"viewer": 1, "editor": 2, "admin": 3}
PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/register",
    "/api/local-helper/distribution",
    "/api/local-helper/distribution/download",
    "/api/auth/oidc/start",
    "/api/auth/oidc/callback",
}
PENDING_ALLOWED_PATHS = {"/api/auth/me", "/api/auth/logout", "/api/auth/status"}
DEVICE_API_PATHS = {
    "/api/local-execution/device/pair-preview",
    "/api/local-execution/device/pair",
    "/api/local-execution/device/authorize",
    "/api/local-execution/device/events",
}
SESSION_COOKIE = "analysis_canvas_session"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    account_status: AccountStatus
    is_global_admin: bool
    employee_id: str | None
    # Retained only while password users and callers migrate away from the
    # viewer/editor/admin contract. It is never the source for new permissions.
    legacy_role: Role | None = None

    @property
    def role(self) -> Role:
        """Compatibility view for legacy response/audit code."""
        if self.is_global_admin:
            return "admin"
        return self.legacy_role if self.legacy_role in ROLE_LEVEL else "viewer"


class AuthenticationError(Exception):
    pass


def _truthy_environment(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def auth_setup_state() -> tuple[bool, str | None]:
    """Return public-safe account bootstrap readiness, without leaking state."""
    settings = security_settings()
    if settings.auth_mode == "oidc":
        return False, None
    if (
        settings.auth_mode == "disabled"
        and os.getenv("DEPLOYMENT_PROFILE", "local").strip().lower() == "local"
        and _truthy_environment("AUTH_ALLOW_INSECURE_LOCAL")
    ):
        return False, None
    if settings.auth_mode != "password":
        return True, "AUTH_SETUP_REQUIRED"
    if not settings.secret_key or len(settings.secret_key) < 32:
        return True, "AUTH_SECRET_REQUIRED"
    try:
        with connect() as conn:
            has_admin = conn.execute(
                "SELECT 1 FROM users WHERE is_global_admin=true AND is_active=true AND account_status='ACTIVE' AND password_hash IS NOT NULL AND password_hash <> '' LIMIT 1"
            ).fetchone()
    except Exception:
        return True, "AUTH_SETUP_REQUIRED"
    return (not bool(has_admin), "INITIAL_ADMIN_REQUIRED" if not has_admin else None)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_b64url(salt)}${_b64url(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64url_decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(_b64url(digest), expected)
    except (ValueError, TypeError):
        return False


def create_access_token(principal: Principal) -> tuple[str, int]:
    settings = security_settings()
    if not settings.secret_key:
        raise RuntimeError("AUTH_SECRET_KEY가 설정되지 않았습니다.")
    now = int(time.time())
    expires_at = now + settings.token_ttl_minutes * 60
    payload = _b64url(json.dumps({"sub": principal.user_id, "iat": now, "exp": expires_at}, separators=(",", ":")).encode("utf-8"))
    signature = _b64url(hmac.new(settings.secret_key.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}", expires_at


def _token_user_id(token: str) -> str:
    settings = security_settings()
    if not settings.secret_key:
        raise AuthenticationError("인증 설정이 준비되지 않았습니다.")
    try:
        payload, supplied_signature = token.split(".", 1)
        expected_signature = _b64url(hmac.new(settings.secret_key.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise AuthenticationError("인증 토큰이 올바르지 않습니다.")
        claims = json.loads(_b64url_decode(payload))
        if int(claims["exp"]) <= int(time.time()):
            raise AuthenticationError("인증 토큰이 만료되었습니다.")
        return str(claims["sub"])
    except AuthenticationError:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AuthenticationError("인증 토큰이 올바르지 않습니다.") from exc


def _load_principal(user_id: str) -> Principal:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, username, display_name, account_status, is_global_admin,
                   employee_id, legacy_role, is_active
            FROM users WHERE id=?
            """,
            [user_id],
        ).fetchone()
    if not row or row[3] not in {"PENDING", "ACTIVE", "SUSPENDED"}:
        raise AuthenticationError("사용할 수 없는 계정입니다.")
    account_status: AccountStatus = row[3]
    if not row[7] and account_status == "ACTIVE":
        account_status = "SUSPENDED"
    legacy_role = row[6] if row[6] in ROLE_LEVEL else None
    return Principal(row[0], row[1], row[2], account_status, bool(row[4]), row[5], legacy_role)


def authenticate_request(request: Request) -> Principal:
    if security_settings().auth_mode == "disabled":
        if auth_setup_state()[0]:
            raise AuthenticationError("계정 설정이 완료되지 않았습니다.")
        return Principal("local-admin", "local", "로컬 관리자", "ACTIVE", True, None, "admin")
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        raise AuthenticationError("Bearer 인증 토큰이 필요합니다.")
    return _load_principal(_token_user_id(token))


def authenticate_credentials(username: str, password: str) -> Principal | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, username, password_hash, display_name, legacy_role, is_active,
                   account_status, is_global_admin, employee_id
            FROM users WHERE username=?
            """,
            [username.strip().lower()],
        ).fetchone()
    if (
        not row
        or not row[5]
        or row[6] != "ACTIVE"
        or not row[2]
        or not verify_password(password, row[2])
    ):
        return None
    legacy_role = row[4] if row[4] in ROLE_LEVEL else None
    return Principal(row[0], row[1], row[3], "ACTIVE", bool(row[7]), row[8], legacy_role)


def write_audit_event(
    *,
    request: Request,
    principal: Principal | None,
    status_code: int,
    action: str,
    detail: dict[str, Any] | None = None,
    connection: ConnectionLike | None = None,
) -> None:
    request_id = getattr(request.state, "request_id", str(uuid4()))

    def insert(conn: ConnectionLike) -> None:
        conn.execute(
            """
            INSERT INTO audit_events
            (id, occurred_at, user_id, username, role, action, method, path, status_code, request_id, client_ip, user_agent, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(uuid4()),
                datetime.now(timezone.utc).replace(tzinfo=None),
                principal.user_id if principal else None,
                principal.username if principal else None,
                principal.role if principal else None,
                action,
                request.method,
                request.url.path,
                status_code,
                request_id,
                request.client.host if request.client else None,
                request.headers.get("user-agent", "")[:500],
                json.dumps(detail or {}, ensure_ascii=False),
            ],
        )

    if connection is not None:
        insert(connection)
        return
    with connect() as conn:
        insert(conn)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or str(uuid4())
        path = request.url.path
        protected_path = path.startswith("/api") or path.startswith("/assets/")
        # Device requests have their own pairing-secret authentication in the
        # router.  This remains an exact-path allow-list: no other /api/local-
        # execution route can bypass ordinary company authentication.
        if not protected_path or path in PUBLIC_API_PATHS or (request.method == "POST" and path in DEVICE_API_PATHS) or request.method == "OPTIONS":
            response = await call_next(request)
            response.headers["X-Request-Id"] = request.state.request_id
            return response

        setup_required, setup_reason = auth_setup_state()
        if setup_required:
            response = JSONResponse(
                {"detail": {"code": "AUTH_SETUP_REQUIRED", "message": "서버 계정 설정이 필요합니다.", "setup_reason": setup_reason}},
                status_code=503,
            )
            response.headers["X-Request-Id"] = request.state.request_id
            return response

        principal: Principal | None = None
        try:
            principal = authenticate_request(request)
        except AuthenticationError as exc:
            response = JSONResponse(
                {
                    "detail": {
                        "code": "AUTHENTICATION_REQUIRED",
                        "message": str(exc),
                    }
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            try:
                write_audit_event(request=request, principal=None, status_code=401, action="AUTHENTICATION_DENIED")
            except Exception:
                logger.exception("Failed to persist authentication audit event")
            response.headers["X-Request-Id"] = request.state.request_id
            return response

        request.state.principal = principal
        if principal.account_status == "PENDING" and path not in PENDING_ALLOWED_PATHS:
            response = JSONResponse(
                {
                    "detail": {
                        "code": "AUTH_ACCOUNT_PENDING",
                        "message": "전역 관리자의 계정 승인을 기다리고 있습니다.",
                    }
                },
                status_code=403,
            )
            try:
                write_audit_event(
                    request=request,
                    principal=principal,
                    status_code=403,
                    action="ACCOUNT_PENDING_BLOCKED",
                )
            except Exception:
                logger.exception("Failed to persist pending-account audit event")
            response.headers["X-Request-Id"] = request.state.request_id
            return response
        if principal.account_status == "SUSPENDED":
            response = JSONResponse(
                {
                    "detail": {
                        "code": "AUTH_ACCOUNT_SUSPENDED",
                        "message": "중지된 계정입니다. 관리자에게 문의하세요.",
                    }
                },
                status_code=403,
            )
            try:
                write_audit_event(
                    request=request,
                    principal=principal,
                    status_code=403,
                    action="ACCOUNT_SUSPENDED_BLOCKED",
                )
            except Exception:
                logger.exception("Failed to persist suspended-account audit event")
            response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
            response.headers["X-Request-Id"] = request.state.request_id
            return response
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        if response.status_code == 403:
            try:
                write_audit_event(
                    request=request,
                    principal=principal,
                    status_code=403,
                    action="AUTHORIZATION_DENIED",
                    detail=getattr(request.state, "authorization_detail", None),
                )
            except Exception:
                logger.exception("Failed to persist authorization audit event")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                write_audit_event(request=request, principal=principal, status_code=response.status_code, action="API_MUTATION")
            except Exception:
                logger.exception("Failed to persist mutation audit event")
        return response
