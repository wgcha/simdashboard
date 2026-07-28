from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
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
from .database_connection import connect


Role = Literal["viewer", "editor", "admin"]
ROLE_LEVEL: dict[str, int] = {"viewer": 1, "editor": 2, "admin": 3}
PUBLIC_API_PATHS = {"/api/health", "/api/auth/status", "/api/auth/login"}
SESSION_COOKIE = "analysis_canvas_session"
ADMIN_MUTATION_PREFIXES = (
    "/api/import-schemas",
    "/api/quality-thresholds",
    "/api/admin/",
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    role: Role


class AuthenticationError(Exception):
    pass


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("비밀번호는 12자 이상이어야 합니다.")
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
            "SELECT id, username, display_name, role, is_active FROM users WHERE id=?",
            [user_id],
        ).fetchone()
    if not row or not row[4] or row[3] not in ROLE_LEVEL:
        raise AuthenticationError("사용할 수 없는 계정입니다.")
    return Principal(row[0], row[1], row[2], row[3])


def authenticate_request(request: Request) -> Principal:
    if security_settings().auth_mode == "disabled":
        return Principal("local-admin", "local", "로컬 관리자", "admin")
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
            "SELECT id, username, password_hash, display_name, role, is_active FROM users WHERE username=?",
            [username.strip().lower()],
        ).fetchone()
    if not row or not row[5] or row[4] not in ROLE_LEVEL or not verify_password(password, row[2]):
        return None
    return Principal(row[0], row[1], row[3], row[4])


def minimum_role(method: str, path: str) -> Role:
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "admin" if path.startswith("/api/admin/") or path.startswith("/api/audit-events") else "viewer"
    if method == "DELETE" or path.startswith(ADMIN_MUTATION_PREFIXES):
        return "admin"
    if "/variables" in path and method in {"POST", "PUT", "PATCH"}:
        return "admin"
    return "editor"


def write_audit_event(
    *,
    request: Request,
    principal: Principal | None,
    status_code: int,
    action: str,
    detail: dict[str, Any] | None = None,
) -> None:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    with connect() as conn:
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


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or str(uuid4())
        path = request.url.path
        protected_path = path.startswith("/api") or path.startswith("/assets/")
        if not protected_path or path in PUBLIC_API_PATHS or request.method == "OPTIONS":
            response = await call_next(request)
            response.headers["X-Request-Id"] = request.state.request_id
            return response

        principal: Principal | None = None
        try:
            principal = authenticate_request(request)
        except AuthenticationError as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            try:
                write_audit_event(request=request, principal=None, status_code=401, action="AUTHENTICATION_DENIED")
            except Exception:
                logger.exception("Failed to persist authentication audit event")
            response.headers["X-Request-Id"] = request.state.request_id
            return response

        request.state.principal = principal
        required = minimum_role(request.method, path)
        if ROLE_LEVEL[principal.role] < ROLE_LEVEL[required]:
            response = JSONResponse({"detail": f"{required} 역할이 필요한 작업입니다."}, status_code=403)
            try:
                write_audit_event(request=request, principal=principal, status_code=403, action="AUTHORIZATION_DENIED", detail={"required_role": required})
            except Exception:
                logger.exception("Failed to persist authorization audit event")
            response.headers["X-Request-Id"] = request.state.request_id
            return response

        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                write_audit_event(request=request, principal=principal, status_code=response.status_code, action="API_MUTATION")
            except Exception:
                logger.exception("Failed to persist mutation audit event")
        return response
