"""Automatic discovery of new SPDM project and request folders (synthetic root only, DEPTH_V1)."""
from __future__ import annotations

import threading
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.database_connection import connect
from app.services import environment_folder_profiles, folder_auto_discovery, folder_auto_sync, spdm_storage
from app.services import folder_discovery_environment as environment_service
from tests.test_new_scene_registration import CSV, CSV_BYTES, OPTION, _seed, admin_client  # noqa: F401
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse: undo _container_schema)

pytestmark = pytest.mark.duckdb_integration
DISCOVER = "/api/folder-discovery/environments/discover"
CONTAINER = "SPDM (Admin)"
PROJECT = f"{CONTAINER}/75R9J_PV"
WR2 = f"{PROJECT}/[WR-0002]_[유통_환경]"
CASE_PATH = "Working/Package_Model_SetCase3_CushionCase1/Drop/85qn80h_ref_organized/INDIVIDUAL"
FINAL_ID = "0bb7b4f6567f4ce985bba010c7f569e7"
SYNTHETIC_PROJECT_NAMES = {"75r9j_pv", "77777_pv", "proj-000099_r95j 개발 과제", "proj-000099_r95j개발과제", "proj-000099_r95j_개발과제",
                           "proj-000099_r95j_copy", "other_pv"}
AUTO_DISCOVERED_AUDITS = "SELECT count(*) FROM audit_events WHERE action='FOLDER_ENVIRONMENT_AUTO_DISCOVERED'"


@pytest.fixture(autouse=True)
def _isolate_project_names(request):
    """PostgreSQL test runs share one database: earlier tests leave projects named after the synthetic
    folders, which discovery would then report as PROJECT_NAME_EXISTS. Rename those leftovers first."""
    if "admin_client" not in request.fixturenames:
        yield
        return
    request.getfixturevalue("admin_client")
    with connect() as conn:
        for project_id, name, product_name in conn.execute("SELECT id,name,product_name FROM projects").fetchall():
            if {str(name or "").casefold(), str(product_name or "").casefold()} & SYNTHETIC_PROJECT_NAMES:
                suffix = f" (earlier test {str(project_id)[:8]})"
                conn.execute("UPDATE projects SET name=?, product_name=? WHERE id=?",
                             [f"{name}{suffix}", f"{product_name}{suffix}", project_id])
    yield


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


def _container_schema():
    """Upper section Root -> CONTAINER -> PROJECT -> REQUEST (the "SPDM (Admin)" layout)."""
    with connect() as conn:
        current = environment_folder_profiles.get_depth_schema(conn)
        environments = {env: {"lower": item["lower"], "usage_sources": item["usage_sources"]}
                        for env, item in current["environments"].items()}
        upper = {"levels": [{"level": 1, "role": "CONTAINER"}, {"level": 2, "role": "PROJECT"},
                            {"level": 3, "role": "REQUEST"}]}
        return environment_folder_profiles.save_depth_schema(conn, "admin", current["schema_set_id"], upper, environments)


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
    _container_schema()
    audits_before = _count(AUTO_DISCOVERED_AUDITS)
    _distribution_request(root, WR2)
    # A Final copy gets Final roles (D10-D12) but is never a capture source.
    final_case = (root / WR2 / "Final" / "CAE" / "Package_Model_SetCase3_CushionCase1" / FINAL_ID
                  / "Drop" / "85qn80h_ref_organized" / "INDIVIDUAL" / "9_Final")
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
    # Final has roles in the registration preview but is never a capture source (one Working Case only).
    assert _count("SELECT count(*) FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
                  "ON r.id=j.registration_id WHERE r.request_id=?", [request_id]) == 1
    assert _count(AUTO_DISCOVERED_AUDITS) == audits_before + 1


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


def test_request_names_map_by_keyword_and_wr_key():
    identity = folder_auto_discovery.request_identity
    assert identity("[WR-0001]_[유통_환경]") == ("0001", "DISTRIBUTION", None)
    assert identity("[WR-0001]_[사용_환경]") == ("0001", "USAGE", None)
    assert identity("[WR-0002]_[유통_환경] - 복사본")[:2] == ("0002", "DISTRIBUTION")
    assert identity("WR_A1_사용") == ("a1", "USAGE", None)
    # The key is the WR number only: "_", spaces, "]" and Korean text never join it, so a renamed
    # folder (spaces removed) keeps its key. Leading zeros are kept as written.
    assert identity("WR-0000806_유통환경시뮬레이션") == ("0000806", "DISTRIBUTION", None)
    assert identity("WR-0000806_유통 환경 시뮬레이션")[:2] == ("0000806", "DISTRIBUTION")
    assert identity("WR-00000804_유통 환경 시뮬레이션")[0] == "00000804"
    assert identity("WR-0000806사용환경시뮬레이션")[:2] == ("0000806", "USAGE")
    assert identity("[wr_0010] 사용 환경")[0] == "0010"
    assert identity("WR0011_유통")[0] == "0011"
    # No WR number: the casefolded folder name ("WRONG" is not "WR" + a number).
    assert identity("WRONG_유통") == ("wrong_유통", "DISTRIBUTION", None)
    assert identity("Drop_유통_Test") == ("drop_유통_test", "DISTRIBUTION", None)
    assert folder_auto_discovery.wr_number("WR_x_SimType1") == "x"
    assert folder_auto_discovery.wr_number("WR-_유통") is None and folder_auto_discovery.wr_number(None) is None
    assert identity("[WR-0003]_[기타]") == ("0003", None, "ENV_KEYWORD_NONE")
    assert identity("[WR-0003]_[사용_유통]") == ("0003", None, "ENV_KEYWORD_BOTH")
    assert identity("Request 유통") == ("request유통", "DISTRIBUTION", None)  # whitespace carries no meaning
    assert folder_auto_discovery.request_environment("WR_A1_SimType2") is None  # D5: no SimType rule


