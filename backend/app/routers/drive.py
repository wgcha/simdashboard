"""SCX drive administration and status API (integration 02 §2/§4, plan D0/D7).

* ``/api/admin/drive/*`` — global administrators only.
* ``/api/drive/status`` — any signed-in user; state string and write availability (banner, read-only notice).
* ``/api/drive/source-changes`` — drive files of a request whose change or removal waits for
  confirmation (stage D2, 05 §4): list (request viewers), accept/dismiss (result import permission).
* ``/api/drive/upload-batches/{batch_id}`` — progress of one drive upload batch (request viewers, D3).
* ``/api/admin/drive/queue`` — the drive upload queue (D3): list, retry, cancel, resume after
  re-authentication.  Cancelling only marks the row; nothing on the drive is deleted.
* ``/api/admin/drive/auth|reconnect|login`` — re-login from the dashboard (issue #50,
  docs/plans/scx-drive-relogin-spec.md §3): status, refresh with the stored token, account
  login.  The login body (password) is read by hand (no validation echo), never logged,
  stored, audited or notified; failures return codes only.  A token of an account other
  than the remembered "연결 계정" is saved only with ``confirm_account_change``.

In ``none`` mode the status endpoints report ``mode: "none"`` and every admin
write returns 409 ``DRIVE_MODE_DISABLED``.  Token values are never returned,
logged or written to the audit trail.  No handler keeps a database connection
open while it waits for the drive (the worker may call ``TokenStore.save`` on
another thread meanwhile).

Status endpoints never return 500 and never build the gateway: they read
health only from a gateway that an admin action (register/test/check) or a
later drive feature already built; without one the state is ``UNKNOWN``
(token present) or ``AUTH_REQUIRED`` (no usable token).  Any failure while
reading status is reported as ``UNAVAILABLE``.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..database_connection import connect
from ..modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT, require_resource_permission
from ..security import write_audit_event
from ..services import folder_auto_sync
from ..services import notifications
from ..services.drive import auth as drive_auth
from ..services.drive import gateway as drive_gateway
from ..services.drive import reads as drive_reads
from ..services.drive import sources as drive_sources
from ..services.drive import upload_queue
from ..services.drive import writes as drive_writes
from ..services.drive.check import CheckAlreadyRunning, run_drive_check
from ..services.drive.config import DriveSettings, url_host, validate_drive_rel_path
from ..services.drive.token_store import parse_obtained_at, same_account
from ..services.storage.drive import drive_root_from_settings

logger = logging.getLogger("app.routers.drive")

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


class DriveTokenObservation(BaseModel):
    """Which ``version_token`` form the drive listings produced (C1/C9: sha1 in listings?)."""

    last_kind: str | None = None
    sha1_files: int = 0
    size_mtime_files: int = 0
    last_at: str | None = None


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
    writes_enabled: bool = False
    writes_available: bool = True
    version_tokens: DriveTokenObservation | None = None


class DriveUserStatus(BaseModel):
    mode: str
    state: str | None = None
    writes_enabled: bool = False
    writes_available: bool = True
    queue_paused: bool = False
    can_reconnect: bool = False


class DriveAuthCapabilities(BaseModel):
    refresh: bool = False
    password: bool = False
    mfa: bool = False


class DriveAuthStatus(BaseModel):
    """Reconnect dialog (issue #50): what the adapter supports and why account login may be refused."""

    capabilities: DriveAuthCapabilities
    adapter_supported: bool
    capabilities_error: str | None = None
    connected_account: str | None = None
    account_hint: str | None = None
    credentials_present: bool = False
    obtained_at: str | None = None
    expires_at: str | None = None
    last_refresh_at: str | None = None
    last_error_code: str | None = None
    last_error_at: str | None = None
    password_login: str
    password_login_allowed: bool = False
    password_login_blocked: str | None = None   # OFF | HTTP
    attempts_left: int = 0
    max_attempts: int = 0
    locked_until: str | None = None


class DriveReconnectRequest(BaseModel):
    confirm_account_change: bool = False


class DriveReloginResult(BaseModel):
    ok: bool = True
    account_hint: str | None = None
    obtained_at: str
    account_changed: bool = False
    queue_resumed: int = 0


class DriveQueueItem(BaseModel):
    id: str
    batch_id: str
    seq: int
    kind: str
    state: str
    target: str
    source: str | None = None
    size: int | None = None
    attempts: int = 0
    next_attempt_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    transfer_method: str | None = None
    requested_by: str | None = None
    origin: str
    origin_ref: str | None = None
    project_id: str | None = None
    request_id: str | None = None
    environment: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None


class DriveQueueList(BaseModel):
    counts: dict[str, int]
    paused: bool
    worker_running: bool
    writes_enabled: bool = False
    items: list[DriveQueueItem]


QUEUE_STATE_PATTERN = "^(PENDING|RUNNING|DONE|CONFLICT|FAILED|BLOCKED|CANCELLED)$"


class DriveQueueFilter(BaseModel):
    """The admin list filter the selection was made in (``state`` empty: the default view)."""
    state: str | None = Field(default=None, pattern=QUEUE_STATE_PATTERN)


class DriveQueueBulkCancelInput(BaseModel):
    """Exactly one of ``ids`` (selected rows) or ``all_filter`` (every row of that list filter)."""
    ids: list[str] | None = Field(default=None, max_length=upload_queue.BULK_MAX_ITEMS)
    all_filter: DriveQueueFilter | None = None


class DriveQueuePurgeInput(BaseModel):
    """Exactly one of ``ids`` or ``states`` (every finished row in those states)."""
    ids: list[str] | None = Field(default=None, max_length=upload_queue.BULK_MAX_ITEMS)
    states: list[str] | None = Field(default=None, max_length=4)


class DriveQueueSkipped(BaseModel):
    id: str
    code: str
    state: str | None = None


class DriveQueueBulkResult(BaseModel):
    affected: list[str]
    skipped: list[DriveQueueSkipped]
    staging_removed: int = 0
    queue: DriveQueueList


class DriveBatchError(BaseModel):
    id: str
    target: str
    state: str
    code: str | None = None
    message: str | None = None


class DriveUploadBatch(BaseModel):
    batch_id: str
    state: str
    finished: bool
    paused: bool
    counts: dict[str, int]
    items_total: int
    files_total: int
    files_done: int
    bytes_total: int
    bytes_done: int
    current: str | None = None
    current_kind: str | None = None
    next_retry_at: str | None = None
    errors: list[DriveBatchError] = Field(default_factory=list)
    transfer_methods: dict[str, int] = Field(default_factory=dict)
    origin: str | None = None
    origin_ref: str | None = None
    requested_by: str | None = None
    project_id: str | None = None
    request_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DriveSourceVersionInfo(BaseModel):
    size: int | None = None
    modified_at: str | None = None
    token_kind: str | None = None
    registered_at: str | None = None


class DriveSourceChangeItem(BaseModel):
    id: str
    relative_path: str
    kind: str                       # CHANGED | MISSING
    review_state: str               # PENDING | IGNORED | NONE (MISSING without decision)
    version_no: int
    registered: DriveSourceVersionInfo
    drive: DriveSourceVersionInfo | None = None
    detected_at: str | None = None


class DriveSourceChanges(BaseModel):
    project_id: str
    request_id: str
    items: list[DriveSourceChangeItem]
    pending_changes: int = 0
    missing: int = 0
    ignored: int = 0


class DriveSourceChangeDecision(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    ids: list[str] | None = Field(default=None, max_length=5000)


class DriveSourceChangeResult(BaseModel):
    accepted: int = 0
    removed: int = 0
    dismissed: int = 0
    pending_changes: int = 0
    missing: int = 0
    ignored: int = 0


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


def _scx_snapshot() -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
    """Credentials metadata and health without building the gateway (see module docstring)."""
    store = drive_gateway.token_store()
    gateway = drive_gateway.existing_gateway()
    credentials = store.metadata()
    health = _health(gateway) if gateway is not None else {"state": "UNAVAILABLE"}
    return credentials, health, store, gateway


def _connection_test() -> DriveTestResult:
    started = time.perf_counter()
    try:
        gateway = drive_gateway.get_drive_gateway()
        gateway.stat("", priority=drive_gateway.priority("INTERACTIVE"))
        identity = gateway.root_identity()
    except Exception as error:
        return DriveTestResult(ok=False, latency_ms=round((time.perf_counter() - started) * 1000, 1), error=_error_info(error))
    return DriveTestResult(ok=True, latency_ms=round((time.perf_counter() - started) * 1000, 1), root_identity=str(identity))


@admin_router.get("/status", response_model=DriveAdminStatus)
def admin_status(request: Request) -> DriveAdminStatus:
    _require_admin(request)
    try:
        settings = _settings()
        if not settings.enabled:
            return DriveAdminStatus(mode="none")
        return _admin_status(settings)
    except Exception as error:
        logger.warning("SCX drive admin status unavailable (%s)", type(error).__name__)
        return DriveAdminStatus(mode="scx", state="UNAVAILABLE")


def _admin_status(settings: DriveSettings) -> DriveAdminStatus:
    credentials, health, store, gateway = _scx_snapshot()
    problem = store.last_load_problem if credentials.get("present") else "MISSING"
    if credentials.get("key_matches") is False:
        problem = "KEY_CHANGED"
    version = getattr(gateway, "worker_version", None)
    saved_failed = store.last_save_error_at
    return DriveAdminStatus(
        mode="scx", state=_derived_state(credentials, health), server_url=settings.server_url,
        drive_root=settings.spdm_root, drive_root_locked=settings.spdm_root is not None,
        credentials=DriveCredentialsMeta(**credentials), credentials_problem=problem, health=DriveHealth(**health),
        worker_version=str(version) if isinstance(version, str) else None,
        token_save_failed_at=saved_failed.isoformat() if saved_failed else None,
        writes_enabled=settings.writes_enabled, writes_available=drive_writes.writes_available(),
        version_tokens=_token_observation(),
    )


def _token_observation() -> DriveTokenObservation:
    observed = drive_reads.token_observation()
    return DriveTokenObservation(last_kind=observed.get("last_kind"), sha1_files=int(observed.get("sha1") or 0),
                                 size_mtime_files=int(observed.get("size_mtime") or 0), last_at=observed.get("last_at"))


def _parse_bundle(raw: bytes, settings: DriveSettings) -> dict[str, Any]:
    invalid = lambda message: _fail(400, "DRIVE_CREDENTIALS_INVALID", message)  # noqa: E731
    if len(raw) > MAX_CREDENTIALS_BODY:
        raise invalid("토큰 JSON이 너무 큽니다.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
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


def _account_change(store: Any, received: str | None, confirm: bool) -> tuple[str | None, bool]:
    """(connected account, changed); raises 409 DRIVE_ACCOUNT_CHANGE_CONFIRM before anything is saved."""
    connected = store.connected_account()
    changed = bool(connected) and not same_account(received, connected)
    if changed and not confirm:
        raise drive_auth.DriveAuthError(
            409, "DRIVE_ACCOUNT_CHANGE_CONFIRM",
            f"연결 계정이 {connected}에서 {received or '(계정 이름 없음)'}(으)로 바뀝니다. 계속하려면 변경을 확인하세요.",
            current=connected, received=received)
    return connected, changed


def _auth_fail(error: drive_auth.DriveAuthError) -> HTTPException:
    return HTTPException(error.status, {"code": error.code, "message": error.message, **error.extra})


def _after_reauth() -> int:
    """Queue paused by AUTH_REQUIRED continues; the admins' queue-paused notice is resolved."""
    with connect() as conn:
        resumed = upload_queue.resume_blocked(conn)
        notifications.resolve_drive_queue_paused(conn)
    if resumed:
        logger.info("SCX drive upload queue resumed after re-authentication (%d item(s))", resumed)
    return resumed


def _register(request: Request, raw: bytes, confirm: bool = False) -> DriveCredentialsSaved:
    settings = _require_scx()
    fields = _parse_bundle(raw, settings)
    store = drive_gateway.token_store()
    bundle = store.make_bundle(**fields)
    try:
        _connected, changed = _account_change(store, bundle.account_hint, confirm)
    except drive_auth.DriveAuthError as error:
        # Nothing saved; the worker keeps the current tokens (no gateway reset).
        write_audit_event(request=request, principal=request.state.principal, status_code=error.status,
                          action="DRIVE_CREDENTIALS_REJECTED",
                          detail={"account_hint": bundle.account_hint, "result": error.code})
        raise _auth_fail(error) from None
    actor = request.state.principal.user_id
    store.write(bundle, updated_by=actor, remember_account=True)
    drive_gateway.reset_gateway()   # the worker must drop the previous account's in-memory tokens
    drive_auth.record_registered()
    _after_reauth()   # 05 §5.2: a queue paused by AUTH_REQUIRED continues
    obtained = bundle.obtained_at.isoformat(timespec="microseconds")
    write_audit_event(request=request, principal=request.state.principal, status_code=200,
                      action="DRIVE_CREDENTIALS_REGISTERED",
                      detail={"account_hint": bundle.account_hint, "obtained_at": obtained,
                              "server_host": settings.server_host, "key_id": store.key_id,
                              "account_changed": changed})
    test = _connection_test()
    return DriveCredentialsSaved(account_hint=bundle.account_hint, obtained_at=obtained, test=test)


@admin_router.put("/credentials", response_model=DriveCredentialsSaved, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": TOKEN_BUNDLE_SCHEMA}}}})
async def put_credentials(request: Request,
                          confirm_account_change: bool = Query(default=False)) -> DriveCredentialsSaved:
    _require_admin(request)
    await run_in_threadpool(_require_scx)
    raw = await _read_capped_body(request)
    return await run_in_threadpool(_register, request, raw, confirm_account_change)


async def _read_capped_body(request: Request, *, limit: int = MAX_CREDENTIALS_BODY,
                            code: str = "DRIVE_CREDENTIALS_INVALID") -> bytes:
    """Reject by Content-Length before reading, then read at most the cap (chunked bodies too)."""
    too_large = _fail(400, code, "토큰 JSON이 너무 큽니다." if code == "DRIVE_CREDENTIALS_INVALID" else "요청 본문이 너무 큽니다.")
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise _fail(400, code, "Content-Length가 올바르지 않습니다.") from None
        if length > limit:
            raise too_large
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


@admin_router.delete("/credentials", status_code=204, response_class=Response)
def delete_credentials(request: Request) -> Response:
    _require_admin(request)
    _require_scx()
    store = drive_gateway.token_store()
    meta = store.metadata()
    existed = store.clear()
    drive_gateway.reset_gateway()   # the worker must not keep using the deleted tokens from memory
    write_audit_event(request=request, principal=request.state.principal, status_code=204,
                      action="DRIVE_CREDENTIALS_DELETED",
                      detail={"existed": existed, "account_hint": meta.get("account_hint")})
    return Response(status_code=204)


@admin_router.post("/test", response_model=DriveTestResult)
def connection_test(request: Request) -> DriveTestResult:
    _require_admin(request)
    _require_scx()
    return _connection_test()


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
                      detail={"test_folder": folder, "ok": report["ok"], "failed_steps": failed})
    return DriveCheckReport(**report)


@status_router.get("/status", response_model=DriveUserStatus)
def user_status(request: Request) -> DriveUserStatus:
    """Any signed-in user (SecurityMiddleware); ``can_reconnect`` marks global admins (banner action)."""
    try:
        settings = _settings()
        if not settings.enabled:
            return DriveUserStatus(mode="none")
        credentials, health, _store, _gateway = _scx_snapshot()
        with connect() as conn:
            paused = upload_queue.queue_paused(conn)
        principal = getattr(request.state, "principal", None)
        return DriveUserStatus(mode="scx", state=_derived_state(credentials, health),
                               writes_enabled=settings.writes_enabled,
                               writes_available=drive_writes.writes_available(), queue_paused=paused,
                               can_reconnect=bool(principal is not None and principal.is_global_admin))
    except Exception as error:
        logger.warning("SCX drive status unavailable (%s)", type(error).__name__)
        return DriveUserStatus(mode="scx", state="UNAVAILABLE", writes_available=False)


# --- pending drive source changes (stage D2, 05 §4) -------------------------------------------

def _source_root_key() -> str:
    settings = _require_scx()
    if not settings.spdm_root:
        raise _fail(409, "DRIVE_ROOT_NOT_SET", "SIMDASH_SCX_DRIVE_ROOT(SPDM 루트)가 설정되지 않았습니다.")
    return drive_root_from_settings(settings).root_key()


def _source_error(error: drive_sources.DriveSourceError) -> HTTPException:
    return _fail(error.status_code, error.code, str(error))


@status_router.get("/source-changes", response_model=DriveSourceChanges)
def source_changes(request: Request, project_id: str = Query(min_length=1, max_length=128),
                   request_id: str = Query(min_length=1, max_length=128)) -> DriveSourceChanges:
    """Changed or missing drive sources of one request, registered version vs. drive (no drive call)."""
    root_key = _source_root_key()
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        try:
            return DriveSourceChanges(**drive_sources.list_changes(conn, root_key, project_id, request_id))
        except drive_sources.DriveSourceError as error:
            raise _source_error(error) from error


def _decide(request: Request, payload: DriveSourceChangeDecision, action: str) -> DriveSourceChangeResult:
    root_key = _source_root_key()
    actor = request.state.principal.user_id
    ids, verified = payload.ids, None
    if action == "accept":
        # 05 §4: download each pending version once (no DB connection held) and record its sha256;
        # only the rows checked here are accepted (a change that appears meanwhile waits).
        with connect() as conn:
            require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
            try:
                found = drive_sources.accept_candidates(conn, root_key, payload.project_id, payload.request_id, payload.ids)
            except drive_sources.DriveSourceError as error:
                raise _source_error(error) from error
        ids = [str(row["id"]) for row in found]
        try:
            verified = drive_sources.verify_pending(found)
        except drive_sources.DriveSourceError as error:
            raise _source_error(error) from error
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            if action == "accept":
                result = drive_sources.accept(conn, root_key, payload.project_id, payload.request_id, ids, actor,
                                              verified=verified)
            else:
                result = drive_sources.dismiss(conn, root_key, payload.project_id, payload.request_id, ids, actor)
        except drive_sources.DriveSourceError as error:
            raise _source_error(error) from error
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="DRIVE_SOURCE_CHANGES_ACCEPTED" if action == "accept" else "DRIVE_SOURCE_CHANGES_DISMISSED",
                          detail={"project_id": payload.project_id, "request_id": payload.request_id,
                                  "ids": payload.ids, **{key: value for key, value in result.items()
                                                         if key in {"accepted", "removed", "dismissed"}}},
                          connection=conn)
    # the next sync of this request applies the accepted versions (downloads only those files)
    folder_auto_sync.invalidate_request(root_key, payload.project_id, payload.request_id)
    return DriveSourceChangeResult(**result)


@status_router.post("/source-changes/accept", response_model=DriveSourceChangeResult)
def accept_source_changes(payload: DriveSourceChangeDecision, request: Request) -> DriveSourceChangeResult:
    """[새 버전 등록]: register the drive versions (all of the request, or ``ids``); old versions are kept."""
    return _decide(request, payload, "accept")


@status_router.post("/source-changes/dismiss", response_model=DriveSourceChangeResult)
def dismiss_source_changes(payload: DriveSourceChangeDecision, request: Request) -> DriveSourceChangeResult:
    """[무시]: keep the registered versions; the same drive versions are not asked again."""
    return _decide(request, payload, "dismiss")


# --- drive upload queue (stage D3, 05 §5) ------------------------------------------------------

@status_router.get("/upload-batches/{batch_id}", response_model=DriveUploadBatch)
def upload_batch(request: Request, batch_id: str) -> DriveUploadBatch:
    """Progress of one queued drive write batch (result drop publish, folder, Final, summary file)."""
    _require_scx()
    with connect() as conn:
        summary = upload_queue.batch_summary(conn, batch_id)
        if summary is None or not summary.get("request_id"):
            raise _fail(404, "DRIVE_BATCH_NOT_FOUND", "드라이브 업로드 묶음을 찾을 수 없습니다.")
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", str(summary["request_id"]), conn=conn)
    return DriveUploadBatch(**summary)


@admin_router.get("/queue", response_model=DriveQueueList)
def queue_list(request: Request, state: str | None = Query(default=None, pattern="^(PENDING|RUNNING|DONE|CONFLICT|FAILED|BLOCKED|CANCELLED)$"),
               limit: int = Query(default=200, ge=1, le=1000)) -> DriveQueueList:
    _require_admin(request)
    settings = _require_scx()
    with connect() as conn:
        listed = upload_queue.admin_list(conn, state=state, limit=limit)
    return DriveQueueList(**listed, writes_enabled=settings.writes_enabled)


def _queue_action(request: Request, item_id: str, action: str) -> DriveQueueItem:
    _require_admin(request)
    _require_scx()
    with connect() as conn:
        existing = upload_queue.get_item(conn, item_id)
        if existing is not None and action == "retry" and existing.origin == "finalization" and existing.origin_ref \
                and existing.state in {"FAILED", "CONFLICT", "BLOCKED", "CANCELLED", "PENDING"}:
            # A Final batch runs again only while it holds its request lock (review #6); the stopped Final
            # becomes PUBLISHING ("드라이브 반영 중") with the lock in this transaction (re-review #3).
            from ..services import case_finalization_drive
            from ..services.case_finalization import CaseFinalizationError

            try:
                case_finalization_drive.acquire_retry_lock(conn, existing.origin_ref)
            except CaseFinalizationError as error:
                raise _fail(409, error.code, str(error)) from None
        try:
            item = (upload_queue.retry_item if action == "retry" else upload_queue.cancel_item)(conn, item_id)
        except LookupError:
            raise _fail(404, "DRIVE_QUEUE_ITEM_NOT_FOUND", "대기열 항목을 찾을 수 없습니다.") from None
        except ValueError as error:
            raise _fail(409, "DRIVE_QUEUE_ITEM_STATE", f"이 상태({error})의 항목은 {'다시 시도' if action == 'retry' else '취소'}할 수 없습니다.") from None
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="DRIVE_QUEUE_ITEM_RETRIED" if action == "retry" else "DRIVE_QUEUE_ITEM_CANCELLED",
                          detail={"item_id": item_id, "batch_id": item.batch_id, "origin": item.origin,
                                  "origin_ref": item.origin_ref, "target": item.target}, connection=conn)
    if action == "cancel":
        # After the commit (review #4): the batch's finish hook sees the cancelled item.
        upload_queue.request_finish(item.batch_id)
    upload_queue.wake()
    return DriveQueueItem(**item.view())


@admin_router.post("/queue/{item_id}/retry", response_model=DriveQueueItem)
def queue_retry(item_id: str, request: Request) -> DriveQueueItem:
    """Run a FAILED/CONFLICT/BLOCKED/CANCELLED item again (a CONFLICT is re-checked; nothing is overwritten)."""
    return _queue_action(request, item_id, "retry")


@admin_router.post("/queue/{item_id}/cancel", response_model=DriveQueueItem)
def queue_cancel(item_id: str, request: Request) -> DriveQueueItem:
    """Mark a not-yet-done item CANCELLED. Drive content is never deleted; a cancelled Final stays incomplete."""
    return _queue_action(request, item_id, "cancel")


def _bulk_ids(conn, ids: list[str] | None, *, state: str | None = None, states: list[str] | None = None,
              use_filter: bool = False) -> list[str]:
    if use_filter:
        return upload_queue.filter_ids(conn, state=state, states=states)
    return [str(value) for value in (ids or [])]


def _queue_view(settings: DriveSettings) -> DriveQueueList:
    with connect() as conn:
        listed = upload_queue.admin_list(conn)
    return DriveQueueList(**listed, writes_enabled=settings.writes_enabled)


@admin_router.post("/queue/bulk-cancel", response_model=DriveQueueBulkResult)
def queue_bulk_cancel(payload: DriveQueueBulkCancelInput, request: Request) -> DriveQueueBulkResult:
    """Cancel the selected (or all filtered) open items, as the single cancel does. Drive files are never deleted."""
    _require_admin(request)
    settings = _require_scx()
    if (payload.ids is None) == (payload.all_filter is None):
        raise _fail(422, "DRIVE_QUEUE_SELECTION_INVALID", "취소할 항목(ids) 또는 목록 조건(all_filter) 중 하나만 보내세요.")
    with connect() as conn:
        ids = _bulk_ids(conn, payload.ids, state=payload.all_filter.state if payload.all_filter else None,
                        states=list(upload_queue.CANCELLABLE_STATES), use_filter=payload.all_filter is not None)
        result = upload_queue.bulk_cancel(conn, ids)
        cancelled = result["cancelled"]
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="DRIVE_QUEUE_BULK_CANCELLED",
                          detail={"count": len(cancelled), "skipped": len(result["skipped"]),
                                  "all_filter": payload.all_filter.model_dump() if payload.all_filter else None,
                                  "item_ids": [item.id for item in cancelled][:200],
                                  "batches": result["batches"][:200]}, connection=conn)
    for batch_id in result["batches"]:
        # After the commit (review #4): each batch's finish hook (Final lock release) sees the cancelled items.
        upload_queue.request_finish(batch_id)
    upload_queue.wake()
    return DriveQueueBulkResult(affected=[item.id for item in cancelled],
                                skipped=[DriveQueueSkipped(**entry) for entry in result["skipped"]],
                                queue=_queue_view(settings))


