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
from urllib.parse import urlsplit

from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))


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
    command_env = os.environ.copy()
    command_env.update(env)
    command_env.pop("PGPASSWORD", None)
    command_env["DATABASE_URL"] = owner_url
    if password:
        command_env["PGPASSWORD"] = owner_target[3]
    supported = {"users", "project_memberships"}.issubset(names)
    if supported:
        # Keep the canonical snapshot/inventory/manifest contract used by
        # restore_postgres.py.  Its failures are fatal for the current schema;
        # only an actually legacy schema takes the minimal fallback below.
        command = [sys.executable, str(BACKEND / "scripts" / "backup_postgres.py"), "--output-dir", str(directory), "--label", "database"]
        try:
            subprocess.run(command, cwd=BACKEND, env=command_env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as error:
            raise RuntimeError("PostgreSQL 계정 백업에 실패했습니다.") from error
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
        return {"classification": "existing", "database_backup": evidence}

    # Legacy schemas still receive a complete dump, but are explicitly marked
    # without pretending that current account/media inventory verification is
    # available during restore.
    dump = directory / "database.dump"
    pg_bin = env.get("POSTGRES_BIN")
    executable = lambda name: str(Path(pg_bin) / (name + (".exe" if os.name == "nt" else ""))) if pg_bin else name
    subprocess.run([executable("pg_dump"), "--host", host, "--port", port, "--username", user, "--dbname", database, "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump)], env=command_env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    subprocess.run([executable("pg_restore"), "--list", str(dump)], env=command_env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _secure_payload(dump)
    evidence = {"format": "postgresql-custom", "created_at": datetime.now(timezone.utc).isoformat(), "database": database, "filename": dump.name, "bytes": dump.stat().st_size, "sha256": _sha256(dump), "legacy_inventory_status": "unavailable_legacy_schema"}
    manifest_path = dump.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _secure_payload(manifest_path)
    return {"classification": "existing", "database_backup": evidence}


def prepare(project_root: Path) -> Path:
    project_root = project_root.expanduser().resolve()
    env = _effective_env(project_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = _secure_dir(project_root / "backups" / "accounts" / stamp)
    try:
        # A missing backend follows the application default: PostgreSQL.  Do
        # not silently create a legacy DuckDB file when DATABASE_URL is absent.
        backend = env.get("ANALYSIS_DB_BACKEND", "postgresql").strip().lower()
        if backend == "duckdb":
            db = Path(env.get("ANALYSIS_DUCKDB_PATH", str(project_root / "backend" / "data" / "analysis_dashboard.duckdb"))).expanduser()
            if not db.is_absolute():
                db = (project_root / "backend" / db).resolve()
            result = _duckdb_backup(db, directory)
        elif backend == "postgresql":
            url = env.get("DATABASE_URL")
            if not url:
                raise RuntimeError("DATABASE_URL이 필요합니다.")
            result = _postgres_backup(url, directory, env)
        else:
            raise RuntimeError("ANALYSIS_DB_BACKEND은 duckdb 또는 postgresql이어야 합니다.")
        files = [x for x in (
            _copy_secret_file(project_root / ".env", directory / ".env.backup"),
            _copy_secret_file(project_root / "backend" / ".env", directory / "backend.env.backup"),
            _copy_secret_file(project_root / ".postgres-owner.env", directory / ".postgres-owner.env.backup"),
        ) if x]
        manifest = {"format": "analysis-canvas-account-deployment-backup", "format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "backend": backend, **result, "environment_files": files}
        manifest_path = _write_manifest(directory, manifest)
        print(f"ACCOUNT_BACKUP_READY classification={result['classification']} manifest={manifest_path}")
        return directory
    except Exception:
        (directory / "BACKUP_FAILED").write_text("Backup failed; deployment must stop.\n", encoding="utf-8")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a protected account backup before deployment.")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        prepare(args.project_root)
        return 0
    except Exception:
        print("ACCOUNT_BACKUP_FAILED: protected account backup could not be completed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
