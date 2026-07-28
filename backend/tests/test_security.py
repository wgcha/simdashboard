from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.config import database_settings
from app.main import app
from app.security import hash_password, minimum_role, verify_password


def _insert_user(username: str, role: str, password: str) -> str:
    user_id = f"user-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, true, ?, ?)",
            [user_id, username, hash_password(password), username.title(), role, now, now],
        )
    return user_id


def test_password_hash_and_role_policy():
    encoded = hash_password("a-strong-password")
    assert verify_password("a-strong-password", encoded)
    assert not verify_password("wrong-password", encoded)
    assert minimum_role("GET", "/api/projects") == "viewer"
    assert minimum_role("PUT", "/api/dashboards/example") == "editor"
    assert minimum_role("PUT", "/api/quality-thresholds/key") == "admin"
    assert minimum_role("DELETE", "/api/report-layouts/example") == "admin"


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
            assert client.get("/assets/sample-contour.svg").status_code == 200
            assert client.post("/api/dashboard-commands/preview", headers=viewer_headers, json={"command": "KPI 추가"}).status_code == 403

            editor_login = client.post("/api/auth/login", json={"username": f"editor-{suffix}", "password": password}).json()
            editor_headers = {"Authorization": f"Bearer {editor_login['access_token']}"}
            assert client.post("/api/dashboard-commands/preview", headers=editor_headers, json={"command": "KPI 추가"}).status_code == 200
            assert client.delete("/api/report-layouts/not-found", headers=editor_headers).status_code == 403

            admin_login = client.post("/api/auth/login", json={"username": f"admin-{suffix}", "password": password}).json()
            admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
            assert client.get("/api/auth/me").status_code == 200  # HttpOnly login cookie
            me = client.get("/api/auth/me", headers=admin_headers)
            assert me.status_code == 200 and me.json()["role"] == "admin"
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
