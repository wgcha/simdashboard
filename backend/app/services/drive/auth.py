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
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from . import gateway as drive_gateway
from .token_store import LocalTokenBundle, same_account

logger = logging.getLogger("app.services.drive.auth")

WINDOW_SECONDS = 15 * 60
CAPTURE_WAIT_SECONDS = 5.0
NO_CAPABILITIES = {"refresh": False, "password": False, "mfa": False, "device": False}
CAPABILITY_NAMES = ("refresh", "password", "mfa", "device")

# Adapter auth error codes (spec §2.4) → HTTP status and Korean text.  Never the SSO message itself.
AUTH_ERRORS: dict[str, tuple[int, str]] = {
    "AUTH_INVALID_CREDENTIALS": (401, "아이디 또는 비밀번호가 맞지 않습니다."),
    "AUTH_ACCOUNT_LOCKED": (409, "HPC 계정이 잠겼거나 비활성 상태입니다. HPC 관리자에게 문의하세요."),
    "AUTH_MFA_REQUIRED": (409, "이 계정은 추가 인증(OTP)이 필요해 대시보드에서 로그인할 수 없습니다."),
    "AUTH_METHOD_UNSUPPORTED": (409, "HPC SSO가 아이디·비밀번호 로그인을 허용하지 않습니다. 토큰 붙여넣기를 사용하세요."),
    "AUTH_REFRESH_EXPIRED": (409, "저장된 토큰이 만료되었습니다. 계정으로 로그인하세요."),
    "RATE_LIMITED": (429, "HPC SSO가 시도 횟수를 제한했습니다. 잠시 후 다시 시도하세요."),
    "AUTH_REQUIRED": (409, "저장된 토큰으로 연결할 수 없습니다. 계정으로 로그인하세요."),
    # Device-code login (2026-10-14, spec §7): the code ran out or the approver refused it.
    "AUTH_DEVICE_EXPIRED": (409, "활성화 코드가 만료되었습니다. [코드로 로그인]을 다시 시작하세요."),
    "AUTH_DEVICE_DENIED": (409, "인증 페이지에서 승인을 거부했습니다. 다시 시작하세요."),
}


