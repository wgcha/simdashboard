"""Issue #55 (2026-10-21): the full rescan never waits unbounded, reports live progress, can be cancelled;
a drive file whose name this Windows server cannot hold is one reported item, not a failed request sync.

Production evidence: ``GET .../rescan`` 14 minutes after [저장 폴더 새로고침] still answered
``status: RUNNING, phase: DISCOVER, rounds: 0, progress 0/0`` while ``list_dir`` calls failed with
``DRIVE_BUSY queue wait timeout``; a request sync failed as a whole with ``SPDM_INVALID_PATH``
("이 서버에서 쓸 수 없는 이름입니다"). Everything here runs on the in-memory fake adapter.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

import pytest

from app.database_connection import connect
from app.services import folder_auto_discovery, result_auto_sync, storage_rescan
from app.services.drive import reads as drive_reads
from app.services.storage.drive import DriveRoot, DriveStorageProvider
from app.services.storage.provider import SpdmStorageError
from drive_fakes import ErrorCode
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_discovery_tree import CONTAINER, _request_tree
from tests.test_drive_reads import CSV, CSV_BYTES, ENV, OPTION, _register, _scenes, _sync, scx  # noqa: F401
from tests.test_folder_auto_discovery import _container_schema

RESCAN = ENV + "/rescan"


@pytest.fixture(autouse=True)
def _rescan(monkeypatch):
    folder_auto_discovery.reset_for_tests()
    storage_rescan.reset_for_tests()
    monkeypatch.setattr(folder_auto_discovery, "MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(folder_auto_discovery, "FORCE_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(storage_rescan, "INLINE", True)
    monkeypatch.setattr(drive_reads, "BUSY_BACKOFF_SECONDS", (0.01, 0.01))
    yield
    storage_rescan.reset_for_tests()
    folder_auto_discovery.reset_for_tests()


def _wait_for(check, seconds: float = 30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not reached in time")


def _big_drive(scx, projects: int = 6, requests: int = 2) -> list[str]:
    tag = uuid.uuid4().hex[:5]
    paths = []
    for number in range(projects):
        project = f"{CONTAINER}/PROJ-{tag}{number:02d}_과제"
        scx.drive.add_dir(scx.path(f"{project}/CAD"))
        for index in range(requests):
            request = f"{project}/WR-{tag}{number:02d}{index}_유통 환경"
            _request_tree(scx, request, scenes=("2_Face",))
            paths.append(request)
    return paths


def test_rescan_on_a_slow_busy_drive_ends_within_its_phase_deadlines_and_logs_progress(scx, monkeypatch, caplog):
    """Reproduces the stuck DISCOVER: speculative listings of every project on a slow worker whose queue
    times out (DRIVE_BUSY). Before the fix the first round listed ~LIST_PREFETCH_MAX folders with no time
    bound, so ``rounds`` stayed 0; now every phase ends at its deadline and the run reports where it was."""
    _container_schema()
    paths = _big_drive(scx)
    busy = paths[0].rsplit("/", 1)[0] + "/CAD"
    scx.drive.fail_path[("list_dir", scx.path(busy))] = [ErrorCode.OVERLOADED] * 200
    scx.drive.call_delay_s["list_dir"] = 0.03
    monkeypatch.setattr(storage_rescan, "DISCOVER_BUDGET_SECONDS", 2.0)
    monkeypatch.setattr(storage_rescan, "ROUND_BUDGET_SECONDS", 1.0)
    monkeypatch.setattr(storage_rescan, "SYNC_BUDGET_SECONDS", 1.5)
    monkeypatch.setattr(storage_rescan, "SCOPE_BUDGET_SECONDS", 1.0)
    monkeypatch.setattr(folder_auto_discovery, "DRIVE_READ_SLACK_SECONDS", 0.3)
    monkeypatch.setattr(storage_rescan, "PROGRESS_LOG_SECONDS", 0.2)
    caplog.set_level(logging.WARNING)

    started = time.monotonic()
    assert scx.post(RESCAN, {})["started"] is True
    elapsed = time.monotonic() - started
    state = scx.client.get(RESCAN).json()

    assert state["status"] == "DONE", state
    # DISCOVER 2 s + SYNC 1.5 s + one drive call / backoff each; far below the ~30 s the listings need.
    assert elapsed < 12, elapsed
    assert state["rounds"] >= 1 and state["elapsed_seconds"] >= 1
    drive = state["drive"]
    assert drive["listings"] > 0 and drive["calls"] >= drive["listings"]
    assert drive["last_error"] and drive["last_error"]["code"] in {"DRIVE_BUSY", "DRIVE_READ_DEADLINE"} or drive["errors"] == 0
    progress_lines = [record.getMessage() for record in caplog.records
                      if record.getMessage().startswith("Storage rescan progress:")]
    assert progress_lines, "a progress line is logged while the rescan runs"
    assert "phase=" in progress_lines[0] and "listings=" in progress_lines[0] and "elapsed=" in progress_lines[0]
    assert any(record.getMessage().startswith("Storage rescan done:") for record in caplog.records)


def test_busy_listing_is_retried_with_backoff_then_reported_with_its_path(scx, monkeypatch):
    _container_schema()
    paths = _big_drive(scx, projects=1, requests=2)
    flaky, stuck = paths[0], paths[1]
    scx.drive.fail_path[("list_dir", scx.path(flaky))] = [ErrorCode.OVERLOADED]           # one busy answer
    scx.drive.fail_path[("list_dir", scx.path(stuck))] = [ErrorCode.OVERLOADED] * 200     # always busy

    assert scx.post(RESCAN, {})["started"] is True
    state = scx.client.get(RESCAN).json()
    assert state["status"] == "DONE", state
    assert [item["name"] for item in state["created_requests"]] == [flaky.rsplit("/", 1)[-1]], state
    review = next(item for item in state["needs_review"] if item["relative_path"] == stuck)
    assert review.get("drive_error_code") == "DRIVE_BUSY" or "DRIVE_BUSY" in review["reason"], review
    assert state["drive"]["retries"] >= 3, state["drive"]
    assert state["drive"]["last_error"]["code"] == "DRIVE_BUSY", state["drive"]


def test_cancel_stops_a_running_rescan_at_the_next_check_point(scx, monkeypatch):
    _container_schema()
    _big_drive(scx, projects=4, requests=2)
    scx.drive.call_delay_s["list_dir"] = 0.15
    monkeypatch.setattr(storage_rescan, "INLINE", False)

    assert scx.post(RESCAN, {})["started"] is True
    running = _wait_for(lambda: (lambda s: s if s["drive"]["calls"] >= 2 else None)(scx.client.get(RESCAN).json()))
    assert running["status"] == "RUNNING" and running["phase"] == "DISCOVER"
    assert set(running["progress"]) >= {"step", "current_path", "projects_done", "projects_total"}

    cancelled = scx.post(RESCAN + "/cancel", {})
    assert cancelled["requested"] is True and cancelled["cancel_requested"] is True
    asked = time.monotonic()
    final = _wait_for(lambda: (lambda s: s if s["status"] != "RUNNING" else None)(scx.client.get(RESCAN).json()), 15)
    assert final["status"] == "CANCELLED", final
    assert time.monotonic() - asked < 5, "cancel takes effect at the next drive call / check point"
    assert final["error"] is None and final["finished_at"]
    assert scx.post(RESCAN + "/cancel", {})["requested"] is False
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='STORAGE_RESCAN_CANCELLED'").fetchone()[0] >= 1


def test_screens_discovery_and_auto_sync_stay_off_the_drive_while_a_rescan_runs(scx):
    _container_schema()
    _big_drive(scx, projects=1, requests=1)
    with storage_rescan._lock:
        storage_rescan._state.update(storage_rescan._initial("admin"), status="RUNNING", phase="DISCOVER")
    try:
        calls = len(scx.drive.calls)
        body = scx.post(ENV + "/discover", {"force": True})
        assert body["status"] == "RUNNING" and body["created_requests"] == [] and body["coalesced"] is True
        assert result_auto_sync.run_once()["skipped"] == "STORAGE_RESCAN_RUNNING"
        assert len(scx.drive.calls) == calls, "no drive read while the rescan runs"
    finally:
        storage_rescan.reset_for_tests()
    assert scx.post(ENV + "/discover", {"force": True})["created_requests"], "after the rescan the screens discover again"


def test_lock_wait_is_bounded_and_cancellable(monkeypatch):
    """A discovery run that holds the lock: the rescan waits ROUND_WAIT_SECONDS at most, or stops on [중지]."""
    monkeypatch.setattr(storage_rescan, "ROUND_WAIT_SECONDS", 0.3)
    assert folder_auto_discovery._run_lock.acquire(timeout=5)
    try:
        started = time.monotonic()
        storage_rescan.start("admin")
        state = storage_rescan.state()
        assert state["status"] == "FAILED" and "0초" in state["error"] and time.monotonic() - started < 3, state

        monkeypatch.setattr(storage_rescan, "ROUND_WAIT_SECONDS", 60.0)
        monkeypatch.setattr(storage_rescan, "INLINE", False)
        storage_rescan.start("admin")
        time.sleep(0.2)
        assert storage_rescan.cancel("admin")[1] is True
        asked = time.monotonic()
        _wait_for(lambda: storage_rescan.state()["status"] != "RUNNING", 5)
        assert storage_rescan.state()["status"] == "CANCELLED" and time.monotonic() - asked < 3
    finally:
        folder_auto_discovery._run_lock.release()


def test_bounded_reads_stop_before_a_drive_call_and_end_the_read_rounds(scx):
    root = DriveRoot(scx.root, "scx.example.test")
    fs = DriveStorageProvider(root)
    scx.add("A/B/x.csv")
    calls = len(scx.drive.calls)
    with drive_reads.bounded(deadline=time.monotonic() - 1):
        with pytest.raises(SpdmStorageError) as caught:
            fs.list("A")
        assert caught.value.code == drive_reads.READ_DEADLINE
        rounds: list[bool] = []

        def body():
            rounds.append(drive_reads.final_round())
            with connect():
                try:
                    return [entry.name for entry in fs.list("A")]
                except SpdmStorageError as error:
                    return error.code

        assert drive_reads.run(body, rounds=5) == drive_reads.NOT_PREPARED
        assert rounds == [True], "an ended bound makes the first read round the last one"
    cancel = threading.Event()
    cancel.set()
    with drive_reads.bounded(cancel=cancel):
        with pytest.raises(SpdmStorageError) as caught:
            fs.list("A")
        assert caught.value.code == drive_reads.READ_CANCELLED
    assert len(scx.drive.calls) == calls, "no drive call after the bounds ended"
    assert [entry.name for entry in fs.list("A")] == ["B"]


# --- SPDM_INVALID_PATH: one offending file is a reported item (task 2) -----------------------------------

def test_file_name_the_server_cannot_hold_is_one_unprocessed_item_not_a_failed_sync(scx, caplog):
    project_id, request_id = _register(scx)
    _sync(scx, project_id, request_id)
    bad = f"{OPTION}/7_Face/MAX_RESULT_bad?name.csv"          # '?' is forbidden on Windows
    twin = f"{OPTION}/7_Face/{CSV.upper()}"                  # only a case twin: stageable in its own folder
    colon = f"{OPTION}/7_Face/colon:name.csv"               # ':' (NTFS stream syntax) as well
    scx.add(f"{OPTION}/7_Face/{CSV}")
    scx.add(bad)
    scx.add(colon)
    scx.add(twin, CSV_BYTES)
    scx.drive.unsafe_local.update({scx.path(bad), scx.path(colon), scx.path(twin)})
    caplog.set_level(logging.WARNING)

    result = _sync(scx, project_id, request_id)
    assert (result["status"], result["changed"]) == ("REFRESHED", True), result
    assert "7_Face" in _scenes(scx, request_id)
    assert not {scx.path(bad), scx.path(colon)} & set(scx.drive.downloads()), "a name Windows cannot hold is never staged"
    assert scx.path(twin) in scx.drive.downloads(), "a case twin is downloaded into its own staging folder"
    assert any("bad?name.csv" in record.getMessage() for record in caplog.records), "the log names the file"
    with connect() as conn:
        issues = [str(row[0]) for row in conn.execute(
            "SELECT c.payload_json FROM dashboard_captures c JOIN dashboard_cases d ON d.id=c.case_id "
            "WHERE d.request_id=?", [request_id]).fetchall()]
    assert any("NAME_NOT_LOCAL_SAFE" in item and "bad?name.csv" in item for item in issues), issues


def test_content_token_needs_no_local_name_but_a_local_read_names_the_file(scx):
    root = DriveRoot(scx.root, "scx.example.test")
    fs = DriveStorageProvider(root)
    scx.add("A/odd?name.csv")
    scx.drive.unsafe_local.add(scx.path("A/odd?name.csv"))
    assert fs.content_token("A/odd?name.csv").startswith("sha1:")
    with pytest.raises(SpdmStorageError) as caught:
        fs.read_stable("A/odd?name.csv")
    assert caught.value.code == "SPDM_INVALID_PATH" and "A/odd?name.csv" in str(caught.value)


def test_background_drive_work_steps_aside_once_a_rescan_runs(monkeypatch):
    """Issue #51 (2026-10-23 18:22): a rescan waited 3 min at ROUND_START behind a capture job's session."""
    shutdown = threading.Event()
    signal = storage_rescan.yield_signal(shutdown)
    running = {"on": False}
    monkeypatch.setattr(storage_rescan, "running", lambda: running["on"])
    with drive_reads.bounded(cancel=signal):
        assert drive_reads.stop_reason() is None
        running["on"] = True
        assert drive_reads.stop_reason() == drive_reads.READ_CANCELLED
        running["on"] = False
        shutdown.set()
        assert drive_reads.stop_reason() == drive_reads.READ_CANCELLED
