from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.config import database_settings
from app.main import app
from app.security import hash_password, verify_password


def _insert_user(username: str, role: str, password: str) -> str:
    user_id = f"user-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, ?, true, ?, ?, 'ACTIVE', ?)
            """,
            [user_id, username, hash_password(password), username.title(), role, now, now, role == "admin"],
        )
        if role in {"viewer", "editor"}:
            membership_role = "general" if role == "viewer" else "power"
            conn.execute(
                """
                INSERT INTO project_memberships
                    (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
                SELECT 'membership-' || substr(md5(id || ':' || ?), 1, 24),
                       id, ?, ?, 'test', ?, 'test', ? FROM projects
                """,
                [user_id, user_id, membership_role, now, now],
            )
    return user_id


def test_password_hash_policy():
    encoded = hash_password("a-strong-password")
    assert verify_password("a-strong-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_password_auth_rbac_and_audit(monkeypatch):
    initialize_database()
    suffix = uuid4().hex[:8]
    password = "correct-horse-battery-staple"
    viewer_id = _insert_user(f"viewer-{suffix}", "viewer", password)
    editor_id = _insert_user(f"editor-{suffix}", "editor", password)
    admin_id = _insert_user(f"admin-{suffix}", "admin", password)
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")

    try:
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/projects").status_code == 401
            assert client.get("/assets/sample-contour.svg").status_code == 401
            assert client.post("/api/auth/login", json={"username": f"viewer-{suffix}", "password": "wrong"}).status_code == 401

            viewer_login = client.post("/api/auth/login", json={"username": f"viewer-{suffix}", "password": password})
            assert viewer_login.status_code == 200
            viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
            assert client.get("/api/projects", headers=viewer_headers).status_code == 200
            assert client.get("/api/workbench/task-types", headers=viewer_headers).status_code == 200
            assert client.get("/assets/sample-contour.svg").status_code == 200
            assert client.post("/api/dashboard-commands/preview", headers=viewer_headers, json={"command": "KPI 추가"}).status_code == 403
            assert client.delete("/api/dashboards/dashboard-drop-default/versions/1", headers=viewer_headers).status_code == 403
            demo_payload = {
                "name": "권한 검증 데모",
                "execution_mode": "DEMO_ONLY",
                "created_by": "클라이언트 위조 이름",
                "nodes": [{"node_key": "cad", "task_type_id": "cad-prepare", "task_type_version": 1, "depends_on": []}],
            }
            assert client.post("/api/workbench/demo-runs", headers=viewer_headers, json=demo_payload).status_code == 403
            assignment_payload = {"request_type_id": "design-reliability-validation", "request_type_version": 1}
            assignment_request_id = "request-showcase-waiting"
            assert client.put(f"/api/workbench/requests/{assignment_request_id}/request-type", headers=viewer_headers, json=assignment_payload).status_code == 403
            current_work_item = next(
                item
                for item in client.get("/api/requests/request-drop-001/workflow", headers=viewer_headers).json()["steps"]
                if item["status"] == "IN_PROGRESS"
            )
            assert client.post(
                f"/api/workbench/work-items/{current_work_item['id']}/complete",
                headers=viewer_headers,
                json={"completed_by": "권한 없는 사용자"},
            ).status_code == 403

            editor_login = client.post("/api/auth/login", json={"username": f"editor-{suffix}", "password": password}).json()
            editor_headers = {"Authorization": f"Bearer {editor_login['access_token']}"}
            public_profiles = client.get("/api/workbench/batch-profiles", headers=editor_headers).json()
            assert public_profiles and public_profiles[0]["solver_path"] == ""
            assert public_profiles[0]["working_directory"] == ""
            assert public_profiles[0]["environment"] == {}
            not_assigned = client.patch(
                f"/api/workbench/work-items/{current_work_item['id']}/progress",
                headers=editor_headers,
                json={"progress": max(1, current_work_item["progress"] + 1), "updated_by": "위조 담당자"},
            )
            assert not_assigned.status_code == 403
            assert not_assigned.json()["detail"]["code"] == "WORK_ITEM_NOT_ASSIGNED"
            assert client.post("/api/dashboard-commands/preview", headers=editor_headers, json={"command": "KPI 추가"}).status_code == 403
            demo_run = client.post("/api/workbench/demo-runs", headers=editor_headers, json=demo_payload)
            assert demo_run.status_code == 201
            assert demo_run.json()["created_by"] == f"Editor-{suffix}".title()
            assigned = client.put(f"/api/workbench/requests/{assignment_request_id}/request-type", headers=editor_headers, json=assignment_payload)
            assert assigned.status_code == 200
            assert assigned.json()["source"] == "USER"
            assert client.get("/api/admin/workbench/batch-profiles/altair-default/versions", headers=editor_headers).status_code == 403
            assert client.delete("/api/report-layouts/not-found", headers=editor_headers).status_code == 403
            assert client.delete("/api/dashboards/dashboard-drop-default/versions/1", headers=editor_headers).status_code == 403

            admin_login = client.post("/api/auth/login", json={"username": f"admin-{suffix}", "password": password}).json()
            admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
            admin_profiles = client.get("/api/workbench/batch-profiles", headers=admin_headers).json()
            assert admin_profiles and admin_profiles[0]["solver_path"]
            assert client.get("/api/auth/me").status_code == 200  # HttpOnly login cookie
            me = client.get("/api/auth/me", headers=admin_headers)
            assert me.status_code == 200 and me.json()["role"] == "admin"
            assert client.delete("/api/dashboards/dashboard-drop-default/versions/1", headers=admin_headers).status_code == 409
            events = client.get("/api/audit-events", headers=admin_headers)
            assert events.status_code == 200
            actions = {event["action"] for event in events.json()}
            assert {"LOGIN_FAILED", "LOGIN_SUCCEEDED", "AUTHENTICATION_DENIED", "AUTHORIZATION_DENIED", "API_MUTATION"} <= actions
            assert client.post("/api/auth/logout").status_code == 200
            assert client.get("/api/auth/me").status_code == 401
    finally:
        with connect() as conn:
            if database_settings().backend == "duckdb":
                conn.execute("DELETE FROM audit_events WHERE user_id IN (?, ?, ?) OR username LIKE ?", [viewer_id, editor_id, admin_id, f"%-{suffix}"])
            conn.execute("DELETE FROM users WHERE id IN (?, ?, ?)", [viewer_id, editor_id, admin_id])
