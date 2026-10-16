"""PhysicsAI 연동 계약(docs/physicsai-integration.md).

PhysicsAI 플랫폼은 같은 주소의 ``/physicsai/``에서 대시보드 세션 쿠키와
``/api/auth/me``·``/api/projects``에 기대어 로그인을 공유한다(PhysicsAI platform.md
§20 D2~D4). 이 시험은 그 모양이 바뀌지 않았는지와 사이드바 "AI 예측" 링크 표시
플래그(``SIMDASH_PHYSICSAI_ENABLED``, 기본 off)를 고정한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import database_settings, physicsai_link_enabled
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import SESSION_COOKIE, hash_password


PASSWORD = "physicsai-contract-password"


def _insert_user(username: str, *, admin: bool, membership_role: str | None) -> str:
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
            [user_id, username, hash_password(PASSWORD), username.title(), "admin" if admin else "viewer", now, now, admin],
        )
        if membership_role:
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


def _cleanup(user_ids: list[str], suffix: str) -> None:
    placeholders = ", ".join("?" for _ in user_ids)
    with connect() as conn:
        if database_settings().backend == "duckdb":
            conn.execute(f"DELETE FROM audit_events WHERE user_id IN ({placeholders}) OR username LIKE ?", [*user_ids, f"%-{suffix}"])
        conn.execute(f"DELETE FROM project_memberships WHERE user_id IN ({placeholders})", user_ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)


def test_physicsai_session_and_identity_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    admin_id = _insert_user(f"pai-admin-{suffix}", admin=True, membership_role=None)
    member_id = _insert_user(f"pai-member-{suffix}", admin=False, membership_role="power")
    outsider_id = _insert_user(f"pai-outsider-{suffix}", admin=False, membership_role=None)
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    monkeypatch.delenv("SIMDASH_PHYSICSAI_ENABLED", raising=False)
    try:
        with TestClient(app) as client:
            # D2: cookie name, Path=/, SameSite=Strict, HttpOnly.
            assert SESSION_COOKIE == "analysis_canvas_session"
            login = client.post("/api/auth/login", json={"username": f"pai-member-{suffix}", "password": PASSWORD})
            assert login.status_code == 200
            set_cookie = login.headers["set-cookie"]
            assert set_cookie.startswith("analysis_canvas_session=")
            lowered = set_cookie.lower()
            assert "path=/" in lowered and "samesite=strict" in lowered and "httponly" in lowered
            token = login.json()["access_token"]

            # D3: /api/auth/me fields, via cookie and via Bearer (PhysicsAI introspection).
            assert client.get("/api/auth/me").status_code == 200
            client.cookies.clear()
            me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200
            body = me.json()
            for field in ("id", "username", "display_name", "account_status", "is_global_admin", "memberships"):
                assert field in body, field
            assert body["id"] == member_id and body["account_status"] == "ACTIVE" and body["is_global_admin"] is False
            assert body["memberships"] and all(set(item) >= {"project_id", "role"} for item in body["memberships"])
            assert {item["role"] for item in body["memberships"]} == {"power"}
            assert client.get("/api/auth/me").status_code == 401

            # D4: every ACTIVE user (also a non-member) may list projects with id, name, product_name.
            outsider = client.post("/api/auth/login", json={"username": f"pai-outsider-{suffix}", "password": PASSWORD}).json()
            client.cookies.clear()
            outsider_headers = {"Authorization": f"Bearer {outsider['access_token']}"}
            assert client.get("/api/auth/me", headers=outsider_headers).json()["memberships"] == []
            projects = client.get("/api/projects", headers=outsider_headers)
            assert projects.status_code == 200
            assert projects.json() and all(set(item) >= {"id", "name", "product_name"} for item in projects.json())

            # Link flag: default off hides the sidebar link; on shows it.
            link = client.get("/api/integrations/physicsai", headers=outsider_headers)
            assert link.status_code == 200
            assert link.json() == {"enabled": False, "base_path": "/physicsai/"}
            monkeypatch.setenv("SIMDASH_PHYSICSAI_ENABLED", "true")
            enabled = client.get("/api/integrations/physicsai", headers=outsider_headers).json()
            assert enabled == {"enabled": True, "base_path": "/physicsai/"}
            assert client.get("/api/integrations/physicsai").status_code == 401
    finally:
        _cleanup([admin_id, member_id, outsider_id], suffix)


@pytest.mark.unit
def test_physicsai_link_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIMDASH_PHYSICSAI_ENABLED", raising=False)
    assert physicsai_link_enabled() is False
    for value in ("false", "0", "off", "", "no", "maybe"):
        monkeypatch.setenv("SIMDASH_PHYSICSAI_ENABLED", value)
        assert physicsai_link_enabled() is False, value
    for value in ("true", "1", "on", "yes", " TRUE "):
        monkeypatch.setenv("SIMDASH_PHYSICSAI_ENABLED", value)
        assert physicsai_link_enabled() is True, value
