"""Cross-feature contracts: a folder plan must lead to usable dashboard data."""
from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.services import dashboard_capture, folder_schema_locations, materials_catalog, result_registration
from app.services.folder_schema_locations import EnvironmentLocations

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


def test_schema_scoped_empty_projection_fails_closed_and_legacy_remains_compatible():
    scoped = {"folder_schema_scoped": True, "folder_schema_snapshot_id": "snapshot-test",
              "folder_schema_locations": []}
    result_path = "Case/Evaluation/results/result.json"
    assert not dashboard_capture._schema_allows_file(scoped, "Case", result_path)
    with pytest.raises(dashboard_capture.DashboardCaptureError):
        dashboard_capture._assert_schema_allows_approved_files(
            scoped, [(result_path, b"{}", "application/json")],
        )
    assert dashboard_capture._schema_allows_file({}, "Case", result_path)

    blocked = {**scoped, "folder_schema_locations": [{
        "relative_path": "Case/Evaluation/results", "role_kind": "RESULTS", "status": "CONFIRMED",
    }], "folder_schema_blocked_paths": ["Case/Evaluation/results/Excluded"]}
    assert not dashboard_capture._schema_allows_file(
        blocked, "Case", "Case/Evaluation/results/Excluded/hidden.json",
    )
    with pytest.raises(dashboard_capture.DashboardCaptureError):
        dashboard_capture._assert_schema_allows_approved_files(
            blocked, [("Case/Evaluation/results/Excluded/hidden.json", b"{}", "application/json")],
        )


def test_environment_locations_exposes_snapshot_contract_and_label_alias():
    schema = {
        "project_id": "project", "request_id": "request", "environment": "DISTRIBUTION",
        "request_relative_path": "Project/Request", "nodes": [],
        "scan": {"id": "scan-1", "profile_id": "profile-1", "profile_revision": 3},
        "profile": {"id": "profile-1", "revision": 3},
        "structure_fingerprint": "structure-hash", "content_fingerprint": "content-hash",
    }
    projection = EnvironmentLocations(schema, ({"name": "Scene A", "relative_path": "Project/Request/Scene A"},))
    assert (projection.scan_id, projection.profile_id, projection.profile_revision) == ("scan-1", "profile-1", 3)
    assert projection.structure_fingerprint == "structure-hash"
    assert projection.content_fingerprint == "content-hash"
    serialized = projection.as_dict()
    assert {key: serialized[key] for key in (
        "scan_id", "profile_id", "profile_revision", "structure_fingerprint", "content_fingerprint",
    )} == {
        "scan_id": "scan-1", "profile_id": "profile-1", "profile_revision": 3,
        "structure_fingerprint": "structure-hash", "content_fingerprint": "content-hash",
    }
    assert serialized["locations"][0]["label"] == "Scene A"


def test_materials_catalog_accepts_refresh_scene_evidence_bases():
    assert materials_catalog._schema_scene_role({"role_source": "PROFILE", "role_basis": "PATTERN"})
    assert materials_catalog._schema_scene_role({"role_source": "INHERITED", "role_basis": "LEVEL"})


def test_result_registration_current_target_blocks_excluded_nested_result_files(admin_client):
    client, root = admin_client
    case_path = "Project_9048_ResultExcluded/WR_9048_SimType1/Assy_RES_Model_SetCase1"
    usage_files(root / case_path)
    blocked_path = f"{case_path}/Settle/results/private"
    blocked_file = f"{blocked_path}/approved.csv"
    destination = root / blocked_file
    destination.parent.mkdir(parents=True)
    destination.write_text("secret,result\n1,2\n", encoding="utf-8")

    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    excluded = next(node for node in scan["nodes"] if node["relative_path"] == blocked_path)
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": excluded["id"], "role_kind": "EXCLUDE", "confirm": True},
    ]})
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"result-exclude-{uuid4()}", "capture": False,
    })
    with connect() as conn:
        target = result_registration._current_target(
            conn, project_id=registration["project_id"], request_id=registration["request_id"],
            environment="USAGE", case_relative_path=case_path,
            result_relative_path=f"{case_path}/Settle/results",
        )

    row = {"context_json": {"simulation_case": {"id": "case-id"}},
           "project_id": registration["project_id"], "request_id": registration["request_id"],
           "case_relative_path": case_path, "environment": "USAGE",
           "storage_root_id": target["root_id"]}
    payload = result_registration._capture_payload(row, target, {}, [], [])
    assert payload["folder_schema_blocked_paths"] == [blocked_path]
    assert payload["folder_schema_scoped"] is True
    with pytest.raises(dashboard_capture.DashboardCaptureError):
        dashboard_capture._assert_schema_allows_approved_files(
            payload, [(blocked_file, b"secret,result\n1,2\n", "text/csv")],
        )


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