def test_keyword_both_none_and_depth_deviation_need_review(admin_client):
    client, root = admin_client
    _container_schema()
    baseline = (_count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects"))
    (root / PROJECT / "[WR-0003]_[기타]" / "Working").mkdir(parents=True)
    (root / PROJECT / "[WR-0004]_[사용_유통]" / "Working").mkdir(parents=True)
    (root / PROJECT / "[WR-0007]_[유통_환경]" / "Final").mkdir(parents=True)  # no Working
    (root / PROJECT / "[WR-0008]_[사용_환경]" / "Working").mkdir(parents=True)
    (root / PROJECT / "[WR-0008]_[사용_환경]" / "Misc").mkdir()  # UNEXPECTED_REQUEST_CHILD

    result = _discover(client)
    # Issue #55: the project folder gets its project although none of its requests can be registered.
    assert [item["name"] for item in result["created_projects"]] == ["75R9J_PV"] and result["created_requests"] == []
    reviews = {item["relative_path"]: item for item in result["needs_review"]}
    assert {path: item["code"] for path, item in reviews.items()} == {
        f"{PROJECT}/[WR-0003]_[기타]": "ENV_KEYWORD_NONE",
        f"{PROJECT}/[WR-0004]_[사용_유통]": "ENV_KEYWORD_BOTH",
        f"{PROJECT}/[WR-0007]_[유통_환경]": "DEPTH_DEVIATION",
        f"{PROJECT}/[WR-0008]_[사용_환경]": "DEPTH_DEVIATION",
    }
    missing = reviews[f"{PROJECT}/[WR-0007]_[유통_환경]"]
    assert (missing["deviation_code"], missing["deviation_count"]) == ("WORKING_MISSING", 1)
    assert reviews[f"{PROJECT}/[WR-0008]_[사용_환경]"]["deviation_code"] == "UNEXPECTED_REQUEST_CHILD"
    assert (_count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects")) == (
        baseline[0], baseline[1] + 1)

    # An unchanged request kept in review is not deep-scanned again on the next run.
    scans = _count("SELECT count(*) FROM folder_environment_scans")
    again = _discover(client)
    assert {item["relative_path"] for item in again["needs_review"]} == set(reviews)
    assert _count("SELECT count(*) FROM folder_environment_scans") == scans

    # Fixing the folder registers it on a later run.
    (root / PROJECT / "[WR-0007]_[유통_환경]" / "Working").mkdir()
    folder_auto_discovery.reset_for_tests()
    fixed = _discover(client)
    assert [item["name"] for item in fixed["created_requests"]] == ["[WR-0007]_[유통_환경]"]


def test_real_tree_layout_registers_without_deviations(admin_client):
    """Root/75R9J_PV/[WR-0002]_[유통_환경] and [WR-0001]_[사용_환경] with the standard tree (§3)."""
    client, root = admin_client
    dist = root / "75R9J_PV" / "[WR-0002]_[유통_환경]"
    case = "Package_Model_SetCase3_CushionCase3_조건표시"
    for option in ("INDIVIDUAL", "CUMULATIVE"):
        scene = dist / "Working" / case / "Drop" / "85qn80h_ref_organized" / option / "2_Face"
        scene.mkdir(parents=True)
        (scene / CSV).write_bytes(CSV_BYTES)
    for branch in ("CAE", "Report"):
        (dist / "Final" / branch / case / FINAL_ID / "Drop" / "85qn80h_ref_organized" / "INDIVIDUAL").mkdir(parents=True)
    (dist / "Final" / "CAD").mkdir()
    (dist / "Final" / ".finalizations" / FINAL_ID).mkdir(parents=True)
    usage = root / "75R9J_PV" / "[WR-0001]_[사용_환경]"
    settle = usage / "Working" / "Assy_RES_Model_SetCase1_StandCase1_Inner" / "Settle"
    settle.mkdir(parents=True)
    (settle / "75R9J_settle_result.json").write_text('{"Set Tilt Angle @ Settle (deg)": 1.18}', encoding="utf-8")
    (usage / "Final").mkdir()
    # §15 D22 Verifier 3: project-level CAD/Report manual folders are ignored (any case).
    (root / "75R9J_PV" / "CAD" / "drawings").mkdir(parents=True)
    (root / "75R9J_PV" / "report").mkdir()
    (root / "75R9J_PV" / "report" / "summary.pptx").write_bytes(b"manual")
    manual_before = sorted(p.relative_to(root).as_posix() for p in (root / "75R9J_PV").rglob("*")
                           if p.relative_to(root / "75R9J_PV").parts[0] in {"CAD", "report"})

    result = _discover(client)
    assert result["needs_review"] == [], result["needs_review"]
    assert sorted(p.relative_to(root).as_posix() for p in (root / "75R9J_PV").rglob("*")
                  if p.relative_to(root / "75R9J_PV").parts[0] in {"CAD", "report"}) == manual_before
    assert [item["name"] for item in result["created_projects"]] == ["75R9J_PV"]
    assert {(item["name"], item["environment"]) for item in result["created_requests"]} == {
        ("[WR-0002]_[유통_환경]", "DISTRIBUTION"), ("[WR-0001]_[사용_환경]", "USAGE")}
    with connect() as conn:
        schema_set = environment_folder_profiles.get_depth_schema(conn)["schema_set_id"]
        stored = conn.execute(
            "SELECT r.idempotency_key,p.rows_json FROM folder_environment_registrations r "
            "JOIN folder_environment_previews p ON p.id=r.preview_id "
            "JOIN folder_environment_scans s ON s.id=p.scan_id WHERE r.created_by=? AND s.root_key=?",
            [folder_auto_discovery.SYSTEM.user_id, _root_key(root)]).fetchall()
    assert len(stored) == 2
    for key, rows_json in stored:
        assert environment_service.preview_data(rows_json)["deviations"] == []
        assert key.startswith("auto-discovery-")
    assert {key for key, _ in stored} == {
        folder_auto_discovery.idempotency_key(_root_key(root), path, schema_set)
        for path in ("75R9J_PV/[WR-0002]_[유통_환경]", "75R9J_PV/[WR-0001]_[사용_환경]")}


# Registrations made by discovery under this test's synthetic root (PostgreSQL runs share one database).
_THIS_ROOT_REGISTRATIONS = (
    "FROM folder_environment_registrations r JOIN folder_environment_previews p ON p.id=r.preview_id "
    "JOIN folder_environment_scans s ON s.id=p.scan_id WHERE r.created_by=? AND s.root_key=?")


def _root_key(root):
    from app.services.folder_discovery_scan import root_identity
    return root_identity(root)


def test_repeat_is_idempotent_and_calls_are_coalesced(admin_client, monkeypatch):
    client, root = admin_client
    _container_schema()
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
    _container_schema()
    baseline = (_count("SELECT count(*) FROM analysis_requests"), _count("SELECT count(*) FROM projects"))
    (root / WR2 / "Working").mkdir(parents=True)
    results, errors = [], []

    def run():
        try:
            results.append(folder_auto_discovery.discover(force=True))
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
    _container_schema()
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
    _container_schema()
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


def test_running_discovery_never_blocks_a_caller(admin_client):
    client, root = admin_client
    _container_schema()
    (root / WR2 / "Working").mkdir(parents=True)
    opened = []
    assert folder_auto_discovery._run_lock.acquire(blocking=False)
    try:
        result = folder_auto_discovery.discover(force=True, connection_factory=lambda: opened.append(1))
        via_api = _discover(client)
    finally:
        folder_auto_discovery._run_lock.release()
    assert opened == []  # no DB connection while another run holds the lock
    assert result["status"] == via_api["status"] == "RUNNING" and result["created_requests"] == []
    assert _discover(client)["created_requests"]


def test_copied_or_renamed_request_folders_are_not_registered(admin_client):
    client, root = admin_client
    _container_schema()
    baseline = _count("SELECT count(*) FROM analysis_requests")
    (root / WR2 / "Working").mkdir(parents=True)
    for name in ("[WR-0002]_[유통_환경] - 복사본", "[WR-0002]_[유통_환경]_old", "WR_0002_SimType2"):
        (root / PROJECT / name / "Working").mkdir(parents=True)
    result = _discover(client)
    assert [item["name"] for item in result["created_requests"]] == ["[WR-0002]_[유통_환경]"]
    codes = {item["relative_path"].rsplit("/", 1)[-1]: item["code"] for item in result["needs_review"]}
    assert codes == {"[WR-0002]_[유통_환경] - 복사본": "WR_ALREADY_LINKED", "[WR-0002]_[유통_환경]_old": "WR_ALREADY_LINKED",
                     "WR_0002_SimType2": "ENV_KEYWORD_NONE"}
    assert _count("SELECT count(*) FROM analysis_requests") == baseline + 1
    # Also on a later run, after the first one is linked.
    again = _discover(client)
    assert again["created_requests"] == [] and {item["code"] for item in again["needs_review"]} == {
        "ENV_KEYWORD_NONE", "WR_ALREADY_LINKED"}


def test_failed_registration_backs_off_even_when_forced(admin_client, monkeypatch):
    client, root = admin_client
    _container_schema()
    (root / WR2 / "Working").mkdir(parents=True)
    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(environment_service, "register", failing)
    first = _discover(client, force=True)
    assert [item["code"] for item in first["needs_review"]] == ["REGISTRATION_FAILED"]
    scans = _count("SELECT count(*) FROM folder_environment_scans")
    second = _discover(client, force=True)
    assert [item["code"] for item in second["needs_review"]] == ["REGISTRATION_FAILED"]
    assert len(calls) == 1 and _count("SELECT count(*) FROM folder_environment_scans") == scans
    monkeypatch.setattr(folder_auto_discovery, "RETRY_BACKOFF_SECONDS", (0.0,))
    with folder_auto_discovery._state_lock:
        for value in folder_auto_discovery._retry_memo.values():
            value["until"] = 0.0
    _discover(client, force=True)
    assert len(calls) == 2


def test_admin_excluded_request_is_reported_not_hidden(admin_client):
    client, root = admin_client
    for scene in ("2_Face", "3_Face"):
        (root / OPTION / scene).mkdir(parents=True)
        (root / OPTION / scene / CSV).write_bytes(CSV_BYTES)
    excluded = "75R9J_PV/[WR-0009]_[유통_환경]"
    (root / excluded / "Working").mkdir(parents=True)
    scan = client.post("/api/folder-discovery/environments/scan", json={"environment": "DISTRIBUTION", "relative_path": ""}).json()
    assignments = [{"node_id": n["id"], "role_kind": "EXCLUDE", "confirm": True}
                    for n in scan["nodes"] if n["relative_path"] == excluded]
    preview = client.post("/api/folder-discovery/environments/previews", json={"scan_id": scan["id"], "assignments": assignments}).json()
    assert preview["can_apply"], preview
    registered = client.post("/api/folder-discovery/environments/registrations",
                             json={"preview_id": preview["id"], "idempotency_key": f"admin-exclude-{uuid4()}", "capture": False})
    assert registered.status_code == 200, registered.text
    result = _discover(client)
    assert result["created_requests"] == []
    assert {(item["relative_path"], item["code"]) for item in result["needs_review"]} == {(excluded, "ADMIN_EXCLUDED")}


def test_unlinked_project_with_same_name_is_not_duplicated(admin_client):
    client, root = admin_client
    _container_schema()
    created = client.post("/api/projects", json={"name": "75r9j_pv", "product_name": "TV"})
    assert created.status_code == 201, created.text
    before = _count("SELECT count(*) FROM projects")
    (root / WR2 / "Working").mkdir(parents=True)
    result = _discover(client)
    assert result["created_projects"] == [] and result["created_requests"] == []
    assert [(item["relative_path"], item["code"]) for item in result["needs_review"]] == [(PROJECT, "PROJECT_NAME_EXISTS")]
    assert _count("SELECT count(*) FROM projects") == before


def test_one_unreadable_child_does_not_hide_its_siblings(admin_client, monkeypatch):
    client, root = admin_client
    _container_schema()
    (root / WR2 / "Working").mkdir(parents=True)
    # An unreadable sibling project folder in the same container.
    (root / CONTAINER / "77777_PV" / "[WR-0008]_[사용_환경]").mkdir(parents=True)
    from app.services.storage import local as storage_local
    original = storage_local._is_reparse

    def flaky(path):
        if path.name == "77777_PV":
            raise spdm_storage.SpdmStorageError("SPDM_PATH_UNAVAILABLE", "synthetic")
        return original(path)

    monkeypatch.setattr(storage_local, "_is_reparse", flaky)
    result = _discover(client)
    assert [item["name"] for item in result["created_requests"]] == ["[WR-0002]_[유통_환경]"]
    assert {(item["relative_path"], item["code"]) for item in result["needs_review"]} == {
        (f"{CONTAINER}/77777_PV", "PATH_UNAVAILABLE")}


def test_race_lost_to_another_process_is_not_reported_as_created(admin_client, monkeypatch):
    client, root = admin_client
    _container_schema()
    (root / WR2 / "Working").mkdir(parents=True)
    audits_before = _count(AUTO_DISCOVERED_AUDITS)
    real_register = environment_service.register

    def other_process_wins(conn, preview_id, key, capture, principal, root_path, **kwargs):
        # Another process registers the same folder with its own preview first.
        other = conn.execute("SELECT scan_id FROM folder_environment_previews WHERE id=?", [preview_id]).fetchone()[0]
        rival = environment_service.preview(conn, other, [], principal.user_id, allow_without_cases=True)
        real_register(conn, rival["id"], key, capture, principal, root_path, **kwargs)
        return real_register(conn, preview_id, key, capture, principal, root_path, **kwargs)

    monkeypatch.setattr(environment_service, "register", other_process_wins)
    result = _discover(client)
    # The request was registered by the other process, not by this run; the project (added by this run
    # before any request, issue #55) is reported.
    assert result["created_requests"] == []
    assert [item["name"] for item in result["created_projects"]] == ["75R9J_PV"]
    assert _count(AUTO_DISCOVERED_AUDITS) == audits_before + 1


def test_discovery_can_be_disabled_for_isolated_runs(monkeypatch):
    from app.services import folder_auto_discovery
    monkeypatch.setenv("SIMDASH_AUTO_DISCOVERY", "0")
    assert folder_auto_discovery.discover()["status"] == "DISABLED"
    monkeypatch.setenv("SIMDASH_AUTO_DISCOVERY", "1")
    assert folder_auto_discovery.enabled()


def test_project_linked_under_previous_root_is_not_duplicated_after_root_move(admin_client, monkeypatch):
    """Field report 2026-10-02: the root moved from E:\\shared\\SPDM (Admin) to E:\\shared.

    The old links are keyed by the old root, so discovery under the new root
    created a second "75R9J_PV" project and a second WR-0001 request for the
    same folders; both then failed the materials ownership check.
    """
    client, root = admin_client
    _distribution_request(root, WR2)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root / CONTAINER))
    first = _discover(client, force=True)
    assert len(first["created_requests"]) == 1, first
    projects = _count("SELECT count(*) FROM projects")
    requests = _count("SELECT count(*) FROM analysis_requests")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    _container_schema()  # the project folder is one level deeper under the new root
    folder_auto_discovery.reset_for_tests()
    moved = _discover(client, force=True)
    assert moved["created_projects"] == [] and moved["created_requests"] == []
    assert (PROJECT, "PROJECT_NAME_EXISTS") in {(item["relative_path"], item["code"]) for item in moved["needs_review"]}
    assert _count("SELECT count(*) FROM projects") == projects
    assert _count("SELECT count(*) FROM analysis_requests") == requests


