from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings
from scripts.postgres_cli import PostgresTarget, parse_target


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
OWNER_ENV_FILE = ROOT / ".postgres-owner.env"
CORE_WORKBENCH_TABLES = (
    "task_type_versions",
    "request_type_versions",
    "analysis_request_type_assignments",
    "request_work_plans",
    "request_work_items",
    "workflow_runs",
    "task_runs",
    "task_run_events",
)
MIGRATION_ADVISORY_LOCK_ID = 7_416_230_003


class StartupMigrationError(RuntimeError):
    """A safe, non-secret error code for PostgreSQL startup migration failures."""


@dataclass(frozen=True)
class RevisionState:
    current: str
    head: str

    @property
    def pending(self) -> bool:
        return self.current != self.head


def _fail(code: str) -> None:
    raise StartupMigrationError(code)


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def _code_head(script: ScriptDirectory) -> str:
    heads = script.get_heads()
    if len(heads) != 1:
        _fail("CODE_MIGRATION_HEAD_INVALID")
    return heads[0]


def _database_revision(connection: psycopg.Connection) -> str:
    relation = connection.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
    if relation is None:
        _fail("DATABASE_SETUP_REQUIRED")
    revisions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version").fetchall()]
    if len(revisions) != 1 or not revisions[0]:
        _fail("DATABASE_REVISION_INVALID")
    return str(revisions[0])


def _known_ancestors(script: ScriptDirectory, head: str) -> set[str]:
    return {revision.revision for revision in script.walk_revisions(base="base", head=head)}


def _validated_revision_state(current: str) -> RevisionState:
    script = _script_directory()
    head = _code_head(script)
    if current != head and current not in _known_ancestors(script, head):
        _fail("DATABASE_REVISION_DIVERGED")
    return RevisionState(current=current, head=head)


def _same_target(left: PostgresTarget, right: PostgresTarget) -> bool:
    return (
        left.host.casefold().rstrip(".") == right.host.casefold().rstrip(".")
        and left.port == right.port
        and left.database == right.database
    )


def _verify_server_writable(connection: psycopg.Connection, *, owner: bool) -> str:
    current_user, in_recovery, read_only = connection.execute(
        "SELECT current_user, pg_is_in_recovery(), current_setting('transaction_read_only')"
    ).fetchone()
    if in_recovery or str(read_only).lower() == "on":
        _fail("DATABASE_READ_ONLY")
    expected_role = os.getenv("SIM_DASH_OWNER_ROLE", "simdashboard_owner") if owner else os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app")
    if current_user != expected_role:
        _fail("OWNER_ROLE_INVALID" if owner else "APP_ROLE_INVALID")
    return str(current_user)


def _verify_app_no_ddl(connection: psycopg.Connection) -> None:
    attributes = connection.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    ddl_privileges = connection.execute(
        "SELECT "
        "has_database_privilege(current_user, current_database(), 'CREATE'), "
        "has_schema_privilege(current_user, 'public', 'CREATE')"
    ).fetchone()
    if attributes != (False, False, False) or ddl_privileges != (False, False):
        _fail("APP_ROLE_DDL_PRIVILEGE_DETECTED")


def _verify_owner_authority(connection: psycopg.Connection) -> None:
    database_owner, schema_owner = connection.execute(
        "SELECT "
        "pg_get_userbyid((SELECT datdba FROM pg_database WHERE datname=current_database())), "
        "pg_get_userbyid((SELECT nspowner FROM pg_namespace WHERE nspname='public'))"
    ).fetchone()
    current_user = connection.execute("SELECT current_user").fetchone()[0]
    if database_owner != current_user or schema_owner != current_user:
        _fail("OWNER_AUTHORITY_INVALID")


def _verify_catalog_access(connection: psycopg.Connection) -> None:
    for table in CORE_WORKBENCH_TABLES:
        relation = connection.execute("SELECT to_regclass(%s)", [f"public.{table}"]).fetchone()[0]
        if relation is None:
            _fail("MIGRATION_CATALOG_MISSING")
        privileges = connection.execute(
            "SELECT "
            "has_table_privilege(current_user, %s, 'SELECT'), "
            "has_table_privilege(current_user, %s, 'INSERT'), "
            "has_table_privilege(current_user, %s, 'UPDATE'), "
            "has_table_privilege(current_user, %s, 'DELETE')",
            [f"public.{table}"] * 4,
        ).fetchone()
        if privileges != (True, True, True, True):
            _fail("APP_CATALOG_PRIVILEGE_INVALID")


