"""Create a protected, verified pre-deployment account backup.

This command is deliberately read-only with respect to the configured
database.  It is used by the deployment driver after the old service has
stopped and before migrations are run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
from scripts.backup_failure_details import child_failure_details, exception_details


class AccountBackupChildError(RuntimeError):
    """Keep a failed child result available for safe diagnostic classification."""

    def __init__(self, stage: str, cause: BaseException):
        super().__init__("PostgreSQL 계정 백업에 실패했습니다." if stage == "postgres_current_schema_backup" else stage)
        self.stage = stage
        self.cause = cause


class AccountBackupFailure(RuntimeError):
    """A backup failure carrying only allowlisted deployment diagnostics."""

    def __init__(self, code: str, stage: str, cause: BaseException, report_path: Path | None, media_diagnostics: dict[str, object] | None = None, details: dict[str, object] | None = None):
        # Preserve the original message for direct Python callers and existing
        # error handling.  ``main`` intentionally never prints it.
        super().__init__(str(cause))
        self.code = code
        self.stage = stage
        self.report_path = report_path
        self.media_diagnostics = media_diagnostics or {}
        self.details = details or {}
        self.__cause__ = cause


_POSTGRES_TOOL_MARKERS = ("pg_dump", "pg_restore")
_MISSING_TOOL_MARKERS = ("not recognized as an internal", "command not found", "no such file or directory")
_PERMISSION_OR_DISK_MARKERS = ("permission denied", "access is denied", "disk full", "not enough space", "no space left", "quota exceeded", "read-only file system", "acl", "reparse point")
_VERSION_MARKERS = ("server version", "version mismatch", "unsupported version", "server version:")
_MEDIA_INTEGRITY_MARKERS = ("deploymentmediabackuperror", "mediaintegrityerror", "media_integrity_failed", "media integrity", "media_inventory", "media inventory", "media blob", "media file")
_SCHEMA_MISSING_MARKERS = ("undefinedtable", "undefinedcolumn", "relation does not exist", "column does not exist", "table does not exist")
_AUTH_CONNECTION_MARKERS = ("password authentication failed", "authentication failed", "no pg_hba.conf entry", "could not connect", "connection refused", "connection timed out", "operationalerror")
_PRE_MEDIA_ALEMBIC_REVISIONS = {
    "0001_initial",
    "0002_security_audit",
    "0003_workbench_demo",
    "0004_request_work_plans",
    "0005_batch_execution_profiles",
    "0006_batch_attempts",
    "0007_access_control_menu_policy",
}

_REMEDIATION = {
    "ACCOUNT_BACKUP_FAILED_MISSING_POSTGRES_TOOLS": "Install matching PostgreSQL client tools or set POSTGRES_BIN, then retry.",
    "ACCOUNT_BACKUP_FAILED_FILESYSTEM_ACCESS_OR_DISK": "Check the backup path permissions, available disk space, and Windows folder access policy, then retry.",
    "ACCOUNT_BACKUP_FAILED_WINDOWS_WRITE_PROTECTED": "Windows returned ERROR_WRITE_PROTECT (19). Check the storage/access policy for the operation named in ACCOUNT_BACKUP_DETAIL. If it is a PostgreSQL tool resolve or launch, run check-backup-tools.ps1 to test --version without a DB backup.",
    "ACCOUNT_BACKUP_FAILED_DATABASE_CONNECTION_OR_AUTHENTICATION": "Verify the PostgreSQL service and configured owner connection credentials, then retry.",
    "ACCOUNT_BACKUP_FAILED_DATABASE_SCHEMA_OBJECT_MISSING": "Check the database revision and missing schema objects with the administrator; preserve a verified backup before any migration.",
    "ACCOUNT_BACKUP_FAILED_DATABASE_PRIVILEGE": "Check the configured PostgreSQL backup owner role and its database read permissions, then retry.",
    "ACCOUNT_BACKUP_FAILED_POSTGRES_VERSION_MISMATCH": "Use PostgreSQL client tools compatible with the server version, then retry.",
    "ACCOUNT_BACKUP_FAILED_MEDIA_INTEGRITY": "Repair the reported media integrity issue before retrying the deployment backup.",
    "ACCOUNT_BACKUP_FAILED_POSTGRES_COMMAND": "Check the PostgreSQL client and server versions and the reported command stage/returncode; keep the failed backup directory for diagnosis.",
    "ACCOUNT_BACKUP_FAILED_BACKUP_VERIFICATION": "The dump or companion manifest did not pass verification. Preserve the failed backup directory and check storage and deployment file versions before retrying.",
    "ACCOUNT_BACKUP_FAILED_ENCODING": "Check the reported stage and Python console/file encoding; share the safe ACCOUNT_BACKUP_DETAIL line for diagnosis.",
    "ACCOUNT_BACKUP_FAILED_DEPENDENCY": "Run the deployment runtime setup and confirm all source files belong to the same version, then retry.",
    "ACCOUNT_BACKUP_FAILED_UNKNOWN": "Review the protected failure report and deployment prerequisites, then retry.",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _reject_links(path: Path) -> Path:
    path = path.expanduser().absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise RuntimeError(f"백업 경로에 링크를 사용할 수 없습니다: {current}")
        if os.name == "nt" and current.exists() and _is_reparse_point(current):
            raise RuntimeError(f"백업 경로에 reparse point를 사용할 수 없습니다: {current}")
    return path


def _is_reparse_point(path: Path) -> bool:
    try:
        return bool(path.stat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def _secure_dir(path: Path) -> Path:
    path = _reject_links(path)
    if path.exists() and not path.is_dir():
        raise RuntimeError(f"백업 대상이 디렉터리가 아닙니다: {path}")
    path.mkdir(parents=True, exist_ok=False)
    if os.name == "nt":
        # Reuse the same owner-only DACL contract as account setup.  Importing
        # it here keeps the Windows ACL implementation in one place.
        import ctypes
        descriptor = ctypes.c_void_p()
        size = ctypes.c_uint32()
        if not ctypes.windll.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P(A;OICI;FA;;;OW)", 1, ctypes.byref(descriptor), ctypes.byref(size)
        ):
            raise RuntimeError("백업 디렉터리 ACL을 설정할 수 없습니다.")
        try:
            if not ctypes.windll.advapi32.SetFileSecurityW(str(path), 0x00000004, descriptor):
                raise RuntimeError("백업 디렉터리 ACL을 설정할 수 없습니다.")
        finally:
            ctypes.windll.kernel32.LocalFree(descriptor)
    else:
        os.chmod(path, 0o700)
    return path


def _effective_env(project_root: Path) -> dict[str, str]:
    values = {k: v for k, v in dotenv_values(project_root / ".env").items() if v is not None}
    backend_values = {k: v for k, v in dotenv_values(project_root / "backend" / ".env").items() if v is not None}
    values = {**backend_values, **values}
    # The process environment always wins over deployment files.
    effective = {**values, **os.environ}
    owner = {k: v for k, v in dotenv_values(project_root / ".postgres-owner.env").items() if v is not None}
    for key, value in owner.items():
        effective.setdefault(key, value)
    return effective


def _copy_secret_file(source: Path, destination: Path) -> dict[str, object] | None:
    if not source.exists():
        return None
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"환경 파일은 일반 파일이어야 합니다: {source}")
    shutil.copy2(source, destination)
    os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
    if os.name == "nt":
        from scripts.setup_accounts import _set_owner_only_windows_dacl
        _set_owner_only_windows_dacl(destination)
    return {"filename": destination.name, "bytes": destination.stat().st_size, "sha256": _sha256(destination)}


def _secure_payload(path: Path) -> None:
    if os.name == "nt":
        from scripts.setup_accounts import _set_owner_only_windows_dacl
        _set_owner_only_windows_dacl(path)
    else:
        os.chmod(path, 0o600)


def _write_manifest(directory: Path, value: dict[str, object]) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _secure_payload(path)
    return path


def _child_environment(env: dict[str, str]) -> dict[str, str]:
    """Build a deterministic, non-interactive child process environment."""
    result = os.environ.copy()
    result.update(env)
    result["PYTHONIOENCODING"] = "utf-8"
    return result


def _run_backup_child(command: list[str], *, cwd: Path, env: dict[str, str], stage: str, stdout: int | None = subprocess.PIPE) -> None:
    """Run a backup child without ever relaying its untrusted output."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=_child_environment(env),
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as error:
        raise AccountBackupChildError(stage, error) from error
    if getattr(result, "returncode", 0):
        error = subprocess.CalledProcessError(
            result.returncode,
            command,
            output=getattr(result, "stdout", None),
            stderr=getattr(result, "stderr", None),
        )
        raise AccountBackupChildError(stage, error) from error


