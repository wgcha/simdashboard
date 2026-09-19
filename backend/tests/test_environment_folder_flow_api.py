"""Cross-feature contracts: a folder plan must lead to usable dashboard data."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app

pytestmark = pytest.mark.duckdb_integration
BASE = "/api/folder-discovery/environments"


@pytest.fixture
def admin_client(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "environment-flow-isolated-test-secret-at-least-32")
    _, username, password = password_auth_bootstrap_admin
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield client, root


def post(client, route, payload):
    response = client.post(BASE + route, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def usage_files(case):
    values = {
        "Settle/model_settle_result.json": {"Set Tilt Angle @ Settle (deg)": 1.18},
        "Wobble/model_wobble_center_front_result.json": {"Wobble Disp. (mm)": -2.5},
        "Horizontal_Force_Angle/model_horizontal_force_angle_front_result.json": {"Set Tilt Angle Difference (deg)": 3.0},
        "Slope_Angle/model_slope_angle_front_result.json": {"Slope Angle (deg)": 8.0, "OK/NG": "OK"},
        "Slope_Angle_360/model_slope_angle_front_360_result.json": {"OK/NG": "NG"},
    }
    for relative, data in values.items():
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


def test_standard_usage_plan_registers_real_business_and_dashboard_without_load_cases(admin_client):
    client, root = admin_client
    case_path = "Project_9001_Flow/WR_9001_SimType1/Assy_RES_Model_SetCase1"
    usage_files(root / case_path)
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    key = f"flow-{uuid4()}"
    registration = post(client, "/registrations", {"preview_id": preview["id"], "idempotency_key": key, "capture": True})
    jobs = registration["capture_jobs"]
    assert len(jobs) == 1, registration
    assert jobs[0]["status"] == "COMPLETED", registration
    case_id, capture_id = jobs[0]["case_id"], jobs[0]["capture_id"]
    with connect() as conn:
        case = conn.execute("SELECT project_id,request_id,relative_path FROM dashboard_cases WHERE id=?", [case_id]).fetchone()
        assert case and case[2] == case_path
        assert conn.execute("SELECT name FROM projects WHERE id=?", [case[0]]).fetchone()[0] == "Project_9001_Flow"
        assert conn.execute("SELECT title FROM analysis_requests WHERE id=?", [case[1]]).fetchone()[0] == "WR_9001_SimType1"
        assert conn.execute("SELECT count(*) FROM load_cases WHERE request_id=?", [case[1]]).fetchone()[0] == 0
    response = client.get(f"/api/dashboard/usage/cases/{case_id}", params={"capture_id": capture_id})
    assert response.status_code == 200, response.text
    assert len(response.json()["evaluations"]) == 5
    repeated = post(client, "/registrations", {"preview_id": preview["id"], "idempotency_key": key, "capture": True})
    assert repeated["registration_id"] == registration["registration_id"]
    assert repeated["capture_jobs"][0]["capture_id"] == capture_id
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [case_id]).fetchone()[0] == 1


def test_existing_request_can_register_case_root_and_changed_tree_cannot_apply_old_preview(admin_client):
    client, root = admin_client
    with connect() as conn:
        project_id, request_id = conn.execute("SELECT project_id,id FROM analysis_requests ORDER BY id LIMIT 1").fetchone()
    usage_files(root / "Assy_RES_Existing")
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": "Assy_RES_Existing", "project_id": project_id, "request_id": request_id})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    (root / "Assy_RES_Existing" / "new-unreviewed-folder").mkdir()
    denied = client.post(BASE + "/registrations", json={"preview_id": preview["id"], "idempotency_key": f"stale-{uuid4()}", "capture": True})
    assert denied.status_code in (409, 422), denied.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_cases WHERE relative_path='Assy_RES_Existing'").fetchone()[0] == 0


def test_distribution_registration_keeps_named_unknown_separate_from_no_option(admin_client):
    client, root = admin_client
    case_path = "Project_9002_Flow/WR_9002_SimType2/Package_SetCase1_CushionCase1"
    scene = "1_Face_Drop_Scene01_Face1_1st"
    filename = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
    for option, value in (("", 10), ("UNKNOWN", 20), ("Custom Fatigue", 30)):
        path = root / case_path / "Drop" / "Run01" / option / scene / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,{value},{value},{value},{value}\n", encoding="utf-8")
    scan = post(client, "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    assignments = [
        {"node_id": node["id"], "role_kind": "RUN_OPTION", "confirm": True, "target_mode": "CREATE"}
        for node in scan["nodes"] if node["name"] in ("UNKNOWN", "Custom Fatigue")
    ]
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": assignments})
    assert preview["can_apply"], preview
    registration = post(client, "/registrations", {"preview_id": preview["id"], "idempotency_key": f"distribution-{uuid4()}", "capture": True})
    assert len(registration["capture_jobs"]) == 1, registration
    job = registration["capture_jobs"][0]
    assert job["status"] == "COMPLETED", registration
    with connect() as conn:
        request_id = conn.execute("SELECT request_id FROM dashboard_cases WHERE id=?", [job["case_id"]]).fetchone()[0]
    response = client.get("/api/dashboard/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    catalog = response.json()
    options = catalog["run_options"]
    assert len(options) == 3, options
    assert {option["option_status"] for option in options} == {"ABSENT", "PRESENT"}
    assert {option["label"] for option in options} == {"옵션 없음", "UNKNOWN", "Custom Fatigue"}
    assert len({option["id"] for option in options}) == 3
    results = {}
    for option in options:
        result = client.get(f"/api/dashboard/distribution/runs/{option['execution_run_id']}", params={
            "capture_id": job["capture_id"], "mode": option["mode"], "run_option_id": option["id"],
            "component_id": "C23", "basis": "REPORTED_SUMMARY", "edge_keys": "TOP",
        })
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["context"]["run_option_id"] == option["id"]
        results[option["label"]] = payload["series"][0]["value"]
    assert results == {"옵션 없음": 10, "UNKNOWN": 20, "Custom Fatigue": 30}


def test_saved_profile_revision_is_used_and_old_preview_cannot_apply_after_edit(admin_client):
    client, root = admin_client
    usage_files(root / "Project_9003_Flow" / "WR_9003_SimType1" / "Model Custom")
    definition = {"environment": "USAGE", "name": f"Custom usage {uuid4()}", "rules": {"rules": [
        {"role_kind": "SIMULATION_CASE", "parent_role": "REQUEST", "pattern": "Model*", "match_mode": "glob"},
    ]}}
    profile = post(client, "/profiles", definition)
    assert profile["revision"] == 1
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": "", "profile_id": profile["id"]})
    custom = next(node for node in scan["nodes"] if node["name"] == "Model Custom")
    assert custom["role_kind"] == "SIMULATION_CASE"
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    updated = client.put(BASE + f"/profiles/{profile['id']}", json={**definition, "expected_revision": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    conflict = client.put(BASE + f"/profiles/{profile['id']}", json={**definition, "expected_revision": 1})
    assert conflict.status_code == 409, conflict.text
    stale = client.post(BASE + "/registrations", json={"preview_id": preview["id"], "idempotency_key": f"revision-{uuid4()}", "capture": True})
    assert stale.status_code in (409, 422), stale.text


def test_legacy_copy_preserves_original_and_requires_case_review(admin_client):
    client, root = admin_client
    from app.services.folder_discovery import root_identity
    original = [{"depth": 1, "role": "PROJECT", "keyword": "Project_", "delimiter": "_", "code_token": 2, "name_from_token": 3},
                {"depth": 3, "role": "LOAD_CASE", "keyword": "LC_", "delimiter": "_", "code_token": 2, "name_from_token": 3, "analysis_type": "SPDM_CMS"}]
    with connect() as conn:
        conn.execute("INSERT INTO folder_discovery_rules VALUES (?, '', ?, 1, CURRENT_TIMESTAMP, 'test')", [root_identity(root), json.dumps(original)])
    profile = post(client, "/profiles/from-legacy", {"environment": "USAGE", "name": f"Copied legacy {uuid4()}", "relative_path": ""})
    assert profile["requires_review"]
    assert [r["role_kind"] for r in profile["rules"]["rules"]] == ["PROJECT"]
    assert len(profile["omitted_rules"]) == 1
    with connect() as conn:
        stored = conn.execute("SELECT rules_json,revision FROM folder_discovery_rules WHERE root_key=?", [root_identity(root)]).fetchone()
        assert (json.loads(stored[0]) if isinstance(stored[0], str) else stored[0]) == original
        assert stored[1] == 1
