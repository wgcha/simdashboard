"""Server-local first password administrator setup.

This command deliberately has no HTTP entrypoint.  It is safe to re-run: it
never changes existing users, roles, IDs, or passwords, and only creates an
administrator when no active password administrator exists.
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import errno
import getpass
import json
import os
import re
import secrets
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values, load_dotenv


ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
INITIAL_ENVIRONMENT = dict(os.environ)
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,80}$")
PASSWORD_MAX_LENGTH = 256
PASSWORD_MIN_LENGTH = 8
PROMPT_ATTEMPTS = 3
_setup_lock_handle = None
_setup_stage = "entrypoint"

_REMEDIATION = {
    "ACCOUNT_SETUP_FAILED_INPUT_USERNAME": "Enter a unique 3-80 character username and retry.",
    "ACCOUNT_SETUP_FAILED_INPUT_DISPLAY_NAME": "Enter a display name of 1-200 characters and retry.",
    "ACCOUNT_SETUP_FAILED_INPUT_PASSWORD_MISMATCH": "Enter the same password in both password fields and retry.",
    "ACCOUNT_SETUP_FAILED_INPUT_PASSWORD_LENGTH": "Enter a password between 8 and 256 characters and retry.",
    "ACCOUNT_SETUP_FAILED_INPUT_USERNAME_DUPLICATE": "Choose an unused username; existing users are never changed by setup.",
    "ACCOUNT_SETUP_FAILED_DATABASE_IN_PROGRESS": "Another administrator setup is running or holds the database lock; wait for it to finish and retry.",
    "ACCOUNT_SETUP_FAILED_DATABASE_SCHEMA": "Apply the database migration, then retry account setup.",
    "ACCOUNT_SETUP_FAILED_DATABASE_CONNECTION_OR_AUTHENTICATION": "Verify the database service and configured credentials, then retry.",
    "ACCOUNT_SETUP_FAILED_DATABASE_PRIVILEGE": "Grant the configured database role permission to create the initial administrator, then retry.",
    "ACCOUNT_SETUP_FAILED_ENVIRONMENT_OVERRIDE": "Update the conflicting server environment setting, then retry.",
    "ACCOUNT_SETUP_FAILED_ENVIRONMENT_FILE_ACL": "Check the .env file access controls and retry.",
    "ACCOUNT_SETUP_FAILED_UNKNOWN": "Review the server configuration and database migration, then retry.",
}


class SetupInputError(RuntimeError):
    """A bounded user-input failure whose message is never shown by the CLI."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class SetupInProgressError(RuntimeError):
    pass


def _set_stage(stage: str) -> None:
    global _setup_stage
    _setup_stage = stage


def _read_env(path: Path) -> dict[str, str]:
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def _effective(name: str, values: dict[str, str]) -> str | None:
    return os.environ.get(name, values.get(name))


def _is_external_override(name: str, values: dict[str, str]) -> bool:
    # dotenv may have populated os.environ after process start. Only values
    # inherited by this process are immutable external overrides.
    return name in INITIAL_ENVIRONMENT


def _write_env(updates: dict[str, str], values: dict[str, str]) -> None:
    for name, value in updates.items():
        if _is_external_override(name, values) and INITIAL_ENVIRONMENT[name] != value:
            raise RuntimeError(f"{name} is supplied by the process environment; update that server setting instead.")
    original = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    lines = original.splitlines()
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                result.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        result.append(line)
    for key, value in updates.items():
        if key not in seen:
            result.append(f"{key}={value}")
    backup = ENV_FILE.with_name(f".env.accounts-backup-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(4)}")
    existing_mode = stat.S_IMODE(ENV_FILE.stat().st_mode) if ENV_FILE.exists() else 0o600
    if ENV_FILE.exists():
        shutil.copy2(ENV_FILE, backup)
        if os.name == "nt":
            _copy_windows_security_descriptor(ENV_FILE, backup)
    temporary = ENV_FILE.with_name(f".env.{os.getpid()}.tmp")
    try:
        temporary.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")
        if ENV_FILE.exists() and os.name == "nt":
            _copy_windows_security_descriptor(ENV_FILE, temporary)
        elif os.name == "nt":
            _set_owner_only_windows_dacl(temporary)
        # POSIX mode is retained explicitly. On Windows the security
        # descriptor above retains NTFS ACLs; chmod is only a readonly flag.
        os.chmod(temporary, existing_mode)
        os.replace(temporary, ENV_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_windows_security_descriptor(source: Path, target: Path) -> None:
    """Copy owner/group/DACL before publishing a replacement secret file."""
    security_information = 0x00000001 | 0x00000002 | 0x00000004  # owner, group, DACL
    needed = ctypes.c_uint32()
    advapi = ctypes.windll.advapi32
    if advapi.GetFileSecurityW(str(source), security_information, None, 0, ctypes.byref(needed)) or not needed.value:
        raise RuntimeError("Could not read existing .env access controls.")
    descriptor = ctypes.create_string_buffer(needed.value)
    if not advapi.GetFileSecurityW(str(source), security_information, descriptor, needed.value, ctypes.byref(needed)):
        raise RuntimeError("Could not read existing .env access controls.")
    if not advapi.SetFileSecurityW(str(target), security_information, descriptor):
        raise RuntimeError("Could not preserve existing .env access controls.")


def _set_owner_only_windows_dacl(target: Path) -> None:
    """Protect a newly-created .env before it is atomically published."""
    descriptor = ctypes.c_void_p()
    descriptor_size = ctypes.c_uint32()
    advapi = ctypes.windll.advapi32
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), ctypes.byref(descriptor_size)
    ):
        raise RuntimeError("Could not secure the new .env access controls.")
    try:
        if not advapi.SetFileSecurityW(str(target), 0x00000004, descriptor):
            raise RuntimeError("Could not secure the new .env access controls.")
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)


