"""SCX drive: folders respaced on the drive are the same folder (user rule 2026-10-19).

A registered file whose request/project folder was renamed only by whitespace is not ``MISSING``
(``원본 없음``); discovery follows the registration to the actual spelling with the same ids; two sibling
folders that differ only by whitespace are ambiguous.  In-memory fake drive, synthetic folders only.
"""
from __future__ import annotations

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery
from app.services.drive import reads as drive_reads
from app.services.drive import sources as drive_sources
from app.services.storage.drive import DriveRoot

from test_drive_reads import CSV, ENV, REQUEST, OPTION, Scx, _register, _scenes, _sync, scx  # noqa: F401  (fixture)

pytestmark = pytest.mark.duckdb_integration
PROJECT_FOLDER, REQUEST_FOLDER = REQUEST.split("/")
RESPACED_PROJECT, RESPACED_REQUEST = "75R9J _PV", "[WR-0001]_[유통 _ 환경]"


def _rename(scx: Scx, old: str, new: str) -> None:
    """Rename a folder on the fake drive (item ids and content are kept, as a drive rename does)."""
    old, new = scx.path(old), scx.path(new)
    for path in sorted(path for path in scx.drive.nodes if path == old or path.startswith(old + "/")):
        scx.drive.nodes[new + path[len(old):]] = scx.drive.nodes.pop(path)


def _source_rows(scx: Scx, request_id: str) -> list[tuple]:
    root_key = DriveRoot(scx.root, "scx.example.test").root_key()
    with connect() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT rel_path,source_state,review_state,version_no FROM drive_source_versions WHERE root_key=? "
            "AND request_id=? AND superseded_at IS NULL ORDER BY rel_path", [root_key, request_id]).fetchall()]


def _discover(scx: Scx) -> dict:
    folder_auto_discovery.reset_for_tests()
    return scx.post(ENV + "/discover", {"force": True})


@pytest.fixture(autouse=True)
def _discovery_interval(monkeypatch):
    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)


def test_respaced_folders_are_not_missing_even_before_discovery_follows(scx):
    project_id, request_id = _register(scx)
    assert _sync(scx, project_id, request_id)["drive"]["missing"] == 0
    registered = _source_rows(scx, request_id)
    assert registered and {row[1] for row in registered} == {"PRESENT"}

    _rename(scx, f"{PROJECT_FOLDER}/{REQUEST_FOLDER}", f"{PROJECT_FOLDER}/{RESPACED_REQUEST}")
    _rename(scx, PROJECT_FOLDER, RESPACED_PROJECT)
    result = _sync(scx, project_id, request_id)       # classification resolves the stored path by name_key
    assert result["drive"]["missing"] == 0 and result["drive"]["pending_changes"] == 0, result
    rows = _source_rows(scx, request_id)
    assert len(rows) == len(registered)                       # same rows (ids, versions), re-spelled in place
    assert all(row[0].startswith(f"{RESPACED_PROJECT}/{RESPACED_REQUEST}/") for row in rows), rows
    assert {(row[1], row[2], row[3]) for row in rows} == {("PRESENT", "NONE", 1)}
    listed = scx.client.get("/api/drive/source-changes", params={"project_id": project_id, "request_id": request_id}).json()
    assert listed["items"] == [] and listed["missing"] == 0