@admin_router.post("/queue/purge", response_model=DriveQueueBulkResult)
def queue_purge(payload: DriveQueuePurgeInput, request: Request) -> DriveQueueBulkResult:
    """Remove finished rows (DONE/CANCELLED/FAILED/CONFLICT) and their server staging. Drive files are never deleted."""
    _require_admin(request)
    settings = _require_scx()
    if (payload.ids is None) == (payload.states is None):
        raise _fail(422, "DRIVE_QUEUE_SELECTION_INVALID", "지울 항목(ids) 또는 상태(states) 중 하나만 보내세요.")
    states = [state for state in (payload.states or []) if state in upload_queue.TERMINAL_STATES]
    if payload.states is not None and len(states) != len(payload.states):
        raise _fail(422, "DRIVE_QUEUE_PURGE_STATE", "완료·취소·실패·충돌 상태만 목록에서 지울 수 있습니다.")
    with connect() as conn:
        # ``states``: every row in those states, not only the default view's last day
        ids = upload_queue.ids_in_states(conn, states) if payload.states is not None else list(payload.ids or [])
        result = upload_queue.purge(conn, ids)
        purged = result["purged"]
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="DRIVE_QUEUE_PURGED",
                          detail={"count": len(purged), "skipped": len(result["skipped"]), "states": states or None,
                                  "item_ids": [item.id for item in purged][:200],
                                  "batches": sorted({item.batch_id for item in purged})[:200]}, connection=conn)
    removed = upload_queue.remove_purged_staging(result["cleanup"])
    return DriveQueueBulkResult(affected=[item.id for item in purged],
                                skipped=[DriveQueueSkipped(**entry) for entry in result["skipped"]],
                                staging_removed=removed, queue=_queue_view(settings))


