from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.database import connect, initialize_database
from app.main import app
from app.repositories.workbench import WorkbenchRepository
from app.security import hash_password


def _page(page_id: str, widget_id: str) -> dict:
    return {
        "id": page_id,
        "name": "프로젝트 결과",
        "description": "프로젝트 결과 프로필 검증",
        "widgets": [{"id": widget_id, "type": "summary", "title": "결과 요약", "x": 0, "y": 0, "w": 12, "h": 3, "settings": {}}],
    }


def _request_type(client: TestClient, suffix: str) -> dict:
    response = client.post("/api/admin/workbench/request-types", json={
        "display_name": f"Project result profile {suffix}",
        "description": "project result profile contract",
        "allowed_task_types": [{"id": "analysis-db-publish", "version": 1}],
        "default_workflow": {"nodes": [{"node_key": "publish", "task_type_id": "analysis-db-publish", "task_type_version": 1, "depends_on": []}]},
        "match_rules": {"labels": ["project-result-profile"]},
        "is_active": True,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_project_result_profile_binding_is_scoped_and_snapshotted() -> None:
    suffix = uuid4().hex[:8]
    project_id = "project-tv-001"
    other_project_id = f"project-profile-other-{suffix}"
    template_id = f"project-result-template-{suffix}"
    widget_id = f"project-result-widget-{suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with connect() as conn:
        conn.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?)", [other_project_id, "Other project", "Other product", "scope test", now])

    with TestClient(app) as client:
        request_type = _request_type(client, suffix)
        template = client.post("/api/admin/workbench/analysis-templates", json={
            "id": template_id,
            "display_name": "프로젝트 결과 템플릿",
            "description": "same-project template",
            "page_definitions": [_page(f"page-{suffix}", widget_id)],
            "lifecycle_status": "PUBLISHED",
            "scope_kind": "PROJECT",
            "project_id": project_id,
        })
        assert template.status_code == 201, template.text

        bound = client.put(
            f"/api/projects/{project_id}/admin/workbench/request-types/{request_type['id']}/1/result-profile",
            json={"template_id": template_id, "template_version": 1, "included_widget_ids": [widget_id], "overrides": {}, "required_data_contracts": []},
        )
        assert bound.status_code == 201, bound.text
        assert bound.json()["profile_scope"] == "PROJECT_OVERRIDE"
        assert bound.json()["project_id"] == project_id

        preview = client.get(f"/api/workbench/request-types/{request_type['id']}/1/result-profile?project_id={project_id}")
        assert preview.status_code == 200, preview.text
        assert preview.json()["template_id"] == template_id
        assert preview.json()["profile_scope"] == "PROJECT_OVERRIDE"

        created = client.post(f"/api/projects/{project_id}/requests", json={
            "title": f"Project profile request {suffix}", "owner_user_id": "local-admin", "due_in_days": 7,
            "overall_note": "project profile snapshot", "source_type": "EXTERNAL_SYSTEM", "source_reference": "test",
            "requested_by": "test", "request_type_id": request_type["id"], "request_type_version": 1,
        })
        assert created.status_code == 201, created.text
        layout = client.get(f"/api/workbench/requests/{created.json()['id']}/result-layout")
        assert layout.status_code == 200, layout.text
        assert layout.json()["snapshot"]["profile_scope"] == "PROJECT_OVERRIDE"
        assert layout.json()["snapshot"]["profile_project_id"] == project_id
        assert layout.json()["snapshot"]["template_id"] == template_id
        assert layout.json()["snapshot"]["profile_binding_version"] == 1

        rebound = client.put(
            f"/api/projects/{project_id}/admin/workbench/request-types/{request_type['id']}/1/result-profile",
            json={"template_id": template_id, "template_version": 1, "included_widget_ids": None, "overrides": {"presentation": "expanded"}, "required_data_contracts": []},
        )
        assert rebound.status_code == 201, rebound.text
        assert rebound.json()["binding_version"] == 2
        assert client.get(f"/api/workbench/requests/{created.json()['id']}/result-layout").json()["snapshot"]["profile_binding_version"] == 1
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM project_request_type_result_profiles WHERE project_id=? AND request_type_id=?", [project_id, request_type["id"]]).fetchone()[0] == 2

        rejected = client.put(
            f"/api/projects/{other_project_id}/admin/workbench/request-types/{request_type['id']}/1/result-profile",
            json={"template_id": template_id, "template_version": 1, "included_widget_ids": [widget_id], "overrides": {}, "required_data_contracts": []},
        )
        assert rejected.status_code == 403
        assert rejected.json()["detail"]["code"] == "PROJECT_RESULT_TEMPLATE_SCOPE_MISMATCH"

    with connect() as conn:
        repository = WorkbenchRepository(conn)
        assert repository.project_result_profile_for(other_project_id, request_type["id"], 1) is None
        assert repository.resolved_result_profile_for(project_id, request_type["id"], 1)["template_id"] == template_id


def test_project_admin_can_bind_only_own_project_and_viewer_cannot_write(monkeypatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    project_id = "project-tv-001"
    other_project_id = f"project-profile-auth-other-{suffix}"
    password = "project-result-profile-password"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    admin_id = f"project-result-admin-{suffix}"
    viewer_id = f"project-result-viewer-{suffix}"
    with connect() as conn:
        conn.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?)", [other_project_id, "Other project", "Other product", "auth scope", now])
        for user_id, username, role in ((admin_id, f"project-result-admin-{suffix}", "admin"), (viewer_id, f"project-result-viewer-{suffix}", "general")):
            conn.execute("""INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, is_global_admin, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 'viewer', 'ACTIVE', false, true, ?, ?)""", [user_id, username, hash_password(password), username, now, now])
            conn.execute("""INSERT INTO project_memberships (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at) VALUES (?, ?, ?, ?, 'test', ?, 'test', ?)""", [f"membership-{user_id}", project_id, user_id, role, now, now])
        repository = WorkbenchRepository(conn)
        request_type = repository.create_request_type_version({"display_name": f"Project auth type {suffix}", "description": "auth scope", "allowed_task_types": [{"id": "analysis-db-publish", "version": 1}], "default_workflow": {"nodes": [{"node_key": "publish", "task_type_id": "analysis-db-publish", "task_type_version": 1, "depends_on": []}]}, "match_rules": {"labels": ["project-result-auth"]}, "is_active": True})
        template_id = f"project-auth-template-{suffix}"
        widget_id = f"project-auth-widget-{suffix}"
        repository.create_analysis_template_version({"id": template_id, "scope_kind": "PROJECT", "project_id": project_id, "display_name": "프로젝트 권한 결과", "description": "auth scope", "lifecycle_status": "PUBLISHED", "page_definitions": [_page(f"page-auth-{suffix}", widget_id)]}, "test")
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    with TestClient(app) as client:
        def headers(username: str) -> dict[str, str]:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            assert login.status_code == 200, login.text
            return {"Authorization": f"Bearer {login.json()['access_token']}"}
        admin_headers = headers(f"project-result-admin-{suffix}")
        viewer_headers = headers(f"project-result-viewer-{suffix}")
        payload = {"template_id": template_id, "template_version": 1, "included_widget_ids": [widget_id], "overrides": {}, "required_data_contracts": []}
        own = client.put(f"/api/projects/{project_id}/admin/workbench/request-types/{request_type['id']}/1/result-profile", headers=admin_headers, json=payload)
        assert own.status_code == 201, own.text
        other = client.put(f"/api/projects/{other_project_id}/admin/workbench/request-types/{request_type['id']}/1/result-profile", headers=admin_headers, json=payload)
        assert other.status_code == 403
        read_own = client.get(f"/api/workbench/request-types/{request_type['id']}/1/result-profile?project_id={project_id}", headers=viewer_headers)
        assert read_own.status_code == 200, read_own.text
        viewer_write = client.put(f"/api/projects/{project_id}/admin/workbench/request-types/{request_type['id']}/1/result-profile", headers=viewer_headers, json=payload)
        assert viewer_write.status_code == 403


def test_project_request_list_requires_project_data_view_membership(monkeypatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    own_project_id = "project-tv-001"
    other_project_id = f"project-request-list-other-{suffix}"
    password = "project-request-list-password"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    viewer_id = f"project-request-list-viewer-{suffix}"
    admin_id = f"project-request-list-admin-{suffix}"
    viewer_username = f"project-request-list-viewer-{suffix}"
    admin_username = f"project-request-list-admin-{suffix}"
    with connect() as conn:
        conn.execute("INSERT INTO projects VALUES (?, ?, ?, ?, ?)", [other_project_id, "Other project", "Other product", "authorization scope", now])
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, is_global_admin, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [viewer_id, viewer_username, hash_password(password), viewer_username, "viewer", "ACTIVE", False, True, now, now],
        )
        conn.execute(
            """INSERT INTO project_memberships (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [f"membership-{viewer_id}", own_project_id, viewer_id, "general", "test", now, "test", now],
        )
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name, legacy_role, account_status, is_global_admin, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [admin_id, admin_username, hash_password(password), admin_username, "admin", "ACTIVE", True, True, now, now],
        )
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    with TestClient(app) as client:
        def headers(username: str) -> dict[str, str]:
            login = client.post("/api/auth/login", json={"username": username, "password": password})
            assert login.status_code == 200, login.text
            return {"Authorization": f"Bearer {login.json()["access_token"]}"}

        viewer_headers = headers(viewer_username)
        own_project = client.get(f"/api/projects/{own_project_id}/requests", headers=viewer_headers)
        assert own_project.status_code == 200, own_project.text
        other_project = client.get(f"/api/projects/{other_project_id}/requests", headers=viewer_headers)
        assert other_project.status_code == 403
        assert other_project.json()["detail"]["code"] == "PROJECT_MEMBERSHIP_REQUIRED"
        admin_project = client.get(f"/api/projects/{other_project_id}/requests", headers=headers(admin_username))
        assert admin_project.status_code == 200, admin_project.text


def test_analysis_template_family_scope_and_project_are_immutable() -> None:
    suffix = uuid4().hex[:8]
    project_id = "project-tv-001"
    template_id = "template-family-" + suffix
    payload = {
        "id": template_id, "scope_kind": "PROJECT", "project_id": project_id,
        "display_name": "Immutable template family", "description": "scope invariant",
        "lifecycle_status": "PUBLISHED", "page_definitions": [_page("template-family-page-" + suffix, "template-family-widget-" + suffix)],
    }
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        repository.create_analysis_template_version(payload, "test")
        with pytest.raises(ValueError, match="ANALYSIS_TEMPLATE_FAMILY_SCOPE_IMMUTABLE"):
            repository.create_analysis_template_version({**payload, "scope_kind": "SYSTEM", "project_id": None}, "test")
        with pytest.raises(ValueError, match="ANALYSIS_TEMPLATE_SCOPE_PROJECT_MISMATCH"):
            repository.create_analysis_template_version({**payload, "id": "template-no-project-" + suffix, "project_id": None}, "test")
