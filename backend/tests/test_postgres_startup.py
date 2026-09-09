from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from app import database as app_database
from scripts import upgrade_postgres_schema as startup
from scripts.postgres_cli import parse_target


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


class _CatalogConnection:
    def __init__(self, *, missing: str | None = None, denied: str | None = None):
        self.missing = missing
        self.denied = denied

    def execute(self, statement: str, parameters: list[str]):
        table = parameters[0].removeprefix("public.")
        if "to_regclass" in statement:
            return _FakeResult((None if table == self.missing else f"public.{table}",))
        privileges = (True, True, False, True) if table == self.denied else (True, True, True, True)
        return _FakeResult(privileges)


class _PostgresStartupConnection:
    def execute(self, statement: str):
        if "to_regclass('public." in statement:
            table = statement.split("to_regclass('public.", 1)[1].split("'", 1)[0]
            return _FakeResult((f"public.{table}",))
        raise AssertionError(f"unexpected startup query: {statement}")


def test_postgres_initialization_is_read_only_schema_preflight(monkeypatch: pytest.MonkeyPatch):
    connection = _PostgresStartupConnection()

    @contextmanager
    def fake_connect():
        yield connection

    monkeypatch.setattr(app_database, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(app_database, "connect", fake_connect)

    app_database.initialize_database()

    # _PostgresStartupConnection rejects every statement except to_regclass;
    # success proves startup performs no INSERT/UPDATE/DDL/seed work.


def test_system_analysis_page_backfill_creates_missing_run_comparison_idempotently():
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE load_cases (id VARCHAR PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE dashboards (id VARCHAR PRIMARY KEY, project_id VARCHAR, request_id VARCHAR, load_case_id VARCHAR, name VARCHAR, description VARCHAR, version INTEGER, definition_json JSON, updated_at TIMESTAMP)"
    )
    connection.execute(
        "CREATE TABLE dashboard_versions (dashboard_id VARCHAR, version INTEGER, definition_json JSON, created_by VARCHAR, created_at TIMESTAMP, is_valid BOOLEAN, PRIMARY KEY (dashboard_id, version))"
    )
    connection.execute("INSERT INTO load_cases VALUES ('loadcase-drop-bottom-001')")

    app_database.ensure_system_analysis_page_metadata(connection)
    app_database.ensure_system_analysis_page_metadata(connection)

    stored = connection.execute(
        "SELECT version, definition_json FROM dashboards WHERE id = 'dashboard-run-comparison-default'"
    ).fetchone()
    assert stored is not None
    definition = app_database.json_value(stored[1])
    assert stored[0] == 1
    assert definition["page"]["analysis_key"] == "run_comparison"
    assert definition["widgets"][0]["type"] == "run_comparison"
    assert connection.execute(
        "SELECT COUNT(*) FROM dashboard_versions WHERE dashboard_id = 'dashboard-run-comparison-default'"
    ).fetchone()[0] == 1


def test_already_head_never_reads_owner_credentials(monkeypatch: pytest.MonkeyPatch):
    state = startup.RevisionState(current="head", head="head")
    calls: list[bool] = []

    def inspect(_: str, *, verify_catalog: bool):
        calls.append(verify_catalog)
        return state

    monkeypatch.setattr(startup, "inspect_app_revision", inspect)
    monkeypatch.setattr(startup, "_owner_url_from_file", lambda _: pytest.fail("owner file must not be opened at head"))

    assert startup.upgrade_pending_schema("postgresql://app:secret@db:5432/dashboard") is False
    assert calls == [False, True]


def test_pending_known_revision_uses_owner_only_for_validation_and_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    states = iter(
        [
            startup.RevisionState(current="0002", head="0003"),
            startup.RevisionState(current="0002", head="0003"),
            startup.RevisionState(current="0003", head="0003"),
        ]
    )
    monkeypatch.setattr(startup, "inspect_app_revision", lambda *_args, **_kwargs: next(states))
    owner_file = tmp_path / ".postgres-owner.env"
    owner_file.write_text("POSTGRES_OWNER_URL=postgresql://owner:owner-secret@db:5432/dashboard\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    @contextmanager
    def migration_lock(_app, owner):
        calls.append(("lock", owner))
        yield

    monkeypatch.setattr(startup, "_owner_migration_lock", migration_lock)
    monkeypatch.setattr(startup, "_run_alembic_child", lambda owner: calls.append(("upgrade", owner)))

    assert startup.upgrade_pending_schema("postgresql://app:app-secret@db:5432/dashboard", owner_env_file=owner_file) is True
    assert [item[0] for item in calls] == ["lock", "upgrade"]


def test_concurrent_starter_rechecks_under_lock_and_skips_duplicate_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    states = iter(
        [
            startup.RevisionState(current="0002", head="0003"),
            startup.RevisionState(current="0003", head="0003"),
            startup.RevisionState(current="0003", head="0003"),
        ]
    )
    monkeypatch.setattr(startup, "inspect_app_revision", lambda *_args, **_kwargs: next(states))
    owner_file = tmp_path / ".postgres-owner.env"
    owner_file.write_text("POSTGRES_OWNER_URL=postgresql://owner:secret@db:5432/dashboard\n", encoding="utf-8")

    @contextmanager
    def migration_lock(_app, _owner):
        yield

    monkeypatch.setattr(startup, "_owner_migration_lock", migration_lock)
    monkeypatch.setattr(startup, "_run_alembic_child", lambda _owner: pytest.fail("duplicate migration must be skipped"))
    assert startup.upgrade_pending_schema("postgresql://app:secret@db:5432/dashboard", owner_env_file=owner_file) is False


def test_empty_preprovisioned_database_bootstraps_only_when_explicitly_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    owner_file = tmp_path / ".postgres-owner.env"
    owner_file.write_text("POSTGRES_OWNER_URL=postgresql://owner:secret@db:5432/dashboard\n", encoding="utf-8")
    calls: list[str] = []
    states = iter(
        [
            startup.StartupMigrationError("DATABASE_SETUP_REQUIRED"),
            startup.StartupMigrationError("DATABASE_SETUP_REQUIRED"),
            startup.RevisionState(current="head", head="head"),
        ]
    )

    def inspect(*_args, **_kwargs):
        item = next(states)
        if isinstance(item, Exception):
            raise item
        return item

    @contextmanager
    def migration_lock(_app, _owner):
        calls.append("lock")
        yield

    monkeypatch.setattr(startup, "inspect_app_revision", inspect)
    monkeypatch.setattr(startup, "_owner_migration_lock", migration_lock)
    monkeypatch.setattr(startup, "_verify_empty_bootstrap_database", lambda _app: calls.append("empty-check"))
    monkeypatch.setattr(startup, "_run_alembic_child", lambda _owner: calls.append("upgrade"))

    assert startup.upgrade_pending_schema(
        "postgresql://app:secret@db:5432/dashboard", owner_env_file=owner_file, allow_empty_bootstrap=True
    ) is True
    assert calls == ["lock", "empty-check", "upgrade"]


def test_missing_migration_metadata_stays_fail_closed_without_explicit_bootstrap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(startup, "inspect_app_revision", lambda *_args, **_kwargs: (_ for _ in ()).throw(startup.StartupMigrationError("DATABASE_SETUP_REQUIRED")))
    with pytest.raises(startup.StartupMigrationError, match="^DATABASE_SETUP_REQUIRED$"):
        startup.upgrade_pending_schema("postgresql://app:secret@db:5432/dashboard", owner_env_file=tmp_path / "missing.env")


def test_owner_and_app_must_target_same_database():
    app = parse_target("postgresql://app:a@db.internal:5432/dashboard")
    same = parse_target("postgresql://owner:b@DB.INTERNAL.:5432/dashboard")
    other_host = parse_target("postgresql://owner:b@other:5432/dashboard")
    other_database = parse_target("postgresql://owner:b@db.internal:5432/another")
    assert startup._same_target(app, same)
    assert not startup._same_target(app, other_host)
    assert not startup._same_target(app, other_database)

    with pytest.raises(startup.StartupMigrationError, match="^OWNER_TARGET_MISMATCH$"):
        with startup._owner_migration_lock(
            "postgresql://app:a@db.internal:5432/dashboard",
            "postgresql://owner:b@other:5432/dashboard",
        ):
            pass


def test_unknown_or_newer_database_revision_fails_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(startup, "_code_head", lambda _script: "0003_workbench_demo")
    monkeypatch.setattr(startup, "_known_ancestors", lambda _script, _head: {"0001_initial", "0002_security_audit", "0003_workbench_demo"})
    with pytest.raises(startup.StartupMigrationError, match="^DATABASE_REVISION_DIVERGED$"):
        startup._validated_revision_state("future_or_foreign_revision")


def test_missing_or_unreadable_owner_file_has_safe_error_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(startup.StartupMigrationError, match="^OWNER_CREDENTIAL_UNAVAILABLE$"):
        startup._owner_url_from_file(tmp_path / "missing.env")

    monkeypatch.setattr(startup, "dotenv_values", lambda _path: (_ for _ in ()).throw(PermissionError("private path")))
    with pytest.raises(startup.StartupMigrationError, match="^OWNER_CREDENTIAL_UNAVAILABLE$"):
        startup._owner_url_from_file(tmp_path / "protected.env")


def test_alembic_child_receives_owner_url_without_mutating_parent_or_rendering_output(monkeypatch: pytest.MonkeyPatch):
    app_url = "postgresql://app:app-secret@db:5432/dashboard"
    owner_url = "postgresql://owner:owner-secret@db:5432/dashboard"
    monkeypatch.setenv("DATABASE_URL", app_url)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured.update({"command": command, **kwargs})
        assert kwargs["env"]["DATABASE_URL"] == owner_url
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.PIPE
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return SimpleNamespace(returncode=0, stdout="owner-secret", stderr="owner-secret")

    monkeypatch.setattr(startup.subprocess, "run", fake_run)
    startup._run_alembic_child(owner_url)
    assert os.environ["DATABASE_URL"] == app_url
    assert "bootstrap_postgres.py" not in " ".join(captured["command"])


def test_failed_child_raises_only_allowlisted_code_without_rendering_secrets(monkeypatch: pytest.MonkeyPatch):
    owner_url = "postgresql://owner:owner-secret@private-db.internal/dashboard"
    monkeypatch.setattr(
        startup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=owner_url, stderr=f"unmapped failure {owner_url} password=owner-secret token=private-token"),
    )
    with pytest.raises(startup.StartupMigrationError) as raised:
        startup._run_alembic_child(owner_url)
    message = str(raised.value)
    assert message == "MIGRATION_COMMAND_FAILED_UNCLASSIFIED"
    assert "owner-secret" not in message
    assert "private-token" not in message
    assert "private-db.internal" not in message


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("psycopg.errors.InsufficientPrivilege: must be owner of table requests", "MIGRATION_COMMAND_FAILED_OBJECT_OWNERSHIP_REQUIRED"),
        ("psycopg.errors.InsufficientPrivilege: permission denied for schema public", "MIGRATION_COMMAND_FAILED_INSUFFICIENT_PRIVILEGE"),
        ("canceling statement due to lock timeout", "MIGRATION_COMMAND_FAILED_LOCK_CONFLICT"),
        ("password authentication failed for user simdashboard_owner", "MIGRATION_COMMAND_FAILED_CONNECTION_OR_AUTHENTICATION"),
        ("psycopg.errors.UndefinedColumn: column result_profile does not exist", "MIGRATION_COMMAND_FAILED_DATABASE_OBJECT_MISSING"),
        ("FAILED: Can't locate revision identified by '0014'", "MIGRATION_COMMAND_FAILED_ALEMBIC_REVISION_ERROR"),
    ],
)
def test_alembic_child_failure_classification_is_actionable_and_fixed(stderr: str, expected: str):
    assert startup._safe_alembic_failure_code(f"{stderr}\npostgresql://owner:secret@private-db/dashboard", 1) == expected


