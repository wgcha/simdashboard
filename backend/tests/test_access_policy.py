from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import duckdb
import pytest
from fastapi import HTTPException, Request

from app.access_policy import (
    ALL_PERMISSIONS,
    COMPANY_PERMISSIONS,
    GENERAL_PERMISSIONS,
    GLOBAL_ADMIN_PERMISSIONS,
    POWER_PERMISSIONS,
    PROJECT_ADMIN_PERMISSIONS,
    ROLE_PERMISSIONS,
    permissions_for,
    project_role,
    require_assigned_work_item,
)
from app.database import ensure_access_control_schema, initialize_database
from app.database_connection import connect


@dataclass(frozen=True)
class StubPrincipal:
    user_id: str
    account_status: str = "ACTIVE"
    is_global_admin: bool = False


def _request(principal: StubPrincipal) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/test", "headers": []})
    request.state.principal = principal
    return request


@pytest.mark.unit
def test_permission_sets_match_the_product_matrix_exactly():
    assert GENERAL_PERMISSIONS == {
        "company.dashboard.view",
        "project.data.view",
        "report.export",
        "work.execute_assigned",
    }
    assert POWER_PERMISSIONS == GENERAL_PERMISSIONS | {
        "request.create",
        "request.edit",
        "workflow.edit",
        "result.import",
        "result.review",
    }
    assert PROJECT_ADMIN_PERMISSIONS == POWER_PERMISSIONS | {
        "dashboard.edit",
        "project.layout.edit",
        "project.threshold.manage",
        "project.variable.manage",
        "project.member.manage",
        "project.invitation.create",
    }
    assert GLOBAL_ADMIN_PERMISSIONS == PROJECT_ADMIN_PERMISSIONS | {
        "work.execute_any",
        "system.catalog.manage",
        "system.user.approve",
        "system.menu_policy.manage",
        "audit.view",
    }
    assert ALL_PERMISSIONS == GLOBAL_ADMIN_PERMISSIONS
    assert ROLE_PERMISSIONS == {
        "general": GENERAL_PERMISSIONS,
        "power": POWER_PERMISSIONS,
        "admin": PROJECT_ADMIN_PERMISSIONS,
    }


@pytest.mark.unit
def test_active_nonmember_keeps_company_read_permissions_only():
    principal = StubPrincipal("user-nonmember")
    assert permissions_for(principal, None) == COMPANY_PERMISSIONS
    assert "request.create" not in permissions_for(principal, None)
    assert permissions_for(StubPrincipal("pending", "PENDING"), "admin") == frozenset()
    assert permissions_for(StubPrincipal("suspended", "SUSPENDED"), "admin") == frozenset()
    assert permissions_for(StubPrincipal("global", is_global_admin=True), None) == ALL_PERMISSIONS


def test_project_role_and_assigned_work_use_user_id_not_display_name():
    initialize_database()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    suffix = uuid4().hex[:10]
    owner_id = f"user-owner-{suffix}"
    same_name_id = f"user-same-name-{suffix}"
    item_id = f"item-owner-{suffix}"
    try:
        with connect() as conn:
            for user_id in (owner_id, same_name_id):
                conn.execute(
                    """
                    INSERT INTO users
                        (id, username, display_name, account_status, is_global_admin,
                         is_active, created_at, updated_at)
                    VALUES (?, ?, '동명이인', 'ACTIVE', false, true, ?, ?)
                    """,
                    [user_id, user_id, now, now],
                )
                conn.execute(
                    """
                    INSERT INTO project_memberships
                        (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
                    VALUES (?, 'project-tv-001', ?, 'general', 'test', ?, 'test', ?)
                    """,
                    [f"membership-{user_id}", user_id, now, now],
                )
            conn.execute(
                """
                INSERT INTO request_work_items
                    (id, request_id, node_key, task_type_id, task_type_version, sequence_no,
                     display_name, status, progress, owner, owner_user_id)
                VALUES (?, 'request-drop-001', ?, 'cad-prepare', 1, 99,
                        'ID 소유권 검증', 'READY', 0, '동명이인', ?)
                """,
                [item_id, f"node-{suffix}", owner_id],
            )
            assert project_role(conn, StubPrincipal(owner_id), "project-tv-001") == "general"
            require_assigned_work_item(_request(StubPrincipal(owner_id)), item_id, conn=conn)
            with pytest.raises(HTTPException) as exc_info:
                require_assigned_work_item(_request(StubPrincipal(same_name_id)), item_id, conn=conn)
            assert exc_info.value.status_code == 403
            assert exc_info.value.detail["code"] == "WORK_ITEM_NOT_ASSIGNED"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM request_work_items WHERE id=?", [item_id])
            conn.execute("DELETE FROM project_memberships WHERE user_id IN (?, ?)", [owner_id, same_name_id])
            conn.execute("DELETE FROM users WHERE id IN (?, ?)", [owner_id, same_name_id])


