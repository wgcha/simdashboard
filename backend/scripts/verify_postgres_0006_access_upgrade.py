from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import psycopg


ACCESS_TABLES = (
    "role_menu_policies",
    "menu_policy_versions",
    "menu_policy_state",
    "menu_definitions",
    "project_invitations",
    "project_memberships",
    "project_workspace_layout_versions",
    "project_workspace_layouts",
)
USER_ACCESS_COLUMNS = (
    "employee_id",
    "email",
    "department",
    "job_title",
    "oidc_issuer",
    "oidc_subject",
    "account_status",
    "is_global_admin",
    "approved_by",
    "approved_at",
    "last_login_at",
)


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://")


def prepare(connection: psycopg.Connection) -> None:
    for table in ACCESS_TABLES:
        connection.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for table in ("analysis_requests", "request_steps", "request_work_items"):
        connection.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS owner_user_id CASCADE")
    for column in USER_ACCESS_COLUMNS:
        connection.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {column} CASCADE")

    now = datetime.now(timezone.utc)
    users = (
        ("fixture-viewer", "fixture-viewer", "Fixture Viewer", "viewer"),
        ("fixture-editor", "fixture-editor", "Fixture Editor", "editor"),
        ("fixture-admin", "fixture-admin", "Fixture Admin", "admin"),
        ("fixture-twin-a", "fixture-twin-a", "Duplicate Owner", "viewer"),
        ("fixture-twin-b", "fixture-twin-b", "Duplicate Owner", "viewer"),
    )
    for user_id, username, display_name, role in users:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active, created_at, updated_at)
            VALUES (%s, %s, 'fixture-password-hash', %s, %s, true, %s, %s)
            ON CONFLICT (id) DO UPDATE SET display_name=excluded.display_name, legacy_role=excluded.legacy_role
            """,
            [user_id, username, display_name, role, now, now],
        )
    connection.execute(
        """
        INSERT INTO projects (id, name, product_name, description, created_at)
        VALUES ('fixture-project', 'Fixture Project', 'Fixture Product', '0006 upgrade fixture', %s)
        ON CONFLICT (id) DO NOTHING
        """,
        [now],
    )
    connection.execute(
        """
        INSERT INTO analysis_requests
            (id, project_id, title, status, owner, requested_at, due_at, overall_note)
        VALUES
            ('fixture-request-unique', 'fixture-project', 'Unique owner', 'READY', 'fixture-editor', %s, %s, ''),
            ('fixture-request-ambiguous', 'fixture-project', 'Ambiguous owner', 'READY', 'Duplicate Owner', %s, %s, '')
        ON CONFLICT (id) DO NOTHING
        """,
        [now, now, now, now],
    )
    connection.execute(
        """
        INSERT INTO request_steps
            (id, request_id, sequence_no, name, status, owner, progress, is_optional)
        VALUES ('fixture-step', 'fixture-request-unique', 1, 'Fixture Step', 'READY', 'fixture-editor', 0, false)
        ON CONFLICT (id) DO NOTHING
        """
    )
    connection.execute(
        """
        INSERT INTO request_work_items
            (id, request_id, node_key, task_type_id, task_type_version, sequence_no,
             display_name, status, progress, owner)
        VALUES ('fixture-item', 'fixture-request-unique', 'fixture-node', 'cad-prepare', 1, 1,
                'Fixture Work Item', 'READY', 0, 'fixture-editor')
        ON CONFLICT (id) DO NOTHING
        """
    )


def assert_upgraded(connection: psycopg.Connection) -> None:
    missing = [
        table
        for table in ACCESS_TABLES
        if connection.execute("SELECT to_regclass(%s)", [f"public.{table}"]).fetchone()[0] is None
    ]
    if missing:
        raise AssertionError(f"0007 tables missing: {missing}")

    status = connection.execute(
        "SELECT account_status, is_global_admin FROM users WHERE id='fixture-admin'"
    ).fetchone()
    if status != ("ACTIVE", True):
        raise AssertionError(f"legacy admin backfill mismatch: {status}")

    roles = dict(
        connection.execute(
            """
            SELECT users.id, memberships.role
            FROM project_memberships memberships JOIN users ON users.id=memberships.user_id
            WHERE memberships.project_id='fixture-project' AND users.id IN ('fixture-viewer', 'fixture-editor')
            """
        ).fetchall()
    )
    if roles != {"fixture-viewer": "general", "fixture-editor": "power"}:
        raise AssertionError(f"membership backfill mismatch: {roles}")

    owners = dict(
        connection.execute(
            "SELECT id, owner_user_id FROM analysis_requests WHERE id LIKE 'fixture-request-%'"
        ).fetchall()
    )
    if owners.get("fixture-request-unique") != "fixture-editor":
        raise AssertionError(f"unique owner backfill mismatch: {owners}")
    if owners.get("fixture-request-ambiguous") is not None:
        raise AssertionError(f"ambiguous owner must remain NULL: {owners}")
    for table, row_id in (("request_steps", "fixture-step"), ("request_work_items", "fixture-item")):
        owner = connection.execute(f"SELECT owner_user_id FROM {table} WHERE id=%s", [row_id]).fetchone()[0]
        if owner != "fixture-editor":
            raise AssertionError(f"{table} owner backfill mismatch: {owner}")

    menu_state = connection.execute("SELECT version FROM menu_policy_state WHERE id='global'").fetchone()
    if menu_state != (1,):
        raise AssertionError(f"menu policy seed mismatch: {menu_state}")
    layout_count = connection.execute(
        "SELECT count(*) FROM project_workspace_layouts WHERE project_id='fixture-project'"
    ).fetchone()[0]
    if layout_count != 2:
        raise AssertionError(f"project workspace backfill mismatch: {layout_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare or assert a real PostgreSQL 0006→0007 access-control fixture.")
    parser.add_argument("mode", choices=("prepare", "assert"))
    args = parser.parse_args()
    with psycopg.connect(database_url(), autocommit=True) as connection:
        prepare(connection) if args.mode == "prepare" else assert_upgraded(connection)
    print(f"PostgreSQL 0006 access upgrade fixture {args.mode}: OK")


if __name__ == "__main__":
    main()
