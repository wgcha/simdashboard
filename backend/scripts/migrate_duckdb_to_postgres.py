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
    ("batch_execution_attempts", "work_item_id", "request_work_items", "id", "hard"),
    ("batch_execution_attempts", "workflow_run_id", "workflow_runs", "id", "hard"),
    ("batch_execution_events", "attempt_id", "batch_execution_attempts", "id", "hard"),
    ("project_memberships", "project_id", "projects", "id", "hard"),
    ("project_memberships", "user_id", "users", "id", "hard"),
    ("project_invitations", "project_id", "projects", "id", "hard"),
    ("project_invitations", "resolved_user_id", "users", "id", "warning"),
    ("role_menu_policies", "menu_id", "menu_definitions", "id", "hard"),
    ("analysis_requests", "owner_user_id", "users", "id", "warning"),
    ("request_steps", "owner_user_id", "users", "id", "warning"),
    ("request_work_items", "owner_user_id", "users", "id", "warning"),
]


def table_order() -> list[str]:
    return re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-zA-Z0-9_]+)", SCHEMA_FILE.read_text(encoding="utf-8"), flags=re.IGNORECASE)


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


def transform_source_row(table: str, columns: list[str], row: tuple[Any, ...]) -> tuple[Any, ...]:
    if table != "request_steps":
        return row
    values = dict(zip(columns, row))
    blocked_reason = values.get("blocked_reason")
    if isinstance(blocked_reason, str) and blocked_reason.lower() in {"true", "false"}:
        if values.get("is_optional") is None:
            values["is_optional"] = blocked_reason.lower() == "true"
        values["blocked_reason"] = None
    if values.get("is_optional") is None:
        values["is_optional"] = False
    return tuple(values[column] for column in columns)