def inspect_app_revision(app_url: str, *, verify_catalog: bool) -> RevisionState:
    try:
        with psycopg.connect(_psycopg_url(app_url)) as connection:
            _verify_server_writable(connection, owner=False)
            _verify_app_no_ddl(connection)
            current = _database_revision(connection)
            if verify_catalog:
                _verify_catalog_access(connection)
    except StartupMigrationError:
        raise
    except Exception:
        _fail("APP_CONNECTION_FAILED")
    return _validated_revision_state(current)


def _owner_url_from_file(path: Path = OWNER_ENV_FILE) -> str:
    try:
        values = dotenv_values(path)
    except Exception:
        _fail("OWNER_CREDENTIAL_UNAVAILABLE")
    value = values.get("POSTGRES_OWNER_URL")
    if not value:
        _fail("OWNER_CREDENTIAL_UNAVAILABLE")
    return str(value)


@contextmanager
def _owner_migration_lock(app_url: str, owner_url: str):
    try:
        if not _same_target(parse_target(app_url), parse_target(owner_url)):
            _fail("OWNER_TARGET_MISMATCH")
    except StartupMigrationError:
        raise
    except Exception:
        _fail("OWNER_CREDENTIAL_INVALID")
    connection = None
    try:
        connection = psycopg.connect(_psycopg_url(owner_url), autocommit=True)
        _verify_server_writable(connection, owner=True)
        _verify_owner_authority(connection)
        connection.execute("SET lock_timeout = '30s'")
        connection.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_ADVISORY_LOCK_ID])
        yield
    except StartupMigrationError:
        raise
    except Exception:
        _fail("OWNER_LOCK_OR_CONNECTION_FAILED")
    finally:
        if connection is not None:
            try:
                connection.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_ADVISORY_LOCK_ID])
            except Exception:
                pass
            connection.close()


def _run_alembic_child(owner_url: str) -> None:
    child_environment = os.environ.copy()
    child_environment["ANALYSIS_DB_BACKEND"] = "postgresql"
    child_environment["DATABASE_URL"] = owner_url
    # Windows may otherwise encode the Alembic child output as UTF-8 while the
    # parent subprocess reader assumes the active CP949 code page.  Keep the
    # pipe contract deterministic so Korean migration output cannot crash the
    # startup preflight before it can report a safe result code.
    child_environment["PYTHONIOENCODING"] = "utf-8"
    for key in ("POSTGRES_ADMIN_URL", "SIM_DASH_OWNER_PASSWORD", "SIM_DASH_APP_PASSWORD"):
        child_environment.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND,
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        _fail("MIGRATION_COMMAND_FAILED")


def upgrade_pending_schema(app_url: str, *, owner_env_file: Path = OWNER_ENV_FILE) -> bool:
    state = inspect_app_revision(app_url, verify_catalog=False)
    if not state.pending:
        inspect_app_revision(app_url, verify_catalog=True)
        return False
    owner_url = _owner_url_from_file(owner_env_file)
    with _owner_migration_lock(app_url, owner_url):
        # A concurrent starter may have completed the same migration while this
        # process waited for the owner-scoped advisory lock.
        locked_state = inspect_app_revision(app_url, verify_catalog=False)
        if not locked_state.pending:
            inspect_app_revision(app_url, verify_catalog=True)
            return False
        _run_alembic_child(owner_url)
        verified = inspect_app_revision(app_url, verify_catalog=True)
        if verified.pending:
            _fail("MIGRATION_REVISION_NOT_ADVANCED")
        return True


def main() -> int:
    settings = database_settings()
    if settings.backend != "postgresql":
        print("PostgreSQL startup migration skipped for the configured database backend.")
        return 0
    if not settings.database_url:
        print("[ERROR] PostgreSQL startup migration failed: APP_DATABASE_URL_MISSING.", file=sys.stderr)
        return 2
    try:
        changed = upgrade_pending_schema(settings.database_url)
    except StartupMigrationError as error:
        print(f"[ERROR] PostgreSQL startup migration failed: {error}.", file=sys.stderr)
        return 1
    print("PostgreSQL pending migrations applied and verified." if changed else "PostgreSQL schema is already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
