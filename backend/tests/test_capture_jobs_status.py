"""Issue #55 (2026-10-27): long result downloads must not block the rescan, and their status is visible.

* the rescan's CAPTURE phase has a short budget: it ends DONE with the rest left PENDING, a Korean note
  and a wake-up for the background capture worker;
* ``GET /api/folder-discovery/environments/capture-jobs`` lists the jobs with status, error text and the
  live progress of the running job (global administrators only).
"""
from __future__ import annotations

import uuid

import pytest

from app.database_connection import connect
from app.services import folder_discovery_environment as environment_service
from app.services import storage_rescan
from tests.test_capture_jobs_drive import CASES, _capture_jobs, _jobs, _slow_drive, _slow_request
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_discovery_tree import _discovery, _project  # noqa: F401  (autouse)
from tests.test_drive_reads import ENV, scx  # noqa: F401  (fixture)
from tests.test_folder_auto_discovery import _container_schema

pytestmark = pytest.mark.duckdb_integration


def test_capture_budget_default_and_env_override(monkeypatch):
    monkeypatch.delenv(storage_rescan.ENV_CAPTURE_BUDGET, raising=False)
    assert storage_rescan.capture_budget_seconds() == storage_rescan.CAPTURE_BUDGET_SECONDS
    assert storage_rescan.DEFAULT_CAPTURE_BUDGET_SECONDS <= 180.0
    monkeypatch.setenv(storage_rescan.ENV_CAPTURE_BUDGET, "45")
    assert storage_rescan.capture_budget_seconds() == 45.0
    monkeypatch.setenv(storage_rescan.ENV_CAPTURE_BUDGET, "oops")
    assert storage_rescan.capture_budget_seconds() == storage_rescan.CAPTURE_BUDGET_SECONDS


def test_rescan_hands_remaining_captures_to_the_background_worker_and_status_lists_them(scx, monkeypatch):
    _container_schema()
    _slow_drive(scx, monkeypatch)
    project = _project(uuid.uuid4().hex[:6])
    _slow_request(scx, f"{project}/WR-00000901_유통 환경 시뮬레이션")
    jobs = _capture_jobs()
    jobs._wake.clear()

    assert scx.client.post(ENV + "/rescan", json={}).status_code == 200
    state = scx.client.get(ENV + "/rescan").json()
    # Not blocked: DONE (not FAILED/RUNNING), the unfinished downloads wait as PENDING with a Korean note.
    assert state["status"] == "DONE", state
    assert state["captures"]["pending"] >= 1 and state["captures"]["failed"] == 0, state["captures"]
    assert f"결과 받기 {state['captures']['pending']}건은 백그라운드에서 계속 받습니다" in state["captures_note"]
    assert jobs._wake.is_set(), "the background worker is woken when the rescan ends"
    request_id = state["created_requests"][0]["id"]
    assert "PENDING" in {status for status, _code in _jobs(request_id)}

    body = scx.client.get(ENV + "/capture-jobs").json()
    mine = [item for item in body["items"] if item["request_id"] == request_id]
    assert len(mine) == len(CASES)
    assert body["totals"]["total"] >= len(CASES) and body["totals"]["pending"] >= 1
    assert {item["case_relative_path"].split("/")[-1] for item in mine} == set(CASES)
    for item in mine:
        assert item["status"] in {"PENDING", "COMPLETED"}
        assert item["request_name"] and item["project_name"] and item["registration_id"] and item["created_at"]
        assert item["error_code"] is None and item["progress"] is None
    pending_only = scx.client.get(ENV + "/capture-jobs", params={"status": "PENDING"}).json()
    assert all(item["status"] == "PENDING" for item in pending_only["items"])
    assert pending_only["totals"] == body["totals"]
    assert scx.client.get(ENV + "/capture-jobs", params={"status": "BOGUS"}).status_code == 422


def test_status_shows_live_progress_failed_job_with_korean_error_and_retry(scx, monkeypatch):
    _container_schema()
    _slow_drive(scx, monkeypatch)
    project = _project(uuid.uuid4().hex[:6])
    _slow_request(scx, f"{project}/WR-00000902_유통 환경 시뮬레이션")
    request_id = scx.client.post(ENV + "/discover", json={"force": True}).json()["created_requests"][0]["id"]
    jobs = _capture_jobs()

    # While a job runs, its downloaded files and bytes are visible (in memory) and its status is RUNNING.
    # The live entry is dropped only after capture_job returns, so the spy sees the finished round's counters.
    seen: dict = {}
    original = environment_service.capture_job

    def spying(conn, job_id, root, **kwargs):
        result = original(conn, job_id, root, **kwargs)
        live = jobs.live_progress()
        if any(entry["files"] for entry in live.values()):
            seen["live"] = live
            with connect() as other:
                seen["listing"] = jobs.list_jobs(other)["items"]
        return result

    monkeypatch.setattr(environment_service, "capture_job", spying)
    jobs.run_pending(budget_seconds=6.0, session_seconds=5.0)
    monkeypatch.setattr(environment_service, "capture_job", original)
    assert seen, "a job downloaded files while the spy watched"
    entry = next(iter(seen["live"].values()))
    assert entry["files"] > 0 and entry["bytes"] > 0
    running = [item for item in seen["listing"] if item["status"] == "RUNNING"]
    assert running and running[0]["progress"]["files"] > 0 and running[0]["progress"]["bytes"] > 0
    assert jobs.live_progress() == {}, "nothing is left running afterwards"

    with connect() as conn:
        conn.execute("UPDATE folder_environment_capture_jobs SET status='FAILED',error_code='DASHBOARD_CASE_INVALID' "
                     "WHERE id=(SELECT j.id FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
                     "ON r.id=j.registration_id WHERE r.request_id=? AND j.status='PENDING' LIMIT 1)", [request_id])
    body = scx.client.get(ENV + "/capture-jobs", params={"status": "FAILED"}).json()
    failed = [item for item in body["items"] if item["request_id"] == request_id]
    assert len(failed) == 1 and failed[0]["error_code"] == "DASHBOARD_CASE_INVALID"
    assert "Case 폴더" in failed[0]["error_message"]
    assert body["totals"]["failed"] >= 1
    # Retry button: the existing endpoint puts the job back to PENDING.
    retry = scx.client.post(f"{ENV}/registrations/{failed[0]['registration_id']}/capture/retry",
                            json={"job_ids": [failed[0]["job_id"]]})
    assert retry.status_code == 200, retry.text
    after = scx.client.get(ENV + "/capture-jobs", params={"status": "FAILED"}).json()["items"]
    assert all(item["job_id"] != failed[0]["job_id"] for item in after)


def test_status_requires_authentication(scx):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anonymous:
        assert anonymous.get(ENV + "/capture-jobs").status_code in {401, 403}