def test_root_unset_is_reported_to_administrators(admin_client, monkeypatch):
    client, _root = admin_client
    from app.services import folder_discovery

    def unset(conn):
        folder_discovery.fail("SPDM_ROOT_UNSET", "먼저 서버 저장소 경로를 설정하세요.")

    monkeypatch.setattr(folder_discovery, "configured_root", unset)
    result = _discover(client, force=True)
    assert result["status"] == "ROOT_UNSET"
    assert [item["code"] for item in result["needs_review"]] == ["SPDM_ROOT_UNSET"]


def test_deleted_registration_is_not_linked_and_request_is_registered_again(admin_client):
    """§13.5 / Verifier §13.8 item 15: a DELETED registration blocks nothing."""
    client, root = admin_client
    project_id, _existing = _seed(client, root)
    usage = "75R9J_PV/[WR-0001]_[사용_환경]"
    (root / usage / "Working").mkdir(parents=True)
    first = _discover(client)
    [created] = first["created_requests"]
    assert created["project_id"] == project_id  # LINK to the existing project, no new project
    assert first["created_projects"] == []
    with connect() as conn:
        old_id, old_key = conn.execute(
            f"SELECT r.id,r.idempotency_key {_THIS_ROOT_REGISTRATIONS}",
            [folder_auto_discovery.SYSTEM.user_id, _root_key(root)]).fetchone()
        try:
            conn.execute("UPDATE folder_environment_registrations SET status='DELETED' WHERE id=?", [old_id])
        except Exception as exc:  # pragma: no cover - CHECK constraint before migration 0035
            pytest.skip(f"pending migration 0035 (DELETED status): {type(exc).__name__}")
        conn.execute("DELETE FROM folder_environment_capture_jobs WHERE registration_id=?", [old_id])
        conn.execute("DELETE FROM folder_environment_registry WHERE registration_id=?", [old_id])
        linked = folder_auto_discovery._linked_state(conn, _root_key(root))
    assert usage.casefold() not in linked["requests"] and old_key in linked["deleted_keys"]
    assert old_key not in linked["keys"]

    folder_auto_discovery.reset_for_tests()
    again = _discover(client)
    assert [item["name"] for item in again["created_requests"]] == ["[WR-0001]_[사용_환경]"], again
    with connect() as conn:
        rows = conn.execute(
            f"SELECT r.idempotency_key,r.status {_THIS_ROOT_REGISTRATIONS} ORDER BY r.created_at",
            [folder_auto_discovery.SYSTEM.user_id, _root_key(root)]).fetchall()
    assert [status for _, status in rows][0] == "DELETED" and rows[1][1] != "DELETED"
    assert rows[1][0] != old_key
    # The new live registration links the folder again: a third run does nothing.
    folder_auto_discovery.reset_for_tests()
    assert _discover(client)["created_requests"] == []


