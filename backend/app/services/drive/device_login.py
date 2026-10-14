"""SCX drive login with an activation code (OAuth device grant), 2026-10-14 — spec §7.

The HPC SSO refuses ID/password logins for the ``drive-desktop`` client (issue #52), so the
dashboard lets a global admin connect the shared drive account like the adapter CLI does:

1. :func:`start` asks the adapter for an activation code (``device_login_start``).  The adapter must
   *not* open a browser on the server; the dashboard shows the code and the verification address.
2. The admin opens that address in any browser (another PC or a phone works too), signs in to the HPC
   SSO with the shared account and enters the code.
3. The screen calls :func:`poll` every ``interval`` seconds; one ``device_login_poll`` per due call.
   On success the router's ``apply`` callback checks the account (the shared account is fixed:
   another account is refused, nothing is saved) and stores the tokens like the other re-logins.

One session exists at a time for the whole dashboard (scx mode runs one process).  The adapter's
``login_id`` never leaves the server; the screen uses a random ``device_id``.  Nothing secret passes
through the dashboard (no password); the code itself is shown to global admins only and is never
logged or audited.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from . import auth as drive_auth
from . import gateway as drive_gateway
from .auth import DriveAuthError

logger = logging.getLogger("app.services.drive.device_login")

PENDING, SUCCEEDED, FAILED, EXPIRED, CANCELLED = "PENDING", "SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED"
DEFAULT_INTERVAL = 5
MIN_INTERVAL, MAX_INTERVAL = 2, 30
SLOW_DOWN_STEP = 5
DEFAULT_LIFETIME = 600
MIN_LIFETIME, MAX_LIFETIME = 30, 1800
KEEP_FINISHED = timedelta(minutes=10)
_USER_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{2,31}$")
_PENDING_WORDS = {"PENDING", "AUTHORIZATION_PENDING", "WAITING"}
_SLOW_WORDS = {"SLOW_DOWN"}
_EXPIRED_CODES = {"AUTH_DEVICE_EXPIRED", "NOT_FOUND"}


@dataclass
class DeviceSession:
    device_id: str
    login_id: str
    user_code: str
    verification_uri: str | None
    verification_uri_complete: str | None
    started_at: datetime
    expires_at: datetime
    interval: int
    started_by: str
    next_poll_at: datetime
    status: str = PENDING
    code: str | None = None
    message: str | None = None
    account: str | None = None
    queue_resumed: int = 0
    finished_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def view(self) -> dict[str, Any]:
        pending = self.status == PENDING
        return {
            "device_id": self.device_id, "status": self.status,
            # The code and the address matter only while the login waits for approval.
            "user_code": self.user_code if pending else None,
            "verification_uri": self.verification_uri if pending else None,
            "verification_uri_complete": self.verification_uri_complete if pending else None,
            "started_at": self.started_at.isoformat(), "expires_at": self.expires_at.isoformat(),
            "interval": self.interval, "code": self.code, "message": self.message,
            "account": self.account, "queue_resumed": self.queue_resumed,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None, **self.extra,
        }


_lock = threading.Lock()
_session: DeviceSession | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reset() -> None:
    """Drive shutdown / tests: forget the session (the worker that held it is gone)."""
    global _session
    with _lock:
        _session = None


def current() -> dict[str, Any] | None:
    with _lock:
        session = _session
        if session is None:
            return None
        _expire_locked(session)
        if session.status != PENDING and session.finished_at and _now() - session.finished_at > KEEP_FINISHED:
            return None
        return session.view()


def _field(raw: Any, name: str) -> Any:
    return raw.get(name) if isinstance(raw, dict) else getattr(raw, name, None)


def _safe_url(value: Any) -> str | None:
    """Only a plain http(s) address is ever shown as a link (no ``javascript:`` from the adapter)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 2048 or any(ord(ch) <= 32 or ord(ch) == 127 for ch in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    return value


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _expire_locked(session: DeviceSession) -> bool:
    if session.status == PENDING and _now() >= session.expires_at:
        _finish_locked(session, EXPIRED, "AUTH_DEVICE_EXPIRED", drive_auth.AUTH_ERRORS["AUTH_DEVICE_EXPIRED"][1])
        return True
    return False


def _finish_locked(session: DeviceSession, status: str, code: str | None, message: str | None, **extra: Any) -> None:
    session.status, session.code, session.message = status, code, message
    session.finished_at = _now()
    session.extra = {key: value for key, value in extra.items() if key in {"current", "received"}}


def _adapter_cancel(login_id: str) -> None:
    """Best effort: the adapter stops polling the SSO for this code."""
    gateway = drive_gateway.existing_gateway()
    method = getattr(gateway, "device_login_cancel", None) if gateway is not None else None
    if not callable(method):
        return
    try:
        method(login_id)
    except Exception as error:  # noqa: BLE001 - cancelling never fails the request
        logger.info("SCX drive device_login_cancel failed (%s)", drive_gateway.drive_error_code(error) or type(error).__name__)


def start(started_by: str) -> tuple[dict[str, Any], bool]:
    """(session view, created).  A waiting session is reused; a finished one is replaced."""
    global _session
    with _lock:
        if _session is not None and _session.status == PENDING and not _expire_locked(_session):
            return _session.view(), False
    caps, supported, _error = drive_auth.capabilities()
    if not supported:
        raise DriveAuthError(409, "DRIVE_ADAPTER_UPDATE_REQUIRED",
                             "설치된 드라이브 어댑터가 코드 로그인을 지원하지 않습니다. 어댑터 업데이트 필요 (이슈 #52).")
    if not caps.get("device"):
        raise DriveAuthError(409, "AUTH_METHOD_UNSUPPORTED",
                             "설치된 드라이브 어댑터가 코드 로그인(device_login_start)을 지원하지 않습니다. 어댑터 업데이트 필요 (이슈 #52).")
    if not drive_auth.operation_lock.acquire(blocking=False):
        raise DriveAuthError(409, "DRIVE_AUTH_BUSY", "다른 재연결이 진행 중입니다. 잠시 후 다시 시도하세요.")
    try:
        gateway = drive_auth._current_gateway()
        try:
            raw = drive_auth._method(gateway, "device_login_start")()
        except Exception as error:  # noqa: BLE001
            raise drive_auth._adapter_failure(gateway, "device_login_start", error) from None
    finally:
        drive_auth.operation_lock.release()
    login_id, user_code = _field(raw, "login_id"), _field(raw, "user_code")
    if not isinstance(login_id, str) or not login_id or len(login_id) > 200 \
            or not isinstance(user_code, str) or not _USER_CODE.match(user_code.strip()):
        logger.warning("SCX drive device_login_start returned no usable code (login_id or user_code missing)")
        raise DriveAuthError(502, "DRIVE_INTERNAL", "어댑터가 올바른 활성화 코드를 주지 않았습니다(어댑터 확인 필요).")
    uri, complete = _safe_url(_field(raw, "verification_uri")), _safe_url(_field(raw, "verification_uri_complete"))
    if uri is None and complete is None:
        logger.warning("SCX drive device_login_start returned no http(s) verification address")
    now = _now()
    lifetime = _bounded(_field(raw, "expires_in"), DEFAULT_LIFETIME, MIN_LIFETIME, MAX_LIFETIME)
    interval = _bounded(_field(raw, "interval"), DEFAULT_INTERVAL, MIN_INTERVAL, MAX_INTERVAL)
    session = DeviceSession(device_id=secrets.token_urlsafe(16), login_id=login_id, user_code=user_code.strip(),
                            verification_uri=uri, verification_uri_complete=complete, started_at=now,
                            expires_at=now + timedelta(seconds=lifetime), interval=interval, started_by=started_by,
                            next_poll_at=now + timedelta(seconds=interval))
    with _lock:
        previous, _session = _session, session
    if previous is not None and previous.status == PENDING:
        _adapter_cancel(previous.login_id)
    return session.view(), True


def _session_for(device_id: str) -> DeviceSession:
    session = _session
    if session is None or not secrets.compare_digest(session.device_id, device_id):
        raise DriveAuthError(404, "DRIVE_DEVICE_LOGIN_NOT_FOUND",
                             "진행 중인 코드 로그인이 없습니다(다른 관리자가 새로 시작했거나 끝났습니다). 다시 시작하세요.")
    return session


def cancel(device_id: str) -> dict[str, Any]:
    with _lock:
        session = _session_for(device_id)
        if session.status != PENDING:
            return session.view()
        _finish_locked(session, CANCELLED, "DRIVE_DEVICE_LOGIN_CANCELLED", "코드 로그인을 취소했습니다.")
        view, login_id = session.view(), session.login_id
    _adapter_cancel(login_id)
    return view


Apply = Callable[[drive_auth.AuthResult, Any], dict[str, Any]]


def poll(device_id: str, apply: Apply) -> tuple[dict[str, Any], str | None]:
    """One ``device_login_poll`` when due; returns (view, finished status or ``None``).

    ``apply(result, gateway_store)`` saves the tokens (raises :class:`DriveAuthError` to refuse them,
    e.g. another account); it runs while drive reads wait for the account check."""
    with _lock:
        session = _session_for(device_id)
        if session.status != PENDING:
            return session.view(), None
        if _expire_locked(session):
            view, login_id = session.view(), session.login_id
            expired = True
        else:
            expired = False
            if _now() < session.next_poll_at:
                return session.view(), None
            session.next_poll_at = _now() + timedelta(seconds=session.interval)   # claim this poll
            login_id = session.login_id
    if expired:
        _adapter_cancel(login_id)
        return view, EXPIRED
    if not drive_auth.operation_lock.acquire(blocking=False):
        with _lock:
            return session.view(), None          # another re-login runs: try again next time
    try:
        try:
            gateway = drive_auth._current_gateway()
            store = drive_gateway.gateway_token_store()
            with drive_auth.account_switch():
                raw, captured = _poll_adapter(gateway, store, login_id)
                state = str(_field(raw, "status") or "").strip().upper() if raw is not None else ""
                if not _has_token(raw) and not captured and state in _PENDING_WORDS | _SLOW_WORDS:
                    with _lock:
                        if state in _SLOW_WORDS:
                            session.interval = min(MAX_INTERVAL, session.interval + SLOW_DOWN_STEP)
                            session.next_poll_at = _now() + timedelta(seconds=session.interval)
                        return session.view(), None
                result = drive_auth._result_bundle(raw, captured, store)
                outcome = apply(result, store)
        except DriveAuthError as error:
            status = EXPIRED if error.code in _EXPIRED_CODES else FAILED
            message = drive_auth.AUTH_ERRORS["AUTH_DEVICE_EXPIRED"][1] if error.code == "NOT_FOUND" else error.message
            code = "AUTH_DEVICE_EXPIRED" if error.code == "NOT_FOUND" else error.code
            with _lock:
                if session.status == PENDING:
                    _finish_locked(session, status, code, message, **error.extra)
                return session.view(), session.status
    finally:
        drive_auth.operation_lock.release()
    with _lock:
        session.account = outcome.get("account_hint")
        session.queue_resumed = int(outcome.get("queue_resumed") or 0)
        _finish_locked(session, SUCCEEDED, None, None)
        return session.view(), SUCCEEDED


def _has_token(raw: Any) -> bool:
    return raw is not None and not isinstance(raw, dict) and bool(getattr(raw, "access_token", None))


def _poll_adapter(gateway: Any, store: Any, login_id: str) -> tuple[Any, list[Any]]:
    try:
        method = drive_auth._method(gateway, "device_login_poll")
        if store is None:
            return method(login_id), []
        with store.capturing(strict=True) as captured:
            raw = method(login_id)
            state = str(_field(raw, "status") or "").strip().upper() if raw is not None else ""
            if not _has_token(raw) and not captured and state not in _PENDING_WORDS | _SLOW_WORDS:
                store.wait_captured(drive_auth.CAPTURE_WAIT_SECONDS)
            return raw, list(captured)
    except DriveAuthError:
        raise
    except Exception as error:  # noqa: BLE001 - only the code leaves this function
        raise drive_auth._adapter_failure(gateway, "device_login_poll", error) from None
