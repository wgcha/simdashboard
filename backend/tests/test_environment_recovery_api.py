"""Atomic registration/capture recovery contracts for environment folder flows."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.services import dashboard_capture, folder_discovery_environment


pytestmark = pytest.mark.duckdb_integration
BASE = "/api/folder-discovery/environments"


@pytest.fixture
def admin_client(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "environment-recovery-test-secret-at-least-32")
    _, username, password = password_auth_bootstrap_admin
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        yield client, root


def post(client, path, payload):
    response = client.post(BASE + path, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def usage_tree(root, suffix="recovery"):
    case = root / f"Project_{suffix}" / f"WR_{suffix}_SimType1" / f"Assy_RES_{suffix}"
    result = case / "Settle" / "model_settle_result.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({"Set Tilt Angle @ Settle (deg)": 1.5}), encoding="utf-8")
    return case


def prepared_preview(client, root, suffix="recovery"):
    usage_tree(root, suffix)
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    return preview


def test_capture_failure_commits_registration_and_retry_reuses_saved_context(admin_client, monkeypatch):
    client, root = admin_client
    preview = prepared_preview(client, root)
    original = dashboard_capture.create_capture
    payloads = []

    def fail_first(conn, payload, *, actor):
        payloads.append(dict(payload))
        if len(payloads) == 1:
            raise dashboard_capture.DashboardCaptureError("INJECTED_CAPTURE_FAILURE", "injected")
        return original(conn, payload, actor=actor)

    monkeypatch.setattr(folder_discovery_environment.dashboard_capture, "create_capture", fail_first)
    key = f"recovery-{uuid4()}"
    registration = post(client, "/registrations", {"preview_id": preview["id"], "idempotency_key": key, "capture": True})
    assert registration["capture_jobs"][0]["status"] == "FAILED"
    assert registration["capture_jobs"][0]["error_code"] == "INJECTED_CAPTURE_FAILURE"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM folder_environment_registrations WHERE id=?", [registration["registration_id"]]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM folder_environment_registry WHERE registration_id=?", [registration["registration_id"]]).fetchone()[0] > 0
        assert conn.execute("""SELECT count(*) FROM dashboard_captures c
            JOIN folder_environment_capture_jobs j ON j.case_id=c.case_id
            WHERE j.registration_id=?""", [registration["registration_id"]]).fetchone()[0] == 0

    retried = post(client, f"/registrations/{registration['registration_id']}/capture/retry", {})
    assert retried["capture_jobs"][0]["status"] == "COMPLETED", retried
    assert retried["capture_jobs"][0]["capture_id"]
    for key in ("project_id", "request_id", "root_relative_path", "environment", "simulation_case_id",
                "run_option_labels", "hierarchy_assignments", "rule_profile_id", "rule_profile_version"):
        assert payloads[1].get(key) == payloads[0].get(key), key


def test_same_idempotency_key_for_different_preview_is_rejected(admin_client):
    client, root = admin_client
    first = prepared_preview(client, root, "idem1")
    key = f"same-key-{uuid4()}"
    created = post(client, "/registrations", {"preview_id": first["id"], "idempotency_key": key, "capture": False})
    second_scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    second = post(client, "/previews", {"scan_id": second_scan["id"], "assignments": []})
    response = client.post(BASE + "/registrations", json={"preview_id": second["id"], "idempotency_key": key, "capture": False})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ENVIRONMENT_IDEMPOTENCY_CONFLICT"
    assert created["preview_id"] == first["id"]


def test_link_to_unrelated_target_kind_is_rejected(admin_client):
    client, root = admin_client
    usage_tree(root, "link")
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    project_node = next(node for node in scan["nodes"] if node["role_kind"] == "PROJECT")
    with connect() as conn:
        unrelated_request = conn.execute("SELECT id FROM analysis_requests ORDER BY id LIMIT 1").fetchone()[0]
    response = client.post(BASE + "/previews", json={"scan_id": scan["id"], "assignments": [{
        "node_id": project_node["id"], "role_kind": "PROJECT", "target_mode": "LINK", "target_id": unrelated_request,
    }]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "ENVIRONMENT_PREVIEW_INVALID"


def test_mixed_simtype_request_cannot_apply_under_wrong_environment(admin_client):
    client, root = admin_client
    # The folder name declares distribution while the selected resolver is usage.
    case = root / "Project_mixed" / "WR_mixed_SimType2" / "Assy_RES_mixed"
    result = case / "Settle" / "model_settle_result.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({"Set Tilt Angle @ Settle (deg)": 1}), encoding="utf-8")
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert not preview["can_apply"]
    assert preview["unresolved_count"] > 0


def test_invalid_distribution_parent_chain_cannot_apply(admin_client):
    client, root = admin_client
    scene = root / "Project_parent" / "WR_parent_SimType2" / "Package_parent" / "Drop" / "Run01" / "1_Face_Drop_Scene01"
    scene.mkdir(parents=True)
    (scene / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv").write_text("Position,Layer_1\nTOP,1\n", encoding="utf-8")
    scan = post(client, "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    load = next(node for node in scan["nodes"] if node["name"] == "Drop")
    response = client.post(BASE + "/previews", json={"scan_id": scan["id"], "assignments": [{
        "node_id": load["id"], "role_kind": "EXECUTION_RUN", "target_mode": "CREATE",
    }]})
    assert response.status_code == 422 or not response.json()["can_apply"]
