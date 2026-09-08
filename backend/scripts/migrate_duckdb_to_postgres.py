from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import duckdb
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


SCHEMA_FILE = Path(__file__).resolve().parents[1] / "migrations" / "schema.sql"
RELATIONSHIPS = [
    ("product_information", "project_id", "projects", "id", "hard"),
    ("analysis_requests", "project_id", "projects", "id", "hard"),
    ("spdm_storage_project_parents", "project_id", "projects", "id", "hard"),
    ("spdm_storage_request_parents", "project_folder", "spdm_storage_project_parents", "project_folder", "hard"),
    ("spdm_storage_request_parents", "project_id", "projects", "id", "hard"),
    ("spdm_storage_request_parents", "request_id", "analysis_requests", "id", "hard"),
    ("spdm_storage_bindings", "load_case_id", "load_cases", "id", "hard"),
    ("spdm_storage_bindings", "project_id", "projects", "id", "hard"),
    ("spdm_storage_bindings", "request_id", "analysis_requests", "id", "hard"),
    ("spdm_storage_files", "load_case_id", "load_cases", "id", "hard"),
    ("spdm_storage_files", "run_id", "analysis_runs", "id", "hard"),
    ("request_steps", "request_id", "analysis_requests", "id", "hard"),
    ("load_cases", "request_id", "analysis_requests", "id", "hard"),
    ("template_executions", "load_case_id", "load_cases", "id", "hard"),
    ("analysis_runs", "load_case_id", "load_cases", "id", "hard"),
    ("analysis_runs", "template_execution_id", "template_executions", "id", "hard"),
    ("scalar_results", "analysis_run_id", "analysis_runs", "id", "hard"),
    ("time_series_results", "analysis_run_id", "analysis_runs", "id", "hard"),
    ("curve_results", "analysis_run_id", "analysis_runs", "id", "hard"),
    ("curve_points", "curve_id", "curve_results", "id", "hard"),
    ("result_locations", "analysis_run_id", "analysis_runs", "id", "hard"),
    ("qualitative_notes", "analysis_run_id", "analysis_runs", "id", "hard"),
    ("media_assets", "analysis_run_id", "analysis_runs", "id", "hard"),
    ("media_assets", "blob_id", "asset_blobs", "id", "hard"),
    ("dashboard_versions", "dashboard_id", "dashboards", "id", "hard"),
    ("workspace_layout_versions", "layout_kind", "workspace_layouts", "layout_kind", "hard"),
    ("project_workspace_layouts", "project_id", "projects", "id", "hard"),
    ("project_workspace_layout_versions", "project_id", "projects", "id", "hard"),
    ("report_layout_versions", "layout_id", "report_layouts", "id", "hard"),
    ("analysis_run_metadata", "analysis_run_id", "analysis_runs", "id", "warning"),
    ("analysis_run_metadata", "schema_id", "import_schemas", "id", "warning"),
    ("folder_import_jobs", "schema_id", "import_schemas", "id", "warning"),
    ("batch_dispatches", "work_item_id", "request_work_items", "id", "hard"),
    ("batch_dispatches", "workflow_run_id", "workflow_runs", "id", "hard"),
    ("batch_dispatches", "batch_profile_id", "batch_path_profiles", "id", "hard"),
    ("batch_dispatches", "attempt_id", "batch_execution_attempts", "id", "hard"),
    ("batch_execution_attempts", "work_item_id", "request_work_items", "id", "hard"),
    ("batch_execution_attempts", "workflow_run_id", "workflow_runs", "id", "hard"),
    ("workflow_runs", "batch_attempt_id", "batch_execution_attempts", "id", "hard"),
    ("batch_execution_events", "attempt_id", "batch_execution_attempts", "id", "hard"),
    ("request_work_items", "demo_run_id", "workflow_runs", "id", "hard"),
    ("project_memberships", "project_id", "projects", "id", "hard"),
    ("project_memberships", "user_id", "users", "id", "hard"),
    ("project_invitations", "project_id", "projects", "id", "hard"),
    ("project_invitations", "resolved_user_id", "users", "id", "warning"),
    ("role_menu_policies", "menu_id", "menu_definitions", "id", "hard"),
    ("analysis_requests", "owner_user_id", "users", "id", "warning"),
    ("request_steps", "owner_user_id", "users", "id", "warning"),
    ("request_work_items", "owner_user_id", "users", "id", "warning"),
]

# These values form an immediate-FK cycle in the canonical PostgreSQL schema.
# Insert the rows with NULL references, then restore their exact DuckDB values
# only after every table exists in the still-uncommitted target transaction.
STAGED_REFERENCE_COLUMNS: dict[str, frozenset[str]] = {
    "media_assets": frozenset({"blob_id"}),
    "request_work_items": frozenset({"demo_run_id"}),
    "workflow_runs": frozenset({"batch_attempt_id"}),
    "batch_execution_attempts": frozenset({"workflow_run_id"}),
    "batch_dispatches": frozenset({"attempt_id"}),
}

