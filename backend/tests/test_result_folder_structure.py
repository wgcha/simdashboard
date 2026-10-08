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
    from app.services.storage.provider import StorageError, check_write, skeleton_request_depth, skeleton_zone_allows

    cases = {"P/[WR-1]_[사용_환경]": True, "P/[WR-1]_[유통_환경]/Working": True, "C/P/R_유통/working": True,
             "[WR-1]_[사용_환경]": False, "P/[WR-1]_[사용_유통]": False, "P/[WR-1]": False, "P/Working": False,
             "P/R_사용/Final": False, "P/R_사용/Working/Case": False, "P/R_사용/Final/Working": False,
             "P/../R_사용": False, "P/.x_사용": False, "P/R_사용/Working/Working": False}
    assert {key: skeleton_zone_allows(key) for key in cases} == cases
    # L4: depth-aware — the request folder at exactly the request depth, Working directly below it.
    depth2 = {"P/R_사용": True, "P/R_사용/Working": True, "C/P/R_사용": False, "C/P/R_유통/Working": False,
              "P/X/R_사용": False}
    assert {key: skeleton_zone_allows(key, 2) for key in depth2} == depth2
    assert skeleton_zone_allows("C/P/R_유통/Working", 3) and not skeleton_zone_allows("P/R_유통/Working", 3)
    structure = "app.services.result_folder_structure"
    with pytest.raises(StorageError):
        check_write("P/R_사용", "SKELETON", "app.services.result_drop_upload", "mkdir_pinned")
    with pytest.raises(StorageError):  # no declared request depth
        check_write("P/R_사용", "SKELETON", structure, "mkdir_pinned")
    with skeleton_request_depth(2):
        check_write("P/R_사용", "SKELETON", structure, "mkdir_pinned")
        for path, operation in (("C/P/R_사용", "mkdir_pinned"), ("P/R_사용", "mkdirs"), ("P/R_사용", None)):
            with pytest.raises(StorageError):
                check_write(path, "SKELETON", structure, operation)
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


def _as_structure_module(code: str, **names):
    """Run ``code`` with the structure module as the provider's calling module (L3 allowlist probe)."""
    exec(compile(code, "<structure-probe>", "exec"), {"__name__": "app.services.result_folder_structure", **names})


def test_structure_module_may_only_create_folders_in_working_and_skeleton(tmp_path):
    from app.services.storage.provider import StorageError, skeleton_request_depth

    fs = storage_local.LocalFsProvider(tmp_path)
    (tmp_path / "P/R_사용/Working/Case").mkdir(parents=True)
    for code in ('fs.create_exclusive("P/R_사용/Working/Case/a.txt", b"x", zone="WORKING")',
                 'fs.write_chunk("P/R_사용/Working/Case/a.part", 0, b"x", zone="WORKING")',
                 'fs.remove("P/R_사용/Working/Case", zone="WORKING", directory=True)',
                 'fs.set_hidden("P/R_사용/Working/Case", zone="WORKING")',
                 'fs.rename_no_replace("P/R_사용/Working/Case", "P/R_사용/Working/Other", zone="WORKING")'):
        with pytest.raises(StorageError) as error:
            _as_structure_module(code, fs=fs)
        assert error.value.code == "NOT_ALLOWED_WRITE", code
    _as_structure_module('fs.mkdir_pinned("P/R_사용/Working/Case/New", zone="WORKING")', fs=fs)
    assert (tmp_path / "P/R_사용/Working/Case/New").is_dir()
    assert not (tmp_path / "P/R_사용/Working/Case/a.txt").exists() and (tmp_path / "P/R_사용/Working/Case").is_dir()
    # SKELETON: the provider enforces the declared depth (request folder at depth 2, Working below it).
    with pytest.raises(StorageError):
        _as_structure_module('fs.mkdir_pinned("P/S_사용", zone="SKELETON")', fs=fs)
    with skeleton_request_depth(3), pytest.raises(StorageError):
        _as_structure_module('fs.mkdir_pinned("P/S_사용", zone="SKELETON")', fs=fs)
    with skeleton_request_depth(2):
        _as_structure_module('fs.mkdir_pinned("P/S_사용", zone="SKELETON")', fs=fs)
    assert (tmp_path / "P/S_사용").is_dir()


