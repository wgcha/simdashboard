from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.postgres_cli import command_env, connection_args, parse_target


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ASSETS = BACKEND / "assets"
ENV_FILE = ROOT / ".env"
PID_FILE = ROOT / ".server-pids.json"
DATABASE = "simulation_dashboard"
OWNER_ROLE = "simdashboard_owner"
APP_ROLE = "simdashboard_app"
FORMAT_VERSION = 1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"Unsafe bundle path: {value!r}")
    if any(":" in part for part in path.parts):
        raise RuntimeError(f"Unsafe bundle path: {value!r}")
    return path


def process_is_running(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def require_stopped() -> None:
    if not PID_FILE.is_file():
        return
    try:
        payload = json.loads(PID_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("The server PID file is unreadable. Run stop.bat before transfer.")
    running = [int(value) for value in (payload.get("backend"), payload.get("frontend")) if value and process_is_running(int(value))]
    if running:
        raise RuntimeError("Analysis Canvas is running. Run stop.bat before transfer.")


def find_pg_tool(name: str) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    configured = os.getenv("POSTGRES_BIN")
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured) / f"{name}{suffix}")
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        program_files = os.getenv("ProgramFiles")
        if program_files:
            candidates.extend(sorted(Path(program_files).glob(f"PostgreSQL/*/bin/{name}.exe"), reverse=True))
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            candidates.extend(sorted(Path(f"{drive}:\\PostgreSQL").glob(f"*/bin/{name}.exe"), reverse=True))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    raise RuntimeError(f"{name} was not found. Set POSTGRES_BIN to the PostgreSQL bin directory.")


def service_url(parts: dict[str, str], role: str, password: str) -> str:
    query = {key: value for key, value in parts.items() if key not in {"user", "password", "host", "port", "dbname"}}
    return URL.create(
        "postgresql+psycopg",
        username=role,
        password=password,
        host=parts.get("host"),
        port=int(parts["port"]) if parts.get("port") else None,
        database=DATABASE,
        query=query,
    ).render_as_string(hide_password=False)


def expected_alembic_head() -> str:
    configuration = Config(str(BACKEND / "alembic.ini"))
    head = ScriptDirectory.from_config(configuration).get_current_head()
    if not head:
        raise RuntimeError("Could not determine the Alembic head revision.")
    return head


def database_snapshot(database_url: str) -> dict[str, Any]:
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = [row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()]
        counts = {
            table: connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            for table in tables
        }
        managed_paths: set[str] = set()
        if "media_assets" in tables:
            managed_paths.update(row[0] for row in connection.execute("SELECT file_path FROM media_assets").fetchall() if row[0])
        if "report_template_assets" in tables:
            managed_paths.update(row[0] for row in connection.execute(
                "SELECT file_path FROM report_template_assets WHERE is_active=true"
            ).fetchall() if row[0])
        server_version = connection.info.server_version
    return {
        "alembic_revision": revision,
        "server_version": server_version,
        "table_counts": counts,
        "managed_asset_paths": sorted(managed_paths),
    }


