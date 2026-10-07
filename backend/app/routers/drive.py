"""SCX drive administration and status API (integration 02 §2/§4, plan D0/D7).

* ``/api/admin/drive/*`` — global administrators only.
* ``/api/drive/status`` — any signed-in user; state string and write availability (banner, read-only notice).
* ``/api/drive/source-changes`` — drive files of a request whose change or removal waits for
  confirmation (stage D2, 05 §4): list (request viewers), accept/dismiss (result import permission).
* ``/api/drive/upload-batches/{batch_id}`` — progress of one drive upload batch (request viewers, D3).
* ``/api/admin/drive/queue`` — the drive upload queue (D3): list, retry, cancel, resume after
  re-authentication.  Cancelling only marks the row; nothing on the drive is deleted.

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
from ..services.drive import gateway as drive_gateway
from ..services.drive import reads as drive_reads
from ..services.drive import sources as drive_sources
from ..services.drive import upload_queue
from ..services.drive import writes as drive_writes
from ..services.drive.check import CheckAlreadyRunning, run_drive_check
from ..services.drive.config import DriveSettings, url_host, validate_drive_rel_path
from ..services.drive.token_store import parse_obtained_at
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


def _register(request: Request, raw: bytes) -> DriveCredentialsSaved:
    settings = _require_scx()
    fields = _parse_bundle(raw, settings)
    store = drive_gateway.token_store()
    bundle = store.make_bundle(**fields)
    actor = request.state.principal.user_id
    store.write(bundle, updated_by=actor)
    drive_gateway.reset_gateway()   # the worker must drop the previous account's in-memory tokens
    resumed = upload_queue.resume_blocked()   # 05 §5.2: a queue paused by AUTH_REQUIRED continues
    if resumed:
        logger.info("SCX drive upload queue resumed after credential registration (%d item(s))", resumed)
    obtained = bundle.obtained_at.isoformat(timespec="microseconds")
    write_audit_event(request=request, principal=request.state.principal, status_code=200,
                      action="DRIVE_CREDENTIALS_REGISTERED",
                      detail={"account_hint": bundle.account_hint, "obtained_at": obtained,
                              "server_host": settings.server_host, "key_id": store.key_id})
    test = _connection_test()
    return DriveCredentialsSaved(account_hint=bundle.account_hint, obtained_at=obtained, test=test)


@admin_router.put("/credentials", response_model=DriveCredentialsSaved, openapi_extra={
    "requestBody": {"required": True, "content": {"application/json": {"schema": TOKEN_BUNDLE_SCHEMA}}}})
async def put_credentials(request: Request) -> DriveCredentialsSaved:
    _require_admin(request)
    await run_in_threadpool(_require_scx)
    raw = await _read_capped_body(request)
    return await run_in_threadpool(_register, request, raw)


async def _read_capped_body(request: Request) -> bytes:
    """Reject by Content-Length before reading, then read at most the cap (chunked bodies too)."""
    too_large = _fail(400, "DRIVE_CREDENTIALS_INVALID", "토큰 JSON이 너무 큽니다.")
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError:
            raise _fail(400, "DRIVE_CREDENTIALS_INVALID", "Content-Length가 올바르지 않습니다.") from None
        if length > MAX_CREDENTIALS_BODY:
            raise too_large
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_CREDENTIALS_BODY:
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
    del request  # any signed-in user (SecurityMiddleware)
    try:
        settings = _settings()
        if not settings.enabled:
            return DriveUserStatus(mode="none")
        credentials, health, _store, _gateway = _scx_snapshot()
        with connect() as conn:
            paused = upload_queue.queue_paused(conn)
        return DriveUserStatus(mode="scx", state=_derived_state(credentials, health),
                               writes_enabled=settings.writes_enabled,
                               writes_available=drive_writes.writes_available(), queue_paused=paused)
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
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            decide = drive_sources.accept if action == "accept" else drive_sources.dismiss
            result = decide(conn, root_key, payload.project_id, payload.request_id, payload.ids, actor)
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
        try:
            item = (upload_queue.retry_item if action == "retry" else upload_queue.cancel_item)(conn, item_id)
        except LookupError:
            raise _fail(404, "DRIVE_QUEUE_ITEM_NOT_FOUND", "대기열 항목을 찾을 수 없습니다.") from None
        except ValueError as error:
            raise _fail(409, "DRIVE_QUEUE_ITEM_STATE", f"이 상태({error})의 항목은 {'다시 시도' if action == 'retry' else '취소'}할 수 없습니다.") from None
        if item.origin == "finalization" and item.origin_ref and action == "retry":
            # A stopped Final batch runs again from this item; the Final shows "드라이브 반영 중" again.
            conn.execute("UPDATE finalization_operations SET status='PUBLISHING', error_code=NULL, error_message=NULL "
                         "WHERE operation_id=? AND status='FAILED'", [item.origin_ref])
        write_audit_event(request=request, principal=request.state.principal, status_code=200,
                          action="DRIVE_QUEUE_ITEM_RETRIED" if action == "retry" else "DRIVE_QUEUE_ITEM_CANCELLED",
                          detail={"item_id": item_id, "batch_id": item.batch_id, "origin": item.origin,
                                  "origin_ref": item.origin_ref, "target": item.target}, connection=conn)
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
