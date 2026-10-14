"""Issue #50: SCX drive re-login from the dashboard (docs/plans/scx-drive-relogin-spec.md §3).

Runs against the in-memory fake adapter (``drive_fakes``); no real SDK, SSO, drive,
user database or running service is touched.
"""
from __future__ import annotations

import json
import logging
import sys

import pytest
import test_drive_foundation as foundation
from app.database_connection import connect
from app.main import app
from app.services.drive import auth as drive_auth
from app.services.drive import gateway as drive_gateway
from drive_fakes import ErrorCode, MemoryDrive, make_fake_adapter_module
from fastapi.testclient import TestClient
from test_drive_foundation import _bundle_json, _login

# Shared fixtures of the D0 tests (same database reset, users and scx settings).
_reset_drive_state = foundation._reset_drive_state
password_auth = foundation.password_auth
scx_env = foundation.scx_env

pytestmark = pytest.mark.duckdb_integration

HPC_PASSWORD = "Hpc-Pa55word-NEVER-LOG-7731"
OTHER_PASSWORD = "Other-Pa55-NEVER-LOG-9922"
HTTPS = "https://dashboard.example.test"


@pytest.fixture
def relogin_drive(monkeypatch, scx_env):
    drive = MemoryDrive()
    drive.add_dir("SPDM/Projects/check")
    drive.sso_accounts = {"spdm-shared": (HPC_PASSWORD, "spdm-shared"),
                          "someone-else": (OTHER_PASSWORD, "someone-else")}
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(drive))
    drive_auth.reset_state()
    yield drive
    drive_auth.reset_state()


def _client(base_url: str = HTTPS, peer: str | None = None) -> TestClient:
    if peer is None:
        return TestClient(app, base_url=base_url)
    return TestClient(app, base_url=base_url, client=(peer, 50000))


def _stored_account() -> tuple[str | None, str | None]:
    with connect() as conn:
        token = conn.execute("SELECT account_hint FROM drive_credentials WHERE id='scx'").fetchone()
        connected = conn.execute("SELECT account_hint FROM drive_connected_account WHERE id='scx'").fetchone()
    return (token[0] if token else None), (connected[0] if connected else None)


def _dump_everything() -> str:
    """Every text value of the tables a password could leak into."""
    parts: list[str] = []
    with connect() as conn:
        for table in ("audit_events", "notifications", "drive_credentials", "drive_connected_account"):
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():
                parts.append(" ".join(str(value) for value in row))
    return "\n".join(parts)


def _no_password(*texts: str) -> None:
    for text in texts:
        assert HPC_PASSWORD not in text
        assert OTHER_PASSWORD not in text


def _login_body(username: str, password: str, **extra) -> dict:
    return {"username": username, "password": password, **extra}


# --- status ------------------------------------------------------------------------------------

def test_auth_status_reports_capabilities_and_the_http_block(relogin_drive, password_auth):
    with _client("http://dashboard.example.test") as client:
        admin = _login(client, "drive-admin")
        body = client.get("/api/admin/drive/auth", headers=admin).json()
        assert body["adapter_supported"] is True
        assert body["capabilities"] == {"refresh": True, "password": True, "mfa": False}
        assert body["password_login"] == "https_only"
        assert body["password_login_blocked"] == "HTTP" and body["password_login_allowed"] is False
        assert body["attempts_left"] == 3 and body["max_attempts"] == 3
        viewer = _login(client, "drive-viewer")
        assert client.get("/api/admin/drive/auth", headers=viewer).status_code == 403
        assert client.get("/api/drive/status", headers=admin).json()["can_reconnect"] is True
        assert client.get("/api/drive/status", headers=viewer).json()["can_reconnect"] is False
    with _client() as client:
        admin = _login(client, "drive-admin")
        body = client.get("/api/admin/drive/auth", headers=admin).json()
        assert body["password_login_blocked"] is None and body["password_login_allowed"] is True


# --- account login ---------------------------------------------------------------------------------

def test_first_account_login_saves_tokens_and_remembers_the_account(relogin_drive, password_auth, caplog):
    caplog.set_level(logging.DEBUG)
    with _client() as client:
        admin = _login(client, "drive-admin")
        response = client.post("/api/admin/drive/login", headers=admin,
                               json=_login_body("spdm-shared", HPC_PASSWORD))
        assert response.status_code == 200, response.text
        assert response.json()["account_hint"] == "spdm-shared"
        assert response.json()["account_changed"] is False
        assert _stored_account() == ("spdm-shared", "spdm-shared")
        status = client.get("/api/admin/drive/auth", headers=admin).json()
        assert status["connected_account"] == "spdm-shared" and status["credentials_present"] is True
        # The worker keeps the new session: a drive call works without another login.
        assert client.post("/api/admin/drive/test", headers=admin).json()["ok"] is True
        _no_password(response.text, json.dumps(status), caplog.text, _dump_everything())
    assert relogin_drive.auth_calls.count(("login_password", "spdm-shared")) == 1
    with connect() as conn:
        actions = [row[0] for row in conn.execute("SELECT action FROM audit_events WHERE action LIKE 'DRIVE_LOGIN%'").fetchall()]
    assert actions == ["DRIVE_LOGIN_SUCCEEDED"]


