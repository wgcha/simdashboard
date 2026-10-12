"""SCX drive re-login from the dashboard (issue #50, docs/plans/scx-drive-relogin-spec.md §3).

* :func:`capabilities` / :func:`refresh_now` / :func:`login_password` call the
  adapter ``WorkerDriveGateway`` methods of the same names (worker ops
  ``auth_capabilities`` / ``refresh_now`` / ``login_password``, spec §2.2).  An
  adapter without them (``AttributeError``/``NotImplementedError`` or a worker
  "unknown op" answer) is reported as ``adapter_supported=False``: the screen
  then shows "어댑터 업데이트 필요 (이슈 #50)" and only the token paste.
* The password exists only as a local of the request handler and of
  :func:`login_password`; it is passed straight to the gateway and never logged,
  stored, audited, notified or echoed (errors carry codes only).
* :class:`AttemptLimiter` — ``SIMDASH_DRIVE_LOGIN_MAX_ATTEMPTS`` (default 3) account
  logins per 15 minutes for the whole dashboard, then a 15-minute lock.  Kept in
  process memory: scx mode runs a single dashboard process (``dashboard.lock``), so
  the limit is global; a restart clears it (documented in docs/features/scx-drive.md §6).
* :func:`auto_refresh_once` — one ``refresh_now`` per ``AUTH_REQUIRED`` episode
  (a new episode starts only after a later successful drive call or a manual
  re-login), only when the adapter reports ``refresh=True``.  Callers hold no DB
  connection (the read path and the upload queue already guarantee this).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from . import gateway as drive_gateway
from .token_store import LocalTokenBundle, same_account

logger = logging.getLogger("app.services.drive.auth")

WINDOW_SECONDS = 15 * 60
CAPTURE_WAIT_SECONDS = 5.0
NO_CAPABILITIES = {"refresh": False, "password": False, "mfa": False}

# Adapter auth error codes (spec §2.4) → HTTP status and Korean text.  Never the SSO message itself.
AUTH_ERRORS: dict[str, tuple[int, str]] = {
    "AUTH_INVALID_CREDENTIALS": (401, "아이디 또는 비밀번호가 맞지 않습니다."),
    "AUTH_ACCOUNT_LOCKED": (409, "HPC 계정이 잠겼거나 비활성 상태입니다. HPC 관리자에게 문의하세요."),
    "AUTH_MFA_REQUIRED": (409, "이 계정은 추가 인증(OTP)이 필요해 대시보드에서 로그인할 수 없습니다."),
    "AUTH_METHOD_UNSUPPORTED": (409, "HPC SSO가 아이디·비밀번호 로그인을 허용하지 않습니다. 토큰 붙여넣기를 사용하세요."),
    "AUTH_REFRESH_EXPIRED": (409, "저장된 토큰이 만료되었습니다. 계정으로 로그인하세요."),
    "RATE_LIMITED": (429, "HPC SSO가 시도 횟수를 제한했습니다. 잠시 후 다시 시도하세요."),
    "AUTH_REQUIRED": (409, "저장된 토큰으로 연결할 수 없습니다. 계정으로 로그인하세요."),
}


class DriveAuthError(Exception):
    """A re-login failure as (HTTP status, code, Korean message); never carries secrets."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


class AdapterUnsupported(Exception):
    """The installed adapter has no re-login methods (issue #50 not implemented there yet)."""


@dataclass
class AuthResult:
    bundle: LocalTokenBundle
    expires_at: datetime | None = None


@dataclass
class _Status:
    lock: threading.Lock = field(default_factory=threading.Lock)
    capabilities: dict[str, bool] | None = None
    capabilities_gateway: int | None = None
    adapter_supported: bool | None = None
    last_refresh_at: datetime | None = None
    expires_at: datetime | None = None
    last_error_code: str | None = None
    last_error_at: datetime | None = None
    auto_attempted_at: datetime | None = None


_status = _Status()
# One re-login at a time (reconnect, login, automatic refresh): the worker serializes too.
operation_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reset_state() -> None:
    """Drive shutdown (process lifecycle): forget capabilities, timestamps and the attempt window."""
    global _status
    _status = _Status()
    limiter.reset()


