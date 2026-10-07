"""SCX drive gateway lifecycle (integration 01 §3–4, contract §9–10).

* ``none`` mode: :func:`get_drive_gateway` returns ``None`` and nothing else
  in this module runs.  The adapter package is never imported.
* ``scx`` mode: :func:`startup` (FastAPI lifespan) validates the settings,
  imports ``scx_drive_adapter`` (installed separately with ``--no-deps``,
  decision D6) and takes an exclusive, non-blocking lock on
  ``<SIMDASH_SCX_WORK_DIR>/dashboard.lock`` so only one dashboard process
  owns the worker.  The single ``WorkerDriveGateway`` is built lazily on first
  use and closed by :func:`shutdown`.

The dashboard never deletes, moves or overwrites anything on the drive
(contract §2.5).  ``tests/test_drive_foundation.py`` checks this package
statically for forbidden call names.
"""
from __future__ import annotations

import importlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Literal, Protocol, runtime_checkable

from ..storage.provider import SpdmStorageError
from .config import DriveConfigError, DriveSettings, drive_mode, drive_settings

logger = logging.getLogger("app.services.drive")

ADAPTER_MODULE = "scx_drive_adapter"
ADAPTER_MISSING_MESSAGE = (
    "SCX 드라이브 어댑터 패키지(scx_drive_adapter)가 설치되지 않았습니다. 어댑터 wheel(vd_scx_drive_adapter-*.whl)을 "
    "external-wheels 폴더(소스 PC: 저장소 루트의 external-wheels\\, 폐쇄망 서버: 설치 루트의 state\\external-wheels\\)에 "
    "복사한 뒤 update.bat(폐쇄망 서버는 오프라인 설치기)을 다시 실행하세요. "
    "(SCX drive adapter package 'scx_drive_adapter' is not installed; copy the adapter wheel into the "
    "external-wheels folder and run update.bat, or the offline installer on the server.)"
)
LOCK_HELD_MESSAGE = (
    "다른 대시보드 프로세스가 SCX 워커를 사용 중입니다. scx 모드는 단일 프로세스(uvicorn 작업자 1개)만 허용합니다: {path} "
    "(another dashboard process already owns the SCX worker; scx mode requires a single process)"
)

HealthState = Literal["OK", "DEGRADED", "AUTH_REQUIRED", "UNAVAILABLE"]


# --- Local mirror of the adapter contract (contract §2) -----------------------------------
# The rest of the dashboard type-checks against these without the adapter installed.

@runtime_checkable
class DriveEntryLike(Protocol):
    name: str
    rel_path: str
    kind: str
    size: int | None
    modified_at: datetime | None
    created_at: datetime | None
    item_id: str | None
    sha1: str | None
    version_token: str
    local_safe: bool


class DownloadResultLike(Protocol):
    local_path: Path
    entry: DriveEntryLike
    size: int
    sha256: str


class HealthStatusLike(Protocol):
    state: HealthState
    last_success_at: datetime | None
    last_error_code: str | None
    last_error_at: datetime | None
    token_refreshed_at: datetime | None
    queue_depth: int
    in_flight: int


class DriveGatewayLike(Protocol):
    def root_identity(self) -> str: ...
    def list_dir(self, rel_path: str, *, max_entries: int = 50_000, priority: Any = ...) -> list[Any]: ...
    def stat(self, rel_path: str, *, priority: Any = ...) -> Any | None: ...
    def download_to(self, rel_path: str, local_dir: Path, *, max_bytes: int, priority: Any = ...) -> Any: ...
    def upload_new(self, local_file: Path, rel_dir: str, *, name: str | None = None) -> Any: ...
    def copy_within(self, src_rel: str, dst_dir_rel: str, *, new_name: str | None = None) -> Any: ...
    def mkdirs(self, rel_path: str) -> Any: ...
    def health(self) -> Any: ...
    def close(self) -> None: ...


# --- Error mapping (integration 06 §2) -----------------------------------------------------

@dataclass(frozen=True)
class DriveErrorMapping:
    storage_code: str
    http_status: int
    retryable: bool
    retry_after: int | None = None