def test_terminated_alembic_child_has_fixed_safe_code():
    assert startup._safe_alembic_failure_code("password=secret", -9) == "MIGRATION_COMMAND_FAILED_PROCESS_TERMINATED"


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("sqlalchemy.exc.DataError: (psycopg.errors.StringDataRightTruncation) value too long", "MIGRATION_COMMAND_FAILED_DATA_VALUE_TOO_LONG"),
        ("sqlalchemy.exc.IntegrityError: (psycopg.errors.CheckViolation) rejected", "MIGRATION_COMMAND_FAILED_CHECK_CONSTRAINT_VIOLATION"),
        ("sqlalchemy.exc.ProgrammingError: (psycopg.errors.InvalidTableDefinition) invalid definition", "MIGRATION_COMMAND_FAILED_INVALID_TABLE_DEFINITION"),
        ("sqlalchemy.exc.ProgrammingError: (psycopg.errors.DatatypeMismatch) incompatible types", "MIGRATION_COMMAND_FAILED_DATATYPE_MISMATCH"),
        ("sqlalchemy.exc.ProgrammingError: guarded details unavailable", "MIGRATION_COMMAND_FAILED_DATABASE_PROGRAMMING_ERROR"),
    ],
)
def test_alembic_exception_classes_map_to_fixed_diagnostics(stderr: str, expected: str):
    diagnostic = startup._safe_alembic_failure_code(f"{stderr}\npostgresql://owner:secret@private-db/dashboard token=private-token", 1)
    assert diagnostic == expected
    assert all(secret not in diagnostic for secret in ("secret", "private-db", "private-token"))


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("driver failure SQLSTATE: 22001", "MIGRATION_COMMAND_FAILED_DATA_VALUE_TOO_LONG"),
        ("driver failure SQL state [23514]", "MIGRATION_COMMAND_FAILED_CHECK_CONSTRAINT_VIOLATION"),
        ("driver failure pgcode='42804'", "MIGRATION_COMMAND_FAILED_DATATYPE_MISMATCH"),
        ("driver failure SQLSTATE 42P16", "MIGRATION_COMMAND_FAILED_INVALID_TABLE_DEFINITION"),
        ("driver failure SQLSTATE=42ZZZ", "MIGRATION_COMMAND_FAILED_DATABASE_PROGRAMMING_ERROR"),
    ],
)
def test_sqlstate_maps_to_allowlisted_category_without_echoing_input(stderr: str, expected: str):
    diagnostic = startup._safe_alembic_failure_code(f"{stderr} password=secret host=private-db", 1)
    assert diagnostic == expected
    assert "secret" not in diagnostic and "private-db" not in diagnostic and "42ZZZ" not in diagnostic


