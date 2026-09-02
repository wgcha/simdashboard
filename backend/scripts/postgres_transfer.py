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
from uuid import UUID, uuid4

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.postgres_cli import command_env, connection_args, parse_target
from scripts.postgres_replacement import (
    create_replacement_backup,
    create_assets_backup,
    database_identity,
    database_name,
    finalize_promoted_database,
    rollback_database_swap,
    swap_databases,
    validate_dedicated_roles,
    write_recovery_marker,
)
from app.services.media_integrity import (  # noqa: E402
    MEDIA_INVENTORY_FORMAT,
    MEDIA_INVENTORY_FORMAT_VERSION,
    MediaIntegrityError,
    media_inventory,
    require_media_integrity,
)


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ASSETS = BACKEND / "assets"
ENV_FILE = ROOT / ".env"
OWNER_ENV_FILE = ROOT / ".postgres-owner.env"
RECOVERY_MARKER = ROOT / ".setup-recovery-required.json"
PID_FILE = ROOT / ".server-pids.json"
DATABASE = "simulation_dashboard"
OWNER_ROLE = "simdashboard_owner"
APP_ROLE = "simdashboard_app"
FORMAT_VERSION = 2
MAX_ASSETS_BYTES = 20 * 1024 * 1024 * 1024
MAX_ASSET_FILES = 100_000


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
        raise RuntimeError("The server PID file is unreadable. Run stop.ps1 before transfer.")
    running = [int(value) for value in (payload.get("backend"), payload.get("frontend")) if value and process_is_running(int(value))]
    if running:
        raise RuntimeError("Analysis Canvas is running. Run stop.ps1 before transfer.")


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


def service_url(parts: dict[str, str], role: str, password: str, database: str = DATABASE) -> str:
    query = {key: value for key, value in parts.items() if key not in {"user", "password", "host", "port", "dbname"}}
    return URL.create(
        "postgresql+psycopg",
        username=role,
        password=password,
        host=parts.get("host"),
        port=int(parts["port"]) if parts.get("port") else None,
        database=database,
        query=query,
    ).render_as_string(hide_password=False)


def expected_alembic_head() -> str:
    configuration = Config(str(BACKEND / "alembic.ini"))
    head = ScriptDirectory.from_config(configuration).get_current_head()
    if not head:
        raise RuntimeError("Could not determine the Alembic head revision.")
    return head


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _normalized_asset_path(raw: object) -> str:
    normalized = str(raw).replace("\\", "/")
    if normalized[:7].casefold() == "assets/":
        normalized = normalized[7:]
    return safe_relative_path(normalized).as_posix()


def _database_snapshot_from_connection(connection: Any) -> dict[str, Any]:
    revisions = connection.execute("SELECT version_num FROM alembic_version ORDER BY version_num").fetchall()
    if len(revisions) != 1 or not revisions[0][0]:
        raise RuntimeError("The source database Alembic version must contain exactly one revision.")
    revision = revisions[0][0]
    tables = [row[0] for row in connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
    ).fetchall()]
    counts = {
        table: connection.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
        for table in tables
    }
    managed_paths: set[str] = set()
    bound_media_paths: set[str] = set()
    if "media_assets" in tables:
        for file_path, blob_id in connection.execute(
            "SELECT file_path, blob_id FROM media_assets WHERE file_path IS NOT NULL"
        ).fetchall():
            normalized = _normalized_asset_path(file_path)
            if blob_id is None:
                managed_paths.add(normalized)
            else:
                bound_media_paths.add(normalized)
    if "report_template_assets" in tables:
        managed_paths.update(
            _normalized_asset_path(row[0])
            for row in connection.execute(
                "SELECT file_path FROM report_template_assets WHERE is_active=true AND file_path IS NOT NULL"
            ).fetchall()
        )
    server_version = connection.info.server_version
    return {
        "alembic_revision": revision,
        "server_version": server_version,
        "table_counts": counts,
        "managed_asset_paths": sorted(managed_paths),
        "bound_media_asset_paths": sorted(bound_media_paths),
    }


def database_snapshot(database_url: str) -> dict[str, Any]:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        return _database_snapshot_from_connection(connection)


