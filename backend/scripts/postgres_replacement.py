from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from scripts.postgres_cli import PostgresTarget, command_env, connection_args


DATABASE = "simulation_dashboard"
OWNER_ROLE = "simdashboard_owner"
APP_ROLE = "simdashboard_app"


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def database_name(prefix: str) -> str:
    # PostgreSQL identifiers are limited to 63 bytes. These names are ASCII.
    return f"{prefix}_{utc_stamp()}_{uuid4().hex[:6]}"[:63]


def admin_url_for_database(admin_url: str, database: str) -> str:
    parts = conninfo_to_dict(admin_url.replace("postgresql+psycopg://", "postgresql://", 1))
    return make_conninfo(**{**parts, "dbname": database})


def database_exists(admin_url: str, database: str = DATABASE) -> bool:
    with psycopg.connect(admin_url.replace("postgresql+psycopg://", "postgresql://", 1)) as connection:
        return connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", [database]).fetchone() is not None


def validate_dedicated_roles(admin_url: str, target_database: str = DATABASE) -> set[str]:
    """Reject roles that cannot safely be rotated for a dedicated installation."""
    normalized = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(normalized) as connection:
        roles = {
            row[0]
            for row in connection.execute(
                "SELECT rolname FROM pg_roles WHERE rolname IN (%s, %s)",
                [OWNER_ROLE, APP_ROLE],
            ).fetchall()
        }
        if not roles:
            return roles
        if roles != {OWNER_ROLE, APP_ROLE}:
            raise RuntimeError("Only one of the dedicated PostgreSQL roles exists; replacement stopped.")
        target_owner = connection.execute(
            "SELECT owner.rolname, db.oid FROM pg_database db "
            "JOIN pg_roles owner ON owner.oid=db.datdba WHERE db.datname=%s",
            [target_database],
        ).fetchone()
        if not target_owner or target_owner[0] != OWNER_ROLE:
            raise RuntimeError("Existing dedicated roles are not owned by the target installation.")
        target_oid = target_owner[1]
        other_owned = connection.execute(
            "SELECT db.datname FROM pg_database db JOIN pg_roles owner ON owner.oid=db.datdba "
            "WHERE owner.rolname IN (%s, %s) AND db.datname<>%s AND NOT db.datistemplate",
            [OWNER_ROLE, APP_ROLE, target_database],
        ).fetchall()
        memberships = connection.execute(
            "SELECT 1 FROM pg_auth_members membership "
            "JOIN pg_roles granted ON granted.oid=membership.roleid "
            "JOIN pg_roles member ON member.oid=membership.member "
            "WHERE granted.rolname IN (%s, %s) OR member.rolname IN (%s, %s) LIMIT 1",
            [OWNER_ROLE, APP_ROLE, OWNER_ROLE, APP_ROLE],
        ).fetchone()
        external_dependencies = connection.execute(
            "SELECT 1 FROM pg_shdepend dependency "
            "JOIN pg_roles role ON role.oid=dependency.refobjid "
            "WHERE role.rolname IN (%s, %s) "
            "AND ((dependency.dbid<>0 AND dependency.dbid<>%s) "
            "OR (dependency.dbid=0 AND dependency.classid='pg_database'::regclass AND dependency.objid<>%s) "
            "OR (dependency.dbid=0 AND dependency.classid<>'pg_database'::regclass)) LIMIT 1",
            [OWNER_ROLE, APP_ROLE, target_oid, target_oid],
        ).fetchone()
    if other_owned:
        names = ", ".join(row[0] for row in other_owned)
        raise RuntimeError(f"Dedicated PostgreSQL roles own other databases: {names}")
    if memberships:
        raise RuntimeError("Dedicated PostgreSQL roles have external role memberships; replacement stopped.")
    if external_dependencies:
        raise RuntimeError("Dedicated PostgreSQL roles are referenced outside the target database; replacement stopped.")
    return roles


def database_identity(admin_url: str, database: str) -> str:
    normalized = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(normalized) as connection:
        row = connection.execute("SELECT oid::text FROM pg_database WHERE datname=%s", [database]).fetchone()
    if not row:
        raise RuntimeError(f"PostgreSQL database was not found: {database}")
    return row[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_directory(source: Path, destination: Path) -> int:
    count = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        if source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise RuntimeError(f"Symbolic links are not allowed in managed assets: {path.name}")
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
                    count += 1
    return count


def create_assets_backup(assets: Path, output_dir: Path) -> Path:
    backup_dir = output_dir.expanduser().resolve() / f"pre-transfer-assets-{utc_stamp()}-{uuid4().hex[:6]}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    archive = backup_dir / "assets.zip"
    try:
        asset_count = _archive_directory(assets, archive)
        manifest = {
            "format": "analysis-canvas-assets-backup",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "assets_archive": {"file": archive.name, "bytes": archive.stat().st_size, "sha256": _sha256(archive)},
            "asset_count": asset_count,
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        with zipfile.ZipFile(archive) as payload:
            bad_file = payload.testzip()
            if bad_file:
                raise RuntimeError(f"Assets backup is corrupt: {bad_file}")
    except Exception:
        (backup_dir / "BACKUP_INCOMPLETE").write_text("Transfer was not started.\n", encoding="utf-8")
        raise
    return backup_dir


def _table_counts(database_url: str) -> dict[str, int]:
    with psycopg.connect(database_url) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
            ).fetchall()
        ]
        return {
            table: connection.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in tables
        }