# ---- [저장 폴더 새로고침] full rescan (storage_rescan, 2026-10-17) ----
RESCAN = "/api/folder-discovery/environments/rescan"


@pytest.fixture
def inline_rescan(monkeypatch):
    from app.services import storage_rescan
    storage_rescan.reset_for_tests()
    monkeypatch.setattr(storage_rescan, "INLINE", True)
    yield storage_rescan
    storage_rescan.reset_for_tests()


def _rescan(client):
    response = client.post(RESCAN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["started"] is True and body["status"] == "DONE", body
    status = client.get(RESCAN)
    assert status.status_code == 200, status.text
    assert status.json() == {key: value for key, value in body.items() if key != "started"}
    return body


def test_rescan_registers_every_new_request_beyond_one_run_limit(admin_client, inline_rescan, monkeypatch):
    client, root = admin_client
    _container_schema()
    monkeypatch.setattr(folder_auto_discovery, "MAX_NEW_REQUESTS_PER_RUN", 1)
    _distribution_request(root, WR2)
    (root / PROJECT / "[WR-0006]_[사용_환경]" / "Working").mkdir(parents=True)

    result = _rescan(client)
    assert sorted(item["name"] for item in result["created_requests"]) == ["[WR-0002]_[유통_환경]", "[WR-0006]_[사용_환경]"]
    assert [item["name"] for item in result["created_projects"]] == ["75R9J_PV"]
    assert result["rounds"] >= 2 and result["partial"] is False and result["needs_review"] == []
    assert result["phase"] == "SYNC" and result["progress"] == {"done": result["scopes"], "total": result["scopes"]}
    assert result["scopes"] >= 2 and result["failed"] == []
    assert result["started_by"] and result["finished_at"]
    assert _count("SELECT count(*) FROM audit_events WHERE action='STORAGE_RESCAN_STARTED'") >= 1


def test_rescan_reports_folders_needing_review_even_when_remembered(admin_client, inline_rescan):
    client, root = admin_client
    _container_schema()
    (root / PROJECT / "[WR-0003]_[기타]" / "Working").mkdir(parents=True)
    (root / PROJECT / "[WR-0007]_[유통_환경]" / "Final").mkdir(parents=True)  # no Working
    first = _discover(client)
    assert {item["code"] for item in first["needs_review"]} == {"ENV_KEYWORD_NONE", "DEPTH_DEVIATION"}
    scans = _count("SELECT count(*) FROM folder_environment_scans")

    result = _rescan(client)
    reviews = {item["relative_path"]: item for item in result["needs_review"]}
    assert set(reviews) == {f"{PROJECT}/[WR-0003]_[기타]", f"{PROJECT}/[WR-0007]_[유통_환경]"}
    assert reviews[f"{PROJECT}/[WR-0003]_[기타]"]["code"] == "ENV_KEYWORD_NONE"
    assert reviews[f"{PROJECT}/[WR-0003]_[기타]"]["reason"]
    deviation = reviews[f"{PROJECT}/[WR-0007]_[유통_환경]"]
    assert (deviation["code"], deviation["deviation_code"]) == ("DEPTH_DEVIATION", "WORKING_MISSING")
    assert deviation["reason"]
    # The review memo was cleared: the remembered folder was examined again, not just repeated.
    assert _count("SELECT count(*) FROM folder_environment_scans") > scans
    assert result["created_requests"] == []


def test_rescan_requires_global_administrator(admin_client, inline_rescan, monkeypatch):
    client, _ = admin_client
    from app.routers import folder_discovery_environment as router

    def denied(request, permission, project_id=None, **kwargs):
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})

    monkeypatch.setattr(router, "require_permission", denied)
    assert client.post(RESCAN).status_code == 403
    assert client.get(RESCAN).status_code == 403
    assert inline_rescan.state()["status"] == "IDLE"


