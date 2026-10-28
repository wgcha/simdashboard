"""Old folder spellings must not come back after deletes; 「폴더 등록 전체 초기화」 (2026-10-23).

Production (SCX drive, PostgreSQL 18): after 등록 이력 [전체 삭제] and 프로젝트 정리 [강제 삭제] the
[저장 폴더 새로고침] still showed ``PROJ-000099_R95J 개발 과제`` (the drive has ``PROJ-000099_R95J개발과제``),
stale ``…/Drop/85qn80h_ref_organized/INDIVIDUAL`` paths (the drive has ``completed_Drop``) and never
registered ``WR-00000800_유통환경시뮬레이션``.  In-memory fake drive and synthetic folders only.
"""
from __future__ import annotations

import uuid

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery, folder_registration_reset, project_cleanup, storage_rescan
from app.services.drive import reads as drive_reads
from app.services.storage.drive import DriveRoot
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_reads import CSV, ENV, scx  # noqa: F401  (fixture)

pytestmark = pytest.mark.duckdb_integration

CASE = "Package_Model_SetCase2_CushionCase2"
OLD_OPTION = "Drop/85qn80h_ref_organized/INDIVIDUAL"
NEW_OPTION = "completed_Drop/85qn80h_ref_organized/INDIVIDUAL"
RESET = ENV + "/registration-reset"
# Every table that stores a folder path or folder-derived identity (docs/contracts/depth-schema.md §16.8).
PATH_TABLES = tuple(table for stage in folder_registration_reset.WIPED_TABLES for table in stage) + (
    "folder_environment_scans", "folder_discovery_exclusions", "dashboard_cases", "dashboard_captures", "dashboard_assets",
    "dashboard_scene_summaries", "projects", "analysis_requests", "notifications")


@pytest.fixture(autouse=True)
def _discovery(monkeypatch):
    folder_auto_discovery.reset_for_tests()
    storage_rescan.reset_for_tests()
    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(storage_rescan, "INLINE", True)
    yield
    folder_auto_discovery.reset_for_tests()
    storage_rescan.reset_for_tests()


def _names(tag: str) -> tuple[str, str]:
    # Project names are shared by the test database: a unique number keeps PROJECT_NAME_EXISTS away.
    return f"PROJ-{tag}_R95J 개발 과제", f"PROJ-{tag}_R95J개발과제"


def _request_tree(scx, request: str, option: str = OLD_OPTION, scenes=("2_Face", "3_Face")) -> None:
    for scene in scenes:
        scx.add(f"{request}/Working/{CASE}/{option}/{scene}/{CSV}")
    scx.drive.add_dir(scx.path(f"{request}/Final"))


def _rename(scx, old: str, new: str) -> None:
    """Rename a folder on the fake drive (item ids and content are kept, as a drive rename does)."""
    old, new = scx.path(old), scx.path(new)
    for path in sorted(path for path in scx.drive.nodes if path == old or path.startswith(old + "/")):
        scx.drive.nodes[new + path[len(old):]] = scx.drive.nodes.pop(path)


def _discover(scx) -> dict:
    folder_auto_discovery.reset_for_tests()
    return scx.post(ENV + "/discover", {"force": True})


def _sync(scx, project_id: str, request_id: str) -> dict:
    return scx.post(ENV + "/sync", {"project_id": project_id, "request_id": request_id, "environment": "DISTRIBUTION"})


def _rescan(scx) -> dict:
    started = scx.client.post(ENV + "/rescan")
    assert started.status_code == 200, started.text
    state = scx.client.get(ENV + "/rescan").json()
    assert state["status"] == "DONE", state
    return state


def _rows_with(texts: tuple[str, ...], *, history: bool = True) -> list[str]:
    """``table: row`` of every row in a folder table whose text contains one of ``texts``
    (``history=False``: superseded drive source versions, the kept version history, are not looked at)."""
    found = []
    with connect() as conn:
        for table in PATH_TABLES:
            where = " WHERE superseded_at IS NULL" if table == "drive_source_versions" and not history else ""
            try:
                rows = conn.execute(f"SELECT * FROM {table}{where}").fetchall()
            except Exception:  # noqa: BLE001 - optional table absent
                continue
            for row in rows:
                text = " ".join(str(value) for value in row)
                if any(item in text for item in texts):
                    found.append(f"{table}: {text[:200]}")
    return found