def collect_assets() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not ASSETS.is_dir():
        return records
    for path in sorted(ASSETS.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Symbolic links are not allowed in assets: {path.name}")
        if not path.is_file():
            continue
        relative = path.relative_to(ASSETS).as_posix()
        safe_relative_path(relative)
        records.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return records


def validate_managed_asset_paths(paths: list[str], assets: list[dict[str, Any]]) -> None:
    available = {item["path"].casefold() for item in assets}
    missing: list[str] = []
    for raw in paths:
        normalized = raw.replace("\\", "/").removeprefix("assets/")
        safe_relative_path(normalized)
        if normalized.casefold() not in available:
            missing.append(normalized)
    if missing:
        raise RuntimeError("Managed asset files are missing: " + ", ".join(missing[:10]))


def create_assets_archive(destination: Path, assets: list[dict[str, Any]]) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in assets:
            archive.write(ASSETS / Path(*PurePosixPath(item["path"]).parts), arcname=item["path"])


def export_bundle(output_dir: Path) -> Path:
    require_stopped()
    values = dotenv_values(ENV_FILE)
    database_url = values.get("DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing from .env.")
    target = parse_target(database_url)
    before = database_snapshot(database_url)
    if before["alembic_revision"] != expected_alembic_head():
        raise RuntimeError("The source database is not at the current Alembic revision.")
    assets = collect_assets()
    validate_managed_asset_paths(before.pop("managed_asset_paths"), assets)

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = output_dir / f"analysis-canvas-transfer-{timestamp}"
    stage = output_dir / f".{final.name}.{uuid4().hex}.partial"
    stage.mkdir(parents=True)
    try:
        dump = stage / "database.dump"
        command = [
            find_pg_tool("pg_dump"), *connection_args(target), "--format=custom", "--compress=6",
            "--no-owner", "--no-privileges", "--file", str(dump),
        ]
        subprocess.run(command, env=command_env(target), check=True)
        subprocess.run([find_pg_tool("pg_restore"), "--list", str(dump)], check=True, stdout=subprocess.DEVNULL)
        archive = stage / "assets.zip"
        create_assets_archive(archive, assets)
        after = database_snapshot(database_url)
        after.pop("managed_asset_paths")
        if before != after:
            raise RuntimeError("The database changed during export. Keep the application stopped and retry.")
        manifest = {
            "format": "analysis-canvas-postgresql-transfer",
            "format_version": FORMAT_VERSION,
            "bundle_id": str(uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": DATABASE,
            "alembic_revision": before["alembic_revision"],
            "postgres_server_version": before["server_version"],
            "table_counts": before["table_counts"],
            "database_dump": {"file": dump.name, "bytes": dump.stat().st_size, "sha256": sha256(dump)},
            "assets_archive": {"file": archive.name, "bytes": archive.stat().st_size, "sha256": sha256(archive)},
            "assets": assets,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if final.exists():
            raise RuntimeError(f"Transfer bundle already exists: {final.name}")
        os.replace(stage, final)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(f"PostgreSQL transfer bundle created: {final}")
    print(f"tables={len(before['table_counts'])}, rows={sum(before['table_counts'].values())}, assets={len(assets)}")
    return final


def validate_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.expanduser().resolve()
    manifest_path = bundle / "manifest.json"
    if not bundle.is_dir() or not manifest_path.is_file():
        raise RuntimeError("The transfer bundle or manifest.json was not found.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "analysis-canvas-postgresql-transfer" or manifest.get("format_version") != FORMAT_VERSION:
        raise RuntimeError("Unsupported transfer bundle format.")
    if manifest.get("database") != DATABASE:
        raise RuntimeError("The transfer bundle database name is not supported.")
    if manifest.get("alembic_revision") != expected_alembic_head():
        raise RuntimeError("The transfer bundle and target code use different Alembic revisions.")
    for section in ("database_dump", "assets_archive"):
        item = manifest.get(section) or {}
        file_name = safe_relative_path(str(item.get("file", "")))
        if len(file_name.parts) != 1:
            raise RuntimeError("Bundle payload files must be at the bundle root.")
        payload = bundle / file_name.name
        if not payload.is_file() or payload.stat().st_size != item.get("bytes") or sha256(payload) != item.get("sha256"):
            raise RuntimeError(f"Transfer bundle checksum failed: {section}")
    asset_records = manifest.get("assets")
    if not isinstance(asset_records, list):
        raise RuntimeError("The asset manifest is missing.")
    expected: dict[str, dict[str, Any]] = {}
    for item in asset_records:
        relative = safe_relative_path(str(item.get("path", ""))).as_posix()
        folded = relative.casefold()
        if folded in expected:
            raise RuntimeError(f"Duplicate asset path in manifest: {relative}")
        expected[folded] = item
    archive_path = bundle / manifest["assets_archive"]["file"]
    seen: set[str] = set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = safe_relative_path(member.filename).as_posix()
            folded = relative.casefold()
            if folded in seen or folded not in expected:
                raise RuntimeError(f"Unexpected or duplicate asset in archive: {relative}")
            seen.add(folded)
            digest = hashlib.sha256()
            size = 0
            with archive.open(member) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
            record = expected[folded]
            if size != record.get("bytes") or digest.hexdigest() != record.get("sha256"):
                raise RuntimeError(f"Asset checksum failed: {relative}")
    if seen != set(expected):
        raise RuntimeError("One or more assets are missing from the archive.")
    subprocess.run([find_pg_tool("pg_restore"), "--list", str(bundle / manifest["database_dump"]["file"])], check=True, stdout=subprocess.DEVNULL)
    print(
        "Transfer bundle validation passed: "
        f"tables={len(manifest['table_counts'])}, rows={sum(manifest['table_counts'].values())}, assets={len(asset_records)}"
    )
    return manifest


def extract_assets(bundle: Path, manifest: dict[str, Any]) -> Path:
    stage = BACKEND / f".transfer-assets-{manifest['bundle_id']}"
    if stage.exists():
        raise RuntimeError("An asset staging directory already exists from an earlier attempt.")
    stage.mkdir(parents=True)
    archive_path = bundle / manifest["assets_archive"]["file"]
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                relative = safe_relative_path(member.filename)
                destination = stage.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
        return stage
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def grant_runtime_privileges(owner_url: str) -> None:
    with psycopg.connect(owner_url.replace("postgresql+psycopg://", "postgresql://", 1), autocommit=True) as connection:
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(APP_ROLE)))
        connection.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(APP_ROLE)))
        connection.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(APP_ROLE)))


