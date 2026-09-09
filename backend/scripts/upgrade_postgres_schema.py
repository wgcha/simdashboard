from __future__ import annotations

import os
import re
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
ALEMBIC_FAILURE_RULES = (
    ("OBJECT_OWNERSHIP_REQUIRED", ("must be owner of", "not owner of")),
    ("INSUFFICIENT_PRIVILEGE", ("insufficientprivilege", "permission denied", "not permitted")),
    ("LOCK_CONFLICT", ("locknotavailable", "lock timeout", "could not obtain lock", "deadlock detected", "deadlockdetected")),
    ("CONNECTION_OR_AUTHENTICATION", ("operationalerror", "could not connect", "connection refused", "connection timed out", "password authentication failed", "no pg_hba.conf entry", "server closed the connection")),
    ("DATABASE_OBJECT_ALREADY_EXISTS", ("duplicatecolumn", "duplicatetable", "duplicateobject", "already exists")),
    ("DATABASE_OBJECT_MISSING", ("undefinedtable", "undefinedcolumn", "does not exist")),
    ("DATA_CONSTRAINT_CONFLICT", ("integrityerror", "uniqueviolation", "notnullviolation", "foreignkeyviolation", "violates unique constraint", "violates foreign key constraint", "violates not-null constraint")),
    ("DEPENDENT_OBJECTS_EXIST", ("dependentobjectsstillexist", "other objects depend on it")),
    ("ALEMBIC_REVISION_ERROR", ("can't locate revision", "cannot locate revision", "multiple heads", "target database is not up to date")),
    ("MIGRATION_SQL_INVALID", ("syntaxerror", "syntax error")),
)
ALEMBIC_EXCEPTION_CLASS_RULES = (
    ("DATA_VALUE_TOO_LONG", ("stringdatarighttruncation",)),
    ("CHECK_CONSTRAINT_VIOLATION", ("checkviolation",)),
    ("INVALID_TABLE_DEFINITION", ("invalidtabledefinition",)),
    ("DATATYPE_MISMATCH", ("datatypemismatch",)),
)
ALEMBIC_GENERIC_EXCEPTION_RULES = (
    ("DATA_EXCEPTION", ("sqlalchemy.exc.dataerror", "psycopg.dataerror")),
    ("DATABASE_PROGRAMMING_ERROR", ("sqlalchemy.exc.programmingerror", "psycopg.programmingerror")),
)
ALEMBIC_CLIENT_EXCEPTION_RULES = (
    ("SQLALCHEMY_RESOURCE_CLOSED", ("sqlalchemy.exc.resourceclosederror",)),
    ("SQLALCHEMY_PENDING_ROLLBACK", ("sqlalchemy.exc.pendingrollbackerror",)),
    ("SQLALCHEMY_INVALID_REQUEST", ("sqlalchemy.exc.invalidrequesterror",)),
    ("SQLALCHEMY_STATEMENT_ERROR", ("sqlalchemy.exc.statementerror",)),
    ("SQLALCHEMY_DATABASE_ERROR", ("sqlalchemy.exc.databaseerror",)),
    ("SQLALCHEMY_DBAPI_ERROR", ("sqlalchemy.exc.dbapierror",)),
    ("SQLALCHEMY_ERROR", ("sqlalchemy.exc.sqlalchemyerror",)),
    ("ALEMBIC_COMMAND_ERROR", ("alembic.util.exc.commanderror",)),
    ("ALEMBIC_REVISION_CLIENT_ERROR", ("alembic.script.revision.revisionerror", "alembic.script.revision.resolutionerror")),
    ("CLIENT_ASSERTION_ERROR", ("assertionerror",)),
    ("CLIENT_ATTRIBUTE_ERROR", ("attributeerror",)),
    ("CLIENT_TYPE_ERROR", ("typeerror",)),
    ("CLIENT_VALUE_ERROR", ("valueerror",)),
    ("CLIENT_KEY_ERROR", ("keyerror",)),
    ("CLIENT_TEXT_ENCODING_ERROR", ("unicodedecodeerror", "unicodeencodeerror", "unicodeerror", "codec can't decode", "codec can't encode")),
    ("CLIENT_PIPE_ERROR", ("brokenpipeerror",)),
    ("CLIENT_OS_ERROR", ("connectionreseterror", "connectionabortederror", "timeouterror", "oserror", "ioerror")),
    ("CLIENT_IMPORT_ERROR", ("modulenotfounderror", "importerror")),
    ("CLIENT_MEMORY_ERROR", ("memoryerror",)),
    ("CLIENT_RUNTIME_ERROR", ("runtimeerror",)),
)
SQLALCHEMY_INVALID_REQUEST_DETAIL_RULES = (
    (
        "TRANSACTION_ALREADY_BEGUN",
        (
            "a transaction is already begun on this session",
            "has already initialized a sqlalchemy transaction() object via begin() or autobegin",
            "can't call begin() here unless rollback() or commit() is called first",
        ),
    ),
    (
        "TRANSACTION_CLOSED_IN_CONTEXT",
        (
            "can't operate on closed transaction inside context manager",
            "please complete the context manager before emitting further commands",
        ),
    ),
    ("TRANSACTION_INACTIVE", ("this transaction is inactive", "this transaction is closed")),
    ("CONNECTION_INVALIDATED", ("connection is invalidated", "can't reconnect until invalid transaction is rolled back")),
    ("CONNECTION_CLOSED", ("this connection is closed", "connection is closed")),
    ("EXECUTABLE_EXPECTED", ("not an executable object", "executable sql or text() construct expected")),
    ("BIND_PARAMETER_REQUIRED", ("a value is required for bind parameter",)),
    (
        "AUTOBEGIN_CONFLICT",
        ("autobegin is disabled on this session", "please call session.begin() to start a new transaction"),
    ),
)
SQLSTATE_EXACT_CATEGORIES = {
    "22001": "DATA_VALUE_TOO_LONG",
    "23514": "CHECK_CONSTRAINT_VIOLATION",
    "42804": "DATATYPE_MISMATCH",
    "42P16": "INVALID_TABLE_DEFINITION",
    "42501": "INSUFFICIENT_PRIVILEGE",
    "42P01": "DATABASE_OBJECT_MISSING",
    "42703": "DATABASE_OBJECT_MISSING",
    "42701": "DATABASE_OBJECT_ALREADY_EXISTS",
    "42P07": "DATABASE_OBJECT_ALREADY_EXISTS",
    "42710": "DATABASE_OBJECT_ALREADY_EXISTS",
    "23502": "DATA_CONSTRAINT_CONFLICT",
    "23503": "DATA_CONSTRAINT_CONFLICT",
    "23505": "DATA_CONSTRAINT_CONFLICT",
    "40P01": "LOCK_CONFLICT",
    "55P03": "LOCK_CONFLICT",
}
SQLSTATE_CLASS_CATEGORIES = {
    "08": "CONNECTION_OR_AUTHENTICATION",
    "22": "DATA_EXCEPTION",
    "23": "DATA_CONSTRAINT_CONFLICT",
    "28": "CONNECTION_OR_AUTHENTICATION",
    "40": "TRANSACTION_ROLLBACK",
    "42": "DATABASE_PROGRAMMING_ERROR",
    "53": "DATABASE_RESOURCE_EXHAUSTED",
    "55": "DATABASE_STATE_CONFLICT",
    "57": "DATABASE_OPERATOR_INTERVENTION",
    "58": "DATABASE_SYSTEM_ERROR",
}
SQLSTATE_PATTERN = re.compile(r"\b(?:sqlstate|sql\s+state|pgcode)(?:\s+code)?\s*[:=\[]?\s*['\"]?([0-9A-Z]{5})\b", re.IGNORECASE)
KNOWN_MIGRATION_DIAGNOSTICS = {
    "0011_result_layout_snapshot": "0011",
    "0012_project_result_profiles": "0012",
    "0013_project_result_profile_menu": "0013",
    "0014_result_profile_revs": "0014",
    "0015_legacy_drop_layout": "0015",
}
RUNNING_UPGRADE_PATTERN = re.compile(r"\brunning upgrade\s+\S+\s+->\s+([0-9a-z_]+)\b", re.IGNORECASE)
KNOWN_MIGRATION_STEP_DIAGNOSTICS = {
    "0011_ANALYSIS_TEMPLATES": "0011_ANALYSIS_TEMPLATES",
    "0011_ANALYSIS_TEMPLATES_DONE": "0011_ANALYSIS_TEMPLATES_DONE",
    "0011_REQUEST_TYPE_PROFILES": "0011_REQUEST_TYPE_PROFILES",
    "0011_REQUEST_TYPE_PROFILES_DONE": "0011_REQUEST_TYPE_PROFILES_DONE",
    "0011_SNAPSHOTS": "0011_SNAPSHOTS",
    "0011_SNAPSHOTS_DONE": "0011_SNAPSHOTS_DONE",
    "0011_ANALYSIS_TEMPLATE_STATUS_INDEX": "0011_ANALYSIS_TEMPLATE_STATUS_INDEX",
    "0011_ANALYSIS_TEMPLATE_STATUS_INDEX_DONE": "0011_ANALYSIS_TEMPLATE_STATUS_INDEX_DONE",
    "0011_SNAPSHOT_TEMPLATE_INDEX": "0011_SNAPSHOT_TEMPLATE_INDEX",
    "0011_COMPLETE": "0011_COMPLETE",
}
MIGRATION_STEP_PATTERN = re.compile(r"^SIMDASH_MIGRATION_STEP=(0011_[A-Z0-9_]+)\r?$", re.MULTILINE)


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


