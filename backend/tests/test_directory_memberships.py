from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.config import directory_settings
from app.main import app
from app.security import hash_password
from app.services.directory_service import DirectoryEmployee, employee_directory, set_employee_directory_for_tests


class FakeDirectory:
    def __init__(self, items: list[DirectoryEmployee]):
        self.items = items

    def search(self, query: str, limit: int) -> list[DirectoryEmployee]:
        normalized = query.casefold()
        return [
            item
            for item in self.items
            if normalized in item.employee_id.casefold() or normalized in item.display_name.casefold()
        ][:limit]

    def get_by_employee_id(self, employee_id: str) -> DirectoryEmployee | None:
        return next((item for item in self.items if item.employee_id == employee_id), None)


def _insert_user(
    *,
    username: str,
    display_name: str,
    employee_id: str | None,
    password: str | None = None,
) -> tuple[str, datetime]:
    user_id = f"user-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, employee_id, legacy_role,
                 account_status, is_global_admin, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'viewer', 'ACTIVE', false, true, ?, ?)
            """,
            [user_id, username, hash_password(password) if password else None, display_name, employee_id, now, now],
        )
    return user_id, now


def test_directory_invitation_membership_and_assignee_candidates():
    initialize_database()
    employee = DirectoryEmployee(
        employee_id="E90210",
        display_name="초대 대상",
        department="해석팀",
        job_title="연구원",
        email="invitee@example.test",
        employment_status="ACTIVE",
    )
    inactive = DirectoryEmployee(
        employee_id="E00000",
        display_name="퇴직 대상",
        employment_status="INACTIVE",
    )
    set_employee_directory_for_tests(FakeDirectory([employee, inactive]))
    target_id, _ = _insert_user(username="invite-target", display_name=employee.display_name, employee_id=employee.employee_id)
    try:
        with TestClient(app) as client:
            too_short = client.get("/api/projects/project-tv-001/directory/employees", params={"q": "E"})
            assert too_short.status_code == 422
            searched = client.get("/api/projects/project-tv-001/directory/employees", params={"q": "E9", "limit": 50})
            assert searched.status_code == 200
            assert searched.json()[0]["employee_id"] == employee.employee_id

            invitation = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": employee.employee_id, "desired_role": "power"},
            )
            assert invitation.status_code == 201
            assert invitation.json()["status"] == "READY"

            duplicate = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": employee.employee_id, "desired_role": "power"},
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["detail"]["code"] == "INVITATION_ALREADY_EXISTS"

            completed = client.post(
                f"/api/projects/project-tv-001/invitations/{invitation.json()['id']}/complete"
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "COMPLETED"

            replayed = client.post(
                f"/api/projects/project-tv-001/invitations/{invitation.json()['id']}/complete"
            )
            assert replayed.status_code == 409
            assert replayed.json()["detail"]["code"] == "INVITATION_ALREADY_FINAL"

            candidates = client.get("/api/projects/project-tv-001/assignee-candidates", params={"q": "초대"})
            assert candidates.status_code == 200
            assert candidates.json()[0]["user_id"] == target_id

            inactive_invite = client.post(
                "/api/projects/project-tv-001/invitations",
                json={"employee_id": inactive.employee_id, "desired_role": "general"},
            )
            assert inactive_invite.status_code == 422
            assert inactive_invite.json()["detail"]["code"] == "INACTIVE_EMPLOYEE"
    finally:
        set_employee_directory_for_tests(None)


def test_project_admin_scope_and_last_admin_protection(monkeypatch):
    initialize_database()
    suffix = uuid4().hex[:8]
    password = "project-admin-password"
    admin_id, now = _insert_user(
        username=f"project-admin-{suffix}",
        display_name="프로젝트 관리자",
        employee_id=f"EA{suffix}",
        password=password,
    )
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, product_name, description, created_at) VALUES (?, ?, ?, ?, ?)",
            [f"project-other-{suffix}", "다른 프로젝트", "다른 제품", "범위 검증", now],
        )
        conn.execute(
            """
            INSERT INTO project_memberships
                (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
            VALUES (?, 'project-tv-001', ?, 'admin', 'test', ?, 'test', ?)
            """,
            [f"membership-{admin_id}", admin_id, now, now],
        )
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": f"project-admin-{suffix}", "password": password},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        demote = client.patch(
            f"/api/projects/project-tv-001/members/{admin_id}",
            headers=headers,
            json={"role": "power"},
        )
        assert demote.status_code == 409
        assert demote.json()["detail"]["code"] == "LAST_PROJECT_ADMIN_PROTECTED"
        other_project = client.get(f"/api/projects/project-other-{suffix}/members", headers=headers)
        assert other_project.status_code == 403


def test_http_directory_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("DIRECTORY_MODE", "http")
    monkeypatch.delenv("DIRECTORY_API_BASE_URL", raising=False)
    monkeypatch.delenv("DIRECTORY_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="DIRECTORY_API_BASE_URL"):
        directory_settings()

    monkeypatch.setenv("DIRECTORY_API_BASE_URL", "http://directory.example.test")
    monkeypatch.setenv("DIRECTORY_API_TOKEN", "secret")
    with pytest.raises(RuntimeError, match="HTTPS"):
        directory_settings()


def test_local_directory_returns_active_users_only(monkeypatch):
    initialize_database()
    monkeypatch.setenv("DIRECTORY_MODE", "local")
    suffix = uuid4().hex[:8]
    active_id, _ = _insert_user(
        username=f"local-active-{suffix}",
        display_name=f"Local Active {suffix}",
        employee_id=f"LA{suffix}",
    )
    suspended_id, _ = _insert_user(
        username=f"local-suspended-{suffix}",
        display_name=f"Local Suspended {suffix}",
        employee_id=f"LS{suffix}",
    )
    try:
        with connect() as conn:
            conn.execute("UPDATE users SET account_status='SUSPENDED' WHERE id=?", [suspended_id])
        results = employee_directory().search("Local", 20)
        assert [item.employee_id for item in results if suffix in item.display_name] == [f"LA{suffix}"]
        inactive = employee_directory().get_by_employee_id(f"LS{suffix}")
        assert inactive and inactive.employment_status == "INACTIVE"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM project_memberships WHERE user_id IN (?, ?)", [active_id, suspended_id])
            conn.execute("DELETE FROM users WHERE id IN (?, ?)", [active_id, suspended_id])
