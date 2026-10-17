"""SCX drive automatic discovery and full rescan on a deep, real-like tree (issue #51, 2026-10-17).

Real server: ``POST /discover`` answered 500 (``_Lister.mtime`` -> ``DRIVE_READ_NOT_PREPARED`` after the
read session's last round) and request folders were reported ``SCAN_INCOMPLETE`` with
``PATH_UNAVAILABLE`` below ``FINAL/CAD`` etc. The tree here has the real shape: a container level,
a project folder with spaces and Korean text, several request folders (with and without spaces) with
a deep Working Case and a deeper FINAL branch. In-memory fake adapter only.
"""
from __future__ import annotations

import unicodedata
import uuid

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery, storage_rescan
from app.services.drive import reads as drive_reads
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_reads import CSV, CSV_BYTES, ENV, scx  # noqa: F401  (fixture)
from drive_fakes import ErrorCode
from tests.test_folder_auto_discovery import RESCAN, _container_schema
from tests.test_new_scene_registration import admin_client  # noqa: F401  (fixture)

pytestmark = pytest.mark.duckdb_integration

CONTAINER = "SPDM (Admin)"
CASE = "Package_Model_SetCase1_CushionCase3"
OPTION = "Drop/85qn80h_ref_organized/INDIVIDUAL"
SCENE = "DAMP-13_Corner_Drop_2nd_Scene08_Corner346_2nd"
JPG = "CONTOUR_COMP23_Max_Stress_P1 (major)_Mid.jpg"
FINAL_ID = "0bb7b4f6567f4ce985bba010c7f569e7"


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


def _request_tree(scx, request: str, scenes=("2_Face", SCENE)) -> None:
    for scene in scenes:
        scx.add(f"{request}/Working/{CASE}/{OPTION}/{scene}/{CSV}")
        scx.add(f"{request}/Working/{CASE}/{OPTION}/{scene}/{JPG}", b"\xff\xd8jpeg")
    scx.drive.add_dir(scx.path(f"{request}/FINAL/CAD"))
    scx.drive.add_dir(scx.path(f"{request}/FINAL/Report"))
    scx.add(f"{request}/FINAL/CAE/{CASE}/{FINAL_ID}/{OPTION}/9_Final/{CSV}")


def _project(tag: str) -> str:
    # Project names are shared by every test of the database: a unique prefix keeps PROJECT_NAME_EXISTS away.
    return f"{CONTAINER}/PROJ-{tag}_R95J 개발 과제"


def test_discover_registers_every_request_of_a_deep_tree_with_spaced_names(scx):
    _container_schema()
    project = _project(uuid.uuid4().hex[:6])
    requests = [f"{project}/WR-00000800_유통 환경 시뮬레이션", f"{project}/WR-00000804_유통환경시뮬레이션",
                f"{project}/WR-00000806_유통  환경 (2차)"]
    for request in requests:
        _request_tree(scx, request)
    scx.drive.add_dir(scx.path(f"{project}/CAD"))   # project-level shared folder (never a request)

    result = scx.post(ENV + "/discover", {"force": True})
    assert result["needs_review"] == [], result["needs_review"]
    assert sorted(item["name"] for item in result["created_requests"]) == sorted(
        path.rsplit("/", 1)[-1] for path in requests)
    assert [item["name"] for item in result["created_projects"]] == [project.rsplit("/", 1)[-1]]
    # Nothing left for a later run, and a repeated run finds nothing new and asks nothing.
    again = scx.post(ENV + "/discover", {"force": True})
    assert again["created_requests"] == [] and again["needs_review"] == [], again


