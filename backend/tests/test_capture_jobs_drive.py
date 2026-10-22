"""Issue #55 follow-up (2026-10-22): registration and folder checks never wait for result downloads.

Production (SCX drive mode, ~6 s per download, adapter concurrency 1): a request with many Scenes stayed
``DISCOVERY_TIME_BUDGET`` on every [저장 폴더 새로고침] (its registration downloaded every result file inside
the 300 s round budget and never finished), and another request's sync failed with
``FOLDER_SCHEMA_SCAN_INCOMPLETE ... FINAL (list_dir, DRIVE_READ_NOT_PREPARED)`` (downloads used up the
check's deadline before the FINAL folder was listed).  Nothing was kept between attempts.

Here the in-memory fake drive gets a per-download latency, a request with two Cases of many Scenes, and
budgets a few seconds long, so the old code hits exactly those two failures.  Now the request registers
at once (listings only), its Cases are 결과 받는 중, and the capture jobs finish over successive bounded
passes, each file downloaded once.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery, folder_auto_sync, storage_rescan
from app.services.drive import reads as drive_reads
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_discovery_tree import FINAL_ID, JPG, OPTION, _discovery, _project  # noqa: F401  (autouse)
from tests.test_drive_reads import CSV, ENV, scx  # noqa: F401  (fixture)
from tests.test_folder_auto_discovery import _container_schema

pytestmark = pytest.mark.duckdb_integration

CASES = ("Package_Model_SetCase1_CushionCase3", "Package_Model_SetCase2_CushionCase1")
SCENES = tuple(f"DAMP-{number:02d}_Corner_Drop_Scene{number:02d}" for number in range(6))
DOWNLOAD_SECONDS = 0.3           # 2 Cases x 6 Scenes x 2 files = 24 downloads = 7.2 s per full read


def _capture_jobs():
    from app.services import capture_jobs   # imported late: the module is new with this fix

    return capture_jobs


def _slow_request(scx, request: str) -> list[str]:
    files = []
    for case in CASES:
        for scene in SCENES:
            for name, content in ((CSV, f"Position,Layer_1\nTOP,{len(files)}\n".encode()), (JPG, b"\xff\xd8jpeg" + scene.encode())):
                rel = f"{request}/Working/{case}/{OPTION}/{scene}/{name}"
                scx.add(rel, content)
                files.append(scx.path(rel))
    scx.drive.add_dir(scx.path(f"{request}/FINAL/CAD"))
    scx.drive.add_dir(scx.path(f"{request}/FINAL/Report"))
    scx.add(f"{request}/FINAL/CAE/{CASES[0]}/{FINAL_ID}/{OPTION}/9_Final/{CSV}")
    return files


def _slow_drive(scx, monkeypatch) -> None:
    scx.drive.call_delay_s = {"download_to": DOWNLOAD_SECONDS}
    monkeypatch.setattr(storage_rescan, "ROUND_BUDGET_SECONDS", 3.0)
    monkeypatch.setattr(storage_rescan, "DISCOVER_BUDGET_SECONDS", 30.0)
    monkeypatch.setattr(storage_rescan, "SCOPE_BUDGET_SECONDS", 3.0)
    monkeypatch.setattr(storage_rescan, "CAPTURE_BUDGET_SECONDS", 2.5)
    monkeypatch.setattr(folder_auto_discovery, "DRIVE_READ_SLACK_SECONDS", 1.0)
    monkeypatch.setattr(_capture_jobs(), "SESSION_SECONDS", 3.0)


def _jobs(request_id: str) -> list[tuple[str, str | None]]:
    with connect() as conn:
        return [(str(row[0]), row[1]) for row in conn.execute(
            "SELECT j.status,j.error_code FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
            "ON r.id=j.registration_id WHERE r.request_id=? AND r.status<>'DELETED' ORDER BY j.created_at",
            [request_id]).fetchall()]


def _catalog_cases(scx, request_id: str) -> list[dict]:
    response = scx.client.get("/api/dashboard/catalog", params={"request_id": request_id, "environment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    return response.json()["cases"]


def _passes_until_done(scx, request_id: str, files: list[str], *, limit: int = 12) -> int:
    """Successive bounded capture passes (the worker's): progress accumulates, every file is read once."""
    capture_jobs = _capture_jobs()
    done_before, passes = -1, 0
    while any(status == "PENDING" for status, _code in _jobs(request_id)):
        passes += 1
        assert passes <= limit, f"capture jobs made no progress: {_jobs(request_id)}"
        downloads_before = len(scx.drive.downloads())
        result = capture_jobs.run_pending(budget_seconds=3.5, session_seconds=3.0)
        assert result["failed_now"] == 0, result
        assert len(scx.drive.downloads()) > downloads_before or result["completed_now"], "a pass downloaded nothing"
        done = sum(status == "COMPLETED" for status, _code in _jobs(request_id))
        assert done >= done_before
        done_before = done
    assert [status for status, _code in _jobs(request_id)] == ["COMPLETED"] * len(CASES)
    downloaded = scx.drive.downloads()
    assert sorted(path for path in downloaded if path in files) == sorted(files), "every result file once"
    return passes


def test_rescan_registers_a_slow_many_scene_request_at_once_and_captures_it_over_passes(scx, monkeypatch):
    _container_schema()
    _slow_drive(scx, monkeypatch)
    project = _project(uuid.uuid4().hex[:6])
    name = "WR-00000806_유통 환경 시뮬레이션"
    files = _slow_request(scx, f"{project}/{name}")

    response = scx.client.post(ENV + "/rescan", json={})
    assert response.status_code == 200, response.text
    state = scx.client.get(ENV + "/rescan").json()
    assert state["status"] == "DONE", state
    # Old code: DISCOVERY_TIME_BUDGET (the registration downloaded 24 files in a 3 s round, every round).
    assert state["needs_review"] == [], state["needs_review"]
    assert [item["name"] for item in state["created_requests"]] == [name]
    assert state["failed"] == [], state["failed"]
    request_id = state["created_requests"][0]["id"]
    # The CAPTURE phase got as far as its budget allowed; the rest is 결과 받는 중, not a failure.
    captures = state["captures"]
    assert captures["total"] >= len(CASES) and captures["pending"] >= 1 and captures["failed"] == 0, captures
    assert "PENDING" in {status for status, _code in _jobs(request_id)}
    pending = [case for case in _catalog_cases(scx, request_id) if case.get("capture_pending")]
    assert pending, "the screens show the Cases as 결과 받는 중"
    assert "captures_pending=" in storage_rescan.progress_line()

    passes = _passes_until_done(scx, request_id, files)
    assert passes >= 1
    assert not any(case.get("capture_pending") for case in _catalog_cases(scx, request_id))
    assert all(case.get("capture_count") for case in _catalog_cases(scx, request_id) if case.get("source") == "FOLDER_SCHEMA")


def test_discovery_registration_lists_only_and_the_cases_wait_as_pending_jobs(scx, monkeypatch):
    _container_schema()
    _slow_drive(scx, monkeypatch)
    project = _project(uuid.uuid4().hex[:6])
    name = "WR-00000804_유통환경시뮬레이션 (유통)"
    files = _slow_request(scx, f"{project}/{name}")
    response = scx.client.post(ENV + "/discover", json={"force": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["name"] for item in body["created_requests"]] == [name], body
    assert body["needs_review"] == [], body["needs_review"]
    assert scx.drive.downloads() == [], "registration reads listings only"
    request_id = body["created_requests"][0]["id"]
    assert _jobs(request_id) == [("PENDING", None)] * len(CASES)
    with connect() as conn:
        registration = conn.execute("SELECT id FROM folder_environment_registrations WHERE request_id=?",
                                    [request_id]).fetchone()[0]
    status = scx.client.get(f"{ENV}/registrations/{registration}").json()
    assert status["status"] == "CAPTURING", status
    assert {case["label"] for case in _catalog_cases(scx, request_id) if case.get("capture_pending")} == set(CASES)
    _passes_until_done(scx, request_id, files)
    assert scx.client.get(f"{ENV}/registrations/{registration}").json()["status"] == "COMPLETED"


def test_sync_scan_is_complete_even_when_content_checks_use_up_its_deadline(scx, monkeypatch):
    """Old code: classification downloads (size/time tokens) used the deadline, then the scan lacked its
    listings (FINAL is not walked by the classification) -> FAILED (here FOLDER_SCHEMA_SCAN_UNAVAILABLE, on the
    server FOLDER_SCHEMA_SCAN_INCOMPLETE '.../FINAL (list_dir, DRIVE_READ_NOT_PREPARED)'), and the files left
    uncompared after the deadline were recorded as pending changes."""
    _container_schema()
    scx.drive.list_has_sha1 = scx.drive.stat_has_sha1 = False      # size + time tokens: content compares
    project = _project(uuid.uuid4().hex[:6])
    request = f"{project}/WR-00000805_유통 환경 시뮬레이션"
    files = _slow_request(scx, request)
    body = scx.post(ENV + "/discover", {"force": True})
    [created] = body["created_requests"]
    project_id, request_id = created["project_id"], created["id"]
    assert folder_auto_sync.sync_scope(project_id, request_id, "DISTRIBUTION", "test", force=True)["status"] != "FAILED"
    _passes_until_done(scx, request_id, files)

    _slow_drive(scx, monkeypatch)
    for path in files:
        scx.drive.touch(path)               # same bytes, new time: every file needs a content compare
    with drive_reads.bounded(deadline=time.monotonic() + 1.5):
        result = folder_auto_sync.sync_scope(project_id, request_id, "DISTRIBUTION", "test", force=True)
    assert result["status"] in {"REFRESHED", "UNCHANGED"}, result
    assert result["drive"]["pending_changes"] == 0, result["drive"]
    # The next checks finish the comparisons they had no time for; nothing is asked of the user.
    scx.drive.call_delay_s = {}
    final = folder_auto_sync.sync_scope(project_id, request_id, "DISTRIBUTION", "test", force=True)
    assert final["status"] in {"REFRESHED", "UNCHANGED"} and final["drive"]["pending_changes"] == 0, final