def forget_capabilities() -> None:
    """The gateway was replaced: ask the new worker again."""
    with _status.lock:
        _status.capabilities, _status.capabilities_gateway, _status.adapter_supported = None, None, None


# --- attempt limit --------------------------------------------------------------------------------

class AttemptLimiter:
    """Sliding 15-minute window of account-login attempts (process memory, single scx process)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: deque[float] = deque()

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()

    def _prune(self, now: float) -> None:
        while self._attempts and now - self._attempts[0] >= WINDOW_SECONDS:
            self._attempts.popleft()

    def state(self, maximum: int) -> tuple[int, datetime | None]:
        """(attempts left, locked until) without recording anything."""
        with self._lock:
            now = time.time()
            self._prune(now)
            left = max(0, maximum - len(self._attempts))
            if left:
                return left, None
            until = self._attempts[len(self._attempts) - maximum] + WINDOW_SECONDS
            return 0, datetime.fromtimestamp(until, timezone.utc)

    def take(self, maximum: int) -> float:
        """Record one attempt or raise 429 ``DRIVE_LOGIN_LOCKED``; returns the attempt stamp."""
        with self._lock:
            now = time.time()
            self._prune(now)
            if len(self._attempts) >= maximum:
                until = datetime.fromtimestamp(self._attempts[len(self._attempts) - maximum] + WINDOW_SECONDS, timezone.utc)
                raise DriveAuthError(429, "DRIVE_LOGIN_LOCKED",
                                     f"로그인 시도가 15분에 {maximum}회를 넘어 잠시 잠갔습니다(HPC 계정 잠금 방지). "
                                     "잠금이 풀린 뒤 다시 시도하세요.", locked_until=until.isoformat())
            self._attempts.append(now)
            return now

    def refund(self, stamp: float) -> None:
        """An attempt that never reached the SSO (adapter without the method) does not count."""
        with self._lock:
            kept = [value for value in self._attempts if value != stamp]
            self._attempts = deque(kept)

    def clear(self) -> None:
        """A successful login ends the failure window."""
        self.reset()


limiter = AttemptLimiter()


# --- adapter calls --------------------------------------------------------------------------------

_UNKNOWN_OP_HINTS = ("unknown op", "unsupported op", "unknown operation", "unsupported operation", "no such op")


def _unsupported(error: BaseException) -> bool:
    if isinstance(error, (AttributeError, NotImplementedError)):
        return True
    if drive_gateway.drive_error_code(error) in {"INTERNAL", "INVALID_PATH", None}:
        text = str(getattr(error, "message", None) or error).casefold()
        return any(hint in text for hint in _UNKNOWN_OP_HINTS)
    return False


def _method(gateway: Any, name: str) -> Any:
    method = getattr(gateway, name, None)
    if not callable(method):
        raise AdapterUnsupported(name)
    return method


def capabilities(gateway: Any | None = None) -> tuple[dict[str, bool], bool, str | None]:
    """(capabilities, adapter_supported, error code); cached per gateway instance (no SDK call)."""
    if gateway is None:
        try:
            gateway = drive_gateway.get_drive_gateway()
        except drive_gateway.DriveStartupError:
            return dict(NO_CAPABILITIES), True, "DRIVE_UNAVAILABLE"
        except Exception as error:  # noqa: BLE001 - status must not fail
            return dict(NO_CAPABILITIES), True, drive_gateway.drive_error_code(error) or "DRIVE_UNAVAILABLE"
    if gateway is None:
        return dict(NO_CAPABILITIES), True, "DRIVE_MODE_DISABLED"
    with _status.lock:
        if _status.capabilities is not None and _status.capabilities_gateway == id(gateway):
            return dict(_status.capabilities), bool(_status.adapter_supported), None
    try:
        raw = _method(gateway, "auth_capabilities")()
    except AdapterUnsupported:
        return _remember_capabilities(gateway, dict(NO_CAPABILITIES), False), False, "DRIVE_ADAPTER_UPDATE_REQUIRED"
    except Exception as error:  # noqa: BLE001
        if _unsupported(error):
            return _remember_capabilities(gateway, dict(NO_CAPABILITIES), False), False, "DRIVE_ADAPTER_UPDATE_REQUIRED"
        logger.warning("SCX drive auth_capabilities failed (%s)", drive_gateway.drive_error_code(error) or type(error).__name__)
        return dict(NO_CAPABILITIES), True, drive_gateway.drive_error_code(error) or "INTERNAL"
    caps = {name: bool((raw or {}).get(name, False)) if isinstance(raw, dict) else bool(getattr(raw, name, False))
            for name in ("refresh", "password", "mfa")}
    return _remember_capabilities(gateway, caps, True), True, None


def _remember_capabilities(gateway: Any, caps: dict[str, bool], supported: bool) -> dict[str, bool]:
    with _status.lock:
        _status.capabilities, _status.capabilities_gateway, _status.adapter_supported = dict(caps), id(gateway), supported
    return caps


def _stamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            from .token_store import parse_obtained_at

            return parse_obtained_at(value)
        except (ValueError, TypeError):
            return None
    return None


def _result_bundle(result: Any, captured: list[LocalTokenBundle], store: Any) -> AuthResult:
    """The new TokenBundle: the gateway's return value, else the worker's ``tokens`` notification."""
    expires_at = _stamp(result.get("expires_at") if isinstance(result, dict) else getattr(result, "expires_at", None))
    if result is not None and not isinstance(result, dict) and getattr(result, "access_token", None):
        return AuthResult(store.to_local(result) if store is not None else _local(result), expires_at)
    if captured:
        return AuthResult(captured[-1], expires_at)
    raise DriveAuthError(502, "DRIVE_INTERNAL", "어댑터가 새 토큰을 전달하지 않았습니다(어댑터 업데이트 필요, 이슈 #50).")


def _local(bundle: Any) -> LocalTokenBundle:
    from .token_store import DbTokenStore

    return DbTokenStore.to_local(bundle)


def _call(name: str, *args: Any) -> AuthResult:
    """Run one adapter re-login method on the current gateway; maps errors to :class:`DriveAuthError`."""
    try:
        gateway = drive_gateway.get_drive_gateway()
    except drive_gateway.DriveStartupError as error:
        raise DriveAuthError(503, "DRIVE_UNAVAILABLE", str(error)[:200]) from None
    if gateway is None:
        raise DriveAuthError(409, "DRIVE_MODE_DISABLED", "SCX 드라이브 모드가 아닙니다.")
    store = drive_gateway.gateway_token_store()
    try:
        method = _method(gateway, name)
        if store is None:
            result = method(*args)
            captured: list[LocalTokenBundle] = []
        else:
            with store.capturing(strict=name == "login_password") as captured:
                result = method(*args)
                if not (result is not None and not isinstance(result, dict) and getattr(result, "access_token", None)) \
                        and not captured:
                    store.wait_captured(CAPTURE_WAIT_SECONDS)
        return _result_bundle(result, list(captured), store)
    except DriveAuthError:
        raise
    except AdapterUnsupported:
        _remember_capabilities(gateway, dict(NO_CAPABILITIES), False)
        raise DriveAuthError(409, "DRIVE_ADAPTER_UPDATE_REQUIRED",
                             "설치된 드라이브 어댑터가 재연결을 지원하지 않습니다. 어댑터 업데이트 필요 (이슈 #50).") from None
    except Exception as error:  # noqa: BLE001 - only the code leaves this function
        if _unsupported(error):
            _remember_capabilities(gateway, dict(NO_CAPABILITIES), False)
            raise DriveAuthError(409, "DRIVE_ADAPTER_UPDATE_REQUIRED",
                                 "설치된 드라이브 어댑터가 재연결을 지원하지 않습니다. 어댑터 업데이트 필요 (이슈 #50).") from None
        code = drive_gateway.drive_error_code(error) or "INTERNAL"
        _record_error(code)
        if code in AUTH_ERRORS:
            status, message = AUTH_ERRORS[code]
            raise DriveAuthError(status, code, message) from None
        mapping = drive_gateway.DRIVE_ERROR_MAP.get(code, drive_gateway.DRIVE_ERROR_MAP["INTERNAL"])
        raise DriveAuthError(mapping.http_status, mapping.storage_code, "드라이브 인증 서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.") from None


def _record_error(code: str) -> None:
    with _status.lock:
        _status.last_error_code, _status.last_error_at = code, _now()


def refresh_now() -> AuthResult:
    """``refresh_now`` keeps the stored refresh-token chain, hence the account: an adapter that leaves
    ``account_hint`` empty is treated as the connected account (a *different* name is still checked)."""
    result = _call("refresh_now")
    if not (result.bundle.account_hint or "").strip():
        store = drive_gateway.token_store()
        try:
            connected = store.connected_account() if store is not None else None
        except Exception:  # noqa: BLE001
            connected = None
        if connected:
            result = AuthResult(replace(result.bundle, account_hint=connected), result.expires_at)
    return result


def login_password(username: str, password: str) -> AuthResult:
    """Hand the password to the worker (stdin JSON) once; the caller drops its reference right after."""
    try:
        return _call("login_password", username, password)
    finally:
        del password


def record_success(result: AuthResult) -> None:
    with _status.lock:
        _status.last_refresh_at = _now()
        _status.expires_at = result.expires_at
        _status.last_error_code = None
        _status.last_error_at = None
        _status.auto_attempted_at = None   # a new AUTH_REQUIRED episode may try refresh_now again


def record_registered() -> None:
    """Token paste (PUT /credentials): earlier re-login errors no longer apply."""
    with _status.lock:
        _status.last_error_code = None
        _status.last_error_at = None
        _status.auto_attempted_at = None


def record_failure(code: str) -> None:
    _record_error(code)


def snapshot() -> dict[str, Any]:
    with _status.lock:
        def stamp(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {"last_refresh_at": stamp(_status.last_refresh_at), "expires_at": stamp(_status.expires_at),
                "last_error_code": _status.last_error_code, "last_error_at": stamp(_status.last_error_at)}


# --- automatic refresh (spec §3.5) ------------------------------------------------------------------

def _last_success(gateway: Any) -> datetime | None:
    try:
        return _stamp(getattr(gateway.health(), "last_success_at", None))
    except Exception:  # noqa: BLE001
        return None


def auto_refresh_once(gateway: Any) -> bool:
    """One ``refresh_now`` for this AUTH_REQUIRED episode; True when the caller may retry its call once.

    Conservative: never with a different account (the gateway is reset so the
    worker drops it), never while a manual re-login runs, never when the adapter
    lacks ``refresh``, and at most once until a drive call succeeds again.
    """
    with _status.lock:
        attempted = _status.auto_attempted_at
    if attempted is not None:
        succeeded = _last_success(gateway)
        if succeeded is None or succeeded <= attempted:
            return False
    caps, supported, _error = capabilities(gateway)
    if not supported or not caps.get("refresh"):
        return False
    if not operation_lock.acquire(blocking=False):
        return False
    try:
        with _status.lock:
            if _status.auto_attempted_at is not None and _status.auto_attempted_at != attempted:
                return False          # another thread started this episode's attempt
            _status.auto_attempted_at = _now()
        try:
            result = refresh_now()
        except DriveAuthError as error:
            logger.warning("SCX drive automatic token refresh failed (%s)", error.code)
            return False
        store = drive_gateway.token_store()
        try:
            connected = store.connected_account() if store is not None else None
        except Exception:  # noqa: BLE001
            connected = None
        received = result.bundle.account_hint
        if connected and (not received or not same_account(received, connected)):
            logger.warning("SCX drive automatic refresh returned another account; the worker session is reset")
            gateway_store = drive_gateway.gateway_token_store()
            if gateway_store is not None:
                gateway_store.detach()
            drive_gateway.reset_gateway()
            _record_error("DRIVE_ACCOUNT_CHANGE_CONFIRM")
            return False
        with _status.lock:
            _status.last_refresh_at = _now()
            _status.expires_at = result.expires_at
        logger.info("SCX drive token refreshed automatically after AUTH_REQUIRED")
        return True
    finally:
        operation_lock.release()