# These are the only additive canonical columns that can be reconstructed for
# an older DuckDB file without guessing user data. They are materialized into
# the same canonical projection for both INSERT values and checksums.
LEGACY_COLUMN_DEFAULTS: dict[str, dict[str, Any]] = {
    "request_steps": {"is_optional": False},
    "media_assets": {"blob_id": None, "original_filename": None},
    "request_work_items": {
        "progress": 0,
        "progress_updated_by": None,
        "progress_updated_at": None,
        "demo_run_id": None,
    },
    "workflow_runs": {"batch_attempt_id": None},
    "batch_dispatches": {"attempt_id": None},
    "batch_execution_attempts": {
        "recovery_lease_owner_id": None,
        "recovery_lease_token": None,
        "recovery_lease_generation": 0,
        "recovery_lease_acquired_at": None,
        "recovery_lease_expires_at": None,
    },
    "folder_import_jobs": {
        "source_type": None,
        "source_checksum": None,
        "source_run_id": None,
        "conflict_policy": None,
        "outcome_reason": None,
        "replaced_analysis_run_id": None,
        "completed_at": None,
    },
    "batch_path_profiles": {"version": 1, "task_type_id": None, "task_type_version": 1},
    "batch_path_profile_versions": {"task_type_id": None, "task_type_version": 1},
}

RECOVERY_LEASE_COLUMNS = frozenset(LEGACY_COLUMN_DEFAULTS["batch_execution_attempts"])

REQUIRED_TARGET_CONSTRAINTS = frozenset({
    ("media_assets", "fk_media_assets_blob"),
    ("request_work_items", "fk_request_work_items_demo_run"),
    ("workflow_runs", "fk_workflow_runs_batch_attempt"),
    ("batch_execution_attempts", "fk_batch_attempts_workflow_run"),
    ("batch_dispatches", "fk_batch_dispatches_attempt"),
    ("batch_execution_attempts", "ck_batch_attempts_recovery_lease_generation"),
    ("batch_execution_attempts", "ck_batch_attempts_recovery_lease_state"),
    ("batch_execution_attempts", "ck_batch_attempts_recovery_lease_identity"),
})

REQUIRED_TARGET_INDEXES = frozenset({
    ("workflow_runs", "ux_workflow_runs_batch_attempt_id"),
    ("batch_dispatches", "ux_batch_dispatches_attempt_id"),
    ("batch_execution_attempts", "ux_batch_attempts_recovery_lease_token"),
})


def table_order() -> list[str]:
    return re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z0-9_]+)", SCHEMA_FILE.read_text(encoding="utf-8"), flags=re.IGNORECASE)


def _split_sql_items(body: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(body):
        character = body[index]
        if quote:
            if character == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            items.append(body[start:index].strip())
            start = index + 1
        index += 1
    tail = body[start:].strip()
    if tail:
        items.append(tail)
    return items


def canonical_table_columns() -> dict[str, list[str]]:
    schema = SCHEMA_FILE.read_text(encoding="utf-8")
    result: dict[str, list[str]] = {}
    pattern = re.compile(
        r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z0-9_]+)\s*\((.*?)^\s*\);",
        flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(schema):
        columns = []
        for item in _split_sql_items(match.group(2)):
            upper_item = item.lstrip().upper()
            if upper_item.startswith(("CONSTRAINT ", "PRIMARY KEY", "UNIQUE ", "UNIQUE(", "CHECK ", "CHECK(", "FOREIGN KEY")):
                continue
            columns.append(item.split(None, 1)[0].strip('"'))
        result[match.group(1)] = columns
    return result


