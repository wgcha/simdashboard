from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app


def _member(*, status: str = "ACTIVE", membership: bool = True) -> tuple[str, str]:
    suffix = uuid4().hex[:10]
    user_id = f"assignee-{suffix}"
    display_name = f"Assignee {suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, account_status,
                 is_global_admin, is_active, created_at, updated_at)
            VALUES (?, ?, NULL, ?, NULL, ?, false, true, ?, ?)
            """,
            [user_id, user_id, display_name, status, now, now],
        )
        if membership:
            conn.execute(
                """
                INSERT INTO project_memberships
                    (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
                VALUES (?, 'project-tv-001', ?, 'general', 'test', ?, 'test', ?)
                """,
                [f"membership-{suffix}", user_id, now, now],
            )
    return user_id, display_name


def _request_payload(owner_user_id: str) -> dict[str, object]:
    return {
        "title": f"Canonical owner {uuid4().hex[:8]}",
        "owner_user_id": owner_user_id,
        "owner": "forged display name",
        "due_in_days": 7,
        "overall_note": "assignment policy test",
        "source_type": "DEPARTMENT_HEAD",
        "source_reference": "directory-test",
        "requested_by": "forged actor",
        "assigned_by": "forged actor",
        "request_type_id": "design-reliability-validation",
        "request_type_version": 1,
    }


def test_request_owner_is_canonical_reassignment_is_audited_and_global_override_is_explicit():
    initialize_database()
    first_id, first_name = _member()
    second_id, second_name = _member()
    with TestClient(app) as client:
        created = client.post(
            "/api/projects/project-tv-001/requests",
            json=_request_payload(first_id),
        )
        assert created.status_code == 201, created.text
        assert created.json()["owner_user_id"] == first_id
        assert created.json()["owner"] == first_name
        assert created.json()["requested_by"] == "로컬 관리자"

        workflow = client.get(f"/api/requests/{created.json()['id']}/workflow").json()
        assert {item["owner_user_id"] for item in workflow["steps"]} == {first_id}
        first_item = workflow["steps"][0]

        request_reassigned = client.patch(
            f"/api/requests/{created.json()['id']}/assignee",
            json={"owner_user_id": second_id},
        )
        assert request_reassigned.status_code == 200, request_reassigned.text
        assert request_reassigned.json()["owner_user_id"] == second_id
        assert request_reassigned.json()["owner"] == second_name

        reassigned = client.patch(
            f"/api/workbench/work-items/{first_item['id']}/assignee",
            json={"owner_user_id": second_id},
        )
        assert reassigned.status_code == 200, reassigned.text
        assert reassigned.json()["owner_user_id"] == second_id
        assert reassigned.json()["owner"] == second_name

        started = client.post(
            f"/api/workbench/work-items/{first_item['id']}/start",
            json={"started_by": "forged executor"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["steps"][0]["started_by"] == "로컬 관리자"

    with connect() as conn:
        actions = [
            row[0]
            for row in conn.execute(
                """
                SELECT action FROM audit_events
                WHERE action IN ('ANALYSIS_REQUEST_CREATED', 'REQUEST_ASSIGNEE_CHANGED', 'WORK_ITEM_ASSIGNEE_CHANGED', 'WORK_EXECUTION_OVERRIDE')
                ORDER BY occurred_at
                """
            ).fetchall()
        ]
        assert "ANALYSIS_REQUEST_CREATED" in actions
        assert "REQUEST_ASSIGNEE_CHANGED" in actions
        assert "WORK_ITEM_ASSIGNEE_CHANGED" in actions
        assert "WORK_EXECUTION_OVERRIDE" in actions


def test_only_active_project_members_can_be_assigned_and_null_owner_blocks_everyone():
    initialize_database()
    inactive_id, _ = _member(status="SUSPENDED")
    nonmember_id, _ = _member(membership=False)
    with TestClient(app) as client:
        inactive = client.post(
            "/api/projects/project-tv-001/requests",
            json=_request_payload(inactive_id),
        )
        assert inactive.status_code == 422
        assert inactive.json()["detail"]["code"] == "ASSIGNEE_ACCOUNT_NOT_ACTIVE"

        nonmember = client.post(
            "/api/projects/project-tv-001/requests",
            json=_request_payload(nonmember_id),
        )
        assert nonmember.status_code == 422
        assert nonmember.json()["detail"]["code"] == "ASSIGNEE_PROJECT_MEMBERSHIP_REQUIRED"

        created = client.post(
            "/api/projects/project-tv-001/requests",
            json=_request_payload("local-admin"),
        )
        item_id = client.get(f"/api/requests/{created.json()['id']}/workflow").json()["steps"][0]["id"]
        with connect() as conn:
            conn.execute("UPDATE request_work_items SET owner_user_id=NULL WHERE id=?", [item_id])
        blocked = client.post(
            f"/api/workbench/work-items/{item_id}/start",
            json={"started_by": "forged executor"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "OWNER_REASSIGNMENT_REQUIRED"