def test_alembic_child_classifies_exception_from_stdout_without_exposing_either_stream(monkeypatch: pytest.MonkeyPatch):
    owner_url = "postgresql://owner:owner-secret@private-db.internal/dashboard"
    monkeypatch.setattr(
        startup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout=f"sqlalchemy.exc.DataError: psycopg.errors.StringDataRightTruncation password=stdout-secret {owner_url}",
            stderr="token=stderr-secret host=internal-db",
        ),
    )
    with pytest.raises(startup.StartupMigrationError) as raised:
        startup._run_alembic_child(owner_url)
    diagnostic = str(raised.value)
    assert diagnostic == "MIGRATION_COMMAND_FAILED_DATA_VALUE_TOO_LONG"
    assert all(secret not in diagnostic for secret in ("stdout-secret", "stderr-secret", "private-db", "internal-db"))


def test_known_running_revision_is_safe_fallback_when_combined_stream_has_no_exception_class():
    diagnostic = startup._safe_alembic_failure_code(
        "stderr password=stderr-secret",
        1,
        stdout="INFO Running upgrade 0010_task_batch_identity -> 0011_result_layout_snapshot, add snapshot token=stdout-secret",
    )
    assert diagnostic == "MIGRATION_COMMAND_FAILED_AT_0011"
    assert "secret" not in diagnostic and "result_layout_snapshot" not in diagnostic