def _safe_alembic_failure_code(stderr: str | None, returncode: int, *, stdout: str | None = None) -> str:
    """Reduce both untrusted child streams to an allowlisted code; never return output text."""
    if returncode < 0:
        return "MIGRATION_COMMAND_FAILED_PROCESS_TERMINATED"
    combined = "\n".join((stderr or "", stdout or ""))
    normalized = combined.casefold()
    for category, exception_classes in ALEMBIC_EXCEPTION_CLASS_RULES:
        if any(exception_class in normalized for exception_class in exception_classes):
            return f"MIGRATION_COMMAND_FAILED_{category}"
    sqlstate_match = SQLSTATE_PATTERN.search(combined)
    if sqlstate_match:
        sqlstate = sqlstate_match.group(1).upper()
        category = SQLSTATE_EXACT_CATEGORIES.get(sqlstate) or SQLSTATE_CLASS_CATEGORIES.get(sqlstate[:2])
        if category:
            return f"MIGRATION_COMMAND_FAILED_{category}"
    for category, markers in ALEMBIC_FAILURE_RULES:
        if any(marker in normalized for marker in markers):
            return f"MIGRATION_COMMAND_FAILED_{category}"
    for category, exception_classes in ALEMBIC_GENERIC_EXCEPTION_RULES:
        if any(exception_class in normalized for exception_class in exception_classes):
            return f"MIGRATION_COMMAND_FAILED_{category}"
    if "sqlalchemy.exc.invalidrequesterror" in normalized:
        for category, markers in SQLALCHEMY_INVALID_REQUEST_DETAIL_RULES:
            if any(marker in normalized for marker in markers):
                return f"MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_{category}"
    for category, exception_classes in ALEMBIC_CLIENT_EXCEPTION_RULES:
        if any(exception_class in normalized for exception_class in exception_classes):
            return f"MIGRATION_COMMAND_FAILED_{category}"
    for step_marker in reversed(MIGRATION_STEP_PATTERN.findall(combined)):
        diagnostic_step = KNOWN_MIGRATION_STEP_DIAGNOSTICS.get(step_marker)
        if diagnostic_step:
            return f"MIGRATION_COMMAND_FAILED_AT_{diagnostic_step}"
    revision_match = RUNNING_UPGRADE_PATTERN.search(combined)
    if revision_match:
        diagnostic_revision = KNOWN_MIGRATION_DIAGNOSTICS.get(revision_match.group(1).casefold())
        if diagnostic_revision:
            return f"MIGRATION_COMMAND_FAILED_AT_{diagnostic_revision}"
    return "MIGRATION_COMMAND_FAILED_UNCLASSIFIED"


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