def _root_listing(scx) -> list[str]:
    """Top folders as the dashboard sees them (drive listing + registered MISSING sources)."""
    from app.services import folder_discovery

    def body():
        with connect() as conn:
            root = folder_discovery.configured_root(conn)
        from app.services.storage import provider_for_root
        return sorted(entry.name for entry in provider_for_root(root).list(""))
    return drive_reads.run(body)


def _build_old_state(scx, tag: str) -> dict:
    """The old state: requests registered under the spaced project folder, a Scene removed (MISSING source),
    then the project folder respelled, ``Drop`` renamed and a new request added -- on the drive only."""
    old_project, new_project = _names(tag)
    _request_tree(scx, f"{old_project}/WR-00000806_유통환경시뮬레이션")
    first = _discover(scx)
    assert [item["name"] for item in first["created_projects"]] == [old_project], first
    (registered,) = first["created_requests"]
    project_id, request_id = registered["project_id"], registered["id"]
    assert _sync(scx, project_id, request_id)["status"] in {"REFRESHED", "UNCHANGED"}
    scx.drive.delete(scx.path(f"{old_project}/WR-00000806_유통환경시뮬레이션/Working/{CASE}/{OLD_OPTION}/3_Face"))
    assert _sync(scx, project_id, request_id)["drive"]["missing"] == 1
    _rename(scx, old_project, new_project)
    _rename(scx, f"{new_project}/WR-00000806_유통환경시뮬레이션/Working/{CASE}/Drop",
            f"{new_project}/WR-00000806_유통환경시뮬레이션/Working/{CASE}/completed_Drop")
    _request_tree(scx, f"{new_project}/WR-00000800_유통환경시뮬레이션", option=NEW_OPTION)
    return {"old": old_project, "new": new_project, "project_id": project_id, "request_id": request_id, "tag": tag}


def _assert_current_folders_only(scx, state: dict, rescan: dict) -> None:
    old, new = state["old"], state["new"]
    stale = (old, f"/{OLD_OPTION}")
    assert old not in _root_listing(scx) and new in _root_listing(scx)
    assert not [item for item in rescan["needs_review"] if old in str(item.get("relative_path"))], rescan["needs_review"]
    with connect() as conn:
        projects = [row[0] for row in conn.execute("SELECT name FROM projects WHERE name LIKE ?",
                                                   [f"PROJ-{state['tag']}%"]).fetchall()]
        requests = sorted(row[0] for row in conn.execute(
            "SELECT a.title FROM analysis_requests a JOIN projects p ON p.id=a.project_id WHERE p.name LIKE ?",
            [f"PROJ-{state['tag']}%"]).fetchall())
        live = [row[0] for row in conn.execute(
            "SELECT g.relative_path FROM folder_environment_registry g JOIN folder_environment_registrations r "
            "ON r.id=g.registration_id WHERE r.status<>'DELETED' AND g.role_kind IN ('PROJECT','REQUEST')").fetchall()]
    assert projects == [new], projects
    assert requests == ["WR-00000800_유통환경시뮬레이션", "WR-00000806_유통환경시뮬레이션"], requests
    assert live and all(path.startswith(new) for path in live if path.startswith(f"PROJ-{state['tag']}")), live
    # Normal deletes keep tombstones/scan history and the superseded drive version history (never read by
    # discovery or sync); the reset test checks that nothing at all is left.
    leftovers = [row for row in _rows_with(stale, history=False) if not row.startswith(("folder_environment_registrations:",
                                                                          "folder_environment_scans:",
                                                                          "folder_environment_previews:"))]
    assert leftovers == [], leftovers