def test_arbitrary_running_revision_is_never_reflected_and_known_exception_wins_revision_fallback():
    arbitrary = startup._safe_alembic_failure_code("", 1, stdout="Running upgrade base -> attacker_secret_revision password=secret")
    classified = startup._safe_alembic_failure_code(
        "psycopg.errors.DatatypeMismatch",
        1,
        stdout="Running upgrade 0010_task_batch_identity -> 0011_result_layout_snapshot",
    )
    assert arbitrary == "MIGRATION_COMMAND_FAILED_UNCLASSIFIED"
    assert "attacker" not in arbitrary and "secret" not in arbitrary
    assert classified == "MIGRATION_COMMAND_FAILED_DATATYPE_MISMATCH"


@pytest.mark.parametrize(
    "step",
    [
        "0011_ANALYSIS_TEMPLATES",
        "0011_ANALYSIS_TEMPLATES_DONE",
        "0011_REQUEST_TYPE_PROFILES",
        "0011_REQUEST_TYPE_PROFILES_DONE",
        "0011_SNAPSHOTS",
        "0011_SNAPSHOTS_DONE",
        "0011_ANALYSIS_TEMPLATE_STATUS_INDEX",
        "0011_ANALYSIS_TEMPLATE_STATUS_INDEX_DONE",
        "0011_SNAPSHOT_TEMPLATE_INDEX",
        "0011_COMPLETE",
    ],
)
def test_known_0011_step_marker_is_allowlisted_without_reflecting_stream(step: str):
    diagnostic = startup._safe_alembic_failure_code(
        "stderr password=stderr-secret",
        1,
        stdout=f"SIMDASH_MIGRATION_STEP={step}\nstdout token=stdout-secret host=private-db",
    )
    assert diagnostic == f"MIGRATION_COMMAND_FAILED_AT_{step}"
    assert all(secret not in diagnostic for secret in ("stderr-secret", "stdout-secret", "private-db"))