def test_wrong_password_counts_attempts_and_locks_without_retrying(relogin_drive, password_auth, caplog):
    caplog.set_level(logging.DEBUG)
    with _client() as client:
        admin = _login(client, "drive-admin")
        for left in (2, 1, 0):
            response = client.post("/api/admin/drive/login", headers=admin,
                                   json=_login_body("spdm-shared", "wrong-" + HPC_PASSWORD))
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "AUTH_INVALID_CREDENTIALS"
            assert client.get("/api/admin/drive/auth", headers=admin).json()["attempts_left"] == left
        locked = client.post("/api/admin/drive/login", headers=admin, json=_login_body("spdm-shared", HPC_PASSWORD))
        assert locked.status_code == 429 and locked.json()["detail"]["code"] == "DRIVE_LOGIN_LOCKED"
        assert client.get("/api/admin/drive/auth", headers=admin).json()["locked_until"]
        _no_password(caplog.text, _dump_everything(), locked.text)
    # One request = one SSO attempt; the locked request never reached the SSO.
    assert len([call for call in relogin_drive.auth_calls if call[0] == "login_password"]) == 3
    assert _stored_account() == (None, None)


@pytest.mark.parametrize("code,status", [("AUTH_ACCOUNT_LOCKED", 409), ("AUTH_MFA_REQUIRED", 409),
                                         ("AUTH_METHOD_UNSUPPORTED", 409), ("RATE_LIMITED", 429)])
def test_sso_refusals_map_to_codes_without_the_sso_text(relogin_drive, password_auth, code, status):
    relogin_drive.auth_fail_next["login_password"] = ErrorCode(code)
    with _client() as client:
        admin = _login(client, "drive-admin")
        response = client.post("/api/admin/drive/login", headers=admin, json=_login_body("spdm-shared", HPC_PASSWORD))
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert "injected" not in response.text