@admin_router.post("/queue/resume", response_model=DriveQueueList)
def queue_resume(request: Request) -> DriveQueueList:
    """Continue items paused by AUTH_REQUIRED (credential registration does this automatically)."""
    _require_admin(request)
    settings = _require_scx()
    resumed = upload_queue.resume_blocked()
    write_audit_event(request=request, principal=request.state.principal, status_code=200,
                      action="DRIVE_QUEUE_RESUMED", detail={"items": resumed})
    with connect() as conn:
        listed = upload_queue.admin_list(conn)
    return DriveQueueList(**listed, writes_enabled=settings.writes_enabled)


# --- re-login from the dashboard (issue #50, docs/plans/scx-drive-relogin-spec.md §3) ----------------

MAX_LOGIN_BODY = 4 * 1024
LOGIN_SCHEMA = {
    "type": "object",
    "required": ["username", "password"],
    "properties": {
        "username": {"type": "string", "maxLength": 128},
        "password": {"type": "string", "maxLength": 256, "format": "password", "writeOnly": True},
        "confirm_account_change": {"type": "boolean", "default": False},
    },
}
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
# Failures raised before the worker reached the HPC SSO: they do not use up a login attempt.
NOT_AN_SSO_ATTEMPT = frozenset({"DRIVE_ADAPTER_UPDATE_REQUIRED", "DRIVE_MODE_DISABLED", "DRIVE_UNAVAILABLE"})
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded")


