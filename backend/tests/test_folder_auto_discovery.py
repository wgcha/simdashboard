"""Automatic discovery of new SPDM project and request folders (synthetic root only)."""
from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from app.database_connection import connect
from app.services import environment_folder_profiles, folder_auto_discovery, folder_auto_sync
from tests.test_new_scene_registration import CSV, CSV_BYTES, _seed, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration
DISCOVER = "/api/folder-discovery/environments/discover"
CONTAINER = "SPDM (Admin)"
PROJECT = f"{CONTAINER}/75R9J_PV"
WR2 = f"{PROJECT}/[WR-0002]_[유통_환경]"
CASE_PATH = "Working/Package_Model_SetCase3_CushionCase1/Drop/85qn80h_ref_organized/INDIVIDUAL"
LEVEL_RULES = {"rules": [
    {"role_kind": "SIMULATION_CASE", "match_mode": "level", "depth": 1, "pattern": ""},
    {"role_kind": "LOAD_CASE", "match_mode": "level", "depth": 2, "pattern": ""},
    {"role_kind": "EXECUTION_RUN", "match_mode": "level", "depth": 3, "pattern": ""},
    {"role_kind": "RUN_OPTION", "match_mode": "level", "depth": 4, "pattern": ""},
    {"role_kind": "SCENE", "match_mode": "level", "depth": 5, "pattern": ""},
]}


@pytest.fixture(autouse=True)
def _no_interval(monkeypatch):
    folder_auto_discovery.reset_for_tests()
    folder_auto_sync.reset_for_tests()
    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_sync, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_sync, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    yield
    folder_auto_discovery.reset_for_tests()
    folder_auto_sync.reset_for_tests()


def _level_profile():
    """Working-relative depth roles, as the active DISTRIBUTION profile."""
    with connect() as conn:
        environment_folder_profiles.save_profile(
            conn, environment="DISTRIBUTION", name="기본 유통환경 규칙", rules=LEVEL_RULES,
            profile_id="environment-profile-distribution-default", expected_revision=1)


def _distribution_request(root, request, scenes=("2_Face", "3_Face")):
    for scene in scenes:
        folder = root / request / CASE_PATH / scene
        folder.mkdir(parents=True)
        (folder / CSV).write_bytes(CSV_BYTES)
    (root / request / "Final").mkdir(parents=True, exist_ok=True)


def _discover(client, **body):
    response = client.post(DISCOVER, json=body or None)
    assert response.status_code == 200, response.text
    return response.json()


def _requests(client, project_id):
    response = client.get(f"/api/projects/{project_id}/requests")
    assert response.status_code == 200, response.text
    return {item["id"]: item for item in response.json()}


def _scenes(client, project_id, request_id, environment="DISTRIBUTION"):
    response = client.get("/api/dashboard/catalog", params={"project_id": project_id, "request_id": request_id,
                                                            "environment": environment})
    assert response.status_code == 200, response.text
    return {str(scene.get("relative_path") or scene.get("label") or "").rsplit("/", 1)[-1]
            for scene in response.json().get("scenes", [])}


def _count(sql, params=()):
    with connect() as conn:
        return conn.execute(sql, list(params)).fetchone()[0]


def test_new_project_and_request_under_container_are_registered(admin_client):
    client, root = admin_client
    _level_profile()
    _distribution_request(root, WR2)
    # A copied Final with a Case-like tree is preserved, never scanned as cases.
    final_case = root / WR2 / "Final" / "Package_Final_copy" / "Drop" / "r" / "INDIVIDUAL" / "9_Final"
    final_case.mkdir(parents=True)
    (final_case / CSV).write_bytes(CSV_BYTES)

    result = _discover(client)
    assert result["coalesced"] is False and result["needs_review"] == []
    assert [item["name"] for item in result["created_projects"]] == ["75R9J_PV"]
    [created] = result["created_requests"]
    assert (created["name"], created["environment"]) == ("[WR-0002]_[유통_환경]", "DISTRIBUTION")
    project_id, request_id = created["project_id"], created["id"]
    assert project_id == result["created_projects"][0]["id"]

    projects = client.get("/api/projects").json()
    assert any(item["id"] == project_id and item["name"] == "75R9J_PV" for item in projects)
    assert _requests(client, project_id)[request_id]["title"] == "[WR-0002]_[유통_환경]"
    scenes = _scenes(client, project_id, request_id)
    assert {"2_Face", "3_Face"} <= scenes and "9_Final" not in scenes
    # No membership is invented for the new project.
    assert _count("SELECT count(*) FROM project_memberships WHERE project_id=?", [project_id]) == 0
    # The registration scan never read below Final.
    with connect() as conn:
        tree = conn.execute(
            "SELECT s.tree_json FROM folder_environment_registrations r JOIN folder_environment_previews p ON p.id=r.preview_id "
            "JOIN folder_environment_scans s ON s.id=p.scan_id WHERE r.request_id=?", [request_id]).fetchone()[0]
    assert "Package_Final_copy" not in str(tree)
    assert _count("SELECT count(*) FROM audit_events WHERE action='FOLDER_ENVIRONMENT_AUTO_DISCOVERED'") == 1


def test_usage_name_registers_as_usage_under_existing_project_and_leaves_existing_request(admin_client):
    client, root = admin_client
    project_id, existing_request = _seed(client, root)
    before = _count("SELECT count(*) FROM folder_environment_registrations")
    projects_before = _count("SELECT count(*) FROM projects")
    usage = root / "75R9J_PV" / "[WR-0001]_[사용_환경]" / "Working" / "Package_Usage_Case1" / "settle"
    usage.mkdir(parents=True)
    (usage / "result.json").write_text('{"max": 1}', encoding="utf-8")

    result = _discover(client)
    assert result["created_projects"] == [] and result["needs_review"] == []
    [created] = result["created_requests"]
    assert (created["name"], created["environment"], created["project_id"]) == (
        "[WR-0001]_[사용_환경]", "USAGE", project_id)
    assert set(_requests(client, project_id)) == {existing_request, created["id"]}
    assert _count("SELECT count(*) FROM folder_environment_registrations") == before + 1
    assert _count("SELECT count(*) FROM folder_environment_registrations WHERE request_id=?", [existing_request]) == 1
    assert _count("SELECT count(*) FROM projects") == projects_before


