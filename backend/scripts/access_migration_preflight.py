from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import database_settings  # noqa: E402


REQUIRED_TABLES = (
    "users",
    "projects",
    "analysis_requests",
    "request_steps",
    "request_work_items",
    "project_memberships",
    "project_invitations",
    "menu_definitions",
    "menu_policy_state",
    "role_menu_policies",
    "menu_policy_versions",
)
OWNER_TABLES = ("analysis_requests", "request_steps", "request_work_items")


def _rows(connection: Any, sql: str, parameters: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, parameters or [])
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]


def _table_names(connection: Any) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')"
        ).fetchall()
    }


def _columns(connection: Any, table: str) -> set[str]:
    if table not in {*REQUIRED_TABLES, *OWNER_TABLES}:
        raise ValueError("unsupported table")
    return {
        row[0]
        for row in connection.execute(
            f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'",
        ).fetchall()
    }


def build_report(connection: Any, backend: str) -> dict[str, Any]:
    tables = _table_names(connection)
    missing = sorted(set(REQUIRED_TABLES) - tables)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if missing:
        blockers.append({"code": "ACCESS_SCHEMA_MISSING", "tables": missing})

    report: dict[str, Any] = {
        "schema_version": 1,
        "backend": backend,
        "status": "ready",
        "alembic_revision": None,
        "counts": {},
        "identity_conflicts": [],
        "unresolved_owners": {},
        "projects_without_admin": [],
        "blockers": blockers,
        "warnings": warnings,
    }

    if "alembic_version" in tables:
        revisions = [row[0] for row in connection.execute("SELECT version_num FROM alembic_version").fetchall()]
        report["alembic_revision"] = revisions[0] if len(revisions) == 1 else revisions

    for table in ("users", "projects", "project_memberships", "project_invitations", "menu_policy_versions"):
        if table in tables:
            report["counts"][table] = int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])

    if "users" in tables:
        user_columns = _columns(connection, "users")
        for column, code in (
            ("employee_id", "DUPLICATE_EMPLOYEE_ID"),
            ("username", "DUPLICATE_USERNAME"),
        ):
            if column in user_columns:
                conflicts = _rows(
                    connection,
                    f"SELECT {column} AS value, count(*) AS count FROM users "
                    f"WHERE {column} IS NOT NULL AND trim({column}) <> '' GROUP BY {column} HAVING count(*) > 1",
                )
                report["identity_conflicts"].extend({"code": code, **item} for item in conflicts)
        if {"oidc_issuer", "oidc_subject"}.issubset(user_columns):
            conflicts = _rows(
                connection,
                "SELECT oidc_issuer, oidc_subject, count(*) AS count FROM users "
                "WHERE oidc_issuer IS NOT NULL AND oidc_subject IS NOT NULL "
                "GROUP BY oidc_issuer, oidc_subject HAVING count(*) > 1",
            )
            report["identity_conflicts"].extend({"code": "DUPLICATE_OIDC_IDENTITY", **item} for item in conflicts)
        if report["identity_conflicts"]:
            blockers.append({"code": "IDENTITY_CONFLICTS", "count": len(report["identity_conflicts"])})

        if {"account_status", "is_global_admin"}.issubset(user_columns):
            active_globals = int(
                connection.execute(
                    "SELECT count(*) FROM users WHERE account_status='ACTIVE' AND is_global_admin=true"
                ).fetchone()[0]
            )
            report["counts"]["active_global_admins"] = active_globals
            if active_globals == 0:
                warnings.append({"code": "NO_ACTIVE_GLOBAL_ADMIN", "recovery": "approve_oidc_global_admin.py"})

    for table in OWNER_TABLES:
        if table not in tables:
            continue
        columns = _columns(connection, table)
        if {"owner", "owner_user_id"}.issubset(columns):
            unresolved = int(
                connection.execute(
                    f"SELECT count(*) FROM {table} WHERE owner_user_id IS NULL AND owner IS NOT NULL AND trim(owner) <> ''"
                ).fetchone()[0]
            )
            report["unresolved_owners"][table] = unresolved
            if unresolved:
                warnings.append({"code": "OWNER_REASSIGNMENT_REQUIRED", "table": table, "count": unresolved})

    if {"projects", "project_memberships", "users"}.issubset(tables):
        report["projects_without_admin"] = _rows(
            connection,
            "SELECT projects.id AS project_id, projects.name AS project_name "
            "FROM projects LEFT JOIN project_memberships memberships "
            "ON memberships.project_id=projects.id AND memberships.role='admin' "
            "LEFT JOIN users ON users.id=memberships.user_id AND users.account_status='ACTIVE' "
            "GROUP BY projects.id, projects.name HAVING count(users.id)=0 ORDER BY projects.id",
        )
        if report["projects_without_admin"]:
            blockers.append({"code": "PROJECT_ADMIN_MISSING", "count": len(report["projects_without_admin"])})

    if "menu_policy_state" in tables:
        state = connection.execute("SELECT version FROM menu_policy_state WHERE id='global'").fetchone()
        report["menu_policy_version"] = int(state[0]) if state else None
        if state is None:
            blockers.append({"code": "MENU_POLICY_STATE_MISSING"})

    report["status"] = "blocked" if blockers else "ready_with_warnings" if warnings else "ready"
    return report


def _connect() -> tuple[Any, str]:
    settings = database_settings()
    if settings.backend == "duckdb":
        return duckdb.connect(str(settings.duckdb_path), read_only=True), "duckdb"
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL preflight")
    import psycopg

    return psycopg.connect(settings.database_url), "postgresql"


def main() -> int:
    try:
        connection, backend = _connect()
        try:
            report = build_report(connection, backend)
        finally:
            connection.close()
    except Exception as exc:
        report = {
            "schema_version": 1,
            "status": "error",
            "blockers": [{"code": "PREFLIGHT_FAILED", "message": str(exc)}],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] in {"ready", "ready_with_warnings"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
