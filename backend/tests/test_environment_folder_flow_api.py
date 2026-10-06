"""Cross-feature contracts: a folder plan must lead to usable dashboard data."""
from __future__ import annotations

import hashlib
import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.services import dashboard_capture, folder_discovery_environment, folder_schema_locations, materials_catalog, result_registration
from app.services.folder_schema_locations import EnvironmentLocations

pytestmark = pytest.mark.duckdb_integration


@pytest.fixture(autouse=True)
def _legacy_profiles(isolated_database):
    """These scenarios use legacy folder layouts: run them as a pre-0034 database."""
    from tests.legacy_environment_profiles import activate_legacy_profiles
    activate_legacy_profiles()
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


def synthetic_pptx(extra: dict[str, bytes] | None = None, content_types: bytes | None = None) -> bytes:
    """Smallest zip the Final report check accepts as PPTX (synthetic, not a real deck)."""
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types or (
            b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/></Types>'))
        archive.writestr("ppt/presentation.xml", b"<p:presentation/>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def post(client, route, payload):
    response = client.post(BASE + route, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _profile_call(operation):
    """Legacy profile editing is HTTP 410 since DEPTH_V1 (§6); these legacy scenarios drive the service."""
    from fastapi import HTTPException
    from app.services import environment_folder_profiles
    from app.services.folder_discovery import WRITE_LOCK
    from app.services.semantic_mapping import semantic_transaction
    with connect() as conn:
        try:
            with WRITE_LOCK, semantic_transaction(conn):
                return 200, operation(environment_folder_profiles, conn)
        except HTTPException as exc:
            return exc.status_code, exc.detail


def test_profile_archive_keeps_revision_and_history_reference_and_rejects_stale_edit(admin_client):
    client, _ = admin_client
    for method, path in (("post", "/profiles"), ("put", "/profiles/x"), ("delete", "/profiles/x?expected_revision=1"),
                         ("post", "/profiles/from-legacy")):
        gone = getattr(client, method)(BASE + path, **({"json": {}} if method != "delete" else {}))
        assert gone.status_code == 410 and gone.json()["detail"]["code"] == "DEPRECATED_USE_DEPTH_SCHEMA", gone.text
    _, profile = _profile_call(lambda svc, conn: svc.save_profile(conn, **{
        "environment": "DISTRIBUTION", "name": f"archive test {uuid4().hex[:8]}",
        "rules": {"rules": [], "description": "Synthetic archive lifecycle check."},
    }))
    status, archived = _profile_call(lambda svc, conn: svc.archive_profile(conn, profile["id"], profile["revision"]))
    assert status == 200, archived
    assert archived["revision"] == profile["revision"]
    assert archived["archived"] is True
    assert profile["id"] not in {item["id"] for item in client.get(BASE).json()["items"]}

    with connect() as conn:
        saved = folder_discovery_environment._profile(conn, profile["id"], "DISTRIBUTION")
        assert saved["revision"] == profile["revision"]
        assert saved["rules"]["profile_metadata"]["archived"] is True
    status, stale_edit = _profile_call(lambda svc, conn: svc.save_profile(
        conn, profile_id=profile["id"], environment="DISTRIBUTION", name=profile["name"], rules={"rules": []},
        expected_revision=profile["revision"]))
    assert status == 409
    assert stale_edit["code"] == "ENVIRONMENT_PROFILE_ARCHIVED"


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

    direct_scene = {**scoped, "folder_schema_locations": [{
        "relative_path": "Case/Drop/Run/INDIVIDUAL/2_Face &3_Face",
        "role_kind": "SCENE", "status": "CONFIRMED",
    }]}
    direct_file = "Case/Drop/Run/INDIVIDUAL/2_Face &3_Face/result.csv"
    assert dashboard_capture._schema_allows_file(direct_scene, "Case", direct_file)
    dashboard_capture._assert_schema_allows_approved_files(
        direct_scene, [(direct_file, b"Position,Layer_1\nTOP,1\n", "text/csv")],
    )
    with pytest.raises(dashboard_capture.DashboardCaptureError):
        dashboard_capture._assert_schema_allows_approved_files(
            direct_scene, [(direct_file + "/nested.csv", b"x", "text/csv")],
        )

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
    direct_file = scene_path / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C24_scene.h3d.csv"
    direct_file.parent.mkdir(parents=True)
    direct_file.write_text("Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,20,20,20,20\n", encoding="utf-8")
    (scene_path / "model.rad").write_text("solver input", encoding="utf-8")
    (scene_path / "include.inc").write_text("solver include", encoding="utf-8")
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
    with connect() as conn:
        direct_target = result_registration._current_target(
            conn, project_id=registered["project_id"], request_id=request_id,
            environment="DISTRIBUTION", case_relative_path=case_path,
            result_relative_path=scene_path.relative_to(root).as_posix(),
            require_schema_roles=True,
        )
    assert direct_target["result_relative_path"] == scene_path.relative_to(root).as_posix()
    assert direct_target["context"]["scene"]["relative_path"] == scene_path.relative_to(root).as_posix()
    results_location = next(item for item in refreshed.json()["locations"]
                            if item["relative_path"] == result_path.relative_to(root).as_posix())
    assert location_scene["result_paths"] == [{
        "relative_path": scene_path.relative_to(root).as_posix(),
        "source": "SCENE", "priority": 0, "location_id": location_scene["id"], "role_kind": "SCENE",
    }, {
        "relative_path": result_path.relative_to(root).as_posix(),
        "source": "SCENE", "priority": 0, "location_id": results_location["id"], "role_kind": "RESULTS",
    }]
    with connect() as conn:
        manifest_json, payload_json = conn.execute(
            "SELECT manifest_json,payload_json FROM dashboard_captures WHERE case_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            [registered["capture_jobs"][0]["case_id"]],
        ).fetchone()
        capture_json = json.loads(payload_json)
        manifest_json = json.loads(manifest_json)
    captured_scene = capture_json["runs"][0]["scenes"][0]
    manifest_paths = {item["relative_path"] for item in manifest_json}
    assert direct_file.relative_to(root).as_posix() in manifest_paths
    assert "model.rad" not in manifest_paths and "include.inc" not in manifest_paths
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


def test_preview_manual_role_clears_scan_diagnosis_and_reports_invalid_scene_parent(admin_client):
    client, root = admin_client
    project_path = "Project_9301_Preview"
    request_path = f"{project_path}/WR_9301_SimType1"
    case_path = f"{request_path}/Package_한국어_case_결과_매우_긴_이름_SetCase1"
    valid_scene = case_path + "/Drop/한국어_run_이름이_긴_실행_경로/Individual/2_Face_Drop_Scene01"
    invalid_scene = case_path + "/Drop/한국어_run_이름이_긴_실행_경로/Individual/RESULTS/3_Face_Drop_Scene02"
    (root / valid_scene).mkdir(parents=True)
    (root / invalid_scene).mkdir(parents=True)

    scan = post(client, "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    mismatched_request = next(node for node in scan["nodes"] if node["relative_path"] == request_path)
    assert mismatched_request["status"] == "UNRESOLVED"
    assert "SimType 폴더가 일치하지 않습니다" in mismatched_request["message"]

    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": mismatched_request["id"], "role_kind": "REQUEST", "confirm": True, "target_mode": "CREATE"},
    ]})
    corrected_request = next(row for row in preview["rows"] if row["relative_path"] == request_path)
    assert corrected_request["status"] == "CONFIRMED"
    assert corrected_request.get("message") is None
    rejected_scene = next(row for row in preview["rows"] if row["relative_path"] == invalid_scene)
    assert rejected_scene["status"] == "UNRESOLVED"
    assert rejected_scene["message"] == "상위 EXECUTION_RUN 또는 RUN_OPTION 역할이 필요합니다."
    assert preview["unresolved_count"] == 1


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
    options = [item for item in catalog["run_options"] if item.get("capture_id") == job["capture_id"]]
    schema_options = [item for item in catalog["run_options"] if item.get("capture_id") is None]
    assert len(options) == 3, options
    assert len(schema_options) == 3, schema_options
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


def test_case_finalization_is_capture_pinned_signed_retryable_and_keeps_existing_final(admin_client, monkeypatch):
    from app.services import case_finalization
    from app.security import hash_password
    from datetime import datetime, timezone

    client, root = admin_client
    case_path = "Project_9910_Final/WR_9910_SimType3/Package_SetCase1_CushionCase1"
    scene_path = root / case_path / "Drop" / "Run01" / "Individual" / "1_Face_Drop_Scene01_Face1_1st"
    result_path = scene_path / "results"
    direct_csv = scene_path / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C24_scene.h3d.csv"
    result_csv = result_path / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
    direct_csv.parent.mkdir(parents=True)
    result_path.mkdir()
    csv_data = "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,10,10,10\n"
    direct_csv.write_text(csv_data, encoding="utf-8")
    result_csv.write_text(csv_data, encoding="utf-8")
    (scene_path / "model.rad").write_text("/INCLUDE include.inc\n", encoding="utf-8")
    (scene_path / "include.inc").write_text("synthetic include\n", encoding="utf-8")
    (scene_path / "review.pdf").write_bytes(b"synthetic report")
    excluded = scene_path / "Private"
    excluded.mkdir()
    (excluded / "private.json").write_text('{"private":true}', encoding="utf-8")

    # A pre-existing unrelated Final file must never be overwritten or removed.
    preserved = root / "Project_9910_Final" / "WR_9910_SimType3" / "Final" / "CAE" / "manual" / "keep.txt"
    preserved.parent.mkdir(parents=True)
    preserved.write_text("keep this", encoding="utf-8")

    scan = post(client, "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    private_node = next(node for node in scan["nodes"] if node["relative_path"] == f"{case_path}/Drop/Run01/Individual/1_Face_Drop_Scene01_Face1_1st/Private")
    schema_preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": [
        {"node_id": private_node["id"], "role_kind": "EXCLUDE", "confirm": True},
    ]})
    registered = post(client, "/registrations", {
        "preview_id": schema_preview["id"], "idempotency_key": f"final-{uuid4()}", "capture": True,
    })
    job = registered["capture_jobs"][0]
    assert job["status"] == "COMPLETED", registered
    request_id = registered["request_id"]
    case_id, capture_id = job["case_id"], job["capture_id"]

    # A Scene that appears after capture and schema refresh is outside this
    # operation's capture-pinned source scope.
    added_scene = root / case_path / "Drop" / "Run01" / "Individual" / "2_Face_Added_Scene02"
    added_scene.mkdir(parents=True)
    (added_scene / "later.rad").write_text("new scene input", encoding="utf-8")
    (added_scene / "later.inc").write_text("new scene include", encoding="utf-8")
    (added_scene / "later.pdf").write_bytes(b"new scene report")
    added_scene_relative = added_scene.relative_to(root).as_posix()
    original_scope = case_finalization._scope

    def scope_with_added_current_scene(*args, **kwargs):
        result = original_scope(*args, **kwargs)
        result["scene_locations"] = [*result["scene_locations"], {
            "relative_path": added_scene_relative, "role_kind": "SCENE",
            "status": "CONFIRMED", "target_id": "synthetic-current-scene",
        }]
        return result

    monkeypatch.setattr(case_finalization, "_scope", scope_with_added_current_scene)

    body = {"project_id": registered["project_id"], "request_id": request_id,
            "environment": "DISTRIBUTION", "case_id": case_id, "capture_id": capture_id}

    # The write route requires result.import; status is readable with the ordinary project data permission.
    viewer_id = f"finalization-viewer-{uuid4().hex}"
    viewer_name = viewer_id
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (id,username,password_hash,display_name,legacy_role,is_active,created_at,updated_at,account_status,is_global_admin) "
            "VALUES (?,?,?,?,'viewer',true,?,?,'ACTIVE',false)",
            [viewer_id, viewer_name, hash_password("synthetic-viewer-password"), viewer_name, now, now],
        )
        conn.execute(
            "INSERT INTO project_memberships (id,project_id,user_id,role,created_by,created_at,updated_by,updated_at) "
            "VALUES (?,?,?,'general',?,?,?,?)",
            [f"finalization-membership-{uuid4().hex}", registered["project_id"], viewer_id,
             "synthetic-admin", now, "synthetic-admin", now],
        )
    with TestClient(app) as viewer:
        login = viewer.post("/api/auth/login", json={"username": viewer_name, "password": "synthetic-viewer-password"})
        assert login.status_code == 200, login.text
        viewer.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        denied = viewer.post("/api/dashboard/finalizations/preview", json=body)
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["required_permission"] == "result.import"
        assert viewer.get("/api/dashboard/finalizations/status", params={key: value for key, value in body.items() if key != "capture_id"}).status_code == 200

    for bad_body in (
        {**body, "project_id": "another-project"},
        {**body, "request_id": "another-request"},
        {**body, "capture_id": "another-capture"},
    ):
        rejected = client.post("/api/dashboard/finalizations/preview", json=bad_body)
        assert rejected.status_code >= 400, rejected.text

    # Real scan -> schema confirmation -> registration -> capture provenance.
    plan = client.post("/api/dashboard/finalizations/preview", json=body)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan["counts"]["rad_decks"] == 1
    assert plan["counts"]["inc_decks"] == 1
    assert plan["counts"]["scene_reports"] == 1
    assert plan["counts"]["results"] == 2
    assert added_scene_relative not in plan["scene_paths"]
    assert not any(item["source_relative_path"].startswith(added_scene_relative + "/") for item in plan["files"])
    assert {item["source_basis"] for item in plan["files"]} == {"SOURCE_CAPTURE", "CURRENT_CONFIRMED_SCENE"}
    assert {item["category"] for item in plan["files"]} == {"CAE"}
    assert plan["report_files"] == {"pptx": "Package_SetCase1_CushionCase1_report.pptx",
                                    "html": "Package_SetCase1_CushionCase1_report.html"}
    assert not any("Private" in item["source_relative_path"] for item in plan["files"])
    assert any(item["source_relative_path"].endswith("review.pdf") for item in plan["files"])
    assert not (root / "Project_9910_Final" / "WR_9910_SimType3" / "Final" / "Report" / "Package_SetCase1_CushionCase1").exists()

    # Tampered durable plans fail closed before any destination is copied.
    operation_dir = root / plan["metadata_relative_path"]
    stored_plan_path = operation_dir / "plan.json"
    stored_plan = json.loads(stored_plan_path.read_text(encoding="utf-8"))
    stored_plan["final_relative_path"] = "Elsewhere"
    stored_plan_path.write_text(json.dumps(stored_plan), encoding="utf-8")
    tampered = client.post("/api/dashboard/finalizations/confirm", json={**body, "operation_id": plan["operation_id"], "report_formats": ["pptx"]})
    assert tampered.status_code == 409, tampered.text
    assert preserved.read_text(encoding="utf-8") == "keep this"
    stored_plan["final_relative_path"] = plan["final_relative_path"]
    stored_plan_path.write_text(json.dumps(stored_plan), encoding="utf-8")
    # This repaired test fixture is signed metadata; in a real retry, preview creates the immutable plan.
    stored_plan = case_finalization._signed_record(stored_plan, "plan_signature", b"case-finalization:plan:v1\0")
    stored_plan_path.write_bytes(case_finalization._encode(stored_plan))

    # A changed capture source blocks confirmation, then restoring the captured bytes permits a new preview.
    result_csv.write_text(csv_data + "# changed\n", encoding="utf-8")
    stale_preview = client.post("/api/dashboard/finalizations/preview", json=body)
    assert stale_preview.status_code == 409, stale_preview.text
    result_csv.write_text(csv_data, encoding="utf-8")

    retry_plan = client.post("/api/dashboard/finalizations/preview", json=body)
    assert retry_plan.status_code == 200, retry_plan.text
    retry_plan = retry_plan.json()
    from app.services import case_finalization_jobs
    from app.services.spdm_storage import SpdmStorageError
    from app.services.storage import provider as storage_provider
    from app.services.storage.local import LocalFsProvider
    # The wrapper below runs on the stack of this test module (S3 caller check).
    monkeypatch.setattr(storage_provider, "FINAL_WRITER_MODULES",
                        frozenset({*storage_provider.FINAL_WRITER_MODULES, __name__}))
    original_copy = LocalFsProvider.copy_stream
    call_count = 0

    def fail_once(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise SpdmStorageError("SYNTHETIC_COPY_FAILURE", "synthetic copy failure")
        return original_copy(self, *args, **kwargs)

    status_query = {key: value for key, value in body.items() if key != "capture_id"}

    def confirm_and_wait(operation_id):
        """W2: confirm queues a copy job; wait for the worker and read the job outcome."""
        started = client.post("/api/dashboard/finalizations/confirm", json={**body, "operation_id": operation_id, "report_formats": ["pptx"]})
        assert started.status_code == 200, started.text
        assert case_finalization_jobs.wait_idle(120)
        job = client.get(f"/api/dashboard/finalizations/{operation_id}/job", params=status_query)
        assert job.status_code == 200, job.text
        return job.json()

    report_query = {**body}
    staged = client.put(f"/api/dashboard/finalizations/{retry_plan['operation_id']}/reports/pptx",
                        params=report_query, content=synthetic_pptx())
    assert staged.status_code == 200, staged.text
    monkeypatch.setattr(LocalFsProvider, "copy_stream", fail_once)
    failed = confirm_and_wait(retry_plan["operation_id"])
    assert failed["state"] == "FAILED" and failed["error"]["code"] == "SYNTHETIC_COPY_FAILURE", failed
    assert not (root / retry_plan["output_paths"]["CAE"]).exists()
    retryable = client.get("/api/dashboard/finalizations/status", params=status_query).json()["retryable_operations"]
    assert retryable and retryable[0]["job"]["state"] == "FAILED"
    monkeypatch.setattr(LocalFsProvider, "copy_stream", original_copy)
    confirmed = confirm_and_wait(retry_plan["operation_id"])
    assert confirmed["state"] == "COMPLETE", confirmed
    record = confirmed["record"]
    assert record["status"] == "COMPLETE"
    assert record["counts"]["rad_decks"] == 1 and record["counts"]["inc_decks"] == 1
    assert record["output_paths"]["CAE"].startswith("Project_9910_Final/WR_9910_SimType3/Final/CAE/Package_SetCase1_CushionCase1/")
    assert record["output_paths"]["Reports"].startswith("Project_9910_Final/WR_9910_SimType3/Final/Report/Package_SetCase1_CushionCase1/")
    for item in record["files"]:
        destination = root / record["output_paths"][item["category"]] / item["case_relative_path"]
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == item["sha256"]
    assert [path.name for path in (root / record["output_paths"]["Reports"]).iterdir()] == ["Package_SetCase1_CushionCase1_report.pptx"]
    assert (root / record["output_paths"]["CAE"] / "Drop/Run01/Individual/1_Face_Drop_Scene01_Face1_1st/review.pdf").is_file()
    assert (root / record["output_paths"]["CAE"] / "Drop/Run01/Individual/1_Face_Drop_Scene01_Face1_1st/include.inc").is_file()
    assert preserved.read_text(encoding="utf-8") == "keep this"

    # Idempotent retry uses the signed completion record even after the source Scene disappears.
    source_bytes = result_csv.read_bytes()
    result_csv.unlink()
    repeated = client.post("/api/dashboard/finalizations/confirm", json={**body, "operation_id": retry_plan["operation_id"], "report_formats": ["pptx"]})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["state"] == "COMPLETE" and repeated.json()["record"]["confirmed_at"] == record["confirmed_at"]
    result_csv.write_bytes(source_bytes)

    # Oversized and deeply nested unsigned siblings are reported as unverified
    # without hiding the valid, signed completion record.
    metadata_dir = root / retry_plan["metadata_relative_path"].replace("/" + retry_plan["operation_id"], "")
    oversized_dir = metadata_dir / uuid4().hex
    oversized_dir.mkdir()
    (oversized_dir / "plan.json").write_bytes(
        b'{"x":"' + b"x" * case_finalization.MAX_METADATA_BYTES + b'"}'
    )
    deep_dir = metadata_dir / uuid4().hex
    deep_dir.mkdir()
    depth = 1200
    (deep_dir / "plan.json").write_bytes(b'{"x":' * depth + b"0" + b"}" * depth)
    state = client.get("/api/dashboard/finalizations/status", params={key: value for key, value in body.items() if key != "capture_id"})
    assert state.status_code == 200, state.text
    assert state.json()["latest"]["operation_id"] == retry_plan["operation_id"]
    assert state.json()["unverified_records"] >= 2

    with monkeypatch.context() as patcher:
        patcher.setattr(case_finalization, "MAX_STATUS_ITEMS", 1)
        limited_items = client.get("/api/dashboard/finalizations/status", params={key: value for key, value in body.items() if key != "capture_id"})
        assert limited_items.status_code == 422, limited_items.text
        assert limited_items.json()["detail"]["code"] == "FINALIZATION_STATUS_LIMIT"
    with monkeypatch.context() as patcher:
        # W2: a record over the hash budget is shown verified by existence and size only.
        patcher.setattr(case_finalization, "MAX_STATUS_VERIFY_BYTES", 0)
        limited_bytes = client.get("/api/dashboard/finalizations/status", params={key: value for key, value in body.items() if key != "capture_id"})
        assert limited_bytes.status_code == 200, limited_bytes.text
        assert limited_bytes.json()["latest"]["verification"] == "SIZE"

    original_settings = case_finalization.security_settings
    monkeypatch.setattr(case_finalization, "security_settings", lambda: type("Settings", (), {"secret_key": "rotated-synthetic-key"})())
    rotated = client.get("/api/dashboard/finalizations/status", params={key: value for key, value in body.items() if key != "capture_id"})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["latest"] is None and rotated.json()["unverified_records"] >= 1
    monkeypatch.setattr(case_finalization, "security_settings", original_settings)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory-handle sharing semantics")
def test_case_finalization_pins_output_parent_during_temp_write(tmp_path, monkeypatch):
    """W2 streaming copy: the partial folder chain stays pinned while the new file is created."""
    from app.services import case_finalization
    from app.services.storage import local as storage_local

    root = tmp_path / "spdm-root"
    root.mkdir()
    payload = b"synthetic deck bytes"
    (root / "source.inc").write_bytes(payload)
    operation = "a" * 32
    base = f"Request/Final/.finalizations/{operation}/staging"
    partial = root / base / "partial"
    partial.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    parked = partial.with_name("partial-parked")
    rename_blocked: list[bool] = []
    original_create = storage_local._create_new_file

    def attempt_junction_swap(path):
        if not rename_blocked:
            try:
                partial.rename(parked)
            except OSError:
                rename_blocked.append(True)
            else:
                rename_blocked.append(False)
                import subprocess
                subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(partial), str(outside)],
                               check=True, capture_output=True)
        return original_create(path)

    monkeypatch.setattr(storage_local, "_create_new_file", attempt_junction_swap)
    operation_error = None
    try:
        try:
            digest = case_finalization._stage_file(
                root, "source.inc", f"{base}/CAE/model.inc", f"{base}/partial",
                expected_size=len(payload), expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_modified_ns=None, on_progress=None, ensured=set(),
            )
        except Exception as exc:  # Capture the vulnerable unpinned path's post-write guard failure.
            operation_error = exc
        assert rename_blocked == [True], "partial folder was renameable while the new file was created"
        assert not list(outside.iterdir()), "temporary bytes escaped the configured SPDM root"
        assert operation_error is None, f"safe copy unexpectedly failed: {operation_error}"
        assert digest == hashlib.sha256(payload).hexdigest()
        assert (root / base / "CAE" / "model.inc").read_bytes() == payload
    finally:
        if partial.is_junction():
            os.rmdir(partial)
        if parked.exists() and not partial.exists():
            parked.rename(partial)