def test_capture_and_retry_exclude_files_under_excluded_schema_descendants(admin_client, monkeypatch):
    client, root = admin_client
    case_path = "Project_9042_Excluded/WR_9042_SimType1/Assy_RES_Model_SetCase1"
    usage_files(root / case_path)
    blocked_path = f"{case_path}/Settle/Excluded_Output"
    blocked_file = f"{blocked_path}/results/should_not_be_captured.json"
    destination = root / blocked_file
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps({"excluded": True}), encoding="utf-8")
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    excluded = next(node for node in scan["nodes"] if node["relative_path"] == blocked_path)
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": excluded["id"], "role_kind": "EXCLUDE", "confirm": True},
    ]})
    original_capture = dashboard_capture.create_capture
    attempts = 0

    def fail_first_capture(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise dashboard_capture.DashboardCaptureError("INJECTED_FAILURE", "retry regression fixture")
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(dashboard_capture, "create_capture", fail_first_capture)
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"excluded-capture-{uuid4()}", "capture": True,
    })
    assert registration["capture_jobs"][0]["status"] == "FAILED"
    monkeypatch.setattr(dashboard_capture, "create_capture", original_capture)
    retried = post(client, f"/registrations/{registration['registration_id']}/capture/retry", {})
    job = retried["capture_jobs"][0]
    assert job["status"] == "COMPLETED"
    with connect() as conn:
        manifest = json.loads(conn.execute(
            "SELECT manifest_json FROM dashboard_captures WHERE id=?", [job["capture_id"]],
        ).fetchone()[0])
    manifest_paths = {item["relative_path"] for item in manifest}
    assert f"{case_path}/Settle/model_settle_result.json" in manifest_paths
    assert blocked_file not in manifest_paths


