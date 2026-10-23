"""SCX relay for PhysicsAI (docs/features/scx-relay.md, contract v1).

PhysicsAI shares the dashboard's SCX connection: the dashboard process is the only owner of
the adapter worker (``dashboard.lock``, rotating refresh token), so PhysicsAI talks to this
loopback-only internal HTTP API (``/internal/scx-relay/v1``, :mod:`app.routers.scx_relay`)
instead of starting its own worker.

* Off by default (``SIMDASH_SCX_RELAY_ENABLED``).  Every request is checked in this order:
  disabled → 404 ``RELAY_DISABLED``; not loopback or any proxy header → 403
  ``RELAY_FORBIDDEN``; ``X-SCX-Relay-Key`` ≠ secret file (``hmac.compare_digest``) → 401
  ``RELAY_UNAUTHORIZED``; not scx mode → 503 ``DRIVE_MODE_DISABLED``.
* Drive writes go only through the upload queue (:mod:`.upload_queue`, root kind "relay":
  folders relative to ``SIMDASH_SCX_RELAY_DRIVE_BASE``).  This module never calls a drive
  write method; reads are ``list_dir``/``stat``/``download_to``; jobs are
  ``submit_radioss``/``get_job``/``terminate_job``.  Nothing is deleted, moved or overwritten.
* The SPDM master root (``SIMDASH_SCX_DRIVE_ROOT``) is never read or written through the relay
  (no setting allows it); the queue checks it again for every relay item.
* A submission is never retried: ``scx_relay_submissions`` records it before the adapter call
  (``SUBMITTING``), the same ``request_id`` replays the record without calling the adapter, and a
  lost reply (``SUBMISSION_UNKNOWN`` or a code-less failure) stays ``UNKNOWN`` until a person
  resolves it.  ``SUBMITTING`` rows left by a stopped process become ``UNKNOWN`` at startup.
* No database connection is held during a drive or job call.  The secret is read once at
  startup and never logged, returned or written anywhere.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Callable, Mapping, TypeVar

from ...database_connection import connect, connection_held
from ..storage import server_local
from ..storage.provider import SpdmStorageError
from . import auth as drive_auth
from . import gateway as drive_gateway
from . import lanes as drive_lanes
from . import upload_queue
from . import writes as drive_writes
from .config import DriveConfigError, RelaySettings, check_relay_rel_path, drive_mode, relay_settings

logger = logging.getLogger("app.services.drive.relay")

T = TypeVar("T")

RELAY_VERSION = 1
PREFIX = "/internal/scx-relay/v1"
ORIGIN = upload_queue.RELAY_ORIGIN
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-forwarded-port",
                 "x-forwarded-prefix", "forwarded", "x-real-ip", "x-simdash-proxy-peer")
KEY_HEADER = "x-scx-relay-key"
CLIENT_HEADER = "x-scx-relay-client"
DEFAULT_CLIENT = "relay"
REQUEST_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")
CLIENT_NAME = re.compile(r"[a-z0-9_\-]{1,32}")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
STAGING_MIN_FREE_BYTES = 1024 ** 3
DOWNLOAD_PREFIX = "relay-dl-"
DOWNLOAD_ORPHAN_SECONDS = 3600.0
LIST_DEFAULT = 5000
LIST_MAX = 50_000
JSON_BODY_MAX = 1024 * 1024
MAX_ENGINE_PATHS = 64
MAX_ADDITIONAL_PATHS = 2000
JOB_API_METHODS = ("submit_radioss", "get_job", "terminate_job")
JOB_API_TYPES = ("RadiossJobRequest", "JobResources")
SUBMISSION_STATES = ("SUBMITTING", "SUBMITTED", "UNKNOWN", "FAILED", "ATTACHED", "ABANDONED")
TRACKED_STATES = ("SUBMITTED", "ATTACHED")
RECORD_RETRY_DELAYS = (0.5, 2.0, 5.0)
DEFAULT_RUN_TYPE = "both - use starter and engine files"
DEFAULT_MPI = "SMP (default)"


# --- errors (contract §7) ------------------------------------------------------------------------

class RelayError(Exception):
    """A relay answer other than success; :meth:`body` is the contract §7 error body."""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False,
                 retry_after: int | None = None, adapter_code: str | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = drive_gateway.mask_secrets(str(message))[:200]
        self.retryable = retryable
        self.retry_after = retry_after
        self.adapter_code = adapter_code

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable,
                "retry_after": self.retry_after, "adapter_code": self.adapter_code}


_AUTH = (503, "DRIVE_AUTH_REQUIRED", False, None,
         "드라이브 인증이 필요합니다. 대시보드 관리 화면에서 다시 연결하세요.")
_BUSY = (503, "DRIVE_BUSY", True, 5, "드라이브가 바쁩니다. 잠시 후 다시 시도하세요.")
# adapter code → (HTTP, relay code, retryable, retry_after, message)
ADAPTER_ERRORS: dict[str, tuple[int, str, bool, int | None, str]] = {
    "NOT_FOUND": (404, "NOT_FOUND", False, None, "드라이브 경로를 찾을 수 없습니다."),
    "FORBIDDEN": (403, "FORBIDDEN", False, None, "드라이브가 이 작업을 허용하지 않습니다."),
    "CONFLICT": (409, "CONFLICT", False, None, "드라이브에 같은 이름이 이미 있습니다."),
    "LIMIT": (413, "LIMIT", False, None, "드라이브 한도를 넘었습니다."),
    "LOCKED": (423, "LOCKED", True, None, "드라이브 항목이 잠겨 있습니다."),
    "BUSY": _BUSY,
    "OVERLOADED": _BUSY,
    "RATE_LIMITED": _BUSY,
    "TIMEOUT": (504, "DRIVE_TIMEOUT", True, None, "드라이브 응답 시간이 초과되었습니다."),
    "UNAVAILABLE": (503, "DRIVE_UNAVAILABLE", True, None, "드라이브에 연결할 수 없습니다."),
    "INTERNAL": (500, "DRIVE_INTERNAL", False, None, "드라이브 내부 오류입니다."),
    "INVALID_PATH": (400, "INVALID_PATH", False, None, "드라이브가 경로를 받지 않았습니다."),
    "SUBMISSION_UNKNOWN": (409, "SUBMISSION_UNKNOWN", False, None,
                           "SCX 제출 결과를 알 수 없습니다. SCX 화면에서 확인한 뒤 정리하세요."),
}
for _code in drive_gateway.AUTH_FAILURE_CODES | {"AUTH_DEVICE_EXPIRED", "AUTH_DEVICE_DENIED"}:
    ADAPTER_ERRORS[_code] = _AUTH


def _mapped(code: str | None, message: str | None = None, *, job: bool = False,
            job_lookup: bool = False) -> RelayError:
    """The relay error of an adapter code (drive codes reuse ``DRIVE_ERROR_MAP`` without ``SPDM_``)."""
    if code and job_lookup and code == "NOT_FOUND":
        return RelayError(404, "SCX_JOB_NOT_FOUND", message or "SCX가 이 잡을 모릅니다.", adapter_code=code)
    known = ADAPTER_ERRORS.get(code or "")
    if known is not None:
        status, relay_code, retryable, retry_after, text = known
        return RelayError(status, relay_code, message or text, retryable=retryable, retry_after=retry_after,
                          adapter_code=code)
    mapping = drive_gateway.DRIVE_ERROR_MAP.get(code or "")
    if mapping is not None and not job:
        return RelayError(mapping.http_status, mapping.storage_code.removeprefix("SPDM_"), message or code or "",
                          retryable=mapping.retryable, retry_after=mapping.retry_after, adapter_code=code)
    if job and code:
        return RelayError(502, "SCX_JOB_ERROR", message or "SCX 잡 오류입니다.", adapter_code=code)
    return RelayError(500, "DRIVE_INTERNAL", message or "드라이브 내부 오류입니다.", adapter_code=code)


def adapter_error(error: BaseException, *, job: bool = False, job_lookup: bool = False) -> RelayError:
    code = drive_gateway.drive_error_code(error)
    return _mapped(code, drive_gateway.safe_error_message(error), job=job, job_lookup=job_lookup)


def _invalid(message: str) -> RelayError:
    return RelayError(422, "INVALID_REQUEST", message)


# --- settings and lifecycle ----------------------------------------------------------------------

class _State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings: RelaySettings | None = None


_state = _State()
# One submission / one queue batch per request_id: check and insert under these (single process).
_submit_lock = threading.Lock()
_upload_lock = threading.Lock()


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _inside(path: str, root: str) -> bool:
    """``path`` is ``root`` or below it (NFC, case-insensitive)."""
    p, r = _fold(path), _fold(root.strip("/"))
    return bool(r) and (p == r or p.startswith(r + "/"))


def startup() -> RelaySettings:
    """Validate the relay settings (FastAPI lifespan, after the drive startup); no-op while off."""
    with _state.lock:
        try:
            settings = relay_settings()
        except DriveConfigError as exc:
            raise drive_gateway.DriveStartupError(str(exc)) from exc
        if settings.enabled:
            if drive_mode() == "scx":
                spdm_root = drive_gateway.current_settings().spdm_root
                if spdm_root and _inside(settings.drive_base, spdm_root):
                    error = DriveConfigError("SIMDASH_SCX_RELAY_DRIVE_BASE",
                                             "SPDM 마스터 루트(SIMDASH_SCX_DRIVE_ROOT)와 같거나 그 안일 수 없습니다.")
                    raise drive_gateway.DriveStartupError(str(error))
                recovered = recover_submitting()
                if recovered:
                    logger.warning("SCX relay: %d submission(s) left SUBMITTING by a stopped process are UNKNOWN now "
                                   "(a person checks them in SCX)", recovered)
                _cleanup_downloads()
                logger.info("SCX relay enabled: base=%r job_path_form=%s", settings.drive_base, settings.job_path_form)
            else:
                logger.warning("SCX relay is enabled but SIMDASH_DRIVE_GATEWAY is not scx; "
                               "relay requests answer 503 DRIVE_MODE_DISABLED")
        _state.settings = settings
        return settings


def shutdown() -> None:
    with _state.lock:
        _state.settings = None


def settings() -> RelaySettings:
    with _state.lock:
        if _state.settings is not None:
            return _state.settings
    return startup()


def root_key() -> str:
    """Queue root key of relay items: ``relay:<server host>:/<SIMDASH_SCX_RELAY_DRIVE_BASE>`` (sha256)."""
    host = drive_gateway.current_settings().server_host or ""
    identity = f"relay:{host}:/{settings().drive_base}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _spdm_root() -> str | None:
    if drive_mode() != "scx":
        return None
    return drive_gateway.current_settings().spdm_root


def queue_drive_path(rel: str) -> str:
    """Drive path (home relative) of a relay queue item path (relay-base relative); never the SPDM root."""
    base = settings().drive_base
    path = f"{base}/{rel}" if base and rel else (rel or base)
    spdm_root = _spdm_root()
    if not path or (spdm_root and _inside(path, spdm_root)):
        raise SpdmStorageError("SPDM_FORBIDDEN", "relay 항목은 SPDM 루트나 공용 계정 홈 자체에 쓸 수 없습니다.")
    return path


def recover_submitting() -> int:
    """Startup: a submission left ``SUBMITTING`` has an unknown outcome (the process stopped mid-call)."""
    now = _now()
    with connect() as conn:
        count = int(conn.execute("SELECT count(*) FROM scx_relay_submissions WHERE state='SUBMITTING'").fetchone()[0])
        if count:
            conn.execute("UPDATE scx_relay_submissions SET state='UNKNOWN', error_code='SUBMISSION_UNKNOWN', "
                         "error_message=?, updated_at=? WHERE state='SUBMITTING'",
                         ["대시보드가 제출 도중 멈춰 결과를 알 수 없습니다. SCX 화면에서 확인하세요.", now])
    return count


def _cleanup_downloads() -> None:
    try:
        server_local.remove_old_dirs(drive_gateway.current_settings().staging_dir, DOWNLOAD_PREFIX, DOWNLOAD_ORPHAN_SECONDS)
    except OSError:
        logger.warning("SCX relay: download staging cleanup failed", exc_info=True)


# --- access control (contract §3) -----------------------------------------------------------------

def guard(client_host: str | None, headers: Mapping[str, str]) -> str:
    """Every relay request, in contract order.  Returns the client name for audit/queue rows."""
    current = settings()
    if not current.enabled:
        raise RelayError(404, "RELAY_DISABLED", "SCX relay가 꺼져 있습니다.")
    if client_host not in LOOPBACK_HOSTS or any(name in headers for name in PROXY_HEADERS):
        raise RelayError(403, "RELAY_FORBIDDEN", "relay는 이 서버 안(loopback)에서 직접 보낸 요청만 받습니다.")
    provided = headers.get(KEY_HEADER)
    given = provided.encode("latin-1", "replace") if provided else b""
    if not hmac.compare_digest(given, current.secret) or not current.secret:
        raise RelayError(401, "RELAY_UNAUTHORIZED", "relay 인증에 실패했습니다.")
    client = headers.get(CLIENT_HEADER) or DEFAULT_CLIENT
    if not CLIENT_NAME.fullmatch(client):
        client = DEFAULT_CLIENT
    if drive_mode() != "scx":
        raise RelayError(503, "DRIVE_MODE_DISABLED", "대시보드가 SCX 드라이브 모드가 아닙니다.")
    return client


# --- paths (contract §4) --------------------------------------------------------------------------

def check_path(value: Any, *, allow_empty: bool = False) -> str:
    """Shared-account-home relative path: form (400) and the forbidden places (403)."""
    if not isinstance(value, str):
        raise RelayError(400, "INVALID_PATH", "경로가 없습니다.")
    try:
        text = check_relay_rel_path(value, allow_empty=allow_empty)
    except ValueError as exc:
        raise RelayError(400, "INVALID_PATH", f"경로가 올바르지 않습니다. {exc}") from None
    if text.startswith("."):
        raise RelayError(403, "PATH_NOT_ALLOWED", "'.'으로 시작하는 폴더는 relay로 다룰 수 없습니다.")
    spdm_root = _spdm_root()
    if text and spdm_root and _inside(text, spdm_root):
        raise RelayError(403, "PATH_NOT_ALLOWED", "SPDM 마스터 루트는 relay로 읽거나 쓸 수 없습니다.")
    return text


def _below_base(path: str) -> str | None:
    """``path`` relative to the relay base, or ``None`` outside it."""
    base = settings().drive_base
    if not base:
        return path
    if path == base:
        return ""
    if path.startswith(base + "/"):
        return path[len(base) + 1:]
    return None


def _home_path(rel: str) -> str:
    base = settings().drive_base
    return f"{base}/{rel}" if base and rel else (rel or base)


def write_target(path: str, *, min_depth: int) -> str:
    """Relay-base relative folder of a write; at least ``min_depth`` levels below the base."""
    rel = _below_base(path)
    if rel is None:
        raise RelayError(403, "PATH_NOT_ALLOWED", "relay 기준 폴더(SIMDASH_SCX_RELAY_DRIVE_BASE) 밖입니다.")
    depth = len(rel.split("/")) if rel else 0
    if depth < min_depth:
        raise RelayError(403, "PATH_NOT_ALLOWED",
                         f"relay 기준 폴더 아래 {min_depth}단계 이상(<프로젝트>/<잡 폴더>)이어야 합니다.")
    return rel


def check_read(path: str) -> None:
    """Reads: the relay base, or below the output folder of a job the relay recorded."""
    if _below_base(path) is not None:
        return
    with connect() as conn:
        outputs = [str(row[0]) for row in conn.execute(
            "SELECT output_rel FROM scx_relay_submissions WHERE output_rel IS NOT NULL "
            "AND state IN ('SUBMITTED','ATTACHED')").fetchall()]
    if any(path == output or path.startswith(output + "/") for output in outputs):
        return
    raise RelayError(403, "PATH_NOT_ALLOWED", "relay 기준 폴더나 기록된 잡 결과 폴더 밖입니다.")


def check_name(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise RelayError(400, "INVALID_PATH", "파일 이름이 없습니다.")
    name = unicodedata.normalize("NFC", value)
    if (len(name) > 255 or name in {".", ".."} or any(ch in name for ch in "/\\:")
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)):
        raise RelayError(400, "INVALID_PATH", "파일 이름이 올바르지 않습니다.")
    return name


def output_rel(output_path: Any) -> str | None:
    """``JobInfo.output_path`` as a home-relative path (``~/x``, ``<HOME_ABS>/x``, ``x``), else ``None``."""
    if not output_path or not isinstance(output_path, str):
        return None
    text = unicodedata.normalize("NFC", output_path.strip())
    home = settings().home_abs
    if text.startswith("~/"):
        rel = text[2:]
    elif _fold(text).startswith(_fold(home) + "/"):
        rel = text[len(home) + 1:]
    elif text.startswith(("/", "~")):
        return None
    else:
        rel = text
    rel = rel.rstrip("/")
    try:
        return check_path(rel)
    except RelayError:
        return None


def job_path(rel: str) -> str:
    """Path handed to ``submit_radioss`` (``SIMDASH_SCX_RELAY_JOB_PATH_FORM``, server check E4-11-3)."""
    current = settings()
    if current.job_path_form == "tilde":
        return f"~/{rel}"
    if current.job_path_form == "absolute":
        return f"{current.home_abs}/{rel}"
    return rel


# --- drive and adapter access (no DB connection held) ---------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().removesuffix("+00:00") + "Z"
    return str(value) if value else None


def _gateway() -> Any:
    try:
        gateway = drive_gateway.get_drive_gateway()
    except drive_gateway.DriveStartupError as exc:
        raise RelayError(503, "DRIVE_UNAVAILABLE", drive_gateway.safe_error_message(exc), retryable=True) from None
    if gateway is None:
        raise RelayError(503, "DRIVE_MODE_DISABLED", "대시보드가 SCX 드라이브 모드가 아닙니다.")
    return gateway


def _ready() -> Any:
    if connection_held():
        raise RuntimeError("SCX relay drive call while the thread holds a database connection")
    if not drive_auth.wait_account_switch():
        raise RelayError(503, "DRIVE_BUSY", "드라이브 재연결이 진행 중입니다. 잠시 후 다시 시도하세요.",
                         retryable=True, retry_after=5)
    return _gateway()


def _read_call(fn: Callable[[Any], T], op: str, rel: str, *, job_lookup: bool = False) -> T:
    """A read (or job status) call: one refresh-and-retry on ``AUTH_REQUIRED`` like the dashboard reads."""
    gateway = _ready()
    try:
        # Issue #51: the relay's reads share the worker slots with the dashboard (screens first).
        with drive_lanes.call_slot(drive_lanes.BACKGROUND):
            try:
                return fn(gateway)
            except Exception as error:  # noqa: BLE001
                if drive_gateway.drive_error_code(error) != "AUTH_REQUIRED":
                    raise
                outcome = drive_auth.auto_refresh(gateway)
                if outcome == drive_auth.TRANSIENT:
                    raise RelayError(503, "DRIVE_UNAVAILABLE", "드라이브 토큰 갱신이 잠시 실패했습니다. 잠시 후 다시 시도하세요.",
                                     retryable=True, retry_after=30) from None
                if outcome != drive_auth.REFRESHED:
                    raise
                return fn(drive_gateway.get_drive_gateway() or gateway)
    except RelayError:
        raise
    except SpdmStorageError as error:   # no free worker slot in time
        raise RelayError(503, str(error.code), drive_gateway.safe_error_message(error), retryable=True,
                         retry_after=5) from None
    except Exception as error:  # noqa: BLE001 - adapter DriveError carries a code
        if drive_gateway.drive_error_code(error) is None:
            logger.warning("SCX relay %s raised %s: rel=%s", op, type(error).__name__, rel)
        else:
            logger.info("SCX relay %s failed: rel=%s code=%s", op, rel, drive_gateway.drive_error_code(error))
        raise adapter_error(error, job=job_lookup, job_lookup=job_lookup) from None


def _adapter() -> Any | None:
    try:
        return drive_gateway.load_adapter()
    except drive_gateway.DriveStartupError:
        return None


def job_api_available(adapter: Any, gateway: Any = None) -> bool:
    """Adapter v0.3.0+: ``RadiossJobRequest``/``JobResources`` and the three job methods."""
    if adapter is None or not all(hasattr(adapter, name) for name in JOB_API_TYPES):
        return False
    target = gateway if gateway is not None else getattr(adapter, "WorkerDriveGateway", None)
    return target is not None and all(callable(getattr(target, name, None)) for name in JOB_API_METHODS)


def _job_api() -> tuple[Any, Any]:
    unavailable = RelayError(501, "JOB_API_UNAVAILABLE",
                             "대시보드 어댑터에 잡 API가 없습니다(v0.3.0 이상 필요). 드라이브 기능은 계속 쓸 수 있습니다.")
    adapter = _adapter()
    if not job_api_available(adapter):
        raise unavailable
    gateway = _gateway()
    if not job_api_available(adapter, gateway):
        raise unavailable
    return adapter, gateway


def _plain(value: Any) -> Any:
    value = getattr(value, "value", value)
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _bounded(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = drive_gateway.mask_secrets(str(_plain(value)))
    return text[:limit] or None


def job_view(info: Any) -> dict[str, Any]:
    path = _plain(getattr(info, "output_path", None))
    return {"job_id": _bounded(getattr(info, "job_id", None), 128),
            "solver": _bounded(getattr(info, "solver", None), 64),
            "status": _bounded(getattr(info, "status", None), 128),
            "output_path": path if isinstance(path, str) and len(path) <= 1024 else None,
            "output_rel": output_rel(path),
            "error_message": _bounded(getattr(info, "error_message", None), 200)}


# --- health (contract §5.1) -------------------------------------------------------------------------

def health() -> dict[str, Any]:
    """Never builds the gateway (``existing_gateway``)."""
    current = settings()
    drive_settings = drive_gateway.current_settings()
    gateway = drive_gateway.existing_gateway()
    drive = None
    if gateway is not None:
        try:
            drive = drive_gateway.cached_health(gateway)   # issue #51: at most one worker call per window
        except Exception:  # noqa: BLE001 - status only
            drive = {"state": "UNAVAILABLE", "last_success_at": None, "last_error_code": None, "last_error_at": None,
                     "token_refreshed_at": None, "queue_depth": 0, "in_flight": 0}
    with connect() as conn:
        paused = upload_queue.queue_paused(conn)
    adapter = _adapter()
    version = getattr(adapter, "__version__", None) if adapter is not None else None
    return {"relay_version": RELAY_VERSION, "drive_mode": drive_settings.mode,
            "writes_active": drive_writes.drive_writes_active(), "job_api": job_api_available(adapter, gateway),
            "adapter_version": str(version) if version is not None else None, "drive": drive,
            "queue_paused": paused, "relay_base": current.drive_base,
            "forbidden_prefixes": [drive_settings.spdm_root] if drive_settings.spdm_root else [],
            "home_display": current.home_abs, "job_path_form": current.job_path_form,
            "max_upload_bytes": current.max_upload_bytes, "max_download_bytes": current.max_download_bytes}


# --- queue batches (contract §5.2–5.4) --------------------------------------------------------------

def _require_writes() -> None:
    if not drive_writes.drive_writes_active():
        raise RelayError(409, "DRIVE_WRITE_DISABLED",
                         "대시보드 드라이브 쓰기가 꺼져 있습니다(SIMDASH_DRIVE_WRITES_ENABLED·SIMDASH_SCX_DRIVE_ROOT).")


def _request_id(value: Any, what: str = "request_id") -> str:
    if not isinstance(value, str) or not REQUEST_ID.fullmatch(value):
        raise _invalid(f"{what} 형식이 올바르지 않습니다(영문·숫자·_.:- 1~128자).")
    return value


def _relay_batch(conn: Any, request_id: str) -> list[upload_queue.Item]:
    row = conn.execute("SELECT batch_id FROM drive_upload_queue WHERE origin=? AND request_id=? ORDER BY created_at LIMIT 1",
                       [ORIGIN, request_id]).fetchone()
    return upload_queue.batch_items(conn, str(row[0])) if row else []


def batch_view(items: list[upload_queue.Item]) -> dict[str, Any]:
    summary = upload_queue.summarize(items)
    return {"batch_id": summary["batch_id"], "request_id": summary["request_id"], "state": summary["state"],
            "finished": summary["finished"], "paused": summary["paused"],
            "items": [{"kind": item.kind, "target": _home_path(item.target), "state": item.state,
                       "size": item.size_bytes, "attempts": item.attempts, "next_attempt_at": iso(item.next_attempt_at),
                       "error_code": item.last_error_code, "error_message": item.last_error_msg} for item in items]}


def _shape(items: list[upload_queue.Item]) -> list[tuple[Any, ...]]:
    return [(item.kind, item.dst_rel_dir, item.dst_name, item.sha256 if item.kind == "FILE" else None) for item in items]


def mkdirs(payload: Any, client: str) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) - {"request_id", "path"}:
        raise _invalid("본문은 {request_id, path}여야 합니다.")
    request_id = _request_id(payload.get("request_id"))
    rel = write_target(check_path(payload.get("path")), min_depth=1)
    _require_writes()
    wanted = [("MKDIR", rel, None, None)]
    with _upload_lock:
        with connect() as conn:
            existing = _relay_batch(conn, request_id)
            if existing:
                if _shape(existing) != wanted:
                    raise RelayError(409, "REQUEST_ID_REUSED", "같은 request_id로 다른 요청이 이미 있습니다.")
                return 200, batch_view(existing)
            batch_id = upload_queue.enqueue(conn, origin=ORIGIN, requested_by=client, origin_ref=client,
                                            request_id=request_id, root_key=root_key(),
                                            items=[{"kind": "MKDIR", "dst_rel_dir": rel}])
            items = upload_queue.batch_items(conn, batch_id)
    upload_queue.wake()
    logger.info("SCX relay mkdirs queued: batch=%s client=%s", batch_id, client)
    return 202, batch_view(items)


@dataclass
class UploadPlan:
    request_id: str
    client: str
    rel_dir: str
    name: str
    size: int
    sha256: str
    batch_id: str
    staging_dir: Path
    staging_path: Path


def _staging_room(size: int) -> bool:
    free = server_local.disk_free(drive_gateway.current_settings().staging_dir)
    return free is None or free >= STAGING_MIN_FREE_BYTES + int(size)


def plan_upload(client: str, query: Mapping[str, str], headers: Mapping[str, str]) -> UploadPlan | tuple[int, dict[str, Any]]:
    """Checks before the body is read; a replay of the same request_id answers here (body discarded)."""
    length = headers.get("content-length")
    if length is None:
        raise RelayError(411, "LENGTH_REQUIRED", "Content-Length가 필요합니다.")
    if not length.isdigit():
        raise _invalid("Content-Length가 올바르지 않습니다.")
    size = int(length)
    request_id = _request_id(headers.get("x-request-id"), "X-Request-Id")
    digest = headers.get("x-content-sha256") or ""
    if not SHA256_HEX.fullmatch(digest):
        raise _invalid("X-Content-SHA256은 소문자 16진수 64자여야 합니다.")
    name = check_name(query.get("name"))
    folder = check_path(query.get("dir"))
    rel_dir = write_target(folder, min_depth=2)
    check_path(f"{folder}/{name}")
    _require_writes()
    if size > settings().max_upload_bytes:
        raise RelayError(413, "TOO_LARGE", "파일이 relay 업로드 상한(SIMDASH_SCX_RELAY_MAX_UPLOAD_BYTES)보다 큽니다.")
    replay = _upload_replay(request_id, rel_dir, name, digest)
    if replay is not None:
        return replay
    if not _staging_room(size):
        raise RelayError(503, "DRIVE_STAGING_FULL", "서버 임시 공간이 부족합니다.", retryable=True)
    batch_id = upload_queue.new_batch_id()
    staging_dir = server_local.ensure_dir(upload_queue.staging_dir_for(batch_id))
    return UploadPlan(request_id, client, rel_dir, name, size, digest, batch_id, staging_dir, staging_dir / name)


def _upload_replay(request_id: str, rel_dir: str, name: str, digest: str) -> tuple[int, dict[str, Any]] | None:
    with connect() as conn:
        existing = _relay_batch(conn, request_id)
    if not existing:
        return None
    if _shape(existing) != [("MKDIR", rel_dir, None, None), ("FILE", rel_dir, name, digest)]:
        raise RelayError(409, "REQUEST_ID_REUSED", "같은 request_id로 다른 요청이 이미 있습니다.")
    return 200, batch_view(existing)


class StagingWriter:
    """Receives the upload body into ``<SIMDASH_SCX_STAGING_DIR>/upload-<batch>/<name>`` (no DB connection)."""

    def __init__(self, plan: UploadPlan) -> None:
        self.plan = plan
        self.size = 0
        self.digest = hashlib.sha256()
        self.handle: IO[bytes] | None = open(plan.staging_path, "xb")

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.size += len(chunk)
        if self.size > self.plan.size:
            raise RelayError(400, "HASH_MISMATCH", "받은 바이트가 Content-Length보다 많습니다.", retryable=True)
        assert self.handle is not None
        self.handle.write(chunk)
        self.digest.update(chunk)

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            handle.close()


def abort_upload(plan: UploadPlan, writer: StagingWriter | None = None) -> None:
    if writer is not None:
        writer.close()
    server_local.remove_tree(plan.staging_dir)


def commit_upload(plan: UploadPlan, writer: StagingWriter) -> tuple[int, dict[str, Any]]:
    writer.close()
    if writer.size != plan.size or writer.digest.hexdigest() != plan.sha256:
        abort_upload(plan)
        raise RelayError(400, "HASH_MISMATCH", "받은 파일의 크기나 SHA-256이 다릅니다. 다시 보내세요.", retryable=True)
    file_item = {"kind": "FILE", "dst_rel_dir": plan.rel_dir, "dst_name": plan.name, "staging_path": str(plan.staging_path),
                 "size_bytes": plan.size, "sha256": plan.sha256}
    with _upload_lock:
        replay = _upload_replay(plan.request_id, plan.rel_dir, plan.name, plan.sha256)  # raises REQUEST_ID_REUSED
        if replay is not None:
            abort_upload(plan)
            return replay
        with connect() as conn:
            upload_queue.enqueue(conn, origin=ORIGIN, requested_by=plan.client, origin_ref=plan.client,
                                 request_id=plan.request_id, root_key=root_key(), batch_id=plan.batch_id,
                                 items=[{"kind": "MKDIR", "dst_rel_dir": plan.rel_dir}, file_item])
            items = upload_queue.batch_items(conn, plan.batch_id)
    upload_queue.wake()
    logger.info("SCX relay upload queued: batch=%s client=%s size=%d", plan.batch_id, plan.client, plan.size)
    return 202, batch_view(items)


def batch(batch_id: str) -> dict[str, Any]:
    with connect() as conn:
        items = upload_queue.batch_items(conn, batch_id) if len(batch_id) <= 80 else []
    if not items or items[0].origin != ORIGIN:
        raise RelayError(404, "NOT_FOUND", "relay 업로드 묶음을 찾을 수 없습니다.")
    return batch_view(items)


# --- reads (contract §5.5–5.6) ----------------------------------------------------------------------

def _int_param(raw: Any, default: int, minimum: int, maximum: int, what: str) -> int:
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str) or not raw.isdigit() or not minimum <= int(raw) <= maximum:
        raise _invalid(f"{what}는 {minimum}~{maximum} 정수여야 합니다.")
    return int(raw)


def list_entries(query: Mapping[str, str]) -> dict[str, Any]:
    path = check_path(query.get("path", ""), allow_empty=True)
    max_entries = _int_param(query.get("max_entries"), LIST_DEFAULT, 1, LIST_MAX, "max_entries")
    check_read(path)
    background = drive_gateway.priority("BACKGROUND")
    entries = _read_call(lambda gateway: gateway.list_dir(path, max_entries=max_entries, priority=background),
                         "list_dir", path)
    result = []
    for entry in entries:
        name = str(getattr(entry, "name", ""))
        result.append({"name": name, "rel_path": str(getattr(entry, "rel_path", "") or (f"{path}/{name}" if path else name)),
                       "kind": str(_plain(getattr(entry, "kind", "other"))), "size": getattr(entry, "size", None),
                       "modified_at": iso(getattr(entry, "modified_at", None)), "sha1": getattr(entry, "sha1", None)})
    return {"path": path, "entries": result}


@dataclass
class Download:
    local_path: Path
    size: int
    sha256: str
    version_token: str | None
    work_dir: Path = field(repr=False)


def prepare_download(query: Mapping[str, str]) -> Download:
    """Download into ``<STAGING>/relay-dl-<uuid>/``; the response removes the folder when it ends."""
    path = check_path(query.get("path"))
    current = settings()
    limit = min(_int_param(query.get("max_bytes"), current.max_download_bytes, 1, 2 ** 50, "max_bytes"),
                current.max_download_bytes)
    check_read(path)
    background = drive_gateway.priority("BACKGROUND")
    entry = _read_call(lambda gateway: gateway.stat(path, priority=background), "stat", path)
    if entry is None or str(_plain(getattr(entry, "kind", ""))) != "file":
        raise RelayError(404, "NOT_FOUND", "드라이브 파일을 찾을 수 없습니다.")
    size = getattr(entry, "size", None)
    if size is not None and int(size) > limit:
        raise RelayError(413, "TOO_LARGE", "파일이 다운로드 상한보다 큽니다.")
    if not _staging_room(int(size or 0)):
        raise RelayError(503, "DRIVE_STAGING_FULL", "서버 임시 공간이 부족합니다.", retryable=True)
    work = server_local.new_dir(drive_gateway.current_settings().staging_dir, DOWNLOAD_PREFIX)
    try:
        result = _read_call(lambda gateway: gateway.download_to(path, work, max_bytes=limit, priority=background),
                            "download_to", path)
    except RelayError as error:
        server_local.remove_tree(work)
        if error.adapter_code == "LIMIT":
            raise RelayError(413, "TOO_LARGE", "파일이 다운로드 상한보다 큽니다.", adapter_code="LIMIT") from None
        raise
    except BaseException:
        server_local.remove_tree(work)
        raise
    token = getattr(getattr(result, "entry", None), "version_token", None) or getattr(entry, "version_token", None)
    return Download(Path(result.local_path), int(result.size), str(result.sha256).lower(),
                    str(token) if token else None, work)


def finish_download(download: Download) -> None:
    server_local.remove_tree(download.work_dir)


# --- jobs (contract §5.7–5.10) ----------------------------------------------------------------------

@dataclass(frozen=True)
class SubmitRequest:
    request_id: str
    input_path: str
    engine_paths: tuple[str, ...]
    additional_paths: tuple[str, ...]
    version: str
    appliance: str
    ncpu: int
    memory_gb: int | None
    queue_type: str
    run_type: str
    mpi: str
    single_precision: bool
    client_ref: dict[str, str]

    def digest(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def job_dir(self) -> str:
        return self.input_path.rsplit("/", 1)[0]


_SUBMIT_KEYS = {"request_id", "input_path", "engine_paths", "additional_paths", "version", "resources", "run_type", "mpi",
                "single_precision", "client_ref"}
_RESOURCE_KEYS = {"appliance", "ncpu", "memory_gb", "queue_type"}


def _short_text(value: Any, what: str, *, default: str | None = None, limit: int = 128) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise _invalid(f"{what}는 제어 문자 없는 1~{limit}자 문자열이어야 합니다.")
    return value


def _bounded_int(value: Any, what: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise _invalid(f"{what}는 {minimum}~{maximum} 정수여야 합니다.")
    return value


def _paths(value: Any, what: str, limit: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > limit:
        raise _invalid(f"{what}는 경로 0~{limit}개 배열이어야 합니다.")
    return tuple(check_path(item) for item in value)


def parse_submit(payload: Any) -> SubmitRequest:
    if not isinstance(payload, dict) or set(payload) - _SUBMIT_KEYS:
        raise _invalid("제출 본문 형식이 올바르지 않습니다.")
    resources = payload.get("resources")
    if not isinstance(resources, dict) or set(resources) - _RESOURCE_KEYS:
        raise _invalid("resources 형식이 올바르지 않습니다.")
    memory = resources.get("memory_gb")
    single = payload.get("single_precision", False)
    if not isinstance(single, bool):
        raise _invalid("single_precision은 true/false여야 합니다.")
    client_ref = payload.get("client_ref") or {}
    if not isinstance(client_ref, dict) or len(client_ref) > 16 or not all(
            isinstance(key, str) and 1 <= len(key) <= 32 and isinstance(value, str) and len(value) <= 128
            and not any(ord(ch) < 32 for ch in value) for key, value in client_ref.items()):
        raise _invalid("client_ref는 문자열 값(각 128자 이하) 객체여야 합니다.")
    return SubmitRequest(
        request_id=_request_id(payload.get("request_id")),
        input_path=check_path(payload.get("input_path")),
        engine_paths=_paths(payload.get("engine_paths"), "engine_paths", MAX_ENGINE_PATHS),
        additional_paths=_paths(payload.get("additional_paths"), "additional_paths", MAX_ADDITIONAL_PATHS),
        version=_short_text(payload.get("version"), "version"),
        appliance=_short_text(resources.get("appliance"), "resources.appliance"),
        ncpu=_bounded_int(resources.get("ncpu", 1), "resources.ncpu", 1, 4096),
        memory_gb=None if memory is None else _bounded_int(memory, "resources.memory_gb", 1, 65536),
        queue_type=_short_text(resources.get("queue_type"), "resources.queue_type", default="implicit"),
        run_type=_short_text(payload.get("run_type"), "run_type", default=DEFAULT_RUN_TYPE),
        mpi=_short_text(payload.get("mpi"), "mpi", default=DEFAULT_MPI),
        single_precision=single,
        client_ref=dict(sorted(client_ref.items())),
    )


def _check_submit_paths(request: SubmitRequest) -> None:
    """Every path in the write scope and inside the job folder (the parent of ``input_path``)."""
    job_dir = request.job_dir if "/" in request.input_path else ""
    write_target(job_dir, min_depth=2)
    for path in (*request.engine_paths, *request.additional_paths):
        if not path.startswith(job_dir + "/"):
            raise _invalid("engine_paths·additional_paths는 input_path와 같은 잡 폴더 안이어야 합니다.")


_COLUMNS = ("request_id", "request_sha256", "client", "client_ref", "input_rel", "job_dir_rel", "state", "job_id", "solver",
            "last_status", "output_path", "output_rel", "error_code", "error_message", "created_at", "updated_at",
            "resolved_at")
_SELECT = "SELECT " + ",".join(_COLUMNS) + " FROM scx_relay_submissions"


def _row(conn: Any, request_id: str) -> dict[str, Any] | None:
    row = conn.execute(_SELECT + " WHERE request_id=?", [request_id]).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


def _row_by_job(conn: Any, job_id: str) -> dict[str, Any] | None:
    row = conn.execute(_SELECT + " WHERE job_id=?", [job_id]).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


def _recorded_job(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"job_id": row["job_id"], "solver": row["solver"], "status": row["last_status"],
            "output_path": row["output_path"], "output_rel": row["output_rel"],
            "error_message": row["error_message"] if not row["error_code"] else None}


def submission_view(row: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
    return {"request_id": row["request_id"], "state": row["state"], "replayed": replayed, "job": _recorded_job(row)}


def _replay(row: Mapping[str, Any], digest: str) -> tuple[int, dict[str, Any]]:
    if row["request_sha256"] != digest:
        raise RelayError(409, "REQUEST_ID_REUSED", "같은 request_id로 다른 제출이 이미 있습니다. 새 제출은 새 request_id로 하세요.")
    state = row["state"]
    if state in TRACKED_STATES:
        return 200, submission_view(row, replayed=True)
    if state == "SUBMITTING":
        raise RelayError(409, "SUBMISSION_IN_PROGRESS", "같은 request_id의 제출을 처리하고 있습니다.", retryable=True)
    if state == "UNKNOWN":
        raise RelayError(409, "SUBMISSION_UNKNOWN", "SCX 제출 결과를 알 수 없습니다. SCX 화면에서 확인한 뒤 정리하세요.",
                         adapter_code="SUBMISSION_UNKNOWN")
    if state == "ABANDONED":
        raise RelayError(409, "SUBMISSION_ABANDONED", "이 request_id의 제출은 '제출되지 않음'으로 정리되었습니다.")
    raise _mapped(row["error_code"], row["error_message"], job=True)


def _record(request_id: str, state: str, *, info: Any = None, code: str | None = None, message: str | None = None) -> None:
    """The submission outcome with short connections, retried; a final failure leaves ``SUBMITTING``
    (startup turns it ``UNKNOWN``)."""
    view = job_view(info) if info is not None else None
    for attempt in range(len(RECORD_RETRY_DELAYS) + 1):
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE scx_relay_submissions SET state=?, job_id=?, solver=?, last_status=?, output_path=?, "
                    "output_rel=?, error_code=?, error_message=?, updated_at=? WHERE request_id=? AND state='SUBMITTING'",
                    [state, view["job_id"] if view else None, view["solver"] if view else None,
                     view["status"] if view else None, view["output_path"] if view else None,
                     view["output_rel"] if view else None, code,
                     _bounded(message, 200) if code else (view["error_message"] if view else None), _now(), request_id])
            return
        except Exception:  # noqa: BLE001 - DB hiccup after the adapter call
            if attempt >= len(RECORD_RETRY_DELAYS):
                raise
            logger.warning("SCX relay: recording submission %s as %s failed; retrying", request_id, state, exc_info=True)
            time.sleep(RECORD_RETRY_DELAYS[attempt])


def submit(payload: Any, client: str) -> tuple[int, dict[str, Any]]:
    adapter, gateway = _job_api()
    request = parse_submit(payload)
    _check_submit_paths(request)
    digest = request.digest()
    with connect() as conn:
        row = _row(conn, request.request_id)
    if row is not None:
        return _replay(row, digest)
    try:
        adapter_request = adapter.RadiossJobRequest(
            input_path=job_path(request.input_path), version=request.version,
            resources=adapter.JobResources(appliance=request.appliance, ncpu=request.ncpu, memory_gb=request.memory_gb,
                                           queue_type=request.queue_type),
            engine_paths=tuple(job_path(path) for path in request.engine_paths),
            additional_paths=tuple(job_path(path) for path in request.additional_paths),
            run_type=request.run_type, mpi=request.mpi, single_precision=request.single_precision)
    except TypeError:
        raise RelayError(501, "JOB_API_UNAVAILABLE", "대시보드 어댑터의 잡 요청 형식이 계약(v0.3.0)과 다릅니다.") from None
    # Pre-flight without a DB connection: the starter exists (no adapter submit call yet).
    entry = _read_call(lambda drive: drive.stat(request.input_path, priority=drive_gateway.priority("BACKGROUND")),
                       "stat", request.input_path)
    if entry is None or str(_plain(getattr(entry, "kind", ""))) != "file":
        raise RelayError(404, "NOT_FOUND", "input_path 파일이 드라이브에 없습니다.")
    with _submit_lock:
        with connect() as conn:
            row = _row(conn, request.request_id)
            if row is None:
                now = _now()
                conn.execute(
                    "INSERT INTO scx_relay_submissions (request_id,request_sha256,client,client_ref,input_rel,job_dir_rel,"
                    "state,created_at,updated_at) VALUES (?,?,?,?,?,?,'SUBMITTING',?,?)",
                    [request.request_id, digest, client, json.dumps(request.client_ref, ensure_ascii=False),
                     request.input_path, request.job_dir, now, now])
        if row is not None:
            return _replay(row, digest)
    # The adapter call: never retried, no DB connection held.
    if connection_held():
        raise RuntimeError("SCX relay submit while the thread holds a database connection")
    try:
        info = gateway.submit_radioss(adapter_request)
    except Exception as error:  # noqa: BLE001 - every outcome is recorded
        code = drive_gateway.drive_error_code(error)
        message = drive_gateway.safe_error_message(error)
        if code is None or code == "SUBMISSION_UNKNOWN":
            logger.warning("SCX relay submission %s outcome unknown (%s)", request.request_id, code or type(error).__name__)
            _record(request.request_id, "UNKNOWN", code="SUBMISSION_UNKNOWN", message=message)
            raise RelayError(409, "SUBMISSION_UNKNOWN", "SCX 제출 결과를 알 수 없습니다. SCX 화면에서 확인한 뒤 정리하세요.",
                             adapter_code=code) from None
        logger.warning("SCX relay submission %s failed: code=%s", request.request_id, code)
        _record(request.request_id, "FAILED", code=code, message=message)
        raise _mapped(code, message, job=True) from None
    _record(request.request_id, "SUBMITTED", info=info)
    logger.info("SCX relay submission %s submitted: job=%s client=%s", request.request_id,
                _bounded(getattr(info, "job_id", None), 128), client)
    with connect() as conn:
        row = _row(conn, request.request_id)
    assert row is not None
    return 201, submission_view(row, replayed=False)


def _job_id(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise RelayError(404, "JOB_NOT_FOUND", "relay 기록에 없는 잡입니다.")
    return value


def _tracked(job_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = _row_by_job(conn, job_id)
    if row is None or row["state"] not in TRACKED_STATES:
        raise RelayError(404, "JOB_NOT_FOUND", "relay 기록에 없는 잡입니다.")
    return row


def _update_job(request_id: str, view: Mapping[str, Any]) -> None:
    with connect() as conn:
        conn.execute("UPDATE scx_relay_submissions SET solver=COALESCE(?, solver), last_status=?, output_path=?, "
                     "output_rel=?, error_message=?, updated_at=? WHERE request_id=?",
                     [view["solver"], view["status"], view["output_path"], view["output_rel"], view["error_message"],
                      _now(), request_id])


def get_job(job_id: str) -> dict[str, Any]:
    job_id = _job_id(job_id)
    _job_api()
    row = _tracked(job_id)
    info = _read_call(lambda gateway: gateway.get_job(job_id), "get_job", "-", job_lookup=True)
    view = job_view(info)
    view["job_id"] = job_id
    _update_job(str(row["request_id"]), view)
    return view


def terminate_job(job_id: str) -> dict[str, Any]:
    job_id = _job_id(job_id)
    _, gateway = _job_api()
    _tracked(job_id)
    if connection_held():
        raise RuntimeError("SCX relay terminate while the thread holds a database connection")
    _ready()
    try:
        gateway.terminate_job(job_id)            # once; never retried
    except Exception as error:  # noqa: BLE001
        logger.warning("SCX relay terminate %s failed: code=%s", job_id, drive_gateway.drive_error_code(error))
        raise adapter_error(error, job=True, job_lookup=True) from None
    logger.info("SCX relay terminate requested: job=%s", job_id)
    return {"job_id": job_id, "requested": True}


def resolve(request_id: str, payload: Any) -> dict[str, Any]:
    """A person checked SCX: attach the job found there, or abandon (never decided by the relay itself)."""
    request_id = _request_id(request_id)
    if not isinstance(payload, dict) or payload.get("action") not in {"attach", "abandon"}:
        raise _invalid("action은 attach 또는 abandon이어야 합니다.")
    action = payload["action"]
    if set(payload) - ({"action", "job_id"} if action == "attach" else {"action"}):
        raise _invalid("resolve 본문 형식이 올바르지 않습니다.")
    with connect() as conn:
        row = _row(conn, request_id)
    if row is None:
        raise RelayError(404, "NOT_FOUND", "이 request_id의 제출 기록이 없습니다.")
    not_unknown = RelayError(409, "NOT_UNKNOWN", "결과 모름(UNKNOWN) 상태의 제출만 정리할 수 있습니다.")
    if row["state"] != "UNKNOWN":
        raise not_unknown
    if action == "abandon":
        with _submit_lock, connect() as conn:
            now = _now()
            done = conn.execute("UPDATE scx_relay_submissions SET state='ABANDONED', resolved_at=?, updated_at=? "
                                "WHERE request_id=? AND state='UNKNOWN' RETURNING request_id",
                                [now, now, request_id]).fetchone()
        if done is None:
            raise not_unknown
        logger.info("SCX relay submission %s abandoned after a manual check", request_id)
        return {"request_id": request_id, "state": "ABANDONED"}
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not 1 <= len(job_id) <= 128 or any(ord(ch) < 32 or ord(ch) == 127 for ch in job_id):
        raise _invalid("job_id는 제어 문자 없는 1~128자여야 합니다.")
    _job_api()
    in_use = RelayError(409, "JOB_ID_IN_USE", "이 잡 ID는 다른 제출 기록에 이미 연결되어 있습니다.")
    with connect() as conn:
        if _row_by_job(conn, job_id) is not None:
            raise in_use
    info = _read_call(lambda gateway: gateway.get_job(job_id), "get_job", "-", job_lookup=True)
    view = job_view(info)
    view["job_id"] = job_id
    with _submit_lock:
        with connect() as conn:
            if _row_by_job(conn, job_id) is not None:
                raise in_use
            now = _now()
            done = conn.execute(
                "UPDATE scx_relay_submissions SET state='ATTACHED', job_id=?, solver=?, last_status=?, output_path=?, "
                "output_rel=?, error_code=NULL, error_message=?, resolved_at=?, updated_at=? "
                "WHERE request_id=? AND state='UNKNOWN' RETURNING request_id",
                [job_id, view["solver"], view["status"], view["output_path"], view["output_rel"], view["error_message"],
                 now, now, request_id]).fetchone()
        if done is None:
            raise not_unknown
    logger.info("SCX relay submission %s attached to job %s after a manual check", request_id, job_id)
    return {"request_id": request_id, "state": "ATTACHED", "job": view}


__all__ = ["Download", "PREFIX", "RelayError", "StagingWriter", "UploadPlan", "abort_upload", "batch", "commit_upload",
           "finish_download", "get_job", "guard", "health", "job_api_available", "list_entries", "mkdirs", "plan_upload",
           "prepare_download", "queue_drive_path", "recover_submitting", "resolve", "root_key", "settings", "shutdown",
           "startup", "submit", "terminate_job"]
