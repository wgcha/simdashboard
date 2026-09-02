from __future__ import annotations

import duckdb

from scripts.access_migration_preflight import build_report


def _preflight_database() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE users (
            id VARCHAR, username VARCHAR, employee_id VARCHAR,
            oidc_issuer VARCHAR, oidc_subject VARCHAR,
            account_status VARCHAR, is_global_admin BOOLEAN
        );
        CREATE TABLE projects (id VARCHAR, name VARCHAR);
        CREATE TABLE analysis_requests (id VARCHAR, owner VARCHAR, owner_user_id VARCHAR);
        CREATE TABLE request_steps (id VARCHAR, owner VARCHAR, owner_user_id VARCHAR);
        CREATE TABLE request_work_items (id VARCHAR, owner VARCHAR, owner_user_id VARCHAR);
        CREATE TABLE project_memberships (project_id VARCHAR, user_id VARCHAR, role VARCHAR);
        CREATE TABLE project_invitations (id VARCHAR);
        CREATE TABLE menu_definitions (id VARCHAR);
        CREATE TABLE menu_policy_state (id VARCHAR, version INTEGER);
        CREATE TABLE role_menu_policies (menu_id VARCHAR);
        CREATE TABLE menu_policy_versions (version INTEGER);
        INSERT INTO users VALUES ('user-1', 'admin', 'E001', 'https://issuer', 'subject-1', 'ACTIVE', true);
        INSERT INTO projects VALUES ('project-1', 'Project One');
        INSERT INTO project_memberships VALUES ('project-1', 'user-1', 'admin');
        INSERT INTO menu_policy_state VALUES ('global', 1);
        INSERT INTO menu_policy_versions VALUES (1);
        """
    )
    return connection


def test_access_migration_preflight_reports_ready_database() -> None:
    connection = _preflight_database()
    try:
        report = build_report(connection, "duckdb")
    finally:
        connection.close()

    assert report["status"] == "ready"
    assert report["counts"]["active_global_admins"] == 1
    assert report["menu_policy_version"] == 1
    assert report["blockers"] == []


def test_access_migration_preflight_blocks_identity_conflict_and_warns_unresolved_owner() -> None:
    connection = _preflight_database()
    connection.execute(
        """
        INSERT INTO users VALUES ('user-2', 'other', 'E001', 'https://issuer', 'subject-2', 'ACTIVE', false);
        INSERT INTO analysis_requests VALUES ('request-1', '동명이인', NULL);
        """
    )
    try:
        report = build_report(connection, "duckdb")
    finally:
        connection.close()

    assert report["status"] == "blocked"
    assert any(item["code"] == "IDENTITY_CONFLICTS" for item in report["blockers"])
    assert report["unresolved_owners"]["analysis_requests"] == 1
    assert any(item["code"] == "OWNER_REASSIGNMENT_REQUIRED" for item in report["warnings"])