def test_registration_and_retry_use_new_manual_exclusions_after_active_snapshot(admin_client, monkeypatch):
    client, root = admin_client
    case_path = "Project_9047_ReregisterCapture/WR_9047_SimType1/Assy_RES_Model_SetCase1"
    usage_files(root / case_path)
    first_scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    first_preview = post(client, "/previews", {"scan_id": first_scan["id"], "assignments": []})
    first_registration = post(client, "/registrations", {
        "preview_id": first_preview["id"], "idempotency_key": f"seed-{uuid4()}", "capture": True,
    })
    assert first_registration["capture_jobs"][0]["status"] == "COMPLETED"
    case_id = first_registration["capture_jobs"][0]["case_id"]
    with connect() as conn:
        project_id, request_id = conn.execute(
            "SELECT project_id,request_id FROM folder_environment_registrations WHERE id=?",
            [first_registration["registration_id"]],
        ).fetchone()
    active = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert active.status_code == 200 and active.json()["status"] == "REFRESHED", active.text

    slope_path = f"{case_path}/Slope_Angle"
    slope_secret = f"{slope_path}/private_after_refresh.json"
    slope_file = root / slope_secret
    slope_file.write_text(json.dumps({"private": "slope"}), encoding="utf-8")
    slope_scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    slope_node = next(item for item in slope_scan["nodes"] if item["relative_path"] == slope_path)
    slope_preview = post(client, "/previews", {"scan_id": slope_scan["id"], "assignments": [
        {"node_id": slope_node["id"], "role_kind": "EXCLUDE", "confirm": True},
    ]})
    slope_registration = post(client, "/registrations", {
        "preview_id": slope_preview["id"], "idempotency_key": f"exclude-slope-{uuid4()}", "capture": True,
    })
    assert slope_registration["capture_jobs"][0]["status"] == "COMPLETED"
    with connect() as conn:
        slope_manifest = json.loads(conn.execute(
            "SELECT manifest_json FROM dashboard_captures WHERE id=?",
            [slope_registration["capture_jobs"][0]["capture_id"]],
        ).fetchone()[0])
    assert slope_secret not in {item["relative_path"] for item in slope_manifest}

    wobble_path = f"{case_path}/Wobble"
    wobble_secret = f"{wobble_path}/private_after_refresh.json"
    (root / wobble_secret).write_text(json.dumps({"private": "wobble"}), encoding="utf-8")
    wobble_scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    wobble_node = next(item for item in wobble_scan["nodes"] if item["relative_path"] == wobble_path)
    wobble_preview = post(client, "/previews", {"scan_id": wobble_scan["id"], "assignments": [
        {"node_id": wobble_node["id"], "role_kind": "EXCLUDE", "confirm": True},
    ]})
    original_capture = dashboard_capture.create_capture

    def fail_capture(*_args, **_kwargs):
        raise dashboard_capture.DashboardCaptureError("INJECTED_FAILURE", "registration retry regression")

    monkeypatch.setattr(dashboard_capture, "create_capture", fail_capture)
    wobble_registration = post(client, "/registrations", {
        "preview_id": wobble_preview["id"], "idempotency_key": f"exclude-wobble-{uuid4()}", "capture": True,
    })
    assert wobble_registration["capture_jobs"][0]["status"] == "FAILED"
    monkeypatch.setattr(dashboard_capture, "create_capture", original_capture)
    retried = post(client, f"/registrations/{wobble_registration['registration_id']}/capture/retry", {})
    assert retried["capture_jobs"][0]["status"] == "COMPLETED"
    with connect() as conn:
        retry_manifest = json.loads(conn.execute(
            "SELECT manifest_json FROM dashboard_captures WHERE id=?",
            [retried["capture_jobs"][0]["capture_id"]],
        ).fetchone()[0])
    retry_paths = {item["relative_path"] for item in retry_manifest}
    assert slope_secret not in retry_paths
    assert wobble_secret not in retry_paths
    assert f"{case_path}/Settle/model_settle_result.json" in retry_paths
    refreshed = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == "REFRESHED"
    refreshed_nodes = {item["relative_path"]: item for item in refreshed.json()["nodes"]}
    assert refreshed_nodes[slope_path]["status"] == "EXCLUDED"
    assert refreshed_nodes[wobble_path]["status"] == "EXCLUDED"
    assert not any(item["relative_path"] in {slope_path, wobble_path}
                   for item in refreshed.json()["locations"])
    with connect() as conn:
        latest_manifest = json.loads(conn.execute(
            "SELECT manifest_json FROM dashboard_captures WHERE case_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            [case_id],
        ).fetchone()[0])
    latest_paths = {item["relative_path"] for item in latest_manifest}
    assert slope_secret not in latest_paths
    assert wobble_secret not in latest_paths
    assert case_id == retried["capture_jobs"][0]["case_id"]


