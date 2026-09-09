from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password


def test_disabled_auth_is_setup_required_without_explicit_local_escape(monkeypatch):
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "local")
    monkeypatch.delenv("AUTH_ALLOW_INSECURE_LOCAL", raising=False)
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["setup_required"] is True
        assert status["registration_enabled"] is False
        assert client.get("/api/projects").status_code == 503


def test_password_auth_needs_secret_and_active_password_admin(monkeypatch):
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.delenv("AUTH_SECRET_KEY", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/auth/status").json()["setup_reason"] == "AUTH_SECRET_REQUIRED"
        assert client.post("/api/auth/register", json={"username": "member.01", "display_name": "Member", "password": "long-enough-password"}).status_code == 503

    monkeypatch.setenv("AUTH_SECRET_KEY", "bootstrap-test-secret-key-that-is-at-least-32")
    suffix = uuid4().hex[:10]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active,
               created_at, updated_at, account_status, is_global_admin)
               VALUES (?, ?, ?, ?, 'admin', true, ?, ?, 'ACTIVE', true)""",
            [f"admin-{suffix}", f"admin-{suffix}", hash_password("bootstrap-admin-password"), "Admin", now, now],
        )
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["setup_required"] is False
        assert status["registration_enabled"] is True


def test_registration_username_accepts_requested_ascii_symbols(monkeypatch):
    # Schema validation is exercised through the public route after bootstrap.
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "username-test-secret-key-that-is-at-least-32")
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active, created_at, updated_at, account_status, is_global_admin) VALUES (?, ?, ?, ?, 'admin', true, ?, ?, 'ACTIVE', true)", [f"admin-{suffix}", f"admin-{suffix}", hash_password("username-admin-password"), "Admin", now, now])
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json={"username": "a._-9", "display_name": "Member", "password": "long-enough-password"}).status_code == 201
        assert client.post("/api/auth/register", json={"username": "bad/name", "display_name": "Member", "password": "long-enough-password"}).status_code == 422