def import_bundle(bundle: Path, validate_only: bool) -> None:
    require_stopped()
    bundle = bundle.expanduser().resolve()
    manifest = validate_bundle(bundle)
    if validate_only:
        print("Validation-only mode completed; no database or files were changed.")
        return

    values = dotenv_values(ENV_FILE)
    configured_url = values.get("POSTGRES_ADMIN_URL") or values.get("DATABASE_URL") or os.getenv("POSTGRES_ADMIN_URL")
    if not configured_url:
        raise RuntimeError("Set POSTGRES_ADMIN_URL in .env to the target PostgreSQL administrator connection.")
    admin_url = configured_url.replace("postgresql+psycopg://", "postgresql://", 1)
    admin_parts = conninfo_to_dict(admin_url)
    with psycopg.connect(admin_url) as admin:
        role_state = admin.execute("SELECT rolcreatedb, rolcreaterole, rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()
        if not role_state or not (role_state[2] or (role_state[0] and role_state[1])):
            raise RuntimeError("The configured PostgreSQL account needs CREATEDB and CREATEROLE privileges.")
        database_exists = admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", [DATABASE]).fetchone() is not None
        role_names = {row[0] for row in admin.execute("SELECT rolname FROM pg_roles WHERE rolname IN (%s, %s)", [OWNER_ROLE, APP_ROLE]).fetchall()}
    if database_exists or role_names:
        raise RuntimeError("The target database or application roles already exist. Import requires a fresh target.")

    owner_password = secrets.token_urlsafe(36)
    app_password = secrets.token_urlsafe(36)
    owner_url = service_url(admin_parts, OWNER_ROLE, owner_password)
    app_url = service_url(admin_parts, APP_ROLE, app_password)
    environment = os.environ.copy()
    environment.update({
        "POSTGRES_ADMIN_URL": admin_url,
        "SIM_DASH_DATABASE": DATABASE,
        "SIM_DASH_OWNER_ROLE": OWNER_ROLE,
        "SIM_DASH_APP_ROLE": APP_ROLE,
        "SIM_DASH_OWNER_PASSWORD": owner_password,
        "SIM_DASH_APP_PASSWORD": app_password,
        "ANALYSIS_DB_BACKEND": "postgresql",
        "DATABASE_URL": owner_url,
    })
    subprocess.run([sys.executable, str(BACKEND / "scripts" / "bootstrap_postgres.py")], cwd=BACKEND, env=environment, check=True)
    owner_target = parse_target(owner_url)
    restore = [
        find_pg_tool("pg_restore"), *connection_args(owner_target), "--single-transaction", "--exit-on-error",
        "--no-owner", "--no-privileges", str(bundle / manifest["database_dump"]["file"]),
    ]
    subprocess.run(restore, env=command_env(owner_target), check=True)
    subprocess.run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], cwd=BACKEND, env=environment, check=True)
    grant_runtime_privileges(owner_url)
    subprocess.run([sys.executable, str(BACKEND / "scripts" / "harden_postgres_privileges.py")], cwd=BACKEND, env=environment, check=True)
    restored = database_snapshot(app_url)
    restored.pop("managed_asset_paths")
    if restored["alembic_revision"] != expected_alembic_head() or restored["table_counts"] != manifest["table_counts"]:
        raise RuntimeError("Restored database verification failed. Do not start the application.")

    from scripts.setup_local_postgres import replace_service_env, verify_app_privileges, write_owner_env

    verify_app_privileges(app_url)

    staged_assets = extract_assets(bundle, manifest)
    backup_root = BACKEND / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    previous_assets = backup_root / f"assets-pre-transfer-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    if ASSETS.exists():
        os.replace(ASSETS, previous_assets)
    os.replace(staged_assets, ASSETS)
    write_owner_env(owner_url)
    replace_service_env(app_url)
    print("PostgreSQL transfer import completed and service credentials were activated.")
    print(f"tables={len(restored['table_counts'])}, rows={sum(restored['table_counts'].values())}, assets={len(manifest['assets'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or import an Analysis Canvas PostgreSQL transfer bundle.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output-dir", type=Path, default=ROOT / "transfer-bundles")
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("bundle", type=Path)
    import_parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.command == "export":
        export_bundle(args.output_dir)
    else:
        import_bundle(args.bundle, args.validate_only)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"[ERROR] {message}", file=sys.stderr)
        raise SystemExit(1)
