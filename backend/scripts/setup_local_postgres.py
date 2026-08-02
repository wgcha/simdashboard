from __future__ import annotations

import argparse
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values, load_dotenv
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from sqlalchemy.engine import URL

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.postgres_replacement import (
    create_replacement_backup,
    database_identity,
    database_name,
    finalize_promoted_database,
    rollback_database_swap,
    swap_databases,
    validate_dedicated_roles,
    write_recovery_marker,
)
from scripts.postgres_transfer import find_pg_tool


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ENV_FILE = ROOT / ".env"
OWNER_ENV_FILE = ROOT / ".postgres-owner.env"
SOURCE = BACKEND / "data" / "analysis_dashboard.duckdb"
DATABASE = "simulation_dashboard"
OWNER_ROLE = "simdashboard_owner"
APP_ROLE = "simdashboard_app"


def fail(message: str) -> None:
    raise RuntimeError(message)


def normalized_psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def service_url(parts: dict[str, str], role: str, password: str, database: str = DATABASE) -> str:
    port = int(parts["port"]) if parts.get("port") else None
    query = {
        key: value
        for key, value in parts.items()
        if key not in {"user", "password", "host", "port", "dbname"}
    }
    return URL.create(
        "postgresql+psycopg",
        username=role,
        password=password,
        host=parts.get("host"),
        port=port,
        database=database,
        query=query,
    ).render_as_string(hide_password=False)


def run(command: list[str], environment: dict[str, str]) -> None:
    result = subprocess.run(command, cwd=BACKEND, env=environment, check=False)
    if result.returncode:
        fail(f"Setup command failed with exit code {result.returncode}.")


def restrict_private_file(path: Path) -> None:
    if os.name != "nt":
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as error:
            fail(f"Could not restrict private setup file permissions: {path.name}")
        return
    username = os.getenv("USERNAME")
    domain = os.getenv("USERDOMAIN")
    principal = f"{domain}\\{username}" if domain and username else username
    if not principal:
        fail(f"Could not determine the Windows user for private file ACL: {path.name}")
    result = subprocess.run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{principal}:(R,W)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        fail(f"Could not apply the private Windows ACL: {path.name}")


