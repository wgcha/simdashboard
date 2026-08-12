from __future__ import annotations

import base64
import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.config import security_settings
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.routers import security as security_router
from app.services import oidc_service
from app.services.oidc_service import OidcDiscovery, OidcProtocolError
from scripts import approve_oidc_global_admin


def _oidc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "oidc")
    monkeypatch.setenv("AUTH_SECRET_KEY", "oidc-test-secret-key-that-is-longer-than-32-characters")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("OIDC_ALLOW_INSECURE_LOCALHOST", "true")
    monkeypatch.setenv("OIDC_ISSUER_URL", "http://localhost:9000")
    monkeypatch.setenv("OIDC_CLIENT_ID", "analysis-canvas")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "mock-client-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "http://localhost:8000/api/auth/oidc/callback")
    monkeypatch.setenv("OIDC_LOGIN_SUCCESS_URL", "/approved")


def _discovery() -> OidcDiscovery:
    return OidcDiscovery(
        issuer="http://localhost:9000",
        authorization_endpoint="http://localhost:9000/authorize",
        token_endpoint="http://localhost:9000/token",
        jwks_uri="http://localhost:9000/jwks",
        signing_algorithms=("RS256",),
    )


def _start_flow(client: TestClient) -> dict[str, list[str]]:
    response = client.get("/api/auth/oidc/start", follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["nonce"][0]
    return query


def test_oidc_requires_https_and_secure_cookie(monkeypatch: pytest.MonkeyPatch):
    _oidc_env(monkeypatch)
    monkeypatch.delenv("OIDC_ALLOW_INSECURE_LOCALHOST")
    with pytest.raises(RuntimeError, match="HTTPS"):
        security_settings()

    monkeypatch.setenv("OIDC_ISSUER_URL", "https://idp.example.test")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example.test/api/auth/oidc/callback")
    with pytest.raises(RuntimeError, match="AUTH_COOKIE_SECURE"):
        security_settings()


def test_oidc_state_pkce_pending_account_and_no_token_persistence(monkeypatch: pytest.MonkeyPatch):
    initialize_database()
    _oidc_env(monkeypatch)
    suffix = uuid4().hex[:10]

    async def fake_discovery(_settings):
        return _discovery()

    async def fake_exchange(_settings, _discovery_value, *, code, code_verifier):
        assert code == "authorization-code"
        assert 43 <= len(code_verifier) <= 128
        return "never-persist-this-id-token"

    async def fake_validate(_settings, _discovery_value, *, id_token, expected_nonce):
        assert id_token == "never-persist-this-id-token"
        assert expected_nonce
        return {
            "iss": "http://localhost:9000",
            "sub": f"subject-{suffix}",
            "preferred_username": f"oidc-{suffix}",
            "name": f"OIDC User {suffix}",
            "employee_id": f"employee-{suffix}",
            "department": "CAE",
            "job_title": "Engineer",
            "email": f"{suffix}@example.test",
        }

    monkeypatch.setattr(security_router, "fetch_discovery", fake_discovery)
    monkeypatch.setattr(security_router, "exchange_code", fake_exchange)
    monkeypatch.setattr(security_router, "validate_id_token", fake_validate)

    try:
        with TestClient(app) as client:
            query = _start_flow(client)
            wrong = client.get(
                "/api/auth/oidc/callback",
                params={"code": "authorization-code", "state": "wrong-state"},
                follow_redirects=False,
            )
            assert wrong.status_code == 401
            assert wrong.json()["detail"]["code"] == "OIDC_STATE_MISMATCH"

            query = _start_flow(client)
            callback = client.get(
                "/api/auth/oidc/callback",
                params={"code": "authorization-code", "state": query["state"][0]},
                follow_redirects=False,
            )
            assert callback.status_code == 302
            assert callback.headers["location"] == "/approved"
            assert "analysis_canvas_session=" in callback.headers["set-cookie"]
            assert "HttpOnly" in callback.headers["set-cookie"]
            assert "SameSite=strict" in callback.headers["set-cookie"]

            me = client.get("/api/auth/me")
            assert me.status_code == 200
            assert me.json()["account_status"] == "PENDING"
            blocked = client.get("/api/projects")
            assert blocked.status_code == 403
            assert blocked.json()["detail"]["code"] == "AUTH_ACCOUNT_PENDING"

        with connect() as conn:
            user = conn.execute(
                "SELECT id, password_hash, account_status, oidc_issuer, oidc_subject FROM users WHERE employee_id=?",
                [f"employee-{suffix}"],
            ).fetchone()
            assert user and user[1] is None and user[2] == "PENDING"
            assert user[3:] == ("http://localhost:9000", f"subject-{suffix}")
            audit_text = "\n".join(
                str(row[0]) for row in conn.execute("SELECT detail_json FROM audit_events WHERE user_id=?", [user[0]]).fetchall()
            )
            assert "never-persist-this-id-token" not in audit_text
            assert "mock-client-secret" not in audit_text
    finally:
        with connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE oidc_subject=?", [f"subject-{suffix}"]).fetchone()
            if row:
                conn.execute("DELETE FROM audit_events WHERE user_id=?", [row[0]])
                conn.execute("DELETE FROM users WHERE id=?", [row[0]])


def test_oidc_identity_conflict_is_not_auto_merged(monkeypatch: pytest.MonkeyPatch):
    initialize_database()
    _oidc_env(monkeypatch)
    suffix = uuid4().hex[:10]
    employee_id = f"employee-conflict-{suffix}"
    user_id = f"existing-{suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, employee_id, account_status, is_global_admin)
            VALUES (?, ?, NULL, ?, NULL, true, ?, ?, ?, 'ACTIVE', false)
            """,
            [user_id, f"existing-{suffix}", "Existing User", now, now, employee_id],
        )

    async def fake_discovery(_settings):
        return _discovery()

    async def fake_exchange(*_args, **_kwargs):
        return "id-token"

    async def fake_validate(*_args, **_kwargs):
        return {
            "sub": f"different-subject-{suffix}",
            "preferred_username": f"different-{suffix}",
            "name": "Different Identity",
            "employee_id": employee_id,
        }

    monkeypatch.setattr(security_router, "fetch_discovery", fake_discovery)
    monkeypatch.setattr(security_router, "exchange_code", fake_exchange)
    monkeypatch.setattr(security_router, "validate_id_token", fake_validate)
    try:
        with TestClient(app) as client:
            query = _start_flow(client)
            response = client.get(
                "/api/auth/oidc/callback",
                params={"code": "code", "state": query["state"][0]},
                follow_redirects=False,
            )
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "OIDC_IDENTITY_CONFLICT"
        with connect() as conn:
            assert conn.execute("SELECT oidc_subject FROM users WHERE id=?", [user_id]).fetchone()[0] is None
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM audit_events WHERE user_id=? OR detail_json LIKE ?", [user_id, f"%{suffix}%"])
            conn.execute("DELETE FROM users WHERE id=?", [user_id])


def test_recovery_cli_approves_existing_oidc_account_without_password(monkeypatch: pytest.MonkeyPatch):
    initialize_database()
    suffix = uuid4().hex[:10]
    user_id = f"recovery-{suffix}"
    username = f"recovery-{suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, oidc_issuer, oidc_subject, account_status, is_global_admin)
            VALUES (?, ?, NULL, ?, NULL, true, ?, ?, 'https://idp.example.test', ?, 'PENDING', false)
            """,
            [user_id, username, "Recovery User", now, now, f"subject-{suffix}"],
        )
    monkeypatch.setattr(
        "sys.argv",
        [
            "approve_oidc_global_admin.py",
            "--username",
            username,
            "--confirm-user",
            username,
            "--reason",
            "초기 운영 관리자 승인",
        ],
    )
    try:
        approve_oidc_global_admin.main()
        with connect() as conn:
            user = conn.execute("SELECT password_hash, account_status, is_global_admin FROM users WHERE id=?", [user_id]).fetchone()
            assert user == (None, "ACTIVE", True)
            assert conn.execute(
                "SELECT count(*) FROM audit_events WHERE user_id=? AND action='GLOBAL_ADMIN_RECOVERY_APPROVED'",
                [user_id],
            ).fetchone()[0] == 1
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM audit_events WHERE user_id=?", [user_id])
            conn.execute("DELETE FROM users WHERE id=?", [user_id])