def _child_text(error: BaseException) -> str:
    """Return child output only for local allowlist matching; never persist it."""
    if isinstance(error, AccountBackupChildError):
        error = error.cause
    if isinstance(error, subprocess.CalledProcessError):
        return "\n".join(str(value) for value in (error.stderr, error.stdout) if value is not None)
    return str(error)


def _safe_failure_code(error: BaseException) -> str:
    """Map untrusted exception details to a stable, non-secret code."""
    child = error.cause if isinstance(error, AccountBackupChildError) else error
    details = child_failure_details(_child_text(error)) if isinstance(child, subprocess.CalledProcessError) else exception_details(child)
    if details.get("winerror") == 19:
        return "ACCOUNT_BACKUP_FAILED_WINDOWS_WRITE_PROTECTED"
    try:
        from scripts.postgres_cli import PostgresToolNotFound
    except ImportError:  # pragma: no cover - script invocation fallback
        PostgresToolNotFound = ()  # type: ignore[assignment]
    if isinstance(child, PostgresToolNotFound):
        return "ACCOUNT_BACKUP_FAILED_MISSING_POSTGRES_TOOLS"
    if isinstance(child, FileNotFoundError):
        return "ACCOUNT_BACKUP_FAILED_MISSING_POSTGRES_TOOLS"
    if isinstance(child, (PermissionError, IsADirectoryError, NotADirectoryError)):
        return "ACCOUNT_BACKUP_FAILED_FILESYSTEM_ACCESS_OR_DISK"
    if details.get("exception_type") in {"PermissionError", "IsADirectoryError", "NotADirectoryError"}:
        return "ACCOUNT_BACKUP_FAILED_FILESYSTEM_ACCESS_OR_DISK"
    if details.get("exception_type") in {"UnicodeEncodeError", "UnicodeDecodeError"}:
        return "ACCOUNT_BACKUP_FAILED_ENCODING"
    if details.get("exception_type") in {"ImportError", "ModuleNotFoundError"}:
        return "ACCOUNT_BACKUP_FAILED_DEPENDENCY"
    text = _child_text(error).casefold()
    if "postgrestoolnotfound" in text:
        return "ACCOUNT_BACKUP_FAILED_MISSING_POSTGRES_TOOLS"
    if any(marker in text for marker in ("insufficientprivilege", "sqlstate 42501", "permission denied for", "permission denied to", "must be owner of")):
        return "ACCOUNT_BACKUP_FAILED_DATABASE_PRIVILEGE"
    # Reuse the migration preflight's allowlisted PostgreSQL parsing for SQL
    # states and client exception names.  Its return value is a code only.
    try:
        from scripts.upgrade_postgres_schema import _safe_alembic_failure_code
        migration_code = _safe_alembic_failure_code(text, getattr(child, "returncode", 1))
    except Exception:  # Classification must never hide the backup failure.
        migration_code = "MIGRATION_COMMAND_FAILED_UNCLASSIFIED"
    if migration_code.endswith("DATABASE_OBJECT_MISSING"):
        return "ACCOUNT_BACKUP_FAILED_DATABASE_SCHEMA_OBJECT_MISSING"
    if migration_code.endswith("CONNECTION_OR_AUTHENTICATION"):
        return "ACCOUNT_BACKUP_FAILED_DATABASE_CONNECTION_OR_AUTHENTICATION"
    if migration_code.endswith("INSUFFICIENT_PRIVILEGE"):
        return "ACCOUNT_BACKUP_FAILED_FILESYSTEM_ACCESS_OR_DISK"
    command = getattr(child, "cmd", None)
    command_text = " ".join(str(part) for part in command) if isinstance(command, (list, tuple)) else ""
    if any(marker in text for marker in _PERMISSION_OR_DISK_MARKERS):
        return "ACCOUNT_BACKUP_FAILED_FILESYSTEM_ACCESS_OR_DISK"
    if any(marker in text for marker in _VERSION_MARKERS):
        return "ACCOUNT_BACKUP_FAILED_POSTGRES_VERSION_MISMATCH"
    if any(marker in text for marker in _MEDIA_INTEGRITY_MARKERS):
        return "ACCOUNT_BACKUP_FAILED_MEDIA_INTEGRITY"
    if any(marker in text for marker in _SCHEMA_MISSING_MARKERS):
        return "ACCOUNT_BACKUP_FAILED_DATABASE_SCHEMA_OBJECT_MISSING"
    if any(marker in text for marker in _AUTH_CONNECTION_MARKERS):
        return "ACCOUNT_BACKUP_FAILED_DATABASE_CONNECTION_OR_AUTHENTICATION"
    if isinstance(child, subprocess.CalledProcessError) and (
        any(marker in command_text.casefold() for marker in _POSTGRES_TOOL_MARKERS)
        and any(marker in text for marker in _MISSING_TOOL_MARKERS)
    ):
        return "ACCOUNT_BACKUP_FAILED_MISSING_POSTGRES_TOOLS"
    if details.get("stage") in {"pg_dump", "pg_restore_list", "pg_dump_version", "pg_restore_version"} and details.get("exception_type") == "CalledProcessError":
        return "ACCOUNT_BACKUP_FAILED_POSTGRES_COMMAND"
    if any(marker in text for marker in ("deployment backup contract is invalid", "deployment assets backup manifest is invalid", "deployment assets backup verification failed", "백업 파일이 생성되지 않았습니다", "백업 manifest가 생성되지 않았습니다")):
        return "ACCOUNT_BACKUP_FAILED_BACKUP_VERIFICATION"
    return "ACCOUNT_BACKUP_FAILED_UNKNOWN"


