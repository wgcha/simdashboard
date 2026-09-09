from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password


PASSWORD = "correct-horse-battery-staple"
SECRET = "managed-test-secret-key-with-at-least-32-characters"


def _create_user(username: str, *, membership: str | None = "power") -> str:
    user_id = f"managed-security-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active,
               created_at, updated_at, account_status, is_global_admin)
               VALUES (?, ?, ?, ?, 'viewer', true, ?, ?, 'ACTIVE', false)""",
            [user_id, username, hash_password(PASSWORD), username.title(), now, now],
        )
        if membership:
            conn.execute(
                """INSERT INTO project_memberships (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
                   VALUES (?, 'project-tv-001', ?, ?, 'test', ?, 'test', ?)""",
                [f"membership-{uuid4().hex[:12]}", user_id, membership, now, now],
            )
    return user_id


def _headers(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _pair(client: TestClient, headers: dict[str, str], device_id: str = "device-security-001") -> tuple[str, str, str]:
    pairing = client.post("/api/local-execution/pairing", headers=headers, json={"device_id": device_id})
    assert pairing.status_code == 200
    token = pairing.json()["pairing_token"]
    response = client.post(
        "/api/local-execution/device/pair",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_id": device_id, "host_name": "Security PC", "device_secret": "B" * 43},
    )
    assert response.status_code == 200, response.text
    session = client.post(f"/api/local-execution/devices/{response.json()['id']}/session", headers=headers)
    assert session.status_code == 200
    return response.json()["id"], session.json()["token"], "B" * 43


def _context(item: tuple[str, str]) -> dict[str, str]:
    return {"request_id": item[1], "work_item_id": item[0], "task_name": "forged", "actor": "forged"}


def _in_progress_item(owner_id: str, owner: str) -> tuple[str, str]:
    with connect() as conn:
        item = conn.execute("SELECT id, request_id FROM request_work_items WHERE status='IN_PROGRESS' ORDER BY sequence_no LIMIT 1").fetchone()
        assert item
        conn.execute("UPDATE request_work_items SET owner_user_id=?, owner=? WHERE id=?", [owner_id, owner, item[0]])
    return str(item[0]), str(item[1])


def _authorize(client: TestClient, binding_id: str, session: str, secret: str, context: dict[str, str]) -> dict:
    response = client.post(
        "/api/local-execution/device/authorize",
        headers={"Authorization": f"Bearer {secret}"},
        json={"binding_id": binding_id, "session_token": session, "action": "execute", "context": context},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _event(context: dict, grant_id: str, *, sequence: int = 1, version: str = "1.0") -> dict:
    return {
        "binding_id": "",
        "events": [{
            "sequence": sequence,
            "grant_id": grant_id,
            "run": {
                "id": "run-security-001", "source_run_id": None, "batch_id": None, "mode": "DIRECT", "status": "QUEUED",
                "program_name": "Solver", "program_version": version, "input_path": "C:/work/input.inp", "working_directory": "C:/work",
                "created_at": "2026-09-09T00:00:00+00:00", "started_at": None, "completed_at": None,
                "exit_code": None, "note": None, "error": None, "context": context,
                "program_snapshot": {"id": "program-1", "host_id": "device-security-001"},
            },
        }],
    }


def test_pairing_and_session_expiry_and_device_binding_are_fail_closed(monkeypatch):
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", SECRET)
    username = f"expiry-{uuid4().hex[:8]}"
    _create_user(username)
    with TestClient(app) as client:
        headers = _headers(client, username)
        pairing = client.post("/api/local-execution/pairing", headers=headers, json={"device_id": "device-expiry-001"}).json()
        assert client.post(
            "/api/local-execution/device/pair-preview", headers={"Authorization": f"Bearer {pairing['pairing_token']}"}, json={"device_id": "other-device"}
        ).status_code == 401
        with connect() as conn:
            conn.execute("UPDATE managed_device_pairing_tokens SET expires_at=?", [datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)])
        assert client.post(
            "/api/local-execution/device/pair-preview", headers={"Authorization": f"Bearer {pairing['pairing_token']}"}, json={"device_id": "device-expiry-001"}
        ).status_code == 401
        binding_id, session, secret = _pair(client, headers)
        with connect() as conn:
            conn.execute("UPDATE managed_device_sessions SET expires_at=? WHERE binding_id=?", [datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1), binding_id])
        denied = client.post(
            "/api/local-execution/device/authorize", headers={"Authorization": f"Bearer {secret}"},
            json={"binding_id": binding_id, "session_token": session, "action": "history"},
        )
        assert denied.status_code == 401 and denied.json()["detail"]["code"] == "DEVICE_SESSION_INVALID"
        # Pairing endpoints retain a small body ceiling, while event batches
        # can carry up to 100 immutable run snapshots in a 1 MiB envelope.
        assert client.post("/api/local-execution/device/pair", content=b"x" * (64 * 1024 + 1), headers={"Content-Type": "application/json"}).status_code == 413
        event_body = b'{"binding_id":"x","events":[]}' + b" " * (64 * 1024 + 1)
        assert client.post("/api/local-execution/device/events", content=event_body, headers={"Content-Type": "application/json"}).status_code == 422
        assert client.post("/api/local-execution/device/events", content=b"x" * (1_048_576 + 1), headers={"Content-Type": "application/json"}).status_code == 413


def test_assignment_account_and_project_boundaries_are_rechecked(monkeypatch):
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", SECRET)
    owner_name, other_name, outsider_name = (f"owner-{uuid4().hex[:8]}", f"other-{uuid4().hex[:8]}", f"outsider-{uuid4().hex[:8]}")
    owner_id = _create_user(owner_name)
    _create_user(other_name, membership="general")
    _create_user(outsider_name, membership=None)
    item = _in_progress_item(owner_id, owner_name.title())
    with TestClient(app) as client:
        owner_headers, other_headers, outsider_headers = _headers(client, owner_name), _headers(client, other_name), _headers(client, outsider_name)
        binding_id, session, secret = _pair(client, owner_headers)
        payload = {"binding_id": binding_id, "session_token": session, "action": "execute", "context": _context(item)}
        allowed = client.post("/api/local-execution/device/authorize", headers={"Authorization": f"Bearer {secret}"}, json=payload)
        assert allowed.status_code == 200
        other_binding, other_session, other_secret = _pair(client, other_headers, "device-security-other")
        other_attempt = client.post(
            "/api/local-execution/device/authorize", headers={"Authorization": f"Bearer {other_secret}"},
            json={"binding_id": other_binding, "session_token": other_session, "action": "execute", "context": _context(item)},
        )
        assert other_attempt.status_code == 403 and other_attempt.json()["detail"]["code"] == "WORK_ITEM_NOT_ASSIGNED"
        outsider_binding, outsider_session, outsider_secret = _pair(client, outsider_headers, "device-security-outsider")
        outsider_attempt = client.post(
            "/api/local-execution/device/authorize", headers={"Authorization": f"Bearer {outsider_secret}"},
            json={"binding_id": outsider_binding, "session_token": outsider_session, "action": "execute", "context": _context(item)},
        )
        assert outsider_attempt.status_code == 403 and outsider_attempt.json()["detail"]["code"] == "PROJECT_MEMBERSHIP_REQUIRED"
        with connect() as conn:
            conn.execute("UPDATE request_work_items SET owner_user_id=? WHERE id=?", [_create_user(f"replacement-{uuid4().hex[:8]}"), item[0]])
        denied = client.post("/api/local-execution/device/authorize", headers={"Authorization": f"Bearer {secret}"}, json=payload)
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "WORK_ITEM_NOT_ASSIGNED"
        # The existing PROJECT_DATA_VIEW policy intentionally grants active
        # company accounts read access without a project membership. The route
        # still checks the request/work-item relation and this policy helper.
        assert client.get(f"/api/local-execution/runs?request_id={item[1]}&work_item_id={item[0]}", headers=outsider_headers).status_code == 200
        with connect() as conn:
            conn.execute("UPDATE users SET account_status='SUSPENDED' WHERE id=?", [owner_id])
        suspended = client.post("/api/local-execution/device/authorize", headers={"Authorization": f"Bearer {secret}"}, json=payload)
        assert suspended.status_code == 403 and suspended.json()["detail"]["code"] == "ACCOUNT_NOT_ACTIVE"
        assert client.get("/api/local-execution/devices", headers=other_headers).status_code == 200


def test_forged_grant_context_sequence_and_immutable_run_are_rejected(monkeypatch):
    initialize_database()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", SECRET)
    username = f"events-{uuid4().hex[:8]}"
    user_id = _create_user(username)
    item = _in_progress_item(user_id, username.title())
    with TestClient(app) as client:
        headers = _headers(client, username)
        binding_id, session, secret = _pair(client, headers)
        grant = _authorize(client, binding_id, session, secret, _context(item))
        event = _event(grant["context"], grant["grant_id"])
        event["binding_id"] = binding_id
        response = client.post("/api/local-execution/device/events", headers={"Authorization": f"Bearer {secret}"}, json=event)
        assert response.status_code == 200
        forged = _event({**grant["context"], "actor": "forged"}, grant["grant_id"], sequence=2)
        forged["binding_id"] = binding_id
        assert client.post("/api/local-execution/device/events", headers={"Authorization": f"Bearer {secret}"}, json=forged).status_code == 403
        immutable = _event(grant["context"], grant["grant_id"], sequence=2, version="2.0")
        immutable["binding_id"] = binding_id
        assert client.post("/api/local-execution/device/events", headers={"Authorization": f"Bearer {secret}"}, json=immutable).status_code == 409
        conflict = _event(grant["context"], grant["grant_id"], sequence=1)
        conflict["binding_id"] = binding_id
        conflict["events"][0]["run"]["note"] = "changed duplicate"
        assert client.post("/api/local-execution/device/events", headers={"Authorization": f"Bearer {secret}"}, json=conflict).status_code == 409