def test_duckdb_legacy_upgrade_backfills_roles_owners_and_menu_seed(tmp_path):
    database_path = tmp_path / "legacy-0006.duckdb"
    with duckdb.connect(str(database_path)) as conn:
        conn.execute(
            """
            CREATE TABLE projects (id VARCHAR PRIMARY KEY);
            CREATE TABLE users (
                id VARCHAR PRIMARY KEY,
                username VARCHAR NOT NULL UNIQUE,
                password_hash VARCHAR NOT NULL,
                display_name VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                is_active BOOLEAN NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            CREATE TABLE analysis_requests (id VARCHAR PRIMARY KEY, owner VARCHAR);
            CREATE TABLE request_steps (id VARCHAR PRIMARY KEY, owner VARCHAR);
            CREATE TABLE request_work_items (id VARCHAR PRIMARY KEY, owner VARCHAR);
            """
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute("INSERT INTO projects VALUES ('project-a'), ('project-b')")
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, role, is_active, created_at, updated_at)
            VALUES
                ('admin', 'admin', 'hash', '관리자', 'admin', true, ?, ?),
                ('editor', 'editor', 'hash', '유일 담당자', 'editor', true, ?, ?),
                ('viewer-a', 'viewer-a', 'hash', '동명이인', 'viewer', true, ?, ?),
                ('viewer-b', 'viewer-b', 'hash', '동명이인', 'viewer', true, ?, ?)
            """,
            [now, now, now, now, now, now, now, now],
        )
        conn.execute("INSERT INTO analysis_requests VALUES ('unique-owner', '유일 담당자')")
        conn.execute("INSERT INTO request_steps VALUES ('ambiguous-owner', '동명이인')")
        conn.execute("INSERT INTO request_work_items VALUES ('username-owner', 'viewer-a')")

        ensure_access_control_schema(conn)
        ensure_access_control_schema(conn)

        columns = {row[1] for row in conn.execute("PRAGMA table_info('users')").fetchall()}
        assert {"legacy_role", "account_status", "is_global_admin", "oidc_subject"} <= columns
        assert conn.execute("SELECT is_global_admin FROM users WHERE id='admin'").fetchone()[0] is True
        assert conn.execute("SELECT count(*) FROM project_memberships WHERE user_id='editor' AND role='power'").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM project_memberships WHERE user_id='viewer-a' AND role='general'").fetchone()[0] == 2
        assert conn.execute("SELECT owner_user_id FROM analysis_requests WHERE id='unique-owner'").fetchone()[0] == "editor"
        assert conn.execute("SELECT owner_user_id FROM request_steps WHERE id='ambiguous-owner'").fetchone()[0] is None
        assert conn.execute("SELECT owner_user_id FROM request_work_items WHERE id='username-owner'").fetchone()[0] == "viewer-a"
        assert conn.execute("SELECT version FROM menu_policy_state WHERE id='global'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM menu_definitions").fetchone()[0] == 16
        assert conn.execute("SELECT count(*) FROM role_menu_policies").fetchone()[0] == 48

        # The one-time data conversion must not re-promote or auto-grant
        # accounts and resources created after the 0007 marker.
        conn.execute("UPDATE users SET is_global_admin=false WHERE id='admin'")
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, is_global_admin, is_active, created_at, updated_at) VALUES ('late-viewer', 'late-viewer', NULL, 'Late Viewer', 'viewer', 'ACTIVE', false, true, ?, ?)",
            [now, now],
        )
        conn.execute("INSERT INTO projects VALUES ('project-late')")
        conn.execute("INSERT INTO analysis_requests (id, owner) VALUES ('late-owner', 'Late Viewer')")
        ensure_access_control_schema(conn)
        assert conn.execute("SELECT is_global_admin FROM users WHERE id='admin'").fetchone()[0] is False
        assert conn.execute("SELECT count(*) FROM project_memberships WHERE user_id='late-viewer'").fetchone()[0] == 0
        assert conn.execute("SELECT owner_user_id FROM analysis_requests WHERE id='late-owner'").fetchone()[0] is None
        with pytest.raises(duckdb.ConstraintException):
            conn.execute("UPDATE users SET account_status='BOGUS' WHERE id='late-viewer'")
        with pytest.raises(duckdb.ConstraintException):
            conn.execute("UPDATE users SET account_status=NULL WHERE id='late-viewer'")
