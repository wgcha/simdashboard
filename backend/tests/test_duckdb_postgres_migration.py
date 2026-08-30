from __future__ import annotations

import json
import re
import sys

import duckdb
import pytest

from scripts import migrate_duckdb_to_postgres as migration


pytestmark = pytest.mark.unit


def test_recovery_lease_preflight_blocks_active_leases_and_preserves_legacy_sources() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """CREATE TABLE batch_execution_attempts (
        id VARCHAR, recovery_lease_owner_id VARCHAR, recovery_lease_token VARCHAR,
        recovery_lease_generation BIGINT, recovery_lease_acquired_at TIMESTAMP,
        recovery_lease_expires_at TIMESTAMP)"""
    )
    connection.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-1', 'runner', 'opaque-token', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '1 minute')")
    assert migration.recovery_lease_preflight(connection) == {
        "status": "blocked_active_recovery_leases",
        "active_recovery_lease_count": 1,
        "residual_recovery_lease_count": 1,
        "invalid_recovery_lease_count": 0,
        "safe_to_execute": False,
    }
    # An expired lease still carries ownership metadata.  Transfer must not
    # mistake it for a safely inactive row before a new PostgreSQL epoch.
    connection.execute(
        "UPDATE batch_execution_attempts SET recovery_lease_acquired_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '2 minutes', recovery_lease_expires_at=(CURRENT_TIMESTAMP AT TIME ZONE 'UTC') - INTERVAL '1 minute'"
    )
    assert migration.recovery_lease_preflight(connection) == {
        "status": "blocked_residual_recovery_leases",
        "active_recovery_lease_count": 0,
        "residual_recovery_lease_count": 1,
        "invalid_recovery_lease_count": 0,
        "safe_to_execute": False,
    }
    connection.execute("UPDATE batch_execution_attempts SET recovery_lease_owner_id=NULL, recovery_lease_token=NULL, recovery_lease_acquired_at=NULL, recovery_lease_expires_at=NULL, recovery_lease_generation=0")
    assert migration.recovery_lease_preflight(connection) == {
        "status": "clear",
        "active_recovery_lease_count": 0,
        "residual_recovery_lease_count": 0,
        "invalid_recovery_lease_count": 0,
        "safe_to_execute": True,
    }
    connection.execute("UPDATE batch_execution_attempts SET recovery_lease_generation=-1")
    assert migration.recovery_lease_preflight(connection)["status"] == "blocked_invalid_recovery_leases"
    connection.execute("UPDATE batch_execution_attempts SET recovery_lease_generation=NULL")
    assert migration.recovery_lease_preflight(connection)["status"] == "blocked_invalid_recovery_leases"
    connection.execute("UPDATE batch_execution_attempts SET recovery_lease_generation=1, recovery_lease_owner_id='orphaned-owner'")
    assert migration.recovery_lease_preflight(connection)["status"] == "blocked_invalid_recovery_leases"
    connection.execute("DROP TABLE batch_execution_attempts")
    connection.execute("CREATE TABLE batch_execution_attempts (id VARCHAR)")
    assert migration.recovery_lease_preflight(connection) == {
        "status": "legacy_column_missing",
        "active_recovery_lease_count": 0,
        "safe_to_execute": True,
    }
    connection.execute("DROP TABLE batch_execution_attempts")
    connection.execute("CREATE TABLE batch_execution_attempts (id VARCHAR, recovery_lease_token VARCHAR)")
    assert migration.recovery_lease_preflight(connection) == {
        "status": "blocked_partial_lease_schema",
        "active_recovery_lease_count": None,
        "safe_to_execute": False,
    }