def password_login_block(request: Request, settings: DriveSettings) -> str | None:
    """``OFF`` / ``HTTP`` when account login is refused for this request, else ``None``.

    The scheme is ``request.url.scheme``: uvicorn (``--proxy-headers``, default on) already
    replaced it with ``X-Forwarded-Proto`` when — and only when — the peer is a trusted proxy
    (``--forwarded-allow-ips``, default 127.0.0.1 = the Caddy reverse proxy on the same server).
    The dashboard never reads ``X-Forwarded-Proto`` itself, so a client cannot claim HTTPS.
    Same-PC access (127.0.0.1/::1 without proxy headers) is allowed in ``https_only``.
    """
    mode = settings.password_login
    if mode == "off":
        return "OFF"
    if mode == "on" or request.url.scheme == "https":
        return None
    host = request.client.host if request.client else ""
    if host in LOOPBACK_HOSTS and not any(name in request.headers for name in PROXY_HEADERS):
        return None
    return "HTTP"


def _blocked_fail(reason: str) -> HTTPException:
    message = ("계정 로그인이 꺼져 있습니다(SIMDASH_DRIVE_PASSWORD_LOGIN=off). 토큰 붙여넣기를 사용하세요."
               if reason == "OFF" else
               "HTTPS 접속에서만 계정 로그인을 사용할 수 있습니다(비밀번호 평문 전송 방지). HTTPS 주소로 접속하거나 토큰 붙여넣기를 사용하세요.")
    return HTTPException(403, {"code": "DRIVE_PASSWORD_LOGIN_DISABLED", "message": message, "reason": reason})