def _snapshot_export_state(database_url: str) -> tuple[Any, str, dict[str, Any], dict[str, object]]:
    """Keep a repeatable-read snapshot open for both inventory and pg_dump."""
    connection = psycopg.connect(_psycopg_url(database_url))
    try:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot = str(connection.execute("SELECT pg_export_snapshot()").fetchone()[0])
        state = _database_snapshot_from_connection(connection)
        inventory = media_inventory(connection)
        require_media_integrity(inventory)
        return connection, snapshot, state, inventory
    except BaseException:
        connection.close()
        raise


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
        normalized = _normalized_asset_path(raw)
        if normalized.casefold() not in available:
            missing.append(normalized)
    if missing:
        raise RuntimeError("Managed asset files are missing: " + ", ".join(missing[:10]))


def transfer_assets(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Exclude only filesystem copies made redundant by a bound DB blob.

    A path that has any legacy/unbound reference remains in the archive, even
    if another row with the same path is blob-backed.  Unrelated static and
    report-template files remain untouched for backward-compatible transfer.
    """
    required = {str(path).casefold() for path in snapshot["managed_asset_paths"]}
    bound_only = {
        str(path).casefold()
        for path in snapshot["bound_media_asset_paths"]
        if str(path).casefold() not in required
    }
    return [item for item in collect_assets() if str(item["path"]).casefold() not in bound_only]


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
    snapshot_connection, exported_snapshot, before, inventory = _snapshot_export_state(database_url)
    try:
        if before["alembic_revision"] != expected_alembic_head():
            raise RuntimeError("The source database is not at the current Alembic revision.")
        assets = transfer_assets(before)
        validate_managed_asset_paths(before["managed_asset_paths"], assets)

        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final = output_dir / f"analysis-canvas-transfer-{timestamp}"
        stage = output_dir / f".{final.name}.{uuid4().hex}.partial"
        stage.mkdir(parents=True)
    except BaseException:
        snapshot_connection.close()
        raise
    try:
        dump = stage / "database.dump"
        command = [
            find_pg_tool("pg_dump"), *connection_args(target), "--format=custom", "--compress=6",
            "--no-owner", "--no-privileges", f"--snapshot={exported_snapshot}", "--file", str(dump),
        ]
        try:
            subprocess.run(command, env=command_env(target), check=True)
        finally:
            snapshot_connection.close()
        subprocess.run([find_pg_tool("pg_restore"), "--list", str(dump)], check=True, stdout=subprocess.DEVNULL)
        archive = stage / "assets.zip"
        create_assets_archive(archive, assets)
        after = database_snapshot(database_url)
        after_inventory: dict[str, object]
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            after_inventory = media_inventory(connection)
        require_media_integrity(after_inventory)
        if before != after or inventory != after_inventory:
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
            "media_inventory": inventory,
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
    if manifest.get("format") != "analysis-canvas-postgresql-transfer":
        raise RuntimeError("Unsupported transfer bundle format.")
    if manifest.get("format_version") != FORMAT_VERSION:
        if manifest.get("format_version") == 1:
            raise RuntimeError("Transfer bundle format version 1 is not supported; export a new version 2 bundle.")
        raise RuntimeError("Unsupported transfer bundle format version.")
    if manifest.get("database") != DATABASE:
        raise RuntimeError("The transfer bundle database name is not supported.")
    if manifest.get("alembic_revision") != expected_alembic_head():
        raise RuntimeError("The transfer bundle and target code use different Alembic revisions.")
    inventory = manifest.get("media_inventory")
    if not isinstance(inventory, dict) or inventory.get("format") != MEDIA_INVENTORY_FORMAT or inventory.get("format_version") != MEDIA_INVENTORY_FORMAT_VERSION:
        raise RuntimeError("The transfer bundle media inventory is missing or unsupported.")
    try:
        require_media_integrity(inventory)
    except MediaIntegrityError as error:
        raise RuntimeError("The transfer bundle media inventory is not strict.") from error
    try:
        bundle_id = UUID(str(manifest.get("bundle_id", "")))
    except ValueError as error:
        raise RuntimeError("The transfer bundle_id is not a valid UUID.") from error
    if str(bundle_id) != str(manifest.get("bundle_id")):
        raise RuntimeError("The transfer bundle_id must use canonical UUID form.")
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
    if len(asset_records) > MAX_ASSET_FILES:
        raise RuntimeError("The transfer bundle contains too many asset files.")
    expected: dict[str, dict[str, Any]] = {}
    for item in asset_records:
        relative = safe_relative_path(str(item.get("path", ""))).as_posix()
        folded = relative.casefold()
        if folded in expected:
            raise RuntimeError(f"Duplicate asset path in manifest: {relative}")
        expected[folded] = item
    total_asset_bytes = sum(int(item.get("bytes", -1)) for item in asset_records)
    if total_asset_bytes < 0 or total_asset_bytes > MAX_ASSETS_BYTES:
        raise RuntimeError("The transfer bundle asset size is invalid or exceeds the safety limit.")
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
            record = expected[folded]
            if member.file_size != record.get("bytes"):
                raise RuntimeError(f"Asset size metadata mismatch: {relative}")
            digest = hashlib.sha256()
            size = 0
            with archive.open(member) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
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
    if stage.resolve().parent != BACKEND.resolve():
        raise RuntimeError("The transfer asset staging path escaped the backend directory.")
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


def verify_restored_media_inventory(app_url: str, expected: object) -> dict[str, object]:
    """Require the app role, not the owner/admin, to see the exact inventory."""
    if not isinstance(expected, dict):
        raise RuntimeError("The transfer bundle media inventory is missing.")
    with psycopg.connect(_psycopg_url(app_url)) as connection:
        actual = media_inventory(connection)
    require_media_integrity(actual)
    if actual != expected:
        raise RuntimeError("Restored media inventory does not match the transfer bundle. Do not start the application.")
    return actual


def run_database_only_verifier(app_url: str) -> None:
    environment = os.environ.copy()
    environment.update({
        "ANALYSIS_DB_BACKEND": "postgresql",
        "DATABASE_URL": app_url,
        "SIM_DASH_APP_ROLE": APP_ROLE,
    })
    subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "verify_media_database_only.py")],
        cwd=BACKEND,
        env=environment,
        check=True,
    )


def _existing_service_passwords() -> tuple[str, str]:
    service_values = dotenv_values(ENV_FILE)
    owner_values = dotenv_values(OWNER_ENV_FILE)
    app_url = service_values.get("DATABASE_URL")
    owner_url = owner_values.get("POSTGRES_OWNER_URL")
    if not app_url or not owner_url:
        raise RuntimeError(
            "Existing dedicated roles require the current .env and .postgres-owner.env credentials; replacement stopped."
        )
    app_parts = conninfo_to_dict(app_url.replace("postgresql+psycopg://", "postgresql://", 1))
    owner_parts = conninfo_to_dict(owner_url.replace("postgresql+psycopg://", "postgresql://", 1))
    if (
        app_parts.get("user") != APP_ROLE
        or owner_parts.get("user") != OWNER_ROLE
        or app_parts.get("dbname") != DATABASE
        or owner_parts.get("dbname") != DATABASE
        or not app_parts.get("password")
        or not owner_parts.get("password")
    ):
        raise RuntimeError("Stored PostgreSQL service credentials do not match the dedicated target roles.")
    return owner_parts["password"], app_parts["password"]


def _file_state(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _restore_file_state(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.restore")
    from scripts.setup_local_postgres import restrict_private_file
    try:
        temporary.write_bytes(content)
        restrict_private_file(temporary)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _activate_staged_assets(staged_assets: Path) -> Path | None:
    previous = BACKEND / f".pre-transfer-assets-{uuid4().hex}"
    moved_previous = False
    try:
        if ASSETS.exists():
            os.replace(ASSETS, previous)
            moved_previous = True
        os.replace(staged_assets, ASSETS)
    except Exception:
        if moved_previous and previous.exists() and not ASSETS.exists():
            os.replace(previous, ASSETS)
        raise
    return previous if moved_previous else None


def _rollback_assets(previous: Path | None, failed: Path) -> None:
    if ASSETS.exists():
        os.replace(ASSETS, failed)
    if previous and previous.exists():
        os.replace(previous, ASSETS)


def import_bundle(
    bundle: Path,
    validate_only: bool,
    *,
    replace_existing: bool = False,
    backup_dir: Path | None = None,
) -> None:
    require_stopped()
    bundle = bundle.expanduser().resolve()
    manifest = validate_bundle(bundle)
    if validate_only:
        print("Validation-only mode completed; no database or files were changed.")
        return
    if RECOVERY_MARKER.is_file():
        raise RuntimeError("A previous PostgreSQL replacement requires manual recovery; import is blocked.")

    values = dotenv_values(ENV_FILE)
    configured_url = os.getenv("POSTGRES_ADMIN_URL") or values.get("POSTGRES_ADMIN_URL") or values.get("DATABASE_URL")
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
    if database_exists and not replace_existing:
        raise RuntimeError("The target database already exists. Re-run with --replace-existing after reviewing the backup policy.")
    if role_names and not database_exists:
        raise RuntimeError("Dedicated application roles exist without the target database; replacement stopped.")
    if role_names:
        validate_dedicated_roles(admin_url)
        owner_password, app_password = _existing_service_passwords()
    else:
        owner_password = secrets.token_urlsafe(36)
        app_password = secrets.token_urlsafe(36)
    if database_exists and replace_existing and not role_state[2]:
        raise RuntimeError("Replacing an existing database requires a PostgreSQL superuser administrator connection.")

    selected_backup_dir = (backup_dir or (BACKEND / "backups")).expanduser().resolve()
    replacement_backup: Path | None = None
    if database_exists:
        replacement_backup = create_replacement_backup(
            admin_url,
            ASSETS,
            selected_backup_dir,
            find_pg_tool,
        )
        print(f"Verified pre-replacement backup: {replacement_backup}")
    elif ASSETS.is_dir() and any(path.is_file() for path in ASSETS.rglob("*")):
        replacement_backup = create_assets_backup(ASSETS, selected_backup_dir)
        print(f"Verified pre-transfer assets backup: {replacement_backup}")

    staging_database = database_name("simulation_dashboard_stage")
    write_recovery_marker(
        ROOT,
        {
            "status": "replacement-preparation-in-progress",
            "backup": str(replacement_backup or ""),
            "target_database": DATABASE,
            "target_database_oid": database_identity(admin_url, DATABASE) if database_exists else "",
            "staging_database": staging_database,
        },
    )
    owner_url = service_url(admin_parts, OWNER_ROLE, owner_password, staging_database)
    app_url = service_url(admin_parts, APP_ROLE, app_password, staging_database)
    environment = os.environ.copy()
    environment.update({
        "POSTGRES_ADMIN_URL": admin_url,
        "SIM_DASH_DATABASE": staging_database,
        "SIM_DASH_OWNER_ROLE": OWNER_ROLE,
        "SIM_DASH_APP_ROLE": APP_ROLE,
        "SIM_DASH_OWNER_PASSWORD": owner_password,
        "SIM_DASH_APP_PASSWORD": app_password,
        "ANALYSIS_DB_BACKEND": "postgresql",
        "DATABASE_URL": owner_url,
    })
    if role_names:
        environment["SIM_DASH_PRESERVE_EXISTING_ROLES"] = "1"
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
    validate_managed_asset_paths(restored["managed_asset_paths"], manifest["assets"])
    if restored["alembic_revision"] != expected_alembic_head() or restored["table_counts"] != manifest["table_counts"]:
        raise RuntimeError("Restored database verification failed. Do not start the application.")
    verify_restored_media_inventory(app_url, manifest["media_inventory"])
    run_database_only_verifier(app_url)

    from scripts.setup_local_postgres import replace_service_env, verify_app_privileges, write_owner_env

    verify_app_privileges(app_url)

    staged_assets = extract_assets(bundle, manifest)
    original_env = _file_state(ENV_FILE)
    original_owner_env = _file_state(OWNER_ENV_FILE)
    previous_database: str | None = None
    previous_assets: Path | None = None
    database_swapped = False
    assets_swapped = False
    failed_database = database_name("simulation_dashboard_failed")
    failed_assets = BACKEND / f".failed-transfer-assets-{uuid4().hex}"
    final_owner_url = service_url(admin_parts, OWNER_ROLE, owner_password)
    final_app_url = service_url(admin_parts, APP_ROLE, app_password)
    target_oid = database_identity(admin_url, DATABASE) if database_exists else ""
    staging_oid = database_identity(admin_url, staging_database)
    try:
        marker_details = {
            "status": "replacement-in-progress",
            "backup": str(replacement_backup or ""),
            "target_database": DATABASE,
            "target_database_oid": target_oid,
            "staging_database": staging_database,
            "staging_database_oid": staging_oid,
            "failed_database": failed_database,
        }
        write_recovery_marker(ROOT, marker_details)
        previous_database = swap_databases(
            admin_url,
            staging_database,
            expected_staging_oid=staging_oid,
            expected_target_oid=target_oid or None,
        )
        database_swapped = True
        previous_assets = _activate_staged_assets(staged_assets)
        assets_swapped = True
        write_owner_env(final_owner_url)
        replace_service_env(final_app_url)
        if database_identity(admin_url, DATABASE) != staging_oid:
            raise RuntimeError("The promoted database identity verification failed.")
        finalize_promoted_database(admin_url, staging_oid)
        verify_app_privileges(final_app_url)
        RECOVERY_MARKER.unlink(missing_ok=True)
    except Exception as activation_error:
        rollback_errors: list[str] = []
        try:
            _restore_file_state(ENV_FILE, original_env)
            _restore_file_state(OWNER_ENV_FILE, original_owner_env)
        except Exception as error:
            rollback_errors.append(f"environment={type(error).__name__}")
        if assets_swapped:
            try:
                _rollback_assets(previous_assets, failed_assets)
            except Exception as error:
                rollback_errors.append(f"assets={type(error).__name__}")
        if database_swapped:
            try:
                rollback_database_swap(
                    admin_url,
                    previous_database,
                    failed_database,
                    expected_promoted_oid=staging_oid,
                    expected_previous_oid=target_oid or None,
                )
            except Exception as error:
                rollback_errors.append(f"database={type(error).__name__}")
        if rollback_errors:
            marker = write_recovery_marker(
                ROOT,
                {
                    "status": "manual-recovery-required",
                    "reason": type(activation_error).__name__,
                    "rollback_errors": ",".join(rollback_errors),
                    "backup": str(replacement_backup or ""),
                    "previous_database": previous_database or "",
                    "failed_database": failed_database,
                    "failed_assets": str(failed_assets),
                },
            )
            raise RuntimeError(f"Replacement activation failed and rollback is incomplete. See {marker.name}.") from activation_error
        if not database_swapped or previous_database is None:
            marker = write_recovery_marker(
                ROOT,
                {
                    "status": "manual-recovery-required",
                    "reason": type(activation_error).__name__,
                    "backup": str(replacement_backup or ""),
                    "target_database_oid": target_oid,
                    "staging_database_oid": staging_oid,
                    "failed_database": failed_database,
                },
            )
            raise RuntimeError(f"Database cutover did not complete. Review {marker.name} before starting.") from activation_error
        RECOVERY_MARKER.unlink(missing_ok=True)
        raise RuntimeError("Replacement activation failed; the previous installation was restored.") from activation_error

    if previous_assets and previous_assets.exists():
        shutil.rmtree(previous_assets)
    print("PostgreSQL transfer import completed and service credentials were activated.")
    print(f"tables={len(restored['table_counts'])}, rows={sum(restored['table_counts'].values())}, assets={len(manifest['assets'])}")
    if previous_database:
        print(f"Previous database retained as: {previous_database}")
    if replacement_backup:
        print(f"Verified replacement backup retained at: {replacement_backup}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or import an Analysis Canvas PostgreSQL transfer bundle.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--output-dir", type=Path, default=ROOT / "transfer-bundles")
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("bundle", type=Path)
    import_parser.add_argument("--validate-only", action="store_true")
    import_parser.add_argument("--replace-existing", action="store_true")
    import_parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        export_bundle(args.output_dir)
    else:
        import_bundle(
            args.bundle,
            args.validate_only,
            replace_existing=args.replace_existing,
            backup_dir=args.backup_dir,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"[ERROR] {message}", file=sys.stderr)
        raise SystemExit(1)