DRIVE_ERROR_MAP: dict[str, DriveErrorMapping] = {
    "NOT_FOUND": DriveErrorMapping("SPDM_NOT_FOUND", 404, False),
    "FORBIDDEN": DriveErrorMapping("SPDM_FORBIDDEN", 403, False),
    "CONFLICT": DriveErrorMapping("SPDM_CONFLICT", 409, False),
    "LOCKED": DriveErrorMapping("SPDM_LOCKED", 423, True),
    "BUSY": DriveErrorMapping("SPDM_FILE_BUSY", 409, True),
    "LIMIT": DriveErrorMapping("SPDM_LIMIT", 413, False),
    "INVALID_PATH": DriveErrorMapping("SPDM_INVALID_PATH", 400, False),
    "AUTH_REQUIRED": DriveErrorMapping("DRIVE_AUTH_REQUIRED", 503, False),
    "OVERLOADED": DriveErrorMapping("DRIVE_BUSY", 503, True, 5),
    "TIMEOUT": DriveErrorMapping("DRIVE_TIMEOUT", 504, True),
    "UNAVAILABLE": DriveErrorMapping("DRIVE_UNAVAILABLE", 503, True),
    "INTERNAL": DriveErrorMapping("DRIVE_INTERNAL", 500, False),
}
_FALLBACK = DRIVE_ERROR_MAP["INTERNAL"]


def drive_error_code(error: BaseException) -> str | None:
    """Adapter ``DriveError.code`` as a plain string, or ``None`` for other exceptions."""
    code = getattr(error, "code", None)
    if code is None:
        return None
    value = getattr(code, "value", code)
    return str(value) if isinstance(value, str) else None


def map_drive_error(error: BaseException) -> DriveErrorMapping:
    return DRIVE_ERROR_MAP.get(drive_error_code(error) or "", _FALLBACK)


_BEARER = re.compile(r"(?i)\bbearer\s+[^\s\"',;]+")
_JWT_LIKE = re.compile(r"eyJ[A-Za-z0-9_\-]*(?:\.[A-Za-z0-9_\-]*)*")


def mask_secrets(text: str) -> str:
    """Mask ``Bearer <x>`` and JWT-like ``eyJ...`` substrings (defence in depth)."""
    return _JWT_LIKE.sub("***", _BEARER.sub("Bearer ***", text))


def safe_error_message(error: BaseException) -> str:
    """Adapter messages are already sanitized (contract §3/§6); mask and bound them anyway."""
    message = getattr(error, "message", None) or str(error) or type(error).__name__
    return mask_secrets(str(message)[:4096])[:200]


def to_storage_error(error: BaseException) -> SpdmStorageError:
    mapping = map_drive_error(error)
    return SpdmStorageError(mapping.storage_code, safe_error_message(error))


# --- Single-process lock (integration 01 §4) ------------------------------------------------

