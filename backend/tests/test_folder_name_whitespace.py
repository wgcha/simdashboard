"""Whitespace carries no meaning in SPDM folder names (user rule 2026-10-19).

``PROJ-000099_R95J 개발 과제`` and ``PROJ-000099_R95J개발과제`` are the same folder: discovery follows the
actual spelling (same project/request ids, no duplicates), the drive source classification does not report
``원본 없음`` for a respaced folder, and two sibling folders that differ only by whitespace are ambiguous
(``FOLDER_NAME_WHITESPACE_DUPLICATE``).  Local synthetic root and the in-memory fake drive only.
"""
from __future__ import annotations

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery
from app.services.drive import reads as drive_reads
from tests.test_folder_auto_discovery import (  # noqa: F401  (fixtures and helpers)
    CONTAINER, OLD_PROJECT, PROJECT, R1, R2, SPACED, JOINED, JOINED_PROJECT, _container_schema, _count, _counts,
    _discover, _distribution_request, _no_interval, _isolate_project_names, _registered_request, _request_rows,
    _root_key, _scenes, _sync, admin_client, _restore_default_depth_schema,
)

pytestmark = pytest.mark.duckdb_integration


# --- the key ------------------------------------------------------------------------------------------

def test_name_key_ignores_whitespace_case_and_unicode_form():
    import unicodedata

    key = drive_reads.name_key
    assert key("PROJ-000099_R95J 개발 과제") == key("PROJ-000099_R95J개발과제") == key("proj-000099_r95j  개발\t과제")
    assert key("WR-00000804_유통 환경 시뮬레이션") == key("WR-00000804_유통환경시뮬레이션")
    assert key(unicodedata.normalize("NFD", "개발 과제")) == key("개발과제")
    assert key("A_B") != key("AB")   # underscores separate parts: they are never dropped
    assert drive_reads.path_key("A B//C /d") == "ab/c/d" and folder_auto_discovery._folded_name("X Y") == "xy"


def test_resolve_spelling_is_segment_wise_and_never_guesses():
    tree = {"": ["PROJ A", "Other"], "PROJ A": ["WR-1_유통환경", "WR-2_x"], "Other": ["a b", "ab"]}

    def children(parent):
        return tree.get(parent)

    assert drive_reads.resolve_spelling("PROJA/WR-1_유통 환경", children) == ("PROJ A/WR-1_유통환경", False)
    assert drive_reads.resolve_spelling("PROJ A/WR-2_x", children) == ("PROJ A/WR-2_x", False)   # exact stays
    assert drive_reads.resolve_spelling("PROJA/WR-3_none", children) == (None, False)            # missing segment
    assert drive_reads.resolve_spelling("Other/a  b", children) == (None, True)                  # two matches
    assert drive_reads.resolve_spelling("Other/ab", children) == ("Other/ab", False)             # exact wins
    assert drive_reads.resolve_spelling("PROJA/WR-1_유통환경", lambda parent: None if parent else ["PROJ A"]) == (None, False)


# --- local mode: respaced request and project folders ------------------------------------------------

def _live(request_id):
    return [row for row in _request_rows(request_id) if row[1] != "DELETED"]


def test_request_folder_respaced_both_ways_keeps_ids_and_creates_no_duplicates(admin_client):
    from app.services import folder_schema_resolver as resolver

    client, root = admin_client
    project_id, request_id = _registered_request(client, root)         # registered with spaces (SPACED)
    before = _counts()
    for old, new in ((SPACED, JOINED), (JOINED, SPACED)):              # spaces removed, then added again
        (root / old).rename(root / new)
        result = _discover(client)
        assert result["created_requests"] == [] and result["created_projects"] == [], result
        assert result["needs_review"] == [], result["needs_review"]
        [followed] = result["renamed_requests"]
        assert (followed["id"], followed["project_id"], followed["old_path"], followed["new_path"]) == (
            request_id, project_id, old, new)
        assert _counts()[:2] == before[:2]                              # no duplicate project/request
        assert len(_live(request_id)) == 1                              # the old registration is a tombstone
        with connect() as conn:
            assert resolver._request_path(conn, _root_key(root), project_id, request_id, "DISTRIBUTION") == new
        assert _sync(client, project_id, request_id)["status"] in {"REFRESHED", "UNCHANGED"}
        assert {"2_Face", "3_Face"} <= _scenes(client, project_id, request_id)
        folder_auto_discovery.reset_for_tests()
        again = _discover(client)                                       # idempotent
        assert (again["created_requests"], again["renamed_requests"], again["needs_review"]) == ([], [], [])