def _verify_empty_bootstrap_database(app_url: str) -> None:
    """Allow first Alembic setup only for a genuinely empty provisioned DB.

    This never provisions a database or roles.  It merely distinguishes a new,
    owner/app-role provisioned database from a database whose migration metadata
    is missing after a failed or foreign installation.
    """
    try:
        with psycopg.connect(_psycopg_url(app_url)) as connection:
            _verify_server_writable(connection, owner=False)
            _verify_app_no_ddl(connection)
            if connection.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0] is not None:
                _fail("DATABASE_SETUP_CHANGED_CONCURRENTLY")
            relations = connection.execute(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') "
                "AND n.nspname !~ '^pg_' "
                "AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
                ")"
            ).fetchone()[0]
            if relations:
                _fail("DATABASE_SETUP_REQUIRED")
    except StartupMigrationError:
        raise
    except Exception:
        _fail("APP_CONNECTION_FAILED")


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
        _fail(_safe_alembic_failure_code(result.stderr, result.returncode, stdout=result.stdout))


def upgrade_pending_schema(
    app_url: str,
    *,
    owner_env_file: Path = OWNER_ENV_FILE,
    allow_empty_bootstrap: bool = False,
) -> bool:
    try:
        state = inspect_app_revision(app_url, verify_catalog=False)
    except StartupMigrationError as error:
        if str(error) != "DATABASE_SETUP_REQUIRED" or not allow_empty_bootstrap:
            raise
        owner_url = _owner_url_from_file(owner_env_file)
        with _owner_migration_lock(app_url, owner_url):
            try:
                locked_state = inspect_app_revision(app_url, verify_catalog=False)
            except StartupMigrationError as locked_error:
                if str(locked_error) != "DATABASE_SETUP_REQUIRED":
                    raise
                _verify_empty_bootstrap_database(app_url)
                _run_alembic_child(owner_url)
                verified = inspect_app_revision(app_url, verify_catalog=True)
                if verified.pending:
                    _fail("MIGRATION_REVISION_NOT_ADVANCED")
                return True
            if not locked_state.pending:
                inspect_app_revision(app_url, verify_catalog=True)
                return False
            _run_alembic_child(owner_url)
            verified = inspect_app_revision(app_url, verify_catalog=True)
            if verified.pending:
                _fail("MIGRATION_REVISION_NOT_ADVANCED")
            return True
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
    import argparse

    parser = argparse.ArgumentParser(description="Apply pending PostgreSQL schema migrations safely.")
    parser.add_argument(
        "--allow-empty-bootstrap",
        action="store_true",
        help="Permit Alembic initialization only for an empty, pre-provisioned PostgreSQL database.",
    )
    args = parser.parse_args()
    settings = database_settings()
    if settings.backend != "postgresql":
        print("PostgreSQL startup migration skipped for the configured database backend.")
        return 0
    if not settings.database_url:
        print("[ERROR] PostgreSQL startup migration failed: APP_DATABASE_URL_MISSING.", file=sys.stderr)
        return 2
    try:
        changed = upgrade_pending_schema(settings.database_url, allow_empty_bootstrap=args.allow_empty_bootstrap)
    except StartupMigrationError as error:
        print(f"[ERROR] PostgreSQL startup migration failed: {error}.", file=sys.stderr)
        return 1
    print("PostgreSQL pending migrations applied and verified." if changed else "PostgreSQL schema is already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