def _safe_failure_stage(error: BaseException, fallback: str) -> str:
    if isinstance(error, AccountBackupChildError):
        return error.stage
    if isinstance(error, (PermissionError, IsADirectoryError, NotADirectoryError)):
        return "filesystem"
    return fallback


def _write_failure_report(directory: Path | None, *, code: str, stage: str, media_diagnostics: dict[str, object] | None = None, details: dict[str, object] | None = None) -> Path | None:
    """Best-effort, protected report containing no exception or child output."""
    if directory is None:
        return None
    try:
        path = directory / "failure.json"
        payload = {
            "format": "analysis-canvas-account-deployment-backup-failure",
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "code": code,
            "stage": stage,
            "remediation": _REMEDIATION[code],
        }
        if media_diagnostics:
            payload["media_diagnostics"] = media_diagnostics
        if details:
            payload["details"] = details
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary = directory / f".failure-{uuid.uuid4().hex}.partial"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            offset = 0
            while offset < len(encoded):
                offset += os.write(descriptor, encoded[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        _secure_payload(path)
        return path
    except Exception:
        return None


def _duckdb_backup(path: Path, directory: Path) -> dict[str, object]:
    if path.exists() and not path.is_file():
        raise RuntimeError("ANALYSIS_DUCKDB_PATH가 일반 파일이 아닙니다.")
    if not path.exists():
        return {"classification": "fresh", "database_backup": None}
    import duckdb
    # A normal connection obtains DuckDB's writer lock. CHECKPOINT folds WAL
    # into the main file before the immutable copy is made.
    connection = duckdb.connect(str(path), read_only=False)
    try:
        connection.execute("CHECKPOINT")
        destination = directory / "database.duckdb"
        try:
            shutil.copy2(path, destination)
        except PermissionError:
            # Windows holds the database file exclusively even after
            # CHECKPOINT. Closing this process-owned connection releases the
            # lock; no application service is running during deployment.
            connection.close()
            connection = None
            shutil.copy2(path, destination)
        if _sha256(path) != _sha256(destination):
            raise RuntimeError("DuckDB 백업 SHA-256 검증에 실패했습니다.")
        _secure_payload(destination)
    finally:
        if connection is not None:
            connection.close()
    return {"classification": "existing", "database_backup": {"filename": destination.name, "bytes": destination.stat().st_size, "sha256": _sha256(destination)}}


def _pg_target(url: str) -> tuple[str, str, str, str, str]:
    from scripts.postgres_cli import parse_target
    target = parse_target(url)
    return target.host, str(target.port), target.username, target.password or "", target.database


def _postgres_backup(url: str, directory: Path, env: dict[str, str]) -> dict[str, object]:
    import psycopg
    app_target = _pg_target(url)
    owner_url = env.get("POSTGRES_OWNER_URL") or url
    owner_target = _pg_target(owner_url)
    if (app_target[0].rstrip(".").lower(), app_target[1], app_target[4]) != (owner_target[0].rstrip(".").lower(), owner_target[1], owner_target[4]):
        raise RuntimeError("POSTGRES_OWNER_URL은 DATABASE_URL과 같은 PostgreSQL 대상이어야 합니다.")
    host, port, user, password, database = owner_target
    owner_connection_url = owner_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(owner_connection_url) as connection:
        tables = connection.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','v','m','f','S')").fetchone()[0]
        if not tables:
            return {"classification": "fresh", "database_backup": None}
        names = {row[0] for row in connection.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p')").fetchall()}
        version_relation = connection.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
        revisions = set()
        if version_relation:
            revisions = {str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version").fetchall()}
    command_env = dict(env)
    command_env.pop("PGPASSWORD", None)
    command_env["DATABASE_URL"] = owner_url
    if password:
        command_env["PGPASSWORD"] = owner_target[3]
    # Revisions through 0007 predate media storage (0008).  They may already
    # contain accounts, but cannot satisfy the canonical media inventory
    # backup contract.  This is deliberately an exact allowlist: an unknown
    # revision with these tables remains on the canonical, fail-closed path.
    pre_media_schema = bool(revisions) and revisions.issubset(_PRE_MEDIA_ALEMBIC_REVISIONS)
    supported = {"users", "project_memberships"}.issubset(names) and not pre_media_schema
    if supported:
        # Select the contract before starting: database-only uses canonical
        # restore evidence; dual-read also verifies the existing filesystem.
        # Neither path retries a failed backup with a weaker legacy dump.
        command = [sys.executable, str(BACKEND / "scripts" / "backup_postgres.py"), "--output-dir", str(directory), "--label", "database"]
        media_mode = env.get("SIMDASH_MEDIA_STORAGE_MODE", "dual-read").strip().lower()
        if media_mode not in {"dual-read", "database-only"}:
            raise RuntimeError("SIMDASH_MEDIA_STORAGE_MODE_INVALID")
        if media_mode == "dual-read":
            # prepare() always creates <project>/backups/accounts/<run>.
            # Preserve that project's filesystem, not the tool checkout's.
            assets_root = directory.parents[2] / "backend" / "assets"
            command.extend(["--deployment-assets-root", str(assets_root)])
        _run_backup_child(command, cwd=BACKEND, env=command_env, stage="postgres_current_schema_backup")
        dumps = sorted(directory.glob("database-*.dump"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not dumps:
            raise RuntimeError("PostgreSQL 백업 파일이 생성되지 않았습니다.")
        dump = dumps[0]
        standard_manifest = dump.with_suffix(".manifest.json")
        if not standard_manifest.is_file():
            raise RuntimeError("PostgreSQL 백업 manifest가 생성되지 않았습니다.")
        _secure_payload(dump)
        _secure_payload(standard_manifest)
        evidence = json.loads(standard_manifest.read_text(encoding="utf-8"))
        if media_mode == "dual-read":
            if evidence.get("format") != "analysis-canvas-deployment-postgresql":
                raise RuntimeError("Deployment backup contract is invalid.")
            assets = evidence.get("assets_backup", {})
            archive_name = assets.get("filename", "")
            if not archive_name or Path(archive_name).name != archive_name:
                raise RuntimeError("Deployment assets backup manifest is invalid.")
            archive = directory / archive_name
            if archive.stat().st_size != assets.get("bytes") or _sha256(archive) != assets.get("sha256"):
                raise RuntimeError("Deployment assets backup verification failed.")
            _secure_payload(archive)
            print("ACCOUNT_BACKUP_MEDIA_STATUS mode=dual-read database_and_files=verified")
            allowed_warnings = {"ORPHAN_BLOBS_INCLUDED", "INCOMPLETE_DEMO_CATALOG_INCLUDED", "DEMO_SOURCE_MISSING"}
            for warning in assets.get("warnings", []):
                if isinstance(warning, str) and warning in allowed_warnings:
                    print(f"ACCOUNT_BACKUP_MEDIA_WARNING code={warning}")
        return {"classification": "existing", "database_backup": evidence}

    # Legacy schemas still receive a complete dump, but are explicitly marked
    # without pretending that current account/media inventory verification is
    # available during restore.
    dump = directory / "database.dump"
    pg_bin = env.get("POSTGRES_BIN")
    if pg_bin:
        executable = lambda name: str(Path(pg_bin) / (name + (".exe" if os.name == "nt" else "")))
    else:
        # Use the shared resolver so Windows PostgreSQL installer locations
        # work even when their bin folder is not on PATH.
        from scripts.postgres_cli import executable
    _run_backup_child(
        [executable("pg_dump"), "--host", host, "--port", port, "--username", user, "--dbname", database, "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)],
        cwd=BACKEND,
        env=command_env,
        stage="postgres_legacy_dump",
        stdout=subprocess.DEVNULL,
    )
    _run_backup_child(
        [executable("pg_restore"), "--list", str(dump)],
        cwd=BACKEND,
        env=command_env,
        stage="postgres_legacy_integrity_check",
        stdout=subprocess.DEVNULL,
    )
    _secure_payload(dump)
    evidence = {"format": "postgresql-custom", "created_at": datetime.now(timezone.utc).isoformat(), "database": database, "filename": dump.name, "bytes": dump.stat().st_size, "sha256": _sha256(dump), "legacy_inventory_status": "unavailable_legacy_schema"}
    manifest_path = dump.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _secure_payload(manifest_path)
    return {"classification": "existing", "database_backup": evidence}


def prepare(project_root: Path) -> Path:
    project_root = project_root.expanduser().resolve()
    directory: Path | None = None
    stage = "prepare_directory"
    try:
        env = _effective_env(project_root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        directory = _secure_dir(project_root / "backups" / "accounts" / stamp)
        # A missing backend follows the application default: PostgreSQL.  Do
        # not silently create a legacy DuckDB file when DATABASE_URL is absent.
        stage = "select_backend"
        backend = env.get("ANALYSIS_DB_BACKEND", "postgresql").strip().lower()
        if backend == "duckdb":
            stage = "duckdb_backup"
            db = Path(env.get("ANALYSIS_DUCKDB_PATH", str(project_root / "backend" / "data" / "analysis_dashboard.duckdb"))).expanduser()
            if not db.is_absolute():
                db = (project_root / "backend" / db).resolve()
            result = _duckdb_backup(db, directory)
        elif backend == "postgresql":
            stage = "postgres_backup"
            url = env.get("DATABASE_URL")
            if not url:
                raise RuntimeError("DATABASE_URL이 필요합니다.")
            result = _postgres_backup(url, directory, env)
        else:
            raise RuntimeError("ANALYSIS_DB_BACKEND은 duckdb 또는 postgresql이어야 합니다.")
        stage = "copy_environment_files"
        files = [x for x in (
            _copy_secret_file(project_root / ".env", directory / ".env.backup"),
            _copy_secret_file(project_root / "backend" / ".env", directory / "backend.env.backup"),
            _copy_secret_file(project_root / ".postgres-owner.env", directory / ".postgres-owner.env.backup"),
        ) if x]
        stage = "write_manifest"
        manifest = {"format": "analysis-canvas-account-deployment-backup", "format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "backend": backend, **result, "environment_files": files}
        manifest_path = _write_manifest(directory, manifest)
        print(f"ACCOUNT_BACKUP_READY classification={result['classification']} manifest={manifest_path}")
        return directory
    except Exception as error:
        code = _safe_failure_code(error)
        safe_stage = _safe_failure_stage(error, stage)
        from scripts.media_backup_diagnostics import safe_media_diagnostics
        media_details = safe_media_diagnostics(_child_text(error)) if code == "ACCOUNT_BACKUP_FAILED_MEDIA_INTEGRITY" else {}
        cause = error.cause if isinstance(error, AccountBackupChildError) else error
        details = child_failure_details(_child_text(error)) if isinstance(cause, subprocess.CalledProcessError) else {}
        if not details:
            details = exception_details(cause)
        report_path = _write_failure_report(directory, code=code, stage=safe_stage, media_diagnostics=media_details, details=details)
        if directory is not None:
            try:
                (directory / "BACKUP_FAILED").write_text("Backup failed; deployment must stop.\n", encoding="utf-8")
                _secure_payload(directory / "BACKUP_FAILED")
            except Exception:
                pass
        raise AccountBackupFailure(code, safe_stage, error, report_path, media_details, details) from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a protected account backup before deployment.")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        prepare(args.project_root)
        return 0
    except AccountBackupFailure as error:
        report = f" report={error.report_path}" if error.report_path else ""
        print(f"ACCOUNT_BACKUP_FAILED code={error.code} stage={error.stage}{report}", file=sys.stderr)
        print(f"ACCOUNT_BACKUP_ACTION {_REMEDIATION[error.code]}", file=sys.stderr)
        if error.details:
            print("ACCOUNT_BACKUP_DETAIL " + json.dumps(error.details, sort_keys=True), file=sys.stderr)
        if error.media_diagnostics:
            print("ACCOUNT_BACKUP_MEDIA " + json.dumps(error.media_diagnostics, sort_keys=True), file=sys.stderr)
        return 1
    except Exception:
        # Defensive fallback for errors outside ``prepare`` itself.  Never
        # include exception text because it may contain database credentials.
        print("ACCOUNT_BACKUP_FAILED code=ACCOUNT_BACKUP_FAILED_UNKNOWN stage=entrypoint", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