def test_mixed_spaced_project_and_unspaced_request_follow_when_both_flip(admin_client):
    client, root = admin_client
    _container_schema()
    r1_joined = R1.replace(" ", "")
    _distribution_request(root, f"{OLD_PROJECT}/{r1_joined}")           # spaced project, unspaced request
    (root / OLD_PROJECT / R2 / "Working").mkdir(parents=True)
    created = _discover(client)
    assert created["needs_review"] == [], created["needs_review"]
    [project] = created["created_projects"]
    requests = {item["name"]: item["id"] for item in created["created_requests"]}
    assert set(requests) == {r1_joined, R2}
    assert _sync(client, project["id"], requests[r1_joined])["status"] in {"REFRESHED", "UNCHANGED"}
    folder_auto_discovery.reset_for_tests()
    counts = _counts()

    (root / OLD_PROJECT).rename(root / JOINED_PROJECT)                  # project loses its spaces ...
    (root / JOINED_PROJECT / r1_joined).rename(root / JOINED_PROJECT / R1)   # ... the request gains them
    result = _discover(client)
    assert result["needs_review"] == [] and result["created_projects"] == [] and result["created_requests"] == [], result
    assert {item["id"] for item in result["renamed_requests"]} == set(requests.values())
    assert [(item["id"], item["new_path"]) for item in result["renamed_projects"]] == [(project["id"], JOINED_PROJECT)]
    assert _counts()[:2] == counts[:2]
    for request_id in requests.values():
        assert len(_live(request_id)) == 1
    assert {"2_Face", "3_Face"} <= _scenes(client, project["id"], requests[r1_joined])
    folder_auto_discovery.reset_for_tests()
    again = _discover(client)
    assert (again["created_requests"], again["renamed_requests"], again["renamed_projects"], again["needs_review"]) == (
        [], [], [], [])


def test_sibling_request_folders_differing_only_by_whitespace_need_review(admin_client):
    client, root = admin_client
    _container_schema()
    before = _counts()
    (root / PROJECT / "WR-0000806_유통 환경 시뮬레이션" / "Working").mkdir(parents=True)
    (root / PROJECT / "WR-0000806_유통환경시뮬레이션" / "Working").mkdir(parents=True)
    (root / PROJECT / "WR-0000807_유통환경" / "Working").mkdir(parents=True)   # a normal sibling still registers
    result = _discover(client)
    reviews = {item["relative_path"]: item for item in result["needs_review"]}
    assert {path: item["code"] for path, item in reviews.items()} == {
        f"{PROJECT}/WR-0000806_유통 환경 시뮬레이션": "FOLDER_NAME_WHITESPACE_DUPLICATE",
        f"{PROJECT}/WR-0000806_유통환경시뮬레이션": "FOLDER_NAME_WHITESPACE_DUPLICATE"}
    assert "공백" in next(iter(reviews.values()))["reason"]
    assert [item["name"] for item in result["created_requests"]] == ["WR-0000807_유통환경"]
    assert _count("SELECT count(*) FROM analysis_requests") == before[1] + 1   # nothing linked for the pair


def test_duplicate_project_folders_and_registered_request_with_a_new_twin(admin_client):
    client, root = admin_client
    _container_schema()
    (root / CONTAINER / "PROJ-X_가 나" / "WR-0000901_유통환경" / "Working").mkdir(parents=True)
    (root / CONTAINER / "PROJ-X_가나" / "WR-0000901_유통환경" / "Working").mkdir(parents=True)
    before = _counts()
    result = _discover(client)
    # Project-level whitespace twins are ONE project (issue #55, one project row for both spellings); the request
    # folder with the same key under both spellings is the ambiguous one (nothing chosen, nothing registered).
    codes = {item["relative_path"]: item["code"] for item in result["needs_review"]
             if item["relative_path"].startswith(f"{CONTAINER}/PROJ-X_")}
    assert codes == {f"{CONTAINER}/PROJ-X_가 나/WR-0000901_유통환경": "FOLDER_NAME_WHITESPACE_DUPLICATE",
                     f"{CONTAINER}/PROJ-X_가나/WR-0000901_유통환경": "FOLDER_NAME_WHITESPACE_DUPLICATE"}
    assert [item["name"] for item in result["created_projects"] if item["name"].startswith("PROJ-X_")] == ["PROJ-X_가 나"]
    assert not [item for item in result["created_requests"] if item["name"] == "WR-0000901_유통환경"]
    assert _counts()[:2] == (before[0] + 1, before[1])

    # A registered request whose folder gets a whitespace-twin: nothing is followed or registered.
    project_id, request_id = _registered_request(client, root)
    registrations = _count("SELECT count(*) FROM folder_environment_registrations")
    (root / JOINED / "Working").mkdir(parents=True)
    twin = _discover(client)
    assert twin["created_requests"] == [] and twin["renamed_requests"] == []
    assert {item["code"] for item in twin["needs_review"] if item["relative_path"] in {SPACED, JOINED}} == {
        "FOLDER_NAME_WHITESPACE_DUPLICATE"}
    assert _count("SELECT count(*) FROM folder_environment_registrations") == registrations
    assert len(_live(request_id)) == 1
