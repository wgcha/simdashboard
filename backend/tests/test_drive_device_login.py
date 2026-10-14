"""SCX drive login with an activation code (device grant, 2026-10-14, relogin spec §7).

Runs against the in-memory fake adapter (``drive_fakes``); no SDK, SSO, browser, user
database or running service is touched.  The shared drive account is fixed: a code approved
by another account is never saved.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import timedelta

import pytest
import test_drive_foundation as foundation
from app.database_connection import connect
from app.main import app
from app.services.drive import auth as drive_auth
from app.services.drive import device_login
from drive_fakes import MemoryDrive, make_fake_adapter_module
from fastapi.testclient import TestClient
from test_drive_foundation import _login

_reset_drive_state = foundation._reset_drive_state
password_auth = foundation.password_auth
scx_env = foundation.scx_env

pytestmark = pytest.mark.duckdb_integration

BASE = "/api/admin/drive/auth/device"


@pytest.fixture
def device_drive(monkeypatch, scx_env):
    drive = MemoryDrive()
    drive.add_dir("SPDM/Projects/check")
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(drive))
    drive_auth.reset_state()
    with connect() as conn:   # the isolated test database keeps audit rows between tests
        conn.execute("DELETE FROM audit_events WHERE action LIKE 'DRIVE_DEVICE_LOGIN%'")
    yield drive
    drive_auth.reset_state()


def _client() -> TestClient:
    # Plain HTTP on purpose: no password passes through the dashboard, so code login works without HTTPS.
    return TestClient(app, base_url="http://dashboard.example.test")


def _due() -> None:
    """Skip the poll interval (the screen waits ``interval`` seconds between checks)."""
    session = device_login._session
    assert session is not None
    session.next_poll_at = device_login._now() - timedelta(seconds=1)


def _poll(client: TestClient, headers: dict[str, str], device_id: str) -> dict:
    _due()
    response = client.post(f"{BASE}/{device_id}/poll", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _accounts() -> tuple[str | None, str | None]:
    with connect() as conn:
        token = conn.execute("SELECT account_hint FROM drive_credentials WHERE id='scx'").fetchone()
        connected = conn.execute("SELECT account_hint FROM drive_connected_account WHERE id='scx'").fetchone()
    return (token[0] if token else None), (connected[0] if connected else None)


def _actions() -> list[str]:
    with connect() as conn:
        return [row[0] for row in conn.execute(
            "SELECT action FROM audit_events WHERE action LIKE 'DRIVE_DEVICE_LOGIN%' ORDER BY occurred_at").fetchall()]


def _connect_shared(client: TestClient, admin: dict[str, str]) -> None:
    started = client.post(f"{BASE}/start", headers=admin).json()
    assert _poll(client, admin, started["device_id"])["status"] == "PENDING"
    assert _poll(client, admin, started["device_id"])["status"] == "SUCCEEDED"


def test_code_login_connects_the_shared_account_over_http(device_drive, password_auth, caplog):
    caplog.set_level(logging.DEBUG)
    with _client() as client:
        admin = _login(client, "drive-admin")
        status = client.get("/api/admin/drive/auth", headers=admin).json()
        assert status["capabilities"]["device"] is True and status["device_login_allowed"] is True
        assert status["device_login"] is None
        viewer = _login(client, "drive-viewer")
        assert client.post(f"{BASE}/start", headers=viewer).status_code == 403

        started = client.post(f"{BASE}/start", headers=admin)
        assert started.status_code == 200, started.text
        view = started.json()
        assert view["status"] == "PENDING" and view["user_code"] == "WDJB-MJHT"
        assert view["verification_uri"] == "https://sso.example.test/realms/hpc/device"
        assert view["verification_uri_complete"].endswith("user_code=WDJB-MJHT")
        assert "login_id" not in view and "login-" not in started.text   # the adapter id stays on the server
        # A second start while waiting shows the same code (no second SSO request).
        again = client.post(f"{BASE}/start", headers=admin).json()
        assert again["device_id"] == view["device_id"]
        assert [call for call in device_drive.auth_calls if call[0] == "device_login_start"] == [("device_login_start", "")]
        # Not due yet: no adapter call.
        polls_before = len([c for c in device_drive.auth_calls if c[0] == "device_login_poll"])
        assert client.post(f"{BASE}/{view['device_id']}/poll", headers=admin).json()["status"] == "PENDING"
        assert len([c for c in device_drive.auth_calls if c[0] == "device_login_poll"]) == polls_before

        assert _poll(client, admin, view["device_id"])["status"] == "PENDING"
        done = _poll(client, admin, view["device_id"])
        assert done["status"] == "SUCCEEDED" and done["account"] == "spdm-shared"
        assert done["user_code"] is None and done["verification_uri"] is None
        assert _accounts() == ("spdm-shared", "spdm-shared")
        assert client.post("/api/admin/drive/test", headers=admin).json()["ok"] is True
        status = client.get("/api/admin/drive/auth", headers=admin).json()
        assert status["device_login"]["status"] == "SUCCEEDED" and status["connected_account"] == "spdm-shared"
        # Finished sessions are not polled again.
        calls = len(device_drive.auth_calls)
        assert _poll(client, admin, view["device_id"])["status"] == "SUCCEEDED"
        assert len(device_drive.auth_calls) == calls
    assert _actions() == ["DRIVE_DEVICE_LOGIN_STARTED", "DRIVE_DEVICE_LOGIN_SUCCEEDED"]
    with connect() as conn:
        audit = json.dumps([list(row) for row in conn.execute(
            "SELECT detail_json FROM audit_events WHERE action LIKE 'DRIVE_DEVICE_LOGIN%'").fetchall()], default=str)
    for secret in ("WDJB-MJHT", "SECRET", "login-"):
        assert secret not in audit
        assert secret not in caplog.text


def test_another_account_is_refused_and_the_shared_one_kept(device_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        _connect_shared(client, admin)
        device_drive.device_account = "someone-else"
        started = client.post(f"{BASE}/start", headers=admin).json()
        assert _poll(client, admin, started["device_id"])["status"] == "PENDING"
        refused = _poll(client, admin, started["device_id"])
        assert refused["status"] == "FAILED" and refused["code"] == "DRIVE_DEVICE_ACCOUNT_MISMATCH"
        assert refused["current"] == "spdm-shared" and refused["received"] == "someone-else"
        assert "다른 기기" in refused["message"]
        assert _accounts() == ("spdm-shared", "spdm-shared")
        # The worker session of the other account was dropped: the stored shared account works.
        assert client.post("/api/admin/drive/test", headers=admin).json()["ok"] is True
    assert _actions()[-1] == "DRIVE_DEVICE_LOGIN_FAILED"


def test_unnamed_account_is_refused_once_an_account_is_connected(device_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        _connect_shared(client, admin)
        device_drive.device_account = None
        started = client.post(f"{BASE}/start", headers=admin).json()
        _poll(client, admin, started["device_id"])
        refused = _poll(client, admin, started["device_id"])
        assert refused["status"] == "FAILED" and refused["code"] == "DRIVE_DEVICE_ACCOUNT_MISMATCH"
        assert _accounts() == ("spdm-shared", "spdm-shared")


@pytest.mark.parametrize("outcome,status,code", [
    ("DENIED", "FAILED", "AUTH_DEVICE_DENIED"),
    ("EXPIRED", "EXPIRED", "AUTH_DEVICE_EXPIRED"),
])
def test_denied_and_expired_codes(device_drive, password_auth, outcome, status, code):
    device_drive.device_outcome = outcome
    with _client() as client:
        admin = _login(client, "drive-admin")
        started = client.post(f"{BASE}/start", headers=admin).json()
        _poll(client, admin, started["device_id"])
        finished = _poll(client, admin, started["device_id"])
        assert finished["status"] == status and finished["code"] == code and finished["message"]
        assert _accounts() == (None, None)
        # A finished login can be started again with a new code.
        restarted = client.post(f"{BASE}/start", headers=admin).json()
        assert restarted["device_id"] != started["device_id"] and restarted["status"] == "PENDING"


def test_local_expiry_and_unknown_login_cancel_at_the_adapter(device_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        started = client.post(f"{BASE}/start", headers=admin).json()
        device_login._session.expires_at = device_login._now() - timedelta(seconds=1)
        expired = _poll(client, admin, started["device_id"])
        assert expired["status"] == "EXPIRED" and expired["user_code"] is None
        assert device_drive.auth_calls[-1][0] == "device_login_cancel"
        # The worker restarted (its pending login is gone): NOT_FOUND ends the login as expired.
        started = client.post(f"{BASE}/start", headers=admin).json()
        device_drive.device_logins.clear()
        lost = _poll(client, admin, started["device_id"])
        assert lost["status"] == "EXPIRED" and lost["code"] == "AUTH_DEVICE_EXPIRED"


def test_slow_down_lengthens_the_interval(device_drive, password_auth):
    device_drive.device_slow_down = True
    with _client() as client:
        admin = _login(client, "drive-admin")
        started = client.post(f"{BASE}/start", headers=admin).json()
        assert started["interval"] == 5
        assert _poll(client, admin, started["device_id"])["interval"] == 10


def test_cancel_and_wrong_ids(device_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        started = client.post(f"{BASE}/start", headers=admin).json()
        cancelled = client.post(f"{BASE}/{started['device_id']}/cancel", headers=admin).json()
        assert cancelled["status"] == "CANCELLED"
        assert device_drive.auth_calls[-1][0] == "device_login_cancel"
        assert client.post(f"{BASE}/{started['device_id']}/cancel", headers=admin).json()["status"] == "CANCELLED"
        assert _poll(client, admin, started["device_id"])["status"] == "CANCELLED"
        wrong = client.post(f"{BASE}/not-this-one/poll", headers=admin)
        assert wrong.status_code == 404 and wrong.json()["detail"]["code"] == "DRIVE_DEVICE_LOGIN_NOT_FOUND"
    assert _actions() == ["DRIVE_DEVICE_LOGIN_STARTED", "DRIVE_DEVICE_LOGIN_CANCELLED"]


def test_unsafe_or_missing_codes_from_the_adapter(device_drive, password_auth):
    device_drive.device_start_result = {"user_code": "ABCD-EFGH", "verification_uri": "javascript:alert(1)",
                                        "verification_uri_complete": "https://sso.example.test/x y", "interval": 0}
    with _client() as client:
        admin = _login(client, "drive-admin")
        view = client.post(f"{BASE}/start", headers=admin).json()
        assert view["verification_uri"] is None and view["verification_uri_complete"] is None
        assert view["interval"] == device_login.MIN_INTERVAL
        client.post(f"{BASE}/{view['device_id']}/cancel", headers=admin)
        device_drive.device_start_result = {"user_code": "<script>", "verification_uri": "https://sso.example.test/d"}
        bad = client.post(f"{BASE}/start", headers=admin)
        assert bad.status_code == 502 and bad.json()["detail"]["code"] == "DRIVE_INTERNAL"


def test_adapters_without_code_login(monkeypatch, scx_env, password_auth):
    drive = MemoryDrive()
    drive.add_dir("SPDM/Projects/check")
    drive.auth_caps = {"refresh": True, "password": False, "mfa": False}
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(drive))
    drive_auth.reset_state()
    with _client() as client:
        admin = _login(client, "drive-admin")
        assert client.get("/api/admin/drive/auth", headers=admin).json()["device_login_allowed"] is False
        refused = client.post(f"{BASE}/start", headers=admin)
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "AUTH_METHOD_UNSUPPORTED"
    old = MemoryDrive()
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(old, relogin=False))
    drive_auth.reset_state()
    from app.services.drive import gateway as drive_gateway

    drive_gateway.shutdown()
    with _client() as client:
        admin = _login(client, "drive-admin")
        refused = client.post(f"{BASE}/start", headers=admin)
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "DRIVE_ADAPTER_UPDATE_REQUIRED"
    drive_auth.reset_state()


def test_a_rotation_during_a_pending_check_is_not_an_approval(device_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        _connect_shared(client, admin)
        device_drive.device_rotate_while_pending = True
        device_drive.device_pending_polls = 3
        started = client.post(f"{BASE}/start", headers=admin).json()
        for _ in range(3):
            assert _poll(client, admin, started["device_id"])["status"] == "PENDING"
        assert _actions().count("DRIVE_DEVICE_LOGIN_SUCCEEDED") == 1     # only the first connection
        assert _poll(client, admin, started["device_id"])["status"] == "SUCCEEDED"


def test_unnamed_approver_is_refused_even_for_the_first_connection(device_drive, password_auth):
    device_drive.device_account = None
    with _client() as client:
        admin = _login(client, "drive-admin")
        started = client.post(f"{BASE}/start", headers=admin).json()
        _poll(client, admin, started["device_id"])
        refused = _poll(client, admin, started["device_id"])
        assert refused["status"] == "FAILED" and refused["code"] == "DRIVE_DEVICE_ACCOUNT_MISMATCH"
        assert _accounts() == (None, None)


def test_an_unexpected_error_while_saving_drops_the_worker_session(device_drive, password_auth, monkeypatch):
    from app.routers import drive as drive_router
    from app.services.drive import gateway as drive_gateway

    def broken(*_args, **_kwargs):
        raise RuntimeError("database went away")

    resets: list[bool] = []
    real_reset = drive_gateway.reset_gateway
    monkeypatch.setattr(drive_gateway, "reset_gateway", lambda: (resets.append(True), real_reset())[1])
    monkeypatch.setattr(drive_router, "_apply_relogin", broken)
    with _client() as client:
        admin = _login(client, "drive-admin")
        started = client.post(f"{BASE}/start", headers=admin).json()
        _poll(client, admin, started["device_id"])
        failed = _poll(client, admin, started["device_id"])
        assert failed["status"] == "FAILED" and failed["code"] == "DRIVE_INTERNAL"
        assert resets and _accounts() == (None, None)
        assert drive_auth.operation_lock.acquire(blocking=False)      # never left held
        drive_auth.operation_lock.release()


def test_odd_device_ids_are_not_found(device_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        client.post(f"{BASE}/start", headers=admin)
        for wrong in ("%ED%95%9C%EA%B8%80", "a" * 80):
            response = client.post(f"{BASE}/{wrong}/poll", headers=admin)
            assert response.status_code == 404 and response.json()["detail"]["code"] == "DRIVE_DEVICE_LOGIN_NOT_FOUND"
