from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password


PASSWORD = "correct-horse-battery-staple"


def _user(username: str) -> str:
    user_id = f"managed-user-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name, legacy_role, is_active,
               created_at, updated_at, account_status, is_global_admin)
               VALUES (?, ?, ?, ?, 'editor', true, ?, ?, 'ACTIVE', false)""",
            [user_id, username, hash_password(PASSWORD), username.title(), now, now],
        )
        conn.execute(
            """INSERT INTO project_memberships (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
               VALUES (?, 'project-tv-001', ?, 'power', 'test', ?, 'test', ?)""",
            [f"managed-membership-{uuid4().hex[:12]}", user_id, now, now],
        )
    return user_id


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _run(status: str = "QUEUED") -> dict:
    return {
        "id": "run-managed-test-1",
        "source_run_id": None,
        "batch_id": None,
        "mode": "DIRECT",
        "status": status,
        "program_name": "Solver",
        "program_version": "1.0",
        "input_path": "C:/work/input.inp",
        "working_directory": "C:/work",
        "created_at": "2026-09-09T00:00:00+00:00",
        "started_at": None,
        "completed_at": None,
        "exit_code": None,
        "note": None,
        "error": None,
        "context": {"request_id": "", "work_item_id": "", "task_name": "forged", "actor": "forged"},
        "program_snapshot": {"id": "program-1", "host_id": "device-managed-001"},
    }


def test_managed_device_pairing_authorization_events_and_revoke(monkeypatch):
    initialize_database()
    suffix = uuid4().hex[:8]
    username = f"managed-{suffix}"
    other_username = f"managed-other-{suffix}"
    user_id = _user(username)
    _user(other_username)
    with connect() as conn:
        item = conn.execute(
            "SELECT id, request_id FROM request_work_items WHERE status='IN_PROGRESS' ORDER BY sequence_no LIMIT 1"
        ).fetchone()
        assert item
        conn.execute("UPDATE request_work_items SET owner_user_id=?, owner=? WHERE id=?", [user_id, username.title(), item[0]])
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "managed-test-secret-key-with-at-least-32-characters")
    with TestClient(app) as client:
            headers = _login(client, username)
            other_headers = _login(client, other_username)
            pairing = client.post("/api/local-execution/pairing", headers=headers, json={"device_id": "device-managed-001"})
            assert pairing.status_code == 200
            assert pairing.json()["expires_at"].endswith("Z")
            preview = client.post(
                "/api/local-execution/device/pair-preview",
                headers={"Authorization": f"Bearer {pairing.json()['pairing_token']}"},
                json={"device_id": "device-managed-001"},
            )
            assert preview.status_code == 200 and preview.json()["user_id"] == user_id
            paired = client.post(
                "/api/local-execution/device/pair",
                headers={"Authorization": f"Bearer {pairing.json()['pairing_token']}"},
                json={"device_id": "device-managed-001", "host_name": "Test PC", "device_secret": "A" * 43},
            )
            assert paired.status_code == 200
            binding_id = paired.json()["id"]
            assert client.post(
                "/api/local-execution/device/pair",
                headers={"Authorization": f"Bearer {pairing.json()['pairing_token']}"},
                json={"device_id": "device-managed-001", "host_name": "Test PC", "device_secret": "A" * 43},
            ).status_code == 401
            assert client.post(f"/api/local-execution/devices/{binding_id}/session", headers=other_headers).status_code == 403
            session = client.post(f"/api/local-execution/devices/{binding_id}/session", headers=headers)
            assert session.status_code == 200 and session.json()["expires_at"].endswith("Z")
            authorization = client.post(
                "/api/local-execution/device/authorize",
                headers={"Authorization": "Bearer " + "A" * 43},
                json={
                    "binding_id": binding_id,
                    "session_token": session.json()["token"],
                    "action": "execute",
                    "context": {"request_id": item[1], "work_item_id": item[0], "task_name": "forged", "actor": "forged"},
                },
            )
            assert authorization.status_code == 200, authorization.text
            context = authorization.json()["context"]
            assert context["actor"] == username.title() and context["task_name"] != "forged"
            run = _run()
            run["context"] = context
            event = {"binding_id": binding_id, "events": [{"sequence": 1, "grant_id": authorization.json()["grant_id"], "run": run}]}
            accepted = client.post("/api/local-execution/device/events", headers={"Authorization": "Bearer " + "A" * 43}, json=event)
            assert accepted.status_code == 200
            assert client.post("/api/local-execution/device/events", headers={"Authorization": "Bearer " + "A" * 43}, json=event).status_code == 200
            run["status"] = "RUNNING"
            event["events"][0]["sequence"] = 2
            assert client.post("/api/local-execution/device/events", headers={"Authorization": "Bearer " + "A" * 43}, json=event).status_code == 200
            run["status"] = "QUEUED"
            event["events"][0]["sequence"] = 3
            assert client.post("/api/local-execution/device/events", headers={"Authorization": "Bearer " + "A" * 43}, json=event).status_code == 409
            central = client.get(f"/api/local-execution/runs?request_id={item[1]}&work_item_id={item[0]}", headers=headers)
            assert central.status_code == 200 and central.json()[0]["actor_user_id"] == user_id
            assert client.get(f"/api/local-execution/runs?request_id={item[1]}&work_item_id={item[0]}", headers=other_headers).status_code == 200
            assert client.post(f"/api/local-execution/devices/{binding_id}/revoke", headers=headers).status_code == 200
            assert client.post("/api/local-execution/device/events", headers={"Authorization": "Bearer " + "A" * 43}, json=event).status_code == 403