def test_normal_deletes_leave_no_path_state_that_a_rescan_brings_back(scx):
    """Root cause: MISSING drive source rows (and their old spelling) survived both deletes, and the drive
    provider showed their folders again (``storage.drive._children`` via ``drive.sources.load_overlay``)."""
    state = _build_old_state(scx, uuid.uuid4().hex[:6])
    with connect() as conn:
        registrations = [str(row[0]) for row in conn.execute(
            "SELECT id FROM folder_environment_registrations WHERE status<>'DELETED'").fetchall()]
    preview = scx.post(ENV + "/registrations/delete-preview", {"registration_ids": registrations})
    deleted = scx.client.post(ENV + "/registrations/delete", json={"registration_ids": registrations,
                                                                    "confirm_token": preview["confirm_token"]})
    assert deleted.status_code == 200, deleted.text
    with connect() as conn:
        # The registration delete settled the drive source rows of the deleted request: no MISSING row is
        # current and no row keeps the deleted ids.
        assert conn.execute("SELECT count(*) FROM drive_source_versions WHERE superseded_at IS NULL "
                            "AND source_state='MISSING'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM drive_source_versions WHERE request_id=?",
                            [state["request_id"]]).fetchone()[0] == 0
    with connect() as conn:
        leftover = [item["project_id"] for item in project_cleanup.candidates(conn)["items"]
                    if item["project_id"] == state["project_id"]]
    if leftover:   # the user's second step: 프로젝트 정리 with [등록된 프로젝트도 강제 삭제]
        preview = scx.post(ENV + "/project-cleanup/preview", {"project_ids": leftover, "force_project_ids": leftover})
        done = scx.client.post(ENV + "/project-cleanup/delete", json={
            "project_ids": leftover, "force_project_ids": leftover, "confirm_token": preview["confirm_token"],
            "acknowledge_data_project_ids": leftover})
        assert done.status_code == 200, done.text
    _assert_current_folders_only(scx, state, _rescan(scx))


def test_reset_removes_every_folder_row_and_a_rescan_registers_the_current_folders(scx):
    state = _build_old_state(scx, uuid.uuid4().hex[:6])
    old = state["old"]
    # Leftovers of earlier versions and deletes, as found on the production server: an orphan legacy registry
    # row and an orphan MISSING source row with the old spelling, a DELETED tombstone, an exclusion.
    with connect() as conn:
        root_key = DriveRoot(scx.root, "scx.example.test").root_key()
        conn.execute("INSERT INTO folder_discovery_registry(id,root_key,relative_path,role,role_kind,scope_key,code,name,"
                     "analysis_type,target_id,parent_target_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,now())",
                     [f"legacy-{uuid.uuid4().hex}", root_key, f"{old}/WR-00000800_유통환경시뮬레이션", "REQUEST", "REQUEST",
                      "s", "800", "WR-00000800", "", f"request-gone-{uuid.uuid4().hex}", None])
        conn.execute("INSERT INTO folder_discovery_exclusions(root_key,path_key,relative_path,excluded_by,excluded_at) "
                     "VALUES(?,?,?,?,now())", [root_key, "keep/me", "Keep/Me", "admin"])
    assert old in _root_listing(scx)   # the MISSING Scene of the old registration still shows its folder

    preview = scx.client.post(RESET + "/preview", json={"keep_exclusions": True})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["busy"] == [] and body["blockers"] == [] and body["confirm_text"] == "초기화"
    assert state["project_id"] in {item["project_id"] for item in body["projects"]}
    for table in ("folder_environment_registrations", "folder_environment_registry", "folder_environment_scans",
                  "drive_source_versions", "folder_discovery_registry", "dashboard_cases", "projects"):
        assert body["counts"].get(table, 0) >= 1, (table, body["counts"])
    assert "folder_discovery_exclusions" not in body["counts"] and body["exclusions"] == 1

    wrong = scx.client.post(RESET, json={"confirm_token": body["confirm_token"], "confirmation_text": "reset",
                                         "keep_exclusions": True})
    assert wrong.status_code == 409 and wrong.json()["detail"]["code"] == "FOLDER_RESET_CONFIRM_REQUIRED"
    stale = scx.client.post(RESET, json={"confirm_token": "0" * 64, "confirmation_text": "초기화", "keep_exclusions": True})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "DELETE_PREVIEW_STALE"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM folder_environment_registrations").fetchone()[0] >= 1   # untouched

    folder_auto_discovery._review_memo[("x", "y", "z")] = {"stamp": None, "at": 0.0, "review": {}}
    done = scx.client.post(RESET, json={"confirm_token": body["confirm_token"], "confirmation_text": " 초기화 ",
                                        "keep_exclusions": True})
    assert done.status_code == 200, done.text
    assert state["project_id"] in done.json()["deleted_projects"]
    assert folder_auto_discovery._review_memo == {}
    with connect() as conn:
        for table in [*(t for stage in folder_registration_reset.WIPED_TABLES for t in stage), "folder_environment_scans"]:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
        assert conn.execute("SELECT count(*) FROM folder_discovery_exclusions").fetchone()[0] == 1   # kept by choice
        assert conn.execute("SELECT count(*) FROM folder_environment_profiles").fetchone()[0] >= 1   # depth schema kept
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='FOLDER_REGISTRATION_RESET'").fetchone()[0] == 1
    assert _rows_with((old, f"/{OLD_OPTION}")) == []
    assert old not in _root_listing(scx)
    # Drive files were never touched.
    assert scx.drive.nodes.get(scx.path(f"{state['new']}/WR-00000806_유통환경시뮬레이션/Working/{CASE}/{NEW_OPTION}/2_Face/{CSV}"))

    rescan = _rescan(scx)
    _assert_current_folders_only(scx, state, rescan)
    with connect() as conn:
        paths = [row[0] for row in conn.execute("SELECT relative_path FROM folder_environment_registry").fetchall()]
    assert any(NEW_OPTION in path for path in paths), paths
    assert _rows_with((old, f"/{OLD_OPTION}")) == []