def test_discovery_follows_the_respaced_folders_and_results_stay_readable(scx):
    project_id, request_id = _register(scx)
    assert _sync(scx, project_id, request_id)["status"] in {"REFRESHED", "UNCHANGED"}
    with connect() as conn:
        counts = [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("projects", "analysis_requests")]
    _rename(scx, f"{PROJECT_FOLDER}/{REQUEST_FOLDER}", f"{PROJECT_FOLDER}/{RESPACED_REQUEST}")
    _rename(scx, PROJECT_FOLDER, RESPACED_PROJECT)

    result = _discover(scx)
    assert result["created_projects"] == [] and result["created_requests"] == [], result
    assert [item for item in result["needs_review"] if item["code"] != "ADMIN_EXCLUDED"] == [], result["needs_review"]
    assert [item["id"] for item in result["renamed_requests"]] == [request_id]
    assert [item["id"] for item in result["renamed_projects"]] == [project_id]
    with connect() as conn:
        assert [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("projects", "analysis_requests")] == counts
    synced = _sync(scx, project_id, request_id)
    assert synced["status"] in {"REFRESHED", "UNCHANGED"} and synced["drive"]["missing"] == 0, synced
    assert {"2_Face", "3_Face"} <= _scenes(scx, request_id)
    assert {row[1] for row in _source_rows(scx, request_id)} == {"PRESENT"}
    # ... and back without spaces: the same request again, still no duplicates and no MISSING source.
    _rename(scx, RESPACED_PROJECT, PROJECT_FOLDER)
    _rename(scx, f"{PROJECT_FOLDER}/{RESPACED_REQUEST}", f"{PROJECT_FOLDER}/{REQUEST_FOLDER}")
    back = _discover(scx)
    assert [item["id"] for item in back["renamed_requests"]] == [request_id] and back["created_requests"] == []
    after = _sync(scx, project_id, request_id)
    assert after["drive"]["missing"] == 0, after
    assert {"2_Face", "3_Face"} <= _scenes(scx, request_id)
    with connect() as conn:
        assert [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("projects", "analysis_requests")] == counts
    assert all(row[0].startswith(f"{PROJECT_FOLDER}/{REQUEST_FOLDER}/") for row in _source_rows(scx, request_id))


def test_whitespace_twins_on_the_drive_are_ambiguous(scx):
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    # Someone copies the request folder with other spacing next to the registered one.
    for path in [path for path in list(scx.drive.nodes)
                 if path.startswith(scx.path(f"{PROJECT_FOLDER}/{REQUEST_FOLDER}/"))]:
        scx.drive.add_file(scx.path(f"{PROJECT_FOLDER}/{RESPACED_REQUEST}") + path[len(scx.path(f"{PROJECT_FOLDER}/{REQUEST_FOLDER}")):],
                           scx.drive.nodes[path].content)
    result = _discover(scx)
    twins = {item["relative_path"] for item in result["needs_review"] if item["code"] == "FOLDER_NAME_WHITESPACE_DUPLICATE"}
    assert twins == {f"{PROJECT_FOLDER}/{REQUEST_FOLDER}", f"{PROJECT_FOLDER}/{RESPACED_REQUEST}"}   # same name_key
    assert result["created_requests"] == [] and result["renamed_requests"] == []
    # The registered spelling still exists: the sync resolves it exactly and nothing is missing.
    assert _sync(scx, project_id, request_id)["drive"]["missing"] == 0


def test_ambiguous_stored_path_keeps_the_old_behaviour_and_reports(monkeypatch):
    import types

    from app.services.storage.provider import SpdmStorageError

    tree = {"": ["A"], "A": ["REQ_ 1", "REQ 1_"], "B": []}

    def listing(_root, rel):
        if rel not in tree:
            raise SpdmStorageError("SPDM_NOT_FOUND", "gone")
        return [types.SimpleNamespace(name=name, kind="dir") for name in tree[rel]]

    monkeypatch.setattr(drive_reads, "listing", listing)
    session = types.SimpleNamespace(spdm_root="x")
    assert drive_sources.resolve_request_folder(session, "A/REQ_1") == ("A/REQ_ 1", False)   # one key match
    tree["A"] = ["REQ_ 1", "REQ _1"]
    assert drive_sources.resolve_request_folder(session, "A/REQ_1") == ("A/REQ_1", True)     # ambiguous: stored kept
    tree["A"] = ["REQ-1"]
    assert drive_sources.resolve_request_folder(session, "A/REQ_1") == ("A/REQ_1", False)   # nothing matches
    assert drive_sources.resolve_request_folder(session, "Gone/REQ_1") == ("Gone/REQ_1", False)