class SingleProcessLock:
    """Exclusive non-blocking lock on a file, held for the process lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n".encode("ascii"))
            handle.flush()
        except OSError:
            pass
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


# --- Lifecycle ---------------------------------------------------------------------------------

class DriveStartupError(RuntimeError):
    """scx mode cannot start; the message tells the operator what to fix."""


class _State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.settings: DriveSettings | None = None
        self.adapter: Any = None
        self.process_lock: SingleProcessLock | None = None
        self.gateway: Any = None
        self.gateway_store: Any = None
        self.token_store: Any = None


_state = _State()


def load_adapter() -> Any:
    """Import the separately installed adapter package (scx mode only)."""
    try:
        return importlib.import_module(ADAPTER_MODULE)
    except ImportError as exc:
        raise DriveStartupError(ADAPTER_MISSING_MESSAGE) from exc


def startup() -> DriveSettings:
    """Validate scx mode before the app serves requests; no-op in none mode."""
    with _state.lock:
        try:
            settings = drive_settings()
        except DriveConfigError as exc:
            raise DriveStartupError(str(exc)) from exc
        if not settings.enabled:
            return settings
        if _state.process_lock is not None and _state.process_lock.held and _state.settings == settings:
            return settings
        adapter = load_adapter()
        for name in ("WorkerDriveGateway", "WorkerConfig", "AdapterConfig"):
            if not hasattr(adapter, name):
                raise DriveStartupError(f"어댑터 패키지에 {name}이(가) 없습니다. 계약 v0.2 이상 어댑터를 설치하세요. "
                                        f"(adapter package lacks {name})")
        lock = SingleProcessLock(settings.lock_path)
        if not lock.acquire():
            raise DriveStartupError(LOCK_HELD_MESSAGE.format(path=settings.lock_path))
        try:
            settings.staging_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            lock.release()
            raise DriveStartupError(f"SIMDASH_SCX_STAGING_DIR 폴더를 만들 수 없습니다: {settings.staging_dir} ({exc.strerror})") from exc
        _state.settings, _state.adapter, _state.process_lock = settings, adapter, lock
        logger.info("SCX drive mode enabled: server=%s worker_python=%s work_dir=%s",
                    settings.server_host, settings.worker_python, settings.work_dir)
        return settings


def shutdown() -> None:
    with _state.lock:
        gateway, _state.gateway, _state.gateway_store = _state.gateway, None, None
        try:
            if gateway is not None:
                gateway.close()
        except Exception:  # close must not block shutdown; message is adapter-sanitized
            logger.warning("SCX drive gateway close failed", exc_info=True)
        finally:
            if _state.process_lock is not None:
                _state.process_lock.release()
            _state.process_lock = None
            _state.adapter = None
            _state.token_store = None
            _state.settings = None


def current_settings() -> DriveSettings:
    """Settings captured at startup (scx), otherwise the current environment."""
    with _state.lock:
        if _state.settings is not None:
            return _state.settings
    try:
        mode = drive_mode()
    except DriveConfigError as exc:
        raise DriveStartupError(str(exc)) from exc
    if mode == "none":
        return DriveSettings(mode="none")
    return startup()


def token_store() -> Any:
    """The process-wide admin ``DbTokenStore`` (scx mode), or ``None``.

    The gateway gets its own :meth:`DbTokenStore.for_gateway` store (own loaded
    generation, adapter ``TokenBundle`` at the boundary); see :func:`get_drive_gateway`.
    """
    settings = current_settings()
    if not settings.enabled:
        return None
    with _state.lock:
        if _state.token_store is None:
            from .token_store import DbTokenStore

            _state.token_store = DbTokenStore(settings.secret_key or "")
        return _state.token_store


def existing_gateway() -> Any | None:
    """The gateway if one was already built; never builds one (status polling)."""
    with _state.lock:
        return _state.gateway


def gateway_token_store() -> Any | None:
    with _state.lock:
        return _state.gateway_store


def reset_gateway() -> None:
    """Close and drop the gateway so the worker forgets its in-memory tokens.

    Called after the admin deletes or re-registers credentials.  The next
    :func:`get_drive_gateway` builds a new gateway that loads the current row.
    A late rotation from the closed gateway is fenced off by the row generation.
    """
    with _state.lock:
        gateway, _state.gateway, _state.gateway_store = _state.gateway, None, None
    if gateway is None:
        return
    try:
        gateway.close()
    except Exception:  # message is adapter-sanitized; the old worker is abandoned either way
        logger.warning("SCX drive gateway close failed during credential change")


def get_drive_gateway() -> DriveGatewayLike | None:
    """The single ``WorkerDriveGateway`` (scx mode, built lazily) or ``None`` (none mode)."""
    settings = current_settings()
    if not settings.enabled:
        return None
    with _state.lock:
        if _state.gateway is not None:
            return _state.gateway
        if _state.adapter is None or _state.process_lock is None or not _state.process_lock.held:
            startup()
        adapter = _state.adapter
        store = token_store().for_gateway(getattr(adapter, "TokenBundle", None))
        adapter_config = adapter.AdapterConfig(
            server_url=settings.server_url,
            drive_root="~",
            client_name=settings.client_name,
            ca_bundle_path=settings.ca_bundle,
            temp_dir=settings.work_dir / "adapter-tmp",
            max_concurrency=settings.max_concurrency,
            cache_ttl_s=settings.cache_ttl_seconds,
        )
        worker_config = adapter.WorkerConfig(
            python_exe=settings.worker_python,
            adapter=adapter_config,
            work_dir=settings.work_dir,
        )
        _state.gateway = adapter.WorkerDriveGateway(worker_config, store)
        _state.gateway_store = store
        return _state.gateway


def priority(name: str = "INTERACTIVE") -> Any:
    """Adapter ``Priority`` member (INTERACTIVE=0, BACKGROUND=1)."""
    adapter = _state.adapter
    enum = getattr(adapter, "Priority", None) if adapter is not None else None
    if enum is not None and hasattr(enum, name):
        return getattr(enum, name)
    return 0 if name == "INTERACTIVE" else 1


def health_dict(health: Any) -> dict[str, Any] | None:
    if health is None:
        return None

    def stamp(value: Any) -> str | None:
        return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)

    return {
        "state": str(getattr(getattr(health, "state", None), "value", getattr(health, "state", None)) or "UNAVAILABLE"),
        "last_success_at": stamp(getattr(health, "last_success_at", None)),
        "last_error_code": getattr(health, "last_error_code", None),
        "last_error_at": stamp(getattr(health, "last_error_at", None)),
        "token_refreshed_at": stamp(getattr(health, "token_refreshed_at", None)),
        "queue_depth": int(getattr(health, "queue_depth", 0) or 0),
        "in_flight": int(getattr(health, "in_flight", 0) or 0),
    }
