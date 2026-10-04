"""Isolated read-only materials API contracts over synthetic SPDM folders."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services import folder_discovery_environment, materials_catalog

pytestmark = pytest.mark.duckdb_integration


@pytest.fixture(autouse=True)
def _legacy_profiles(isolated_database):
    """These scenarios use legacy folder layouts: run them as a pre-0034 database."""
    from tests.legacy_environment_profiles import activate_legacy_profiles
    activate_legacy_profiles()
BASE = "/api/materials"


def _cells(*values: object, width: int = 10) -> str:
    return "".join(f"{value:>{width}}" for value in values)


def _parts_deck() -> str:
    return "/PART/1\nSynthetic part\n" + _cells(0, 2, 0) + f"{1.5:>20}" + "\n"


def _materials_deck() -> str:
    return (
        "/MAT/ELAST/2\nSynthetic material\n"
        f"{1.2:>20}\n{1000.0:>20}{0.3:>20}\n"
        "/FUNCT/10\nSynthetic curve\n"
        f"{0.0:>20}{0.0:>20}\n{1.0:>20}{2.0:>20}\n"
    )


def _base_distribution_rules() -> list[dict[str, object]]:
    return [
        {"role_kind": "SIMULATION_CASE", "pattern": "Assy_Model", "parent_role": "REQUEST"},
        {"role_kind": "LOAD_CASE", "pattern": "Drop", "parent_role": "SIMULATION_CASE"},
        {"role_kind": "EXECUTION_RUN", "pattern": "Run_*", "parent_role": "LOAD_CASE"},
        {"role_kind": "RUN_OPTION", "pattern": "INDIVIDUAL", "parent_role": "EXECUTION_RUN"},
        {"role_kind": "SCENE", "pattern": "*Scene*", "parent_role": "RUN_OPTION"},
        {"role_kind": "INPUT", "pattern": "INPUT", "parent_role": "EXECUTION_RUN"},
    ]


def _save_distribution_scan(root: Path, project_id: str, request_id: str,
                            rules: list[dict[str, object]], *, scan_path: str | None = None) -> tuple[str, str]:
    from app.services import environment_folder_profiles

    profile_id = f"materials-profile-{uuid4().hex}"
    with connect() as conn:
        profile = environment_folder_profiles.save_profile(
            conn, environment="DISTRIBUTION", name=profile_id,
            rules={"rules": rules},
        )
        default_path = str(conn.execute(
            "SELECT request_folder FROM spdm_storage_request_parents WHERE request_id=?", [request_id],
        ).fetchone()[0])
        scan = folder_discovery_environment.save_scan(
            conn, root, default_path if scan_path is None else scan_path,
            "DISTRIBUTION", profile["id"], project_id, request_id, "synthetic-test",
        )
    return str(profile["id"]), str(scan["id"])


def _delete_distribution_scan(profile_id: str, scan_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM folder_environment_previews WHERE scan_id=?", [scan_id])
        conn.execute("DELETE FROM folder_environment_scans WHERE id=?", [scan_id])
        conn.execute("DELETE FROM folder_environment_profiles WHERE id=?", [profile_id])


@pytest.fixture
def materials_client(tmp_path, monkeypatch, password_auth_bootstrap_admin):
    root = tmp_path / "spdm"
    suffix = uuid4().hex[:10]
    project_folder = f"Project_Materials_{suffix}_Model_1"
    request_folder = f"{project_folder}/WR_Materials_{suffix}_SimType2"
    scene_relative = f"{request_folder}/CAE/Assy_Model/Drop/Run_01/INDIVIDUAL/Scene_01"
    empty_scene_relative = f"{request_folder}/CAE/Assy_Model/Drop/Run_01/INDIVIDUAL/Scene_Empty"
    scene = root.joinpath(*scene_relative.split("/"))
    scene.mkdir(parents=True)
    root.joinpath(*empty_scene_relative.split("/")).mkdir(parents=True)
    (scene / "parts.inc").write_text(_parts_deck(), encoding="utf-8")
    (scene / "materials.inc").write_text(_materials_deck(), encoding="utf-8")
    (scene / "starter.rad").write_text("/TITLE\nSynthetic starter\n", encoding="utf-8")

    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "materials-api-isolated-secret-key-at-least-32")
    admin_id, admin_username, admin_password = password_auth_bootstrap_admin
    project_id, request_id = f"materials-project-{suffix}", f"materials-request-{suffix}"
    viewer_id, viewer_password = f"materials-viewer-{suffix}", "materials-viewer-password"
    profile_id = f"materials-profile-{suffix}"
    scan_id = None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                     [project_id, "Synthetic Materials Project", "Synthetic", "isolated API fixture", now])
        conn.execute("""INSERT INTO analysis_requests
            (id,project_id,title,status,owner,owner_user_id,requested_at,due_at,overall_note)
            VALUES(?,?,?,'READY','test',NULL,?,?,?)""",
                     [request_id, project_id, "Synthetic Materials Request", now, now, ""])
        conn.execute("INSERT INTO spdm_storage_project_parents(project_folder,project_id,created_at,updated_at) VALUES(?,?,?,?)",
                     [project_folder, project_id, now, now])
        conn.execute("INSERT INTO spdm_storage_request_parents(request_folder,project_folder,project_id,request_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                     [request_folder, project_folder, project_id, request_id, now, now])
        from app.services import environment_folder_profiles
        profile = environment_folder_profiles.save_profile(
            conn, environment="DISTRIBUTION", name=profile_id,
            rules={"rules": _base_distribution_rules()},
        )
        scan = folder_discovery_environment.save_scan(
            conn, root, request_folder, "DISTRIBUTION", profile["id"], project_id, request_id, "synthetic-test",
        )
        scan_id = str(scan["id"])
        conn.execute("""INSERT INTO users
            (id,username,password_hash,display_name,legacy_role,account_status,is_global_admin,is_active,created_at,updated_at)
            VALUES(?,?,?,?,'viewer','ACTIVE',false,true,?,?)""",
                     [viewer_id, viewer_id, hash_password(viewer_password), "Synthetic viewer", now, now])
    try:
        with TestClient(app) as client:
            admin_login = client.post("/api/auth/login", json={"username": admin_username, "password": admin_password})
            assert admin_login.status_code == 200, admin_login.text
            client.headers["Authorization"] = f"Bearer {admin_login.json()['access_token']}"
            viewer_login = client.post("/api/auth/login", json={"username": viewer_id, "password": viewer_password})
            assert viewer_login.status_code == 200, viewer_login.text
            yield client, root, project_id, request_id, request_folder, scene_relative, empty_scene_relative, viewer_login.json()["access_token"]
    finally:
        with connect() as conn:
            if scan_id:
                conn.execute("DELETE FROM folder_environment_previews WHERE scan_id=?", [scan_id])
                conn.execute("DELETE FROM folder_environment_scans WHERE id=?", [scan_id])
                conn.execute("DELETE FROM folder_environment_profiles WHERE id=?", [profile_id])
            conn.execute("DELETE FROM project_memberships WHERE user_id=?", [viewer_id])
            conn.execute("DELETE FROM users WHERE id=?", [viewer_id])
            conn.execute("DELETE FROM spdm_storage_request_parents WHERE request_id=?", [request_id])
            conn.execute("DELETE FROM spdm_storage_project_parents WHERE project_id=?", [project_id])
            conn.execute("DELETE FROM analysis_requests WHERE id=?", [request_id])
            conn.execute("DELETE FROM projects WHERE id=?", [project_id])


def test_materials_catalog_and_deck_are_request_scoped_and_uncached(materials_client):
    client, _, _, request_id, _, scene_relative, empty_scene_relative, _ = materials_client
    catalog = client.get(BASE + "/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["environment"] == "DISTRIBUTION"
    scenes = catalog.json()["scenes"]
    scene = next(item for item in scenes if item["relative_path"] == scene_relative)
    empty_scene = next(item for item in scenes if item["relative_path"] == empty_scene_relative)
    assert scene["has_deck"] is True
    assert scene["hierarchy"]["load_case"]["label"] == "Drop"
    assert empty_scene["has_deck"] is False

    response = client.get(BASE + "/deck", params={"request_id": request_id, "environment": "DISTRIBUTION",
                                                   "scene_id": scene["scene_id"], "relative_path": scene_relative})
    assert response.status_code == 200, response.text
    deck = response.json()["deck"]
    assert [part["id"] for part in deck["parts"]] == ["1"]
    assert [material["id"] for material in deck["materials"]] == ["2"]
    assert deck["functions"][0]["point_count"] == 2
    assert deck["functions"][0]["points"] == [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 2.0}]
    assert "raw_points" not in deck["functions"][0]
    assert {item["relative_path"] for item in response.json()["files"]} == {
        f"{scene_relative}/parts.inc", f"{scene_relative}/materials.inc",
    }

    empty = client.get(BASE + "/deck", params={"request_id": request_id, "scene_id": empty_scene["scene_id"]})
    assert empty.status_code == 404
    assert empty.json()["detail"]["code"] == "MATERIALS_DECK_NOT_FOUND"

    # The next read sees changed source contents; the API keeps no parsed-deck cache.
    (materials_client[1].joinpath(*scene_relative.split("/")) / "parts.inc").write_text("", encoding="utf-8")
    fresh = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["deck"]["parts"] == []


def test_materials_request_scope_uses_schema_path_without_result_scope(materials_client, monkeypatch):
    _, _, _, request_id, request_folder, _, _, _ = materials_client

    def forbidden_scope(*args, **kwargs):
        raise AssertionError("materials catalog must not depend on result-registration scope")

    monkeypatch.setattr(materials_catalog.result_registration_paths, "_scope", forbidden_scope)
    with connect() as conn:
        _, scope, _, _, _, _ = materials_catalog._request_scope(conn, request_id, "DISTRIBUTION")
    assert scope["request_relative_path"] == request_folder


def test_materials_requires_a_request_linked_scan_instead_of_defaulting_a_profile(materials_client):
    client, root, project_id, _, project_request_folder, _, _, _ = materials_client
    suffix = uuid4().hex[:10]
    request_id = f"materials-no-scan-{suffix}"
    request_folder = f"{PurePosixPath(project_request_folder).parent}/WR_NoScan_{suffix}_SimType2"
    root.joinpath(*request_folder.split("/")).mkdir(parents=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute("""INSERT INTO analysis_requests
            (id,project_id,title,status,owner,owner_user_id,requested_at,due_at,overall_note)
            VALUES(?,?,?,'READY','test',NULL,?,?,?)""",
                     [request_id, project_id, "Synthetic No Scan Request", now, now, ""])
        conn.execute("INSERT INTO spdm_storage_request_parents(request_folder,project_folder,project_id,request_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                     [request_folder, str(PurePosixPath(project_request_folder).parent), project_id, request_id, now, now])
    try:
        response = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "FOLDER_SCHEMA_REQUEST_SCAN_REQUIRED"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM spdm_storage_request_parents WHERE request_id=?", [request_id])
            conn.execute("DELETE FROM analysis_requests WHERE id=?", [request_id])


def test_materials_can_rebuild_scene_tree_from_request_linked_root_scan(materials_client):
    client, root, project_id, request_id, _, scene_relative, _, _ = materials_client
    profile_id, scan_id = _save_distribution_scan(
        root, project_id, request_id, _base_distribution_rules(), scan_path="",
    )
    try:
        catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert catalog.status_code == 200, catalog.text
        scene = next(item for item in catalog.json()["scenes"] if item["relative_path"] == scene_relative)
        assert scene["hierarchy"]["load_case"]["name"] == "Drop"
    finally:
        _delete_distribution_scan(profile_id, scan_id)


def test_materials_rejects_tied_request_scans_instead_of_choosing_one(materials_client):
    client, root, project_id, request_id, _, _, _, _ = materials_client
    profile_id, scan_id = _save_distribution_scan(root, project_id, request_id, _base_distribution_rules())
    tied_at = datetime(2026, 9, 30, 12, 0, 0)
    with connect() as conn:
        conn.execute("UPDATE folder_environment_scans SET created_at=? WHERE project_id=? AND request_id=?",
                     [tied_at, project_id, request_id])
    try:
        response = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "FOLDER_SCHEMA_SCAN_AMBIGUOUS"
    finally:
        _delete_distribution_scan(profile_id, scan_id)


def test_materials_does_not_infer_standalone_results_by_name(materials_client):
    client, root, _, request_id, request_folder, _, _, _ = materials_client
    result_relative = (f"{request_folder}/CAE/Assy_Model/Drop/Run_02/INDIVIDUAL/"
                       "DAMP-2_Face/results")
    result_path = root.joinpath(*result_relative.split("/"))
    result_path.mkdir(parents=True)
    (result_path / "101_parts.inc").write_text(_parts_deck().replace("/PART/1", "/PART/9"), encoding="utf-8")
    (result_path / "103_material_propertdb.inc").write_text(
        _materials_deck().replace("/MAT/ELAST/2", "/MAT/ELAST/99"), encoding="utf-8",
    )

    unrelated = root.joinpath(*f"{request_folder}/documents/results".split("/"))
    unrelated.mkdir(parents=True)
    (unrelated / "parts.inc").write_text(_parts_deck(), encoding="utf-8")
    (unrelated / "materials.inc").write_text(_materials_deck(), encoding="utf-8")

    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    assert catalog.status_code == 200, catalog.text
    entries = catalog.json()["scenes"]
    assert all(item["relative_path"] != result_relative for item in entries)
    assert all("documents/results" not in item["relative_path"] for item in entries)

    response = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": result_relative})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MATERIALS_SCENE_INVALID"


def test_materials_scene_decks_can_be_in_their_results_subfolder(materials_client):
    client, root, _, request_id, _, _, empty_scene_relative, _ = materials_client
    result_relative = f"{empty_scene_relative}/results"
    result_path = root.joinpath(*result_relative.split("/"))
    result_path.mkdir(parents=True)
    (result_path / "parts.inc").write_text(_parts_deck().replace("/PART/1", "/PART/7"), encoding="utf-8")
    (result_path / "materials.inc").write_text(_materials_deck(), encoding="utf-8")

    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    assert catalog.status_code == 200, catalog.text
    empty_scene = next(item for item in catalog.json()["scenes"] if item["relative_path"] == empty_scene_relative)
    assert empty_scene["kind"] == "SCENE"
    assert empty_scene["has_deck"] is True
    assert all(item["relative_path"] != result_relative for item in catalog.json()["scenes"])
    response = client.get(BASE + "/deck", params={"request_id": request_id, "scene_id": empty_scene["scene_id"]})
    assert response.status_code == 200, response.text
    assert [part["id"] for part in response.json()["deck"]["parts"]] == ["7"]


def test_materials_uses_registered_scene_roles_and_does_not_mistake_parts_preamble_for_materials(materials_client):
    client, root, project_id, request_id, request_folder, _, _, _ = materials_client
    scene_relative = (
        f"{request_folder}/Working/Package_Model_Synthetic/Drop/85qn80h_ref_organized/"
        "INDIVIDUAL/DAMP-2_Face_Drop_Scene02_Face2_1st"
    )
    scene_path = root.joinpath(*scene_relative.split("/"))
    scene_path.mkdir(parents=True)
    parts = (
        "/BEGIN\nSynthetic units\n"
        "/PARAMETER/REAL/1\nSynthetic parameter\n"
        "/SUBSET/7\nSynthetic subset\n"
        + _parts_deck()
    )
    (scene_path / "101_parts.inc").write_text(parts, encoding="utf-8")
    (scene_path / "103_material_propertdb.inc").write_text(_materials_deck(), encoding="utf-8")

    parts_only_relative = f"{PurePosixPath(scene_relative).parent.as_posix()}/DAMP-2_Face_Parts_Only"
    parts_only = root.joinpath(*parts_only_relative.split("/"))
    parts_only.mkdir()
    (parts_only / "101_parts.inc").write_text(parts, encoding="utf-8")
    results_scene_relative = f"{PurePosixPath(scene_relative).parent.as_posix()}/DAMP-2_Face_Registered_Result_Path"
    scene_results_relative = f"{results_scene_relative}/results"
    scene_results = root.joinpath(*scene_results_relative.split("/"))
    scene_results.mkdir(parents=True)
    (scene_results / "101_parts.inc").write_text(_parts_deck(), encoding="utf-8")
    (scene_results / "103_material_propertdb.inc").write_text(_materials_deck(), encoding="utf-8")
    wrong_scene_relative = f"{PurePosixPath(scene_relative).parent.as_posix()}/DAMP-2_Face_Unregistered_Scene"
    wrong_scene = root.joinpath(*wrong_scene_relative.split("/"))
    wrong_scene.mkdir()
    (wrong_scene / "101_parts.inc").write_text(_parts_deck(), encoding="utf-8")
    (wrong_scene / "103_material_propertdb.inc").write_text(_materials_deck(), encoding="utf-8")
    wrong_scene_results_relative = f"{wrong_scene_relative}/results"
    wrong_scene_results = root.joinpath(*wrong_scene_results_relative.split("/"))
    wrong_scene_results.mkdir()
    (wrong_scene_results / "101_parts.inc").write_text(_parts_deck(), encoding="utf-8")
    (wrong_scene_results / "103_material_propertdb.inc").write_text(_materials_deck(), encoding="utf-8")

    from app.services import folder_discovery_environment

    root_key = folder_discovery_environment.root_identity(root)
    registration_id, preview_id, scan_id = (f"materials-env-{uuid4().hex}" for _ in range(3))
    package_path = f"{request_folder}/Working/Package_Model_Synthetic"
    load_case_path = f"{package_path}/Drop"
    execution_path = f"{load_case_path}/85qn80h_ref_organized"
    option_path = f"{execution_path}/INDIVIDUAL"
    role_rows = [
        {"relative_path": package_path, "role_kind": "SIMULATION_CASE", "name": "Package_Model_Synthetic",
         "status": "CONFIRMED", "target_id": f"case-{uuid4().hex}"},
        {"relative_path": load_case_path, "role_kind": "LOAD_CASE", "name": "Drop",
         "status": "CONFIRMED", "target_id": f"load-{uuid4().hex}"},
        {"relative_path": execution_path, "role_kind": "EXECUTION_RUN", "name": "85qn80h_ref_organized",
         "status": "CONFIRMED", "target_id": f"run-{uuid4().hex}"},
        {"relative_path": option_path, "role_kind": "RUN_OPTION", "name": "INDIVIDUAL",
         "status": "CONFIRMED", "option_status": "PRESENT", "target_id": f"option-{uuid4().hex}"},
        {"relative_path": scene_relative, "role_kind": "SCENE",
         "name": "DAMP-2_Face_Drop_Scene02_Face2_1st", "status": "CONFIRMED"},
        {"relative_path": parts_only_relative, "role_kind": "SCENE",
         "name": "DAMP-2_Face_Parts_Only", "status": "CONFIRMED"},
        {"relative_path": results_scene_relative, "role_kind": "SCENE",
         "name": "DAMP-2_Face_Registered_Result_Path", "status": "CONFIRMED"},
    ]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        profile = conn.execute(
            "SELECT id,revision FROM folder_environment_profiles WHERE environment='DISTRIBUTION' "
            "ORDER BY created_at LIMIT 1",
        ).fetchone()
        assert profile is not None
        conn.execute(
            "INSERT INTO folder_environment_scans "
            "(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,request_id,status,"
            "tree_json,issues_json,created_by,created_at) VALUES(?,?,?,'DISTRIBUTION',?,?,?,?, 'COMPLETE',?,?,?,?)",
            [scan_id, root_key, request_folder, profile[0], profile[1], project_id, request_id,
             json.dumps(role_rows, ensure_ascii=False), "[]", "synthetic-test", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_previews(id,scan_id,rows_json,can_apply,created_by,created_at) "
            "VALUES(?,?,?,true,?,?)",
            [preview_id, scan_id, json.dumps(role_rows, ensure_ascii=False), "synthetic-test", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_registrations "
            "(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) "
            "VALUES(?,?,?,'DISTRIBUTION',?,?,'COMPLETED',?,?)",
            [registration_id, preview_id, f"materials-env-key-{uuid4().hex}", project_id, request_id,
             "synthetic-test", now],
        )
        for item in role_rows:
            if item["role_kind"] in {"SCENE"}:
                continue  # Applied plans retain SCENE in preview rows by design.
            conn.execute(
                "INSERT INTO folder_environment_registry "
                "(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,"
                "option_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [f"materials-env-role-{uuid4().hex}", registration_id, root_key, item["relative_path"],
                 item["role_kind"], None, item["target_id"], item["name"], item.get("option_status"), now],
            )

    try:
        catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert catalog.status_code == 200, catalog.text
        scenes = {item["relative_path"]: item for item in catalog.json()["scenes"]}
        scene = scenes[scene_relative]
        assert scene["label"] == "DAMP-2_Face_Drop_Scene02_Face2_1st"
        assert scene["hierarchy"]["load_case"]["label"] == "Drop"
        assert scene["has_deck"] is True
        results_scene = scenes[results_scene_relative]
        assert results_scene["has_deck"] is True
        assert scene_results_relative not in scenes

        response = client.get(BASE + "/deck", params={"request_id": request_id, "scene_id": scene["scene_id"]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert [part["id"] for part in body["deck"]["parts"]] == ["1"]
        assert body["deck"]["parts"][0]["references"]["material_status"] == "resolved"
        assert body["deck"]["parts"][0]["material"]["id"] == "2"
        assert {item["relative_path"] for item in body["files"]} == {
            f"{scene_relative}/101_parts.inc",
            f"{scene_relative}/103_material_propertdb.inc",
        }

        parts_only_entry = scenes[parts_only_relative]
        assert parts_only_entry["has_deck"] is False
        missing_material = client.get(BASE + "/deck", params={"request_id": request_id,
                                                                "scene_id": parts_only_entry["scene_id"]})
        assert missing_material.status_code == 404
        assert missing_material.json()["detail"]["code"] == "MATERIALS_DECK_NOT_FOUND"

        results_response = client.get(BASE + "/deck", params={"request_id": request_id,
                                                                "scene_id": results_scene["scene_id"]})
        assert results_response.status_code == 200, results_response.text
        assert {item["relative_path"] for item in results_response.json()["files"]} == {
            f"{scene_results_relative}/101_parts.inc",
            f"{scene_results_relative}/103_material_propertdb.inc",
        }

        # With a schema applied, a misleading Scene-shaped sibling with decks
        # remains out of the catalog even when the legacy name heuristic matches.
        authoritative_catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert authoritative_catalog.status_code == 200, authoritative_catalog.text
        authoritative_scenes = [item["relative_path"] for item in authoritative_catalog.json()["scenes"]]
        assert wrong_scene_relative not in authoritative_scenes
        assert wrong_scene_results_relative not in authoritative_scenes
        assert scene_results_relative not in authoritative_scenes
        assert authoritative_scenes.count(results_scene_relative) == 1
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM folder_environment_registry WHERE registration_id=?", [registration_id])
            conn.execute("DELETE FROM folder_environment_registrations WHERE id=?", [registration_id])
            conn.execute("DELETE FROM folder_environment_previews WHERE id=?", [preview_id])
            conn.execute("DELETE FROM folder_environment_scans WHERE id=?", [scan_id])


def test_materials_uses_saved_schema_for_actual_nested_scenes_not_old_false_match(materials_client):
    client, root, project_id, request_id, request_folder, _, _, _ = materials_client
    case_path = f"{request_folder}/Working/Package_Model_SetCase1_CushionCase2_조건표시"
    run_path = f"{case_path}/Drop/85qn80h_ref_organized"
    option_path = f"{run_path}/INDIVIDUAL"
    expected_scenes = [
        f"{option_path}/DAMP-2_Face_Drop_Scene02_Face2_1st",
        f"{option_path}/DAMP-6_Corner_Drop_2nd_Scene04_Corner235_2nd",
        f"{option_path}/DAMP-13_Corner_Drop_2nd_Scene08_Corner346_2nd",
    ]
    old_false_match = f"{request_folder}/Pkg_SetCase2_Cushion2_test/Drop/85qn80h_test/2_face"
    for index, relative in enumerate(expected_scenes, start=2):
        scene_path = root.joinpath(*relative.split("/"))
        scene_path.mkdir(parents=True)
        (scene_path / "parts.inc").write_text(
            _parts_deck().replace("/PART/1", f"/PART/{index}"), encoding="utf-8",
        )
        (scene_path / "materials.inc").write_text(
            _materials_deck().replace("/MAT/ELAST/2", f"/MAT/ELAST/{index}"), encoding="utf-8",
        )
    old_path = root.joinpath(*old_false_match.split("/"))
    old_path.mkdir(parents=True)
    (old_path / "parts.inc").write_text(_parts_deck().replace("/PART/1", "/PART/99"), encoding="utf-8")
    (old_path / "materials.inc").write_text(
        _materials_deck().replace("/MAT/ELAST/2", "/MAT/ELAST/99"), encoding="utf-8",
    )

    profile_id, scan_id = _save_distribution_scan(root, project_id, request_id, [
        {"role_kind": "SIMULATION_CASE", "pattern": "Package_Model_SetCase1_CushionCase2_*",
         "parent_role": "REQUEST"},
        {"role_kind": "LOAD_CASE", "pattern": "Drop", "parent_role": "SIMULATION_CASE"},
        {"role_kind": "EXECUTION_RUN", "pattern": "85qn80h_ref_organized", "parent_role": "LOAD_CASE"},
        {"role_kind": "RUN_OPTION", "pattern": "INDIVIDUAL", "parent_role": "EXECUTION_RUN"},
        {"role_kind": "SCENE", "pattern": "DAMP-*_Scene*", "parent_role": "RUN_OPTION"},
    ], scan_path=case_path)
    try:
        catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert catalog.status_code == 200, catalog.text
        scenes = {item["relative_path"]: item for item in catalog.json()["scenes"]}
        assert all(relative in scenes for relative in expected_scenes)
        assert all(scenes[relative]["has_deck"] is True for relative in expected_scenes)
        assert old_false_match not in scenes

        selected = scenes[expected_scenes[1]]
        deck = client.get(BASE + "/deck", params={"request_id": request_id,
                                                    "scene_id": selected["scene_id"],
                                                    "relative_path": expected_scenes[1]})
        assert deck.status_code == 200, deck.text
        assert [item["id"] for item in deck.json()["deck"]["materials"]] == ["3"]

        rejected = client.get(BASE + "/deck", params={"request_id": request_id,
                                                        "relative_path": old_false_match})
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "MATERIALS_SCENE_INVALID"
    finally:
        _delete_distribution_scan(profile_id, scan_id)


def test_materials_current_schema_replaces_older_registered_scene_discovery(materials_client):
    client, root, project_id, request_id, request_folder, _, _, _ = materials_client
    old_case = f"{request_folder}/Pkg_SetCase2_Cushion2_test"
    old_run = f"{old_case}/Drop/85qn80h_test"
    registered_scene = f"{old_run}/2_face"
    unregistered_sibling = f"{old_run}/DAMP-2_Face_Unregistered_Scene"
    actual_scene = (
        f"{request_folder}/Working/Package_Model_SetCase1_CushionCase2_조건표시/Drop/"
        "85qn80h_ref_organized/INDIVIDUAL/DAMP-2_Face_Drop_Scene02_Face2_1st"
    )
    actual_scenes = (
        actual_scene,
        str(PurePosixPath(actual_scene).parent / "DAMP-6_Corner_Drop_2nd_Scene04_Corner235_2nd"),
        str(PurePosixPath(actual_scene).parent / "DAMP-13_Corner_Drop_2nd_Scene08_Corner346_2nd"),
    )
    for relative in (registered_scene, unregistered_sibling, *actual_scenes):
        directory = root.joinpath(*relative.split("/"))
        directory.mkdir(parents=True)
        (directory / "101_parts.inc").write_text(_parts_deck(), encoding="utf-8")
        (directory / "103_material_propertdb.inc").write_text(_materials_deck(), encoding="utf-8")

    from app.services import folder_discovery_environment

    root_key = folder_discovery_environment.root_identity(root)
    registration_id, preview_id, scan_id = (f"materials-case-auth-{uuid4().hex}" for _ in range(3))
    role_rows = [
        {"relative_path": old_case, "role_kind": "SIMULATION_CASE", "name": "Pkg_SetCase2_Cushion2_test",
         "status": "CONFIRMED", "target_id": f"case-{uuid4().hex}"},
        {"relative_path": f"{old_case}/Drop", "role_kind": "LOAD_CASE", "name": "Drop",
         "status": "CONFIRMED", "target_id": f"load-{uuid4().hex}"},
        {"relative_path": old_run, "role_kind": "EXECUTION_RUN", "name": "85qn80h_test",
         "status": "CONFIRMED", "target_id": f"run-{uuid4().hex}"},
        {"relative_path": registered_scene, "role_kind": "SCENE", "name": "2_face",
         "status": "CONFIRMED"},
    ]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        profile = conn.execute(
            "SELECT id,revision FROM folder_environment_profiles WHERE environment='DISTRIBUTION' "
            "ORDER BY created_at LIMIT 1",
        ).fetchone()
        assert profile is not None
        conn.execute(
            "INSERT INTO folder_environment_scans "
            "(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,request_id,status,"
            "tree_json,issues_json,created_by,created_at) VALUES(?,?,?,'DISTRIBUTION',?,?,?,?, 'COMPLETE',?,?,?,?)",
            [scan_id, root_key, request_folder, profile[0], profile[1], project_id, request_id,
             json.dumps(role_rows, ensure_ascii=False), "[]", "synthetic-test", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_previews(id,scan_id,rows_json,can_apply,created_by,created_at) "
            "VALUES(?,?,?,true,?,?)",
            [preview_id, scan_id, json.dumps(role_rows, ensure_ascii=False), "synthetic-test", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_registrations "
            "(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) "
            "VALUES(?,?,?,'DISTRIBUTION',?,?,'COMPLETED',?,?)",
            [registration_id, preview_id, f"materials-case-auth-key-{uuid4().hex}", project_id, request_id,
             "synthetic-test", now],
        )
        for item in role_rows:
            if item["role_kind"] == "SCENE":
                continue
            conn.execute(
                "INSERT INTO folder_environment_registry "
                "(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,"
                "option_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [f"materials-case-auth-role-{uuid4().hex}", registration_id, root_key, item["relative_path"],
                 item["role_kind"], None, item["target_id"], item["name"], item.get("option_status"), now],
            )

    current_profile_id, current_scan_id = _save_distribution_scan(root, project_id, request_id, [
        {"role_kind": "SIMULATION_CASE", "pattern": "Package_Model_SetCase1_CushionCase2_*",
         "parent_role": "REQUEST"},
        {"role_kind": "LOAD_CASE", "pattern": "Drop", "parent_role": "SIMULATION_CASE"},
        {"role_kind": "EXECUTION_RUN", "pattern": "85qn80h_ref_organized", "parent_role": "LOAD_CASE"},
        {"role_kind": "RUN_OPTION", "pattern": "INDIVIDUAL", "parent_role": "EXECUTION_RUN"},
        {"role_kind": "SCENE", "pattern": "DAMP-*_Scene*", "parent_role": "RUN_OPTION"},
    ])

    try:
        catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert catalog.status_code == 200, catalog.text
        scenes = {item["relative_path"]: item for item in catalog.json()["scenes"]}
        assert registered_scene not in scenes
        assert all(scene in scenes for scene in actual_scenes)
        assert unregistered_sibling not in scenes
        assert scenes[actual_scene]["hierarchy"]["simulation_case"]["relative_path"].endswith(
            "Package_Model_SetCase1_CushionCase2_조건표시",
        )

        actual_deck = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": actual_scene})
        assert actual_deck.status_code == 200, actual_deck.text
        assert [item["id"] for item in actual_deck.json()["deck"]["materials"]] == ["2"]

        registered_deck = client.get(BASE + "/deck", params={"request_id": request_id,
                                                                 "relative_path": registered_scene})
        assert registered_deck.status_code == 422
        assert registered_deck.json()["detail"]["code"] == "MATERIALS_SCENE_INVALID"

        sibling_deck = client.get(BASE + "/deck", params={"request_id": request_id,
                                                             "relative_path": unregistered_sibling})
        assert sibling_deck.status_code == 422
        assert sibling_deck.json()["detail"]["code"] == "MATERIALS_SCENE_INVALID"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM folder_environment_registry WHERE registration_id=?", [registration_id])
            conn.execute("DELETE FROM folder_environment_registrations WHERE id=?", [registration_id])
            conn.execute("DELETE FROM folder_environment_previews WHERE id=?", [preview_id])
            conn.execute("DELETE FROM folder_environment_scans WHERE id=?", [scan_id])
        _delete_distribution_scan(current_profile_id, current_scan_id)


def test_latest_same_profile_schema_replaces_old_roles_only_in_its_confirmed_scope(materials_client):
    client, root, project_id, request_id, request_folder, _, _, _ = materials_client
    old_case = f"{request_folder}/Pkg_SetCase2_Cushion2_test"
    old_scene = f"{old_case}/Drop/85qn80h_test/2_face"
    current_case = f"{request_folder}/Working/Package_Model_SetCase1_CushionCase2_조건표시"
    current_scene = (
        f"{current_case}/Drop/85qn80h_ref_organized/INDIVIDUAL/"
        "DAMP-2_Face_Drop_Scene02_Face2_1st"
    )
    # This is a separate, valid schema branch already present in the fixture.
    other_valid_case = f"{request_folder}/CAE/Assy_Model"
    for relative in (old_scene, current_scene):
        scene_path = root.joinpath(*relative.split("/"))
        scene_path.mkdir(parents=True)
        (scene_path / "101_parts.inc").write_text(_parts_deck(), encoding="utf-8")
        (scene_path / "103_material_propertdb.inc").write_text(_materials_deck(), encoding="utf-8")

    from app.services import environment_folder_profiles

    profile_id = f"materials-shared-profile-{uuid4().hex}"
    rules = [
        {"role_kind": "SIMULATION_CASE", "pattern": "Package_Model_SetCase1_CushionCase2_*", "parent_role": "REQUEST"},
        {"role_kind": "SIMULATION_CASE", "pattern": "Assy_Model", "parent_role": "REQUEST"},
        {"role_kind": "LOAD_CASE", "pattern": "Drop", "parent_role": "SIMULATION_CASE"},
        {"role_kind": "EXECUTION_RUN", "pattern": "85qn80h_ref_organized", "parent_role": "LOAD_CASE"},
        {"role_kind": "EXECUTION_RUN", "pattern": "Run_*", "parent_role": "LOAD_CASE"},
        {"role_kind": "RUN_OPTION", "pattern": "INDIVIDUAL", "parent_role": "EXECUTION_RUN"},
        {"role_kind": "SCENE", "pattern": "*Scene*", "parent_role": "RUN_OPTION"},
        {"role_kind": "SCENE", "pattern": "Scene_*", "parent_role": "RUN_OPTION"},
    ]
    registration_ids: list[str] = []
    scan_ids: list[str] = []
    with connect() as conn:
        profile = environment_folder_profiles.save_profile(
            conn, environment="DISTRIBUTION", name=profile_id, rules={"rules": rules},
        )
        project_folder = str(PurePosixPath(request_folder).parent)

        def apply_scan(assignments_by_path):
            scan = folder_discovery_environment.save_scan(
                conn, root, project_folder, "DISTRIBUTION", profile["id"],
                project_id, request_id, "synthetic-test",
            )
            scan_ids.append(str(scan["id"]))
            nodes = {item["relative_path"]: item for item in scan["nodes"]}
            assignments = [{
                "node_id": nodes[request_folder]["id"], "role_kind": "REQUEST",
                "target_mode": "LINK", "target_id": request_id,
            }]
            assignments.extend({"node_id": nodes[path]["id"], "role_kind": role}
                               for path, role in assignments_by_path.items())
            preview = folder_discovery_environment.preview(
                conn, scan["id"], assignments, "synthetic-test",
            )
            assert preview["can_apply"] is True, preview
            registration = folder_discovery_environment.register(
                conn, preview["id"], f"materials-shared-key-{uuid4().hex}", None,
                SimpleNamespace(user_id="synthetic-test"), root,
            )
            registration_ids.append(str(registration["registration_id"]))
            return registration

        old_registration = apply_scan({
            old_case: "SIMULATION_CASE",
            f"{old_case}/Drop": "LOAD_CASE",
            f"{old_case}/Drop/85qn80h_test": "EXECUTION_RUN",
            old_scene: "SCENE",
        })
        conn.execute(
            "UPDATE folder_environment_scans SET created_at=? WHERE id=?",
            [datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10), scan_ids[0]],
        )
        current_registration = apply_scan({old_case: "EXCLUDE"})
        assert old_registration["registration_id"] != current_registration["registration_id"]

    try:
        catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert catalog.status_code == 200, catalog.text
        catalog_paths = {item["relative_path"] for item in catalog.json()["scenes"]}
        assert current_scene in catalog_paths
        assert old_scene not in catalog_paths
        assert f"{other_valid_case}/Drop/Run_01/INDIVIDUAL/Scene_01" in catalog_paths

        with connect() as conn:
            targets = materials_catalog.result_registration_paths.targets(
                conn, "DISTRIBUTION", {project_id},
            )
        request_target = next(item for item in targets["targets"] if item["request_id"] == request_id)
        case_paths = {item["relative_path"] for item in request_target["cases"]}
        assert current_case in case_paths
        assert other_valid_case in case_paths
        assert old_case not in case_paths
    finally:
        with connect() as conn:
            for registration_id in registration_ids:
                conn.execute("DELETE FROM folder_environment_registry WHERE registration_id=?", [registration_id])
                conn.execute("DELETE FROM folder_environment_registrations WHERE id=?", [registration_id])
            for scan_id in scan_ids:
                conn.execute("DELETE FROM folder_environment_previews WHERE scan_id=?", [scan_id])
                conn.execute("DELETE FROM folder_environment_scans WHERE id=?", [scan_id])
            conn.execute("DELETE FROM folder_environment_profiles WHERE id=?", [profile_id])


def test_materials_rejects_inferred_scene_owned_by_another_request(materials_client):
    client, root, project_id, request_id, request_folder, _, _, _ = materials_client
    foreign_scene = (
        f"{request_folder}/Working/Package_Model_ForeignOwnership/Drop/Run_01/INDIVIDUAL/"
        "Scene_ForeignOwner"
    )
    scene_path = root.joinpath(*foreign_scene.split("/"))
    scene_path.mkdir(parents=True)
    (scene_path / "parts.inc").write_text(_parts_deck(), encoding="utf-8")
    (scene_path / "materials.inc").write_text(_materials_deck(), encoding="utf-8")

    from app.services import folder_discovery_environment

    root_key = folder_discovery_environment.root_identity(root)
    owner_id = f"foreign-owner-{uuid4().hex}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO result_registration_paths "
            "(id,root_key,project_id,request_id,environment,relative_path,path_key,parent_relative_path,"
            "role_kind,target_id,raw_name,option_status,created_by,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [owner_id, root_key, f"{project_id}-other", f"{request_id}-other", "DISTRIBUTION",
             foreign_scene, materials_catalog.result_registration_paths._root_casefold(foreign_scene),
             str(PurePosixPath(foreign_scene).parent), "SCENE", f"target-{owner_id}", "Scene_ForeignOwner",
             None, "synthetic-test", now],
        )

    try:
        catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert catalog.status_code == 200, catalog.text
        assert all(item["relative_path"] != foreign_scene for item in catalog.json()["scenes"])

        deck = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": foreign_scene})
        assert deck.status_code == 422
        assert deck.json()["detail"]["code"] == "MATERIALS_SCENE_INVALID"
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM result_registration_paths WHERE id=?", [owner_id])


def test_materials_exposes_standalone_results_only_when_schema_assigns_the_role(materials_client):
    client, root, project_id, request_id, request_folder, _, _, _ = materials_client
    result_paths = [
        f"{request_folder}/CAE/Assy_Model/Drop/Run_02/results",
        f"{request_folder}/CAE/Assy_Model/Drop/Run_03/INDIVIDUAL/results",
    ]
    for index, relative in enumerate(result_paths, start=2):
        directory = root.joinpath(*relative.split("/"))
        directory.mkdir(parents=True)
        (directory / "parts.inc").write_text(_parts_deck().replace("/PART/1", f"/PART/{index}"), encoding="utf-8")
        (directory / "materials.inc").write_text(_materials_deck(), encoding="utf-8")

    profile_id, scan_id = _save_distribution_scan(root, project_id, request_id, [
        *_base_distribution_rules(),
        {"role_kind": "RESULTS", "pattern": "results", "parent_role": "EXECUTION_RUN"},
        {"role_kind": "RESULTS", "pattern": "results", "parent_role": "RUN_OPTION"},
    ])
    try:
        catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert catalog.status_code == 200, catalog.text
        entries = {item["relative_path"]: item for item in catalog.json()["scenes"]}
        for index, relative in enumerate(result_paths, start=2):
            assert entries[relative]["kind"] == "RESULTS"
            assert entries[relative]["has_deck"] is True
            response = client.get(BASE + "/deck", params={"request_id": request_id,
                                                               "scene_id": entries[relative]["scene_id"]})
            assert response.status_code == 200, response.text
            assert [part["id"] for part in response.json()["deck"]["parts"]] == [str(index)]
    finally:
        _delete_distribution_scan(profile_id, scan_id)


def test_materials_ignores_result_decks_owned_by_another_environment(materials_client):
    client, root, project_id, request_id, request_folder, _, empty_scene_relative, _ = materials_client
    unscened_relative = (f"{request_folder}/CAE/Assy_Model/Drop/Run_02/INDIVIDUAL/"
                         "DAMP-2_Face/results")
    scene_result_relative = f"{empty_scene_relative}/results"
    for relative in (unscened_relative, scene_result_relative):
        directory = root.joinpath(*relative.split("/"))
        directory.mkdir(parents=True)
        (directory / "parts.inc").write_text(_parts_deck(), encoding="utf-8")
        (directory / "materials.inc").write_text(_materials_deck(), encoding="utf-8")

    from app.services import folder_discovery_environment
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    root_key = folder_discovery_environment.root_identity(root)
    with connect() as conn:
        for relative in (unscened_relative, scene_result_relative):
            registration_id = f"foreign-results-{uuid4().hex}"
            conn.execute(
                "INSERT INTO folder_environment_registrations "
                "(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) "
                "VALUES(?,?,?,'USAGE',?,?,'APPLIED','synthetic-test',?)",
                [registration_id, f"preview-{registration_id}", f"key-{registration_id}", project_id, request_id, now],
            )
            conn.execute(
                "INSERT INTO folder_environment_registry "
                "(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,option_status,created_at) "
                "VALUES(?,?,?,?,'RESULTS',NULL,?,'results',NULL,?)",
                [f"registry-{registration_id}", registration_id, root_key, relative, f"target-{registration_id}", now],
            )

    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    assert catalog.status_code == 200, catalog.text
    assert all(item["relative_path"] != unscened_relative for item in catalog.json()["scenes"])
    empty_scene = next(item for item in catalog.json()["scenes"] if item["relative_path"] == empty_scene_relative)
    assert empty_scene["has_deck"] is False


def test_materials_checks_result_folder_ownership_before_reading_deck_content(materials_client, monkeypatch):
    client, root, _, request_id, request_folder, _, _, _ = materials_client
    result_relative = (f"{request_folder}/CAE/Assy_Model/Drop/Run_02/INDIVIDUAL/"
                       "DAMP-2_Face/results")
    result_path = root.joinpath(*result_relative.split("/"))
    result_path.mkdir(parents=True)
    (result_path / "parts.inc").write_text(_parts_deck(), encoding="utf-8")
    (result_path / "materials.inc").write_text(_materials_deck(), encoding="utf-8")

    original_owner_check = materials_catalog.result_registration_paths._owner_conflict
    original_sniff = materials_catalog._file_roles
    sniffed_result_files = []

    def deny_result_folder(conn, root_id, root_key, relative_path, project_id, owner_request_id, environment):
        if relative_path.casefold() == result_relative.casefold():
            raise materials_catalog.result_registration_paths.ResultRegistrationError(
                "RESULT_PATH_OWNERSHIP_CONFLICT", "synthetic foreign result owner",
            )
        return original_owner_check(conn, root_id, root_key, relative_path, project_id, owner_request_id, environment)

    def observe_sniff(path, budget=None):
        if path.parent == result_path:
            sniffed_result_files.append(path.name)
        return original_sniff(path, budget)

    monkeypatch.setattr(materials_catalog.result_registration_paths, "_owner_conflict", deny_result_folder)
    monkeypatch.setattr(materials_catalog, "_file_roles", observe_sniff)

    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    assert catalog.status_code == 200, catalog.text
    assert all(item["relative_path"] != result_relative for item in catalog.json()["scenes"])
    assert sniffed_result_files == []


def test_materials_api_is_distribution_only(materials_client):
    client, _, _, request_id, _, scene_relative, _, _ = materials_client
    catalog = client.get(BASE + "/catalog", params={"request_id": request_id, "environment": "USAGE"})
    deck = client.get(BASE + "/deck", params={"request_id": request_id,
                                                "environment": "USAGE", "relative_path": scene_relative})
    assert catalog.status_code == deck.status_code == 422


def test_materials_rejects_cross_request_non_scene_and_traversal_paths(materials_client):
    client, root, _, request_id, request_folder, scene_relative, _, _ = materials_client
    other_scene = f"{request_folder}-other/CAE/Assy_Model/Drop/Run_01/INDIVIDUAL/Scene_Other"
    root.joinpath(*other_scene.split("/")).mkdir(parents=True)
    non_scene = f"{request_folder}/CAE/Assy_Model/Drop/Run_01/INDIVIDUAL/ordinary_folder"
    root.joinpath(*non_scene.split("/")).mkdir(parents=True)
    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    scene = next(item for item in catalog.json()["scenes"] if item["relative_path"] == scene_relative)

    cross_request = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": other_scene})
    assert cross_request.status_code == 422
    bad_role = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": non_scene})
    assert bad_role.status_code == 422
    traversal = client.get(BASE + "/deck", params={"request_id": request_id,
                                                       "relative_path": scene_relative + "/../Scene_Other"})
    assert traversal.status_code == 422
    mismatch = client.get(BASE + "/deck", params={"request_id": request_id, "scene_id": scene["scene_id"],
                                                    "relative_path": other_scene})
    assert mismatch.status_code == 422


def test_materials_endpoints_call_request_project_view_guard(materials_client, monkeypatch):
    from fastapi import HTTPException
    from app.routers import materials as materials_router

    client, _, _, request_id, _, scene_relative, _, _ = materials_client

    def denied(*args, **kwargs):
        raise HTTPException(403, detail={"code": "PROJECT_DATA_VIEW_DENIED"})

    monkeypatch.setattr(materials_router, "require_resource_permission", denied)
    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    deck = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert catalog.status_code == deck.status_code == 403
    assert catalog.json()["detail"]["code"] == "PROJECT_DATA_VIEW_DENIED"


def test_materials_candidate_priority_includes_execution_run_input(materials_client):
    client, root, _, request_id, request_folder, scene_relative, _, _ = materials_client
    run_input = f"{request_folder}/CAE/Assy_Model/Drop/Run_01/INPUT"
    run_input_path = root.joinpath(*run_input.split("/"))
    run_input_path.mkdir(parents=True)
    (run_input_path / "fallback_parts.inc").write_text(_parts_deck().replace("/PART/1", "/PART/9"), encoding="utf-8")
    (run_input_path / "fallback_materials.inc").write_text(_materials_deck().replace("/MAT/ELAST/2", "/MAT/ELAST/99"), encoding="utf-8")

    scene_read = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert scene_read.status_code == 200, scene_read.text
    assert [part["id"] for part in scene_read.json()["deck"]["parts"]] == ["1"]
    assert all(item["relative_path"].startswith(scene_relative + "/") for item in scene_read.json()["files"])

    scene_path = root.joinpath(*scene_relative.split("/"))
    (scene_path / "parts.inc").unlink()
    (scene_path / "materials.inc").unlink()
    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    assert catalog.status_code == 200, catalog.text
    scene = next(item for item in catalog.json()["scenes"] if item["relative_path"] == scene_relative)
    assert scene["has_deck"] is True
    ancestor_read = client.get(BASE + "/deck", params={"request_id": request_id, "scene_id": scene["scene_id"]})
    assert ancestor_read.status_code == 200, ancestor_read.text
    assert [part["id"] for part in ancestor_read.json()["deck"]["parts"]] == ["9"]
    assert all(item["relative_path"].startswith(run_input + "/") for item in ancestor_read.json()["files"])


def test_materials_scene_candidates_do_not_cross_scene_or_run_option(materials_client):
    client, root, project_id, request_id, request_folder, _, _, _ = materials_client
    run = f"{request_folder}/CAE/Assy_Model/Drop/Run_01"
    individual = f"{run}/INDIVIDUAL"
    scene_a = f"{individual}/Scene_A"
    scene_b = f"{individual}/Scene_B"
    scene_b_results = f"{scene_b}/results"
    cumulative = f"{run}/CUMULATIVE"
    cumulative_results = f"{cumulative}/output_data"

    root.joinpath(*scene_a.split("/")).mkdir(parents=True)
    result_b = root.joinpath(*scene_b_results.split("/"))
    result_b.mkdir(parents=True)
    (result_b / "parts.inc").write_text(_parts_deck().replace("/PART/1", "/PART/21"), encoding="utf-8")
    (result_b / "materials.inc").write_text(_materials_deck().replace("/MAT/ELAST/2", "/MAT/ELAST/21"), encoding="utf-8")
    result_cumulative = root.joinpath(*cumulative_results.split("/"))
    result_cumulative.mkdir(parents=True)
    (result_cumulative / "parts.inc").write_text(_parts_deck().replace("/PART/1", "/PART/31"), encoding="utf-8")
    (result_cumulative / "materials.inc").write_text(_materials_deck().replace("/MAT/ELAST/2", "/MAT/ELAST/31"), encoding="utf-8")

    profile_id, scan_id = _save_distribution_scan(root, project_id, request_id, [
        *_base_distribution_rules(),
        {"role_kind": "RUN_OPTION", "pattern": "CUMULATIVE", "parent_role": "EXECUTION_RUN"},
        {"role_kind": "RESULTS", "pattern": "output_data", "parent_role": "RUN_OPTION"},
    ])
    try:
        response = client.get(BASE + "/catalog", params={"request_id": request_id})
        assert response.status_code == 200, response.text
        entries = {item["relative_path"]: item for item in response.json()["scenes"]}
        assert entries[scene_a]["has_deck"] is False
        assert entries[scene_b]["has_deck"] is True
        assert entries[cumulative_results]["kind"] == "RESULTS"
        assert entries[cumulative_results]["has_deck"] is True

        read_a = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_a})
        assert read_a.status_code == 404, read_a.text
        assert read_a.json()["detail"]["code"] == "MATERIALS_DECK_NOT_FOUND"

        read_b = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_b})
        assert read_b.status_code == 200, read_b.text
        assert [item["id"] for item in read_b.json()["deck"]["parts"]] == ["21"]
        assert all(item["relative_path"].startswith(scene_b_results + "/") for item in read_b.json()["files"])
    finally:
        _delete_distribution_scan(profile_id, scan_id)


def test_materials_accepts_starter_rad_with_both_deck_roles(materials_client):
    client, root, _, request_id, request_folder, _, _, _ = materials_client
    scene_relative = f"{request_folder}/CAE/Assy_Model/Drop/Run_01/INDIVIDUAL/Scene_Rad"
    scene_path = root.joinpath(*scene_relative.split("/"))
    scene_path.mkdir(parents=True)
    (scene_path / "starter.rad").write_text(_parts_deck() + _materials_deck(), encoding="utf-8")
    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    assert catalog.status_code == 200, catalog.text
    scene = next(item for item in catalog.json()["scenes"] if item["relative_path"] == scene_relative)
    assert scene["has_deck"] is True
    response = client.get(BASE + "/deck", params={"request_id": request_id, "scene_id": scene["scene_id"]})
    assert response.status_code == 200, response.text
    assert [part["id"] for part in response.json()["deck"]["parts"]] == ["1"]
    assert [material["id"] for material in response.json()["deck"]["materials"]] == ["2"]
    assert response.json()["files"] == [{"relative_path": f"{scene_relative}/starter.rad", "size_bytes": len((scene_path / "starter.rad").read_bytes())}]


def test_materials_resolves_nested_includes_and_merges_deck_cards(materials_client):
    client, root, _, request_id, _, scene_relative, _, _ = materials_client
    scene_path = root.joinpath(*scene_relative.split("/"))
    (scene_path / "parts.inc").write_text("/INCLUDE\nextra/parts_extra.inc\n", encoding="utf-8")
    (scene_path / "materials.inc").write_text(
        _materials_deck().replace("/FUNCT/10", "/INCLUDE/extra/materials_extra.inc\n/FUNCT/10"),
        encoding="utf-8",
    )
    extra = scene_path / "extra"
    extra.mkdir()
    (extra / "parts_extra.inc").write_text(_parts_deck().replace("/PART/1", "/PART/9"), encoding="utf-8")
    (extra / "materials_extra.inc").write_text(
        "/INCLUDE\n../curves/곡선.inc\n", encoding="cp949",
    )
    curves = scene_path / "curves"
    curves.mkdir()
    (curves / "곡선.inc").write_text(
        "/FUNCT/12\nIncluded curve\n" + f"{0.0:>20}{1.0:>20}\n{2.0:>20}{3.0:>20}\n",
        encoding="cp949",
    )

    response = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [part["id"] for part in body["deck"]["parts"]] == ["9"]
    assert [material["id"] for material in body["deck"]["materials"]] == ["2"]
    assert {function["id"] for function in body["deck"]["functions"]} == {"10", "12"}
    assert {item["relative_path"] for item in body["files"]} == {
        f"{scene_relative}/parts.inc",
        f"{scene_relative}/extra/parts_extra.inc",
        f"{scene_relative}/materials.inc",
        f"{scene_relative}/extra/materials_extra.inc",
        f"{scene_relative}/curves/곡선.inc",
    }


def test_materials_include_cannot_escape_request(materials_client):
    client, root, _, request_id, _, scene_relative, _, _ = materials_client
    scene_path = root.joinpath(*scene_relative.split("/"))
    (scene_path / "parts.inc").write_text("/INCLUDE\n../../../../../../../escaped.inc\n", encoding="utf-8")
    response = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MATERIALS_INCLUDE_OUTSIDE_REQUEST"


def test_materials_include_rejects_reparse_target(materials_client, monkeypatch):
    from app.services import spdm_storage

    _, root, _, _, request_folder, scene_relative, _, _ = materials_client
    scene_path = root.joinpath(*scene_relative.split("/"))
    include_path = scene_path / "linked.inc"
    include_path.write_text(_parts_deck(), encoding="utf-8")
    (scene_path / "parts.inc").write_text("/INCLUDE\nlinked.inc\n", encoding="utf-8")
    parts_path = scene_path / "parts.inc"
    from app.services.storage import local as storage_local
    real_is_reparse = storage_local._is_reparse

    def mark_include_reparse(path):
        if Path(path) == include_path:
            return True
        return real_is_reparse(path)

    monkeypatch.setattr(storage_local, "_is_reparse", mark_include_reparse)
    with pytest.raises(materials_catalog.MaterialsCatalogError) as error:
        materials_catalog._include_sources(
            [(f"{scene_relative}/parts.inc", parts_path, parts_path.stat().st_size)],
            root, {"request_relative_path": request_folder}, materials_catalog._ParseBudget(),
        )
    assert error.value.code == "SPDM_PATH_UNSAFE"


def test_materials_include_cycle_is_rejected(materials_client):
    client, root, _, request_id, _, scene_relative, _, _ = materials_client
    scene_path = root.joinpath(*scene_relative.split("/"))
    (scene_path / "parts.inc").write_text("/INCLUDE\ncycle_a.inc\n", encoding="utf-8")
    (scene_path / "cycle_a.inc").write_text("/INCLUDE\ncycle_b.inc\n", encoding="utf-8")
    (scene_path / "cycle_b.inc").write_text("/INCLUDE\ncycle_a.inc\n", encoding="utf-8")
    response = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MATERIALS_INCLUDE_CYCLE"


def _direct_include_source(tmp_path, relative="Project/Request/CAE/Scene/parts.inc"):
    root = tmp_path / "include_root"
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return root, "Project/Request", relative, path


def test_materials_include_depth_limit_is_enforced(tmp_path, monkeypatch):
    root, request_relative, relative, path = _direct_include_source(tmp_path)
    (path.parent / "a.inc").write_text("/INCLUDE/b.inc\n", encoding="utf-8")
    (path.parent / "b.inc").write_text("/PART/1\nPart\n", encoding="utf-8")
    path.write_text("/INCLUDE/a.inc\n", encoding="utf-8")
    monkeypatch.setattr(materials_catalog, "MAX_INCLUDE_DEPTH", 1)
    with pytest.raises(materials_catalog.MaterialsCatalogError) as error:
        materials_catalog._include_sources(
            [(relative, path, path.stat().st_size)], root,
            {"request_relative_path": request_relative}, materials_catalog._ParseBudget(),
        )
    assert error.value.code == "MATERIALS_INCLUDE_DEPTH_LIMIT"


def test_materials_include_file_count_limit_is_enforced(tmp_path, monkeypatch):
    root, request_relative, relative, path = _direct_include_source(tmp_path)
    (path.parent / "a.inc").write_text("/PART/1\nPart\n", encoding="utf-8")
    (path.parent / "b.inc").write_text("/PART/2\nPart\n", encoding="utf-8")
    path.write_text("/INCLUDE/a.inc\n/INCLUDE/b.inc\n", encoding="utf-8")
    monkeypatch.setattr(materials_catalog, "MAX_INCLUDE_FILES", 2)
    with pytest.raises(materials_catalog.MaterialsCatalogError) as error:
        materials_catalog._include_sources(
            [(relative, path, path.stat().st_size)], root,
            {"request_relative_path": request_relative}, materials_catalog._ParseBudget(),
        )
    assert error.value.code == "MATERIALS_INCLUDE_FILE_LIMIT"


def test_materials_include_total_size_limit_is_enforced(tmp_path, monkeypatch):
    root, request_relative, relative, path = _direct_include_source(tmp_path)
    child = path.parent / "a.inc"
    child.write_text("/PART/1\nPart\n", encoding="utf-8")
    path.write_text("/INCLUDE/a.inc\n", encoding="utf-8")
    monkeypatch.setattr(materials_catalog, "MAX_TOTAL_BYTES", path.stat().st_size + child.stat().st_size - 1)
    with pytest.raises(materials_catalog.MaterialsCatalogError) as error:
        materials_catalog._include_sources(
            [(relative, path, path.stat().st_size)], root,
            {"request_relative_path": request_relative}, materials_catalog._ParseBudget(),
        )
    assert error.value.code == "MATERIALS_TOTAL_SIZE_LIMIT"


def test_materials_include_scan_ignores_references_after_end(tmp_path):
    root, _, relative, path = _direct_include_source(tmp_path)
    path.write_text("/END\n/INCLUDE\nmissing.inc\n", encoding="utf-8")
    references = materials_catalog._scan_include_references(
        path, relative, path.stat().st_size, materials_catalog._ParseBudget(),
    )
    assert references == []


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "expected_code"),
    [
        ("MAX_FILE_BYTES", 10, "MATERIALS_FILE_SIZE_LIMIT"),
        ("MAX_TOTAL_BYTES", 20, "MATERIALS_TOTAL_SIZE_LIMIT"),
        ("MAX_PARSE_SECONDS", -1, "MATERIALS_PARSE_TIME_LIMIT"),
        ("MAX_FUNCTION_POINTS", 1, "MATERIALS_FUNCTION_POINT_LIMIT"),
    ],
)
def test_materials_deck_limits_are_enforced(materials_client, monkeypatch, limit_name, limit_value, expected_code):
    client, _, _, request_id, _, scene_relative, _, _ = materials_client
    monkeypatch.setattr(materials_catalog, limit_name, limit_value)
    response = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == expected_code


def test_materials_function_point_limit_aborts_before_function_parse(materials_client, monkeypatch):
    client, _, _, request_id, _, scene_relative, _, _ = materials_client
    monkeypatch.setattr(materials_catalog, "MAX_FUNCTION_POINTS", 1)

    def should_not_parse_function(*args, **kwargs):
        raise AssertionError("point-row budget must abort before full FUNCT parsing")

    monkeypatch.setattr(materials_catalog.RadiossDeckParser, "_parse_function", should_not_parse_function)
    response = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "MATERIALS_FUNCTION_POINT_LIMIT"


def test_materials_parser_time_budget_is_checked_on_each_input_line(materials_client, monkeypatch):
    client, _, _, request_id, _, scene_relative, _, _ = materials_client
    real_parse_lines = materials_catalog.RadiossDeckParser.parse_lines

    def simulate_slow_parser(parser, *args, **kwargs):
        on_input_line = kwargs["on_input_line"]

        def expire_before_next_line(filename, line_number, raw_line):
            on_input_line.__self__.started -= materials_catalog.MAX_PARSE_SECONDS + 1
            return on_input_line(filename, line_number, raw_line)

        kwargs["on_input_line"] = expire_before_next_line
        return real_parse_lines(parser, *args, **kwargs)

    monkeypatch.setattr(materials_catalog.RadiossDeckParser, "parse_lines", simulate_slow_parser)
    response = client.get(BASE + "/deck", params={"request_id": request_id, "relative_path": scene_relative})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "MATERIALS_PARSE_TIME_LIMIT"