class DriveAuthError(Exception):
    """A re-login failure as (HTTP status, code, Korean message); never carries secrets.

    ``detail`` is the adapter's sanitized message (logs and the transient/definitive decision only;
    never part of an HTTP answer)."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra
        self.detail = ""


# Issue #51 (2026-10-23): a refresh that failed because the SSO answered 5xx (``BAD_GATEWAY``), timed out
# or could not be reached says nothing about the refresh token.  Such a failure is retried later and
# never treated as "re-login required"; only a definitive refusal (expired/invalid grant) is.
TRANSIENT_REFRESH_SECONDS = 30.0
DEFINITIVE_REFRESH_CODES = frozenset({"AUTH_REFRESH_EXPIRED", "AUTH_INVALID_CREDENTIALS", "AUTH_ACCOUNT_LOCKED",
                                      "AUTH_MFA_REQUIRED", "AUTH_METHOD_UNSUPPORTED", "AUTH_REQUIRED",
                                      "AUTH_DEVICE_EXPIRED", "AUTH_DEVICE_DENIED",
                                      "DRIVE_ADAPTER_UPDATE_REQUIRED", "DRIVE_MODE_DISABLED",
                                      "DRIVE_ACCOUNT_CHANGE_CONFIRM"})
_TRANSIENT_HINT = re.compile(r"(?i)\b50[0-9]\b|bad[ _-]?gateway|service[ _-]unavailable|gateway[ _-]time-?out|"
                             r"timed? ?out|temporar|connection (?:reset|refused|aborted|error)|unreachable")
# The worker answered refresh_now without any refresh token in memory (adapter: "no stored token to
# refresh") while the dashboard still holds the encrypted token: the worker lost its state (restart).
_NO_WORKER_TOKEN = re.compile(r"(?i)no (?:stored |refresh |registered )?token")


def refresh_failure_transient(error: DriveAuthError) -> bool:
    """True when a failed refresh may succeed later with the same refresh token (5xx, timeout, network)."""
    if error.code == "RATE_LIMITED":
        return True
    if error.code in DEFINITIVE_REFRESH_CODES:
        # An adapter that reports an SSO 5xx as an auth code: its message still names the gateway error.
        return error.code in {"AUTH_REQUIRED", "AUTH_REFRESH_EXPIRED"} and bool(_TRANSIENT_HINT.search(error.detail or ""))
    return True   # DRIVE_UNAVAILABLE / DRIVE_TIMEOUT / DRIVE_BUSY / DRIVE_INTERNAL: nothing says the token is bad


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
    transient_until: float | None = None      # monotonic: no automatic refresh before (5xx/network backoff)


_status = _Status()
# One re-login at a time (reconnect, login, automatic refresh): the worker serializes too.
operation_lock = threading.Lock()
# Set while a manual reconnect/login runs until its account check finished: the worker may then hold
# tokens of an unconfirmed account, so drive reads and queue writes wait (or retry later) meanwhile.
_switch_idle = threading.Event()
_switch_idle.set()
SWITCH_WAIT_SECONDS = 45.0


@contextmanager
def account_switch() -> Iterator[None]:
    _switch_idle.clear()
    try:
        yield
    finally:
        _switch_idle.set()


def account_switch_active() -> bool:
    return not _switch_idle.is_set()


def wait_account_switch(timeout: float | None = None) -> bool:
    """True when no account switch is pending (after waiting up to ``timeout`` seconds)."""
    return _switch_idle.wait(max(0.0, SWITCH_WAIT_SECONDS if timeout is None else timeout))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reset_state() -> None:
    """Drive shutdown (process lifecycle): forget capabilities, timestamps and the attempt window."""
    global _status
    _status = _Status()
    limiter.reset()
    from . import device_login   # the waiting activation code belonged to the stopped worker

    device_login.reset()


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
            for name in CAPABILITY_NAMES}
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


def _current_gateway() -> Any:
    try:
        gateway = drive_gateway.get_drive_gateway()
    except drive_gateway.DriveStartupError as error:
        raise DriveAuthError(503, "DRIVE_UNAVAILABLE", str(error)[:200]) from None
    if gateway is None:
        raise DriveAuthError(409, "DRIVE_MODE_DISABLED", "SCX 드라이브 모드가 아닙니다.")
    return gateway


def _adapter_failure(gateway: Any, name: str, error: BaseException, redact: tuple[str, ...] = ()) -> DriveAuthError:
    """One adapter re-login error as :class:`DriveAuthError` (logged with the adapter code)."""
    if isinstance(error, DriveAuthError):
        return error
    if isinstance(error, AdapterUnsupported) or _unsupported(error):
        _remember_capabilities(gateway, dict(NO_CAPABILITIES), False)
        return DriveAuthError(409, "DRIVE_ADAPTER_UPDATE_REQUIRED",
                              "설치된 드라이브 어댑터가 이 로그인 방식을 지원하지 않습니다. 어댑터 업데이트 필요 (이슈 #50).")
    adapter_code = drive_gateway.drive_error_code(error)
    code = adapter_code or "INTERNAL"
    detail = drive_gateway.safe_error_message(error)
    for value in redact:
        if value:
            detail = "***".join(detail.split(value))
    logger.warning("SCX drive %s failed: code=%s type=%s message=%s", name, adapter_code or "-",
                   type(error).__name__, detail)
    _record_error(code)
    if code in AUTH_ERRORS:
        status, message = AUTH_ERRORS[code]
        failure = DriveAuthError(status, code, message)
        failure.detail = detail
        return failure
    mapping = drive_gateway.DRIVE_ERROR_MAP.get(code, drive_gateway.DRIVE_ERROR_MAP["INTERNAL"])
    shown = adapter_code or type(error).__name__
    failure = DriveAuthError(mapping.http_status, mapping.storage_code,
                             f"드라이브 인증 서버에 연결하지 못했습니다(어댑터 코드: {shown}). 서버 로그(uvicorn-error.log)의 "
                             f"'SCX drive {name} failed' 줄에 원인이 있습니다.", adapter_code=shown)
    failure.detail = detail
    return failure


def _call(name: str, *args: Any, redact: tuple[str, ...] = ()) -> AuthResult:
    """Run one adapter re-login method on the current gateway; maps errors to :class:`DriveAuthError`.

    Failures are logged with the adapter code and its (sanitized) message so the admin can tell an
    unreachable SSO from a refused login; ``redact`` values (the password) are masked once more.
    """
    gateway = _current_gateway()
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
    except Exception as error:  # noqa: BLE001 - only the code leaves this function
        raise _adapter_failure(gateway, name, error, redact) from None


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
            old = result.bundle
            named = LocalTokenBundle(server_url=old.server_url, access_token=old.access_token,
                                     refresh_token=old.refresh_token, obtained_at=old.obtained_at,
                                     account_hint=connected)
            result = AuthResult(named, result.expires_at)
    return result


def login_password(username: str, password: str) -> AuthResult:
    """Hand the password to the worker (stdin JSON) once; the caller drops its reference right after."""
    try:
        return _call("login_password", username, password, redact=(password,))
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
    # Cached (issue #51): an AUTH_REQUIRED episode must not turn every failing read into a worker round-trip.
    health = drive_gateway.cached_health(gateway, max_age=drive_gateway.HEALTH_EPISODE_SECONDS)
    return _stamp((health or {}).get("last_success_at"))


REFRESHED = "REFRESHED"     # the caller may run its call once more (new token, or a reloaded worker)
TRANSIENT = "TRANSIENT"     # refresh failed for a passing reason: report the call as retryable, not as re-login
NOT_REFRESHED = "NO"        # re-login required (or refresh not possible / already tried in this episode)


def auto_refresh_once(gateway: Any) -> bool:
    """One ``refresh_now`` for this AUTH_REQUIRED episode; True when the caller may retry its call once."""
    return auto_refresh(gateway) == REFRESHED


def auto_refresh(gateway: Any) -> str:
    """One ``refresh_now`` for this AUTH_REQUIRED episode (:data:`REFRESHED`/:data:`TRANSIENT`/:data:`NOT_REFRESHED`).

    Conservative: never with a different account (the gateway is reset so the
    worker drops it), never while a manual re-login runs, never when the adapter
    lacks ``refresh``, and at most once until a drive call succeeds again.
    Issue #51: a transient failure (SSO 5xx such as ``BAD_GATEWAY``, timeout, network) does not use up
    the episode and never touches the stored token; the next attempt waits ``TRANSIENT_REFRESH_SECONDS``.
    A worker that has no token to refresh while the encrypted row still holds one is rebuilt once from the row.
    """
    with _status.lock:
        attempted = _status.auto_attempted_at
        backoff = _status.transient_until
    if backoff is not None and time.monotonic() < backoff:
        return TRANSIENT
    if attempted is not None:
        # A new episode needs a successful drive call after the last attempt *and* after the
        # refresh it produced (an adapter may count refresh_now itself as a success).
        succeeded = _last_success(gateway)
        with _status.lock:
            refreshed = _status.last_refresh_at
        floor = max(attempted, refreshed) if refreshed else attempted
        if succeeded is None or succeeded <= floor:
            return NOT_REFRESHED
    caps, supported, _error = capabilities(gateway)
    if not supported or not caps.get("refresh"):
        return NOT_REFRESHED
    if not operation_lock.acquire(blocking=False):
        return NOT_REFRESHED
    try:
        with _status.lock:
            if _status.auto_attempted_at is not None and _status.auto_attempted_at != attempted:
                return NOT_REFRESHED  # another thread started this episode's attempt
            _status.auto_attempted_at = _now()
        try:
            result = refresh_now()
        except DriveAuthError as error:
            if refresh_failure_transient(error):
                with _status.lock:
                    _status.auto_attempted_at = attempted      # the episode is not used up
                    _status.transient_until = time.monotonic() + TRANSIENT_REFRESH_SECONDS
                logger.warning("SCX drive automatic token refresh failed transiently (%s); the stored token is kept "
                               "and the refresh is retried after %d s", error.code, int(TRANSIENT_REFRESH_SECONDS))
                return TRANSIENT
            if _reload_worker_tokens(error):
                return REFRESHED
            logger.warning("SCX drive automatic token refresh failed (%s)", error.code)
            return NOT_REFRESHED
        with _status.lock:
            _status.transient_until = None
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
            return NOT_REFRESHED
        with _status.lock:
            _status.last_refresh_at = _now()
            _status.expires_at = result.expires_at
        logger.info("SCX drive token refreshed automatically after AUTH_REQUIRED")
        return REFRESHED
    finally:
        operation_lock.release()


def _reload_worker_tokens(error: DriveAuthError) -> bool:
    """The worker had no token to refresh, but the encrypted row has one: rebuild the gateway from the row.

    At most once per AUTH_REQUIRED episode (the caller already used the episode).  The stored row is
    only read here, never changed.
    """
    if error.code not in {"AUTH_REFRESH_EXPIRED", "AUTH_REQUIRED"} or not _NO_WORKER_TOKEN.search(error.detail or ""):
        return False
    store = drive_gateway.token_store()
    try:
        stored = store.load_local() if store is not None else None
    except Exception:  # noqa: BLE001
        stored = None
    if stored is None or not stored.refresh_token:
        return False
    logger.warning("SCX drive worker had no token to refresh (%s) while the encrypted token is stored; "
                   "the worker is restarted with the stored token", error.code)
    drive_gateway.reset_gateway()
    return True