def _integer_b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _JwksClient:
    payload = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get(self, *args, **kwargs):
        return _JsonResponse(self.payload)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("issuer", "OIDC_ID_TOKEN_INVALID"),
        ("audience", "OIDC_ID_TOKEN_INVALID"),
        ("nonce", "OIDC_NONCE_MISMATCH"),
        ("signature", "OIDC_ID_TOKEN_INVALID"),
        ("expired", "OIDC_ID_TOKEN_INVALID"),
    ],
)
def test_oidc_id_token_validation_fails_closed(monkeypatch: pytest.MonkeyPatch, mutation: str, expected_code: str):
    _oidc_env(monkeypatch)
    now = int(time.time())
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "use": "sig",
        "alg": "RS256",
        "n": _integer_b64(public_numbers.n),
        "e": _integer_b64(public_numbers.e),
    }
    _JwksClient.payload = {"keys": [jwk]}
    monkeypatch.setattr(oidc_service.httpx, "AsyncClient", _JwksClient)
    claims = {
        "iss": "http://localhost:9000",
        "aud": "analysis-canvas",
        "sub": "subject",
        "nonce": "expected-nonce",
        "iat": now,
        "exp": now + 300,
    }
    signing_key = private_key
    if mutation == "issuer":
        claims["iss"] = "http://evil.example.test"
    elif mutation == "audience":
        claims["aud"] = "other-client"
    elif mutation == "nonce":
        claims["nonce"] = "wrong-nonce"
    elif mutation == "expired":
        claims["exp"] = now - 10
    elif mutation == "signature":
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test-key"})

    async def validate() -> None:
        await oidc_service.validate_id_token(
            security_settings(),
            _discovery(),
            id_token=token,
            expected_nonce="expected-nonce",
        )

    with pytest.raises(OidcProtocolError) as captured:
        asyncio.run(validate())
    assert captured.value.code == expected_code
