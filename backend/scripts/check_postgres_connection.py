from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import database_settings


BACKEND = Path(__file__).resolve().parents[1]
REQUIRED_TABLES = (
    "users",
    "projects",
    "analysis_requests",
    "analysis_runs",
    "audit_events",
    "workspace_layouts",
    "project_memberships",
    "asset_blobs",
    "asset_blob_chunks",
    "drop_video_assets",
)


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _expected_head() -> str:
    config = Config(str(BACKEND / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head:
        raise RuntimeError("Alembic head를 확인할 수 없습니다.")
    return head


def _verify_runtime_contract(connection: psycopg.Connection) -> tuple[str, str, str]:
    current_database, current_user = connection.execute(
        "SELECT current_database(), current_user"
    ).fetchone()
    expected_role = os.getenv("SIM_DASH_APP_ROLE", "simdashboard_app")
    if current_user != expected_role:
        raise RuntimeError(f"앱 연결 역할이 올바르지 않습니다: {current_user}")

    missing = [
        table
        for table in REQUIRED_TABLES
        if connection.execute("SELECT to_regclass(%s)", [f"public.{table}"]).fetchone()[0] is None
    ]
    if missing:
        raise RuntimeError(f"필수 PostgreSQL 테이블이 없습니다: {', '.join(missing)}")

    revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    if len(revisions) != 1 or not revisions[0][0]:
        raise RuntimeError("Alembic revision은 정확히 하나여야 합니다.")
    revision = revisions[0][0]
    expected_head = _expected_head()
    if revision != expected_head:
        raise RuntimeError(f"Alembic revision 불일치: current={revision}, head={expected_head}")

    privileges = connection.execute(
        "SELECT "
        "has_database_privilege(current_user, current_database(), 'CONNECT'), "
        "has_database_privilege(current_user, current_database(), 'CREATE'), "
        "has_schema_privilege(current_user, 'public', 'USAGE'), "
        "has_schema_privilege(current_user, 'public', 'CREATE'), "
        "has_table_privilege(current_user, 'public.projects', 'SELECT,INSERT,UPDATE,DELETE'), "
        "has_table_privilege(current_user, 'public.audit_events', 'SELECT,INSERT'), "
        "has_table_privilege(current_user, 'public.audit_events', 'UPDATE,DELETE,TRUNCATE')"
    ).fetchone()
    if privileges != (True, False, True, False, True, True, False):
        raise RuntimeError(f"앱 역할의 최소 권한 계약이 올바르지 않습니다: {privileges}")

    connection.rollback()
    marker = uuid4().hex
    with connection.transaction(force_rollback=True):
        connection.execute(
            "INSERT INTO projects (id, name, product_name, description, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            [marker, "connection-smoke", "connection-smoke", "rollback-only", datetime.now(timezone.utc)],
        )
        connection.execute("UPDATE projects SET description=%s WHERE id=%s", ["updated", marker])
        connection.execute("DELETE FROM projects WHERE id=%s", [marker])
        connection.execute(
            "INSERT INTO audit_events "
            "(id, occurred_at, username, action, method, path, status_code, request_id, detail_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                f"preflight-{marker}",
                datetime.now(timezone.utc),
                current_user,
                "postgres.preflight",
                "CHECK",
                "/internal/postgres-preflight",
                200,
                marker,
                Jsonb({"rollback": True}),
            ],
        )

    ddl_denied = False
    try:
        with connection.transaction(force_rollback=True):
            connection.execute(f"CREATE TABLE public.preflight_{marker} (id INTEGER)")
    except psycopg.errors.InsufficientPrivilege:
        ddl_denied = True
    if not ddl_denied:
        raise RuntimeError("앱 역할이 DDL을 실행할 수 있습니다. public CREATE 권한을 회수하세요.")

    return current_database, current_user, revision


def main() -> int:
    settings = database_settings()
    if settings.backend != "postgresql" or not settings.database_url:
        print("[ERROR] ANALYSIS_DB_BACKEND=postgresql 및 DATABASE_URL이 필요합니다.", file=sys.stderr)
        return 1
    try:
        with psycopg.connect(_psycopg_url(settings.database_url), connect_timeout=5) as connection:
            database, user, revision = _verify_runtime_contract(connection)
    except psycopg.OperationalError as exc:
        state = getattr(exc, "sqlstate", None)
        print(f"[ERROR] PostgreSQL 연결 실패 (SQLSTATE: {state or 'unknown'}).", file=sys.stderr)
        return 1
    except (psycopg.Error, RuntimeError) as exc:
        print(f"[ERROR] PostgreSQL preflight 실패: {exc}", file=sys.stderr)
        return 1

    print(f"PostgreSQL preflight passed: database={database}, user={user}, revision={revision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
