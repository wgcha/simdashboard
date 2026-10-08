"""결과 등록 "폴더 구조 만들기": request folder → Working → Case folders per environment (synthetic data, temp root)."""
from __future__ import annotations

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery, folder_auto_sync, result_drop_upload, result_folder_structure
from app.services.storage import local as storage_local
from tests.test_new_scene_registration import CASE, REQUEST, _seed, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration
REG = "/api/result-registration"
PROJECT = REQUEST.split("/")[0]
USAGE_FOLDER = f"{PROJECT}/[WR-0001]_[사용_환경]"
CASE_NAME = CASE.rsplit("/", 1)[-1]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    folder_auto_sync.reset_for_tests()
    folder_auto_discovery.reset_for_tests()
    result_drop_upload.reset_for_tests()
    monkeypatch.setattr(folder_auto_sync, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_sync, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setenv("SIMDASH_AUTO_DISCOVERY", "1")
    yield
    folder_auto_sync.reset_for_tests()
    folder_auto_discovery.reset_for_tests()


def _overview(client, project_id, request_id, **params):
    response = client.get(REG + "/drop-target/structure", params={"project_id": project_id, "request_id": request_id, **params})
    assert response.status_code == 200, response.text
    return {item["environment"]: item for item in response.json()["environments"]}, response.json()


def _create(client, project_id, request_id, environment, **body):
    return client.post(REG + "/drop-target/structure", json={"project_id": project_id, "request_id": request_id,
                                                             "environment": environment, **body})


def _tree(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_dir())


def _request_count():
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM analysis_requests").fetchone()[0]


def test_overview_finds_the_linked_folder_and_proposes_the_missing_environment(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    environments, body = _overview(client, project_id, request_id)
    distribution, usage = environments["DISTRIBUTION"], environments["USAGE"]
    assert body["wr_key"] == "0001" and [item["relative_path"] for item in body["project_folders"]] == [PROJECT]
    assert distribution["status"] == "LINKED" and distribution["folder"]["relative_path"] == REQUEST
    assert distribution["folder"]["working_exists"] and distribution["folder"]["existing_cases"] == [CASE_NAME]
    # Prefill: the request's registered Cases of that environment (dashboard_cases / registry), marked as existing.
    assert {(item["name"], item["source"], item["exists"]) for item in distribution["case_suggestions"]} == {
        (CASE_NAME, "REGISTERED", True)}
    assert usage["status"] == "MISSING" and usage["folder"] is None
    assert usage["proposal"]["name"] == "[WR-0001]_[사용_환경]" and usage["proposal"]["parent_relative_path"] == PROJECT


def test_cases_are_created_in_the_linked_folder_and_a_rerun_is_idempotent(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    response = _create(client, project_id, request_id, "DISTRIBUTION", request_relative_path=REQUEST,
                       case_names=["New_Case_A", CASE_NAME, " ", "New_Case_B"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert [item["name"] for item in result["created"]] == ["New_Case_A", "New_Case_B"]
    assert {item["name"] for item in result["existing"]} == {"Working", CASE_NAME}
    assert result["link"]["status"] == "LINKED" and result["sync"]["status"] in {"REFRESHED", "UNCHANGED"}
    assert (root / REQUEST / "Working/New_Case_A").is_dir() and not any((root / REQUEST / "Working/New_Case_A").iterdir())
    before = _tree(root)
    again = _create(client, project_id, request_id, "DISTRIBUTION", request_relative_path=REQUEST,
                    case_names=["New_Case_A", "new_case_b"])
    assert again.status_code == 200, again.text
    assert again.json()["created"] == [] and {item["name"] for item in again.json()["existing"]} == {"Working", "New_Case_A", "New_Case_B"}
    assert _tree(root) == before
    with connect() as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM audit_events WHERE action LIKE 'RESULT_STRUCTURE_%' ORDER BY occurred_at").fetchall()]
    assert actions == ["RESULT_STRUCTURE_CREATED"]


def test_missing_environment_folder_is_created_after_confirming_the_exact_name_and_linked_to_the_request(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    proposal = _overview(client, project_id, request_id)[0]["USAGE"]["proposal"]
    requests = _request_count()
    changed = _create(client, project_id, request_id, "USAGE", new_request_folder={**proposal, "name": "[WR-0001]_[사용]"},
                      case_names=["Assy_Case1"])
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "RESULT_STRUCTURE_PROPOSAL_CHANGED"
    assert not (root / USAGE_FOLDER).exists()
    response = _create(client, project_id, request_id, "USAGE",
                       new_request_folder={"parent_relative_path": proposal["parent_relative_path"], "name": proposal["name"]},
                       case_names=["Assy_Case1", "Assy_Case2"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert [(item["role"], item["name"]) for item in result["created"]] == [
        ("REQUEST", "[WR-0001]_[사용_환경]"), ("WORKING", "Working"), ("SIMULATION_CASE", "Assy_Case1"),
        ("SIMULATION_CASE", "Assy_Case2")]
    assert (root / USAGE_FOLDER / "Working/Assy_Case2").is_dir()
    # The auto-discovery registered the new folder as a LINK to the selected request, not a new request.
    assert result["link"]["status"] == "LINKED", result["link"]
    assert _request_count() == requests
    environments, _ = _overview(client, project_id, request_id)
    assert environments["USAGE"]["status"] == "LINKED" and environments["USAGE"]["folder"]["relative_path"] == USAGE_FOLDER
    assert environments["USAGE"]["folder"]["existing_cases"] == ["Assy_Case1", "Assy_Case2"]
    assert not (root / REQUEST / "Final").exists() and not (root / USAGE_FOLDER / "Final").exists()


def test_invalid_case_names_are_refused_before_any_write(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    before = _tree(root)
    response = _create(client, project_id, request_id, "DISTRIBUTION", request_relative_path=REQUEST,
                       case_names=["ok_case", "../x", "CON", "Working", "final", "a:b", ".hidden", "Dup", "dup", "Drop"])
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "RESULT_STRUCTURE_NAME_INVALID"
    assert {item["name"] for item in detail["problems"]} == {"../x", "CON", "Working", "final", "a:b", ".hidden", "dup", "Drop"}
    assert _tree(root) == before
    too_many = _create(client, project_id, request_id, "DISTRIBUTION", request_relative_path=REQUEST,
                       case_names=[f"c{index}" for index in range(result_folder_structure.MAX_CASES + 1)])
    assert too_many.status_code == 422


def test_targets_are_chosen_explicitly_and_other_folders_are_refused(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    plain = f"{PROJECT}/[WR-0001]_plain"
    (root / plain).mkdir()
    (root / f"{PROJECT}/[WR-0001]_[사용_환경]_old").mkdir()
    (root / f"{PROJECT}/[WR-0001]_[사용_환경]").mkdir()
    environments, body = _overview(client, project_id, request_id)
    assert environments["USAGE"]["status"] == "AMBIGUOUS"
    assert set(environments["USAGE"]["choices"]) == {USAGE_FOLDER, f"{USAGE_FOLDER}_old"}
    assert next(item for item in body["candidates"] if item["relative_path"] == plain)["environment"] is None
    # The distribution folder is not a usage folder; nothing outside the project's request folders.
    for target, code in ((REQUEST, "RESULT_STRUCTURE_TARGET_INVALID"), (f"{REQUEST}/Working", "RESULT_STRUCTURE_TARGET_INVALID"),
                         ("elsewhere/[WR-0001]_[사용_환경]", "RESULT_STRUCTURE_TARGET_INVALID")):
        response = _create(client, project_id, request_id, "USAGE", request_relative_path=target, case_names=["A"])
        assert response.status_code == 422 and response.json()["detail"]["code"] == code, response.text
    # A folder without an environment keyword needs a confirmation (the dashboard will not read it).
    warned = _create(client, project_id, request_id, "USAGE", request_relative_path=plain, case_names=["A"])
    assert warned.status_code == 409 and warned.json()["detail"]["code"] == "RESULT_STRUCTURE_NAME_WARNING"
    assert not (root / plain / "Working").exists()
    confirmed = _create(client, project_id, request_id, "USAGE", request_relative_path=plain, case_names=["A"], confirm=True)
    assert confirmed.status_code == 200, confirmed.text
    assert (root / plain / "Working/A").is_dir() and confirmed.json()["link"]["status"] == "NONE"
    # Choosing one of the ambiguous usage folders explicitly works and is linked to this request.
    chosen = _create(client, project_id, request_id, "USAGE", request_relative_path=USAGE_FOLDER, case_names=["B"])
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["link"]["status"] == "LINKED"


def test_folder_of_another_request_is_refused(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    other = f"{PROJECT}/[WR-0002]_[사용_환경]"
    (root / other / "Working/Other_Case").mkdir(parents=True)
    discovered = client.post("/api/folder-discovery/environments/discover", json={"force": True})
    assert discovered.status_code == 200, discovered.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM folder_environment_registry WHERE relative_path=? AND role_kind='REQUEST'",
                            [other]).fetchone()[0] == 1
    response = _create(client, project_id, request_id, "USAGE", request_relative_path=other, case_names=["A"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_PATH_OWNERSHIP_CONFLICT", response.text
    assert not (root / other / "Working/A").exists()


def test_structure_permission_is_required(admin_client, monkeypatch):
    from fastapi import HTTPException
    from app.routers import result_registration as router

    client, root = admin_client
    project_id, request_id = _seed(client, root)
    seen = []

    def denied(request, permission, kind, resource_id, **kwargs):
        seen.append((permission, kind, resource_id))
        raise HTTPException(403, detail={"code": "RESULT_IMPORT_DENIED"})

    monkeypatch.setattr(router, "require_resource_permission", denied)
    responses = [client.get(REG + "/drop-target/structure", params={"project_id": project_id, "request_id": request_id}),
                 _create(client, project_id, request_id, "DISTRIBUTION", request_relative_path=REQUEST, case_names=["X"])]
    assert [response.status_code for response in responses] == [403, 403]
    assert set(seen) == {("result.import", "request", request_id)}
    assert not (root / REQUEST / "Working/X").exists()


def test_skeleton_zone_rule_and_refused_primitives(tmp_path):
    from app.services.storage.provider import StorageError, check_write, skeleton_zone_allows

    cases = {"P/[WR-1]_[사용_환경]": True, "P/[WR-1]_[유통_환경]/Working": True, "C/P/R_유통/working": True,
             "[WR-1]_[사용_환경]": False, "P/[WR-1]_[사용_유통]": False, "P/[WR-1]": False, "P/Working": False,
             "P/R_사용/Final": False, "P/R_사용/Working/Case": False, "P/R_사용/Final/Working": False,
             "P/../R_사용": False, "P/.x_사용": False, "P/R_사용/Working/Working": False}
    assert {key: skeleton_zone_allows(key) for key in cases} == cases
    with pytest.raises(StorageError):
        check_write("P/R_사용", "SKELETON", "app.services.result_drop_upload")
    check_write("P/R_사용", "SKELETON", "app.services.result_folder_structure")
    fs = storage_local.LocalFsProvider(tmp_path)
    (tmp_path / "P/R_사용").mkdir(parents=True)
    for call in (lambda: fs.mkdirs("P/R_사용/Working", zone="SKELETON"),
                 lambda: fs.remove("P/R_사용", zone="SKELETON", directory=True),
                 lambda: fs.rename_no_replace("P/R_사용", "P/S_사용", zone="SKELETON"),
                 lambda: fs.set_hidden("P/R_사용", zone="SKELETON"),
                 lambda: fs.write_chunk("P/R_사용/Working", 0, b"", zone="SKELETON"),
                 lambda: fs.mkdir_pinned("P/R_사용/Working", zone="SKELETON")):  # caller is not the structure module
        with pytest.raises(StorageError) as error:
            call()
        assert error.value.code == "NOT_ALLOWED_WRITE"
    assert (tmp_path / "P/R_사용").is_dir() and not (tmp_path / "P/R_사용/Working").exists()
