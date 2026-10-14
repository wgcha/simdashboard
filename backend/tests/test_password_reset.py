"""Administrator password reset with forced change, and the new-password policy (2026-10-14)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.schemas.auth_limits import password_policy_problem
from app.security import hash_password
from app.services.auth_accounts import temporary_password

ADMIN_PASSWORD = "Admin-pass-2026!"
USER_PASSWORD = "User-pass-2026!"


@pytest.fixture
def accounts(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "password-reset-test-secret-key-at-least-32-characters")
    initialize_database()
    suffix = uuid4().hex[:10]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ids = {"admin": f"admin-{suffix}", "user": f"user-{suffix}", "sso": f"sso-{suffix}"}
    with connect() as conn:
        for key, admin, password in (("admin", True, ADMIN_PASSWORD), ("user", False, USER_PASSWORD), ("sso", False, None)):
            conn.execute(
                """INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active,
                   created_at, updated_at, account_status, is_global_admin)
                   VALUES (?, ?, ?, ?, ?, true, ?, ?, 'ACTIVE', ?)""",
                [ids[key], ids[key], hash_password(password) if password else None, key,
                 "admin" if admin else None, now, now, admin])
    return ids


def _login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _updated_at(user_id: str) -> str:
    with connect() as conn:
        return conn.execute("SELECT updated_at FROM users WHERE id=?", [user_id]).fetchone()[0].isoformat()


def test_admin_reset_issues_a_one_time_password_and_forces_a_change(accounts, caplog):
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        admin = _login(client, accounts["admin"], ADMIN_PASSWORD)
        before_reset = _login(client, accounts["user"], USER_PASSWORD)
        assert client.get("/api/auth/me", headers=before_reset).status_code == 200
        listed = {row["id"]: row for row in client.get("/api/admin/users", headers=admin).json()}
        assert listed[accounts["user"]]["has_password"] is True and listed[accounts["sso"]]["has_password"] is False
        reset = client.post(f"/api/admin/users/{accounts['user']}/password-reset", headers=admin,
                            json={"expected_updated_at": _updated_at(accounts["user"]), "reason": "비밀번호 분실"})
        assert reset.status_code == 200, reset.text
        assert reset.headers.get("cache-control") == "no-store"
        temporary = reset.json()["temporary_password"]
        # Sessions signed in before the reset end at once (a stolen token included).
        assert client.get("/api/auth/me", headers=before_reset).status_code == 401
        assert password_policy_problem(temporary) is None
        # Only the hash is stored; the temporary password is never logged or audited.
        with connect() as conn:
            stored, flag = conn.execute("SELECT password_hash, must_change_password FROM users WHERE id=?",
                                        [accounts["user"]]).fetchone()
            audit = json.dumps([list(row) for row in conn.execute(
                "SELECT action, detail_json FROM audit_events WHERE action='PASSWORD_RESET_BY_ADMIN'").fetchall()],
                ensure_ascii=False, default=str)
        assert temporary not in stored and flag is True
        assert "PASSWORD_RESET_BY_ADMIN" in audit and temporary not in audit and temporary not in caplog.text

        # The old password no longer works; the temporary one logs in but only allows the change.
        # (Token ``iat`` is whole seconds: wait out the reset's own second, whose tokens are refused.)
        time.sleep(max(0.0, 1.05 - (time.time() % 1.0)))
        assert client.post("/api/auth/login", json={"username": accounts["user"], "password": USER_PASSWORD}).status_code == 401
        user = _login(client, accounts["user"], temporary)
        me = client.get("/api/auth/me", headers=user).json()
        assert me["must_change_password"] is True
        blocked = client.get("/api/projects", headers=user)
        assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
        weak = client.post("/api/auth/password", headers=user, json={"current_password": temporary, "new_password": "longpassword"})
        assert weak.status_code == 422 and weak.json()["detail"]["code"] == "PASSWORD_POLICY"
        same = client.post("/api/auth/password", headers=user, json={"current_password": temporary, "new_password": temporary})
        assert same.status_code == 422
        changed = client.post("/api/auth/password", headers=user,
                              json={"current_password": temporary, "new_password": "Brand-new-2026!"})
        assert changed.status_code == 200, changed.text
        assert client.get("/api/auth/me", headers=user).json()["must_change_password"] is False
        assert client.get("/api/projects", headers=user).status_code == 200


def test_reset_guards(accounts):
    with TestClient(app) as client:
        admin = _login(client, accounts["admin"], ADMIN_PASSWORD)
        user = _login(client, accounts["user"], USER_PASSWORD)
        body = {"expected_updated_at": _updated_at(accounts["user"]), "reason": "분실 신고"}
        assert client.post(f"/api/admin/users/{accounts['admin']}/password-reset", headers=user,
                           json={**body, "expected_updated_at": _updated_at(accounts["admin"])}).status_code == 403
        sso = client.post(f"/api/admin/users/{accounts['sso']}/password-reset", headers=admin,
                          json={**body, "expected_updated_at": _updated_at(accounts["sso"])})
        assert sso.status_code == 409 and sso.json()["detail"]["code"] == "NOT_PASSWORD_ACCOUNT"
        stale = client.post(f"/api/admin/users/{accounts['user']}/password-reset", headers=admin,
                            json={**body, "expected_updated_at": "2001-01-01T00:00:00"})
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "STALE_USER_VERSION"
        assert client.post("/api/admin/users/missing-user/password-reset", headers=admin, json=body).status_code == 404
        own = client.post(f"/api/admin/users/{accounts['admin']}/password-reset", headers=admin,
                          json={**body, "expected_updated_at": _updated_at(accounts["admin"])})
        assert own.status_code == 409 and own.json()["detail"]["code"] == "PASSWORD_RESET_SELF"
        with connect() as conn:
            conn.execute("UPDATE users SET account_status='PENDING' WHERE id=?", [accounts["user"]])
        pending = client.post(f"/api/admin/users/{accounts['user']}/password-reset", headers=admin,
                              json={**body, "expected_updated_at": _updated_at(accounts["user"])})
        assert pending.status_code == 409 and pending.json()["detail"]["code"] == "PASSWORD_RESET_PENDING"
        assert client.post(f"/api/admin/users/{accounts['user']}/password-reset", headers=admin,
                           json={**body, "reason": " "}).status_code == 422


@pytest.mark.parametrize("password,ok", [
    ("Abcdef1!", True), ("abc-12345", True), ("한글비밀번호입니다1!a", True),
    ("Abc1!", False), ("abcdefgh1", False), ("abcdefgh!", False), ("12345678!", False),
    ("member-2026!", False),           # contains the user ID
    ("abcdefg²!", False),              # superscript two is not an ASCII digit
])
def test_password_policy(password, ok):
    assert (password_policy_problem(password, "member") is None) is ok


def test_registration_applies_the_policy(accounts):
    with TestClient(app) as client:
        weak = client.post("/api/auth/register", json={"username": "new.member", "display_name": "신규", "password": "onlyletters"})
        assert weak.status_code == 422 and weak.json()["detail"]["code"] == "PASSWORD_POLICY"
        assert "onlyletters" not in weak.text
        good = client.post("/api/auth/register", json={"username": "new.member", "display_name": "신규", "password": "Good-pass-1"})
        assert good.status_code == 201, good.text


def test_temporary_passwords_follow_the_policy():
    for _ in range(200):
        assert password_policy_problem(temporary_password()) is None