# --- renamed request folder (user decision 2026-10-17) ---------------------------------------------

SPACED = f"{PROJECT}/WR-0000806_유통 환경 시뮬레이션"
JOINED = f"{PROJECT}/WR-0000806_유통환경시뮬레이션"  # whitespace-only difference: the same folder
RENAMED = f"{PROJECT}/WR-0000806_유통_환경_시뮬레이션"  # a real rename: paired by WR number
RENAMED_AUDITS = "SELECT count(*) FROM audit_events WHERE action='FOLDER_REQUEST_FOLDER_RENAMED'"


def _sync(client, project_id, request_id, environment="DISTRIBUTION"):
    response = client.post("/api/folder-discovery/environments/sync",
                           json={"project_id": project_id, "request_id": request_id, "environment": environment})
    assert response.status_code == 200, response.text
    return response.json()


def _registered_request(client, root, path=SPACED):
    _container_schema()
    _distribution_request(root, path)
    [created] = _discover(client)["created_requests"]
    project_id, request_id = created["project_id"], created["id"]
    assert _sync(client, project_id, request_id)["status"] in {"REFRESHED", "UNCHANGED"}  # an active snapshot
    folder_auto_discovery.reset_for_tests()
    return project_id, request_id


def _request_rows(request_id):
    with connect() as conn:
        return conn.execute("SELECT id,status,deleted_by FROM folder_environment_registrations WHERE request_id=? "
                            "ORDER BY created_at,id", [request_id]).fetchall()