def _verify_dump_restore(
    admin_url: str,
    dump: Path,
    expected_counts: dict[str, int],
    find_pg_tool: Callable[[str], str],
) -> None:
    verification_database = database_name("simulation_dashboard_verify")
    normalized = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    admin_parts = conninfo_to_dict(normalized)
    with psycopg.connect(normalized, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(verification_database)))
    target = PostgresTarget(
        host=admin_parts.get("host") or "127.0.0.1",
        port=int(admin_parts.get("port") or 5432),
        username=admin_parts.get("user") or "postgres",
        password=admin_parts.get("password"),
        database=verification_database,
    )
    try:
        subprocess.run(
            [
                find_pg_tool("pg_restore"),
                *connection_args(target),
                "--single-transaction",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                str(dump),
            ],
            env=command_env(target),
            check=True,
        )
        restored_counts = _table_counts(admin_url_for_database(admin_url, verification_database))
        if restored_counts != expected_counts:
            raise RuntimeError("Replacement backup restore verification did not reproduce the source table counts.")
    finally:
        with psycopg.connect(normalized, autocommit=True) as connection:
            active = connection.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                [verification_database],
            ).fetchone()[0]
            if active:
                raise RuntimeError("Backup verification database still has active connections.")
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(verification_database)))


def create_replacement_backup(
    admin_url: str,
    assets: Path,
    output_dir: Path,
    find_pg_tool: Callable[[str], str],
    target_database: str = DATABASE,
) -> Path:
    """Create a verified database/assets backup before any replacement mutation."""
    backup_dir = output_dir.expanduser().resolve() / f"pre-replacement-{utc_stamp()}-{uuid4().hex[:6]}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    dump = backup_dir / "database.dump"
    archive = backup_dir / "assets.zip"
    admin_parts = conninfo_to_dict(admin_url.replace("postgresql+psycopg://", "postgresql://", 1))
    target = PostgresTarget(
        host=admin_parts.get("host") or "127.0.0.1",
        port=int(admin_parts.get("port") or 5432),
        username=admin_parts.get("user") or "postgres",
        password=admin_parts.get("password"),
        database=target_database,
    )
    environment = command_env(target)
    maintenance_url = admin_url_for_database(admin_url, "postgres")
    original_connection_limit: int | None = None
    try:
        with psycopg.connect(maintenance_url, autocommit=True) as connection:
            state = connection.execute(
                "SELECT datconnlimit FROM pg_database WHERE datname=%s", [target_database]
            ).fetchone()
            if not state:
                raise RuntimeError("The replacement target database disappeared before backup.")
            _assert_no_database_connections(connection, target_database)
            original_connection_limit = state[0]
            connection.execute(
                sql.SQL("ALTER DATABASE {} WITH CONNECTION LIMIT 0").format(sql.Identifier(target_database))
            )
        expected_counts = _table_counts(admin_url_for_database(admin_url, target_database))
        subprocess.run(
            [
                find_pg_tool("pg_dump"),
                *connection_args(target),
                "--format=custom",
                "--compress=6",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(dump),
            ],
            env=environment,
            check=True,
        )
        subprocess.run(
            [find_pg_tool("pg_restore"), "--list", str(dump)],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _verify_dump_restore(admin_url, dump, expected_counts, find_pg_tool)
        asset_count = _archive_directory(assets, archive)
        manifest = {
            "format": "analysis-canvas-pre-replacement",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": target_database,
            "database_dump": {"file": dump.name, "bytes": dump.stat().st_size, "sha256": _sha256(dump)},
            "assets_archive": {"file": archive.name, "bytes": archive.stat().st_size, "sha256": _sha256(archive)},
            "asset_count": asset_count,
            "table_counts": expected_counts,
        }
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Re-read both payloads before reporting the backup as usable.
        if _sha256(dump) != manifest["database_dump"]["sha256"] or _sha256(archive) != manifest["assets_archive"]["sha256"]:
            raise RuntimeError("Replacement backup checksum verification failed.")
        with zipfile.ZipFile(archive) as payload:
            bad_file = payload.testzip()
            if bad_file:
                raise RuntimeError(f"Replacement assets backup is corrupt: {bad_file}")
    except Exception:
        # Keep partial evidence. The caller will refuse to start replacement.
        (backup_dir / "BACKUP_INCOMPLETE").write_text("Replacement was not started.\n", encoding="utf-8")
        raise
    finally:
        if original_connection_limit is not None:
            with psycopg.connect(maintenance_url, autocommit=True) as connection:
                connection.execute(
                    sql.SQL("ALTER DATABASE {} WITH CONNECTION LIMIT {}").format(
                        sql.Identifier(target_database), sql.Literal(original_connection_limit)
                    )
                )
    return backup_dir


def _assert_no_database_connections(connection: psycopg.Connection, database: str) -> None:
    active = connection.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
        [database],
    ).fetchone()[0]
    if active:
        raise RuntimeError(f"Database {database} has {active} active connections; replacement stopped.")