def _stamp(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


@admin_router.get("/auth", response_model=DriveAuthStatus)
def auth_status(request: Request) -> DriveAuthStatus:
    _require_admin(request)
    settings = _require_scx()
    store = drive_gateway.token_store()
    credentials = store.metadata()
    connected = store.connected_account()
    caps, supported, caps_error = drive_auth.capabilities()
    health = _health(drive_gateway.existing_gateway()) if drive_gateway.existing_gateway() is not None else {}
    memory = drive_auth.snapshot()
    left, locked_until = drive_auth.limiter.state(settings.login_max_attempts)
    blocked = password_login_block(request, settings)
    return DriveAuthStatus(
        capabilities=DriveAuthCapabilities(**caps), adapter_supported=supported, capabilities_error=caps_error,
        connected_account=connected, account_hint=credentials.get("account_hint"),
        credentials_present=bool(credentials.get("present")), obtained_at=credentials.get("obtained_at"),
        expires_at=memory["expires_at"], last_refresh_at=memory["last_refresh_at"] or health.get("token_refreshed_at"),
        last_error_code=memory["last_error_code"] or health.get("last_error_code"),
        last_error_at=memory["last_error_at"] or health.get("last_error_at"),
        password_login=settings.password_login, password_login_allowed=blocked is None and supported and caps["password"],
        password_login_blocked=blocked, attempts_left=left, max_attempts=settings.login_max_attempts,
        locked_until=_stamp(locked_until),
    )


def _apply_relogin(request: Request, result: drive_auth.AuthResult, *, confirm: bool, gateway_store: Any) -> DriveReloginResult:
    """Account check → encrypted save as a new generation → worker keeps its session → queue resumes."""
    store = drive_gateway.token_store()
    bundle = result.bundle
    try:
        _connected, changed = _account_change(store, bundle.account_hint, confirm)
    except drive_auth.DriveAuthError:
        # Nothing saved.  The worker now holds the other account's tokens: drop that session so the
        # next call loads the stored (connected) account again; late saves of it are fenced off.
        if gateway_store is not None:
            gateway_store.detach()
        drive_gateway.reset_gateway()
        raise
    generation = store.write(bundle, updated_by=request.state.principal.user_id, remember_account=True)
    if gateway_store is not None and drive_gateway.gateway_token_store() is gateway_store:
        gateway_store.adopt_generation(generation)   # the worker already uses these tokens: no reset
    drive_auth.record_success(result)
    resumed = _after_reauth()
    return DriveReloginResult(account_hint=bundle.account_hint,
                              obtained_at=bundle.obtained_at.isoformat(timespec="microseconds"),
                              account_changed=changed, queue_resumed=resumed)


def _relogin_audit(request: Request, action: str, status: int, *, result: str, received: str | None,
                   username: str | None = None) -> None:
    detail: dict[str, Any] = {"result": result, "received_account": received}
    if username is not None:
        detail["username"] = username
    try:
        write_audit_event(request=request, principal=request.state.principal, status_code=status, action=action,
                          detail=detail)
    except Exception:  # noqa: BLE001 - the audit failure must not hide the result
        logger.error("SCX drive %s audit could not be written", action)


def _reconnect(request: Request, confirm: bool) -> DriveReloginResult:
    _require_scx()
    store = drive_gateway.token_store()
    if not store.metadata().get("present"):
        _relogin_audit(request, "DRIVE_RECONNECT_FAILED", 409, result="DRIVE_CREDENTIALS_MISSING", received=None)
        raise _fail(409, "DRIVE_CREDENTIALS_MISSING", "저장된 토큰이 없습니다. 계정으로 로그인하거나 토큰을 붙여 넣으세요.")
    caps, supported, _error = drive_auth.capabilities()
    if supported and not caps.get("refresh"):
        _relogin_audit(request, "DRIVE_RECONNECT_FAILED", 409, result="AUTH_METHOD_UNSUPPORTED", received=None)
        raise _fail(409, "AUTH_METHOD_UNSUPPORTED", "이 어댑터는 저장된 토큰으로 다시 연결하는 기능을 지원하지 않습니다. 계정으로 로그인하세요.")
    if not drive_auth.operation_lock.acquire(blocking=False):
        raise _fail(409, "DRIVE_AUTH_BUSY", "다른 재연결이 진행 중입니다. 잠시 후 다시 시도하세요.")
    received: str | None = None
    try:
        drive_gateway.get_drive_gateway()
        gateway_store = drive_gateway.gateway_token_store()
        result = drive_auth.refresh_now()
        received = result.bundle.account_hint
        outcome = _apply_relogin(request, result, confirm=confirm, gateway_store=gateway_store)
    except drive_auth.DriveAuthError as error:
        drive_auth.record_failure(error.code)
        _relogin_audit(request, "DRIVE_RECONNECT_FAILED", error.status, result=error.code, received=received)
        raise _auth_fail(error) from None
    except drive_gateway.DriveStartupError as error:
        _relogin_audit(request, "DRIVE_RECONNECT_FAILED", 503, result="DRIVE_UNAVAILABLE", received=None)
        raise _fail(503, "DRIVE_UNAVAILABLE", str(error)[:200]) from None
    finally:
        drive_auth.operation_lock.release()
    _relogin_audit(request, "DRIVE_RECONNECT_SUCCEEDED", 200, result="OK", received=received)
    return outcome


@admin_router.post("/reconnect", response_model=DriveReloginResult)
def reconnect(request: Request, payload: DriveReconnectRequest | None = None) -> DriveReloginResult:
    """[저장된 토큰으로 다시 연결]: ``refresh_now`` with the stored refresh token; no input."""
    _require_admin(request)
    return _reconnect(request, bool(payload and payload.confirm_account_change))


def _login_fields(raw: bytes) -> tuple[str, str, bool]:
    """Validate the login body without ever echoing it (no pydantic 422 ``input``)."""
    invalid = _fail(400, "DRIVE_LOGIN_INVALID", "아이디(128자 이하)와 비밀번호(256자 이하)를 입력하세요.")
    if len(raw) > MAX_LOGIN_BODY:
        raise invalid
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise invalid from None
    if not isinstance(payload, dict):
        raise invalid
    username, password, confirm = payload.get("username"), payload.get("password"), payload.get("confirm_account_change", False)
    payload.clear()
    if not isinstance(username, str) or not isinstance(password, str) or not isinstance(confirm, bool):
        raise invalid
    username = username.strip()
    if not username or len(username) > 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in username):
        raise invalid
    if not password or len(password) > 256:
        raise invalid
    return username, password, confirm