def test_renamed_request_folder_is_relinked_to_the_same_request(admin_client):
    from app.services import folder_schema_resolver as resolver

    client, root = admin_client
    project_id, request_id = _registered_request(client, root)
    requests_before = _count("SELECT count(*) FROM analysis_requests")
    old_cases = _count("SELECT count(*) FROM dashboard_cases WHERE request_id=?", [request_id])
    old_captures = _count("SELECT count(*) FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id "
                          "WHERE dc.request_id=?", [request_id])
    [old_registration] = [row[0] for row in _request_rows(request_id)]
    audits_before = _count(RENAMED_AUDITS)
    assert old_cases and old_captures

    (root / SPACED).rename(root / JOINED)  # SPDM: spaces removed from the request folder name
    result = _discover(client)
    assert result["created_requests"] == [] and result["created_projects"] == [], result
    assert result["needs_review"] == [], result["needs_review"]
    [renamed] = result["renamed_requests"]
    assert (renamed["id"], renamed["project_id"], renamed["environment"]) == (request_id, project_id, "DISTRIBUTION")
    assert (renamed["old_path"], renamed["new_path"]) == (SPACED, JOINED)
    assert _count("SELECT count(*) FROM analysis_requests") == requests_before  # no duplicate request
    # The old folder's registration is a tombstone; one live registration (the LINK) remains.
    rows = _request_rows(request_id)
    assert [(row[0], row[1], row[2]) for row in rows if row[0] == old_registration] == [
        (old_registration, "DELETED", folder_auto_discovery.SYSTEM.user_id)]
    assert len([row for row in rows if row[1] != "DELETED"]) == 1
    # Nothing of the request was deleted: the old Cases and captures stay (history).
    assert _count("SELECT count(*) FROM dashboard_cases WHERE request_id=? AND relative_path LIKE ?",
                  [request_id, f"{SPACED}/%"]) == old_cases
    assert _count("SELECT count(*) FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id "
                  "WHERE dc.request_id=?", [request_id]) >= old_captures
    with connect() as conn:
        assert resolver._request_path(conn, _root_key(root), project_id, request_id, "DISTRIBUTION") == JOINED
    # Results are read from the new folder (registration capture, then the next sync).
    assert _count("SELECT count(*) FROM dashboard_cases WHERE request_id=? AND relative_path LIKE ?",
                  [request_id, f"{JOINED}/%"]) == old_cases
    assert {"2_Face", "3_Face"} <= _scenes(client, project_id, request_id)
    assert _sync(client, project_id, request_id)["status"] in {"REFRESHED", "UNCHANGED"}
    catalog = client.get("/api/dashboard/catalog", params={"project_id": project_id, "request_id": request_id,
                                                           "environment": "DISTRIBUTION"}).json()
    assert catalog["folder_schema"]["status"] == "AVAILABLE", catalog["folder_schema"]
    assert {"2_Face", "3_Face"} <= _scenes(client, project_id, request_id)
    assert _count(RENAMED_AUDITS) == audits_before + 1
    # Idempotent: the next run finds the new folder linked.
    folder_auto_discovery.reset_for_tests()
    again = _discover(client)
    assert again["created_requests"] == [] and again["renamed_requests"] == [] and again["needs_review"] == []
    # Non-administrators see the request, not the folder paths.
    visible = folder_auto_discovery.visible_result(result, is_global_admin=False)["renamed_requests"]
    assert visible == [{"id": request_id, "name": JOINED.rsplit("/", 1)[-1], "environment": "DISTRIBUTION",
                        "project_id": project_id}]


def test_renamed_request_folder_with_two_candidates_needs_review(admin_client):
    client, root = admin_client
    project_id, request_id = _registered_request(client, root)
    requests_before = _count("SELECT count(*) FROM analysis_requests")
    (root / SPACED).rename(root / RENAMED)
    (root / f"{RENAMED}_v2" / "Working").mkdir(parents=True)
    result = _discover(client)
    assert result["created_requests"] == [] and result["renamed_requests"] == []
    codes = {item["relative_path"]: item["code"] for item in result["needs_review"]}
    assert codes == {RENAMED: "RENAME_AMBIGUOUS", f"{RENAMED}_v2": "RENAME_AMBIGUOUS"}
    assert "여러 개" in result["needs_review"][0]["reason"]
    assert _count("SELECT count(*) FROM analysis_requests") == requests_before
    assert [row[1] for row in _request_rows(request_id)] != ["DELETED"]  # still linked to the old folder
    # Once one candidate is gone the rename is followed.
    (root / f"{RENAMED}_v2" / "Working").rmdir()
    (root / f"{RENAMED}_v2").rmdir()
    folder_auto_discovery.reset_for_tests()
    assert [item["id"] for item in _discover(client)["renamed_requests"]] == [request_id]


def test_copy_of_an_existing_request_folder_is_still_not_registered(admin_client):
    client, root = admin_client
    _project_id, request_id = _registered_request(client, root)
    (root / RENAMED / "Working").mkdir(parents=True)  # a copy: the registered folder still exists
    result = _discover(client)
    assert result["created_requests"] == [] and result["renamed_requests"] == []
    assert [(item["relative_path"], item["code"]) for item in result["needs_review"]] == [(RENAMED, "WR_ALREADY_LINKED")]
    assert [row[1] for row in _request_rows(request_id)] != ["DELETED"]


def test_unreadable_listing_is_never_taken_as_a_rename():
    class Lister:
        def __init__(self, tree, broken=()):
            self.tree, self.broken, self.issues, self.calls = tree, set(broken), [], []

        def children(self, relative):
            self.calls.append(relative)
            if relative in self.broken:
                self.issues.append({"relative_path": relative, "code": "PATH_UNAVAILABLE"})
                return []
            return [(name, f"{relative}/{name}" if relative else name) for name in self.tree.get(relative, [])]

    tree = {"": ["SPDM (Admin)"], "SPDM (Admin)": ["75R9J_PV"], "SPDM (Admin)/75R9J_PV": ["WR-0001_유통"]}
    absent = folder_auto_discovery._absent
    assert absent(Lister(tree), "SPDM (Admin)/75R9J_PV/WR-0002_유통", {}) is True
    assert absent(Lister(tree), "SPDM (Admin)/75R9J_PV/wr-0001_유통", {}) is False  # case-insensitive
    assert absent(Lister(tree, broken={"SPDM (Admin)/75R9J_PV"}), "SPDM (Admin)/75R9J_PV/WR-0002_유통", {}) is False
    gone_project = Lister(tree)
    assert absent(gone_project, "SPDM (Admin)/OLD_PV/WR-0002_유통", {}) is True
    assert gone_project.calls == ["", "SPDM (Admin)"]  # a missing folder is never listed itself


