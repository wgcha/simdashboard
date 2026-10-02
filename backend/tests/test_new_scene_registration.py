"""A new DISTRIBUTION Scene created by the registration screen or copied directly.

Field report (2026-10-02, 4_Edge): folders/prepare 200, POST /drafts 409, and
the folder never appeared in the dashboard/materials catalogs. Causes fixed:
  * an unmatched new folder under RUN_OPTION was UNRESOLVED and skipped LEVEL
    inheritance, so the refresh never gave it the sibling SCENE role;
  * prepare returned path-builder ids instead of the Folder Schema candidate
    ids, so the draft context check failed with RESULT_CONTEXT_CHANGED.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app

pytestmark = pytest.mark.duckdb_integration
ENV = "/api/folder-discovery/environments"
REG = "/api/result-registration"

REQUEST = "75R9J_PV/[WR-0001]_[유통_환경]"
CASE = f"{REQUEST}/Working/Package_Model_SetCase2_CushionCase2_조건표시"
OPTION = f"{CASE}/Drop/85qn80h_ref_organized/INDIVIDUAL"
CSV = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C24_scene.h3d.csv"
CSV_BYTES = b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,20,20,20,20\n"


@pytest.fixture
def admin_client(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "new-scene-repro-isolated-test-secret-at-least-32")
    _, username, password = password_auth_bootstrap_admin
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield client, root


def _post(client, route, payload):
    response = client.post(route, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _seed(client, root):
    for scene in ("2_Face", "3_Face"):
        (root / OPTION / scene).mkdir(parents=True)
        (root / OPTION / scene / CSV).write_bytes(CSV_BYTES)
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": ""})
    # 2_Face / 3_Face do not match the default Scene name pattern; the user confirmed them as SCENE.
    assignments = [{"node_id": n["id"], "role_kind": "SCENE", "confirm": True}
                   for n in scan["nodes"] if n["relative_path"] in {f"{OPTION}/2_Face", f"{OPTION}/3_Face"}]
    assert len(assignments) == 2
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": assignments})
    assert preview["can_apply"], preview
    registered = _post(client, ENV + "/registrations", {
        "preview_id": preview["id"], "idempotency_key": f"repro-{uuid4()}", "capture": True,
    })
    with connect() as conn:
        request_id = conn.execute(
            "SELECT request_id FROM folder_environment_registrations WHERE id=?",
            [registered["registration_id"]],
        ).fetchone()[0]
    return registered["project_id"], request_id


def _prepare_new_scene(client, project_id, request_id):
    """Mirror ResultRegistrationWorkspace.missingPlan for DISTRIBUTION: parent=RUN_OPTION, one SCENE segment."""
    body = {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "parent_relative_path": OPTION, "segments": [{"role_kind": "SCENE", "name": "4_Edge"}]}
    preview = _post(client, REG + "/folders/prepare", {**body, "confirm_create": False})
    assert preview["status"] == "CONFIRM_REQUIRED"
    return _post(client, REG + "/folders/prepare", {**body, "confirm_create": True})


def _draft_body(project_id, request_id, prepared, context):
    return {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "case_relative_path": prepared["case_relative_path"],
            "result_relative_path": prepared["result_relative_path"], "context": context,
            "files": [{"relative_path": CSV, "size": len(CSV_BYTES), "sha256": None, "media_type": "text/csv"}]}


def _schema_node(project_id, request_id, path):
    from app.services import folder_schema_resolver
    with connect() as conn:
        schema = folder_schema_resolver.resolve_request_locations(conn, project_id, request_id, "DISTRIBUTION").as_dict()
    return next((n for n in schema["nodes"] if n["relative_path"] == path), None)


def test_new_empty_scene_is_registrable_and_listed(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    prepared = _prepare_new_scene(client, project_id, request_id)
    scene_path = f"{OPTION}/4_Edge"
    assert prepared["result_relative_path"] == scene_path
    assert (root / scene_path).is_dir()
    # The automatic scoped refresh runs and succeeds ...
    assert prepared["schema_refresh"]["status"] == "REFRESHED"
    node = _schema_node(project_id, request_id, scene_path)
    sibling = _schema_node(project_id, request_id, f"{OPTION}/2_Face")
    assert (sibling["role_kind"], sibling["status"]) == ("SCENE", "CONFIRMED")

    # Frontend (ResultRegistrationWorkspace.uploadAndInspect) posts prepared.context verbatim.
    draft = client.post(REG + "/drafts", json=_draft_body(project_id, request_id, prepared, prepared["context"]))
    _post(client, ENV + "/refresh", {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"})
    dash = client.get("/api/dashboard/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    mats = client.get("/api/materials/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert dash.status_code == mats.status_code == 200
    assert "2_Face" in dash.text and "2_Face" in mats.text

    observed = {"node": (node["role_kind"], node["status"], node.get("role_basis")),
                "draft": (draft.status_code, draft.json().get("detail", {}).get("code") if draft.status_code >= 400 else None),
                "dashboard_has_4_Edge": "4_Edge" in dash.text, "materials_has_4_Edge": "4_Edge" in mats.text}
    assert observed == {"node": ("SCENE", "CONFIRMED", "LEVEL"), "draft": (201, None),
                        "dashboard_has_4_Edge": True, "materials_has_4_Edge": True}, observed


def test_prepared_context_matches_schema_after_scene_is_confirmed(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    prepared = _prepare_new_scene(client, project_id, request_id)
    scene_path = f"{OPTION}/4_Edge"
    # Work around the missing role by confirming 4_Edge as SCENE explicitly, then refresh.
    scan = _post(client, ENV + "/scan", {"environment": "DISTRIBUTION", "relative_path": "",
                                         "project_id": project_id, "request_id": request_id})
    assignments = [{"node_id": n["id"], "role_kind": "SCENE", "confirm": True} for n in scan["nodes"]
                   if n["relative_path"] in {f"{OPTION}/2_Face", f"{OPTION}/3_Face", scene_path}]
    preview = _post(client, ENV + "/previews", {"scan_id": scan["id"], "assignments": assignments})
    _post(client, ENV + "/registrations", {"preview_id": preview["id"], "idempotency_key": f"confirm-{uuid4()}", "capture": False})
    _post(client, ENV + "/refresh", {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"})
    locations = client.get(REG + "/locations", params={"project_id": project_id, "request_id": request_id,
                                                        "environment": "DISTRIBUTION"}).json()
    candidate = next(c for c in locations["candidates"] if c["relative_path"] == scene_path)
    # paths._stable (sha256[:24]) vs folder_discovery_environment.stable (uuid5) give different scene ids.
    assert prepared["context"]["scene"]["id"] == candidate["context"]["scene"]["id"], (
        prepared["context"]["scene"]["id"], candidate["context"]["scene"]["id"])
    draft = client.post(REG + "/drafts", json=_draft_body(project_id, request_id, prepared, prepared["context"]))
    assert draft.status_code == 201, draft.text


def _refresh(client, project_id, request_id):
    return _post(client, ENV + "/refresh", {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"})


def test_directly_copied_scene_folder_inherits_scene_role(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    copied = root / OPTION / "5_Corner_any_name"
    copied.mkdir()
    (copied / CSV).write_bytes(CSV_BYTES)
    _refresh(client, project_id, request_id)
    node = _schema_node(project_id, request_id, f"{OPTION}/5_Corner_any_name")
    assert (node["role_kind"], node["status"], node.get("role_basis")) == ("SCENE", "CONFIRMED", "LEVEL")
    dash = client.get("/api/dashboard/catalog", params={"request_id": request_id, "project_id": project_id,
                                                        "environment": "DISTRIBUTION"})
    assert dash.status_code == 200 and "5_Corner_any_name" in dash.text


def test_scene_saved_unresolved_by_older_refresh_is_reinterpreted(admin_client):
    """Field state: an older refresh stored 4_Edge as UNRESOLVED/DEFAULT. The folder
    fingerprints do not change afterwards, yet the next refresh must re-interpret the
    old-revision snapshot once and give 4_Edge the SCENE role."""
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    (root / OPTION / "4_Edge").mkdir()
    _refresh(client, project_id, request_id)
    scene_path = f"{OPTION}/4_Edge"
    with connect() as conn:
        snap_id, tree = conn.execute(
            "SELECT id,tree_json FROM folder_environment_scans WHERE request_id=? AND id LIKE 'folder-refresh-%' "
            "ORDER BY created_at DESC,id DESC LIMIT 1", [request_id]).fetchone()
        snapshot = json.loads(tree)
        schema = snapshot["schema"]
        schema.pop("role_rules_revision", None)
        for node in schema["nodes"]:
            if node["relative_path"] == scene_path:
                node.update(role_kind=None, status="UNRESOLVED", role_basis="DEFAULT", confirmed=False,
                            option_status="UNRESOLVED", target_id=None)
        schema["confirmed_roles"] = {k: v for k, v in schema["confirmed_roles"].items()
                                     if v.get("relative_path") != scene_path}
        conn.execute("UPDATE folder_environment_scans SET tree_json=? WHERE id=?", [json.dumps(snapshot, ensure_ascii=False), snap_id])
    assert _schema_node(project_id, request_id, scene_path)["role_kind"] is None
    result = _refresh(client, project_id, request_id)
    assert result["status"] != "UNCHANGED", result
    node = _schema_node(project_id, request_id, scene_path)
    assert (node["role_kind"], node["status"], node.get("role_basis")) == ("SCENE", "CONFIRMED", "LEVEL")
    # A further refresh with the current revision reuses the snapshot.
    assert _refresh(client, project_id, request_id)["status"] == "UNCHANGED"


def test_folder_at_other_depth_does_not_become_scene(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    nested = root / OPTION / "2_Face" / "extra"
    nested.mkdir()
    _refresh(client, project_id, request_id)
    node = _schema_node(project_id, request_id, f"{OPTION}/2_Face/extra")
    assert node is None or node.get("role_kind") != "SCENE"


RUN = OPTION.rsplit("/", 1)[0]


def test_unrelated_folder_under_run_does_not_become_run_option(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    (root / RUN / "mesh_backup").mkdir()
    result = _refresh(client, project_id, request_id)
    assert result["status"] != "CONFLICT"
    node = _schema_node(project_id, request_id, f"{RUN}/mesh_backup")
    assert node is None or node.get("role_kind") is None


def test_mixed_roles_at_scene_level_never_block_refresh(admin_client):
    """A stray folder beside SCENE and RESULTS siblings stays undecided; the refresh still activates."""
    client, root = admin_client
    (root / OPTION / "Result").mkdir(parents=True)
    project_id, request_id = _seed(client, root)
    assert _refresh(client, project_id, request_id)["status"] != "CONFLICT"
    (root / OPTION / "notes").mkdir()
    for _ in range(2):
        assert _refresh(client, project_id, request_id)["status"] != "CONFLICT"
    body = {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "parent_relative_path": OPTION, "segments": [{"role_kind": "SCENE", "name": "4_Edge"}]}
    prepared = client.post(REG + "/folders/prepare", json={**body, "confirm_create": True})
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["schema_refresh"]["status"] != "FAILED", prepared.json()


def test_revision_only_refresh_creates_no_capture(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    _refresh(client, project_id, request_id)
    with connect() as conn:
        before = conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0]
        snap_id, tree = conn.execute(
            "SELECT id,tree_json FROM folder_environment_scans WHERE request_id=? AND id LIKE 'folder-refresh-%' "
            "ORDER BY created_at DESC,id DESC LIMIT 1", [request_id]).fetchone()
        snapshot = json.loads(tree)
        snapshot["schema"].pop("role_rules_revision", None)
        conn.execute("UPDATE folder_environment_scans SET tree_json=? WHERE id=?",
                     [json.dumps(snapshot, ensure_ascii=False), snap_id])
    result = _refresh(client, project_id, request_id)
    with connect() as conn:
        after = conn.execute("SELECT count(*) FROM dashboard_captures").fetchone()[0]
    assert (after, result["changed"]) == (before, False), (before, after, result["status"], result["diff"])
    assert _refresh(client, project_id, request_id)["status"] == "UNCHANGED"


def test_distribution_scene_proposes_itself_not_a_results_subfolder(admin_client):
    """Distribution inputs and results live directly in the Scene folder (user decision)."""
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    response = client.get(REG + "/folders", params={"project_id": project_id, "request_id": request_id,
                                                    "environment": "DISTRIBUTION", "parent_relative_path": OPTION})
    assert response.status_code == 200, response.text
    scene = next(node for node in response.json()["nodes"] if node["relative_path"] == f"{OPTION}/2_Face")
    assert (scene["suggested_relative_path"], scene["result_state"], scene["can_prepare"]) == (
        f"{OPTION}/2_Face", "PRESENT", False)
    assert not (root / OPTION / "2_Face" / "results").exists()


def test_distribution_scene_proposes_itself_not_a_results_subfolder(admin_client):
    """Distribution inputs and results live directly in the Scene folder (user decision)."""
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    response = client.get(REG + "/folders", params={"project_id": project_id, "request_id": request_id,
                                                    "environment": "DISTRIBUTION", "parent_relative_path": OPTION})
    assert response.status_code == 200, response.text
    scene = next(node for node in response.json()["nodes"] if node["relative_path"] == f"{OPTION}/2_Face")
    assert (scene["suggested_relative_path"], scene["result_state"], scene["can_prepare"]) == (
        f"{OPTION}/2_Face", "PRESENT", False)
    assert not (root / OPTION / "2_Face" / "results").exists()


def test_whole_solver_folder_uploads_into_scene_folder_itself(admin_client):
    """Users upload every analysis file; the dashboard reads what it recognises."""
    import hashlib
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    prepared = _prepare_new_scene(client, project_id, request_id)
    scene = f"{OPTION}/4_Edge"
    assert prepared["result_relative_path"] == scene
    payloads = {CSV: CSV_BYTES, "model_0000.rad": b"#RADIOSS STARTER\n/END\n",
                "sample_parts.inc": b"/PART/1\npart\n1 1\n", "solver.out": b"NORMAL TERMINATION\n",
                "anim.h3d": bytes(range(256)) * 4}
    files = [{"relative_path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
              "media_type": "text/csv" if name.endswith(".csv") else "application/octet-stream"}
             for name, data in payloads.items()]
    blocked = client.post(REG + "/drafts", json={**_draft_body(project_id, request_id, prepared, prepared["context"]),
                                                  "files": [{"relative_path": "run.exe", "size": 4, "sha256": None,
                                                             "media_type": "application/octet-stream"}]})
    assert blocked.status_code in {400, 409, 422} and blocked.json()["detail"]["code"] == "RESULT_FILE_TYPE_BLOCKED"
    created = client.post(REG + "/drafts", json={**_draft_body(project_id, request_id, prepared, prepared["context"]),
                                                  "files": files})
    assert created.status_code == 201, created.text
    draft_id = created.json()["draft_id"]
    uploaded = client.post(REG + f"/drafts/{draft_id}/files", data={"relative_paths": list(payloads)},
                           files=[("files", (name, data, "application/octet-stream")) for name, data in payloads.items()])
    assert uploaded.status_code == 200, uploaded.text
    inspected = client.post(REG + f"/drafts/{draft_id}/inspect")
    assert inspected.status_code == 200, inspected.text
    inspection = inspected.json()
    approved = client.post(REG + f"/drafts/{draft_id}/approve", json={
        "inspection_revision": inspection["inspection_revision"], "acknowledge_partial": True})
    assert approved.status_code == 200, approved.text
    published = client.post(REG + f"/drafts/{draft_id}/publish", json={
        "inspection_revision": inspection["inspection_revision"], "idempotency_key": f"whole-{uuid4().hex}"})
    assert published.status_code == 200, published.text
    for name, data in payloads.items():
        assert (root / scene / name).read_bytes() == data, name
    assert not (root / scene / "results").exists()
