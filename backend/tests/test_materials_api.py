"""Isolated read-only materials API contracts over synthetic SPDM folders."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.security import hash_password
from app.services import materials_catalog

pytestmark = pytest.mark.duckdb_integration
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


def test_materials_discovers_results_without_a_scene_and_ignores_unrelated_decks(materials_client):
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
    result = next(item for item in entries if item["relative_path"] == result_relative)
    assert result["kind"] == "RESULTS"
    assert result["has_deck"] is True
    assert all("documents/results" not in item["relative_path"] for item in entries)

    response = client.get(BASE + "/deck", params={"request_id": request_id, "scene_id": result["scene_id"]})
    assert response.status_code == 200, response.text
    assert [part["id"] for part in response.json()["deck"]["parts"]] == ["9"]
    assert [material["id"] for material in response.json()["deck"]["materials"]] == ["99"]
    assert {item["relative_path"] for item in response.json()["files"]} == {
        f"{result_relative}/101_parts.inc", f"{result_relative}/103_material_propertdb.inc",
    }


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


def test_materials_does_not_duplicate_run_results_that_fallback_to_scene(materials_client):
    client, root, _, request_id, request_folder, _, _, _ = materials_client
    result_paths = [
        f"{request_folder}/CAE/Assy_Model/Drop/Run_02/results",
        f"{request_folder}/CAE/Assy_Model/Drop/Run_03/INDIVIDUAL/results",
    ]
    for index, relative in enumerate(result_paths, start=2):
        directory = root.joinpath(*relative.split("/"))
        directory.mkdir(parents=True)
        (directory / "parts.inc").write_text(_parts_deck().replace("/PART/1", f"/PART/{index}"), encoding="utf-8")
        (directory / "materials.inc").write_text(_materials_deck(), encoding="utf-8")

    catalog = client.get(BASE + "/catalog", params={"request_id": request_id})
    assert catalog.status_code == 200, catalog.text
    for relative in result_paths:
        matches = [item for item in catalog.json()["scenes"] if item["relative_path"] == relative]
        assert len(matches) == 1
        assert matches[0]["kind"] == "SCENE"
        assert matches[0]["has_deck"] is True


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
    real_is_reparse = spdm_storage._is_reparse

    def mark_include_reparse(path):
        if Path(path) == include_path:
            return True
        return real_is_reparse(path)

    monkeypatch.setattr(spdm_storage, "_is_reparse", mark_include_reparse)
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
