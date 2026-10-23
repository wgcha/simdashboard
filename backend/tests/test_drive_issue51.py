"""Issue #51 (2026-10-23): token refresh robustness, cached worker health and one background drive activity.

Runs against the in-memory fake adapter (``drive_fakes``) and an isolated test database with synthetic
folders; no real SDK, SSO, drive, user database or running service is touched.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import pytest
import test_drive_foundation as foundation
import test_drive_reads as reads_tests
from app.database_connection import connect
from app.services import folder_auto_sync
from app.services.drive import auth as drive_auth
from app.services.drive import gateway as drive_gateway
from app.services.drive import lanes as drive_lanes
from app.services.drive import reads as drive_reads
from app.services.drive.token_store import DbTokenStore
from app.services.storage.provider import SpdmStorageError
from drive_fakes import DriveError, ErrorCode, FakeWorkerDriveGateway

_reset_drive_state = foundation._reset_drive_state
password_auth = foundation.password_auth
scx_env = foundation.scx_env
fake_drive = foundation.fake_drive
scx = reads_tests.scx

pytestmark = pytest.mark.duckdb_integration


@pytest.fixture(autouse=True)
def _fresh_lanes():
    drive_lanes.reset_for_tests()
    drive_auth.reset_state()
    yield
    drive_lanes.reset_for_tests()
    drive_auth.reset_state()


def _register_token() -> DbTokenStore:
    store = drive_gateway.token_store()
    bundle = DbTokenStore.make_bundle(server_url=foundation.SERVER, access_token=foundation.ACCESS,
                                      refresh_token=foundation.REFRESH, obtained_at=datetime.now(timezone.utc),
                                      account_hint="spdm-shared")
    store.write(bundle, updated_by="drive-admin", remember_account=True)
    return store


def _ciphertext() -> bytes | None:
    with connect() as conn:
        row = conn.execute("SELECT ciphertext FROM drive_credentials WHERE id='scx'").fetchone()
    return bytes(row[0]) if row else None


# --- 1. the dashboard's TokenStore.load() never answers None for a passing DB problem ----------------

def test_database_error_during_load_serves_the_stored_token_also_to_a_new_gateway_store(password_auth, fake_drive,
                                                                                         monkeypatch):
    store = _register_token()
    assert store.load_local().refresh_token == foundation.REFRESH

    def broken():
        raise RuntimeError("QueuePool limit reached, connection timed out")

    monkeypatch.setattr("app.services.drive.token_store.LOAD_RETRY_PAUSES", (0.0,))
    # A restarted worker gets a fresh gateway store (``init`` sends its load()); the database is busy.
    fresh = DbTokenStore(store._secret_key, connect_fn=broken, status=store._status, write_lock=store._lock)
    loaded = fresh.load_local()
    assert loaded is not None and loaded.refresh_token == foundation.REFRESH
    assert fresh.loaded_generation is not None and store.last_load_problem == "DB_ERROR"
    store.clear()                                 # the admin deleted the token: nothing is served any more
    assert fresh.load_local() is None


def test_a_rotation_without_refresh_token_or_access_token_never_clears_the_stored_refresh_token(password_auth,
                                                                                               fake_drive):
    store = _register_token()
    gateway_store = store.for_gateway()
    assert gateway_store.load_local() is not None
    later = datetime.now(timezone.utc)
    gateway_store.save(DbTokenStore.make_bundle(server_url=foundation.SERVER, access_token="eyJ.new-access",
                                                refresh_token=None, obtained_at=later, account_hint="spdm-shared"))
    kept = store.load_local()
    assert (kept.access_token, kept.refresh_token) == ("eyJ.new-access", foundation.REFRESH)
    before = _ciphertext()
    gateway_store.save(DbTokenStore.make_bundle(server_url=foundation.SERVER, access_token="", refresh_token=None,
                                                obtained_at=later, account_hint="spdm-shared"))
    assert _ciphertext() == before


# --- 1. transient refresh failures keep the episode and the stored token -----------------------------

def test_a_502_refresh_is_retryable_and_keeps_the_stored_token(password_auth, fake_drive, caplog):
    _register_token()
    gateway = drive_gateway.get_drive_gateway()
    before = _ciphertext()
    fake_drive.auth_fail_next["refresh_now"] = ErrorCode.UNAVAILABLE      # SSO answered 502 BAD_GATEWAY
    with caplog.at_level(logging.WARNING):
        assert drive_auth.auto_refresh(gateway) == drive_auth.TRANSIENT
    assert _ciphertext() == before                                         # nothing cleared or replaced
    calls = len(fake_drive.auth_calls)
    assert drive_auth.auto_refresh(gateway) == drive_auth.TRANSIENT       # backoff: the SSO is not asked again
    assert len(fake_drive.auth_calls) == calls
    drive_auth._status.transient_until = time.monotonic() - 1               # backoff over
    assert drive_auth.auto_refresh(gateway) == drive_auth.REFRESHED        # the episode was not used up
    assert "failed transiently" in caplog.text


def test_only_definitive_refusals_count_as_re_login_required():
    def failure(code: str, detail: str = "") -> drive_auth.DriveAuthError:
        error = drive_auth.DriveAuthError(409, code, "x")
        error.detail = detail
        return error

    assert drive_auth.refresh_failure_transient(failure("DRIVE_UNAVAILABLE", 'BAD_GATEWAY {"status": 502}'))
    assert drive_auth.refresh_failure_transient(failure("DRIVE_TIMEOUT"))
    assert drive_auth.refresh_failure_transient(failure("RATE_LIMITED"))
    assert drive_auth.refresh_failure_transient(failure("AUTH_REQUIRED", "refresh failed: 502 Bad Gateway"))
    assert not drive_auth.refresh_failure_transient(failure("AUTH_REFRESH_EXPIRED", "invalid_grant"))
    assert not drive_auth.refresh_failure_transient(failure("AUTH_INVALID_CREDENTIALS"))


def test_worker_without_a_token_is_restarted_from_the_stored_token(password_auth, fake_drive, monkeypatch):
    _register_token()
    old = drive_gateway.get_drive_gateway()

    def lost(self):
        raise DriveError(ErrorCode.AUTH_REFRESH_EXPIRED, "no stored token to refresh")

    monkeypatch.setattr(type(old), "refresh_now", lost)
    before = _ciphertext()
    assert drive_auth.auto_refresh(old) == drive_auth.REFRESHED
    assert old.closed and drive_gateway.existing_gateway() is None   # the next call builds a new worker
    assert _ciphertext() == before
    monkeypatch.undo()
    assert drive_gateway.get_drive_gateway().stat("") is not None      # init loads the stored token


# --- 2. cached worker health ---------------------------------------------------------------------

def test_status_polls_ask_the_worker_for_health_once_per_window_and_never_build_it(scx, monkeypatch):
    calls = []
    original = FakeWorkerDriveGateway.health
    monkeypatch.setattr(FakeWorkerDriveGateway, "health", lambda self: calls.append(1) or original(self))
    drive_gateway.forget_health()
    for _ in range(5):
        assert scx.client.get("/api/drive/status").status_code == 200
        assert scx.client.get("/api/admin/drive/status").status_code == 200
    assert len(calls) == 1
    drive_gateway.reset_gateway()
    assert scx.client.get("/api/drive/status").json()["state"] in {"UNKNOWN", "UNAVAILABLE"}
    assert drive_gateway.existing_gateway() is None and len(calls) == 1


# --- 3. one background activity, screens first, DRIVE_BUSY is "check again later" ----------------------

def test_call_slot_serves_interactive_calls_first_and_times_out_as_drive_busy():
    order: list[str] = []
    held = threading.Event()
    release = threading.Event()

    def holder():
        with drive_lanes.call_slot(drive_lanes.BACKGROUND):
            held.set()
            release.wait(5)

    def waiter(priority: str):
        with drive_lanes.call_slot(priority, timeout=5):
            order.append(priority)

    first = threading.Thread(target=holder)
    first.start()
    held.wait(5)
    with pytest.raises(SpdmStorageError) as busy:
        with drive_lanes.call_slot(drive_lanes.BACKGROUND, timeout=0.1):
            pass
    assert busy.value.code == "DRIVE_BUSY"
    background = threading.Thread(target=waiter, args=(drive_lanes.BACKGROUND,))
    background.start()
    time.sleep(0.1)
    interactive = threading.Thread(target=waiter, args=(drive_lanes.INTERACTIVE,))
    interactive.start()
    time.sleep(0.1)
    release.set()
    for thread in (first, background, interactive):
        thread.join(5)
    assert order == [drive_lanes.INTERACTIVE, drive_lanes.BACKGROUND]
    assert drive_lanes.stats()["max_in_flight"] == 1


def test_background_activities_never_overlap():
    active, overlaps = [0], []

    def activity():
        with drive_lanes.background_activity("t", timeout=5):
            with drive_lanes.background_activity("nested", timeout=0):   # same thread: re-entrant
                active[0] += 1
                overlaps.append(active[0])
                time.sleep(0.02)
                active[0] -= 1

    threads = [threading.Thread(target=activity) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert overlaps == [1] * 5


def test_folder_sync_waits_for_the_running_background_activity_and_reports_drive_busy(scx, monkeypatch, caplog):
    project_id, request_id = reads_tests._register(scx)
    monkeypatch.setattr(folder_auto_sync, "SYNC_ACTIVITY_WAIT_SECONDS", 0.2)
    held, release = threading.Event(), threading.Event()

    def capture_worker():
        with drive_lanes.background_activity("capture"):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=capture_worker)
    thread.start()
    held.wait(5)
    try:
        with caplog.at_level(logging.INFO, logger="app.services.folder_auto_sync"):
            result = folder_auto_sync.sync_scope(project_id, request_id, "DISTRIBUTION", "test", force=True)
    finally:
        release.set()
        thread.join(5)
    assert (result["status"], result["code"], result.get("retry")) == ("FAILED", "DRIVE_BUSY", True), result
    assert "잠시 후 다시 확인" in result["message"]
    assert "Folder sync FAILED" not in caplog.text and "deferred (drive busy" in caplog.text
    after = folder_auto_sync.sync_scope(project_id, request_id, "DISTRIBUTION", "test", force=True)
    assert after["status"] in {"UNCHANGED", "REFRESHED"}, after


def test_worker_queue_timeout_during_the_scan_is_drive_busy_not_a_schema_failure(scx, monkeypatch):
    project_id, request_id = reads_tests._register(scx)
    monkeypatch.setattr(drive_reads, "BUSY_BACKOFF_SECONDS", (0.01,))
    scx.drive.fail_always["list_dir"] = ErrorCode.OVERLOADED          # "queue wait timeout"
    result = folder_auto_sync.sync_scope(project_id, request_id, "DISTRIBUTION", "test", force=True)
    assert (result["status"], result["code"]) == ("FAILED", "DRIVE_BUSY"), result
    scx.drive.fail_always.clear()
    assert folder_auto_sync.sync_scope(project_id, request_id, "DISTRIBUTION", "test", force=True)["status"] in {
        "UNCHANGED", "REFRESHED"}