def normalize(value: Any, json_value: bool = False) -> Any:
    if json_value and isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    if isinstance(value, dict):
        return {str(key): normalize(entry) for key, entry in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [normalize(entry) for entry in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    return value


def digest_rows(rows: Iterable[tuple[Any, ...]], columns: list[str], json_columns: set[str]) -> tuple[int, str]:
    modulus = 1 << 256
    aggregate = 0
    count = 0
    for row in rows:
        payload = [normalize(value, column in json_columns) for column, value in zip(columns, row)]
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        aggregate = (aggregate + int.from_bytes(hashlib.sha256(encoded).digest(), "big")) % modulus
        count += 1
    return count, f"{aggregate:064x}"


def cursor_rows(cursor: Any, batch_size: int = 2000) -> Iterable[tuple[Any, ...]]:
    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            return
        yield from batch


def transform_source_row(
    table: str,
    columns: list[str],
    row: tuple[Any, ...],
    *,
    stage_columns: frozenset[str] = frozenset(),
    output_columns: list[str] | None = None,
) -> tuple[Any, ...]:
    values = materialized_source_values(table, columns, row)
    for column in stage_columns:
        if column in values:
            values[column] = None
    return tuple(values[column] for column in (output_columns or columns))


def normalized_source_values(table: str, columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    """Normalize legacy row semantics before virtual checksum defaults apply."""
    values = dict(zip(columns, row))
    if table == "request_steps":
        blocked_reason = values.get("blocked_reason")
        if isinstance(blocked_reason, str) and blocked_reason.lower() in {"true", "false"}:
            if values.get("is_optional") is None:
                values["is_optional"] = blocked_reason.lower() == "true"
            values["blocked_reason"] = None
        if values.get("is_optional") is None:
            values["is_optional"] = False
    if table == "request_work_items" and "progress" not in values:
        values["progress"] = 100 if values.get("status") == "COMPLETED" else 0
    return values


def materialized_source_values(table: str, columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    """Return the exact canonical row used by both copy and checksums."""
    values = normalized_source_values(table, columns, row)
    for column, default in LEGACY_COLUMN_DEFAULTS.get(table, {}).items():
        values.setdefault(column, default)
    return values


def source_table_info(connection: duckdb.DuckDBPyConnection, table: str) -> tuple[list[str], set[str]]:
    columns = connection.execute(f'SELECT * FROM "{table}" LIMIT 0').description
    names = [column[0] for column in columns]
    json_columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='main' AND table_name=? AND data_type='JSON'", [table]).fetchall()}
    return names, json_columns


def target_json_columns(connection: psycopg.Connection, table: str) -> set[str]:
    return {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND data_type IN ('json','jsonb')", [table]).fetchall()}


def source_table_columns(connection: duckdb.DuckDBPyConnection, tables: list[str]) -> dict[str, list[str]]:
    source_tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    return {table: source_table_info(connection, table)[0] for table in tables if table in source_tables}


def comparison_columns(source_columns: dict[str, list[str]]) -> dict[str, list[str]]:
    """Add only documented, non-ambiguous canonical defaults to checksums."""
    result = {table: sorted(columns) for table, columns in source_columns.items()}
    for table, defaults in LEGACY_COLUMN_DEFAULTS.items():
        if table in result:
            result[table] = sorted(set(result[table]) | set(defaults))
    return result


def source_schema_audit(
    source_columns: dict[str, list[str]],
    tables: list[str],
) -> list[dict[str, Any]]:
    """Compare the read-only source projection with the canonical schema."""
    canonical = canonical_table_columns()
    projected = comparison_columns(source_columns)
    findings: list[dict[str, Any]] = []
    for table in tables:
        if table not in source_columns:
            findings.append({"severity": "hard", "table": table, "status": "source_table_missing"})
            continue
        expected = set(canonical.get(table, []))
        actual = set(projected[table])
        if expected != actual:
            findings.append({
                "severity": "hard",
                "table": table,
                "status": "source_column_contract_mismatch",
                "missing_in_source": sorted(expected - actual),
                "extra_in_source": sorted(actual - expected),
            })
    return findings


def source_manifest(
    connection: duckdb.DuckDBPyConnection,
    tables: list[str],
    *,
    source_columns: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    source_tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    for table in tables:
        if table not in source_tables:
            manifest[table] = {"count": 0, "checksum": "missing"}
            continue
        raw_columns, json_columns = source_table_info(connection, table)
        columns = (source_columns or comparison_columns({table: raw_columns})).get(table, raw_columns)
        projection = ", ".join(f'"{column}"' for column in raw_columns)
        transformed = (
            tuple(materialized_source_values(table, raw_columns, row)[column] for column in columns)
            for row in cursor_rows(connection.execute(f'SELECT {projection} FROM "{table}"'))
        )
        count, checksum = digest_rows(transformed, columns, json_columns)
        manifest[table] = {"count": count, "checksum": checksum}
    return manifest


def relationship_audit(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    findings = []
    available_columns = source_table_columns(connection, sorted({table for relation in RELATIONSHIPS for table in (relation[0], relation[2])}))
    for child, child_key, parent, parent_key, severity in RELATIONSHIPS:
        # A historical DuckDB file may predate an additive identity column.
        # Missing values retain their canonical target defaults instead of
        # turning a compatibility migration into a false orphan failure.
        if child_key not in available_columns.get(child, []) or parent_key not in available_columns.get(parent, []):
            findings.append({"severity": "info", "relationship": f"{child}.{child_key}->{parent}.{parent_key}", "status": "legacy_column_missing"})
            continue
        count = connection.execute(
            f'SELECT count(*) FROM "{child}" child LEFT JOIN "{parent}" parent ON child."{child_key}"=parent."{parent_key}" '
            f'WHERE child."{child_key}" IS NOT NULL AND parent."{parent_key}" IS NULL'
        ).fetchone()[0]
        if count:
            findings.append({"severity": severity, "relationship": f"{child}.{child_key}->{parent}.{parent_key}", "orphan_count": count})
    identity_checks = (
        (
            "workflow_runs.batch_attempt_id<->batch_execution_attempts.workflow_run_id",
            "workflow_runs",
            {"batch_attempt_id"},
            "batch_execution_attempts",
            {"id", "workflow_run_id"},
            """SELECT count(*) FROM workflow_runs AS run
            JOIN batch_execution_attempts AS attempt ON attempt.id=run.batch_attempt_id
            WHERE run.batch_attempt_id IS NOT NULL
              AND attempt.workflow_run_id IS DISTINCT FROM run.id""",
        ),
        (
            "batch_execution_attempts.workflow_run_id<->workflow_runs.batch_attempt_id",
            "batch_execution_attempts",
            {"workflow_run_id", "id"},
            "workflow_runs",
            {"id", "batch_attempt_id"},
            """SELECT count(*) FROM batch_execution_attempts AS attempt
            LEFT JOIN workflow_runs AS run ON run.id=attempt.workflow_run_id
            WHERE attempt.workflow_run_id IS NOT NULL
              AND (run.id IS NULL OR run.batch_attempt_id IS DISTINCT FROM attempt.id)""",
        ),
        (
            "batch_dispatches.attempt_id->batch_execution_attempts provenance",
            "batch_dispatches",
            {"attempt_id", "workflow_run_id", "work_item_id", "batch_profile_id"},
            "batch_execution_attempts",
            {"id", "workflow_run_id", "work_item_id", "batch_profile_id"},
            """SELECT count(*) FROM batch_dispatches AS dispatch
            JOIN batch_execution_attempts AS attempt ON attempt.id=dispatch.attempt_id
            WHERE dispatch.attempt_id IS NOT NULL
              AND (attempt.workflow_run_id IS DISTINCT FROM dispatch.workflow_run_id
                   OR attempt.work_item_id IS DISTINCT FROM dispatch.work_item_id
                   OR attempt.batch_profile_id IS DISTINCT FROM dispatch.batch_profile_id)""",
        ),
        (
            "request_work_items.demo_run_id->workflow_runs request provenance",
            "request_work_items",
            {"demo_run_id", "request_id"},
            "workflow_runs",
            {"id", "request_id"},
            """SELECT count(*) FROM request_work_items AS item
            JOIN workflow_runs AS run ON run.id=item.demo_run_id
            WHERE item.demo_run_id IS NOT NULL
              AND run.request_id IS DISTINCT FROM item.request_id""",
        ),
        (
            "workflow_runs.batch_attempt_id unique",
            "workflow_runs",
            {"batch_attempt_id"},
            "workflow_runs",
            {"batch_attempt_id"},
            """SELECT count(*) FROM (
                SELECT batch_attempt_id FROM workflow_runs
                WHERE batch_attempt_id IS NOT NULL
                GROUP BY batch_attempt_id HAVING count(*) > 1
            ) AS duplicate""",
        ),
        (
            "batch_dispatches.attempt_id unique",
            "batch_dispatches",
            {"attempt_id"},
            "batch_dispatches",
            {"attempt_id"},
            """SELECT count(*) FROM (
                SELECT attempt_id FROM batch_dispatches
                WHERE attempt_id IS NOT NULL
                GROUP BY attempt_id HAVING count(*) > 1
            ) AS duplicate""",
        ),
        (
            "batch_dispatches.workflow_run_id unique",
            "batch_dispatches",
            {"workflow_run_id"},
            "batch_dispatches",
            {"workflow_run_id"},
            """SELECT count(*) FROM (
                SELECT workflow_run_id FROM batch_dispatches
                WHERE workflow_run_id IS NOT NULL
                GROUP BY workflow_run_id HAVING count(*) > 1
            ) AS duplicate""",
        ),
        (
            "batch_dispatches recorded demo status",
            "batch_dispatches",
            {"status"},
            "batch_dispatches",
            {"status"},
            """SELECT count(*) FROM batch_dispatches
            WHERE status IS DISTINCT FROM 'RECORDED_DEMO'""",
        ),
        (
            "batch_execution_attempts.workflow_run_id linked status",
            "batch_execution_attempts",
            {"workflow_run_id", "status"},
            "workflow_runs",
            {"id"},
            """SELECT count(*) FROM batch_execution_attempts AS attempt
            WHERE attempt.workflow_run_id IS NOT NULL
              AND attempt.status NOT IN ('QUEUED', 'SUCCEEDED')""",
        ),
        (
            "batch_execution_attempts.workflow_run_id exact runner identity",
            "batch_execution_attempts",
            {"id", "workflow_run_id", "work_item_id", "status", "created_by", "completed_at"},
            "workflow_runs",
            {"id", "batch_attempt_id", "request_id", "execution_mode", "status", "created_by", "definition_json"},
            """SELECT count(*) FROM batch_execution_attempts AS attempt
            LEFT JOIN workflow_runs AS run ON run.id=attempt.workflow_run_id
            LEFT JOIN request_work_items AS item ON item.id=attempt.work_item_id
            WHERE attempt.workflow_run_id IS NOT NULL
              AND (
                  run.id IS NULL OR item.id IS NULL
                  OR run.batch_attempt_id IS DISTINCT FROM attempt.id
                  OR run.request_id IS DISTINCT FROM item.request_id
                  OR run.execution_mode IS DISTINCT FROM 'DEMO_ONLY'
                  OR run.status IS DISTINCT FROM 'SUCCEEDED'
                  OR run.created_by IS DISTINCT FROM attempt.created_by
                  OR json_array_length(json_extract(run.definition_json, '$.nodes')) IS DISTINCT FROM 1
                  OR json_extract_string(run.definition_json, '$.nodes[0].node_key') IS DISTINCT FROM item.node_key
                  OR json_extract_string(run.definition_json, '$.nodes[0].task_type_id') IS DISTINCT FROM item.task_type_id
                  OR try_cast(json_extract_string(run.definition_json, '$.nodes[0].task_type_version') AS INTEGER) IS DISTINCT FROM item.task_type_version
                  OR json_array_length(json_extract(run.definition_json, '$.nodes[0].depends_on')) IS DISTINCT FROM 0
                  OR (attempt.status='QUEUED' AND attempt.completed_at IS NOT NULL)
              )""",
        ),
        (
            "queued batch_execution_attempt has no dispatch",
            "batch_execution_attempts",
            {"workflow_run_id", "status"},
            "batch_dispatches",
            {"workflow_run_id"},
            """SELECT count(*) FROM batch_execution_attempts AS attempt
            WHERE attempt.status='QUEUED' AND attempt.workflow_run_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM batch_dispatches AS dispatch
                          WHERE dispatch.workflow_run_id=attempt.workflow_run_id)""",
        ),
        (
            "succeeded batch_execution_attempt has exact dispatch",
            "batch_execution_attempts",
            {"id", "workflow_run_id", "work_item_id", "batch_profile_id", "status"},
            "batch_dispatches",
            {"id", "attempt_id", "workflow_run_id", "work_item_id", "batch_profile_id"},
            """SELECT count(*) FROM batch_execution_attempts AS attempt
            LEFT JOIN batch_dispatches AS dispatch ON dispatch.workflow_run_id=attempt.workflow_run_id
            WHERE attempt.status='SUCCEEDED'
              AND (attempt.workflow_run_id IS NULL
                   OR dispatch.id IS NULL
                   OR dispatch.attempt_id IS DISTINCT FROM attempt.id
                   OR dispatch.work_item_id IS DISTINCT FROM attempt.work_item_id
                   OR dispatch.batch_profile_id IS DISTINCT FROM attempt.batch_profile_id)""",
        ),
    )
    for relationship, child, child_columns, parent, parent_columns, query in identity_checks:
        if not child_columns <= set(available_columns.get(child, [])) or not parent_columns <= set(available_columns.get(parent, [])):
            findings.append({"severity": "info", "relationship": relationship, "status": "legacy_column_missing"})
            continue
        count = int(connection.execute(query).fetchone()[0])
        if count:
            findings.append({"severity": "hard", "relationship": relationship, "mismatch_count": count})
    # The current target can represent absent additive columns as defaults, but
    # reverse 0018 identity and normalized 0010 task type values must never be
    # fabricated when the historical source actually contains affected rows.
    batch_attempt_count = 0
    if "batch_execution_attempts" in available_columns:
        batch_attempt_count = int(connection.execute('SELECT count(*) FROM "batch_execution_attempts"').fetchone()[0])
    for table, columns in {
        "workflow_runs": {"batch_attempt_id"},
        "batch_dispatches": {"attempt_id"},
    }.items():
        if table in available_columns and not columns <= set(available_columns[table]) and batch_attempt_count:
            findings.append({
                "severity": "hard",
                "relationship": f"{table}.{next(iter(columns))}",
                "status": "legacy_column_missing",
                "affected_batch_attempt_count": batch_attempt_count,
            })
    for table, columns in {
        "batch_path_profiles": {"task_type_id", "task_type_version"},
        "batch_path_profile_versions": {"task_type_id", "task_type_version"},
    }.items():
        if table in available_columns and not columns <= set(available_columns[table]):
            count = int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            if count:
                findings.append({
                    "severity": "hard",
                    "relationship": f"{table}.task_type identity",
                    "status": "legacy_column_missing",
                    "affected_row_count": count,
                })
    return findings


def target_manifest(
    connection: psycopg.Connection,
    tables: list[str],
    *,
    source_columns: dict[str, list[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    target_tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    for table in tables:
        if table not in target_tables:
            manifest[table] = {"count": 0, "checksum": "missing"}
            continue
        available_columns = {row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            [table],
        ).fetchall()}
        expected_columns = source_columns.get(table) if source_columns is not None else None
        if expected_columns is not None and set(expected_columns) != available_columns:
            missing_in_source = sorted(available_columns - set(expected_columns))
            missing_in_target = sorted(set(expected_columns) - available_columns)
            raise RuntimeError(
                f"원본/대상 컬럼 계약이 일치하지 않습니다: {table}; "
                f"source_missing={missing_in_source}, target_missing={missing_in_target}"
            )
        columns = sorted(available_columns)
        cursor = connection.execute(
            sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.Identifier(table),
            )
        )
        count, checksum = digest_rows(cursor_rows(cursor), columns, target_json_columns(connection, table))
        manifest[table] = {"count": count, "checksum": checksum}
    return manifest


def target_schema_preflight(
    connection: psycopg.Connection,
    tables: list[str],
    source_columns: dict[str, list[str]],
) -> None:
    """Reject target/source schema drift before the first target INSERT."""
    target_tables = {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ).fetchall()
    }
    missing_source_tables = [table for table in tables if table not in source_columns]
    missing_target_tables = [table for table in tables if table not in target_tables]
    if missing_source_tables or missing_target_tables:
        raise RuntimeError(
            "원본/대상 canonical table 계약이 일치하지 않습니다: "
            f"source_missing={missing_source_tables}, target_missing={missing_target_tables}"
        )

    for table in tables:
        available_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s",
                [table],
            ).fetchall()
        }
        expected_columns = set(source_columns[table])
        if expected_columns != available_columns:
            raise RuntimeError(
                f"원본/대상 컬럼 계약이 일치하지 않습니다: {table}; "
                f"source_missing={sorted(available_columns - expected_columns)}, "
                f"target_missing={sorted(expected_columns - available_columns)}"
            )

    table_set = set(tables)
    required_constraints = {
        item for item in REQUIRED_TARGET_CONSTRAINTS if item[0] in table_set
    }
    existing_constraints = {
        (row[0], row[1])
        for row in connection.execute(
            """SELECT relation.relname, constraint_record.conname
            FROM pg_constraint AS constraint_record
            JOIN pg_class AS relation ON relation.oid=constraint_record.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace
            WHERE namespace.nspname='public'"""
        ).fetchall()
    }
    missing_constraints = sorted(required_constraints - existing_constraints)
    if missing_constraints:
        raise RuntimeError(
            "대상 PostgreSQL 필수 constraint가 없습니다: "
            + json.dumps(missing_constraints, ensure_ascii=False)
        )

    required_indexes = {item for item in REQUIRED_TARGET_INDEXES if item[0] in table_set}
    existing_indexes = {
        (row[0], row[1])
        for row in connection.execute(
            "SELECT tablename, indexname FROM pg_indexes WHERE schemaname='public'"
        ).fetchall()
    }
    missing_indexes = sorted(required_indexes - existing_indexes)
    if missing_indexes:
        raise RuntimeError(
            "대상 PostgreSQL 필수 index가 없습니다: "
            + json.dumps(missing_indexes, ensure_ascii=False)
        )

    nonempty = []
    for table in tables:
        count = connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()[0]
        if count:
            nonempty.append(f"{table}={count}")
    if nonempty:
        raise RuntimeError("대상 DB가 비어 있지 않습니다: " + ", ".join(nonempty[:10]))


def restore_staged_references(
    source: duckdb.DuckDBPyConnection,
    target: psycopg.Connection,
    tables: list[str],
    batch_size: int,
) -> None:
    source_columns = source_table_columns(source, tables)
    for table, staged_columns in STAGED_REFERENCE_COLUMNS.items():
        if table not in source_columns:
            continue
        for column in staged_columns & set(source_columns[table]):
            expected_count = int(source.execute(f'SELECT count(*) FROM "{table}" WHERE "{column}" IS NOT NULL').fetchone()[0])
            select_cursor = source.execute(f'SELECT "id", "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL')
            update = sql.SQL("UPDATE {} SET {}={} WHERE id={} AND {} IS NULL").format(
                sql.Identifier(table), sql.Identifier(column), sql.Placeholder(), sql.Placeholder(), sql.Identifier(column)
            )
            restored_count = 0
            with target.cursor() as update_cursor:
                while True:
                    rows = select_cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    batch_restored = 0
                    for row in rows:
                        update_cursor.execute(update, [row[1], row[0]])
                        if update_cursor.rowcount != 1:
                            raise RuntimeError(f"staged reference restore conflicted: {table}.{column}")
                        batch_restored += 1
                    if batch_restored != len(rows):
                        raise RuntimeError(f"staged reference restore batch mismatch: {table}.{column}")
                    restored_count += batch_restored
            if restored_count != expected_count:
                raise RuntimeError(f"staged reference restore count mismatch: {table}.{column}")
            print(f"restored {table}.{column}: {restored_count}")


def copy_tables(source: duckdb.DuckDBPyConnection, target: psycopg.Connection, tables: list[str], batch_size: int) -> None:
    nonempty = []
    for table in tables:
        count = target.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
        if count:
            nonempty.append(f"{table}={count}")
    if nonempty:
        raise RuntimeError("대상 DB가 비어 있지 않습니다: " + ", ".join(nonempty[:10]))

    raw_source_columns = source_table_columns(source, tables)
    canonical_source_columns = comparison_columns(raw_source_columns)
    for table in tables:
        if table not in raw_source_columns:
            continue
        columns = raw_source_columns[table]
        insert_columns = canonical_source_columns[table]
        json_columns = target_json_columns(target, table)
        select_cursor = source.execute(f'SELECT * FROM "{table}"')
        insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, insert_columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in insert_columns),
        )
        with target.cursor() as insert_cursor:
            while True:
                rows = select_cursor.fetchmany(batch_size)
                if not rows:
                    break
                transformed_rows = [
                    transform_source_row(
                        table,
                        columns,
                        row,
                        stage_columns=STAGED_REFERENCE_COLUMNS.get(table, frozenset()),
                        output_columns=insert_columns,
                    )
                    for row in rows
                ]
                adapted = [tuple(Jsonb(json.loads(value) if isinstance(value, str) else value) if column in json_columns and value is not None else value for column, value in zip(insert_columns, row)) for row in transformed_rows]
                insert_cursor.executemany(insert, adapted)
        copied_count = source.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        print(f"copied {table}: {copied_count}")
    restore_staged_references(source, target, tables, batch_size)


def recovery_lease_preflight(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    source_tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    table = "batch_execution_attempts"
    if table not in source_tables:
        return {"status": "legacy_table_missing", "active_recovery_lease_count": 0, "safe_to_execute": True}
    columns, _ = source_table_info(connection, table)
    lease_columns = set(RECOVERY_LEASE_COLUMNS)
    present_lease_columns = lease_columns & set(columns)
    if not present_lease_columns:
        return {"status": "legacy_column_missing", "active_recovery_lease_count": 0, "safe_to_execute": True}
    if present_lease_columns != lease_columns:
        return {
            "status": "blocked_partial_lease_schema",
            "active_recovery_lease_count": None,
            "safe_to_execute": False,
        }
    active_count, residual_count, invalid_count = connection.execute(
        """SELECT
            count(*) FILTER (WHERE recovery_lease_owner_id IS NOT NULL
                                  AND recovery_lease_token IS NOT NULL
                                  AND recovery_lease_acquired_at IS NOT NULL
                                  AND recovery_lease_expires_at IS NOT NULL
                                  AND recovery_lease_generation > 0
                                  AND recovery_lease_expires_at > recovery_lease_acquired_at
                                  AND recovery_lease_expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')),
            count(*) FILTER (WHERE recovery_lease_owner_id IS NOT NULL
                                  OR recovery_lease_token IS NOT NULL
                                  OR recovery_lease_acquired_at IS NOT NULL
                                  OR recovery_lease_expires_at IS NOT NULL),
            count(*) FILTER (WHERE recovery_lease_generation IS NULL
                                  OR recovery_lease_generation < 0
                                  OR ((recovery_lease_owner_id IS NULL)
                                      <> (recovery_lease_token IS NULL))
                                  OR ((recovery_lease_owner_id IS NULL)
                                      <> (recovery_lease_acquired_at IS NULL))
                                  OR ((recovery_lease_owner_id IS NULL)
                                      <> (recovery_lease_expires_at IS NULL))
                                  OR (recovery_lease_owner_id IS NOT NULL
                                      AND (recovery_lease_generation <= 0
                                           OR recovery_lease_expires_at <= recovery_lease_acquired_at)))
        FROM batch_execution_attempts"""
    ).fetchone()
    active_count, residual_count, invalid_count = map(int, (active_count, residual_count, invalid_count))
    if invalid_count:
        status = "blocked_invalid_recovery_leases"
    elif active_count:
        status = "blocked_active_recovery_leases"
    elif residual_count:
        status = "blocked_residual_recovery_leases"
    else:
        status = "clear"
    return {
        "status": status,
        "active_recovery_lease_count": active_count,
        "residual_recovery_lease_count": residual_count,
        "invalid_recovery_lease_count": invalid_count,
        "safe_to_execute": status == "clear",
    }


def compare(source: dict[str, dict[str, Any]], target: dict[str, dict[str, Any]]) -> list[str]:
    return [table for table, expected in source.items() if expected != target.get(table)]


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def execute_copy_and_verify(
    source: duckdb.DuckDBPyConnection,
    target: psycopg.Connection,
    tables: list[str],
    batch_size: int,
    source_state: dict[str, dict[str, Any]],
    source_columns: dict[str, list[str]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Keep copy, cyclic-reference restore, and verification atomic to callers."""
    try:
        target_schema_preflight(target, tables, source_columns)
        copy_tables(source, target, tables, batch_size)
        target_state = target_manifest(target, tables, source_columns=source_columns)
        differences = compare(source_state, target_state)
        if differences:
            raise RuntimeError("복사 후 검증 불일치: " + ", ".join(differences))
        target.commit()
        return target_state, differences
    except Exception:
        target.rollback()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run, copy, and verify DuckDB data into a migrated PostgreSQL database.")
    parser.add_argument("--source", type=Path, default=Path(os.getenv("ANALYSIS_DUCKDB_PATH", Path(__file__).resolve().parents[1] / "data" / "analysis_dashboard.duckdb")))
    parser.add_argument("--target-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--execute", action="store_true", help="Actually copy. Without this flag the command is read-only.")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--json-output", action="store_true", help="Print the complete verification manifest to stdout.")
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise RuntimeError("batch-size는 1 이상이어야 합니다.")
    if args.execute and not args.target_url:
        raise RuntimeError("--execute에는 PostgreSQL target URL이 필요합니다.")
    if not args.source.is_file():
        raise RuntimeError(f"DuckDB 원본을 찾을 수 없습니다: {args.source}")
    tables = table_order()
    with duckdb.connect(str(args.source), read_only=True) as source:
        # One explicit read transaction fixes manifests, audits, and rows to
        # one DuckDB snapshot for the complete target transaction.
        source.execute("BEGIN TRANSACTION")
        raw_source_columns = source_table_columns(source, tables)
        source_columns = comparison_columns(raw_source_columns)
        source_state = source_manifest(source, tables, source_columns=source_columns)
        source_schema_findings = source_schema_audit(raw_source_columns, tables)
        schema_hard_findings = [finding for finding in source_schema_findings if finding["severity"] == "hard"]
        relationships = [] if schema_hard_findings else relationship_audit(source)
        lease_preflight = recovery_lease_preflight(source)
        payload: dict[str, Any] = {
            "source": str(args.source.resolve()),
            "tables": source_state,
            "source_schema_findings": source_schema_findings,
            "relationship_findings": relationships,
            "recovery_lease_preflight": lease_preflight,
        }
        relationship_hard_findings = [finding for finding in relationships if finding["severity"] == "hard"]
        safe_to_execute = not schema_hard_findings and not relationship_hard_findings and bool(lease_preflight["safe_to_execute"])
        payload["transfer_preflight"] = {
            "status": "clear" if safe_to_execute else "blocked",
            "hard_source_schema_finding_count": len(schema_hard_findings),
            "hard_relationship_finding_count": len(relationship_hard_findings),
            "safe_to_execute": safe_to_execute,
        }
        if not safe_to_execute:
            if args.manifest:
                write_manifest(args.manifest, payload)
            if args.json_output:
                print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            if not lease_preflight["safe_to_execute"]:
                raise RuntimeError(
                    "batch recovery lease preflight가 안전하지 않아 DuckDB→PostgreSQL 복사를 중단했습니다: "
                    f"count={lease_preflight['active_recovery_lease_count']}"
                )
            if schema_hard_findings:
                raise RuntimeError(
                    "원본 canonical schema 계약이 일치하지 않아 복사를 중단했습니다: "
                    + json.dumps(schema_hard_findings, ensure_ascii=False)
                )
            raise RuntimeError(
                "필수 참조 무결성 오류가 있어 복사를 중단했습니다: "
                + json.dumps(relationship_hard_findings, ensure_ascii=False)
            )
        if args.target_url:
            url = args.target_url.replace("postgresql+psycopg://", "postgresql://")
            # Do not use Connection as a context manager here: its normal
            # __exit__ commits, which would duplicate execute_copy_and_verify's
            # single deliberate commit (and its single rollback on failure).
            target = psycopg.connect(url)
            try:
                if args.execute:
                    target_state, differences = execute_copy_and_verify(
                        source, target, tables, args.batch_size, source_state, source_columns
                    )
                    payload.update({"target": target.info.dbname, "target_tables": target_state, "differences": differences})
                else:
                    target_state = target_manifest(target, tables, source_columns=source_columns)
                    differences = compare(source_state, target_state)
                    payload.update({"target": target.info.dbname, "target_tables": target_state, "differences": differences})
            finally:
                # A dry-run deliberately does not commit its read transaction;
                # close terminates it while execute has already committed or
                # rolled back exactly once in execute_copy_and_verify.
                target.close()
        if args.manifest:
            write_manifest(args.manifest, payload)
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            source_rows = sum(item["count"] for item in source_state.values())
            hard_count = sum(finding.get("orphan_count", 0) + finding.get("mismatch_count", 0) for finding in relationships if finding["severity"] == "hard")
            warning_count = sum(finding.get("orphan_count", 0) for finding in relationships if finding["severity"] == "warning")
            mode = "copied and verified" if args.execute else "dry-run verified"
            print(f"DuckDB -> PostgreSQL {mode}: tables={len(tables)}, source_rows={source_rows}, hard_orphans={hard_count}, warning_orphans={warning_count}")
            if "differences" in payload:
                print(f"checksum_differences={len(payload['differences'])}")
            if args.manifest:
                print(f"manifest={args.manifest.resolve()}")
        source.execute("COMMIT")


if __name__ == "__main__":
    main()
