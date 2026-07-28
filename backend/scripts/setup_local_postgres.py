from __future__ import annotations

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


def service_url(parts: dict[str, str], role: str, password: str) -> str:
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
        database=DATABASE,
        query=query,
    ).render_as_string(hide_password=False)


def run(command: list[str], environment: dict[str, str]) -> None:
    result = subprocess.run(command, cwd=BACKEND, env=environment, check=False)
    if result.returncode:
        fail(f"Setup command failed with exit code {result.returncode}.")


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
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.replace(temporary, ENV_FILE)


def write_owner_env(owner_url: str) -> None:
    temporary = OWNER_ENV_FILE.with_suffix(".env.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"POSTGRES_OWNER_URL={owner_url}\n")
    os.replace(temporary, OWNER_ENV_FILE)
    try:
        os.chmod(OWNER_ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows ACLs may not support POSIX permission bits; the file remains gitignored.
        pass
    if os.name == "nt":
        username = os.getenv("USERNAME")
        domain = os.getenv("USERDOMAIN")
        principal = f"{domain}\\{username}" if domain and username else username
        if principal:
            subprocess.run(
                [
                    "icacls.exe",
                    str(OWNER_ENV_FILE),
                    "/inheritance:r",
                    "/grant:r",
                    f"{principal}:(R,W)",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


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


def target_table_count(admin_parts: dict[str, str]) -> int | None:
    admin_url = make_conninfo(**{**admin_parts, "dbname": DATABASE})
    try:
        with psycopg.connect(admin_url) as connection:
            return connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            ).fetchone()[0]
    except psycopg.errors.InvalidCatalogName:
        return None


def main() -> int:
    root_values = dotenv_values(ENV_FILE)
    owner_values = dotenv_values(OWNER_ENV_FILE)
    load_dotenv(ENV_FILE, override=False)
    if not SOURCE.is_file():
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

    if exists:
        table_count = target_table_count(admin_parts)
        if table_count:
            fail(
                f"Target database {DATABASE} already contains {table_count} public tables. "
                "Automatic setup stopped to protect existing data."
            )
        print("Target database exists and is empty; setup can resume safely.")
    else:
        print("Target database does not exist; it will be created.")

    base_environment = os.environ.copy()
    dry_run_environment = base_environment.copy()
    dry_run_environment.pop("DATABASE_URL", None)
    print("Validating the DuckDB source in read-only mode...")
    run(
        [sys.executable, "scripts/migrate_duckdb_to_postgres.py", "--source", str(SOURCE)],
        dry_run_environment,
    )

    owner_password = secrets.token_urlsafe(36)
    app_password = secrets.token_urlsafe(36)
    owner_url = service_url(admin_parts, OWNER_ROLE, owner_password)
    app_url = service_url(admin_parts, APP_ROLE, app_password)

    setup_environment = base_environment.copy()
    setup_environment.update(
        {
            "POSTGRES_ADMIN_URL": admin_url,
            "SIM_DASH_DATABASE": DATABASE,
            "SIM_DASH_OWNER_ROLE": OWNER_ROLE,
            "SIM_DASH_APP_ROLE": APP_ROLE,
            "SIM_DASH_OWNER_PASSWORD": owner_password,
            "SIM_DASH_APP_PASSWORD": app_password,
            "ANALYSIS_DB_BACKEND": "postgresql",
            "DATABASE_URL": owner_url,
        }
    )

    print("Creating PostgreSQL roles and database...")
    run([sys.executable, "scripts/bootstrap_postgres.py"], setup_environment)
    print("Applying Alembic migrations...")
    run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], setup_environment)
    print("Applying runtime role restrictions...")
    run([sys.executable, "scripts/harden_postgres_privileges.py"], setup_environment)
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

    verify_environment = setup_environment.copy()
    verify_environment["DATABASE_URL"] = app_url
    print("Verifying the application-role connection...")
    run([sys.executable, "scripts/check_postgres_connection.py"], verify_environment)
    verify_app_privileges(app_url)

    write_owner_env(owner_url)
    replace_service_env(app_url)
    print("PostgreSQL setup completed and .env now contains only service credentials.")
    print("The original DuckDB file was retained unchanged.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Never render connection strings or nested driver messages here.
        print(f"[ERROR] {error if isinstance(error, RuntimeError) else type(error).__name__}", file=sys.stderr)
        raise SystemExit(1)