def test_latest_known_completion_step_wins_and_arbitrary_step_is_never_reflected():
    diagnostic = startup._safe_alembic_failure_code(
        "",
        1,
        stdout="\n".join((
            "SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATES",
            "SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATES_DONE",
            "SIMDASH_MIGRATION_STEP=0011_ATTACKER_SECRET",
        )),
    )
    assert diagnostic == "MIGRATION_COMMAND_FAILED_AT_0011_ANALYSIS_TEMPLATES_DONE"
    assert "ATTACKER" not in diagnostic and "SECRET" not in diagnostic


def test_final_index_start_and_post_operation_complete_are_distinct():
    before = startup._safe_alembic_failure_code("", 1, stdout="SIMDASH_MIGRATION_STEP=0011_SNAPSHOT_TEMPLATE_INDEX")
    after = startup._safe_alembic_failure_code(
        "",
        1,
        stdout="SIMDASH_MIGRATION_STEP=0011_SNAPSHOT_TEMPLATE_INDEX\nSIMDASH_MIGRATION_STEP=0011_COMPLETE",
    )
    assert before == "MIGRATION_COMMAND_FAILED_AT_0011_SNAPSHOT_TEMPLATE_INDEX"
    assert after == "MIGRATION_COMMAND_FAILED_AT_0011_COMPLETE"


