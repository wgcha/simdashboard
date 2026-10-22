"""Issue #55 (2026-10-20): the per-request sync reads a deep real-shaped SCX tree in one bulk round.

Real server: the rescan's request check answered ``FOLDER_SCHEMA_SCAN_INCOMPLETE`` for requests whose
Working/FINAL branches are deep (``FINAL/CAE/<case>/<id or name>/Drop/<run>/INDIVIDUAL/<scene>/files``): the
sync body read the tree a few levels per read round and ran out of ``MAX_ROUNDS``. In-memory fake adapter.
"""
from __future__ import annotations

import uuid

import pytest

from drive_fakes import ErrorCode

from app.database_connection import connect
from app.services import folder_auto_discovery, folder_auto_sync, folder_schema_resolver, storage_rescan
from app.services.drive import reads as drive_reads
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_reads import CSV, ENV, _root_key, scx  # noqa: F401  (fixture)
from tests.test_folder_auto_discovery import _container_schema

pytestmark = pytest.mark.duckdb_integration

CONTAINER = "SPDM (Admin)"
WORK_CASE = "Assy_Motion_75R9J_Set0907_Stand0907_Inner"
FINAL_CASE = "completed_Assy_Motion_75R9J_Set0907_Stand0907_Inner"
RUN = "85qn80h_ref_organized"
FINAL_ID = "0bb7b4f6567f4ce985bba010c7f569e7"
JPG = "CONTOUR_COMP23_Max_Stress_P1 (major)_Mid.jpg"


@pytest.fixture(autouse=True)
def _discovery(monkeypatch):
    folder_auto_discovery.reset_for_tests()
    storage_rescan.reset_for_tests()
    folder_auto_sync.reset_for_tests()
    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(storage_rescan, "INLINE", True)
    yield
    folder_auto_discovery.reset_for_tests()
    storage_rescan.reset_for_tests()
    folder_auto_sync.reset_for_tests()


def _project(tag: str) -> str:
    return f"{CONTAINER}/PROJ-{tag}_R95J 개발 과제"


def _working(scx, request: str, scenes=("2_Face", "3_Face", "4_Face")) -> None:
    for scene in scenes:
        scx.add(f"{request}/Working/{WORK_CASE}/Drop/{RUN}/INDIVIDUAL/{scene}/{CSV}")
        scx.add(f"{request}/Working/{WORK_CASE}/Drop/{RUN}/INDIVIDUAL/{scene}/{JPG}", b"\xff\xd8jpeg")


def _final_deep(scx, request: str, version: str | None = FINAL_ID, leaf: str = "Horizontal_Force_Angle",
                scenes=("9_Final", "9_Final_B")) -> str:
    base = f"{request}/FINAL/CAE/{FINAL_CASE}" + (f"/{version}" if version else "") + f"/{leaf}"
    for scene in scenes:
        scx.add(f"{base}/Drop/{RUN}/INDIVIDUAL/{scene}/{CSV}")
    scx.drive.add_dir(scx.path(f"{request}/FINAL/CAD"))
    scx.drive.add_dir(scx.path(f"{request}/FINAL/Report"))
    return base


def _register_shallow(scx, request: str) -> dict:
    """Discover the request with its Working branch only, so a later sync meets the deep FINAL branch."""
    _working(scx, request)
    result = scx.post(ENV + "/discover", {"force": True})
    assert result["needs_review"] == [], result["needs_review"]
    [created] = [item for item in result["created_requests"] if item["name"] == request.rsplit("/", 1)[-1]]
    return created


def _sync(scx, created: dict, *, drain: bool = True, **extra) -> dict:
    return scx.post(ENV + "/sync", {"project_id": created["project_id"], "request_id": created["id"],
                                    "environment": "DISTRIBUTION", "force": True, **extra}, drain=drain)


def _count_misses(monkeypatch) -> list[tuple[str, str]]:
    misses: list[tuple[str, str]] = []
    original = drive_reads._fulfil

    def counting(session, miss):
        misses.append((miss.kind, miss.rel))
        return original(session, miss)

    monkeypatch.setattr(drive_reads, "_fulfil", counting)
    return misses


def _snapshot_nodes(scx, created: dict) -> dict[str, dict]:
    with connect() as conn:
        snapshot = folder_schema_resolver.active_refresh_snapshot(
            conn, _root_key(scx), created["project_id"], created["id"], "DISTRIBUTION")
    assert snapshot is not None
    return {str(node["relative_path"]): node for node in snapshot["schema_json"]["nodes"]}


