from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
import sqlalchemy as sa


pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND / "migrations" / "versions" / "0017_run_identity_v2.py"


def _migration():
    spec = spec_from_file_location("run_identity_v2_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Operations:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, statement: object) -> None:
        self.statements.append(statement)


def _sql(statements: list[object]) -> str:
    return "\n".join(str(statement) for statement in statements)


def test_run_identity_v2_precedes_batch_attempt_identity_head() -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    revision = script.get_revision("0017_run_identity_v2")
    assert revision and revision.down_revision == "0016_result_ingestion_sources"
    identity = script.get_revision("0018_batch_attempt_run_identity")
    assert identity and identity.down_revision == "0017_run_identity_v2"
    assert tuple(script.get_heads()) == ("0018_batch_attempt_run_identity",)


def test_run_identity_v2_adds_nullable_history_columns_and_constraints(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _migration()
    operations = _Operations()
    monkeypatch.setattr(module, "op", operations)

    module.upgrade()

    sql = _sql(operations.statements)
    assert "duplicate analysis_runs run_no for load case" in sql
    for column in (
        "source_type",
        "source_checksum",
        "source_run_id",
        "conflict_policy",
        "outcome_reason",
        "replaced_analysis_run_id",
        "completed_at",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql
    assert "fk_folder_import_jobs_replaced_run" in sql
    assert "REFERENCES analysis_runs(id)" in sql
    assert "FROM pg_constraint" in sql
    assert "conrelid = 'folder_import_jobs'::regclass" in sql
    assert "ON DELETE CASCADE" not in sql
    assert "ux_analysis_runs_load_case_run_no" in sql
    assert "CREATE TABLE IF NOT EXISTS canonical_result_ingestion_source_versions" in sql
    assert "PRIMARY KEY (load_case_id, source_type, source_key, source_revision)" in sql
    assert "UNIQUE (load_case_id, source_type, source_key, source_checksum)" in sql
    assert "UNIQUE (analysis_run_id)" in sql
    assert "CHECK (source_revision > 0)" in sql
    assert "FOREIGN KEY (load_case_id) REFERENCES load_cases(id)" in sql
    assert "FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id)" in sql
    assert "FOREIGN KEY (supersedes_analysis_run_id) REFERENCES analysis_runs(id)" in sql
    assert "ON CONFLICT" not in sql
    assert "row_number() OVER" in sql
    assert "'name:' || source_name" in sql
    assert "'LEGACY_APPEND'" in sql
    assert "legacy canonical source claim must map to exactly one analysis run" in sql

    assert all(isinstance(statement, sa.sql.elements.TextClause) for statement in operations.statements)


def test_batch_attempt_identity_migration_is_idempotent_and_backfills_only_succeeded_links(monkeypatch: pytest.MonkeyPatch) -> None:
    path = BACKEND / "migrations" / "versions" / "0018_batch_attempt_run_identity.py"
    spec = spec_from_file_location("batch_attempt_identity_migration", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = _Operations()
    monkeypatch.setattr(module, "op", operations)

    module.upgrade()

    sql = _sql(operations.statements)
    assert "ALTER TABLE workflow_runs ADD COLUMN IF NOT EXISTS batch_attempt_id VARCHAR" in sql
    assert "ALTER TABLE batch_dispatches ADD COLUMN IF NOT EXISTS attempt_id VARCHAR" in sql
    assert "status = 'SUCCEEDED'" in sql
    assert "status NOT IN ('QUEUED', 'SUCCEEDED')" in sql
    assert "queued batch attempt link lacks exact runner crash-window identity" in sql
    assert "terminal or preflight batch attempt has workflow-run link" in sql
    assert "ambiguous succeeded batch attempt workflow-run mapping" in sql
    assert "succeeded batch attempt has no batch dispatch" in sql
    assert "succeeded batch attempt and dispatch provenance disagree" in sql
    assert "workflow run batch attempt identity lacks an exact attempt/run/request relationship" in sql
    assert "batch dispatch attempt identity lacks exact run/work-item/profile provenance" in sql
    assert "attempt.workflow_run_id IS DISTINCT FROM run.id" in sql
    assert "item.request_id IS DISTINCT FROM run.request_id" in sql
    assert "attempt.workflow_run_id IS DISTINCT FROM dispatch.workflow_run_id" in sql
    assert "attempt.work_item_id IS DISTINCT FROM dispatch.work_item_id" in sql
    assert "attempt.batch_profile_id IS DISTINCT FROM dispatch.batch_profile_id" in sql
    assert "UPDATE batch_dispatches AS dispatch" in sql
    assert "SET attempt_id = attempt.id" in sql
    assert "ux_workflow_runs_batch_attempt_id" in sql
    assert "ux_batch_dispatches_attempt_id" in sql
    assert "fk_workflow_runs_batch_attempt" in sql
    assert "fk_batch_dispatches_attempt" in sql
    assert "REFERENCES batch_execution_attempts(id)" in sql


def test_run_identity_v2_downgrade_removes_fk_before_nullable_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _migration()
    operations = _Operations()
    monkeypatch.setattr(module, "op", operations)

    module.downgrade()

    statements = [str(statement) for statement in operations.statements]
    constraint_index = next(index for index, statement in enumerate(statements) if "DROP CONSTRAINT" in statement)
    column_indexes = [index for index, statement in enumerate(statements) if "DROP COLUMN" in statement]
    assert column_indexes and constraint_index < min(column_indexes)
    assert statements[0] == "DROP INDEX IF EXISTS ux_analysis_runs_load_case_run_no"


def test_canonical_and_duckdb_bootstrap_schema_include_v2_history_columns() -> None:
    canonical = (BACKEND / "migrations" / "schema.sql").read_text(encoding="utf-8")
    duckdb = (BACKEND / "app" / "database.py").read_text(encoding="utf-8")
    columns = (
        "source_type",
        "source_checksum",
        "source_run_id",
        "conflict_policy",
        "outcome_reason",
        "replaced_analysis_run_id",
        "completed_at",
    )
    for column in columns:
        assert column in canonical
        assert column in duckdb
    assert "fk_folder_import_jobs_replaced_run" in canonical
    assert "CREATE TABLE IF NOT EXISTS canonical_result_ingestion_source_versions" in canonical
    assert "PRIMARY KEY (load_case_id, source_type, source_key, source_revision)" in canonical
    assert "CHECK (source_revision > 0)" in canonical
    assert "fk_canonical_result_ingestion_source_versions_load_case" in canonical
    assert "fk_canonical_result_ingestion_source_versions_run" in canonical
    assert "fk_canonical_result_ingestion_source_versions_supersedes_run" in canonical
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_analysis_runs_load_case_run_no" in canonical
    assert "CREATE TABLE IF NOT EXISTS canonical_result_ingestion_source_versions" in duckdb
    assert "PRIMARY KEY (load_case_id, source_type, source_key, source_revision)" in duckdb
    assert "CHECK (source_revision > 0)" in duckdb
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_analysis_runs_load_case_run_no" in duckdb
    assert "ALTER TABLE folder_import_jobs ADD COLUMN IF NOT EXISTS {column_name} {definition}" in duckdb
    assert "INSERT OR IGNORE INTO folder_import_jobs\n                (id, load_case_id, analysis_run_id" in duckdb
    for column in ("batch_attempt_id", "attempt_id"):
        assert column in canonical
        assert column in duckdb
    assert "ux_workflow_runs_batch_attempt_id" in canonical
    assert "ux_batch_dispatches_attempt_id" in canonical
    assert "fk_workflow_runs_batch_attempt" in canonical
    assert "fk_batch_dispatches_attempt" in canonical
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_runs_batch_attempt_id" in duckdb
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_batch_dispatches_attempt_id" in duckdb
    assert "SET attempt_id = attempt.id" in duckdb