def test_scoped_refresh_projects_locations_and_inherits_unambiguous_level(admin_client):
    client, root = admin_client
    case_path = "Project_9043_Refresh/WR_9043_SimType1/Assy_RES_Model_SetCase1"
    other_case_path = "Project_9043_Refresh/WR_9043_SimType1/Assy_RES_Model_SetCase2"
    usage_files(root / case_path)
    (root / case_path / "Settle" / "results").mkdir(parents=True, exist_ok=True)
    usage_files(root / other_case_path)
    excluded_other_path = f"{other_case_path}/Settle/Excluded_Output"
    (root / excluded_other_path).mkdir()
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    excluded_other = next(node for node in scan["nodes"]
                          if node["relative_path"] == excluded_other_path)
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": excluded_other["id"], "role_kind": "EXCLUDE", "confirm": True},
    ]})
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"refresh-seed-{uuid4()}", "capture": True,
    })
    with connect() as conn:
        project_id, request_id = conn.execute(
            "SELECT project_id,request_id FROM folder_environment_registrations WHERE id=?",
            [registration["registration_id"]],
        ).fetchone()
        case_ids = {str(path): str(case_id) for case_id, path in conn.execute(
            "SELECT id,relative_path FROM dashboard_cases WHERE request_id=?", [request_id],
        ).fetchall()}
    case_id = case_ids[case_path]
    other_case_id = case_ids[other_case_path]

    added_path = f"{case_path}/Custom_Evaluation"
    (root / added_path).mkdir(parents=True)
    first_response = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    refresh_preview = client.post(BASE + "/previews", json={"scan_id": first["snapshot_id"]})
    assert refresh_preview.status_code == 422, refresh_preview.text
    added = next(node for node in first["nodes"] if node["relative_path"] == added_path)
    assert (added["role_kind"], added["status"], added["role_basis"]) == ("EVALUATION", "CONFIRMED", "LEVEL")
    assert first["locations"]
    assert any(location["relative_path"] == added_path for location in first["locations"])
    added_location = next(location for location in first["locations"] if location["relative_path"] == added_path)
    assert added_location["id"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [case_id]).fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [other_case_id]).fetchone()[0] == 1

    empty_path = f"{excluded_other_path}/Archive"
    (root / empty_path).mkdir()
    unrelated_change = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert unrelated_change.status_code == 200, unrelated_change.text
    unrelated = unrelated_change.json()
    assert unrelated["status"] == "REFRESHED"
    assert unrelated["snapshot_id"] != first["snapshot_id"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [case_id]).fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [other_case_id]).fetchone()[0] == 1

    repeated_response = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert repeated_response.status_code == 200, repeated_response.text
    repeated = repeated_response.json()
    assert repeated["status"] == "UNCHANGED"
    assert repeated["snapshot_id"] == unrelated["snapshot_id"]
    assert repeated["structure_fingerprint"] == unrelated["structure_fingerprint"]
    assert repeated["content_fingerprint"] == unrelated["content_fingerprint"]

    source = root / case_path / "Settle" / "model_settle_result.json"
    previous_stat = source.stat()
    source.write_text(json.dumps({"Set Tilt Angle @ Settle (deg)": 1.19}), encoding="utf-8")
    os.utime(source, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
    changed_response = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert changed_response.status_code == 200, changed_response.text
    changed = changed_response.json()
    assert changed["content_fingerprint"] != unrelated["content_fingerprint"]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [case_id]).fetchone()[0] >= 3
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [other_case_id]).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [case_id]).fetchone()[0] == \
            conn.execute("SELECT count(DISTINCT fingerprint) FROM dashboard_captures WHERE case_id=?", [case_id]).fetchone()[0]
        profile_id = conn.execute(
            "SELECT profile_id FROM folder_environment_scans WHERE id=?", [first["snapshot_id"]],
        ).fetchone()[0]
        conn.execute("UPDATE folder_environment_profiles SET revision=revision+1 WHERE id=?", [profile_id])
    revision_changed = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert revision_changed.status_code == 409, revision_changed.text
    assert revision_changed.json()["detail"]["code"] == "FOLDER_SCHEMA_PROFILE_REVISION_CHANGED"
    with connect() as conn:
        from app.services import folder_schema_resolver
        locations = folder_schema_resolver.resolve_request_locations(conn, project_id, request_id, "USAGE")
        assert locations.snapshot_id == changed["snapshot_id"]
    re_registration_scan = post(client, "/scan", {
        "environment": "USAGE", "relative_path": "", "project_id": project_id, "request_id": request_id,
    })
    re_registration_preview = post(client, "/previews", {"scan_id": re_registration_scan["id"]})
    post(client, "/registrations", {
        "preview_id": re_registration_preview["id"],
        "idempotency_key": f"refresh-reregistration-{uuid4()}", "capture": True,
    })
    re_registered = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert re_registered.status_code == 200, re_registered.text
    assert re_registered.json()["status"] == "REFRESHED"
    assert re_registered.json()["snapshot_id"] != changed["snapshot_id"]