@pytest.mark.parametrize(
    ("exception_class", "expected"),
    [
        ("AssertionError", "MIGRATION_COMMAND_FAILED_CLIENT_ASSERTION_ERROR"),
        ("TypeError", "MIGRATION_COMMAND_FAILED_CLIENT_TYPE_ERROR"),
        ("AttributeError", "MIGRATION_COMMAND_FAILED_CLIENT_ATTRIBUTE_ERROR"),
        ("ValueError", "MIGRATION_COMMAND_FAILED_CLIENT_VALUE_ERROR"),
        ("KeyError", "MIGRATION_COMMAND_FAILED_CLIENT_KEY_ERROR"),
        ("UnicodeDecodeError", "MIGRATION_COMMAND_FAILED_CLIENT_TEXT_ENCODING_ERROR"),
        ("BrokenPipeError", "MIGRATION_COMMAND_FAILED_CLIENT_PIPE_ERROR"),
        ("OSError", "MIGRATION_COMMAND_FAILED_CLIENT_OS_ERROR"),
        ("sqlalchemy.exc.StatementError", "MIGRATION_COMMAND_FAILED_SQLALCHEMY_STATEMENT_ERROR"),
        ("sqlalchemy.exc.DatabaseError", "MIGRATION_COMMAND_FAILED_SQLALCHEMY_DATABASE_ERROR"),
        ("sqlalchemy.exc.ResourceClosedError", "MIGRATION_COMMAND_FAILED_SQLALCHEMY_RESOURCE_CLOSED"),
        ("alembic.util.exc.CommandError", "MIGRATION_COMMAND_FAILED_ALEMBIC_COMMAND_ERROR"),
    ],
)
def test_client_exception_class_precedes_complete_marker_without_exposing_stream(exception_class: str, expected: str):
    diagnostic = startup._safe_alembic_failure_code(
        f"Traceback: {exception_class}: password=stderr-secret host=private-db",
        1,
        stdout="SIMDASH_MIGRATION_STEP=0011_COMPLETE\ntoken=stdout-secret",
    )
    assert diagnostic == expected
    assert all(secret not in diagnostic for secret in ("stderr-secret", "stdout-secret", "private-db"))


def test_run_child_reports_safe_client_exception_after_complete_without_raw_stream(monkeypatch: pytest.MonkeyPatch):
    owner_url = "postgresql://owner:owner-secret@private-db.internal/dashboard"
    monkeypatch.setattr(
        startup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="SIMDASH_MIGRATION_STEP=0011_COMPLETE\ntoken=stdout-secret",
            stderr=f"Traceback: AssertionError password=stderr-secret {owner_url}",
        ),
    )
    with pytest.raises(startup.StartupMigrationError) as raised:
        startup._run_alembic_child(owner_url)
    diagnostic = str(raised.value)
    assert diagnostic == "MIGRATION_COMMAND_FAILED_CLIENT_ASSERTION_ERROR"
    assert all(secret not in diagnostic for secret in ("owner-secret", "stdout-secret", "stderr-secret", "private-db"))


def test_unknown_client_exception_is_not_reflected_and_complete_fallback_remains_safe():
    diagnostic = startup._safe_alembic_failure_code(
        "SuperSecretCustomError password=secret",
        1,
        stdout="SIMDASH_MIGRATION_STEP=0011_COMPLETE",
    )
    assert diagnostic == "MIGRATION_COMMAND_FAILED_AT_0011_COMPLETE"
    assert "SuperSecret" not in diagnostic and "secret" not in diagnostic


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "A transaction is already begun on this Session.",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_TRANSACTION_ALREADY_BEGUN",
        ),
        (
            "Can't operate on closed transaction inside context manager. Please complete the context manager before emitting further commands.",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_TRANSACTION_CLOSED_IN_CONTEXT",
        ),
        (
            "This transaction is inactive",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_TRANSACTION_INACTIVE",
        ),
        (
            "Can't reconnect until invalid transaction is rolled back. Please rollback() fully before proceeding",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_CONNECTION_INVALIDATED",
        ),
        (
            "This Connection is closed",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_CONNECTION_CLOSED",
        ),
        (
            "Not an executable object: password=sql-secret",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_EXECUTABLE_EXPECTED",
        ),
        (
            "A value is required for bind parameter 'private_bind'",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_BIND_PARAMETER_REQUIRED",
        ),
        (
            "Autobegin is disabled on this Session; please call session.begin() to start a new transaction",
            "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST_AUTOBEGIN_CONFLICT",
        ),
    ],
)
def test_invalid_request_detail_is_allowlisted_and_precedes_generic_category(message: str, expected: str):
    diagnostic = startup._safe_alembic_failure_code(
        f"sqlalchemy.exc.InvalidRequestError: {message} password=stderr-secret host=private-db",
        1,
        stdout="SIMDASH_MIGRATION_STEP=0011_COMPLETE\ntoken=stdout-secret",
    )
    assert diagnostic == expected
    assert all(secret not in diagnostic for secret in ("stderr-secret", "stdout-secret", "private-db", "private_bind", "sql-secret"))