def test_another_account_needs_confirmation_and_keeps_the_stored_tokens(relogin_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        assert client.post("/api/admin/drive/login", headers=admin,
                           json=_login_body("spdm-shared", HPC_PASSWORD)).status_code == 200
        refused = client.post("/api/admin/drive/login", headers=admin,
                              json=_login_body("someone-else", OTHER_PASSWORD))
        assert refused.status_code == 409
        detail = refused.json()["detail"]
        assert detail["code"] == "DRIVE_ACCOUNT_CHANGE_CONFIRM"
        assert detail["current"] == "spdm-shared" and detail["received"] == "someone-else"
        assert _stored_account() == ("spdm-shared", "spdm-shared")
        # The other account's session was dropped: the next call uses the stored account again.
        assert client.post("/api/admin/drive/test", headers=admin).json()["ok"] is True
        assert _stored_account() == ("spdm-shared", "spdm-shared")
        confirmed = client.post("/api/admin/drive/login", headers=admin,
                                json=_login_body("someone-else", OTHER_PASSWORD, confirm_account_change=True))
        assert confirmed.status_code == 200 and confirmed.json()["account_changed"] is True
        assert _stored_account() == ("someone-else", "someone-else")


def test_token_paste_of_another_account_needs_confirmation(relogin_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        assert client.put("/api/admin/drive/credentials", headers=admin, content=_bundle_json()).status_code == 200
        other = _bundle_json(account_hint="someone-else")
        refused = client.put("/api/admin/drive/credentials", headers=admin, content=other)
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "DRIVE_ACCOUNT_CHANGE_CONFIRM"
        assert _stored_account() == ("spdm-shared", "spdm-shared")
        accepted = client.put("/api/admin/drive/credentials?confirm_account_change=true", headers=admin, content=other)
        assert accepted.status_code == 200
        assert _stored_account() == ("someone-else", "someone-else")


# --- transport and input rules ---------------------------------------------------------------------

def test_https_only_refuses_http_and_a_spoofed_forwarded_proto(relogin_drive, password_auth):
    with _client("http://dashboard.example.test") as client:
        admin = _login(client, "drive-admin")
        for headers in ({}, {"X-Forwarded-Proto": "https"}):
            response = client.post("/api/admin/drive/login", headers={**admin, **headers},
                                   json=_login_body("spdm-shared", HPC_PASSWORD))
            assert response.status_code == 403
            assert response.json()["detail"] == {"code": "DRIVE_PASSWORD_LOGIN_DISABLED",
                                                 "message": response.json()["detail"]["message"], "reason": "HTTP"}
    assert relogin_drive.auth_calls == [] or all(call[0] != "login_password" for call in relogin_drive.auth_calls)


def test_same_pc_rule_uses_the_proxy_peer_not_the_loopback_hop(relogin_drive, password_auth):
    """Source deployment: Vite (127.0.0.1) proxies every browser; only a browser on the server PC counts."""
    http = "http://dashboard.example.test"
    with _client(http, peer="127.0.0.1") as client:
        admin = _login(client, "drive-admin")
        lan = client.post("/api/admin/drive/login", headers={**admin, "X-Simdash-Proxy-Peer": "10.1.2.3"},
                          json=_login_body("spdm-shared", HPC_PASSWORD))
        assert lan.status_code == 403 and lan.json()["detail"]["reason"] == "HTTP"
        mapped = client.post("/api/admin/drive/login", headers={**admin, "X-Simdash-Proxy-Peer": "::ffff:10.1.2.3"},
                             json=_login_body("spdm-shared", HPC_PASSWORD))
        assert mapped.status_code == 403
        same_pc = client.post("/api/admin/drive/login", headers={**admin, "X-Simdash-Proxy-Peer": "::ffff:127.0.0.1"},
                              json=_login_body("spdm-shared", HPC_PASSWORD))
        assert same_pc.status_code == 200, same_pc.text
    with _client(http, peer="10.1.2.3") as client:   # a LAN peer cannot claim the server PC
        admin = _login(client, "drive-admin")
        spoofed = client.post("/api/admin/drive/login", headers={**admin, "X-Simdash-Proxy-Peer": "127.0.0.1"},
                              json=_login_body("spdm-shared", HPC_PASSWORD))
        assert spoofed.status_code == 403


def test_password_login_off_and_on(relogin_drive, password_auth, monkeypatch):
    monkeypatch.setenv("SIMDASH_DRIVE_PASSWORD_LOGIN", "off")
    with _client() as client:
        admin = _login(client, "drive-admin")
        response = client.post("/api/admin/drive/login", headers=admin, json=_login_body("spdm-shared", HPC_PASSWORD))
        assert response.status_code == 403 and response.json()["detail"]["reason"] == "OFF"
    drive_gateway.shutdown()
    monkeypatch.setenv("SIMDASH_DRIVE_PASSWORD_LOGIN", "on")
    with _client("http://dashboard.example.test") as client:
        admin = _login(client, "drive-admin")
        response = client.post("/api/admin/drive/login", headers=admin, json=_login_body("spdm-shared", HPC_PASSWORD))
        assert response.status_code == 200, response.text


@pytest.mark.parametrize("body", [b"not json", b"[]", json.dumps({"username": "", "password": "x"}).encode(),
                                  json.dumps({"username": "a" * 129, "password": "x"}).encode(),
                                  json.dumps({"username": "spdm-shared", "password": "p" * 257}).encode(),
                                  json.dumps({"username": "spdm-shared"}).encode(),
                                  json.dumps({"username": "spdm\nshared", "password": HPC_PASSWORD}).encode()])
def test_invalid_login_body_is_refused_without_echo(relogin_drive, password_auth, body):
    with _client() as client:
        admin = _login(client, "drive-admin")
        response = client.post("/api/admin/drive/login", headers={**admin, "Content-Type": "application/json"},
                               content=body)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DRIVE_LOGIN_INVALID"
    _no_password(response.text)
    assert "ppppp" not in response.text


def test_viewer_cannot_log_in(relogin_drive, password_auth):
    with _client() as client:
        viewer = _login(client, "drive-viewer")
        response = client.post("/api/admin/drive/login", headers=viewer, json=_login_body("spdm-shared", HPC_PASSWORD))
    assert response.status_code == 403
    assert all(call[0] != "login_password" for call in relogin_drive.auth_calls)


# --- reconnect with the stored token ------------------------------------------------------------------

def test_reconnect_refreshes_the_stored_token(relogin_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        missing = client.post("/api/admin/drive/reconnect", headers=admin, json={})
        assert missing.status_code == 409 and missing.json()["detail"]["code"] == "DRIVE_CREDENTIALS_MISSING"
        assert client.put("/api/admin/drive/credentials", headers=admin, content=_bundle_json()).status_code == 200
        response = client.post("/api/admin/drive/reconnect", headers=admin, json={})
        assert response.status_code == 200, response.text
        assert response.json()["account_hint"] == "spdm-shared"
        assert client.get("/api/admin/drive/auth", headers=admin).json()["last_refresh_at"]
        relogin_drive.refresh_expired = True
        expired = client.post("/api/admin/drive/reconnect", headers=admin, json={})
        assert expired.status_code == 409 and expired.json()["detail"]["code"] == "AUTH_REFRESH_EXPIRED"
        assert client.get("/api/admin/drive/auth", headers=admin).json()["last_error_code"] == "AUTH_REFRESH_EXPIRED"
    with connect() as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM audit_events WHERE action LIKE 'DRIVE_RECONNECT%' ORDER BY occurred_at").fetchall()]
    assert actions.count("DRIVE_RECONNECT_SUCCEEDED") == 1 and actions.count("DRIVE_RECONNECT_FAILED") == 2


def test_reconnect_returning_another_account_is_not_saved(relogin_drive, password_auth):
    with _client() as client:
        admin = _login(client, "drive-admin")
        assert client.put("/api/admin/drive/credentials", headers=admin, content=_bundle_json()).status_code == 200
        relogin_drive.refresh_account = "someone-else"
        response = client.post("/api/admin/drive/reconnect", headers=admin, json={})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "DRIVE_ACCOUNT_CHANGE_CONFIRM"
    assert _stored_account() == ("spdm-shared", "spdm-shared")


# --- adapters released before issue #50 ------------------------------------------------------------------

@pytest.mark.parametrize("older", ["no_methods", "unknown_op"])
def test_older_adapter_reports_update_required_and_keeps_attempts(monkeypatch, scx_env, password_auth, older):
    drive = MemoryDrive()
    drive.add_dir("SPDM/Projects/check")
    if older == "unknown_op":
        drive.unknown_op = True
    monkeypatch.setitem(sys.modules, "scx_drive_adapter", make_fake_adapter_module(drive, relogin=older != "no_methods"))
    drive_auth.reset_state()
    with _client() as client:
        admin = _login(client, "drive-admin")
        status = client.get("/api/admin/drive/auth", headers=admin).json()
        assert status["adapter_supported"] is False and status["password_login_allowed"] is False
        response = client.post("/api/admin/drive/login", headers=admin, json=_login_body("spdm-shared", HPC_PASSWORD))
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "DRIVE_ADAPTER_UPDATE_REQUIRED"
        assert client.get("/api/admin/drive/auth", headers=admin).json()["attempts_left"] == 3


# --- token store details ------------------------------------------------------------------------------

def test_write_adopts_the_new_generation_so_a_following_rotation_is_saved():
    from app.services.drive.token_store import DbTokenStore
    from cryptography.fernet import Fernet
    from test_drive_foundation import _bundle

    store = DbTokenStore(Fernet.generate_key().decode())
    store.write(_bundle(), updated_by="drive-admin", remember_account=True)
    gateway_store = store.for_gateway()
    assert gateway_store.load() is not None
    generation = store.write(_bundle(access_token="eyJnew.SECRET"), updated_by="drive-admin",
                             remember_account=True, adopt_into=gateway_store)
    assert gateway_store.loaded_generation == generation
    gateway_store.save(_bundle(access_token="eyJrotated.SECRET", refresh_token="rotated-SECRET"))
    loaded = store.for_gateway().load()
    assert loaded is not None and loaded.access_token == "eyJrotated.SECRET"


def test_unnamed_token_keeps_the_connected_account():
    from app.services.drive.token_store import DbTokenStore
    from cryptography.fernet import Fernet
    from test_drive_foundation import _bundle

    store = DbTokenStore(Fernet.generate_key().decode())
    store.write(_bundle(), updated_by="drive-admin", remember_account=True)
    store.write(_bundle(account_hint=None), updated_by="drive-admin", remember_account=True)
    assert store.connected_account() == "spdm-shared"


def test_drive_calls_wait_while_an_account_switch_is_pending(monkeypatch):
    from app.services.drive import reads as drive_reads
    from app.services.storage.provider import SpdmStorageError

    monkeypatch.setattr(drive_auth, "SWITCH_WAIT_SECONDS", 0.05)
    with drive_auth.account_switch():
        assert drive_auth.account_switch_active()
        with pytest.raises(SpdmStorageError) as caught:
            drive_reads._call(lambda gateway: gateway, op="stat")
        assert caught.value.code == "DRIVE_BUSY"
    assert not drive_auth.account_switch_active()


def test_unreachable_sso_reports_the_adapter_code_and_logs_it(relogin_drive, password_auth, caplog):
    caplog.set_level(logging.WARNING)
    relogin_drive.auth_fail_next["login_password"] = ErrorCode.UNAVAILABLE
    with _client() as client:
        admin = _login(client, "drive-admin")
        response = client.post("/api/admin/drive/login", headers=admin, json=_login_body("spdm-shared", HPC_PASSWORD))
    detail = response.json()["detail"]
    assert response.status_code == 503 and detail["code"] == "DRIVE_UNAVAILABLE"
    assert detail["adapter_code"] == "UNAVAILABLE" and "UNAVAILABLE" in detail["message"]
    assert "SCX drive login_password failed: code=UNAVAILABLE" in caplog.text
    _no_password(response.text, caplog.text)