def test_sync_reads_a_deep_multi_version_final_branch_in_bulk(scx, monkeypatch):
    """Each Final version used to cost two read rounds (4 levels per miss): six versions ran out of MAX_ROUNDS."""
    _container_schema()
    request = f"{_project(uuid.uuid4().hex[:6])}/WR-00000800_유통 환경 시뮬레이션"
    created = _register_shallow(scx, request)
    for number in range(6):
        _final_deep(scx, request, version=f"{number + 1:032x}", leaf=f"Leaf{number}",
                    scenes=tuple(f"S{index}" for index in range(6)))
    misses = _count_misses(monkeypatch)
    synced = _sync(scx, created, drain=False)    # the sync only (its Case captures are capture jobs)
    assert synced["status"] in {"REFRESHED", "UNCHANGED"}, synced
    kinds = [kind for kind, _rel in misses]
    # One bulk listing, not one miss per branch: since 2026-10-22 the classification lists the whole tree
    # before the body (listings first), so the body has no listing miss at all.
    assert "list" not in kinds and len(misses) <= 3, misses
    nodes = _snapshot_nodes(scx, created)
    deepest = f"{request}/FINAL/CAE/{FINAL_CASE}/{6:032x}/Leaf5/Drop/{RUN}/INDIVIDUAL/S5"
    assert deepest in nodes
    # A repeated sync of the unchanged folders reads nothing new through misses.
    again = _sync(scx, created)
    assert again["status"] == "UNCHANGED", again


def test_manual_refresh_reads_the_deep_tree_in_bulk_too(scx, monkeypatch):
    _container_schema()
    request = f"{_project(uuid.uuid4().hex[:6])}/WR-00000801_유통 환경 시뮬레이션"
    created = _register_shallow(scx, request)
    for number in range(5):
        _final_deep(scx, request, version=f"{number + 1:032x}", leaf=f"Leaf{number}")
    misses = _count_misses(monkeypatch)
    refreshed = scx.post(ENV + "/refresh", {"project_id": created["project_id"], "request_id": created["id"],
                                            "environment": "DISTRIBUTION"}, drain=False)
    assert refreshed["status"] in {"REFRESHED", "UNCHANGED"}, refreshed
    assert len(misses) <= 3, misses


def test_unversioned_final_case_is_recognised_by_the_drive_sync(scx):
    """Issue #55 B: FINAL/CAE/<case>/<non-id>/... is one unversioned Final, not a deviation."""
    _container_schema()
    request = f"{_project(uuid.uuid4().hex[:6])}/WR-00000805_유통 환경 시뮬레이션"
    created = _register_shallow(scx, request)
    base = _final_deep(scx, request, version=None, leaf="Drop")
    _final_deep(scx, request, version=FINAL_ID, leaf="Drop", scenes=("9_Final",))
    synced = _sync(scx, created)
    assert synced["status"] in {"REFRESHED", "UNCHANGED"}, synced
    nodes = _snapshot_nodes(scx, created)
    assert nodes[f"{request}/FINAL/CAE/{FINAL_CASE}"]["role_kind"] == "SIMULATION_CASE"
    assert nodes[f"{request}/FINAL/CAE/{FINAL_CASE}/{FINAL_ID}"]["role_kind"] == "FINAL_VERSION"
    assert nodes[base]["role_kind"] == "LOAD_CASE"                       # Working L3 one level higher
    assert not [node for node in nodes.values() if (node.get("deviation") or {}).get("code") == "FINAL_VERSION_INVALID"]


def test_incomplete_scan_message_names_the_unread_path_and_operation(scx):
    _container_schema()
    request = f"{_project(uuid.uuid4().hex[:6])}/WR-00000804_유통환경시뮬레이션"
    created = _register_shallow(scx, request)
    base = _final_deep(scx, request)
    broken = f"{base}/Drop/{RUN}"
    scx.drive.fail_path[("list_dir", scx.path(broken))] = [ErrorCode.INTERNAL] * 50
    synced = _sync(scx, created)
    assert synced["status"] == "FAILED" and synced["code"] == "FOLDER_SCHEMA_SCAN_INCOMPLETE", synced
    assert broken in synced["message"] and "list_dir" in synced["message"], synced["message"]
    assert "읽지 못한 곳" in synced["message"]