def test_invalid_request_detail_marker_requires_invalid_request_class():
    diagnostic = startup._safe_alembic_failure_code(
        "Attacker text: A transaction is already begun on this Session. password=secret",
        1,
        stdout="SIMDASH_MIGRATION_STEP=0011_COMPLETE",
    )
    assert diagnostic == "MIGRATION_COMMAND_FAILED_AT_0011_COMPLETE"
    assert "secret" not in diagnostic


def test_unknown_invalid_request_message_uses_generic_safe_category_without_reflection():
    diagnostic = startup._safe_alembic_failure_code(
        "sqlalchemy.exc.InvalidRequestError: private-new-message password=secret",
        1,
        stdout="SIMDASH_MIGRATION_STEP=0011_COMPLETE",
    )
    assert diagnostic == "MIGRATION_COMMAND_FAILED_SQLALCHEMY_INVALID_REQUEST"
    assert "private-new-message" not in diagnostic and "secret" not in diagnostic


def test_0011_safe_markers_bracket_each_ddl_step_and_finish_after_final_index():
    source = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0011_request_result_layout_snapshots.py").read_text(encoding="utf-8")
    steps = (
        ("SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATES", "CREATE TABLE IF NOT EXISTS analysis_template_versions", "SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATES_DONE"),
        ("SIMDASH_MIGRATION_STEP=0011_REQUEST_TYPE_PROFILES", "CREATE TABLE IF NOT EXISTS request_type_result_profiles", "SIMDASH_MIGRATION_STEP=0011_REQUEST_TYPE_PROFILES_DONE"),
        ("SIMDASH_MIGRATION_STEP=0011_SNAPSHOTS", "CREATE TABLE IF NOT EXISTS request_result_layout_snapshots", "SIMDASH_MIGRATION_STEP=0011_SNAPSHOTS_DONE"),
        ("SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATE_STATUS_INDEX", "CREATE INDEX IF NOT EXISTS ix_analysis_template_versions_status", "SIMDASH_MIGRATION_STEP=0011_ANALYSIS_TEMPLATE_STATUS_INDEX_DONE"),
        ("SIMDASH_MIGRATION_STEP=0011_SNAPSHOT_TEMPLATE_INDEX", "CREATE INDEX IF NOT EXISTS ix_request_result_layout_snapshots_template", "SIMDASH_MIGRATION_STEP=0011_COMPLETE"),
    )
    positions = [(source.index(before), source.index(statement), source.index(after)) for before, statement, after in steps]
    assert all(before_position < statement_position < after_position for before_position, statement_position, after_position in positions)
    assert [before_position for before_position, _, _ in positions] == sorted(before_position for before_position, _, _ in positions)


def test_code_graph_has_one_head_and_known_ancestors():
    script = startup._script_directory()
    head = startup._code_head(script)
    ancestors = startup._known_ancestors(script, head)
    assert head in ancestors
    assert {"0001_initial", "0002_security_audit", "0003_workbench_demo"} <= ancestors


