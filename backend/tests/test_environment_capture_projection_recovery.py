"""Regression coverage for request-scoped capture projection recovery."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.services import dashboard_capture, folder_discovery_environment, folder_schema_resolver


pytestmark = pytest.mark.duckdb_integration


@pytest.fixture(autouse=True)
def _legacy_profiles(isolated_database):
    """These scenarios use legacy folder layouts: run them as a pre-0034 database."""
    from tests.legacy_environment_profiles import activate_legacy_profiles
    activate_legacy_profiles()
BASE = "/api/folder-discovery/environments"


@pytest.fixture
def capture_client(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "capture-projection-test-secret-at-least-32")
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


def add_usage_case(root, request_name, value=1.5):
    case = root / "Project_multi" / request_name / f"Assy_RES_{request_name}"
    result = case / "Settle" / "model_settle_result.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({"Set Tilt Angle @ Settle (deg)": value}), encoding="utf-8")
    return case


def preview_all(client, environment="USAGE"):
    scan = post(client, "/scan", {"environment": environment, "relative_path": ""})
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview
    return preview


def test_capture_binding_uses_only_the_registered_project_and_request_targets(capture_client):
    client, root = capture_client
    first_case = add_usage_case(root, "WR_0001_SimType1", value=11.0)
    second_case = add_usage_case(root, "WR_0002_SimType1", value=22.0)
    preview = preview_all(client)

    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"multi-request-{uuid4()}", "capture": True,
    })

    assert registration["status"] == "FAILED", registration
    assert len(registration["capture_jobs"]) == 2
    case_ids = {
        first_case.relative_to(root).as_posix(): dashboard_capture._case_id(
            dashboard_capture._root_id(root), first_case.relative_to(root).as_posix(),
        ),
        second_case.relative_to(root).as_posix(): dashboard_capture._case_id(
            dashboard_capture._root_id(root), second_case.relative_to(root).as_posix(),
        ),
    }
    jobs_by_case = {job["case_id"]: job for job in registration["capture_jobs"]}
    first_job = jobs_by_case[case_ids[first_case.relative_to(root).as_posix()]]
    second_job = jobs_by_case[case_ids[second_case.relative_to(root).as_posix()]]
    assert first_job["status"] == "COMPLETED" and first_job["capture_id"]
    assert second_job["status"] == "FAILED"
    assert second_job["error_code"] == "CAPTURE_CONTEXT_MISMATCH"

    with connect() as conn:
        persisted = conn.execute(
            "SELECT dc.request_id,c.payload_json FROM dashboard_cases dc "
            "JOIN dashboard_captures c ON c.case_id=dc.id WHERE dc.id=?",
            [first_job["case_id"]],
        ).fetchone()
        assert conn.execute("SELECT count(*) FROM dashboard_cases WHERE id=?", [second_job["case_id"]]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM dashboard_captures WHERE case_id=?", [second_job["case_id"]]).fetchone()[0] == 0
    assert str(persisted[0]) == registration["request_id"]
    payload = json.loads(persisted[1])
    settle = next(item for item in payload["evaluations"] if item["evaluation"] == "Settle")
    assert settle["values"]["Set Tilt Angle @ Settle (deg)"] == 11.0


def test_duplicate_paths_for_one_request_target_remain_ambiguous(capture_client):
    client, root = capture_client
    add_usage_case(root, "WR_0001_SimType1")
    add_usage_case(root, "WR_0002_SimType1")
    preview = preview_all(client)
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"duplicate-request-path-{uuid4()}", "capture": False,
    })
    alternate_request_path = "Project_multi/WR_0002_SimType1_ALIAS"

    with connect() as conn:
        conn.execute(
            "INSERT INTO folder_environment_registry "
            "(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,option_status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                f"synthetic-duplicate-request-path-{uuid4()}", registration["registration_id"],
                folder_discovery_environment.root_identity(root), alternate_request_path, "REQUEST", None,
                registration["request_id"], "synthetic duplicate path", None,
                folder_discovery_environment.now(),
            ],
        )

        with pytest.raises(folder_schema_resolver.FolderSchemaError) as exc_info:
            folder_schema_resolver._request_path(
                conn, folder_discovery_environment.root_identity(root), registration["project_id"],
                registration["request_id"], registration["environment"],
            )

    assert exc_info.value.code == "FOLDER_SCHEMA_REQUEST_BINDING_AMBIGUOUS"


def _add_applied_relinked_preview(conn, root, registration, rows):
    root_key = folder_discovery_environment.root_identity(root)
    scan_id = f"synthetic-relinked-scan-{uuid4()}"
    preview_id = f"synthetic-relinked-preview-{uuid4()}"
    registration_id = f"synthetic-relinked-registration-{uuid4()}"
    scan = conn.execute(
        "SELECT profile_id,profile_revision,environment FROM folder_environment_scans WHERE id=?",
        [registration["scan_id"]],
    ).fetchone()
    now = folder_discovery_environment.now()
    conn.execute(
        "INSERT INTO folder_environment_scans "
        "(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,request_id,status,tree_json,issues_json,created_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [scan_id, root_key, "", scan[2], scan[0], scan[1], registration["project_id"],
         registration["request_id"], "COMPLETE", "[]", "[]", "test", now],
    )
    conn.execute(
        "INSERT INTO folder_environment_previews(id,scan_id,rows_json,can_apply,created_by,created_at) "
        "VALUES(?,?,?,?,?,?)",
        [preview_id, scan_id, json.dumps({"rows": rows}), True, "test", now],
    )
    conn.execute(
        "INSERT INTO folder_environment_registrations "
        "(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        [registration_id, preview_id, registration_id, scan[2], registration["project_id"],
         registration["request_id"], "FAILED", "test", now],
    )
    return root_key, registration_id


def _role(path, role, name, target_id):
    return {"relative_path": path, "role_kind": role, "status": "CONFIRMED",
            "name": name, "target_id": target_id}


def _generated_role(root_key, path, role):
    return folder_discovery_environment.stable(
        "environment-project" if role == "PROJECT" else "environment-request",
        root_key, path, role,
    )


def test_relinked_capture_retry_recovers_selected_request_only(capture_client, monkeypatch):
    client, old_root = capture_client
    add_usage_case(old_root, "WR_0001_SimType1", value=11.0)
    old_preview = preview_all(client)
    old_registration = post(client, "/registrations", {
        "preview_id": old_preview["id"], "idempotency_key": f"old-root-{uuid4()}", "capture": False,
    })

    new_root = old_root.parent / "new_shared"
    new_root.mkdir()
    selected_case = add_usage_case(new_root / "SPDM (Admin)", "WR_0001_SimType1", value=33.0)
    other_request_case = add_usage_case(new_root / "SPDM (Admin)", "WR_0002_SimType1", value=44.0)
    other_project_case = new_root / "SPDM (Admin)" / "Project_other" / "WR_0003_SimType1" / "Assy_RES_WR_0003_SimType1"
    result = other_project_case / "Settle" / "model_settle_result.json"
    result.parent.mkdir(parents=True)
    result.write_text(json.dumps({"Set Tilt Angle @ Settle (deg)": 55.0}), encoding="utf-8")

    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(new_root))
    scan = post(client, "/scan", {
        "environment": "USAGE", "relative_path": "",
        "project_id": old_registration["project_id"], "request_id": old_registration["request_id"],
    })
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": []})
    assert preview["can_apply"], preview

    original_projection = folder_discovery_environment._registration_location_projection
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise folder_schema_resolver.FolderSchemaError(
                "FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED", "synthetic first capture failure",
            )
        return original_projection(*args, **kwargs)

    monkeypatch.setattr(folder_discovery_environment, "_registration_location_projection", fail_once)
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"relinked-retry-{uuid4()}", "capture": True,
    })
    assert registration["status"] == "FAILED"
    monkeypatch.setattr(folder_discovery_environment, "_registration_location_projection", original_projection)

    retried = post(client, f"/registrations/{registration['registration_id']}/capture/retry", {})
    selected_relative = selected_case.relative_to(new_root).as_posix()
    selected_case_id = dashboard_capture._case_id(dashboard_capture._root_id(new_root), selected_relative)
    jobs = {job["case_id"]: job for job in retried["capture_jobs"]}
    assert jobs[selected_case_id]["status"] == "COMPLETED", retried
    assert jobs[selected_case_id]["capture_id"]

    with connect() as conn:
        persisted = conn.execute(
            "SELECT dc.project_id,dc.request_id,c.payload_json FROM dashboard_cases dc "
            "JOIN dashboard_captures c ON c.case_id=dc.id WHERE dc.id=?",
            [selected_case_id],
        ).fetchone()
        assert conn.execute("SELECT count(*) FROM dashboard_cases WHERE relative_path IN (?,?)", [
            other_request_case.relative_to(new_root).as_posix(),
            other_project_case.relative_to(new_root).as_posix(),
        ]).fetchone()[0] == 0
    assert str(persisted[0]) == old_registration["project_id"]
    assert str(persisted[1]) == old_registration["request_id"]
    payload = json.loads(persisted[2])
    settle = next(item for item in payload["evaluations"] if item["evaluation"] == "Settle")
    assert settle["values"]["Set Tilt Angle @ Settle (deg)"] == 33.0


def test_relinked_preview_with_duplicate_historical_matches_is_ambiguous(capture_client):
    client, root = capture_client
    add_usage_case(root, "[WR-0001]_[유통_환경]")
    preview = preview_all(client, "DISTRIBUTION")
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"duplicate-old-root-{uuid4()}", "capture": False,
    })
    with connect() as conn:
        registration["scan_id"] = preview["scan_id"]
        conn.execute(
            "UPDATE folder_environment_registry SET root_key=? WHERE registration_id=?",
            ["synthetic-prior-root", registration["registration_id"]],
        )
        conn.execute("UPDATE folder_environment_scans SET root_key=? WHERE id=?",
                     ["synthetic-prior-root", preview["scan_id"]])
        current_root_key = folder_discovery_environment.root_identity(root)
        rows = [
            _role("Wrapper A/Project_multi", "PROJECT", "Project_multi",
                  _generated_role(current_root_key, "Wrapper A/Project_multi", "PROJECT")),
            _role("Wrapper A/Project_multi/[WR-0001]_[유통_환경]", "REQUEST",
                  "[WR-0001]_[유통_환경]", _generated_role(current_root_key, "Wrapper A/Project_multi/[WR-0001]_[유통_환경]", "REQUEST")),
            _role("Wrapper B/Project_multi", "PROJECT", "Project_multi",
                  _generated_role(current_root_key, "Wrapper B/Project_multi", "PROJECT")),
            _role("Wrapper B/Project_multi/[WR-0001]_[유통_환경]", "REQUEST",
                  "[WR-0001]_[유통_환경]", _generated_role(current_root_key, "Wrapper B/Project_multi/[WR-0001]_[유통_환경]", "REQUEST")),
        ]
        root_key, _ = _add_applied_relinked_preview(conn, root, registration, rows)
        with pytest.raises(folder_schema_resolver.FolderSchemaError) as exc_info:
            folder_schema_resolver._request_path(
                conn, root_key, registration["project_id"], registration["request_id"], "DISTRIBUTION",
            )
    assert exc_info.value.code == "FOLDER_SCHEMA_REQUEST_BINDING_AMBIGUOUS"


def test_relinked_preview_without_historical_registry_evidence_cannot_guess(capture_client):
    client, root = capture_client
    add_usage_case(root, "[WR-0001]_[유통_환경]")
    preview = preview_all(client, "DISTRIBUTION")
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"no-old-root-{uuid4()}", "capture": False,
    })
    with connect() as conn:
        registration["scan_id"] = preview["scan_id"]
        conn.execute(
            "DELETE FROM folder_environment_registry WHERE registration_id=?",
            [registration["registration_id"]],
        )
        conn.execute("UPDATE folder_environment_scans SET root_key=? WHERE id=?",
                     ["synthetic-prior-root", preview["scan_id"]])
        current_root_key = folder_discovery_environment.root_identity(root)
        rows = [
            _role("SPDM (Admin)/Project_multi", "PROJECT", "Project_multi",
                  _generated_role(current_root_key, "SPDM (Admin)/Project_multi", "PROJECT")),
            _role("SPDM (Admin)/Project_multi/[WR-0001]_[유통_환경]", "REQUEST",
                  "[WR-0001]_[유통_환경]", _generated_role(current_root_key, "SPDM (Admin)/Project_multi/[WR-0001]_[유통_환경]", "REQUEST")),
        ]
        root_key, _ = _add_applied_relinked_preview(conn, root, registration, rows)
        with pytest.raises(folder_schema_resolver.FolderSchemaError) as exc_info:
            folder_schema_resolver._request_path(
                conn, root_key, registration["project_id"], registration["request_id"], "DISTRIBUTION",
            )
    assert exc_info.value.code == "FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED"


def test_relinked_preview_does_not_override_an_existing_foreign_project_target(capture_client):
    client, root = capture_client
    add_usage_case(root, "[WR-0001]_[유통_환경]")
    preview = preview_all(client, "DISTRIBUTION")
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"foreign-target-{uuid4()}", "capture": False,
    })
    with connect() as conn:
        registration["scan_id"] = preview["scan_id"]
        conn.execute(
            "UPDATE folder_environment_registry SET root_key=? WHERE registration_id=?",
            ["synthetic-prior-root", registration["registration_id"]],
        )
        conn.execute("UPDATE folder_environment_scans SET root_key=? WHERE id=?",
                     ["synthetic-prior-root", preview["scan_id"]])
        foreign_project_id = f"foreign-project-{uuid4()}"
        conn.execute(
            "INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
            [foreign_project_id, "Project_multi", "synthetic", None, folder_discovery_environment.now()],
        )
        current_root_key = folder_discovery_environment.root_identity(root)
        project_path = "SPDM (Admin)/Project_multi"
        request_path = project_path + "/[WR-0001]_[유통_환경]"
        rows = [
            _role(project_path, "PROJECT", "Project_multi", foreign_project_id),
            _role(request_path, "REQUEST", "[WR-0001]_[유통_환경]",
                  _generated_role(current_root_key, request_path, "REQUEST")),
        ]
        root_key, _ = _add_applied_relinked_preview(conn, root, registration, rows)
        with pytest.raises(folder_schema_resolver.FolderSchemaError) as exc_info:
            folder_schema_resolver._request_path(
                conn, root_key, registration["project_id"], registration["request_id"], "DISTRIBUTION",
            )
    assert exc_info.value.code == "FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED"


def test_relinked_unapplied_preview_cannot_supply_recovery_evidence(capture_client):
    client, root = capture_client
    add_usage_case(root, "[WR-0001]_[유통_환경]")
    preview = preview_all(client, "DISTRIBUTION")
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"unapplied-preview-{uuid4()}", "capture": False,
    })
    with connect() as conn:
        registration["scan_id"] = preview["scan_id"]
        conn.execute(
            "UPDATE folder_environment_registry SET root_key=? WHERE registration_id=?",
            ["synthetic-prior-root", registration["registration_id"]],
        )
        conn.execute("UPDATE folder_environment_scans SET root_key=? WHERE id=?",
                     ["synthetic-prior-root", preview["scan_id"]])
        current_root_key = folder_discovery_environment.root_identity(root)
        project_path = "SPDM (Admin)/Project_multi"
        request_path = project_path + "/[WR-0001]_[유통_환경]"
        rows = [
            _role(project_path, "PROJECT", "Project_multi",
                  _generated_role(current_root_key, project_path, "PROJECT")),
            _role(request_path, "REQUEST", "[WR-0001]_[유통_환경]",
                  _generated_role(current_root_key, request_path, "REQUEST")),
        ]
        root_key, synthetic_registration_id = _add_applied_relinked_preview(conn, root, registration, rows)
        conn.execute("UPDATE folder_environment_registrations SET status='DRAFT' WHERE id=?",
                     [synthetic_registration_id])
        with pytest.raises(folder_schema_resolver.FolderSchemaError) as exc_info:
            folder_schema_resolver._request_path(
                conn, root_key, registration["project_id"], registration["request_id"], "DISTRIBUTION",
            )
    assert exc_info.value.code == "FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED"


def test_projection_failure_is_saved_and_retry_can_complete(capture_client, monkeypatch):
    client, root = capture_client
    add_usage_case(root, "WR_retry_SimType1")
    preview = preview_all(client)
    original = folder_discovery_environment._registration_location_projection
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise folder_schema_resolver.FolderSchemaError(
                "FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED", "synthetic ambiguous request binding",
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(folder_discovery_environment, "_registration_location_projection", fail_once)
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"projection-retry-{uuid4()}", "capture": True,
    })
    assert registration["status"] == "FAILED"
    assert registration["capture_jobs"][0]["status"] == "FAILED"
    assert registration["capture_jobs"][0]["error_code"] == "FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED"

    retried = post(client, f"/registrations/{registration['registration_id']}/capture/retry", {})
    assert retried["status"] == "COMPLETED", retried
    assert retried["capture_jobs"][0]["status"] == "COMPLETED"
    assert retried["capture_jobs"][0]["capture_id"]

    with connect() as conn:
        assert conn.execute(
            "SELECT status FROM folder_environment_registrations WHERE id=?",
            [registration["registration_id"]],
        ).fetchone()[0] == "COMPLETED"


def test_distribution_manual_scene_registration_retries_two_case_captures(capture_client, monkeypatch):
    client, root = capture_client
    project = "75R9J_PV"
    wr1 = f"{project}/[WR-0001]_[유통_환경]/Working"
    case_paths = [
        f"{wr1}/Package_Model_SetCase1/Drop/85qn80h_ref_organized/INDIVIDUAL/2_Face",
        f"{wr1}/Package_Model_SetCase2/Drop/85qn80h_ref_organized/INDIVIDUAL/2_Face",
    ]
    package_paths = [case_path.split("/Drop/", 1)[0] for case_path in case_paths]
    filename = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
    for index, case_path in enumerate(case_paths, start=1):
        scene = root / case_path
        scene.mkdir(parents=True)
        (scene / filename).write_text(
            f"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,{index * 10},{index * 10},{index * 10},{index * 10}\n",
            encoding="utf-8",
        )
        (scene / "model.rad").write_text("/INCLUDE include.inc\n", encoding="utf-8")
        (scene / "include.inc").write_text("synthetic include\n", encoding="utf-8")
    (root / f"{project}/[WR-0002]_[유통_환경]/Working").mkdir(parents=True)

    scan = post(client, "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    scene_nodes = [node for node in scan["nodes"] if node["relative_path"] in case_paths]
    assert len(scene_nodes) == 2, [node["relative_path"] for node in scan["nodes"] if "2_Face" in node["relative_path"]]
    assignments = [
        {"node_id": node["id"], "role_kind": "SCENE", "confirm": True, "target_mode": "CREATE"}
        for node in scene_nodes
    ]
    preview = post(client, "/previews", {"scan_id": scan["id"], "assignments": assignments})
    assert preview["can_apply"], preview

    original = dashboard_capture.create_capture
    attempts = 0

    def fail_first_capture(conn, payload, *, actor):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise dashboard_capture.DashboardCaptureError("INJECTED_CAPTURE_FAILURE", "synthetic retry")
        return original(conn, payload, actor=actor)

    monkeypatch.setattr(dashboard_capture, "create_capture", fail_first_capture)
    registration = post(client, "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"distribution-scenes-{uuid4()}", "capture": True,
    })
    assert len(registration["capture_jobs"]) == 2, registration
    assert sorted(job["status"] for job in registration["capture_jobs"]) == ["COMPLETED", "FAILED"]

    retried = post(client, f"/registrations/{registration['registration_id']}/capture/retry", {})
    assert retried["status"] == "COMPLETED", retried
    assert len(retried["capture_jobs"]) == 2
    assert {job["status"] for job in retried["capture_jobs"]} == {"COMPLETED"}

    with connect() as conn:
        rows = conn.execute(
            "SELECT dc.relative_path,c.payload_json FROM folder_environment_capture_jobs j "
            "JOIN dashboard_captures c ON c.id=j.capture_id "
            "JOIN dashboard_cases dc ON dc.id=j.case_id WHERE j.registration_id=?",
            [registration["registration_id"]],
        ).fetchall()
    assert len(rows) == 2
    payloads_by_case = {str(relative_path): json.loads(payload) for relative_path, payload in rows}
    assert set(payloads_by_case) == set(package_paths)
    for package_path, payload in payloads_by_case.items():
        observations = [
            observation
            for run in payload.get("runs", [])
            for scene in run.get("scenes", [])
            for observation in scene.get("observations", [])
        ]
        assert observations, package_path
        assert any(
            item.get("source_path", "").endswith(filename)
            and case_path in f"{package_path}/" + item.get("source_path", "")
            for case_path in case_paths if case_path.startswith(package_path + "/")
            for item in observations
        ), package_path
