from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock
from time import monotonic
from uuid import uuid4

from ..database_connection import ConnectionLike
from ..security import hash_password, verify_password


class UsernameAlreadyRegisteredError(Exception):
    pass


class PasswordChangeResult(StrEnum):
    CHANGED = "changed"
    NOT_PASSWORD_ACCOUNT = "not_password_account"
    CURRENT_PASSWORD_INVALID = "current_password_invalid"
    CONCURRENT_CHANGE = "concurrent_change"


@dataclass(frozen=True)
class RegisteredAccount:
    user_id: str
    username: str
    display_name: str


class RegistrationAttemptLimiter:
    """Small in-process guard for the public registration endpoint.

    The application has no shared rate-limit backend today.  This bounds
    accidental or opportunistic bursts per application worker without adding
    an external dependency; production deployments should also enforce a
    perimeter limit shared by all workers.
    """

    def __init__(self, *, limit: int = 5, window_seconds: int = 60, max_keys: int = 2048) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = monotonic()
        with self._lock:
            cutoff = now - self._window_seconds
            for stale_key, stale_attempts in list(self._attempts.items()):
                while stale_attempts and stale_attempts[0] <= cutoff:
                    stale_attempts.popleft()
                if not stale_attempts:
                    del self._attempts[stale_key]
            attempts = self._attempts.get(key)
            if attempts is None:
                if len(self._attempts) >= self._max_keys:
                    self._attempts.popitem(last=False)
                attempts = deque()
                self._attempts[key] = attempts
            else:
                self._attempts.move_to_end(key)
            if len(attempts) >= self._limit:
                return False
            attempts.append(now)
            return True


registration_attempt_limiter = RegistrationAttemptLimiter()


def register_password_account(
    connection: ConnectionLike, *, username: str, display_name: str, password: str
) -> RegisteredAccount:
    """Create an unprivileged password account pending administrator approval."""

    existing = connection.execute("SELECT 1 FROM users WHERE username=?", [username]).fetchone()
    if existing:
        raise UsernameAlreadyRegisteredError
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    account = RegisteredAccount(
        user_id=f"user-{uuid4().hex}",
        username=username,
        display_name=display_name,
    )
    try:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, NULL, true, ?, ?, 'PENDING', false)
            """,
            [
                account.user_id,
                account.username,
                hash_password(password),
                account.display_name,
                now,
                now,
            ],
        )
    except Exception as exc:
        if _is_unique_violation(exc):
            raise UsernameAlreadyRegisteredError from exc
        raise
    return account


def change_password(
    connection: ConnectionLike, *, user_id: str, current_password: str, new_password: str
) -> PasswordChangeResult:
    """Replace a password only after verifying the account's existing password."""

    row = connection.execute("SELECT password_hash FROM users WHERE id=?", [user_id]).fetchone()
    password_hash = row[0] if row else None
    if not password_hash:
        return PasswordChangeResult.NOT_PASSWORD_ACCOUNT
    if not verify_password(current_password, password_hash):
        return PasswordChangeResult.CURRENT_PASSWORD_INVALID
    changed = connection.execute(
        """
        UPDATE users SET password_hash=?, updated_at=?, must_change_password=false
        WHERE id=? AND password_hash=?
        RETURNING id
        """,
        [hash_password(new_password), datetime.now(timezone.utc).replace(tzinfo=None), user_id, password_hash],
    ).fetchone()
    if not changed:
        return PasswordChangeResult.CONCURRENT_CHANGE
    return PasswordChangeResult.CHANGED


def _is_unique_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in ("duplicate", "unique constraint", "unique violation", "23505"))


TEMPORARY_PASSWORD_LENGTH = 14
_TEMPORARY_SPECIALS = "!@#$%^&*-_=+"
_TEMPORARY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def temporary_password() -> str:
    """One-time password for an administrator reset: letters, digits and a special character (policy)."""
    import secrets

    while True:
        chars = [secrets.choice(_TEMPORARY_ALPHABET) for _ in range(TEMPORARY_PASSWORD_LENGTH - 2)]
        chars += [secrets.choice("23456789"), secrets.choice(_TEMPORARY_SPECIALS)]
        secrets.SystemRandom().shuffle(chars)
        candidate = "".join(chars)
        if any(ch.isalpha() for ch in candidate):
            return candidate


class PasswordResetResult(StrEnum):
    RESET = "reset"
    NOT_FOUND = "not_found"
    NOT_PASSWORD_ACCOUNT = "not_password_account"
    STALE = "stale"


def reset_password_by_admin(
    connection: ConnectionLike, *, user_id: str, expected_updated_at: datetime, new_password: str
) -> tuple[PasswordResetResult, datetime | None]:
    """Replace the password with ``new_password`` and require a change at the next use (optimistic check)."""
    row = connection.execute("SELECT password_hash, updated_at FROM users WHERE id=?", [user_id]).fetchone()
    if not row:
        return PasswordResetResult.NOT_FOUND, None
    if not row[0]:
        return PasswordResetResult.NOT_PASSWORD_ACCOUNT, None
    expected = expected_updated_at.replace(tzinfo=None) if expected_updated_at.tzinfo is None else \
        expected_updated_at.astimezone(timezone.utc).replace(tzinfo=None)
    current = row[1]
    if current is not None and getattr(current, "tzinfo", None) is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    if current != expected:
        return PasswordResetResult.STALE, None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    changed = connection.execute(
        "UPDATE users SET password_hash=?, must_change_password=true, sessions_revoked_at=?, updated_at=? "
        "WHERE id=? AND updated_at=? RETURNING id",
        [hash_password(new_password), now, now, user_id, row[1]],
    ).fetchone()
    if not changed:
        return PasswordResetResult.STALE, None
    return PasswordResetResult.RESET, now
