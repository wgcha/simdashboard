from __future__ import annotations

import argparse
import json
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
FIXTURE_PROJECT_ID = "fixture-project"
FIXTURE_REQUEST_ID = "fixture-request-unique"
FIXTURE_REQUEST_TYPE_ID = "fixture-request-type"
FIXTURE_TASK_TYPE_ID = "fixture-task-type"
FIXTURE_LAYOUTS = (
    (
        "portfolio",
        3,
        {"fontSize": 11, "chartOrder": ["status", "trend", "quality", "type"]},
        "fixture-layout-owner",
    ),
    (
        "workflow",
        4,
        {"fontSize": 12, "accentColor": "#345678", "items": []},
        "fixture-layout-owner",
    ),
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
        VALUES (%s, 'Fixture Project', 'Fixture Product', '0006 upgrade fixture', %s)
        ON CONFLICT (id) DO NOTHING
        """,
        [FIXTURE_PROJECT_ID, now],
    )
    # 0007 copies the legacy global layout current rows and histories into
    # project-scoped storage only when those legacy parent rows exist.
    for layout_kind, version, definition, owner in FIXTURE_LAYOUTS:
        encoded = json.dumps(definition, ensure_ascii=False, sort_keys=True)
        connection.execute(
            """
            INSERT INTO workspace_layouts
                (layout_kind, version, definition_json, updated_by, updated_at)
            VALUES (%s, %s, CAST(%s AS JSONB), %s, %s)
            ON CONFLICT (layout_kind) DO UPDATE SET
                version=excluded.version, definition_json=excluded.definition_json,
                updated_by=excluded.updated_by, updated_at=excluded.updated_at
            """,
            [layout_kind, version, encoded, owner, now],
        )
        connection.execute(
            """
            INSERT INTO workspace_layout_versions
                (layout_kind, version, definition_json, created_by, created_at, is_valid)
            VALUES (%s, %s, CAST(%s AS JSONB), %s, %s, true)
            ON CONFLICT (layout_kind, version) DO UPDATE SET
                definition_json=excluded.definition_json, created_by=excluded.created_by,
                created_at=excluded.created_at, is_valid=excluded.is_valid
            """,
            [layout_kind, version, encoded, owner, now],
        )
    connection.execute(
        """
        INSERT INTO analysis_requests
            (id, project_id, title, status, owner, requested_at, due_at, overall_note)
        VALUES
            (%s, %s, 'Unique owner', 'READY', 'fixture-editor', %s, %s, ''),
            ('fixture-request-ambiguous', %s, 'Ambiguous owner', 'READY', 'Duplicate Owner', %s, %s, '')
        ON CONFLICT (id) DO NOTHING
        """,
        [FIXTURE_REQUEST_ID, FIXTURE_PROJECT_ID, now, now, FIXTURE_PROJECT_ID, now, now],
    )
    connection.execute(
        """
        INSERT INTO request_steps
            (id, request_id, sequence_no, name, status, owner, progress, is_optional)
        VALUES ('fixture-step', 'fixture-request-unique', 1, 'Fixture Step', 'READY', 'fixture-editor', 0, false)
        ON CONFLICT (id) DO NOTHING
        """
    )
    # The 0004 work-item contract already enforces both parents: a plan for
    # the request and a versioned task type. Seed the smallest realistic plan
    # graph before inserting the ownership-backfill work item below.
    connection.execute(
        """
        INSERT INTO task_type_versions
            (id, version, kind, display_name, description, supports_standalone,
             input_artifact_types_json, output_artifact_types_json, parameter_schema_json,
             demo_artifact_url, is_active, created_at)
        VALUES (%s, 1, 'FIXTURE', 'Fixture Task', '0006 upgrade fixture task', true,
                '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, 'fixture://task', true, %s)
        ON CONFLICT (id, version) DO NOTHING
        """,
        [FIXTURE_TASK_TYPE_ID, now],
    )
    connection.execute(
        """
        INSERT INTO request_type_versions
            (id, version, display_name, description, allowed_task_types_json,
             default_workflow_json, match_rules_json, is_active, created_at)
        VALUES (CAST(%s AS VARCHAR), 1, 'Fixture Request Type', '0006 upgrade fixture request type',
                jsonb_build_array(jsonb_build_object('id', CAST(%s AS VARCHAR), 'version', 1)),
                jsonb_build_object('nodes', jsonb_build_array(jsonb_build_object(
                    'node_key', 'fixture-node', 'task_type_id', CAST(%s AS VARCHAR),
                    'task_type_version', 1, 'depends_on', '[]'::jsonb,
                    'display_name', 'Fixture Work Item'))),
                '{}'::jsonb, true, %s)
        ON CONFLICT (id, version) DO NOTHING
        """,
        [FIXTURE_REQUEST_TYPE_ID, FIXTURE_TASK_TYPE_ID, FIXTURE_TASK_TYPE_ID, now],
    )
    connection.execute(
        """
        INSERT INTO request_work_plans
            (request_id, request_type_id, request_type_version, scenario_name,
             source_type, source_reference, requested_by, definition_snapshot_json,
             assigned_by, assigned_at)
        VALUES (CAST(%s AS VARCHAR), CAST(%s AS VARCHAR), 1, 'Fixture Request Plan', 'DEPARTMENT_HEAD',
                '0006 upgrade fixture', 'fixture-admin',
                jsonb_build_object('nodes', jsonb_build_array(jsonb_build_object(
                    'node_key', 'fixture-node', 'task_type_id', CAST(%s AS VARCHAR),
                    'task_type_version', 1, 'depends_on', '[]'::jsonb,
                    'display_name', 'Fixture Work Item'))),
                'fixture-admin', %s)
        ON CONFLICT (request_id) DO NOTHING
        """,
        [FIXTURE_REQUEST_ID, FIXTURE_REQUEST_TYPE_ID, FIXTURE_TASK_TYPE_ID, now],
    )
    connection.execute(
        """
        INSERT INTO request_work_items
            (id, request_id, node_key, task_type_id, task_type_version, sequence_no,
             display_name, status, progress, owner)
        VALUES ('fixture-item', %s, 'fixture-node', %s, 1, 1,
                'Fixture Work Item', 'READY', 0, 'fixture-editor')
        ON CONFLICT (id) DO NOTHING
        """,
        [FIXTURE_REQUEST_ID, FIXTURE_TASK_TYPE_ID],
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

    work_item_parents = connection.execute(
        """
        SELECT plan.request_type_id, plan.request_type_version,
               item.task_type_id, item.task_type_version
        FROM request_work_plans AS plan
        JOIN request_work_items AS item ON item.request_id=plan.request_id
        WHERE plan.request_id=%s AND item.id='fixture-item'
        """,
        [FIXTURE_REQUEST_ID],
    ).fetchone()
    expected_work_item_parents = (
        FIXTURE_REQUEST_TYPE_ID,
        1,
        FIXTURE_TASK_TYPE_ID,
        1,
    )
    if work_item_parents != expected_work_item_parents:
        raise AssertionError(f"fixture work-item parent contract mismatch: {work_item_parents}")

    menu_state = connection.execute("SELECT version FROM menu_policy_state WHERE id='global'").fetchone()
    if menu_state != (1,):
        raise AssertionError(f"menu policy seed mismatch: {menu_state}")
    current_layouts = connection.execute(
        """
        SELECT project.layout_kind,
               project.version=global.version,
               project.definition_json=global.definition_json,
               project.updated_by=global.updated_by,
               project.updated_at=global.updated_at
        FROM project_workspace_layouts AS project
        JOIN workspace_layouts AS global ON global.layout_kind=project.layout_kind
        WHERE project.project_id=%s
        ORDER BY project.layout_kind
        """,
        [FIXTURE_PROJECT_ID],
    ).fetchall()
    expected_current_layouts = [(kind, True, True, True, True) for kind, *_ in FIXTURE_LAYOUTS]
    if current_layouts != expected_current_layouts:
        raise AssertionError(f"project workspace current-layout backfill mismatch: {current_layouts}")

    layout_versions = connection.execute(
        """
        SELECT project.layout_kind, project.version,
               project.definition_json=global.definition_json,
               project.created_by=global.created_by,
               project.created_at=global.created_at,
               project.is_valid=global.is_valid
        FROM project_workspace_layout_versions AS project
        JOIN workspace_layout_versions AS global
          ON global.layout_kind=project.layout_kind AND global.version=project.version
        WHERE project.project_id=%s
        ORDER BY project.layout_kind, project.version
        """,
        [FIXTURE_PROJECT_ID],
    ).fetchall()
    expected_layout_versions = [(kind, version, True, True, True, True) for kind, version, *_ in FIXTURE_LAYOUTS]
    if layout_versions != expected_layout_versions:
        raise AssertionError(f"project workspace version backfill mismatch: {layout_versions}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare or assert a real PostgreSQL 0006→0007 access-control fixture.")
    parser.add_argument("mode", choices=("prepare", "assert"))
    args = parser.parse_args()
    with psycopg.connect(database_url(), autocommit=True) as connection:
        prepare(connection) if args.mode == "prepare" else assert_upgraded(connection)
    print(f"PostgreSQL 0006 access upgrade fixture {args.mode}: OK")


if __name__ == "__main__":
    main()