# --- renamed project folder (user decision 2026-10-17) ---------------------------------------------

OLD_PROJECT = f"{CONTAINER}/PROJ-000099_R95J 개발 과제"
NEW_PROJECT = f"{CONTAINER}/PROJ-000099_R95J_개발과제"  # not whitespace-only: paired by WR number
JOINED_PROJECT = f"{CONTAINER}/PROJ-000099_R95J개발과제"  # whitespace-only: the same folder (name_key)
R1, R2, R3 = "WR-0000991_유통 환경 시뮬레이션", "[WR-0000992]_[사용_환경]", "WR-0000993_유통 환경"
PROJECT_RENAMED_AUDITS = "SELECT count(*) FROM audit_events WHERE action='FOLDER_PROJECT_FOLDER_RENAMED'"


def _registered_project(client, root, extra=()):
    """A project folder with two registered requests (one with Cases and an active snapshot)."""
    _container_schema()
    _distribution_request(root, f"{OLD_PROJECT}/{R1}")
    (root / OLD_PROJECT / R2 / "Working").mkdir(parents=True)
    for path in extra:
        (root / path / "Working").mkdir(parents=True)
    result = _discover(client)
    assert result["needs_review"] == [], result["needs_review"]
    project = next(item for item in result["created_projects"] if item["name"] == OLD_PROJECT.rsplit("/", 1)[-1])
    requests = {item["name"]: item["id"] for item in result["created_requests"] if item["project_id"] == project["id"]}
    assert set(requests) == {R1, R2}
    assert _sync(client, project["id"], requests[R1])["status"] in {"REFRESHED", "UNCHANGED"}
    folder_auto_discovery.reset_for_tests()
    return project["id"], requests


def _counts():
    return (_count("SELECT count(*) FROM projects"), _count("SELECT count(*) FROM analysis_requests"),
            _count("SELECT count(*) FROM folder_environment_registrations"))


def _live_statuses(request_id):
    return [row[1] for row in _request_rows(request_id)]


def test_renamed_project_folder_keeps_the_project_and_follows_its_requests(admin_client):
    from app.services import folder_schema_resolver as resolver

    client, root = admin_client
    project_id, requests = _registered_project(client, root)
    old_registrations = {name: [row[0] for row in _request_rows(request_id)] for name, request_id in requests.items()}
    assert all(len(ids) == 1 for ids in old_registrations.values())
    old_cases = _count("SELECT count(*) FROM dashboard_cases WHERE request_id=?", [requests[R1]])
    projects_before, requests_before, _ = _counts()
    audits = (_count(PROJECT_RENAMED_AUDITS), _count(RENAMED_AUDITS))
    assert old_cases

    (root / OLD_PROJECT).rename(root / NEW_PROJECT)  # SPDM: spaces removed from the project folder name
    (root / NEW_PROJECT / R3 / "Working").mkdir(parents=True)  # plus a request that is new
    result = _discover(client)
    assert result["needs_review"] == [], result["needs_review"]
    assert result["created_projects"] == []
    assert result["renamed_projects"] == [{"id": project_id, "name": "PROJ-000099_R95J 개발 과제",
                                           "old_path": OLD_PROJECT, "new_path": NEW_PROJECT}]
    renamed = {item["id"]: item for item in result["renamed_requests"]}
    assert set(renamed) == set(requests.values())
    for name, request_id in requests.items():
        assert (renamed[request_id]["old_path"], renamed[request_id]["new_path"]) == (
            f"{OLD_PROJECT}/{name}", f"{NEW_PROJECT}/{name}")
        assert renamed[request_id]["project_id"] == project_id
    [created] = result["created_requests"]
    assert (created["name"], created["project_id"]) == (R3, project_id)  # new request, same project
    # No new project; one new request only; the project's display name is unchanged.
    assert _counts()[:2] == (projects_before, requests_before + 1)
    assert _count("SELECT count(*) FROM projects WHERE id=? AND name=?", [project_id, "PROJ-000099_R95J 개발 과제"]) == 1
    # The old folders' registrations are tombstones; data is kept.
    for name, request_id in requests.items():
        rows = _request_rows(request_id)
        [old_id] = old_registrations[name]
        assert [(row[1], row[2]) for row in rows if row[0] == old_id] == [("DELETED", folder_auto_discovery.SYSTEM.user_id)]
        assert len([row for row in rows if row[1] != "DELETED"]) == 1
        with connect() as conn:
            assert resolver._request_path(conn, _root_key(root), project_id, request_id,
                                          renamed[request_id]["environment"]) == f"{NEW_PROJECT}/{name}"
    assert _count("SELECT count(*) FROM dashboard_cases WHERE request_id=? AND relative_path LIKE ?",
                  [requests[R1], f"{OLD_PROJECT}/%"]) == old_cases
    assert _count("SELECT count(*) FROM dashboard_cases WHERE request_id=? AND relative_path LIKE ?",
                  [requests[R1], f"{NEW_PROJECT}/%"]) == old_cases
    assert {"2_Face", "3_Face"} <= _scenes(client, project_id, requests[R1])
    # The new project folder is linked to the same project (makes later runs idempotent).
    with connect() as conn:
        linked = folder_auto_discovery._linked_state(conn, _root_key(root))
    assert linked["projects"][folder_auto_discovery._node_key(NEW_PROJECT)] == {project_id}
    assert (_count(PROJECT_RENAMED_AUDITS), _count(RENAMED_AUDITS)) == (audits[0] + 1, audits[1] + 2)
    folder_auto_discovery.reset_for_tests()
    again = _discover(client)
    assert (again["created_projects"], again["created_requests"], again["renamed_projects"],
            again["renamed_requests"], again["needs_review"]) == ([], [], [], [], [])
    assert _counts()[:2] == (projects_before, requests_before + 1)
    # Non-administrators see the project, not the folder paths.
    visible = folder_auto_discovery.visible_result(result, is_global_admin=False)
    assert visible["renamed_projects"] == [{"id": project_id, "name": "PROJ-000099_R95J 개발 과제"}]
    assert all("old_path" not in item and "new_path" not in item for item in visible["renamed_requests"])