def test_reset_drops_exclusions_on_request_and_refuses_while_work_runs(scx):
    with connect() as conn:
        conn.execute("INSERT INTO folder_discovery_exclusions(root_key,path_key,relative_path,excluded_by,excluded_at) "
                     "VALUES(?,?,?,?,now())", ["r", "a/b", "A/B", "admin"])
    assert folder_auto_discovery._run_lock.acquire(blocking=False)
    try:
        busy = scx.client.post(RESET + "/preview", json={}).json()
        assert [item["code"] for item in busy["busy"]] == ["DISCOVERY_RUNNING"]
        refused = scx.client.post(RESET, json={"confirm_token": busy["confirm_token"], "confirmation_text": "초기화"})
        assert refused.status_code == 409 and refused.json()["detail"]["code"] == "FOLDER_RESET_BUSY"
    finally:
        folder_auto_discovery._run_lock.release()
    body = scx.client.post(RESET + "/preview", json={"keep_exclusions": False}).json()
    assert body["counts"]["folder_discovery_exclusions"] == 1
    done = scx.client.post(RESET, json={"confirm_token": body["confirm_token"], "confirmation_text": "초기화",
                                        "keep_exclusions": False})
    assert done.status_code == 200, done.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM folder_discovery_exclusions").fetchone()[0] == 0


def test_orphan_legacy_link_no_longer_hides_a_respelled_request_folder(scx):
    """An orphan legacy registry row (request gone) with the old spelling kept the new folder "linked"."""
    old_project, new_project = _names(uuid.uuid4().hex[:6])
    _request_tree(scx, f"{new_project}/WR-00000800_유통환경시뮬레이션")
    with connect() as conn:
        root_key = DriveRoot(scx.root, "scx.example.test").root_key()
        conn.execute("INSERT INTO folder_discovery_registry(id,root_key,relative_path,role,role_kind,scope_key,code,name,"
                     "analysis_type,target_id,parent_target_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,now())",
                     [f"legacy-{uuid.uuid4().hex}", root_key, f"{old_project}/WR-00000800_유통환경시뮬레이션", "REQUEST",
                      "REQUEST", "s", "800", "WR-00000800", "", f"request-gone-{uuid.uuid4().hex}", None])
    result = _discover(scx)
    assert [item["name"] for item in result["created_requests"]] == ["WR-00000800_유통환경시뮬레이션"], result