def test_distribution_and_usage_names_map_to_their_environment():
    env = folder_auto_discovery.request_environment
    assert env("[WR-0001]_[유통_환경]") == "DISTRIBUTION"
    assert env("[WR-0001]_[사용_환경]") == "USAGE"
    assert env("WR_A1_SimType1") == "USAGE"
    assert env("WR_A1_SimType2") == "DISTRIBUTION"
    assert env("[WR-0003]_[기타]") is None
    assert env("[WR-0003]_[사용_유통]") is None


def test_unknown_environment_and_unresolved_roles_need_review(admin_client):
    client, root = admin_client
    baseline = (_count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects"))
    (root / PROJECT / "[WR-0003]_[기타]" / "Working").mkdir(parents=True)
    # The default profile has no depth rules, so 2_Face/3_Face stay unresolved.
    _distribution_request(root, WR2)

    result = _discover(client)
    assert result["created_projects"] == [] and result["created_requests"] == []
    reviews = {item["relative_path"]: item["code"] for item in result["needs_review"]}
    assert reviews == {f"{PROJECT}/[WR-0003]_[기타]": "ENVIRONMENT_UNKNOWN", WR2: "ROLES_UNRESOLVED"}
    assert (_count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects")) == baseline

    # An unchanged unresolved request is not deep-scanned again on the next run.
    scans = _count("SELECT count(*) FROM folder_environment_scans")
    again = _discover(client)
    assert {item["relative_path"] for item in again["needs_review"]} == set(reviews)
    assert _count("SELECT count(*) FROM folder_environment_scans") == scans


def test_repeat_is_idempotent_and_calls_are_coalesced(admin_client, monkeypatch):
    client, root = admin_client
    _level_profile()
    _distribution_request(root, WR2)
    first = _discover(client)
    assert len(first["created_requests"]) == 1
    counts = (_count("SELECT count(*) FROM folder_environment_registrations"),
              _count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects"))
    folder_auto_discovery.reset_for_tests()
    second = _discover(client)
    assert (second["created_projects"], second["created_requests"], second["coalesced"]) == ([], [], False)
    assert counts == (_count("SELECT count(*) FROM folder_environment_registrations"),
                      _count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects"))

    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 60.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 60.0)
    (root / PROJECT / "[WR-0005]_[유통_환경]" / "Working").mkdir(parents=True)
    coalesced = _discover(client, force=True)
    assert coalesced["coalesced"] is True and coalesced["created_requests"] == []
    assert _count("SELECT count(*) FROM analysis_requests") == counts[1]


def test_concurrent_runs_create_one_request(admin_client):
    client, root = admin_client
    baseline = (_count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects"))
    (root / WR2 / "Working").mkdir(parents=True)
    results, errors = [], []

    def run():
        try:
            with connect() as conn:
                results.append(folder_auto_discovery.discover(conn, force=True))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sum(len(item["created_requests"]) for item in results) == 1
    assert (_count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects")) == (
        baseline[0] + 1, baseline[1] + 1)


def test_empty_working_registers_and_later_cases_appear(admin_client):
    client, root = admin_client
    _level_profile()
    (root / WR2 / "Working").mkdir(parents=True)
    (root / WR2 / "Final").mkdir()
    result = _discover(client)
    [created] = result["created_requests"]
    project_id, request_id = created["project_id"], created["id"]
    assert request_id in _requests(client, project_id)
    assert _scenes(client, project_id, request_id) == set()

    _distribution_request(root, WR2, scenes=("2_Face",))
    synced = client.post("/api/folder-discovery/environments/sync",
                         json={"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"})
    assert synced.status_code == 200, synced.text
    assert "2_Face" in _scenes(client, project_id, request_id)


def test_two_new_requests_share_one_new_project(admin_client):
    client, root = admin_client
    _level_profile()
    projects_before = _count("SELECT count(*) FROM projects")
    (root / WR2 / "Working").mkdir(parents=True)
    (root / PROJECT / "[WR-0006]_[사용_환경]" / "Working").mkdir(parents=True)
    result = _discover(client)
    assert len(result["created_projects"]) == 1
    assert {item["environment"] for item in result["created_requests"]} == {"DISTRIBUTION", "USAGE"}
    assert len({item["project_id"] for item in result["created_requests"]}) == 1
    assert _count("SELECT count(*) FROM projects") == projects_before + 1


def test_discover_calls_permission_guard(admin_client, monkeypatch):
    client, _ = admin_client
    from app.routers import folder_discovery_environment as router
    seen = []

    def denied(request, permission, project_id=None, **kwargs):
        seen.append((permission, project_id))
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})

    monkeypatch.setattr(router, "require_permission", denied)
    response = client.post(DISCOVER)
    assert response.status_code == 403
    assert seen == [("project.data.view", None)]


def test_needs_review_is_admin_only():
    result = {"created_projects": [], "created_requests": [], "needs_review": [{"relative_path": "a"}],
              "checked_at": "", "coalesced": False}
    assert folder_auto_discovery.visible_result(result, is_global_admin=False)["needs_review"] == []
    assert folder_auto_discovery.visible_result(result, is_global_admin=True)["needs_review"] == [{"relative_path": "a"}]