# --- review M1: WR key of the chosen folder -----------------------------------------------------------

def _add_request(project_id, request_id, title, note=None):
    from datetime import datetime
    with connect() as conn:
        conn.execute("INSERT INTO analysis_requests (id,project_id,title,overall_note,status,requested_at) VALUES (?,?,?,?,?,?)",
                     [request_id, project_id, title, note, "OPEN", datetime.utcnow()])


def test_folder_with_another_wr_key_needs_an_explicit_confirmation(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    foreign = f"{PROJECT}/[WR-0009]_[사용_환경]"
    (root / foreign).mkdir()
    _, body = _overview(client, project_id, request_id)
    item = next(item for item in body["candidates"] if item["relative_path"] == foreign)
    assert item["wr_match"] is False and item["wr_key"] == "0009" and item["wr_other_request"] is False
    requests = _request_count()
    refused = _create(client, project_id, request_id, "USAGE", request_relative_path=foreign, case_names=["C1"], confirm=True)
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "RESULT_STRUCTURE_OTHER_WR" and detail["warnings"] and detail["folder_wr_key"] == "0009"
    assert not (root / foreign / "Working").exists()
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM folder_link_reservations").fetchone()[0] == 0
    confirmed = _create(client, project_id, request_id, "USAGE", request_relative_path=foreign, case_names=["C1"],
                        confirm=True, confirm_other_wr=True)
    assert confirmed.status_code == 200, confirmed.text
    assert (root / foreign / "Working/C1").is_dir() and confirmed.json()["link"]["status"] == "LINKED"
    assert _request_count() == requests


def test_folder_whose_wr_key_belongs_to_another_request_is_always_refused(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    _add_request(project_id, "req-wr-title", "[WR-0002] 다른 의뢰")
    _add_request(project_id, "req-wr-note", "제목에 번호 없음", "폴더 의뢰번호: WR_0003")
    for key in ("0002", "0003"):
        folder = f"{PROJECT}/[WR-{key}]_[사용_환경]"
        (root / folder).mkdir()
        _, body = _overview(client, project_id, request_id)
        assert next(item for item in body["candidates"] if item["relative_path"] == folder)["wr_other_request"] is True
        response = _create(client, project_id, request_id, "USAGE", request_relative_path=folder, case_names=["C1"],
                           confirm=True, confirm_other_wr=True)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_PATH_OWNERSHIP_CONFLICT", response.text
        assert not (root / folder / "Working").exists()


def test_spdm_request_parent_of_another_request_is_owned_by_it(admin_client):
    from datetime import datetime
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    other = f"{PROJECT}/[WR-0001]_[사용_환경]"   # same WR key, but parented to request B in SPDM
    (root / other).mkdir()
    _add_request(project_id, "req-B", "B")
    now = datetime.utcnow()
    with connect() as conn:
        conn.execute("INSERT INTO spdm_storage_request_parents (request_folder, project_folder, project_id, request_id, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?)", [other, PROJECT, project_id, "req-B", now, now])
    environments, body = _overview(client, project_id, request_id)
    assert next(item for item in body["candidates"] if item["relative_path"] == other)["owner"] == "OTHER"
    assert environments["USAGE"]["status"] == "MISSING" and environments["USAGE"]["choices"] == []
    response = _create(client, project_id, request_id, "USAGE", request_relative_path=other, case_names=["C1"],
                       confirm=True, confirm_other_wr=True)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_PATH_OWNERSHIP_CONFLICT", response.text
    assert not (root / other / "Working").exists()
    from app.services import result_registration_paths as paths
    with connect() as conn:
        root_ctx = paths.storage_context(conn)
        assert paths._has_owner_conflict(conn, root_ctx[1], root_ctx[2], f"{other}/Working/C1", project_id, request_id, "USAGE")
        assert not paths._has_owner_conflict(conn, root_ctx[1], root_ctx[2], f"{other}/Working/C1", project_id, "req-B", "USAGE")


# --- review M3/L1/L2: persistent link reservations -----------------------------------------------------

def test_reservation_is_stored_in_the_database_and_survives_a_restart(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    proposal = _overview(client, project_id, request_id)[0]["USAGE"]["proposal"]
    requests = _request_count()
    monkeypatch.setenv("SIMDASH_AUTO_DISCOVERY", "0")      # the link waits, as with another worker or a restart
    response = _create(client, project_id, request_id, "USAGE",
                       new_request_folder={"parent_relative_path": proposal["parent_relative_path"], "name": proposal["name"]},
                       case_names=["Assy_Case1"])
    assert response.status_code == 200, response.text
    assert response.json()["link"]["status"] == "PENDING"
    with connect() as conn:
        rows = conn.execute("SELECT path_key, project_id, request_id, environment, created_by, expires_at>created_at "
                            "FROM folder_link_reservations").fetchall()
    assert [row[:4] for row in rows] == [(USAGE_FOLDER.casefold(), project_id, request_id, "USAGE")] and rows[0][5]
    folder_auto_discovery.reset_for_tests()                 # process memory gone ("restart")
    monkeypatch.setenv("SIMDASH_AUTO_DISCOVERY", "1")
    found = folder_auto_discovery.discover(force=True)
    assert any(item.get("linked") for item in found["created_requests"]), found
    assert _request_count() == requests
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM folder_link_reservations").fetchone()[0] == 0
    assert _overview(client, project_id, request_id)[0]["USAGE"]["status"] == "LINKED"


def test_unexpired_reservation_of_another_request_is_refused_and_expired_ones_are_replaced(admin_client):
    from datetime import datetime, timedelta
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    proposal = _overview(client, project_id, request_id)[0]["USAGE"]["proposal"]
    with connect() as conn:
        root_key = result_folder_structure.paths.storage_context(conn)[2]
        folder_auto_discovery.reserve_link(conn, root_key, USAGE_FOLDER, project_id, "req-other", "USAGE", created_by="u2")
        with pytest.raises(folder_auto_discovery.LinkReserved):
            folder_auto_discovery.reserve_link(conn, root_key, USAGE_FOLDER.upper(), project_id, request_id, "USAGE",
                                               created_by="u1")
    response = _create(client, project_id, request_id, "USAGE",
                       new_request_folder={"parent_relative_path": proposal["parent_relative_path"], "name": proposal["name"]},
                       case_names=["Assy_Case1"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_STRUCTURE_RESERVED", response.text
    assert not (root / USAGE_FOLDER).exists()
    with connect() as conn:
        conn.execute("UPDATE folder_link_reservations SET expires_at=?", [datetime.utcnow() - timedelta(seconds=1)])
    again = _create(client, project_id, request_id, "USAGE",
                    new_request_folder={"parent_relative_path": proposal["parent_relative_path"], "name": proposal["name"]},
                    case_names=["Assy_Case1"])
    assert again.status_code == 200, again.text
    assert again.json()["link"]["status"] == "LINKED"


def test_failed_local_creation_drops_the_reservation(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    proposal = _overview(client, project_id, request_id)[0]["USAGE"]["proposal"]
    original = storage_local.LocalFsProvider.mkdir_pinned

    def failing(self, rel_path, *, zone):
        if rel_path.endswith("/Assy_Case2"):
            raise OSError("disk full")
        return original(self, rel_path, zone=zone)

    monkeypatch.setattr(storage_local.LocalFsProvider, "mkdir_pinned", failing)
    response = _create(client, project_id, request_id, "USAGE",
                       new_request_folder={"parent_relative_path": proposal["parent_relative_path"], "name": proposal["name"]},
                       case_names=["Assy_Case1", "Assy_Case2"])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_STRUCTURE_FAILED", response.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM folder_link_reservations").fetchone()[0] == 0


def test_failed_or_cancelled_drive_batch_drops_the_reservation(admin_client, monkeypatch):
    from datetime import datetime
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setattr(result_folder_structure, "_kick_discovery", lambda: None)
    now = datetime.utcnow()
    with connect() as conn:
        root_key = result_folder_structure.paths.storage_context(conn)[2]
    for index, (state, kept) in enumerate((("DONE", True), ("PARTIAL", True), ("FAILED", False), ("CANCELLED", False),
                                           ("CONFLICT", False))):
        batch = f"batch-{index}"
        with connect() as conn:
            conn.execute("DELETE FROM folder_link_reservations")
            folder_auto_discovery.reserve_link(conn, root_key, USAGE_FOLDER, project_id, request_id, "USAGE", created_by="u1")
            conn.execute("INSERT INTO drive_upload_queue (id, batch_id, seq, kind, root_key, dst_rel_dir, state, halt_on_error, "
                         "attempts, requested_by, origin, project_id, request_id, environment, created_at, updated_at) "
                         "VALUES (?,?,0,'MKDIR',?,?,?,TRUE,0,'u1',?,?,?,'USAGE',?,?)",
                         [f"item-{index}", batch, root_key, f"{USAGE_FOLDER}/Working", state if state != "PARTIAL" else "DONE",
                          result_folder_structure.ORIGIN, project_id, request_id, now, now])
        result_folder_structure.on_drive_batch_finished(result_folder_structure.ORIGIN, batch, {"state": state})
        with connect() as conn:
            count = conn.execute("SELECT count(*) FROM folder_link_reservations").fetchone()[0]
        assert count == (1 if kept else 0), state
    assert result_folder_structure._request_of(f"{USAGE_FOLDER}/Working/Case") == USAGE_FOLDER
    assert result_folder_structure._request_of(USAGE_FOLDER) == USAGE_FOLDER


def test_project_cleanup_removes_reservations_of_the_deleted_project(admin_client):
    from datetime import datetime
    from app.services import project_cleanup
    with connect() as conn:
        conn.execute("INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
                     ["project-rsv", "예약 정리", "", "", datetime.utcnow()])
        conn.execute("INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at) VALUES(?,?,?,?,?,?)",
                     ["request-rsv", "project-rsv", "의뢰", "IN_PROGRESS", "담당", datetime.utcnow()])
        folder_auto_discovery.reserve_link(conn, "rk", "P/[WR-1]_[사용]", "project-rsv", "request-rsv", "USAGE", created_by="u1")
        folder_auto_discovery.reserve_link(conn, "rk", "P/[WR-9]_[사용]", "project-other", "r", "USAGE", created_by="u1")
        plan = project_cleanup.preview(conn, ["project-rsv"])
        assert plan["totals"].get("folder_link_reservations") == 1
        project_cleanup.delete(conn, ["project-rsv"], plan["confirm_token"])
        assert conn.execute("SELECT project_id FROM folder_link_reservations").fetchall() == [("project-other",)]


# --- review L5: path length ----------------------------------------------------------------------------

def test_request_folder_path_beyond_the_windows_limit_is_refused(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    proposal = _overview(client, project_id, request_id)[0]["USAGE"]["proposal"]
    monkeypatch.setattr(result_drop_upload, "MAX_PATH_CHARS", len(str(root / USAGE_FOLDER)) + 3)  # Working does not fit
    response = _create(client, project_id, request_id, "USAGE",
                       new_request_folder={"parent_relative_path": proposal["parent_relative_path"], "name": proposal["name"]},
                       case_names=[])
    assert response.status_code == 422 and response.json()["detail"]["code"] == "RESULT_STRUCTURE_PATH_TOO_LONG", response.text
    assert not (root / USAGE_FOLDER).exists()