def _acquire_setup_lock() -> None:
    """Make local DuckDB setup and .env replacement a single-writer action."""
    global _setup_lock_handle
    lock = ROOT / ".setup-accounts.lock"
    try:
        handle = lock.open("a+b")
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
    except OSError:
        # Opening or preparing the lock file can fail because of its parent
        # directory or ACL. Preserve that OS error for accurate diagnostics.
        if 'handle' in locals():
            handle.close()
        raise
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if _is_lock_contention(exc):
            raise SetupInProgressError("Another account setup is already running; wait for it to finish.") from exc
        raise
    _setup_lock_handle = handle
    atexit.register(handle.close)


def _is_lock_contention(error: OSError) -> bool:
    """Recognize only documented nonblocking lock collisions."""
    return error.errno in {errno.EACCES, errno.EAGAIN} or getattr(error, "winerror", None) == 33


def _has_active_password_admin() -> bool:
    from app.database_connection import connect
    with connect() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM users WHERE is_global_admin=true AND is_active=true "
            "AND account_status='ACTIVE' AND password_hash IS NOT NULL AND password_hash <> '' LIMIT 1"
        ).fetchone())


def _prompt_with_retries(prompt: str, validator, code: str, retry_message: str) -> str:
    """Prompt a single non-secret field at most three times."""
    for attempt in range(PROMPT_ATTEMPTS):
        if attempt:
            print(retry_message, file=sys.stderr)
        value = input(prompt).strip()
        if validator(value):
            return value
    raise SetupInputError(code)


def _prompt_username() -> str:
    _set_stage("prompt_username")
    return _prompt_with_retries(
        "Initial administrator username (3-80 letters, digits, ., _, or -): ",
        lambda value: bool(USERNAME_PATTERN.fullmatch(value.lower())),
        "ACCOUNT_SETUP_FAILED_INPUT_USERNAME",
        "Username must be 3-80 ASCII letters, digits, dots, underscores, or dashes. Try again.",
    ).lower()


def _prompt_display_name() -> str:
    _set_stage("prompt_display_name")
    return _prompt_with_retries(
        "Initial administrator display name (1-200 characters): ",
        lambda value: bool(value) and len(value) <= 200,
        "ACCOUNT_SETUP_FAILED_INPUT_DISPLAY_NAME",
        "Display name must be 1-200 characters. Try again.",
    )


