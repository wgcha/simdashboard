"""Sliding session: an active cookie session is renewed, revocations still win (2026-10-23)."""
from __future__ import annotations

import time as real_time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import security
from app.config import security_settings
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import SESSION_COOKIE, Principal, create_access_token, hash_password

PASSWORD = "sliding-session-password-1"
DAY = 86400


class _Clock:
    """Stands in for the ``time`` module inside app.security only."""

    def __init__(self) -> None:
        self.now = real_time.time()

    def time(self) -> float:
        return self.now


@pytest.fixture()
def clock(monkeypatch) -> _Clock:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "sliding-session-test-secret-key-at-least-32-chars")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.delenv("AUTH_TOKEN_TTL_MINUTES", raising=False)
    monkeypatch.delenv("AUTH_SESSION_IDLE_DAYS", raising=False)
    fake = _Clock()
    monkeypatch.setattr(security, "time", fake)
    return fake


def _create_user() -> tuple[str, str]:
    initialize_database()
    user_id = f"slide-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        # An ACTIVE global admin must exist, otherwise password mode reports "setup required".
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active,
               created_at, updated_at, account_status, is_global_admin)
               VALUES (?, ?, ?, ?, 'admin', true, ?, ?, 'ACTIVE', true)""",
            [user_id, user_id, hash_password(PASSWORD), "Sliding", now, now],
        )
    return user_id, user_id


def _login(client: TestClient, username: str):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response


def _session_cookies(response) -> list[str]:
    return [value for value in response.headers.get_list("set-cookie") if value.startswith(f"{SESSION_COOKIE}=")]


def _max_age(cookie_header: str) -> int:
    return int(next(part.split("=")[1] for part in cookie_header.split("; ") if part.lower().startswith("max-age=")))


def _attrs(cookie_header: str) -> set[str]:
    return {part.split("=")[0].lower() + ("=" + part.split("=")[1].lower() if "=" in part else "") for part in cookie_header.split("; ")[1:]}


def test_login_cookie_is_idle_lifetime_with_strict_attributes(clock):
    _, username = _create_user()
    with TestClient(app) as client:
        login = _login(client, username)
        (cookie,) = _session_cookies(login)
        assert _max_age(cookie) == 30 * DAY
        attrs = _attrs(cookie)
        assert {"httponly", "samesite=strict", "path=/"} <= attrs
        assert "secure" not in attrs
        assert login.json()["expires_at"] == int(clock.now) + 30 * DAY


def test_renews_only_after_threshold_and_keeps_cookie_attributes(clock, monkeypatch):
    _, username = _create_user()
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, username)
        clock.now += 59 * 60
        early = client.get("/api/auth/me")
        assert early.status_code == 200
        assert _session_cookies(early) == []

        clock.now += 2 * 60  # 61 minutes after login
        renewed = client.get("/api/auth/me")
        assert renewed.status_code == 200
        (cookie,) = _session_cookies(renewed)
        assert _max_age(cookie) == 30 * DAY
        assert {"httponly", "samesite=strict", "path=/", "secure"} <= _attrs(cookie)

        # The renewed token restarts the clock: no further renewal right away.
        again = client.get("/api/auth/me")
        assert again.status_code == 200
        assert _session_cookies(again) == []


def test_old_but_valid_token_is_renewed_and_active_browser_never_expires(clock):
    _, username = _create_user()
    with TestClient(app) as client:
        _login(client, username)
        clock.now += 20 * DAY
        assert client.get("/api/auth/me").status_code == 200  # renewed at day 20
        clock.now += 25 * DAY  # day 45: beyond the original 30 days, inside 20 + 30
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert len(_session_cookies(response)) == 1


def test_expired_token_is_rejected_and_not_renewed(clock):
    _, username = _create_user()
    with TestClient(app) as client:
        _login(client, username)
        clock.now += 30 * DAY + 5
        response = client.get("/api/auth/me")
        assert response.status_code == 401
        assert _session_cookies(response) == []


def test_revoked_token_is_not_renewed_and_later_reset_beats_renewed_token(clock):
    user_id, username = _create_user()
    real_now = clock.now
    with TestClient(app) as client:
        clock.now = real_now - 3 * 3600
        _login(client, username)
        clock.now = real_now - 3600
        renewed = client.get("/api/auth/me")  # renewed token has iat = real_now - 1 h
        assert len(_session_cookies(renewed)) == 1

        revoked_at = datetime.fromtimestamp(real_now, timezone.utc).replace(tzinfo=None)
        with connect() as conn:
            conn.execute("UPDATE users SET sessions_revoked_at=? WHERE id=?", [revoked_at, user_id])
        clock.now = real_now + 2 * 3600  # old enough to renew, but the reset came after the last renewal
        response = client.get("/api/auth/me")
        assert response.status_code == 401
        assert _session_cookies(response) == []


def test_suspended_account_is_not_renewed(clock):
    _create_user()  # keeps another ACTIVE administrator so the server is not in "setup required"
    user_id, username = _create_user()
    with TestClient(app) as client:
        _login(client, username)
        with connect() as conn:
            conn.execute("UPDATE users SET account_status='SUSPENDED' WHERE id=?", [user_id])
        clock.now += 2 * 3600
        response = client.get("/api/auth/me")
        assert response.status_code == 403
        assert all(_max_age(cookie) == 0 for cookie in _session_cookies(response))


def test_logout_clears_cookie_and_is_not_renewed(clock):
    _, username = _create_user()
    with TestClient(app) as client:
        _login(client, username)
        clock.now += 2 * 3600
        response = client.post("/api/auth/logout")
        assert response.status_code == 200
        cookies = _session_cookies(response)
        assert len(cookies) == 1 and _max_age(cookies[0]) == 0
        assert client.get("/api/auth/me").status_code == 401


def test_bearer_header_callers_are_not_renewed(clock):
    user_id, username = _create_user()
    principal = Principal(user_id, username, "Sliding", "ACTIVE", True, None, "admin")
    token, _ = create_access_token(principal)
    clock.now += 2 * 3600
    with TestClient(app) as client:
        response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert _session_cookies(response) == []


def test_explicit_token_ttl_minutes_still_defines_lifetime_and_threshold(clock, monkeypatch):
    _, username = _create_user()
    monkeypatch.setenv("AUTH_TOKEN_TTL_MINUTES", "60")
    monkeypatch.setenv("AUTH_SESSION_IDLE_DAYS", "90")
    with TestClient(app) as client:
        (cookie,) = _session_cookies(_login(client, username))
        assert _max_age(cookie) == 3600
        clock.now += 29 * 60
        assert _session_cookies(client.get("/api/auth/me")) == []
        clock.now += 2 * 60  # past half of the 60-minute lifetime
        (renewed,) = _session_cookies(client.get("/api/auth/me"))
        assert _max_age(renewed) == 3600


def test_session_settings_ranges(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.delenv("AUTH_TOKEN_TTL_MINUTES", raising=False)
    monkeypatch.delenv("AUTH_SESSION_IDLE_DAYS", raising=False)
    assert security_settings().session_lifetime_seconds == 30 * DAY
    monkeypatch.setenv("AUTH_TOKEN_TTL_MINUTES", "")  # empty = not set
    assert security_settings().token_ttl_minutes is None
    monkeypatch.setenv("AUTH_TOKEN_TTL_MINUTES", "525600")
    assert security_settings().session_lifetime_seconds == 365 * DAY
    for bad in ("4", "525601", "x"):
        monkeypatch.setenv("AUTH_TOKEN_TTL_MINUTES", bad)
        with pytest.raises(RuntimeError, match="AUTH_TOKEN_TTL_MINUTES"):
            security_settings()
    monkeypatch.delenv("AUTH_TOKEN_TTL_MINUTES")
    for bad in ("0", "366", "x"):
        monkeypatch.setenv("AUTH_SESSION_IDLE_DAYS", bad)
        with pytest.raises(RuntimeError, match="AUTH_SESSION_IDLE_DAYS"):
            security_settings()