def replace_service_env(database_url: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    replacements = {
        "ANALYSIS_DB_BACKEND": "postgresql",
        "DATABASE_URL": database_url,
    }
    output: list[str] = []
    found: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in replacements:
            output.append(f"{key}={replacements[key]}")
            found.add(key)
        elif key in {
            "POSTGRES_ADMIN_URL",
            "POSTGRES_OWNER_URL",
            "SIM_DASH_OWNER_PASSWORD",
            "SIM_DASH_APP_PASSWORD",
        }:
            # One-time bootstrap credentials must not remain in the service environment.
            continue
        else:
            output.append(line)
    for key in ("ANALYSIS_DB_BACKEND", "DATABASE_URL"):
        if key not in found:
            output.append(f"{key}={replacements[key]}")
    temporary = ENV_FILE.with_suffix(".env.tmp")
    try:
        temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        restrict_private_file(temporary)
        os.replace(temporary, ENV_FILE)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_owner_env(owner_url: str) -> None:
    temporary = OWNER_ENV_FILE.with_suffix(".env.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"POSTGRES_OWNER_URL={owner_url}\n")
        restrict_private_file(temporary)
        os.replace(temporary, OWNER_ENV_FILE)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def verify_app_privileges(app_url: str) -> None:
    with psycopg.connect(normalized_psycopg_url(app_url)) as connection:
        attributes = connection.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        privileges = connection.execute(
            "SELECT "
            "has_database_privilege(current_user, current_database(), 'CONNECT'), "
            "has_database_privilege(current_user, current_database(), 'CREATE'), "
            "has_schema_privilege(current_user, 'public', 'USAGE'), "
            "has_schema_privilege(current_user, 'public', 'CREATE'), "
            "has_table_privilege(current_user, 'public.audit_events', 'SELECT'), "
            "has_table_privilege(current_user, 'public.audit_events', 'INSERT'), "
            "has_table_privilege(current_user, 'public.audit_events', 'UPDATE'), "
            "has_table_privilege(current_user, 'public.audit_events', 'DELETE'), "
            "has_table_privilege(current_user, 'public.audit_events', 'TRUNCATE')"
        ).fetchone()
    if attributes != (False, False, False):
        fail("The application role unexpectedly has PostgreSQL administration privileges.")
    if privileges != (True, False, True, False, True, True, False, False, False):
        fail("The application role privilege verification failed.")


def expected_alembic_head() -> str:
    configuration = Config(str(BACKEND / "alembic.ini"))
    head = ScriptDirectory.from_config(configuration).get_current_head()
    if not head:
        fail("The Alembic migration head could not be determined.")
    return head


def verify_existing_setup(app_url: str, owner_url: str | None) -> None:
    verify_app_privileges(app_url)
    with psycopg.connect(normalized_psycopg_url(app_url)) as connection:
        relation = connection.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
        if relation is None:
            fail("The target database exists but has no Alembic schema.")
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if revision != expected_alembic_head():
            fail("The target database is not at the current Alembic revision.")
    if not owner_url:
        fail("POSTGRES_OWNER_URL is missing; it is required for future migrations and restores.")
    with psycopg.connect(normalized_psycopg_url(owner_url)) as connection:
        if connection.execute("SELECT current_user").fetchone()[0] != OWNER_ROLE:
            fail("POSTGRES_OWNER_URL does not use the expected owner role.")


def target_object_count(admin_parts: dict[str, str]) -> int | None:
    admin_url = make_conninfo(**{**admin_parts, "dbname": DATABASE})
    try:
        with psycopg.connect(admin_url) as connection:
            return connection.execute(
                "SELECT "
                "(SELECT count(*) FROM pg_class object JOIN pg_namespace ns ON ns.oid=object.relnamespace "
                " WHERE ns.nspname NOT IN ('pg_catalog', 'information_schema') AND ns.nspname NOT LIKE 'pg_toast%') + "
                "(SELECT count(*) FROM pg_proc routine JOIN pg_namespace ns ON ns.oid=routine.pronamespace "
                " WHERE ns.nspname NOT IN ('pg_catalog', 'information_schema') AND ns.nspname NOT LIKE 'pg_toast%') + "
                "(SELECT count(*) FROM pg_namespace ns WHERE ns.nspname NOT IN ('public', 'pg_catalog', 'information_schema') "
                " AND ns.nspname NOT LIKE 'pg_%')"
            ).fetchone()[0]
    except psycopg.errors.InvalidCatalogName:
        return None


def existing_service_passwords(
    root_values: dict[str, str | None], owner_values: dict[str, str | None]
) -> tuple[str, str]:
    app_url = root_values.get("DATABASE_URL")
    owner_url = owner_values.get("POSTGRES_OWNER_URL")
    if not app_url or not owner_url:
        fail("Existing dedicated roles require current .env and .postgres-owner.env credentials.")
    app_parts = conninfo_to_dict(normalized_psycopg_url(app_url))
    owner_parts = conninfo_to_dict(normalized_psycopg_url(owner_url))
    if (
        app_parts.get("user") != APP_ROLE
        or owner_parts.get("user") != OWNER_ROLE
        or app_parts.get("dbname") != DATABASE
        or owner_parts.get("dbname") != DATABASE
        or not app_parts.get("password")
        or not owner_parts.get("password")
    ):
        fail("Stored PostgreSQL service credentials do not match the dedicated target roles.")
    return owner_parts["password"], app_parts["password"]


def file_state(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def restore_file_state(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.restore")
    try:
        temporary.write_bytes(content)
        restrict_private_file(temporary)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a local Analysis Canvas PostgreSQL database.")
    parser.add_argument("--seed-mode", choices=("duckdb", "empty", "demo"), default="duckdb")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()

    recovery_marker = ROOT / ".setup-recovery-required.json"
    if recovery_marker.is_file():
        fail("A previous PostgreSQL replacement requires manual recovery; setup is blocked.")

    root_values = dotenv_values(ENV_FILE)
    owner_values = dotenv_values(OWNER_ENV_FILE)
    load_dotenv(ENV_FILE, override=False)
    if args.seed_mode == "duckdb" and not SOURCE.is_file():
        fail(f"DuckDB source was not found: {SOURCE}")

    configured_url = os.getenv("POSTGRES_ADMIN_URL") or os.getenv("DATABASE_URL")
    owner_url = (
        owner_values.get("POSTGRES_OWNER_URL")
        or root_values.get("POSTGRES_OWNER_URL")
        or os.getenv("POSTGRES_OWNER_URL")
    )
    if not configured_url:
        fail("Set DATABASE_URL to the local PostgreSQL administrator connection before first setup.")
    admin_url = normalized_psycopg_url(configured_url)
    admin_parts = conninfo_to_dict(admin_url)

    print("Checking PostgreSQL administrator access and target state...")
    with psycopg.connect(admin_url) as admin:
        current_user, current_database = admin.execute(
            "SELECT current_user, current_database()"
        ).fetchone()
        if current_user == APP_ROLE and current_database == DATABASE:
            verify_existing_setup(configured_url, owner_url)
            write_owner_env(owner_url)
            replace_service_env(configured_url)
            print("PostgreSQL is already configured at the current Alembic revision.")
            return 0
        role_state = admin.execute(
            "SELECT rolcreatedb, rolcreaterole, rolsuper FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        if not role_state or not (role_state[2] or (role_state[0] and role_state[1])):
            fail("The configured PostgreSQL account needs CREATEDB and CREATEROLE privileges.")
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", [DATABASE]).fetchone()
        role_names = {
            row[0]
            for row in admin.execute(
                "SELECT rolname FROM pg_roles WHERE rolname IN (%s, %s)", [OWNER_ROLE, APP_ROLE]
            ).fetchall()
        }

    replacement_backup: Path | None = None
    target_has_data = False
    if exists:
        object_count = target_object_count(admin_parts)
        if object_count:
            target_has_data = True
            if not args.replace_existing:
                fail(
                    f"Target database {DATABASE} already contains {object_count} managed schema objects. "
                    "Automatic setup stopped to protect existing data."
                )
            print(f"Target database contains {object_count} managed schema objects; verified replacement was requested.")
        else:
            print("Target database exists and is empty; setup can resume safely.")
    else:
        print("Target database does not exist; it will be created.")

    if role_names and not exists:
        fail("Dedicated application roles exist without the target database; setup stopped.")
    if role_names:
        validate_dedicated_roles(admin_url)
        owner_password, app_password = existing_service_passwords(root_values, owner_values)
    else:
        owner_password = secrets.token_urlsafe(36)
        app_password = secrets.token_urlsafe(36)

    if target_has_data and not role_state[2]:
        fail("Replacing an existing database requires a PostgreSQL superuser administrator connection.")

    if target_has_data:
        replacement_backup = create_replacement_backup(
            admin_url,
            BACKEND / "assets",
            (args.backup_dir or (BACKEND / "backups")),
            find_pg_tool,
        )
        print(f"Verified pre-replacement backup: {replacement_backup}")

    base_environment = os.environ.copy()
    if args.seed_mode == "duckdb":
        dry_run_environment = base_environment.copy()
        dry_run_environment.pop("DATABASE_URL", None)
        print("Validating the DuckDB source in read-only mode...")
        run(
            [sys.executable, "scripts/migrate_duckdb_to_postgres.py", "--source", str(SOURCE)],
            dry_run_environment,
        )

    staging_database = database_name("simulation_dashboard_stage") if target_has_data else DATABASE
    write_recovery_marker(
        ROOT,
        {
            "status": "database-preparation-in-progress",
            "backup": str(replacement_backup or ""),
            "target_database": DATABASE,
            "target_database_oid": database_identity(admin_url, DATABASE) if exists else "",
            "staging_database": staging_database,
        },
    )
    owner_url = service_url(admin_parts, OWNER_ROLE, owner_password, staging_database)
    app_url = service_url(admin_parts, APP_ROLE, app_password, staging_database)

    setup_environment = base_environment.copy()
    setup_environment.update(
        {
            "POSTGRES_ADMIN_URL": admin_url,
            "SIM_DASH_DATABASE": staging_database,
            "SIM_DASH_OWNER_ROLE": OWNER_ROLE,
            "SIM_DASH_APP_ROLE": APP_ROLE,
            "SIM_DASH_OWNER_PASSWORD": owner_password,
            "SIM_DASH_APP_PASSWORD": app_password,
            "ANALYSIS_DB_BACKEND": "postgresql",
            "DATABASE_URL": owner_url,
        }
    )
    if role_names:
        setup_environment["SIM_DASH_PRESERVE_EXISTING_ROLES"] = "1"

    print("Creating PostgreSQL roles and database...")
    run([sys.executable, "scripts/bootstrap_postgres.py"], setup_environment)
    print("Applying Alembic migrations...")
    run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], setup_environment)
    print("Applying runtime role restrictions...")
    run([sys.executable, "scripts/harden_postgres_privileges.py"], setup_environment)
    if args.seed_mode == "duckdb":
        print("Copying and checksum-verifying DuckDB data...")
        run(
            [
                sys.executable,
                "scripts/migrate_duckdb_to_postgres.py",
                "--source",
                str(SOURCE),
                "--execute",
            ],
            setup_environment,
        )
    else:
        print(f"Initializing PostgreSQL content: mode={args.seed_mode}")
        run([sys.executable, "scripts/seed_database.py", "--mode", args.seed_mode], setup_environment)

    verify_environment = setup_environment.copy()
    verify_environment["DATABASE_URL"] = app_url
    print("Verifying the application-role connection...")
    run([sys.executable, "scripts/check_postgres_connection.py"], verify_environment)
    verify_app_privileges(app_url)

    final_owner_url = service_url(admin_parts, OWNER_ROLE, owner_password)
    final_app_url = service_url(admin_parts, APP_ROLE, app_password)
    if target_has_data:
        original_env = file_state(ENV_FILE)
        original_owner_env = file_state(OWNER_ENV_FILE)
        previous_database: str | None = None
        database_swapped = False
        failed_database = database_name("simulation_dashboard_failed")
        target_oid = database_identity(admin_url, DATABASE)
        staging_oid = database_identity(admin_url, staging_database)
        try:
            write_recovery_marker(
                ROOT,
                {
                    "status": "replacement-in-progress",
                    "backup": str(replacement_backup or ""),
                    "target_database": DATABASE,
                    "target_database_oid": target_oid,
                    "staging_database": staging_database,
                    "staging_database_oid": staging_oid,
                    "failed_database": failed_database,
                },
            )
            previous_database = swap_databases(
                admin_url,
                staging_database,
                expected_staging_oid=staging_oid,
                expected_target_oid=target_oid,
            )
            database_swapped = True
            write_owner_env(final_owner_url)
            replace_service_env(final_app_url)
            if database_identity(admin_url, DATABASE) != staging_oid:
                fail("The promoted database identity verification failed.")
            finalize_promoted_database(admin_url, staging_oid)
            verify_app_privileges(final_app_url)
            (ROOT / ".setup-recovery-required.json").unlink(missing_ok=True)
        except Exception as activation_error:
            rollback_errors: list[str] = []
            try:
                restore_file_state(ENV_FILE, original_env)
                restore_file_state(OWNER_ENV_FILE, original_owner_env)
            except Exception as error:
                rollback_errors.append(f"environment={type(error).__name__}")
            if database_swapped:
                try:
                    rollback_database_swap(
                        admin_url,
                        previous_database,
                        failed_database,
                        expected_promoted_oid=staging_oid,
                        expected_previous_oid=target_oid,
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
                    },
                )
                fail(f"Replacement activation failed and rollback is incomplete. See {marker.name}.")
            if not database_swapped:
                marker = write_recovery_marker(
                    ROOT,
                    {
                        "status": "manual-recovery-required",
                        "reason": type(activation_error).__name__,
                        "backup": str(replacement_backup or ""),
                        "target_database_oid": target_oid,
                        "staging_database_oid": staging_oid,
                    },
                )
                fail(f"Database cutover did not complete. Review {marker.name} before starting.")
            (ROOT / ".setup-recovery-required.json").unlink(missing_ok=True)
            fail("Replacement activation failed; the previous installation was restored.")
        print(f"Previous database retained as: {previous_database}")
    else:
        original_env = file_state(ENV_FILE)
        original_owner_env = file_state(OWNER_ENV_FILE)
        try:
            write_owner_env(final_owner_url)
            replace_service_env(final_app_url)
            recovery_marker.unlink(missing_ok=True)
        except Exception as activation_error:
            rollback_errors: list[str] = []
            try:
                restore_file_state(ENV_FILE, original_env)
                restore_file_state(OWNER_ENV_FILE, original_owner_env)
            except Exception as error:
                rollback_errors.append(f"environment={type(error).__name__}")
            marker = write_recovery_marker(
                ROOT,
                {
                    "status": "manual-recovery-required",
                    "reason": type(activation_error).__name__,
                    "rollback_errors": ",".join(rollback_errors),
                    "target_database": DATABASE,
                    "target_database_oid": database_identity(admin_url, DATABASE),
                },
            )
            fail(f"Service credential activation failed. Review {marker.name} before starting or retrying setup.")
    print("PostgreSQL setup completed and .env now contains only service credentials.")
    if args.seed_mode == "duckdb":
        print("The original DuckDB file was retained unchanged.")
    if replacement_backup:
        print(f"Verified replacement backup retained at: {replacement_backup}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Never render connection strings or nested driver messages here.
        print(f"[ERROR] {error if isinstance(error, RuntimeError) else type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