def test_relationship_audit_rejects_exact_run_and_dispatch_identity_mismatches() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE workflow_runs (id VARCHAR, batch_attempt_id VARCHAR)")
    connection.execute("CREATE TABLE batch_execution_attempts (id VARCHAR, workflow_run_id VARCHAR, work_item_id VARCHAR, batch_profile_id VARCHAR)")
    connection.execute("CREATE TABLE batch_dispatches (id VARCHAR, attempt_id VARCHAR, workflow_run_id VARCHAR, work_item_id VARCHAR, batch_profile_id VARCHAR)")
    connection.execute("INSERT INTO workflow_runs VALUES ('run-1', 'attempt-1')")
    connection.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-1', 'run-other', 'item-1', 'profile-1')")
    connection.execute("INSERT INTO batch_dispatches VALUES ('dispatch-1', 'attempt-1', 'run-1', 'item-other', 'profile-1')")

    findings = migration.relationship_audit(connection)
    hard = {item["relationship"]: item.get("mismatch_count") for item in findings if item["severity"] == "hard"}
    assert hard["workflow_runs.batch_attempt_id<->batch_execution_attempts.workflow_run_id"] == 1
    assert hard["batch_execution_attempts.workflow_run_id<->workflow_runs.batch_attempt_id"] == 1
    assert hard["batch_dispatches.attempt_id->batch_execution_attempts provenance"] == 1


def test_relationship_audit_rejects_succeeded_dispatch_absence_and_runner_identity_mismatch() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE request_work_items (id VARCHAR, request_id VARCHAR, node_key VARCHAR, task_type_id VARCHAR, task_type_version INTEGER)")
    connection.execute("CREATE TABLE workflow_runs (id VARCHAR, batch_attempt_id VARCHAR, request_id VARCHAR, execution_mode VARCHAR, status VARCHAR, created_by VARCHAR, definition_json JSON)")
    connection.execute("CREATE TABLE batch_execution_attempts (id VARCHAR, workflow_run_id VARCHAR, work_item_id VARCHAR, batch_profile_id VARCHAR, status VARCHAR, created_by VARCHAR, completed_at TIMESTAMP)")
    connection.execute("CREATE TABLE batch_dispatches (id VARCHAR, attempt_id VARCHAR, workflow_run_id VARCHAR, work_item_id VARCHAR, batch_profile_id VARCHAR)")
    connection.execute("INSERT INTO request_work_items VALUES ('item-1', 'request-1', 'node-1', 'task-1', 1)")
    connection.execute("INSERT INTO workflow_runs VALUES ('run-1', 'attempt-1', 'request-1', 'DEMO_ONLY', 'SUCCEEDED', 'owner-1', '{\"nodes\":[{\"node_key\":\"node-1\",\"task_type_id\":\"task-1\",\"task_type_version\":1,\"depends_on\":[]}]}')")
    connection.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-1', 'run-1', 'item-1', 'profile-1', 'SUCCEEDED', 'owner-1', CURRENT_TIMESTAMP)")
    hard = {item["relationship"]: item.get("mismatch_count") for item in migration.relationship_audit(connection) if item["severity"] == "hard"}
    assert hard["succeeded batch_execution_attempt has exact dispatch"] == 1
    connection.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-2', NULL, 'item-1', 'profile-1', 'SUCCEEDED', 'owner-1', CURRENT_TIMESTAMP)")
    hard = {item["relationship"]: item.get("mismatch_count") for item in migration.relationship_audit(connection) if item["severity"] == "hard"}
    assert hard["succeeded batch_execution_attempt has exact dispatch"] == 2
    connection.execute("UPDATE workflow_runs SET created_by='wrong-owner'")
    hard = {item["relationship"]: item.get("mismatch_count") for item in migration.relationship_audit(connection) if item["severity"] == "hard"}
    assert hard["batch_execution_attempts.workflow_run_id exact runner identity"] == 1


