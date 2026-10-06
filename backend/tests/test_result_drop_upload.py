"""W8 drag & drop result upload into the request's Working tree (synthetic data, isolated temp root)."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime

import pytest

from app.database_connection import connect
from app.services import folder_auto_sync, folder_discovery_scan, result_drop_upload
from app.services.storage import local as storage_local
from tests.test_new_scene_registration import CSV, CSV_BYTES, OPTION, REQUEST, _seed, admin_client  # noqa: F401

pytestmark = pytest.mark.duckdb_integration
REG = "/api/result-registration"
RUN = OPTION.rsplit("/", 1)[0]
WORKING = f"{REQUEST}/Working"
STAGING = f"{WORKING}/.simdash-upload"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    folder_auto_sync.reset_for_tests()
    result_drop_upload.reset_for_tests()
    monkeypatch.setattr(folder_auto_sync, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_sync, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(result_drop_upload, "DISK_MARGIN_MIN_BYTES", 1024)
    monkeypatch.setattr(result_drop_upload, "RENAME_RETRY_DELAYS", ())
    yield
    folder_auto_sync.reset_for_tests()
    result_drop_upload.reset_for_tests()


def _body(project_id, request_id, target, files, folders=None):
    return {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "target_relative_path": target,
            "files": [{"relative_path": path, "size": len(data)} for path, data in files],
            "folders": folders or []}


def _start(client, body):
    response = client.post(REG + "/drop-uploads", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _put(client, session_id, index, offset, data, **headers):
    return client.put(f"{REG}/drop-uploads/{session_id}/files/{index}", params={"offset": offset},
                      content=data, headers={"Content-Type": "application/octet-stream", **headers})


def _upload_all(client, session, files):
    contents = dict(files)
    for item in session["files"]:
        data = contents[item["relative_path"]]
        response = _put(client, session["session_id"], item["index"], 0, data)
        assert response.status_code == 200, response.text
        assert response.json()["complete"] is True


def _complete(client, session_id):
    response = client.post(f"{REG}/drop-uploads/{session_id}/complete")
    assert response.status_code == 200, response.text
    return response.json()


def _scenes(client, project_id, request_id):
    response = client.get("/api/dashboard/catalog", params={"project_id": project_id, "request_id": request_id,
                                                            "environment": "DISTRIBUTION"})
    assert response.status_code == 200
    return {str(scene.get("relative_path") or "").rsplit("/", 1)[-1] for scene in response.json()["scenes"]}


def _snapshot(root):
    return sorted((str(path.relative_to(root)), path.stat().st_size if path.is_file() else -1)
                  for path in root.rglob("*"))


def test_tree_lists_working_by_depth_with_display_paths(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setenv("SIMDASH_SPDM_DISPLAY_ROOT", r"\\fileserver\SPDM")
    response = client.get(REG + "/drop-target", params={"project_id": project_id, "request_id": request_id,
                                                         "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    tree = response.json()
    assert tree["working_relative_path"] == WORKING
    assert [level["role"] for level in tree["levels"]] == ["WORKING", "SIMULATION_CASE", "LOAD_CASE",
                                                            "EXECUTION_RUN", "RUN_OPTION", "SCENE"]
    roles = {node["relative_path"]: node["role"] for node in tree["nodes"]}
    assert roles[OPTION] == "RUN_OPTION" and roles[f"{OPTION}/2_Face"] == "SCENE"
    option = next(node for node in tree["nodes"] if node["relative_path"] == OPTION)
    assert option["display_path"] == "\\\\fileserver\\SPDM\\" + OPTION.replace("/", "\\")


def test_folder_upload_preserves_structure_and_becomes_scenes_after_sync(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    files = [("CUMULATIVE/7_Edge/" + CSV, CSV_BYTES), ("CUMULATIVE/7_Edge/sub/deck.rad", b"deck"),
             ("CUMULATIVE/8_Corner/" + CSV, CSV_BYTES), ("CUMULATIVE/8_Corner/run.bat", b"echo"),
             ("CUMULATIVE/empty.mp4", b"")]
    body = _body(project_id, request_id, RUN, files, folders=["CUMULATIVE/9_Empty"])
    plan = client.post(REG + "/drop-uploads/plan", json=body)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan["can_upload"] is True and plan["target_role"] == "EXECUTION_RUN"
    assert [item["relative_path"] for item in plan["skipped"]] == ["CUMULATIVE/8_Corner/run.bat"]
    assert plan["skipped"][0]["reason"] == "BLOCKED_EXTENSION"
    created = {item["client_path"]: item["role"] for item in plan["folders_to_create"]}
    assert created == {"CUMULATIVE": "RUN_OPTION", "CUMULATIVE/7_Edge": "SCENE", "CUMULATIVE/7_Edge/sub": "CONTENT",
                       "CUMULATIVE/8_Corner": "SCENE", "CUMULATIVE/9_Empty": "SCENE"}
    assert plan["file_count"] == 4 and plan["total_bytes"] == 2 * len(CSV_BYTES) + 4
    assert {item["code"] for item in plan["issues"]} == {"FILE_ABOVE_SCENE"}  # empty.mp4 directly in the Run Option

    session = _start(client, body)
    _upload_all(client, session, [item for item in files if not item[0].endswith(".bat")])
    # The scanner never sees the staging folder before publishing.
    scanned = folder_discovery_scan.scan(root, REQUEST)
    assert not any(".simdash-upload" in node["relative_path"] for node in scanned["nodes"])
    assert not any(".simdash-upload" in item["relative_path"] for item in scanned["file_state"])
    result = _complete(client, session["session_id"])
    assert result["state"] == "PUBLISHED" and result["published_files"] == 4
    assert (root / RUN / "CUMULATIVE/7_Edge" / CSV).read_bytes() == CSV_BYTES
    assert (root / RUN / "CUMULATIVE/7_Edge/sub/deck.rad").read_bytes() == b"deck"
    assert (root / RUN / "CUMULATIVE/9_Empty").is_dir()
    assert (root / RUN / "CUMULATIVE/empty.mp4").stat().st_size == 0
    assert not (root / RUN / "CUMULATIVE/8_Corner/run.bat").exists()
    assert not (root / STAGING).exists()
    assert result["sync"]["status"] == "REFRESHED"
    assert [case["case_name"] for case in result["cases"]] == [OPTION.split("/")[3]]
    assert result["cases"][0]["case_id"]
    assert {"7_Edge", "8_Corner", "2_Face"} <= _scenes(client, project_id, request_id)
    with connect() as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM audit_events WHERE action LIKE 'RESULT_DROP_%' ORDER BY occurred_at").fetchall()]
    assert actions == ["RESULT_DROP_UPLOAD_STARTED", "RESULT_DROP_UPLOAD_PUBLISHED"]


def test_wrong_depth_drops_are_blocked(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    # INDIVIDUAL/INDIVIDUAL/2_Face: a Run Option folder dropped onto the Run Option itself.
    repeated = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION,
                                                                   [("INDIVIDUAL/2_Face/" + CSV, CSV_BYTES)])).json()
    assert repeated["can_upload"] is False
    assert {"DEPTH_REPEATED_NAME", "DEPTH_ROLE_MISMATCH"} <= {item["code"] for item in repeated["issues"]}
    # Scene folders dropped directly under a Run Case become Run Options by depth.
    scenes = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, RUN,
                                                                 [("2_Face/" + CSV, CSV_BYTES)])).json()
    assert scenes["can_upload"] is False
    mismatch = next(item for item in scenes["issues"] if item["code"] == "DEPTH_ROLE_MISMATCH")
    assert mismatch["paths"] == ["2_Face"] and "Scene" in mismatch["message"]
    blocked = client.post(REG + "/drop-uploads", json=_body(project_id, request_id, RUN, [("2_Face/" + CSV, CSV_BYTES)]))
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "RESULT_DROP_PLAN_BLOCKED"
    # An unknown Scene-like name at the Run Option level is a warning only.
    new_like = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, RUN,
                                                                   [("5_Side/" + CSV, CSV_BYTES)])).json()
    assert new_like["can_upload"] is True
    assert [item["code"] for item in new_like["issues"]] == ["DEPTH_SCENE_LIKE", "FILE_ABOVE_SCENE"]
    assert not (root / STAGING).exists()


def test_existing_file_conflict_is_refused_before_any_write(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    before = _snapshot(root)
    files = [("2_Face/" + CSV, b"other"), ("4_New/" + CSV, CSV_BYTES)]
    plan = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION, files)).json()
    assert plan["can_upload"] is False
    assert plan["conflicts"] == [{"relative_path": "2_Face/" + CSV, "destination_relative_path": f"{OPTION}/2_Face/{CSV}",
                                  "reason": "EXISTS"}]
    response = client.post(REG + "/drop-uploads", json=_body(project_id, request_id, OPTION, files))
    assert response.status_code == 409
    assert response.json()["detail"]["plan"]["conflicts"][0]["reason"] == "EXISTS"
    assert _snapshot(root) == before


def test_conflict_appearing_during_upload_publishes_nothing(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    files = [("4_New/a.csv", CSV_BYTES), ("4_New/b.csv", CSV_BYTES)]
    session = _start(client, _body(project_id, request_id, OPTION, files))
    _upload_all(client, session, files)
    (root / OPTION / "4_New").mkdir()
    (root / OPTION / "4_New" / "b.csv").write_bytes(b"copied by Explorer meanwhile")
    response = client.post(f"{REG}/drop-uploads/{session['session_id']}/complete")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_DROP_CONFLICT"
    assert not (root / OPTION / "4_New" / "a.csv").exists()
    assert (root / OPTION / "4_New" / "b.csv").read_bytes() == b"copied by Explorer meanwhile"
    aborted = client.delete(f"{REG}/drop-uploads/{session['session_id']}")
    assert aborted.status_code == 200
    assert not (root / STAGING).exists()


@pytest.mark.parametrize("path", ["../escape.csv", "a/../../b.csv", "/abs.csv", "C:/x.csv", "a\\b.csv",
                                  "CON/x.csv", "a/aux.txt", "a/b./c.csv", "a/ /c.csv", "a/b?.csv", "a//b.csv"])
def test_traversal_reserved_and_absolute_names_are_rejected(admin_client, path):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    response = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION, [(path, b"x")]))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "RESULT_DROP_PATH_INVALID"


def test_targets_outside_working_or_owned_by_another_request_are_rejected(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    (root / REQUEST / "Final").mkdir()
    (root / "other" / "Working").mkdir(parents=True)
    cases = {REQUEST: "RESULT_DROP_TARGET_OUTSIDE_REQUEST", f"{REQUEST}/Final": "RESULT_DROP_TARGET_OUTSIDE_WORKING",
             "other/Working": "RESULT_DROP_TARGET_OUTSIDE_REQUEST", f"{WORKING}/../x": "RESULT_PATH_INVALID"}
    for target, code in cases.items():
        response = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, target, [("a.csv", b"x")]))
        assert response.status_code == 422, (target, response.text)
        assert response.json()["detail"]["code"] == code, target
    from app.services import result_registration_paths

    with connect() as conn:
        root_key = result_registration_paths.storage_context(conn)[2]
        conn.execute("INSERT INTO result_registration_paths (id,root_key,project_id,request_id,environment,relative_path,"
                     "path_key,parent_relative_path,role_kind,target_id,raw_name,option_status,created_by,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ["foreign", root_key, "foreign-project", "foreign-request", "DISTRIBUTION", f"{OPTION}/3_Face",
                      f"{OPTION}/3_Face".casefold(), OPTION, "SCENE", "foreign-scene", "3_Face", None, "test",
                      datetime(2026, 1, 1)])
    response = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION, [("a/x.csv", b"x")]))
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_PATH_OWNERSHIP_CONFLICT"


def test_chunk_resume_offsets_and_hash_mismatch(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setattr(result_drop_upload, "CHUNK_BYTES", 4)
    data = b"0123456789"
    body = _body(project_id, request_id, f"{OPTION}/2_Face", [("big.bin", data)])
    body["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
    session = _start(client, body)
    sid = session["session_id"]
    assert session["chunk_bytes"] == 4
    assert _put(client, sid, 0, 0, data[:4]).json()["received"] == 4
    # Too large, wrong offset, corrupted chunk: nothing is written.
    assert _put(client, sid, 0, 4, data[4:9]).status_code == 413
    gap = _put(client, sid, 0, 8, data[8:])
    assert gap.status_code == 409 and gap.json()["detail"] == {**gap.json()["detail"], "code": "RESULT_DROP_OFFSET_MISMATCH",
                                                               "expected_offset": 4}
    bad = _put(client, sid, 0, 4, data[4:8], **{"X-Chunk-SHA256": hashlib.sha256(b"nope").hexdigest()})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "RESULT_DROP_CHUNK_HASH_MISMATCH"
    # Resume: the client asks where to continue.
    state = client.get(f"{REG}/drop-uploads/{sid}").json()
    assert state["files"][0]["received"] == 4 and state["received_bytes"] == 4
    assert _put(client, sid, 0, 4, data[4:8], **{"X-Chunk-SHA256": hashlib.sha256(data[4:8]).hexdigest()}).status_code == 200
    incomplete = client.post(f"{REG}/drop-uploads/{sid}/complete")
    assert incomplete.status_code == 409 and incomplete.json()["detail"]["code"] == "RESULT_DROP_INCOMPLETE"
    last = _put(client, sid, 0, 8, data[8:]).json()
    assert last["complete"] and last["sha256"] == hashlib.sha256(data).hexdigest()
    assert _complete(client, sid)["state"] == "PUBLISHED"
    assert (root / OPTION / "2_Face" / "big.bin").read_bytes() == data

    # A whole-file hash mismatch discards the staged file and restarts it from 0.
    body = _body(project_id, request_id, f"{OPTION}/2_Face", [("other.bin", b"abcd")])
    body["files"][0]["sha256"] = hashlib.sha256(b"abcX").hexdigest()
    sid = _start(client, body)["session_id"]
    mismatch = _put(client, sid, 0, 0, b"abcd")
    assert mismatch.status_code == 422 and mismatch.json()["detail"]["code"] == "RESULT_DROP_HASH_MISMATCH"
    assert client.get(f"{REG}/drop-uploads/{sid}").json()["files"][0]["received"] == 0
    assert not (root / STAGING / sid / "0.part").exists()


def test_staging_is_invisible_to_scan_and_auto_sync(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    sync = {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"}
    assert client.post("/api/folder-discovery/environments/sync", json=sync).status_code == 200
    quiet = client.post("/api/folder-discovery/environments/sync", json=sync).json()
    assert (quiet["status"], quiet["check_mode"]) == ("UNCHANGED", "QUICK")
    session = _start(client, _body(project_id, request_id, OPTION, [("6_New/" + CSV, CSV_BYTES)]))
    assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 200
    assert (root / STAGING / session["session_id"] / "0.part").exists()
    after = client.post("/api/folder-discovery/environments/sync", json=sync).json()
    assert (after["status"], after["check_mode"]) == ("UNCHANGED", "QUICK"), after
    tree = client.get(REG + "/drop-target", params=sync).json()
    assert not any(".simdash" in node["relative_path"] for node in tree["nodes"])
    assert all(entry["name"] != ".simdash-upload" for entry in folder_discovery_scan.browse(root, WORKING))


def test_symlink_in_target_chain_is_refused(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    outside = root.parent / "outside"
    outside.mkdir()
    os.symlink(outside, root / OPTION / "linked", target_is_directory=True)
    response = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, f"{OPTION}/linked",
                                                                   [("a.csv", b"x")]))
    assert response.status_code in {409, 422}, response.text
    assert response.json()["detail"]["code"] in {"SPDM_PATH_UNSAFE", "SPDM_FOLDER_UNAVAILABLE"}
    # A link inside the dropped structure's destination is refused too.
    os.symlink(outside, root / OPTION / "2_Face" / "sub", target_is_directory=True)
    plan = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, f"{OPTION}/2_Face",
                                                               [("sub/a.csv", b"x")])).json()
    assert plan["can_upload"] is False and "PATH_UNSAFE" in {item["code"] for item in plan["issues"]}
    assert list(outside.iterdir()) == []


def test_only_blocked_or_hidden_files_is_rejected_and_reported(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    response = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, f"{OPTION}/2_Face",
                                                                   [("tool.exe", b"MZ"), ("Thumbs.db", b"x"),
                                                                    (".git/config", b"x"), ("~$deck.pptx", b"x")]))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "RESULT_DROP_EMPTY"
    assert {item["relative_path"]: item["reason"] for item in detail["skipped"]} == {
        "tool.exe": "BLOCKED_EXTENSION", "Thumbs.db": "SYSTEM_FILE", ".git/config": "HIDDEN_FOLDER",
        "~$deck.pptx": "SYSTEM_FILE"}


def test_permission_is_required(admin_client, monkeypatch):
    from fastapi import HTTPException
    from app.routers import result_registration as router

    client, root = admin_client
    project_id, request_id = _seed(client, root)
    session = _start(client, _body(project_id, request_id, OPTION, [("6_New/" + CSV, CSV_BYTES)]))
    seen = []

    def denied(request, permission, kind, resource_id, **kwargs):
        seen.append((permission, kind, resource_id))
        raise HTTPException(403, detail={"code": "RESULT_IMPORT_DENIED"})

    monkeypatch.setattr(router, "require_resource_permission", denied)
    responses = [
        client.get(REG + "/drop-target", params={"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"}),
        client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION, [("a.csv", b"x")])),
        client.post(REG + "/drop-uploads", json=_body(project_id, request_id, OPTION, [("a.csv", b"x")])),
        _put(client, session["session_id"], 0, 0, CSV_BYTES),
        client.post(f"{REG}/drop-uploads/{session['session_id']}/complete"),
        client.delete(f"{REG}/drop-uploads/{session['session_id']}"),
        client.post(REG + "/drop-target/folders", json={"project_id": project_id, "request_id": request_id,
                                                         "environment": "DISTRIBUTION", "parent_relative_path": OPTION,
                                                         "name": "6_New"}),
        client.get(REG + "/drafts", params={"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"}),
    ]
    assert [response.status_code for response in responses] == [403] * len(responses)
    assert {item for item in seen} == {("result.import", "request", request_id)}
    assert not (root / STAGING / session["session_id"] / "0.part").exists()


def test_free_space_shortage_is_refused_before_any_write(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setattr(storage_local.LocalFsProvider, "free_bytes", lambda self, rel: 10)
    body = _body(project_id, request_id, OPTION, [("6_New/" + CSV, CSV_BYTES)])
    plan = client.post(REG + "/drop-uploads/plan", json=body).json()
    assert plan["can_upload"] is False and plan["issues"][0]["code"] == "FREE_SPACE"
    response = client.post(REG + "/drop-uploads", json=body)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "RESULT_DROP_PLAN_BLOCKED"
    assert not (root / STAGING).exists()


def test_concurrency_limit_and_abort_removes_only_own_staging(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    first = _start(client, _body(project_id, request_id, OPTION, [("6_A/" + CSV, CSV_BYTES)]))
    second = _start(client, _body(project_id, request_id, OPTION, [("6_B/" + CSV, CSV_BYTES)]))
    third = client.post(REG + "/drop-uploads", json=_body(project_id, request_id, OPTION, [("6_C/" + CSV, CSV_BYTES)]))
    assert third.status_code == 429 and third.json()["detail"]["code"] == "RESULT_DROP_BUSY"
    for session in (first, second):
        assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 200
    foreign = root / STAGING / first["session_id"] / "keep.txt"
    foreign.write_bytes(b"not ours")
    assert client.delete(f"{REG}/drop-uploads/{first['session_id']}").status_code == 200
    assert foreign.exists() and not (root / STAGING / first["session_id"] / "0.part").exists()
    assert (root / STAGING / second["session_id"] / "0.part").exists()
    assert client.get(f"{REG}/drop-uploads/{first['session_id']}").status_code == 404
    assert _complete(client, second["session_id"])["state"] == "PUBLISHED"
    assert (root / OPTION / "6_B" / CSV).exists() and not (root / OPTION / "6_A").exists()


def test_new_folder_is_validated_by_depth_and_names(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)

    def create(parent, name, confirm=False):
        return client.post(REG + "/drop-target/folders", json={
            "project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION",
            "parent_relative_path": parent, "name": name, "confirm": confirm})

    assert create(OPTION, "CON").json()["detail"]["code"] == "RESULT_DROP_FOLDER_NAME_INVALID"
    assert create(OPTION, "a:b").status_code == 422
    assert create(f"{OPTION}/2_Face", "inner").json()["detail"]["code"] == "RESULT_DROP_FOLDER_LEVEL_INVALID"
    assert create(RUN, "2_Face").json()["detail"]["code"] == "RESULT_DROP_FOLDER_DEPTH_INVALID"
    assert create(OPTION, "2_face").json()["detail"]["code"] == "RESULT_DROP_FOLDER_EXISTS"
    assert create(OPTION, "2-Face").json()["detail"]["code"] == "RESULT_DROP_FOLDER_NAME_DUPLICATE"
    warned = create(OPTION, "3_Face2")
    assert warned.status_code == 409 and warned.json()["detail"]["code"] == "RESULT_DROP_FOLDER_NAME_WARNING"
    assert not (root / OPTION / "3_Face2").exists()
    created = create(OPTION, "4_Edge")
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "SCENE" and (root / OPTION / "4_Edge").is_dir()
    assert "4_Edge" in _scenes(client, project_id, request_id)


def test_legacy_drafts_are_listed_read_only(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    response = client.get(REG + "/drafts", params={"project_id": project_id, "request_id": request_id,
                                                    "environment": "DISTRIBUTION"})
    assert response.status_code == 200
    assert response.json()["drafts"] == []


# ---------------------------------------------------------------------------
# Independent review fixes (M1–M3, L2–L5); attack probes ported from the review.
# ---------------------------------------------------------------------------

def test_review_m1_blocked_extensions_include_dot_leading_and_shell_formats(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    blocked = [".bat", ".exe", "evil.scf", "evil.url", "x.library-ms", "x.searchConnector-ms", "x.msc", "x.iso",
               "x.img", "x.vhd", "x.vhdx", "x.appref-ms", "x.settingcontent-ms", "x.application", "x.ps1xml", "x.wsc",
               "x.sct", "x.chm", "x.EXE", "x.inf", "x.xll", "x.hta", "x.cpl", "x.reg", "x.lnk", ".simdash-upload",
               "x.msix", "x.appx", "x.ms-appinstaller", "x.diagcab", "x.website", "x.mht", "x.cab", "x.psd1"]
    allowed = ["x.exe.txt", "result.csv", ".hidden_note.txt"]
    plan = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, f"{OPTION}/2_Face",
                                                               [(name, b"x") for name in blocked + allowed])).json()
    assert sorted(item["relative_path"] for item in plan["files"]) == sorted(allowed)
    assert {item["relative_path"] for item in plan["skipped"]} == set(blocked)
    for reserved in ["COM¹.txt", "LPT²", "com³/a.csv", "CON.txt"]:
        response = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION, [(reserved, b"x")]))
        assert response.status_code == 422 and response.json()["detail"]["code"] == "RESULT_DROP_PATH_INVALID", reserved
    assert result_drop_upload.name_problem("COM¹") == "RESERVED"
    assert result_drop_upload.extension_of(".bat") == ".bat" and result_drop_upload.extension_of("README") == ""


def test_review_foreign_user_cannot_use_a_session_but_admin_can_abort(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    session = _start(client, _body(project_id, request_id, OPTION, [("6_N/" + CSV, CSV_BYTES)]))
    sid = session["session_id"]
    for call in (lambda: result_drop_upload.upload_chunk(sid, "intruder", 0, 0, CSV_BYTES),
                 lambda: result_drop_upload.read_session(sid, "intruder"),
                 lambda: result_drop_upload.abort(sid, "intruder")):
        with pytest.raises(result_drop_upload.DropUploadError) as error:
            call()
        assert error.value.status == 404
    assert _put(client, sid, 0, 0, CSV_BYTES).status_code == 200
    assert result_drop_upload.abort(sid, "some-global-admin", admin=True)["state"] == "ABORTED"
    assert not (root / STAGING / sid).exists()


def test_review_link_swaps_before_complete_never_write_outside(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    outside = root.parent / "outside_swap"
    outside.mkdir()
    session = _start(client, _body(project_id, request_id, OPTION, [("6_N/sub/" + CSV, CSV_BYTES)]))
    assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 200
    os.symlink(outside, root / OPTION / "6_N", target_is_directory=True)
    assert client.post(f"{REG}/drop-uploads/{session['session_id']}/complete").status_code == 409
    assert list(outside.iterdir()) == []

    outside2 = root.parent / "outside_swap2"
    outside2.mkdir()
    session = _start(client, _body(project_id, request_id, OPTION, [("2_Face/new.csv", CSV_BYTES)]))
    assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 200
    face = root / OPTION / "2_Face"
    face.rename(root / OPTION / "2_Face_moved")
    os.symlink(outside2, face, target_is_directory=True)
    assert client.post(f"{REG}/drop-uploads/{session['session_id']}/complete").status_code >= 400
    assert list(outside2.iterdir()) == []


def test_review_staged_part_link_is_refused(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    victim = root.parent / "victim.txt"
    victim.write_bytes(b"")
    session = _start(client, _body(project_id, request_id, OPTION, [("6_N/" + CSV, CSV_BYTES)]))
    part = root / STAGING / session["session_id"] / "0.part"
    os.symlink(victim, part)
    assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 409
    part.unlink()
    os.link(victim, part)
    assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 409
    assert victim.read_bytes() == b""
    response = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, f"{STAGING}/{session['session_id']}",
                                                                   [("0.part", b"x")]))
    assert response.status_code == 422


def test_review_l2_working_zone_rule_and_refused_primitives(tmp_path):
    from app.services.storage.provider import StorageError, working_zone_allows

    cases = {"Working/x": False, "P/R/Working": False, "P/R/Working/x": True, "P/R/Final/C/Working/x": False,
             "P/R/WORKING/x": True, "P/R/Working/Final/x": True, "P/Working/x": False,
             "P/R/Final/../Working/x": False, "P/R/Working/a/Working/x": True, "C/P/R/Working/x/y": True,
             "P/R/Final/Working/x": False}
    assert {key: working_zone_allows(key) for key in cases} == cases
    fs = storage_local.LocalFsProvider(tmp_path)
    for call in (lambda: fs.mkdirs("P/R/Working/x", zone="WORKING"),
                 lambda: fs.create_exclusive("P/R/Working/x", b"", zone="WORKING"),
                 lambda: fs.replace("P/R/Working/a", "P/R/Working/b", zone="WORKING"),
                 lambda: fs.move_no_overwrite("P/R/Working/a", "P/R/Working/b", zone="WORKING"),
                 lambda: fs.append_bytes("P/R/Working/a", b"", zone="WORKING"),
                 lambda: fs.copy_stream("P/R/Working/a", "P/R/Working/b", zone="WORKING"),
                 lambda: fs.lock("P/R/Working/a", zone="WORKING")):
        with pytest.raises(StorageError) as error:
            call()
        assert error.value.code == "NOT_ALLOWED_WRITE"


def test_review_m3_plan_and_complete_scale_with_large_folders(admin_client):
    import time

    client, root = admin_client
    project_id, request_id = _seed(client, root)
    face = root / OPTION / "2_Face"
    for index in range(3000):
        (face / f"e{index}.txt").write_bytes(b"")
    files = [(f"n{index}.txt", b"") for index in range(3000)]
    body = _body(project_id, request_id, f"{OPTION}/2_Face", files)
    started = time.perf_counter()
    plan = client.post(REG + "/drop-uploads/plan", json=body)
    elapsed = time.perf_counter() - started
    assert plan.status_code == 200 and plan.json()["file_count"] == 3000
    assert elapsed < 1.0, elapsed
    session = _start(client, body)
    started = time.perf_counter()
    result = _complete(client, session["session_id"])  # empty files: created at complete
    assert result["published_files"] == 3000
    assert time.perf_counter() - started < 10.0


def test_review_m2_partial_sessions_do_not_lock_the_request(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    real = storage_local.LocalFsProvider.rename_no_replace

    def busy(self, src, dst, *, zone):
        raise PermissionError("sharing violation")

    partial = []
    for name in ("6_A", "6_B"):
        session = _start(client, _body(project_id, request_id, OPTION, [(f"{name}/x/{CSV}", CSV_BYTES)]))
        assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 200
        monkeypatch.setattr(storage_local.LocalFsProvider, "rename_no_replace", busy)
        result = _complete(client, session["session_id"])
        monkeypatch.setattr(storage_local.LocalFsProvider, "rename_no_replace", real)
        assert (result["state"], result["busy"]) == ("PARTIAL", [f"{name}/x/{CSV}"])
        # N3: a partial publish keeps the folders (retry and requested empty folders); abort/expiry clean them.
        assert (root / OPTION / name / "x").is_dir() and len(result["created_folders"]) == 2
        partial.append(session["session_id"])
    # Same user: two open sessions is the per-user limit; another user can still upload.
    third = client.post(REG + "/drop-uploads", json=_body(project_id, request_id, OPTION, [("6_C/" + CSV, CSV_BYTES)]))
    assert third.status_code == 429 and third.json()["detail"]["code"] == "RESULT_DROP_BUSY"
    with connect() as conn:
        other, _plan = result_drop_upload.create_session(
            conn, project_id, request_id, "DISTRIBUTION", OPTION, [{"relative_path": "6_D/" + CSV, "size": 1}], [], "other-user")
    listed = client.get(REG + "/drop-uploads", params={"project_id": project_id, "request_id": request_id}).json()["sessions"]
    assert {item["session_id"] for item in listed} >= set(partial)  # the global admin also sees other-user's
    assert other["session_id"] in {item["session_id"] for item in listed}
    # Retrying a PARTIAL session publishes the rest.
    retried = _complete(client, partial[0])
    assert retried["state"] == "PUBLISHED" and (root / OPTION / "6_A/x" / CSV).exists()
    # Idle sessions expire (staging and empty created folders cleaned) and free the slot.
    monkeypatch.setattr(result_drop_upload, "SESSION_IDLE_SECONDS", -1)
    assert _start(client, _body(project_id, request_id, OPTION, [("6_E/" + CSV, CSV_BYTES)]))["state"] == "UPLOADING"
    assert not (root / STAGING / partial[1]).exists()
    assert not (root / OPTION / "6_B").exists()  # L5: expiry removed its still-empty folders
    assert client.get(f"{REG}/drop-uploads/{partial[1]}").status_code == 404


def test_review_l3_open_sessions_reserve_free_space(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setattr(result_drop_upload, "DISK_MARGIN_MIN_BYTES", 0)
    monkeypatch.setattr(result_drop_upload, "DISK_MARGIN_RATIO", 0.0)
    monkeypatch.setattr(storage_local.LocalFsProvider, "free_bytes", lambda self, rel: 150)
    _start(client, _body(project_id, request_id, OPTION, [("6_A/a.bin", b"x" * 100)]))
    plan = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION, [("6_B/b.bin", b"x" * 100)])).json()
    assert plan["reserved_bytes"] == 100 and plan["free_bytes"] == 50
    assert plan["can_upload"] is False and plan["issues"][0]["code"] == "FREE_SPACE"


def test_review_l4_refused_start_is_audited(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    response = client.post(REG + "/drop-uploads", json=_body(project_id, request_id, RUN, [("2_Face/" + CSV, CSV_BYTES)]))
    assert response.status_code == 409
    with connect() as conn:
        row = conn.execute("SELECT status_code, detail_json FROM audit_events WHERE action='RESULT_DROP_UPLOAD_REFUSED'").fetchone()
    assert row is not None and int(row[0]) == 409
    assert "RESULT_DROP_PLAN_BLOCKED" in str(row[1]) and "DEPTH_ROLE_MISMATCH" in str(row[1])


def test_rereview_n1_final_and_working_folders_inside_a_scene_are_content(admin_client):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    face = f"{OPTION}/2_Face"
    for name in ("final", "Working"):  # (not also FINAL: a case-insensitive Windows share cannot hold both)
        (root / face / name).mkdir()
    # An existing final/Working subfolder as the target, as an intermediate folder, and new ones.
    files = [("final/a.csv", b"a"), ("Working/b.csv", b"b"), ("Working/sub/c.csv", b"c"), ("new_final/Final/d.csv", b"d")]
    plan = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, face, files)).json()
    assert plan["can_upload"] is True, plan["issues"]
    session = _start(client, _body(project_id, request_id, face, files))
    _upload_all(client, session, files)
    assert _complete(client, session["session_id"])["state"] == "PUBLISHED"
    assert (root / face / "final/a.csv").read_bytes() == b"a" and (root / face / "new_final/Final/d.csv").exists()
    target = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, f"{face}/final", [("e.csv", b"e")]))
    assert target.status_code == 200 and target.json()["can_upload"] is True
    # The request-level Final (and anything not under Working) is still refused at plan time.
    (root / REQUEST / "Final").mkdir()
    refused = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, f"{REQUEST}/Final", [("e.csv", b"e")]))
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "RESULT_DROP_TARGET_OUTSIDE_WORKING"


def test_rereview_n1_zone_violation_is_reported_at_plan_time(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setattr(result_drop_upload, "working_zone_allows", lambda rel: "blocked" not in rel)
    plan = client.post(REG + "/drop-uploads/plan", json=_body(project_id, request_id, OPTION, [("6_N/blocked.csv", b"x")])).json()
    assert plan["can_upload"] is False and "PATH_NOT_ALLOWED" in {item["code"] for item in plan["issues"]}


def test_rereview_n3_partial_keeps_requested_empty_folder_and_abort_cleans(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    real = storage_local.LocalFsProvider.rename_no_replace
    session = _start(client, _body(project_id, request_id, OPTION, [("6_A/" + CSV, CSV_BYTES)], folders=["6_Empty"]))
    assert _put(client, session["session_id"], 0, 0, CSV_BYTES).status_code == 200
    monkeypatch.setattr(storage_local.LocalFsProvider, "rename_no_replace",
                        lambda self, src, dst, *, zone: (_ for _ in ()).throw(PermissionError("sharing")))
    result = _complete(client, session["session_id"])
    monkeypatch.setattr(storage_local.LocalFsProvider, "rename_no_replace", real)
    assert result["state"] == "PARTIAL"
    assert (root / OPTION / "6_Empty").is_dir() and (root / OPTION / "6_A").is_dir()
    (root / OPTION / "6_A" / "user.txt").write_bytes(b"explorer")
    assert client.delete(f"{REG}/drop-uploads/{session['session_id']}").status_code == 200
    assert not (root / OPTION / "6_Empty").exists()
    assert (root / OPTION / "6_A" / "user.txt").read_bytes() == b"explorer"


def test_rereview_free_space_is_rechecked_when_registering(admin_client, monkeypatch):
    client, root = admin_client
    project_id, request_id = _seed(client, root)
    monkeypatch.setattr(result_drop_upload, "DISK_MARGIN_MIN_BYTES", 0)
    monkeypatch.setattr(result_drop_upload, "DISK_MARGIN_RATIO", 0.0)
    free = iter([150, 40])  # plan sees enough, the re-check under the lock does not
    monkeypatch.setattr(storage_local.LocalFsProvider, "free_bytes", lambda self, rel: next(free))
    response = client.post(REG + "/drop-uploads", json=_body(project_id, request_id, OPTION, [("6_A/a.bin", b"x" * 100)]))
    assert response.status_code == 507 and response.json()["detail"]["code"] == "RESULT_DROP_FREE_SPACE"
    assert not any((root / STAGING).glob("*/*.part"))
