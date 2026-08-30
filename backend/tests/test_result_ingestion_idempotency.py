from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.adapters.persistence.result_ingestion import SQLResultIngestionUnitOfWork
from app.application.results.commands import ingest_result_bundle


pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, row: object | None):
        self._row = row

    def fetchone(self):
        return self._row


class _RecordingPostgresConnection:
    backend = "postgresql"

    def __init__(self, row: object | None):
        self.row = row
        self.calls: list[tuple[str, list[object]]] = []

    def execute(self, statement: str, parameters: list[object]):
        self.calls.append((statement, parameters))
        return _Cursor(self.row)


def _command(*, checksum: str | None = "bundle-sha256"):
    return {
        "project_id": "project-1",
        "request_id": "request-1",
        "load_case_id": "case-1",
        "source_type": "MASTER_FOLDER_REFRESH",
        "source_name": "case-1/manifest.json",
        "source_checksum": checksum,
        "parser_version": "test-v1",
        "parsed": {"schema_id": "test-schema", "scalars": [], "curves": [], "media": [], "note": None},
        "actor": "test",
        "metadata": {},
    }


def test_postgres_load_case_lock_precedes_run_number_allocation():
    connection = _RecordingPostgresConnection(None)
    unit_of_work = SQLResultIngestionUnitOfWork(connection, lambda prefix: prefix)

    unit_of_work.lock_load_case_ingestion("case-1")

    assert connection.calls == [
        (
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
            ["simdashboard:canonical-result-ingestion:load-case:case-1"],
        ),
    ]


def test_source_version_lookup_is_scoped_by_load_case_and_namespaced_source_key():
    connection = _RecordingPostgresConnection(("run-scoped", 3, 1))
    unit_of_work = SQLResultIngestionUnitOfWork(connection, lambda prefix: prefix)
    command = _command()
    command["source_run_id"] = "producer-run-1"

    result = unit_of_work.find_exact_source_run(command)

    assert result == {
        "analysis_run_id": "run-scoped",
        "run_no": 3,
        "source_revision": 1,
    }
    statement, parameters = connection.calls[0]
    assert "version.load_case_id=?" in statement
    assert parameters == [
        "case-1",
        "MASTER_FOLDER_REFRESH",
        "run:producer-run-1",
        "bundle-sha256",
    ]


def test_pre_ledger_exact_fallback_selects_the_latest_historical_run_deterministically():
    class _SequentialConnection:
        backend = "postgresql"

        def __init__(self):
            self.rows = iter([None, ("run-newest", 9, None)])
            self.calls: list[tuple[str, list[object]]] = []

        def execute(self, statement: str, parameters: list[object]):
            self.calls.append((statement, parameters))
            return _Cursor(next(self.rows))

    connection = _SequentialConnection()
    unit_of_work = SQLResultIngestionUnitOfWork(connection, lambda prefix: prefix)

    result = unit_of_work.find_exact_source_run(_command())

    assert result == {
        "analysis_run_id": "run-newest",
        "run_no": 9,
        "source_revision": None,
    }
    assert "ORDER BY run.run_no DESC, run.id DESC" in connection.calls[1][0]


def test_exact_source_lookup_is_reported_before_any_run_write():
    class _UnitOfWork:
        def __init__(self):
            self.calls: list[str] = []

        def validate_target(self, command):
            self.calls.append("validate_target")

        def authorize(self, command):
            self.calls.append("authorize")

        def lock_load_case_ingestion(self, load_case_id):
            self.calls.append("lock_load_case_ingestion")

        def find_exact_source_run(self, command):
            self.calls.append("find_exact_source_run")
            return {"analysis_run_id": "run-existing", "run_no": 7, "source_revision": 1}

        def find_latest_source_run(self, command):
            self.calls.append("find_latest_source_run")
            return {"analysis_run_id": "run-existing", "run_no": 7, "source_revision": 1}

        def add_skipped_job(self, *args):
            self.calls.append("add_skipped_job")

        def add_running_job(self, *args):
            raise AssertionError("duplicate source must not create a running job")

    unit_of_work = _UnitOfWork()

    @contextmanager
    def provider():
        yield unit_of_work

    outcome = ingest_result_bundle(
        _command(),
        provider,
        lambda: datetime(2026, 8, 24),
        lambda prefix: f"{prefix}-id",
    )

    assert outcome["status"] == "SKIPPED"
    assert outcome["analysis_run_id"] == "run-existing"
    assert outcome["run_no"] == 7
    assert unit_of_work.calls == [
        "validate_target",
        "authorize",
        "lock_load_case_ingestion",
        "find_exact_source_run",
        "find_latest_source_run",
        "add_skipped_job",
    ]


def test_null_checksum_keeps_duckdb_ingestion_and_allocates_run_after_the_lock():
    class _UnitOfWork:
        def __init__(self):
            self.calls: list[str] = []

        def validate_target(self, command):
            self.calls.append("validate_target")

        def authorize(self, command):
            self.calls.append("authorize")

        def lock_load_case_ingestion(self, load_case_id):
            self.calls.append("lock_load_case_ingestion")

        def next_run_no(self, load_case_id):
            self.calls.append("next_run_no")
            return 4

        def add_running_job(self, *args):
            self.calls.append("add_running_job")

        def ensure_catalog(self, *args):
            self.calls.append("ensure_catalog")

        def add_run(self, *args):
            self.calls.append("add_run")

        def add_metadata(self, *args):
            self.calls.append("add_metadata")

        def add_results(self, *args):
            self.calls.append("add_results")

        def complete_job(self, *args):
            self.calls.append("complete_job")

        def sync_status(self, *args):
            self.calls.append("sync_status")

    unit_of_work = _UnitOfWork()

    @contextmanager
    def provider():
        yield unit_of_work

    outcome = ingest_result_bundle(
        _command(checksum=None),
        provider,
        lambda: datetime(2026, 8, 24),
        lambda prefix: f"{prefix}-id",
    )

    assert outcome["status"] == "IMPORTED"
    assert unit_of_work.calls.index("lock_load_case_ingestion") < unit_of_work.calls.index("next_run_no")


def test_source_claim_migration_is_the_current_alembic_head():
    config = Config(str(BACKEND / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("0016_result_ingestion_sources")

    assert migration and migration.down_revision == "0015_legacy_drop_layout"
    assert tuple(script.get_heads()) == ("0018_batch_attempt_run_identity",)


def test_source_claim_migration_has_a_three_part_primary_key(monkeypatch: pytest.MonkeyPatch):
    path = BACKEND / "migrations" / "versions" / "0016_canonical_result_ingestion_sources.py"
    spec = spec_from_file_location("source_claim_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    statements: list[str] = []

    class _Operations:
        def execute(self, statement: str) -> None:
            statements.append(statement)

    monkeypatch.setattr(module, "op", _Operations())
    module.upgrade()

    sql = statements[0]
    assert "CREATE TABLE IF NOT EXISTS canonical_result_ingestion_sources" in sql
    assert "PRIMARY KEY (source_type, source_name, source_checksum)" in sql