def test_relationship_audit_rejects_duplicate_identity_and_invalid_dispatch_status() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE workflow_runs (id VARCHAR, batch_attempt_id VARCHAR)")
    connection.execute("INSERT INTO workflow_runs VALUES ('run-1', 'attempt-1'), ('run-2', 'attempt-1')")
    connection.execute("CREATE TABLE batch_dispatches (id VARCHAR, attempt_id VARCHAR, workflow_run_id VARCHAR, status VARCHAR)")
    connection.execute(
        "INSERT INTO batch_dispatches VALUES "
        "('dispatch-1', 'attempt-1', 'run-1', 'RECORDED_DEMO'), "
        "('dispatch-2', 'attempt-1', 'run-1', 'WRONG_STATUS')"
    )
    hard = {
        item["relationship"]: item.get("mismatch_count")
        for item in migration.relationship_audit(connection)
        if item["severity"] == "hard"
    }
    assert hard["workflow_runs.batch_attempt_id unique"] == 1
    assert hard["batch_dispatches.attempt_id unique"] == 1
    assert hard["batch_dispatches.workflow_run_id unique"] == 1
    assert hard["batch_dispatches recorded demo status"] == 1


def test_staged_reference_columns_are_exactly_null_during_initial_copy() -> None:
    columns = ["id", "batch_attempt_id"]
    assert migration.transform_source_row(
        "workflow_runs", columns, ("run-1", "attempt-1"),
        stage_columns=migration.STAGED_REFERENCE_COLUMNS["workflow_runs"],
    ) == ("run-1", None)


def test_source_manifest_materializes_only_legacy_lease_defaults() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE batch_execution_attempts (id VARCHAR)")
    connection.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-1')")
    columns = migration.comparison_columns(migration.source_table_columns(connection, ["batch_execution_attempts"]))
    source = migration.source_manifest(connection, ["batch_execution_attempts"], source_columns=columns)
    expected_columns = sorted(["id", *migration.LEGACY_COLUMN_DEFAULTS["batch_execution_attempts"]])
    expected = migration.digest_rows(
        [("attempt-1", None, None, 0, None, None)], expected_columns, set()
    )
    assert source == {"batch_execution_attempts": {"count": expected[0], "checksum": expected[1]}}


def test_legacy_defaults_preserve_row_aware_pre_0018_and_pre_0019_values() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE request_steps (id VARCHAR, status VARCHAR, blocked_reason VARCHAR)")
    connection.execute("INSERT INTO request_steps VALUES ('step-1', 'READY', 'true')")
    connection.execute("CREATE TABLE request_work_items (id VARCHAR, status VARCHAR)")
    connection.execute("INSERT INTO request_work_items VALUES ('item-done', 'COMPLETED'), ('item-ready', 'READY')")
    connection.execute("CREATE TABLE workflow_runs (id VARCHAR)")
    connection.execute("CREATE TABLE batch_dispatches (id VARCHAR)")
    connection.execute("CREATE TABLE folder_import_jobs (id VARCHAR)")
    connection.execute("CREATE TABLE batch_path_profiles (id VARCHAR)")
    connection.execute("CREATE TABLE batch_path_profile_versions (id VARCHAR, version INTEGER)")
    source_columns = migration.comparison_columns(
        migration.source_table_columns(
            connection,
            ["request_steps", "request_work_items", "workflow_runs", "batch_dispatches", "folder_import_jobs", "batch_path_profiles", "batch_path_profile_versions"],
        )
    )
    assert source_columns["workflow_runs"] == ["batch_attempt_id", "id"]
    assert source_columns["batch_dispatches"] == ["attempt_id", "id"]
    assert source_columns["folder_import_jobs"] == sorted(["id", *migration.LEGACY_COLUMN_DEFAULTS["folder_import_jobs"]])
    assert migration.normalized_source_values("request_steps", ["id", "status", "blocked_reason"], ("step-1", "READY", "true")) == {
        "id": "step-1", "status": "READY", "blocked_reason": None, "is_optional": True,
    }
    assert migration.normalized_source_values("request_work_items", ["id", "status"], ("item-done", "COMPLETED"))["progress"] == 100
    assert migration.normalized_source_values("request_work_items", ["id", "status"], ("item-ready", "READY"))["progress"] == 0
    # The exact canonical projection is shared by the checksum and INSERT path.
    assert migration.transform_source_row("workflow_runs", ["id"], ("run-1",)) == ("run-1",)
    assert migration.transform_source_row(
        "request_steps",
        ["id", "status", "blocked_reason"],
        ("step-1", "READY", "true"),
        output_columns=source_columns["request_steps"],
    ) == (None, "step-1", True, "READY")
    assert migration.transform_source_row(
        "request_work_items",
        ["id", "status"],
        ("item-done", "COMPLETED"),
        output_columns=source_columns["request_work_items"],
    ) == (None, "item-done", 100, None, None, "COMPLETED")