def test_discover_never_answers_500_when_the_read_rounds_run_out(scx, monkeypatch):
    """One read round only: every unread folder must be deferred or reported, never an error answer."""
    _container_schema()
    project = _project(uuid.uuid4().hex[:6])
    for number in range(3):
        _request_tree(scx, f"{project}/WR-0000090{number}_유통 환경 시뮬레이션")
    monkeypatch.setattr(folder_auto_discovery, "DISCOVERY_READ_ROUNDS", 1)
    response = scx.client.post(ENV + "/discover", json={"force": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_requests"] == []
    codes = {item["code"] for item in body["needs_review"]}
    assert codes <= {"DRIVE_READ_LIMIT"}, body["needs_review"]
    # The next run (normal rounds) registers them.
    monkeypatch.setattr(folder_auto_discovery, "DISCOVERY_READ_ROUNDS", 23)
    result = scx.post(ENV + "/discover", {"force": True})
    assert len(result["created_requests"]) == 3 and result["needs_review"] == [], result


def test_full_rescan_registers_a_backlog_beyond_one_run_and_syncs_it(scx):
    _container_schema()
    project = _project(uuid.uuid4().hex[:6])
    names = [f"WR-000010{number:02d}_유통 환경 시뮬레이션" for number in range(folder_auto_discovery.MAX_NEW_REQUESTS_PER_RUN + 2)]
    for name in names:
        _request_tree(scx, f"{project}/{name}", scenes=("2_Face",))
    started = scx.post(ENV + "/rescan", {})
    assert started["started"] is True
    state = scx.client.get(ENV + "/rescan").json()
    assert state["status"] == "DONE", state
    assert state["needs_review"] == [], state["needs_review"]
    assert sorted(item["name"] for item in state["created_requests"]) == sorted(names)
    assert state["failed"] == [], state["failed"]


def test_adapter_listing_failure_is_one_needs_review_item_naming_the_drive_operation(scx):
    _container_schema()
    project = _project(uuid.uuid4().hex[:6])
    good, bad = f"{project}/WR-00000830_유통 환경 시뮬레이션", f"{project}/WR-00000831_유통 환경 시뮬레이션"
    _request_tree(scx, good, scenes=("2_Face",))
    _request_tree(scx, bad, scenes=("2_Face",))
    scx.drive.fail_path[("list_dir", scx.path(f"{bad}/Working"))] = [ErrorCode.INTERNAL] * 50
    response = scx.client.post(ENV + "/discover", json={"force": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["name"] for item in body["created_requests"]] == [good.rsplit("/", 1)[-1]]
    [review] = body["needs_review"]
    assert (review["code"], review["relative_path"], review["drive_operation"], review["issue_path"]) == (
        "DRIVE_READ_FAILED", bad, "list_dir", f"{bad}/Working"), review
    assert "드라이브 어댑터" in review["reason"] and "list_dir" in review["reason"]


def test_trailing_space_folder_on_the_drive_is_reported_not_scanned(scx):
    _container_schema()
    project = _project(uuid.uuid4().hex[:6])
    _request_tree(scx, f"{project}/WR-00000840_유통 환경 ", scenes=("2_Face",))
    body = scx.post(ENV + "/discover", {"force": True})
    assert body["created_requests"] == []
    assert [item["code"] for item in body["needs_review"]] == ["FOLDER_NAME_INVALID"], body["needs_review"]


def test_nfd_folder_names_from_the_drive_are_registered_once_in_nfc(scx):
    """A macOS-backed drive may list Korean names decomposed (NFD); paths are compared in NFC."""
    _container_schema()
    tag = uuid.uuid4().hex[:6]
    project_nfc = _project(tag)
    request_name = "WR-00000810_유통 환경 시뮬레이션"
    nfd = unicodedata.normalize("NFD", f"{project_nfc}/{request_name}")
    assert nfd != unicodedata.normalize("NFC", nfd)
    _request_tree(scx, nfd, scenes=("2_Face",))
    result = scx.post(ENV + "/discover", {"force": True})
    assert result["needs_review"] == [], result["needs_review"]
    assert [unicodedata.normalize("NFC", item["name"]) for item in result["created_requests"]] == [request_name]
    with connect() as conn:
        paths = [str(row[0]) for row in conn.execute(
            "SELECT g.relative_path FROM folder_environment_registry g JOIN folder_environment_registrations r "
            "ON r.id=g.registration_id WHERE r.request_id=? AND g.role_kind='REQUEST'",
            [result["created_requests"][0]["id"]]).fetchall()]
    assert paths and all(path == unicodedata.normalize("NFC", path) for path in paths)
    again = scx.post(ENV + "/discover", {"force": True})
    assert again["created_requests"] == [] and again["needs_review"] == [], again
    # The request sync reads the same folder (NFC paths, NFD names on the drive) without errors.
    request_id, project_id = result["created_requests"][0]["id"], result["created_requests"][0]["project_id"]
    synced = scx.post(ENV + "/sync", {"project_id": project_id, "request_id": request_id,
                                      "environment": "DISTRIBUTION", "force": True})
    assert synced["status"] not in {"FAILED", "CONFLICT"}, synced
    drive_reads.reset_for_tests()


# --- synthetic local root: the same names (spaces, double space, parentheses, NFD, trailing space) ---

def _local_request(root, request: str) -> None:
    for scene in ("2_Face", SCENE):
        folder = root / request / "Working" / CASE / OPTION / scene
        folder.mkdir(parents=True)
        (folder / CSV).write_bytes(CSV_BYTES)
    (root / request / "FINAL" / "CAD").mkdir(parents=True)
    final = root / request / "FINAL" / "CAE" / CASE / FINAL_ID / OPTION / "9_Final"
    final.mkdir(parents=True)
    (final / CSV).write_bytes(CSV_BYTES)


def test_local_root_discovers_rescans_and_syncs_spaced_korean_and_nfd_names(admin_client):
    client, root = admin_client
    _container_schema()
    project = _project(uuid.uuid4().hex[:6])
    names = ["WR-00000820_유통 환경 시뮬레이션", "WR-00000821_유통  환경 (2차)",
             unicodedata.normalize("NFD", "WR-00000822_유통 환경 시뮬레이션")]
    for name in names:
        _local_request(root, f"{project}/{name}")
    (root / project / "WR-00000823_유통 환경 ").mkdir()   # trailing space: not a valid Windows name

    response = client.post(RESCAN)
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["status"] == "DONE", state
    assert sorted(item["name"] for item in state["created_requests"]) == sorted(names)
    assert [(item["code"], item["relative_path"]) for item in state["needs_review"]] == [
        ("FOLDER_NAME_INVALID", f"{project}/WR-00000823_유통 환경 ")]
    assert state["failed"] == [] and state["scopes"] >= 3
    for item in state["created_requests"]:
        synced = client.post(ENV + "/sync", json={"project_id": item["project_id"], "request_id": item["id"],
                                                  "environment": "DISTRIBUTION", "force": True})
        assert synced.status_code == 200 and synced.json()["status"] not in {"FAILED", "CONFLICT"}, synced.text
    again = client.post(ENV + "/discover", json={"force": True}).json()
    assert again["created_requests"] == [], again