def _prompt_password() -> str:
    _set_stage("prompt_password")
    for attempt in range(PROMPT_ATTEMPTS):
        if attempt:
            print("Password entries must match and contain 8-256 characters. Try again.", file=sys.stderr)
        password = getpass.getpass("Initial administrator password (8-256 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            continue
        if PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            return password
    # Prefer the mismatch code when the final failed attempt was a mismatch.
    if password != confirmation:
        raise SetupInputError("ACCOUNT_SETUP_FAILED_INPUT_PASSWORD_MISMATCH")
    raise SetupInputError("ACCOUNT_SETUP_FAILED_INPUT_PASSWORD_LENGTH")


def _username_is_available(username: str) -> bool:
    from app.database_connection import connect
    with connect() as conn:
        return not bool(conn.execute("SELECT 1 FROM users WHERE username=?", [username]).fetchone())


def _prompt_admin() -> tuple[str, str, str]:
    for attempt in range(PROMPT_ATTEMPTS):
        if attempt:
            print("That username already exists. Choose an unused username; existing accounts are never changed.", file=sys.stderr)
        username = _prompt_username()
        _set_stage("check_username")
        if _username_is_available(username):
            display_name = _prompt_display_name()
            return username, display_name, _prompt_password()
    raise SetupInputError("ACCOUNT_SETUP_FAILED_INPUT_USERNAME_DUPLICATE")


def _required() -> int:
    print("ACCOUNT_SETUP_REQUIRED: Run setup-accounts.bat in an interactive server console, then run update.bat.", file=sys.stderr)
    return 2


def _preflight_updates(updates: dict[str, str], values: dict[str, str]) -> None:
    """Reject immutable conflicting service settings before any account write."""
    for name, value in updates.items():
        if _is_external_override(name, values) and INITIAL_ENVIRONMENT[name] != value:
            raise RuntimeError(f"{name} is supplied by the process environment; update that server setting instead.")


def main() -> int:
    global _setup_stage
    _setup_stage = "parse_arguments"
    parser = argparse.ArgumentParser(description="Create the server-local initial password administrator.")
    parser.add_argument("--non-interactive", action="store_true", help="Report pending setup without prompting.")
    parser.add_argument("--check", action="store_true", help="Alias for --non-interactive; makes no changes.")
    parser.add_argument("--env-file", type=Path, help="Environment file to use for a controlled server setup.")
    args = parser.parse_args()
    global ENV_FILE
    if args.env_file:
        ENV_FILE = args.env_file.absolute()
    if ENV_FILE.exists() and ENV_FILE.is_symlink():
        raise RuntimeError("Refusing a linked environment file.")
    _set_stage("acquire_lock")
    _acquire_setup_lock()
    # This is intentionally before importing app.config, whose normal .env
    # load is non-overriding. An explicit target therefore wins for isolated
    # rollout verification without reading its real deployment settings first.
    # Environment/service-manager values always win over a file. For an
    # explicit target this still prevents app.config's normal root .env from
    # supplying missing values because this target loads first.
    load_dotenv(ENV_FILE, override=False)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    _set_stage("load_configuration")
    from app.config import database_settings
    from app.database import initialize_database
    from app.security import hash_password
    values = _read_env(ENV_FILE)
    mode = (_effective("AUTH_MODE", values) or "disabled").strip().lower()
    local_escape = (
        mode == "disabled"
        and (_effective("DEPLOYMENT_PROFILE", values) or "local").strip().lower() == "local"
        and (_effective("AUTH_ALLOW_INSECURE_LOCAL", values) or "").strip().lower() in {"1", "true", "yes", "on"}
    )
    if local_escape:
        print("ACCOUNT_SETUP_READY")
        return 0
    if mode == "oidc":
        print("ACCOUNT_SETUP_SKIPPED_OIDC")
        return 0
    database = database_settings()
    check_only = args.non_interactive or args.check
    # A check must never cause DuckDB to create its configured file merely by
    # connecting. Fresh local interactive setup may initialize it explicitly.
    if database.backend == "duckdb" and not database.duckdb_path.exists():
        if check_only:
            return _required()
        _set_stage("initialize_database")
        initialize_database()
    _set_stage("check_existing_admin")
    try:
        has_admin = _has_active_password_admin()
    except Exception as exc:
        if database.backend == "duckdb" and not check_only:
            _set_stage("initialize_database")
            initialize_database()
            _set_stage("check_existing_admin")
            has_admin = _has_active_password_admin()
        elif check_only:
            raise
        else:
            raise RuntimeError("Database schema is unavailable; apply database migration before account setup.") from exc
    if has_admin and mode == "password" and len(_effective("AUTH_SECRET_KEY", values) or "") >= 32:
        print("ACCOUNT_SETUP_READY")
        return 0
    if check_only or not sys.stdin.isatty():
        return _required()
    updates = {"AUTH_MODE": "password"}
    if len(_effective("AUTH_SECRET_KEY", values) or "") < 32:
        updates["AUTH_SECRET_KEY"] = secrets.token_urlsafe(48)
    _set_stage("preflight_environment")
    _preflight_updates(updates, values)
    username, display_name, password = _prompt_admin() if not has_admin else ("", "", "")
    _set_stage("hash_password")
    password_hash = hash_password(password) if not has_admin else None
    if not has_admin:
        _set_stage("verify_username")
        from app.database_connection import connect
        with connect() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username=?", [username]).fetchone():
                raise SetupInputError("ACCOUNT_SETUP_FAILED_INPUT_USERNAME_DUPLICATE")
    if not has_admin:
        _set_stage("create_administrator")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with connect() as conn:
            if conn.backend == "postgresql":
                acquired = conn.execute("SELECT pg_try_advisory_xact_lock(736928104)").fetchone()
                if not acquired or not acquired[0]:
                    raise SetupInProgressError("PostgreSQL account setup lock is held.")
            # The PostgreSQL advisory transaction lock closes the race between
            # two hosts. The process lock above supplies the equivalent for
            # embedded DuckDB and protects the companion .env update.
            became_ready = conn.execute(
                "SELECT 1 FROM users WHERE is_global_admin=true AND is_active=true "
                "AND account_status='ACTIVE' AND password_hash IS NOT NULL AND password_hash <> '' LIMIT 1"
            ).fetchone()
            if not became_ready:
                conn.execute(
                    """INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active,
                       created_at, updated_at, account_status, is_global_admin)
                       VALUES (?, ?, ?, ?, 'admin', true, ?, ?, 'ACTIVE', true)""",
                    [f"user-{uuid4().hex}", username, password_hash, display_name, now, now],
                )
    _set_stage("write_environment")
    _write_env(updates, values)
    print("ACCOUNT_SETUP_READY")
    return 0


def _unwrap_database_error(error: BaseException) -> BaseException:
    """Use a DB driver's original error for bounded diagnostic metadata."""
    current = error
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        original = getattr(current, "orig", None)
        if isinstance(original, BaseException):
            current = original
            continue
        cause = getattr(current, "__cause__", None)
        if isinstance(cause, BaseException):
            current = cause
            continue
        break
    return current


def _safe_failure_code(error: BaseException) -> str:
    if isinstance(error, SetupInputError):
        return error.code
    if isinstance(error, SetupInProgressError):
        return "ACCOUNT_SETUP_FAILED_DATABASE_IN_PROGRESS"
    cause = _unwrap_database_error(error)
    details: dict[str, object] = {}
    try:
        from scripts.backup_failure_details import exception_details
        details = exception_details(cause)
    except Exception:
        pass
    if isinstance(cause, PermissionError) or details.get("sqlstate") == "42501":
        return "ACCOUNT_SETUP_FAILED_DATABASE_PRIVILEGE" if _setup_stage not in {"write_environment", "acquire_lock"} else "ACCOUNT_SETUP_FAILED_ENVIRONMENT_FILE_ACL"
    text = " ".join(str(item) for item in (type(cause).__name__, str(cause))).casefold()
    if _setup_stage in {"write_environment", "acquire_lock"} and any(marker in text for marker in ("acl", "access", "permission", "security descriptor")):
        return "ACCOUNT_SETUP_FAILED_ENVIRONMENT_FILE_ACL"
    if "supplied by the process environment" in text:
        return "ACCOUNT_SETUP_FAILED_ENVIRONMENT_OVERRIDE"
    if details.get("sqlstate") in {"28P01", "28000", "08001", "08003", "08006"} or any(
        marker in text for marker in ("authentication", "connection", "could not connect", "connection refused", "connection timed out")
    ):
        return "ACCOUNT_SETUP_FAILED_DATABASE_CONNECTION_OR_AUTHENTICATION"
    if details.get("sqlstate") in {"42P01", "42703"} or any(
        marker in text for marker in ("undefinedtable", "undefinedcolumn", "relation does not exist", "table does not exist", "schema is unavailable")
    ):
        return "ACCOUNT_SETUP_FAILED_DATABASE_SCHEMA"
    return "ACCOUNT_SETUP_FAILED_UNKNOWN"


def _safe_failure_stage() -> str:
    # This is exclusively assigned by this module; do not derive a stage from
    # arbitrary exception attributes or text.
    return _setup_stage


def run_cli() -> int:
    """Run the command without exposing database URLs, passwords, or paths."""
    try:
        return main()
    except Exception as error:
        code = _safe_failure_code(error)
        print(f"ACCOUNT_SETUP_FAILED code={code} stage={_safe_failure_stage()}", file=sys.stderr)
        try:
            from scripts.backup_failure_details import exception_details
            details = exception_details(_unwrap_database_error(error))
        except Exception:
            details = {"exception_type": "Exception"}
        print("ACCOUNT_SETUP_DETAIL " + json.dumps(details, ensure_ascii=True, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        print(f"ACCOUNT_SETUP_ACTION {_REMEDIATION[code]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_cli())