def test_copy_inserts_the_same_materialized_legacy_projection_used_by_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def fetchone(self) -> tuple[int]:
            return (0,)

    class InsertCursor:
        def __init__(self) -> None:
            self.query: object | None = None
            self.rows: list[tuple[object, ...]] = []

        def __enter__(self) -> InsertCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def executemany(self, query: object, rows: list[tuple[object, ...]]) -> None:
            self.query = query
            self.rows.extend(rows)

    class Target:
        def __init__(self) -> None:
            self.insert_cursor = InsertCursor()

        def execute(self, _query: object) -> Result:
            return Result()

        def cursor(self) -> InsertCursor:
            return self.insert_cursor

    source = duckdb.connect(":memory:")
    source.execute("CREATE TABLE request_steps (id VARCHAR, status VARCHAR, blocked_reason VARCHAR)")
    source.execute("INSERT INTO request_steps VALUES ('step-1', 'READY', 'true')")
    target = Target()
    monkeypatch.setattr(migration, "target_json_columns", lambda *_args: set())
    monkeypatch.setattr(migration, "restore_staged_references", lambda *_args: None)

    migration.copy_tables(source, target, ["request_steps"], 10)

    assert target.insert_cursor.rows == [(None, "step-1", True, "READY")]
    assert "is_optional" in repr(target.insert_cursor.query)


def test_legacy_identity_or_task_type_columns_with_data_are_hard_blockers() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE batch_execution_attempts (id VARCHAR)")
    connection.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-1')")
    connection.execute("CREATE TABLE workflow_runs (id VARCHAR)")
    connection.execute("CREATE TABLE batch_dispatches (id VARCHAR)")
    connection.execute("CREATE TABLE batch_path_profiles (id VARCHAR)")
    connection.execute("INSERT INTO batch_path_profiles VALUES ('profile-1')")
    hard = {item["relationship"] for item in migration.relationship_audit(connection) if item["severity"] == "hard"}
    assert "workflow_runs.batch_attempt_id" in hard
    assert "batch_dispatches.attempt_id" in hard
    assert "batch_path_profiles.task_type identity" in hard


def test_source_manifest_reprojects_general_rows_to_sorted_comparison_columns() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE ordinary (b VARCHAR, a VARCHAR)")
    connection.execute("INSERT INTO ordinary VALUES ('value-b', 'value-a')")
    columns = migration.comparison_columns(migration.source_table_columns(connection, ["ordinary"]))
    source = migration.source_manifest(connection, ["ordinary"], source_columns=columns)
    expected_count, expected_checksum = migration.digest_rows(
        [("value-a", "value-b")], ["a", "b"], set()
    )
    assert source == {"ordinary": {"count": expected_count, "checksum": expected_checksum}}


def test_source_schema_audit_supports_legacy_lease_defaults_but_rejects_unknown_drift() -> None:
    canonical = migration.canonical_table_columns()
    tables = migration.table_order()
    assert set(canonical) == set(tables)
    assert "UNIQUE(analysis_run_id," not in canonical["curve_results"]
    assert "UNIQUE(load_case_id," not in canonical["variable_definitions"]
    source_columns = {table: list(columns) for table, columns in canonical.items()}
    for column in migration.LEGACY_COLUMN_DEFAULTS["batch_execution_attempts"]:
        source_columns["batch_execution_attempts"].remove(column)
    assert migration.source_schema_audit(source_columns, tables) == []

    source_columns["media_assets"].remove("mime_type")
    del source_columns["projects"]
    findings = migration.source_schema_audit(source_columns, tables)
    assert {
        (finding["table"], finding["status"])
        for finding in findings
    } >= {
        ("media_assets", "source_column_contract_mismatch"),
        ("projects", "source_table_missing"),
    }