def test_saved_profile_revision_is_used_and_old_preview_cannot_apply_after_edit(admin_client):
    client, root = admin_client
    usage_files(root / "Project_9003_Flow" / "WR_9003_SimType1" / "Model Custom")
    definition = {"environment": "USAGE", "name": f"Custom usage {uuid4()}", "rules": {"rules": [
        {"role_kind": "SIMULATION_CASE", "parent_role": "REQUEST", "pattern": "Model*", "match_mode": "glob"},
    ]}}
    _, profile = _profile_call(lambda svc, conn: svc.save_profile(conn, **definition))
    assert profile["revision"] == 1
    scan = post(client, "/scan", {"environment": "USAGE", "relative_path": "", "profile_id": profile["id"]})
    custom = next(node for node in scan["nodes"] if node["name"] == "Model Custom")
    assert custom["role_kind"] == "SIMULATION_CASE"
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    status, updated = _profile_call(lambda svc, conn: svc.save_profile(conn, profile_id=profile["id"], expected_revision=1, **definition))
    assert status == 200, updated
    assert updated["revision"] == 2
    status, conflict = _profile_call(lambda svc, conn: svc.save_profile(conn, profile_id=profile["id"], expected_revision=1, **definition))
    assert status == 409, conflict
    stale = client.post(BASE + "/registrations", json={"preview_id": preview["id"], "idempotency_key": f"revision-{uuid4()}", "capture": True})
    assert stale.status_code in (409, 422), stale.text