def test_distribution_capture_and_materials_share_confirmed_scene_identity(admin_client):
    client, root = admin_client
    case_path = "Project_9044_Shared/WR_9044_SimType2/Package_SetCase1_CushionCase1"
    scene = "1_Face_Drop_Scene01_Face1_1st"
    scene_path = root / case_path / "Drop" / "Run01" / "Individual" / scene
    result_path = scene_path / "results"
    result_path.mkdir(parents=True)
    (result_path / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv").write_text(
        "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,10,10,10\n", encoding="utf-8",
    )
    (root / case_path / "Drop" / "Run01" / "Individual" / "Scene_Empty").mkdir()
    scan = post(client, "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    registered = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"shared-{uuid4()}", "capture": True,
    })
    assert registered["capture_jobs"][0]["status"] == "COMPLETED"
    with connect() as conn:
        request_id = conn.execute(
            "SELECT request_id FROM dashboard_cases WHERE id=?", [registered["capture_jobs"][0]["case_id"]],
        ).fetchone()[0]
    new_scene_path = root / case_path / "Drop" / "Run01" / "Individual" / "2_Face_Second_Scene02"
    (new_scene_path / "results").mkdir(parents=True)
    refreshed = client.post(BASE + "/refresh", json={
        "project_id": registered["project_id"], "request_id": request_id,
        "environment": "DISTRIBUTION",
    })
    assert refreshed.status_code == 200, refreshed.text
    refreshed_scene = next(node for node in refreshed.json()["nodes"]
                           if node["relative_path"] == scene_path.relative_to(root).as_posix())
    added_scene = next(node for node in refreshed.json()["nodes"]
                       if node["relative_path"] == new_scene_path.relative_to(root).as_posix())
    assert added_scene["role_basis"] == "PATTERN"
    location_scene = next(item for item in refreshed.json()["locations"]
                          if item["relative_path"] == scene_path.relative_to(root).as_posix())
    results_location = next(item for item in refreshed.json()["locations"]
                            if item["relative_path"] == result_path.relative_to(root).as_posix())
    assert location_scene["result_paths"] == [{
        "relative_path": (result_path.relative_to(root).as_posix()),
        "source": "SCENE", "priority": 0, "location_id": results_location["id"], "role_kind": "RESULTS",
    }]
    with connect() as conn:
        capture_json = json.loads(conn.execute(
            "SELECT payload_json FROM dashboard_captures WHERE case_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            [registered["capture_jobs"][0]["case_id"]],
        ).fetchone()[0])
    captured_scene = capture_json["runs"][0]["scenes"][0]
    catalog = client.get("/api/materials/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert catalog.status_code == 200, catalog.text
    catalog_scenes = catalog.json()["scenes"]
    matched = next(item for item in catalog_scenes if item["relative_path"] == f"{case_path}/Drop/Run01/Individual/{scene}")
    assert matched["scene_id"] == captured_scene["id"]
    added_catalog_scene = next(item for item in catalog_scenes
                               if item["relative_path"] == new_scene_path.relative_to(root).as_posix())
    added_location = next(item for item in refreshed.json()["locations"]
                          if item["relative_path"] == new_scene_path.relative_to(root).as_posix())
    assert added_catalog_scene["scene_id"] == added_location["scene_id"]
    assert any(item["relative_path"].endswith("Scene_Empty") and not item["has_deck"] for item in catalog_scenes)


def test_scoped_refresh_ambiguous_level_and_failed_refresh_preserve_active_snapshot(admin_client, monkeypatch):
    client, root = admin_client
    case_path = "Project_9045_Ambiguous/WR_9045_SimType1/Assy_RES_Model_SetCase1"
    usage_files(root / case_path)
    (root / case_path / "Input").mkdir()
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    input_node = next(node for node in scan["nodes"] if node["relative_path"] == f"{case_path}/Input")
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": input_node["id"], "role_kind": "INPUT", "confirm": True},
    ]})
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"ambiguous-{uuid4()}", "capture": True,
    })
    with connect() as conn:
        project_id, request_id = conn.execute(
            "SELECT project_id,request_id FROM folder_environment_registrations WHERE id=?",
            [registration["registration_id"]],
        ).fetchone()
    initial = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert initial.status_code == 200, initial.text
    initial_snapshot = initial.json()["snapshot_id"]
    candidate_path = f"{case_path}/NewFolder"
    (root / candidate_path).mkdir()
    refreshed = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert refreshed.status_code == 200, refreshed.text
    candidate = next(node for node in refreshed.json()["nodes"] if node["relative_path"] == candidate_path)
    assert (candidate["status"], candidate["role_basis"]) == ("UNRESOLVED", "CONFLICT")
    assert refreshed.json()["status"] == "CONFLICT"
    assert refreshed.json()["activated"] is False
    assert refreshed.json()["snapshot_id"] == initial_snapshot
    original_snapshot = initial_snapshot

    (root / f"{case_path}/AnotherFolder").mkdir()
    from app.services import folder_discovery_environment, folder_schema_resolver
    real_scan = folder_discovery_environment.scan
    monkeypatch.setattr(folder_discovery_environment, "scan", lambda *_args, **_kwargs: {"status": "INCOMPLETE", "nodes": [], "file_state": [], "issues": [{"code": "TEST"}]})
    failed = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert failed.status_code == 422, failed.text
    with connect() as conn:
        locations = folder_schema_resolver.resolve_request_locations(conn, project_id, request_id, "USAGE")
        assert locations.snapshot_id == original_snapshot
        assert conn.execute("SELECT count(*) FROM folder_environment_scans WHERE id=?", [original_snapshot]).fetchone()[0] == 1
    monkeypatch.setattr(folder_discovery_environment, "scan", real_scan)