def swap_databases(
    admin_url: str,
    staging_database: str,
    target_database: str = DATABASE,
    previous_database: str | None = None,
    expected_staging_oid: str | None = None,
    expected_target_oid: str | None = None,
) -> str | None:
    """Promote a verified staging database. Restore the original name on failure."""
    normalized = admin_url_for_database(admin_url, "postgres")
    old_name = previous_database if previous_database else database_name(f"{target_database}_pre")
    with psycopg.connect(normalized, autocommit=True) as connection:
        target_row = connection.execute(
            "SELECT oid::text FROM pg_database WHERE datname=%s", [target_database]
        ).fetchone()
        staging_row = connection.execute(
            "SELECT oid::text FROM pg_database WHERE datname=%s", [staging_database]
        ).fetchone()
        if not staging_row:
            raise RuntimeError("The verified staging database no longer exists.")
        target_exists = target_row is not None
        if expected_staging_oid and staging_row[0] != expected_staging_oid:
            raise RuntimeError("The staging database identity changed before replacement.")
        if expected_target_oid and (not target_row or target_row[0] != expected_target_oid):
            raise RuntimeError("The target database identity changed before replacement.")
        stage_disabled = False
        target_disabled = False
        old_renamed = False
        promoted = False
        try:
            _assert_no_database_connections(connection, staging_database)
            connection.execute(
                sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS false").format(sql.Identifier(staging_database))
            )
            stage_disabled = True
            if target_exists:
                _assert_no_database_connections(connection, target_database)
                connection.execute(
                    sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS false").format(sql.Identifier(target_database))
                )
                target_disabled = True
                connection.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(target_database), sql.Identifier(old_name))
                )
                old_renamed = True
            connection.execute(
                sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(staging_database), sql.Identifier(target_database))
            )
            promoted = True
        except Exception:
            if promoted:
                connection.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(target_database), sql.Identifier(staging_database))
                )
            if stage_disabled:
                connection.execute(
                    sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS true").format(sql.Identifier(staging_database))
                )
            if old_renamed:
                connection.execute(
                    sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(old_name), sql.Identifier(target_database))
                )
            if target_disabled:
                connection.execute(
                    sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS true").format(sql.Identifier(target_database))
                )
            raise
    return old_name if target_exists else None


def finalize_promoted_database(
    admin_url: str,
    expected_oid: str,
    target_database: str = DATABASE,
) -> None:
    normalized = admin_url_for_database(admin_url, "postgres")
    with psycopg.connect(normalized, autocommit=True) as connection:
        row = connection.execute(
            "SELECT oid::text, datallowconn FROM pg_database WHERE datname=%s", [target_database]
        ).fetchone()
        if not row or row[0] != expected_oid:
            raise RuntimeError("The promoted database identity changed before final activation.")
        if row[1]:
            raise RuntimeError("The promoted database accepted connections before final activation.")
        connection.execute(
            sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS true").format(sql.Identifier(target_database))
        )


def rollback_database_swap(
    admin_url: str,
    previous_database: str | None,
    failed_database: str,
    target_database: str = DATABASE,
    expected_promoted_oid: str | None = None,
    expected_previous_oid: str | None = None,
) -> None:
    normalized = admin_url_for_database(admin_url, "postgres")
    with psycopg.connect(normalized, autocommit=True) as connection:
        promoted = connection.execute(
            "SELECT oid::text FROM pg_database WHERE datname=%s", [target_database]
        ).fetchone()
        previous = (
            connection.execute("SELECT oid::text FROM pg_database WHERE datname=%s", [previous_database]).fetchone()
            if previous_database
            else None
        )
        if expected_promoted_oid and (not promoted or promoted[0] != expected_promoted_oid):
            raise RuntimeError("The promoted database identity changed before rollback.")
        if expected_previous_oid and (not previous or previous[0] != expected_previous_oid):
            raise RuntimeError("The previous database identity changed before rollback.")
        _assert_no_database_connections(connection, target_database)
        connection.execute(
            sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS false").format(sql.Identifier(target_database))
        )
        connection.execute(
            sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(target_database), sql.Identifier(failed_database))
        )
        if previous_database:
            _assert_no_database_connections(connection, previous_database)
            connection.execute(
                sql.SQL("ALTER DATABASE {} RENAME TO {}").format(sql.Identifier(previous_database), sql.Identifier(target_database))
            )
            connection.execute(
                sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS true").format(sql.Identifier(target_database))
            )


def write_recovery_marker(root: Path, details: dict[str, str]) -> Path:
    marker = root / ".setup-recovery-required.json"
    marker.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return marker
