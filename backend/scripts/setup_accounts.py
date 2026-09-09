"""Server-local first password administrator setup.

This command deliberately has no HTTP entrypoint.  It is safe to re-run: it
never changes existing users, roles, IDs, or passwords, and only creates an
administrator when no active password administrator exists.
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import getpass
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
_setup_lock_handle = None


def _read_env(path: Path) -> dict[str, str]:
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


def _effective(name: str, values: dict[str, str]) -> str | None:
    return os.environ.get(name, values.get(name))


def _is_external_override(name: str, values: dict[str, str]) -> bool:
    # dotenv may have populated os.environ after process start. Only values
    # inherited by this process are immutable external overrides.
    return name in INITIAL_ENVIRONMENT


def _write_env(updates: dict[str, str], values: dict[str, str]) -> None:
    for name in updates:
        if _is_external_override(name, values):
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
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if 'handle' in locals():
            handle.close()
        raise RuntimeError("Another account setup is already running; wait for it to finish.") from exc
    _setup_lock_handle = handle
    atexit.register(handle.close)


def _has_active_password_admin() -> bool:
    from app.database_connection import connect
    with connect() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM users WHERE is_global_admin=true AND is_active=true "
            "AND account_status='ACTIVE' AND password_hash IS NOT NULL AND password_hash <> '' LIMIT 1"
        ).fetchone())


def _prompt_admin() -> tuple[str, str, str]:
    username = input("Initial administrator username: ").strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise RuntimeError("Username must be 3-80 ASCII letters, digits, dots, dashes, or underscores.")
    display_name = input("Initial administrator display name: ").strip()
    if not display_name or len(display_name) > 200:
        raise RuntimeError("Display name must be 1-200 characters.")
    password = getpass.getpass("Initial administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise RuntimeError("Passwords do not match.")
    if not 12 <= len(password) <= PASSWORD_MAX_LENGTH:
        raise RuntimeError("Password must be 12-256 characters.")
    return username, display_name, password


def main() -> int:
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
    _acquire_setup_lock()
    # This is intentionally before importing app.config, whose normal .env
    # load is non-overriding. An explicit target therefore wins for isolated
    # rollout verification without reading its real deployment settings first.
    # Environment/service-manager values always win over a file. For an
    # explicit target this still prevents app.config's normal root .env from
    # supplying missing values because this target loads first.
    load_dotenv(ENV_FILE, override=False)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
            print("ACCOUNT_SETUP_REQUIRED", file=sys.stderr)
            return 2
        initialize_database()
    try:
        has_admin = _has_active_password_admin()
    except Exception as exc:
        if database.backend == "duckdb" and not check_only:
            initialize_database()
            has_admin = _has_active_password_admin()
        elif check_only:
            print("ACCOUNT_SETUP_REQUIRED", file=sys.stderr)
            return 2
        else:
            raise RuntimeError("Database schema is unavailable; apply database migration before account setup.") from exc
    if has_admin and mode == "password" and len(_effective("AUTH_SECRET_KEY", values) or "") >= 32:
        print("ACCOUNT_SETUP_READY")
        return 0
    if check_only or not sys.stdin.isatty():
        print("ACCOUNT_SETUP_REQUIRED", file=sys.stderr)
        return 2
    username, display_name, password = _prompt_admin() if not has_admin else ("", "", "")
    password_hash = hash_password(password) if not has_admin else None
    if not has_admin:
        from app.database_connection import connect
        with connect() as conn:
            if conn.execute("SELECT 1 FROM users WHERE username=?", [username]).fetchone():
                raise RuntimeError("That username already exists; existing users are never elevated by setup.")
    updates = {"AUTH_MODE": "password"}
    if len(_effective("AUTH_SECRET_KEY", values) or "") < 32:
        updates["AUTH_SECRET_KEY"] = secrets.token_urlsafe(48)
    if not has_admin:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with connect() as conn:
            if conn.backend == "postgresql":
                conn.execute("SELECT pg_advisory_xact_lock(736928104)")
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
    _write_env(updates, values)
    print("ACCOUNT_SETUP_READY")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("ACCOUNT_SETUP_FAILED: review the server configuration and database migration.", file=sys.stderr)
        raise SystemExit(1)