def _login(request: Request, raw: bytes) -> DriveReloginResult:
    settings = _require_scx()
    username, password, confirm = _login_fields(raw)
    del raw
    received: str | None = None
    try:
        caps, supported, _error = drive_auth.capabilities()
        if not supported:
            raise drive_auth.DriveAuthError(409, "DRIVE_ADAPTER_UPDATE_REQUIRED",
                                            "설치된 드라이브 어댑터가 계정 로그인을 지원하지 않습니다. 어댑터 업데이트 필요 (이슈 #50).")
        if not caps.get("password"):
            raise drive_auth.DriveAuthError(409, "AUTH_METHOD_UNSUPPORTED", drive_auth.AUTH_ERRORS["AUTH_METHOD_UNSUPPORTED"][1])
        if not drive_auth.operation_lock.acquire(blocking=False):
            raise drive_auth.DriveAuthError(409, "DRIVE_AUTH_BUSY", "다른 재연결이 진행 중입니다. 잠시 후 다시 시도하세요.")
        try:
            attempt = drive_auth.limiter.take(settings.login_max_attempts)
            try:
                drive_gateway.get_drive_gateway()
                gateway_store = drive_gateway.gateway_token_store()
                result = drive_auth.login_password(username, password)
            except drive_auth.DriveAuthError as error:
                if error.code in NOT_AN_SSO_ATTEMPT:
                    drive_auth.limiter.refund(attempt)   # the SSO never saw this attempt
                raise
            except drive_gateway.DriveStartupError:
                drive_auth.limiter.refund(attempt)
                raise
            finally:
                del password   # the only reference left; nothing else ever held it
            # The SSO accepted ID and password: no lockout risk any more, whatever happens next.
            drive_auth.limiter.clear()
            received = result.bundle.account_hint
            outcome = _apply_relogin(request, result, confirm=confirm, gateway_store=gateway_store)
        finally:
            drive_auth.operation_lock.release()
    except drive_auth.DriveAuthError as error:
        if error.code not in {"DRIVE_AUTH_BUSY", "DRIVE_LOGIN_LOCKED"}:
            drive_auth.record_failure(error.code)
        _relogin_audit(request, "DRIVE_LOGIN_FAILED", error.status, result=error.code, received=received, username=username)
        raise _auth_fail(error) from None
    except drive_gateway.DriveStartupError as error:
        _relogin_audit(request, "DRIVE_LOGIN_FAILED", 503, result="DRIVE_UNAVAILABLE", received=None, username=username)
        raise _fail(503, "DRIVE_UNAVAILABLE", str(error)[:200]) from None
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - type only: nothing from this request may reach a log
        logger.error("SCX drive account login failed unexpectedly (%s)", type(error).__name__)
        _relogin_audit(request, "DRIVE_LOGIN_FAILED", 500, result="DRIVE_INTERNAL", received=None, username=username)
        raise _fail(500, "DRIVE_INTERNAL", "계정 로그인 처리 중 오류가 발생했습니다.") from None
    _relogin_audit(request, "DRIVE_LOGIN_SUCCEEDED", 200, result="OK", received=received, username=username)
    return outcome


@admin_router.post("/login", response_model=DriveReloginResult, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": LOGIN_SCHEMA}}}})
async def login(request: Request) -> DriveReloginResult:
    """[계정으로 로그인]: the adapter gets new tokens from the HPC SSO with ID and password.

    The body is never stored, logged, audited or echoed (spec §3.3); HTTP is refused in ``https_only``."""
    _require_admin(request)
    settings = await run_in_threadpool(_require_scx)
    blocked = password_login_block(request, settings)
    if blocked is not None:
        await run_in_threadpool(_relogin_audit, request, "DRIVE_LOGIN_FAILED", 403,
                                result=f"DRIVE_PASSWORD_LOGIN_DISABLED:{blocked}", received=None)
        raise _blocked_fail(blocked)
    try:
        raw = await _read_capped_body(request, limit=MAX_LOGIN_BODY, code="DRIVE_LOGIN_INVALID")
    except HTTPException:
        raise _fail(400, "DRIVE_LOGIN_INVALID", "아이디(128자 이하)와 비밀번호(256자 이하)를 입력하세요.") from None
    return await run_in_threadpool(_login, request, raw)
