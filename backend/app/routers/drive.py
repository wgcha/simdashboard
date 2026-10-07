"""SCX drive administration and status API (integration 02 §2/§4, plan D0/D7).

* ``/api/admin/drive/*`` — global administrators only.
* ``/api/drive/status`` — any signed-in user; state string only (banner).

In ``none`` mode the status endpoints report ``mode: "none"`` and every admin
write returns 409 ``DRIVE_MODE_DISABLED``.  Token values are never returned,
logged or written to the audit trail.  No handler keeps a database connection
open while it waits for the drive (the worker may call ``TokenStore.save`` on
another thread meanwhile).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..security import write_audit_event
from ..services.drive import gateway as drive_gateway
from ..services.drive.check import CheckAlreadyRunning, run_drive_check
from ..services.drive.config import DriveSettings, url_host, validate_drive_rel_path
from ..services.drive.token_store import parse_obtained_at

admin_router = APIRouter(prefix="/api/admin/drive", tags=["drive"])
status_router = APIRouter(prefix="/api/drive", tags=["drive"])

MAX_CREDENTIALS_BODY = 64 * 1024
TOKEN_BUNDLE_SCHEMA = {
    "type": "object",
    "required": ["server_url", "access_token", "refresh_token"],
    "properties": {
        "server_url": {"type": "string"},
        "access_token": {"type": "string"},
        "refresh_token": {"type": "string"},
        "obtained_at": {"type": "string", "format": "date-time"},
        "account_hint": {"type": ["string", "null"]},
    },
}


class DriveCredentialsMeta(BaseModel):
    present: bool
    account_hint: str | None = None
    obtained_at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None
    key_matches: bool | None = None


class DriveHealth(BaseModel):
    state: str
    last_success_at: str | None = None
    last_error_code: str | None = None
    last_error_at: str | None = None
    token_refreshed_at: str | None = None
    queue_depth: int = 0
    in_flight: int = 0


class DriveAdminStatus(BaseModel):
    mode: str
    state: str | None = None
    server_url: str | None = None
    drive_root: str | None = None
    drive_root_locked: bool = False
    credentials: DriveCredentialsMeta | None = None
    credentials_problem: str | None = None
    health: DriveHealth | None = None
    worker_version: str | None = None
    token_save_failed_at: str | None = None


class DriveUserStatus(BaseModel):
    mode: str
    state: str | None = None


class DriveErrorInfo(BaseModel):
    code: str
    message: str


class DriveTestResult(BaseModel):
    ok: bool
    latency_ms: float | None = None
    root_identity: str | None = None
    error: DriveErrorInfo | None = None


class DriveCredentialsSaved(BaseModel):
    account_hint: str | None = None
    obtained_at: str
    test: DriveTestResult


class DriveCheckRequest(BaseModel):
    test_folder: str = Field(min_length=1, max_length=1024)


class DriveCheckStep(BaseModel):
    step: str
    label: str
    ok: bool
    skipped: bool = False
    code: str | None = None
    latency_ms: float | None = None
    observations: list[str] = Field(default_factory=list)


class DriveCheckReport(BaseModel):
    test_folder: str
    drive_path: str
    started_at: str
    finished_at: str
    ok: bool
    steps: list[DriveCheckStep]
    leftovers: list[str]
    note: str


def _fail(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def _require_admin(request: Request) -> None:
    principal = getattr(request.state, "principal", None)
    if principal is None or not principal.is_global_admin:
        raise _fail(403, "GLOBAL_ADMIN_REQUIRED", "드라이브 관리는 전체 관리자 기능입니다.")


def _settings() -> DriveSettings:
    return drive_gateway.current_settings()


def _require_scx() -> DriveSettings:
    settings = _settings()
    if not settings.enabled:
        raise _fail(409, "DRIVE_MODE_DISABLED", "SCX 드라이브 모드가 아닙니다(SIMDASH_DRIVE_GATEWAY=none).")
    return settings


def _error_info(error: BaseException) -> DriveErrorInfo:
    code = drive_gateway.drive_error_code(error) or "INTERNAL"
    mapping = drive_gateway.DRIVE_ERROR_MAP.get(code, drive_gateway.DRIVE_ERROR_MAP["INTERNAL"])
    return DriveErrorInfo(code=mapping.storage_code, message=drive_gateway.safe_error_message(error))


def _health(gateway: Any) -> dict[str, Any]:
    try:
        return drive_gateway.health_dict(gateway.health()) or {"state": "UNAVAILABLE"}
    except Exception as error:
        return {"state": "UNAVAILABLE", "last_error_code": drive_gateway.drive_error_code(error) or "INTERNAL"}


def _derived_state(credentials: dict[str, Any], health: dict[str, Any]) -> str:
    """Banner state: no usable token → AUTH_REQUIRED; never tried → UNKNOWN (no banner)."""
    if not credentials.get("present") or credentials.get("key_matches") is False:
        return "AUTH_REQUIRED"
    state = str(health.get("state") or "UNAVAILABLE")
    if state == "UNAVAILABLE" and not health.get("last_success_at") and not health.get("last_error_at") \
            and not health.get("last_error_code"):
        return "UNKNOWN"
    return state


def _scx_snapshot() -> tuple[dict[str, Any], dict[str, Any], Any]:
    store = drive_gateway.token_store()
    gateway = drive_gateway.get_drive_gateway()
    credentials = store.metadata()
    return credentials, _health(gateway), store


def _connection_test(gateway: Any) -> DriveTestResult:
    started = time.perf_counter()
    try:
        gateway.stat("", priority=drive_gateway.priority("INTERACTIVE"))
        identity = gateway.root_identity()
    except Exception as error:
        return DriveTestResult(ok=False, latency_ms=round((time.perf_counter() - started) * 1000, 1), error=_error_info(error))
    return DriveTestResult(ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 1), root_identity=str(identity))


@admin_router.get("/status", response_model=DriveAdminStatus)
def admin_status(request: Request) -> DriveAdminStatus:
    _require_admin(request)
    settings = _settings()
    if not settings.enabled:
        return DriveAdminStatus(mode="none")
    credentials, health, store = _scx_snapshot()
    problem = store.last_load_problem if credentials.get("present") else "MISSING"
    if credentials.get("key_matches") is False:
        problem = "KEY_CHANGED"
    gateway = drive_gateway.get_drive_gateway()
    version = getattr(gateway, "worker_version", None)
    saved_failed = store.last_save_error_at
    return DriveAdminStatus(
        mode="scx", state=_derived_state(credentials, health), server_url=settings.server_url,
        drive_root=settings.spdm_root, drive_root_locked=settings.spdm_root is not None,
        credentials=DriveCredentialsMeta(**credentials), credentials_problem=problem, health=DriveHealth(**health),
        worker_version=str(version) if isinstance(version, str) else None,
        token_save_failed_at=saved_failed.isoformat() if saved_failed else None,
    )


def _parse_bundle(raw: bytes, settings: DriveSettings) -> dict[str, Any]:
    invalid = lambda message: _fail(400, "DRIVE_CREDENTIALS_INVALID", message)  # noqa: E731
    if len(raw) > MAX_CREDENTIALS_BODY:
        raise invalid("토큰 JSON이 너무 큽니다.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise invalid("토큰 JSON을 해석할 수 없습니다. login 명령이 출력한 JSON 전체를 붙여 넣으세요.") from None
    if not isinstance(payload, dict):
        raise invalid("토큰 JSON은 객체여야 합니다.")
    server_url = payload.get("server_url")
    if not isinstance(server_url, str) or url_host(server_url) is None:
        raise invalid("server_url이 없거나 올바르지 않습니다.")
    if url_host(server_url) != settings.server_host:
        raise invalid(f"server_url의 호스트가 설정된 서버({settings.server_host})와 다릅니다.")
    tokens: dict[str, str] = {}
    for name in ("access_token", "refresh_token"):
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise invalid(f"{name}이(가) 비어 있습니다.")
        value = value.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if not value or any(ch.isspace() for ch in value) or len(value) > 16 * 1024:
            raise invalid(f"{name} 형식이 올바르지 않습니다.")
        tokens[name] = value
    hint = payload.get("account_hint")
    if hint is not None and (not isinstance(hint, str) or len(hint) > 200):
        raise invalid("account_hint는 200자 이하 문자열이어야 합니다.")
    raw_obtained = payload.get("obtained_at")
    try:
        obtained_at = parse_obtained_at(raw_obtained) if raw_obtained else datetime.now(timezone.utc)
    except (ValueError, TypeError):
        raise invalid("obtained_at은 ISO-8601 시각이어야 합니다.") from None
    return {"server_url": server_url.strip(), "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"], "obtained_at": obtained_at,
            "account_hint": hint.strip() if isinstance(hint, str) and hint.strip() else None}


def _register(request: Request, raw: bytes) -> DriveCredentialsSaved:
    settings = _require_scx()
    fields = _parse_bundle(raw, settings)
    store = drive_gateway.token_store()
    bundle = store.make_bundle(**fields)
    actor = request.state.principal.user_id
    store.write(bundle, updated_by=actor)
    obtained = bundle.obtained_at.isoformat(timespec="microseconds")
    write_audit_event(request=request, principal=request.state.principal, status_code=200,
                      action="DRIVE_CREDENTIALS_REGISTERED",
                      detail={"account_hint": bundle.account_hint, "obtained_at": obtained,
                              "server_host": settings.server_host, "key_id": store.key_id})
    test = _connection_test(drive_gateway.get_drive_gateway())
    return DriveCredentialsSaved(account_hint=bundle.account_hint, obtained_at=obtained, test=test)


@admin_router.put("/credentials", response_model=DriveCredentialsSaved, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": TOKEN_BUNDLE_SCHEMA}}}})
async def put_credentials(request: Request) -> DriveCredentialsSaved:
    _require_admin(request)
    await run_in_threadpool(_require_scx)
    raw = await request.body()
    return await run_in_threadpool(_register, request, raw)


@admin_router.delete("/credentials", status_code=204, response_class=Response)
def delete_credentials(request: Request) -> Response:
    _require_admin(request)
    _require_scx()
    store = drive_gateway.token_store()
    meta = store.metadata()
    existed = store.clear()
    write_audit_event(request=request, principal=request.state.principal, status_code=204,
                      action="DRIVE_CREDENTIALS_DELETED",
                      detail={"existed": existed, "account_hint": meta.get("account_hint")})
    return Response(status_code=204)


@admin_router.post("/test", response_model=DriveTestResult)
def connection_test(request: Request) -> DriveTestResult:
    _require_admin(request)
    _require_scx()
    return _connection_test(drive_gateway.get_drive_gateway())


@admin_router.post("/check", response_model=DriveCheckReport)
def drive_check(payload: DriveCheckRequest, request: Request) -> DriveCheckReport:
    _require_admin(request)
    settings = _require_scx()
    if settings.spdm_root is None:
        raise _fail(409, "DRIVE_ROOT_NOT_SET", "SIMDASH_SCX_DRIVE_ROOT(SPDM 루트)가 설정되지 않았습니다.")
    try:
        folder = validate_drive_rel_path(payload.test_folder.strip().strip("/"), allow_empty=False)
    except ValueError as error:
        raise _fail(400, "DRIVE_CHECK_FOLDER_INVALID", f"시험 폴더 경로가 올바르지 않습니다: {error}") from error
    try:
        report = run_drive_check(drive_gateway.get_drive_gateway(), spdm_root=settings.spdm_root,
                                 test_folder=folder, staging_dir=settings.staging_dir)
    except CheckAlreadyRunning as error:
        raise _fail(409, "DRIVE_CHECK_RUNNING", str(error)) from error
    failed = [step["step"] for step in report["steps"] if not step["ok"] and not step["skipped"]]
    write_audit_event(request=request, principal=request.state.principal, status_code=200, action="DRIVE_CHECK_RUN",
                      detail={"test_folder": folder, "ok": report["ok"], "failed_steps": failed,
                              "leftovers": report["leftovers"]})
    return DriveCheckReport(**report)


@status_router.get("/status", response_model=DriveUserStatus)
def user_status(request: Request) -> DriveUserStatus:
    del request  # any signed-in user (SecurityMiddleware)
    settings = _settings()
    if not settings.enabled:
        return DriveUserStatus(mode="none")
    credentials, health, _store = _scx_snapshot()
    return DriveUserStatus(mode="scx", state=_derived_state(credentials, health))