def source_table_info(connection: duckdb.DuckDBPyConnection, table: str) -> tuple[list[str], set[str]]:
    columns = connection.execute(f'SELECT * FROM "{table}" LIMIT 0').description
    names = [column[0] for column in columns]
    json_columns = {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='main' AND table_name=? AND data_type='JSON'", [table]).fetchall()}
    return names, json_columns


def target_json_columns(connection: psycopg.Connection, table: str) -> set[str]:
    return {row[0] for row in connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s AND data_type IN ('json','jsonb')", [table]).fetchall()}


def source_manifest(connection: duckdb.DuckDBPyConnection, tables: list[str]) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    source_tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    for table in tables:
        if table not in source_tables:
            manifest[table] = {"count": 0, "checksum": "missing"}
            continue
        source_columns, json_columns = source_table_info(connection, table)
        columns = sorted(source_columns)
        projection = ", ".join(f'"{column}"' for column in columns)
        transformed = (transform_source_row(table, columns, row) for row in cursor_rows(connection.execute(f'SELECT {projection} FROM "{table}"')))
        count, checksum = digest_rows(transformed, columns, json_columns)
        manifest[table] = {"count": count, "checksum": checksum}
    return manifest


def relationship_audit(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    findings = []
    for child, child_key, parent, parent_key, severity in RELATIONSHIPS:
        count = connection.execute(
            f'SELECT count(*) FROM "{child}" child LEFT JOIN "{parent}" parent ON child."{child_key}"=parent."{parent_key}" '
            f'WHERE child."{child_key}" IS NOT NULL AND parent."{parent_key}" IS NULL'
        ).fetchone()[0]
        if count:
            findings.append({"severity": severity, "relationship": f"{child}.{child_key}->{parent}.{parent_key}", "orphan_count": count})
    return findings


def target_manifest(connection: psycopg.Connection, tables: list[str]) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    target_tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    for table in tables:
        if table not in target_tables:
            manifest[table] = {"count": 0, "checksum": "missing"}
            continue
        columns = sorted(row[0] for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            [table],
        ).fetchall())
        cursor = connection.execute(
            sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.Identifier(table),
            )
        )
        count, checksum = digest_rows(cursor_rows(cursor), columns, target_json_columns(connection, table))
        manifest[table] = {"count": count, "checksum": checksum}
    return manifest


def copy_tables(source: duckdb.DuckDBPyConnection, target: psycopg.Connection, tables: list[str], batch_size: int) -> None:
    nonempty = []
    for table in tables:
        count = target.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
        if count:
            nonempty.append(f"{table}={count}")
    if nonempty:
        raise RuntimeError("대상 DB가 비어 있지 않습니다: " + ", ".join(nonempty[:10]))

    source_tables = {row[0] for row in source.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    for table in tables:
        if table not in source_tables:
            continue
        columns, _ = source_table_info(source, table)
        json_columns = target_json_columns(target, table)
        select_cursor = source.execute(f'SELECT * FROM "{table}"')
        insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        with target.cursor() as insert_cursor:
            while True:
                rows = select_cursor.fetchmany(batch_size)
                if not rows:
                    break
                transformed_rows = [transform_source_row(table, columns, row) for row in rows]
                adapted = [tuple(Jsonb(json.loads(value) if isinstance(value, str) else value) if column in json_columns and value is not None else value for column, value in zip(columns, row)) for row in transformed_rows]
                insert_cursor.executemany(insert, adapted)
        copied_count = source.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        print(f"copied {table}: {copied_count}")


def compare(source: dict[str, dict[str, Any]], target: dict[str, dict[str, Any]]) -> list[str]:
    return [table for table, expected in source.items() if expected != target.get(table)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run, copy, and verify DuckDB data into a migrated PostgreSQL database.")
    parser.add_argument("--source", type=Path, default=Path(os.getenv("ANALYSIS_DUCKDB_PATH", Path(__file__).resolve().parents[1] / "data" / "analysis_dashboard.duckdb")))
    parser.add_argument("--target-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--execute", action="store_true", help="Actually copy. Without this flag the command is read-only.")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--json-output", action="store_true", help="Print the complete verification manifest to stdout.")
    args = parser.parse_args()
    if not args.source.is_file():
        raise RuntimeError(f"DuckDB 원본을 찾을 수 없습니다: {args.source}")
    tables = table_order()
    with duckdb.connect(str(args.source), read_only=True) as source:
        source_state = source_manifest(source, tables)
        relationships = relationship_audit(source)
        payload: dict[str, Any] = {"source": str(args.source.resolve()), "tables": source_state, "relationship_findings": relationships}
        if args.target_url:
            url = args.target_url.replace("postgresql+psycopg://", "postgresql://")
            with psycopg.connect(url) as target:
                if args.execute:
                    hard_findings = [finding for finding in relationships if finding["severity"] == "hard"]
                    if hard_findings:
                        raise RuntimeError("필수 참조 무결성 오류가 있어 복사를 중단했습니다: " + json.dumps(hard_findings, ensure_ascii=False))
                    copy_tables(source, target, tables, args.batch_size)
                    target.commit()
                target_state = target_manifest(target, tables)
                differences = compare(source_state, target_state)
                payload.update({"target": target.info.dbname, "target_tables": target_state, "differences": differences})
                if args.execute and differences:
                    raise RuntimeError("복사 후 검증 불일치: " + ", ".join(differences))
        if args.manifest:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        if args.json_output:
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            source_rows = sum(item["count"] for item in source_state.values())
            hard_count = sum(finding["orphan_count"] for finding in relationships if finding["severity"] == "hard")
            warning_count = sum(finding["orphan_count"] for finding in relationships if finding["severity"] == "warning")
            mode = "copied and verified" if args.execute else "dry-run verified"
            print(f"DuckDB -> PostgreSQL {mode}: tables={len(tables)}, source_rows={source_rows}, hard_orphans={hard_count}, warning_orphans={warning_count}")
            if "differences" in payload:
                print(f"checksum_differences={len(payload['differences'])}")
            if args.manifest:
                print(f"manifest={args.manifest.resolve()}")


if __name__ == "__main__":
    main()