def test_target_schema_preflight_rejects_column_drift_before_row_queries() -> None:
    class Result:
        def __init__(self, rows: list[tuple[object, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[object, ...]]:
            return self.rows

    class Target:
        def __init__(self) -> None:
            self.row_query_count = 0

        def execute(self, query: object, _values: object = None) -> Result:
            rendered = str(query)
            if "information_schema.tables" in rendered:
                return Result([("projects",)])
            if "information_schema.columns" in rendered:
                return Result([("id",), ("unexpected_column",)])
            self.row_query_count += 1
            raise AssertionError("row queries must not run after schema drift")

    target = Target()
    with pytest.raises(RuntimeError, match="source_missing=.*unexpected_column"):
        migration.target_schema_preflight(target, ["projects"], {"projects": ["id"]})
    assert target.row_query_count == 0


def test_target_schema_preflight_requires_recovery_constraints_before_row_queries() -> None:
    class Result:
        def __init__(self, rows: list[tuple[object, ...]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[tuple[object, ...]]:
            return self.rows

    class Target:
        def __init__(self) -> None:
            self.row_query_count = 0

        def execute(self, query: object, _values: object = None) -> Result:
            rendered = str(query)
            if "information_schema.tables" in rendered:
                return Result([("workflow_runs",), ("batch_execution_attempts",)])
            if "information_schema.columns" in rendered:
                return Result([("id",)])
            if "pg_constraint" in rendered:
                return Result([])
            self.row_query_count += 1
            raise AssertionError("row queries must not run without required constraints")

    target = Target()
    with pytest.raises(RuntimeError, match="필수 constraint"):
        migration.target_schema_preflight(
            target,
            ["workflow_runs", "batch_execution_attempts"],
            {"workflow_runs": ["id"], "batch_execution_attempts": ["id"]},
        )
    assert target.row_query_count == 0


def test_schema_preflight_failure_rolls_back_without_copy_or_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Target:
        def __init__(self) -> None:
            self.commit_count = 0
            self.rollback_count = 0

        def commit(self) -> None:
            self.commit_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

    copied = False

    def fail_preflight(*_args: object) -> None:
        raise RuntimeError("schema drift")

    def record_copy(*_args: object) -> None:
        nonlocal copied
        copied = True

    target = Target()
    monkeypatch.setattr(migration, "target_schema_preflight", fail_preflight)
    monkeypatch.setattr(migration, "copy_tables", record_copy)
    with pytest.raises(RuntimeError, match="schema drift"):
        migration.execute_copy_and_verify(None, target, ["projects"], 10, {}, {"projects": ["id"]})
    assert copied is False
    assert target.commit_count == 0
    assert target.rollback_count == 1


def test_every_canonical_child_before_parent_fk_is_staged() -> None:
    order = migration.table_order()
    position = {table: index for index, table in enumerate(order)}
    schema = migration.SCHEMA_FILE.read_text(encoding="utf-8")
    foreign_keys = re.findall(
        r"ALTER TABLE (\w+) ADD CONSTRAINT \w+ FOREIGN KEY \((\w+)\) REFERENCES (\w+)\((\w+)\)", schema
    )
    reverse_edges = {
        (child, child_column)
        for child, child_column, parent, _parent_column in foreign_keys
        if position[child] < position[parent]
    }
    staged = {(table, column) for table, columns in migration.STAGED_REFERENCE_COLUMNS.items() for column in columns}
    assert reverse_edges <= staged


def test_copy_verification_rolls_back_before_commit_on_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    class Target:
        def __init__(self) -> None:
            self.commit_count = 0
            self.rollback_count = 0

        def commit(self) -> None:
            self.commit_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

    target = Target()
    monkeypatch.setattr(migration, "target_schema_preflight", lambda *_args: None)
    monkeypatch.setattr(migration, "copy_tables", lambda *_args: None)
    monkeypatch.setattr(migration, "target_manifest", lambda *_args, **_kwargs: {"projects": {"count": 0, "checksum": "target"}})
    with pytest.raises(RuntimeError, match="검증 불일치"):
        migration.execute_copy_and_verify(
            None, target, ["projects"], 100, {"projects": {"count": 0, "checksum": "source"}}, {"projects": ["id"]}
        )
    assert target.commit_count == 0
    assert target.rollback_count == 1


def test_copy_verification_commits_only_after_matching_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    class Target:
        def __init__(self) -> None:
            self.commit_count = 0
            self.rollback_count = 0

        def commit(self) -> None:
            self.commit_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

    target = Target()
    state = {"projects": {"count": 0, "checksum": "matching"}}
    monkeypatch.setattr(migration, "target_schema_preflight", lambda *_args: None)
    monkeypatch.setattr(migration, "copy_tables", lambda *_args: None)
    monkeypatch.setattr(migration, "target_manifest", lambda *_args, **_kwargs: state)
    assert migration.execute_copy_and_verify(None, target, ["projects"], 100, state, {"projects": ["id"]}) == (state, [])
    assert target.commit_count == 1
    assert target.rollback_count == 0


def test_copy_verification_rolls_back_when_copy_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class Target:
        def __init__(self) -> None:
            self.commit_count = 0
            self.rollback_count = 0

        def commit(self) -> None:
            self.commit_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

    target = Target()

    def fail_copy(*_args: object) -> None:
        raise RuntimeError("copy failed")

    monkeypatch.setattr(migration, "target_schema_preflight", lambda *_args: None)
    monkeypatch.setattr(migration, "copy_tables", fail_copy)
    with pytest.raises(RuntimeError, match="copy failed"):
        migration.execute_copy_and_verify(None, target, ["projects"], 100, {}, {"projects": ["id"]})
    assert target.commit_count == 0
    assert target.rollback_count == 1


def test_restore_staged_references_fails_closed_on_compare_and_swap_conflict() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.rowcount = 0
            self.queries: list[object] = []

        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: object, _values: object) -> None:
            self.queries.append(query)

    class Target:
        def __init__(self) -> None:
            self.cursor_instance = Cursor()

        def cursor(self) -> Cursor:
            return self.cursor_instance

    source = duckdb.connect(":memory:")
    source.execute("CREATE TABLE workflow_runs (id VARCHAR, batch_attempt_id VARCHAR)")
    source.execute("INSERT INTO workflow_runs VALUES ('run-1', 'attempt-1')")
    target = Target()
    with pytest.raises(RuntimeError, match="staged reference restore conflicted: workflow_runs.batch_attempt_id"):
        migration.restore_staged_references(source, target, ["workflow_runs"], 10)
    assert "IS NULL" in repr(target.cursor_instance.queries[0])


def test_active_lease_blocks_execute_before_postgres_connection(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    source_path = tmp_path / "active-lease.duckdb"
    source = duckdb.connect(str(source_path))
    source.execute(
        """CREATE TABLE batch_execution_attempts (
        id VARCHAR, recovery_lease_owner_id VARCHAR, recovery_lease_token VARCHAR,
        recovery_lease_generation BIGINT, recovery_lease_acquired_at TIMESTAMP,
        recovery_lease_expires_at TIMESTAMP)"""
    )
    source.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-1', 'owner', 'token', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
    source.close()
    connected = False

    def forbidden_connect(*_args: object, **_kwargs: object) -> object:
        nonlocal connected
        connected = True
        raise AssertionError("PostgreSQL must not be connected for an active lease")

    monkeypatch.setattr(migration.psycopg, "connect", forbidden_connect)
    monkeypatch.setattr(sys, "argv", ["migration", "--source", str(source_path), "--execute", "--target-url", "postgresql://target"])
    with pytest.raises(RuntimeError, match="lease preflight"):
        migration.main()
    assert connected is False


def test_dry_run_unsafe_lease_writes_manifest_json_and_never_connects_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_path = tmp_path / "unsafe-lease.duckdb"
    manifest_path = tmp_path / "preflight.json"
    source = duckdb.connect(str(source_path))
    source.execute(
        """CREATE TABLE batch_execution_attempts (
        id VARCHAR, recovery_lease_owner_id VARCHAR, recovery_lease_token VARCHAR,
        recovery_lease_generation BIGINT, recovery_lease_acquired_at TIMESTAMP,
        recovery_lease_expires_at TIMESTAMP)"""
    )
    source.execute("INSERT INTO batch_execution_attempts VALUES ('attempt-1', 'owner', 'token', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '1 minute')")
    source.close()
    monkeypatch.setattr(migration.psycopg, "connect", lambda *_args, **_kwargs: pytest.fail("target connection is forbidden for unsafe dry-run"))
    monkeypatch.setattr(sys, "argv", ["migration", "--source", str(source_path), "--target-url", "postgresql://target", "--manifest", str(manifest_path), "--json-output"])
    with pytest.raises(RuntimeError, match="lease preflight"):
        migration.main()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["transfer_preflight"]["safe_to_execute"] is False
    assert json.loads(capsys.readouterr().out)["recovery_lease_preflight"]["status"] == "blocked_active_recovery_leases"


def test_dry_run_source_schema_drift_writes_manifest_and_never_connects_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_path = tmp_path / "schema-drift.duckdb"
    manifest_path = tmp_path / "schema-preflight.json"
    source = duckdb.connect(str(source_path))
    source.execute("CREATE TABLE projects (id VARCHAR)")
    source.close()
    monkeypatch.setattr(
        migration.psycopg,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("target connection is forbidden for unsafe source schema"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "migration",
            "--source",
            str(source_path),
            "--target-url",
            "postgresql://target",
            "--manifest",
            str(manifest_path),
            "--json-output",
        ],
    )
    with pytest.raises(RuntimeError, match="canonical schema"):
        migration.main()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["transfer_preflight"]["hard_source_schema_finding_count"] > 0
    assert json.loads(capsys.readouterr().out)["transfer_preflight"]["safe_to_execute"] is False


def test_main_closes_target_without_context_manager_second_commit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class Target:
        class Info:
            dbname = "target"

        def __init__(self) -> None:
            self.info = self.Info()
            self.commit_count = 0
            self.close_count = 0
            self.enter_count = 0
            self.exit_count = 0

        def __enter__(self) -> Target:
            self.enter_count += 1
            return self

        def __exit__(self, *_args: object) -> None:
            self.exit_count += 1
            self.commit()

        def commit(self) -> None:
            self.commit_count += 1

        def close(self) -> None:
            self.close_count += 1

    source_path = tmp_path / "legacy.duckdb"
    source = duckdb.connect(str(source_path))
    source.execute("CREATE TABLE batch_execution_attempts (id VARCHAR)")
    source.close()
    target = Target()
    monkeypatch.setattr(migration.psycopg, "connect", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(migration, "source_schema_audit", lambda *_args: [])

    def copied_once(*args: object) -> tuple[dict[str, dict[str, object]], list[str]]:
        args[1].commit()
        return {}, []

    monkeypatch.setattr(migration, "execute_copy_and_verify", copied_once)
    monkeypatch.setattr(sys, "argv", ["migration", "--source", str(source_path), "--execute", "--target-url", "postgresql://target"])
    migration.main()
    assert target.commit_count == 1
    assert target.close_count == 1
    assert target.enter_count == 0
    assert target.exit_count == 0


@pytest.mark.parametrize(
    "arguments, message",
    [
        (["migration", "--source", "missing.duckdb", "--execute"], "target URL"),
        (["migration", "--source", "missing.duckdb", "--batch-size", "0"], "batch-size"),
    ],
)
def test_execute_requires_target_and_positive_batch_size_before_opening_source(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], message: str
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(RuntimeError, match=message):
        migration.main()


def test_transfer_script_uses_one_explicit_duckdb_snapshot_transaction() -> None:
    source = migration.Path(migration.__file__).read_text(encoding="utf-8")
    assert 'source.execute("BEGIN TRANSACTION")' in source
    assert 'source.execute("COMMIT")' in source