def test_renamed_project_folder_requests_left_by_the_run_limit_follow_on_the_next_run(admin_client, monkeypatch):
    client, root = admin_client
    project_id, requests = _registered_project(client, root)
    projects_before, requests_before, _ = _counts()
    monkeypatch.setattr(folder_auto_discovery, "MAX_NEW_REQUESTS_PER_RUN", 1)
    (root / OLD_PROJECT).rename(root / NEW_PROJECT)
    first = _discover(client)
    assert [item["id"] for item in first["renamed_projects"]] == [project_id] and first["partial"] is True
    assert len(first["renamed_requests"]) == 1
    folder_auto_discovery.reset_for_tests()
    second = _discover(client)  # the project folder is linked now; the other request still follows
    assert second["renamed_projects"] == [] and second["created_requests"] == [], second
    assert {item["id"] for item in first["renamed_requests"] + second["renamed_requests"]} == set(requests.values())
    assert _counts()[:2] == (projects_before, requests_before)


OTHER_REQUEST = f"{CONTAINER}/OTHER_PV/WR-0000995_유통 환경"


@pytest.mark.parametrize("rival", ["second_candidate", "other_project"])
def test_renamed_project_folder_with_an_ambiguous_match_needs_review(admin_client, rival):
    client, root = admin_client
    project_id, requests = _registered_project(client, root, extra=[OTHER_REQUEST] if rival == "other_project" else [])
    before = _counts()
    (root / OLD_PROJECT).rename(root / NEW_PROJECT)
    rival_path = f"{CONTAINER}/PROJ-000099_R95J_copy"
    if rival == "second_candidate":
        (root / rival_path / R1 / "Working").mkdir(parents=True)
        expected = {NEW_PROJECT: "PROJECT_RENAME_AMBIGUOUS", rival_path: "PROJECT_RENAME_AMBIGUOUS"}
    else:  # one request of the new folder carries another (present) project's WR number
        (root / NEW_PROJECT / OTHER_REQUEST.rsplit("/", 1)[-1] / "Working").mkdir(parents=True)
        expected = {NEW_PROJECT: "PROJECT_RENAME_AMBIGUOUS"}
    result = _discover(client)
    assert {item["relative_path"]: item["code"] for item in result["needs_review"]} == expected
    assert "여러" in result["needs_review"][0]["reason"]
    assert (result["created_projects"], result["created_requests"], result["renamed_projects"],
            result["renamed_requests"]) == ([], [], [], [])
    assert _counts() == before  # nothing registered for the candidates
    for request_id in requests.values():
        assert "DELETED" not in _live_statuses(request_id)
    if rival == "second_candidate":  # once the rival is gone the rename is followed
        import shutil
        shutil.rmtree(root / rival_path)
        folder_auto_discovery.reset_for_tests()
        followed = _discover(client)
        assert [item["id"] for item in followed["renamed_projects"]] == [project_id]
        assert {item["id"] for item in followed["renamed_requests"]} == set(requests.values())


@pytest.mark.parametrize("legacy", ["spdm_project_parent", "legacy_registry"])
def test_renamed_project_folder_with_a_legacy_binding_is_not_followed(admin_client, legacy):
    client, root = admin_client
    project_id, requests = _registered_project(client, root)
    with connect() as conn:
        if legacy == "spdm_project_parent":
            conn.execute("INSERT INTO spdm_storage_project_parents(project_folder, project_id, created_at, updated_at) "
                         "VALUES (?,?,current_timestamp,current_timestamp)", [f"legacy-{uuid4()}", project_id])
        else:
            conn.execute("INSERT INTO folder_discovery_registry(id, root_key, relative_path, role, role_kind, scope_key, "
                         "code, name, analysis_type, parent_target_id, target_id, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,current_timestamp)",
                         [str(uuid4()), _root_key(root), f"{CONTAINER}/legacy-{uuid4().hex[:8]}", "PROJECT", "PROJECT",
                          "", None, "legacy", "", None, project_id])
    before = _counts()
    (root / OLD_PROJECT).rename(root / NEW_PROJECT)
    result = _discover(client)
    assert [(item["relative_path"], item["code"]) for item in result["needs_review"]] == [
        (NEW_PROJECT, "PROJECT_RENAME_UNSUPPORTED")]
    assert "자동으로 옮길 수 없습니다" in result["needs_review"][0]["reason"]
    assert (result["created_projects"], result["created_requests"], result["renamed_projects"],
            result["renamed_requests"]) == ([], [], [], [])
    assert _counts() == before
    for request_id in requests.values():
        assert "DELETED" not in _live_statuses(request_id)


def test_present_old_project_folder_is_not_taken_as_a_rename(admin_client):
    """A copy of a project folder (the linked one still exists) keeps the earlier behaviour."""
    client, root = admin_client
    _project_id, requests = _registered_project(client, root)
    (root / NEW_PROJECT / R1 / "Working").mkdir(parents=True)
    result = _discover(client)
    assert result["renamed_projects"] == [] and result["renamed_requests"] == []
    assert [item["name"] for item in result["created_projects"]] == ["PROJ-000099_R95J_개발과제"]
    for request_id in requests.values():
        assert "DELETED" not in _live_statuses(request_id)