def test_refresh_applies_same_revision_registration_after_active_snapshot(admin_client):
    client, root = admin_client
    case_path = "Project_9046_Reregister/WR_9046_SimType1/Assy_RES_Model_SetCase1"
    usage_files(root / case_path)
    first_scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    first_preview = post(client, "/previews", {"scan_id": first_scan["id"], "assignments": []})
    first_registration = post(client, "/registrations", {
        "preview_id": first_preview["id"], "idempotency_key": f"first-{uuid4()}", "capture": False,
    })
    with connect() as conn:
        project_id, request_id = conn.execute(
            "SELECT project_id,request_id FROM folder_environment_registrations WHERE id=?",
            [first_registration["registration_id"]],
        ).fetchone()
    initial_refresh = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert initial_refresh.status_code == 200, initial_refresh.text
    initial = initial_refresh.json()
    assert initial["status"] == "REFRESHED"
    settle_path = f"{case_path}/Settle"
    assert any(item["relative_path"] == settle_path and item["role_kind"] == "EVALUATION"
               for item in initial["nodes"])

    second_scan = post(client, "/scan", {"environment": "USAGE", "relative_path": ""})
    settle_node = next(item for item in second_scan["nodes"] if item["relative_path"] == settle_path)
    second_preview = post(client, "/previews", {"scan_id": second_scan["id"], "assignments": [
        {"node_id": settle_node["id"], "role_kind": "EXCLUDE", "confirm": True},
    ]})
    second_registration = post(client, "/registrations", {
        "preview_id": second_preview["id"], "idempotency_key": f"second-{uuid4()}", "capture": False,
    })
    assert second_registration["registration_id"] != first_registration["registration_id"]

    after_reregistration = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert after_reregistration.status_code == 200, after_reregistration.text
    refreshed = after_reregistration.json()
    assert refreshed["status"] == "REFRESHED"
    assert refreshed["snapshot_id"] != initial["snapshot_id"]
    settle_after = next(item for item in refreshed["nodes"] if item["relative_path"] == settle_path)
    assert (settle_after["status"], settle_after["role_basis"]) == ("EXCLUDED", "EXCLUDED")
    assert not any(item["relative_path"] == settle_path for item in refreshed["locations"])
    repeated = client.post(BASE + "/refresh", json={
        "project_id": project_id, "request_id": request_id, "environment": "USAGE",
    })
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["status"] == "UNCHANGED"
    assert repeated.json()["snapshot_id"] == refreshed["snapshot_id"]


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