def test_legacy_copy_preserves_original_and_requires_case_review(admin_client):
    client, root = admin_client
    from app.services.folder_discovery import root_identity
    original = [{"depth": 1, "role": "PROJECT", "keyword": "Project_", "delimiter": "_", "code_token": 2, "name_from_token": 3},
                {"depth": 3, "role": "LOAD_CASE", "keyword": "LC_", "delimiter": "_", "code_token": 2, "name_from_token": 3, "analysis_type": "SPDM_CMS"}]
    with connect() as conn:
        conn.execute("INSERT INTO folder_discovery_rules VALUES (?, '', ?, 1, CURRENT_TIMESTAMP, 'test')", [root_identity(root), json.dumps(original)])
    _, profile = _profile_call(lambda svc, conn: svc.copy_legacy(conn, "USAGE", f"Copied legacy {uuid4()}", ""))
    assert profile["requires_review"]
    assert [r["role_kind"] for r in profile["rules"]["rules"]] == ["PROJECT"]
    assert len(profile["omitted_rules"]) == 1
    with connect() as conn:
        stored = conn.execute("SELECT rules_json,revision FROM folder_discovery_rules WHERE root_key=?", [root_identity(root)]).fetchone()
        assert (json.loads(stored[0]) if isinstance(stored[0], str) else stored[0]) == original
        assert stored[1] == 1
