from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services import auth_accounts


def _password_env(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "password-account-test-secret-key-at-least-32-characters")


def _oidc_env(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("AUTH_SECRET_KEY", "oidc-account-test-secret-key-at-least-32-characters")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("OIDC_ALLOW_INSECURE_LOCALHOST", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", "http://localhost:9000")
    monkeypatch.setenv("OIDC_CLIENT_ID", "analysis-canvas")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8000/api/auth/oidc/callback")


def test_password_registration_requires_admin_approval_and_supports_password_change(monkeypatch):
    initialize_database()
    _password_env(monkeypatch)
    suffix = uuid4().hex[:12]
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active,
               created_at, updated_at, account_status, is_global_admin)
               VALUES (?, ?, ?, ?, 'admin', true, ?, ?, 'ACTIVE', true)""",
            [f"bootstrap-{suffix}", f"bootstrap-{suffix}", hash_password("bootstrap-admin-password"), "Bootstrap", now, now],
        )
    username = f"member-{suffix}".ljust(80, 'a')
    initial_password = " initial password is long enough "
    replacement_password = "new12345"

    with TestClient(app) as client:
        assert client.get("/api/auth/status").json()["registration_enabled"] is True
        created = client.post(
            "/api/auth/register",
            json={"username": f"  {username.upper()}  ", "display_name": "  개인 사용자  ", "password": initial_password},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["username"] == username
        assert body["account_status"] == "PENDING"
        user_id = body["user_id"]

        # New accounts cannot sign in until an administrator activates them.
        assert client.post("/api/auth/login", json={"username": username, "password": initial_password}).status_code == 401
        assert client.post(
            "/api/auth/register",
            json={"username": username, "display_name": "중복", "password": initial_password},
        ).status_code == 409
        assert client.post(
            "/api/auth/register",
            json={
                "username": username,
                "display_name": "권한 주입",
                "password": initial_password,
                "account_status": "ACTIVE",
            },
        ).status_code == 422
        assert client.post(
            "/api/auth/register",
            json={"username": f"한글-{suffix}", "display_name": "비 ASCII", "password": initial_password},
        ).status_code == 422
        assert client.post(
            "/api/auth/register",
            json={"username": username + 'b', "display_name": "길이 초과", "password": initial_password},
        ).status_code == 422
        invalid_secret = "shortpw"
        invalid = client.post("/api/auth/register", json={
            "username": "invalid-password-user", "display_name": "잘못된 입력", "password": invalid_secret,
        })
        assert invalid.status_code == 422
        assert invalid_secret not in invalid.text
        oversized_secret = "private-login-marker-" * 20
        invalid_login = client.post("/api/auth/login", json={"username": username, "password": oversized_secret})
        assert invalid_login.status_code == 422
        assert "private-login-marker" not in invalid_login.text

        with connect() as conn:
            user = conn.execute(
                "SELECT password_hash, account_status, is_global_admin, legacy_role FROM users WHERE id=?", [user_id]
            ).fetchone()
            assert user and user[0] != initial_password and user[1:] == ("PENDING", False, None)
            audit_rows = conn.execute("SELECT detail_json FROM audit_events WHERE user_id=?", [user_id]).fetchall()
            assert initial_password not in json.dumps(audit_rows, ensure_ascii=False)
            conn.execute(
                "UPDATE users SET account_status='ACTIVE', is_active=true WHERE id=?",
                [user_id],
            )

        login = client.post("/api/auth/login", json={"username": username, "password": initial_password})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.post(
            "/api/auth/password",
            headers=headers,
            json={"current_password": "incorrect-current-password", "new_password": replacement_password},
        ).status_code == 400
        assert client.post(
            "/api/auth/password",
            headers=headers,
            json={"current_password": initial_password, "new_password": replacement_password},
        ).json() == {"ok": True}
        # A stale current password cannot overwrite the already changed hash.
        assert client.post(
            "/api/auth/password",
            headers=headers,
            json={"current_password": initial_password, "new_password": "another replacement password"},
        ).status_code == 400
        assert client.post("/api/auth/login", json={"username": username, "password": initial_password}).status_code == 401
        assert client.post("/api/auth/login", json={"username": username, "password": replacement_password}).status_code == 200


def test_registration_is_unavailable_outside_password_auth_mode(monkeypatch):
    initialize_database()
    payload = {"username": f"mode-{uuid4().hex[:12]}", "display_name": "모드 사용자", "password": "mode-password-is-long-enough"}
    monkeypatch.setenv("AUTH_MODE", "disabled")
    with TestClient(app) as client:
        assert client.get("/api/auth/status").json()["registration_enabled"] is False
        assert client.post("/api/auth/register", json=payload).status_code == 409

    _oidc_env(monkeypatch)
    with TestClient(app) as client:
        assert client.get("/api/auth/status").json()["registration_enabled"] is False
        assert client.post("/api/auth/register", json=payload).status_code == 409


@pytest.mark.unit
def test_registration_rate_limiter_prunes_stale_entries_and_bounds_tracked_clients(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(auth_accounts, "monotonic", lambda: clock[0])
    limiter = auth_accounts.RegistrationAttemptLimiter(limit=1, window_seconds=60, max_keys=2)

    assert limiter.allow("stale-client")
    clock[0] = 161.0
    assert limiter.allow("fresh-client")
    assert "stale-client" not in limiter._attempts

    assert limiter.allow("second-client")
    assert limiter.allow("third-client")
    assert len(limiter._attempts) == 2
    assert "fresh-client" not in limiter._attempts
