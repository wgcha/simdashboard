from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from app import database as app_database
from app.repositories import workbench
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


def test_postgres_initialization_backfills_system_analysis_pages(monkeypatch: pytest.MonkeyPatch):
    connection = _PostgresStartupConnection()
    calls: list[tuple[str, object]] = []

    @contextmanager
    def fake_connect():
        yield connection

    monkeypatch.setattr(app_database, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(app_database, "connect", fake_connect)
    monkeypatch.setattr(
        workbench,
        "ensure_default_workbench_catalog",
        lambda conn: calls.append(("workbench", conn)),
    )
    monkeypatch.setattr(
        app_database,
        "ensure_project_quality_thresholds",
        lambda conn: calls.append(("quality_thresholds", conn)),
    )
    monkeypatch.setattr(
        app_database,
        "ensure_system_analysis_page_metadata",
        lambda conn: calls.append(("analysis_pages", conn)),
    )

    app_database.initialize_database()

    assert calls == [("workbench", connection), ("quality_thresholds", connection), ("analysis_pages", connection)]


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


def test_failed_child_raises_only_safe_code(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        startup.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="postgresql://owner:secret@db/dashboard", stderr="secret"),
    )
    with pytest.raises(startup.StartupMigrationError, match="^MIGRATION_COMMAND_FAILED$"):
        startup._run_alembic_child("postgresql://owner:secret@db/dashboard")


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


def test_start_scripts_use_pending_migration_preflight_and_readiness_cleanup():
    root = Path(__file__).resolve().parents[2]
    postgres_start = (root / "start-postgresql.ps1").read_text(encoding="utf-8")
    general_start = (root / "start.ps1").read_text(encoding="utf-8")
    stop_script = (root / "stop.ps1").read_text(encoding="utf-8")
    postgres_batch = (root / "start-postgresql.bat").read_text(encoding="utf-8")
    migration_source = (root / "backend" / "scripts" / "upgrade_postgres_schema.py").read_text(encoding="utf-8")

    assert "& $StartScript" in postgres_start
    assert "upgrade_postgres_schema.py" in general_start
    assert general_start.index("upgrade_postgres_schema.py") < general_start.index("check_postgres_connection.py") < general_start.index("Start-Process -FilePath $Python")
    assert "Wait-HttpReady" in general_start
    assert "Stop-ProcessTree" in general_start
    assert "Remove-Item -LiteralPath $PidFile" in general_start
    assert "--strictPort" in general_start
    assert "Test-LocalPortInUse -Port 8000" in general_start
    assert "Test-LocalPortInUse -Port 5173" in general_start
    assert "Get-NetTCPConnection" not in general_start
    assert "Get-NetTCPConnection" in stop_script
    assert "netstat.exe" in stop_script
    assert "Test-IsAnalysisCanvasListener" in stop_script
    assert "Test-IsAnalysisCanvasEndpoint" in stop_script
    assert "Analysis Canvas API" in stop_script
    assert "unverified listener is never terminated" in stop_script
    assert "could not be verified as an Analysis Canvas server" in stop_script
    assert "occupied" in postgres_batch and "port" in postgres_batch
    assert general_start.index("catch {") < general_start.index("Backend health database mismatch.")
    assert "process exited during readiness verification" in general_start
    assert "ANALYSIS_DATABASE_PREFLIGHT_COMPLETE" not in general_start
    assert ".setup-recovery-required.json" in general_start
    assert "pg_advisory_lock" in migration_source
    assert "bootstrap_postgres" not in migration_source
    assert "setup_local_postgres" not in migration_source
    assert "migrate_duckdb_to_postgres" not in migration_source