def test_startup_catalog_includes_request_type_assignment_and_fails_closed():
    assert "analysis_request_type_assignments" in startup.CORE_WORKBENCH_TABLES
    assert {"request_work_plans", "request_work_items"} <= set(startup.CORE_WORKBENCH_TABLES)
    assert len(startup.CORE_WORKBENCH_TABLES) == 8

    with pytest.raises(startup.StartupMigrationError, match="^MIGRATION_CATALOG_MISSING$"):
        startup._verify_catalog_access(_CatalogConnection(missing="analysis_request_type_assignments"))

    with pytest.raises(startup.StartupMigrationError, match="^APP_CATALOG_PRIVILEGE_INVALID$"):
        startup._verify_catalog_access(_CatalogConnection(denied="analysis_request_type_assignments"))


def test_start_scripts_use_connection_preflight_and_readiness_cleanup_without_migration():
    root = Path(__file__).resolve().parents[2]
    postgres_start = (root / "start-postgresql.ps1").read_text(encoding="utf-8")
    general_start = (root / "start.ps1").read_text(encoding="utf-8")
    stop_script = (root / "stop.ps1").read_text(encoding="utf-8")
    migration_source = (root / "backend" / "scripts" / "upgrade_postgres_schema.py").read_text(encoding="utf-8")

    assert "& $startScript -DatabaseBackend postgresql" in postgres_start
    assert "upgrade_postgres_schema.py" not in general_start
    assert "check_database_startup_preflight.py" in general_start
    assert general_start.index("check_database_startup_preflight.py") < general_start.index("Checking the existing PostgreSQL connection")
    assert general_start.index("check_database_startup_preflight.py") < general_start.index("if (Test-Path -LiteralPath $PidFile)")
    assert "Get-CurrentBackendDatabaseBackend" in general_start
    assert "Run stop.ps1 and start again with the intended database configuration" in general_start
    assert general_start.index("check_postgres_connection.py") < general_start.index("Start-Process -FilePath $Python")
    assert "Wait-HttpReady" in general_start
    assert "Stop-ProcessTree" in general_start
    assert "Remove-Item -LiteralPath $PidFile" in general_start
    assert "--strictPort" in general_start
    assert "[int]$BackendPort = 8000" in general_start
    assert "[int]$FrontendPort = 5173" in general_start
    assert "Test-LocalPortInUse -Port $BackendPort" in general_start
    assert "Test-LocalPortInUse -Port $FrontendPort" in general_start
    assert '$env:VITE_API_TARGET = "http://127.0.0.1:$BackendPort"' in general_start
    assert "Test-CurrentServerEndpoint -Role $Role -Port $ExpectedPort" in general_start
    assert "[System.IO.Path]::GetFullPath($ExpectedExecutable)" in general_start
    assert "Get-NetTCPConnection" not in general_start
    assert "Get-NetTCPConnection" in stop_script
    assert "netstat.exe" in stop_script
    assert "Test-IsAnalysisCanvasListener" in stop_script
    assert "Test-IsAnalysisCanvasEndpoint" in stop_script
    assert "Analysis Canvas API" in stop_script
    assert "Test-ContainsWorkspacePath" in stop_script
    assert "if (-not $belongsToWorkspace) { return $false }" in stop_script
    assert "unverified listener is never terminated" in stop_script
    assert "could not be verified as an Analysis Canvas server" in stop_script
    assert "[int]$BackendPort = 0" in stop_script
    assert "[int]$FrontendPort = 0" in stop_script
    assert general_start.index("catch {") < general_start.index("Backend health database mismatch.")
    assert "process exited during readiness verification" in general_start
    assert "ANALYSIS_DATABASE_PREFLIGHT_COMPLETE" not in general_start
    assert ".setup-recovery-required.json" in general_start
    assert "pg_advisory_lock" in migration_source
    assert "bootstrap_postgres" not in migration_source
    assert "setup_local_postgres" not in migration_source
    assert "migrate_duckdb_to_postgres" not in migration_source
