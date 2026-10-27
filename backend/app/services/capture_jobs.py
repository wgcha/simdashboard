"""Background capture jobs: Case results are downloaded and parsed after registration (SCX drive).

Issue #55 follow-up (2026-10-22, production: ``DISCOVERY_TIME_BUDGET`` forever for a request with many
Scenes, ``FOLDER_SCHEMA_SCAN_INCOMPLETE ... FINAL (list_dir, DRIVE_READ_NOT_PREPARED)`` for another):
the registration and the folder check used to download every result file inside the same bounded drive
session as the scan.  With a slow drive (~6 s per download, adapter concurrency 1) a request with many
Scenes could never finish inside the 300 s budget, so it was never registered or synced, and no
progress was kept between attempts.

Now, in SCX drive mode (``folder_discovery_environment.captures_deferred``):

* registration, scan, preview and the folder check (refresh snapshot) only **list** folders.  The Cases
  are created right away and each gets one capture job (``folder_environment_capture_jobs``,
  ``PENDING`` = 결과 받는 중); a refresh puts the Cases it would have captured back to ``PENDING``;
* this module's **one worker** runs the jobs, one registration per drive read session, bounded by
  ``SESSION_SECONDS`` (``drive_reads.bounded``) with BACKGROUND priority (below the screens);
* a session interrupted by its deadline / a cancel / a busy drive leaves its job ``PENDING`` (no
  error) for the next pass.  Downloads of these sessions are kept in the server blob store and recorded
  with their drive version token (``drive_reads.run(resumable=True)``), so a continued job does not
  download them again; completed jobs are never redone, and a re-capture of an unchanged file reuses
  its blob.  A job that fails for its files (parse error, path) is ``FAILED`` as before (retry button).

Who runs it: the worker thread (``start``; woken by every registration/refresh that queued jobs, else
every ``IDLE_SECONDS``), the server result auto-sync pass, and the administrator's rescan (its
``CAPTURE`` phase, which reports ``결과 받는 중 n/N``).  A process lock keeps it to one runner at a time,
so the drive sees one capture download stream (``SIMDASH_SCX_MAX_CONCURRENCY``: keep it at 1–2).
Local mode: registration captures inline as before; the worker does nothing.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from ..database_connection import connect

logger = logging.getLogger(__name__)

ACTOR = "system:capture-jobs"
# Drive reads of one registration's session (its jobs one after another).
SESSION_SECONDS = 300.0
# One pass of the background worker / the result auto-sync.
PASS_SECONDS = 600.0
# The worker looks for pending jobs this often without a wake-up (0 or less in the env: no thread).
ENV_IDLE = "SIMDASH_CAPTURE_WORKER_SECONDS"
DEFAULT_IDLE_SECONDS = 60
AUTOSTART = True
LIVE_STATES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")

_run_lock = threading.Lock()
_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


# --- live progress of the running job (in memory, this process only) ------------------------------

_live_lock = threading.Lock()
_live: dict[str, dict[str, Any]] = {}      # job id -> progress of the job the worker runs now
_live_thread: list[int | None] = [None]    # the runner thread: other threads' downloads are not counted


def _on_download(event: str, rel: str, size: int | None) -> None:
    if threading.get_ident() != _live_thread[0]:
        return
    with _live_lock:
        for entry in _live.values():
            if event == "start":
                entry["current_file"] = rel
                entry["current_size"] = size
            else:
                entry["files"] += 1
                entry["bytes"] += int(size or 0)
                if event == "reused":
                    entry["reused_files"] += 1
                entry["current_file"] = None
                entry["current_size"] = None


def _live_begin(job_id: str) -> None:
    """The worker starts (or, after a read round, continues) this job: its progress is kept across the rounds."""
    from .drive import reads as drive_reads

    with _live_lock:
        _live_thread[0] = threading.get_ident()
        _live.setdefault(job_id, {"started_at": _now_iso().isoformat(), "files": 0, "bytes": 0, "reused_files": 0,
                                  "current_file": None, "current_size": None})
    drive_reads.download_listener = _on_download


def _live_end(job_id: str | None = None) -> None:
    """The job ended (or the session did: no ``job_id`` forgets all)."""
    from .drive import reads as drive_reads

    with _live_lock:
        if job_id is None:
            _live.clear()
        else:
            _live.pop(job_id, None)
        idle = not _live
        if idle:
            _live_thread[0] = None
    if idle:
        drive_reads.download_listener = None


def live_progress() -> dict[str, dict[str, Any]]:
    with _live_lock:
        return {key: dict(value) for key, value in _live.items()}


# Plain Korean text of the error codes a capture job can end with (shown in 결과 받기 상태).
ERROR_MESSAGES = {
    "ENVIRONMENT_ROOT_CHANGED": "저장소 루트가 바뀌어 받지 못했습니다. 저장 폴더를 다시 확인한 뒤 다시 시도하세요.",
    "DASHBOARD_CASE_INVALID": "Case 폴더 구조를 확인할 수 없습니다. 폴더 이름과 깊이 스키마를 확인하세요.",
    "CAPTURE_UNEXPECTED_ERROR": "결과를 읽는 중 예상하지 못한 오류가 났습니다. 다시 시도하세요.",
    "DRIVE_READ_NOT_PREPARED": "드라이브 자료를 아직 준비하지 못했습니다. 다시 시도하세요.",
    "SPDM_FILE_BUSY": "다른 프로그램이 파일을 사용 중이어서 받지 못했습니다. 다시 시도하세요.",
    "DRIVE_BUSY": "드라이브가 바빠 잠시 미뤘습니다. 자동으로 다시 시도합니다.",
}


def error_message(code: str | None) -> str | None:
    if not code:
        return None
    return ERROR_MESSAGES.get(code, f"결과를 받지 못했습니다({code}). 다시 시도하고, 반복되면 관리자에게 문의하세요.")


def list_jobs(conn, *, status: str | None = None, limit: int = 300) -> dict[str, Any]:
    """Download status for the administrator: every capture job of live registrations, with totals.

    ``status`` is RUNNING (the job the worker runs now, from the in-memory progress), PENDING (waiting;
    includes interrupted jobs that continue on the next pass), COMPLETED or FAILED.  Jobs are ordered
    running, pending, failed, then completed (newest first).  ``limit`` caps the rows, never the totals.
    """
    live = live_progress()
    marks = ",".join("?" for _ in LIVE_STATES)
    found = conn.execute(
        "SELECT j.id,j.registration_id,j.case_id,j.status,j.error_code,j.created_at,j.updated_at,r.project_id,r.request_id,"
        "r.environment,p.name,a.title,c.relative_path FROM folder_environment_capture_jobs j "
        "JOIN folder_environment_registrations r ON r.id=j.registration_id "
        "LEFT JOIN projects p ON p.id=r.project_id LEFT JOIN analysis_requests a ON a.id=r.request_id "
        f"LEFT JOIN dashboard_cases c ON c.id=j.case_id WHERE r.status IN ({marks})", list(LIVE_STATES)).fetchall()
    order = {"RUNNING": 0, "PENDING": 1, "FAILED": 2, "COMPLETED": 3}
    items: list[dict[str, Any]] = []
    totals = {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0}
    for row in found:
        job_id, stored = str(row[0]), str(row[3])
        state = "RUNNING" if (stored == "RUNNING" or job_id in live) else stored
        totals["total"] += 1
        totals[{"RUNNING": "running", "PENDING": "pending", "COMPLETED": "completed", "FAILED": "failed"}.get(state, "pending")] += 1
        if status and state != status:
            continue
        code = str(row[4]) if row[4] else None
        progress = live.get(job_id)
        items.append({
            "job_id": job_id, "registration_id": str(row[1]), "case_id": str(row[2]), "status": state,
            "error_code": code, "error_message": error_message(code) if state == "FAILED" else None,
            "created_at": row[5], "updated_at": row[6], "project_id": row[7], "request_id": row[8],
            "environment": str(row[9]) if row[9] else None, "project_name": row[10], "request_name": row[11],
            "case_relative_path": row[12],
            "progress": ({"files": progress["files"], "bytes": progress["bytes"], "reused_files": progress["reused_files"],
                          "current_file": progress["current_file"], "current_size": progress["current_size"],
                          "started_at": progress["started_at"]} if progress else None),
        })
    def newest_first(item: dict[str, Any]) -> float:
        stamp = item["updated_at"]
        return -stamp.timestamp() if hasattr(stamp, "timestamp") else 0.0

    items.sort(key=lambda item: (order.get(item["status"], 1), newest_first(item)))
    return {"totals": totals, "items": items[:max(1, int(limit))], "truncated": len(items) > limit,
            "worker_running": _run_lock.locked()}


def counts(conn, *, registration_ids: list[str] | None = None) -> dict[str, int]:
    """Capture jobs of live registrations: total, pending (결과 받는 중), completed, failed."""
    marks = ",".join("?" for _ in LIVE_STATES)
    sql = ("SELECT j.status,count(*) FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
           f"ON r.id=j.registration_id WHERE r.status IN ({marks})")
    params: list[Any] = list(LIVE_STATES)
    if registration_ids is not None:
        if not registration_ids:
            return {"total": 0, "pending": 0, "completed": 0, "failed": 0}
        sql += f" AND r.id IN ({','.join('?' for _ in registration_ids)})"
        params.extend(registration_ids)
    found = {str(status): int(count) for status, count in conn.execute(sql + " GROUP BY j.status", params).fetchall()}
    pending = found.get("PENDING", 0) + found.get("RUNNING", 0)
    return {"total": sum(found.values()), "pending": pending, "completed": found.get("COMPLETED", 0),
            "failed": found.get("FAILED", 0)}


def pending_cases(conn, request_id: str, environment: str) -> set[str]:
    """Dashboard Case ids of one request scope whose capture job is still pending (결과 받는 중)."""
    return {str(row[0]) for row in conn.execute(
        "SELECT DISTINCT j.case_id FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
        "ON r.id=j.registration_id WHERE r.request_id=? AND r.environment=? AND r.status<>'DELETED' "
        "AND j.status IN ('PENDING','RUNNING')", [request_id, str(environment).upper()]).fetchall()}


def failed_cases(conn, request_id: str, environment: str) -> set[str]:
    """Dashboard Case ids of one request scope whose capture job failed (결과 없음, retry offered)."""
    return {str(row[0]) for row in conn.execute(
        "SELECT DISTINCT j.case_id FROM folder_environment_capture_jobs j JOIN folder_environment_registrations r "
        "ON r.id=j.registration_id WHERE r.request_id=? AND r.environment=? AND r.status<>'DELETED' "
        "AND j.status='FAILED'", [request_id, str(environment).upper()]).fetchall()}


def _pending_registrations(conn, only: list[str] | None, skip: set[str]) -> list[str]:
    marks = ",".join("?" for _ in LIVE_STATES)
    found = [str(row[0]) for row in conn.execute(
        "SELECT j.registration_id,min(j.updated_at) FROM folder_environment_capture_jobs j "
        "JOIN folder_environment_registrations r ON r.id=j.registration_id "
        f"WHERE j.status='PENDING' AND r.status IN ({marks}) GROUP BY j.registration_id "
        "ORDER BY min(j.updated_at), j.registration_id", list(LIVE_STATES)).fetchall()]
    return [item for item in found if item not in skip and (only is None or item in only)]


def _ensure_snapshot(registration_id: str) -> None:
    """Listings first: a registration without a later folder check gets one now (``folder_auto_sync``).

    The jobs then take their context from that snapshot and each session lists only its Case folders,
    instead of re-listing the whole request tree for the registration's own context in every session.
    A failed check is fine: the jobs fall back to the registration's context.
    """
    from . import folder_auto_sync, folder_discovery, folder_schema_resolver as resolver
    from .folder_discovery_scan import root_identity

    with connect() as conn:
        row = conn.execute("SELECT project_id,request_id,environment,created_at FROM folder_environment_registrations "
                           "WHERE id=?", [registration_id]).fetchone()
        if not row or not row[0] or not row[1]:
            return
        try:
            active = resolver.active_refresh_snapshot(conn, root_identity(folder_discovery.configured_root(conn)),
                                                      str(row[0]), str(row[1]), str(row[2]).upper())
        except Exception:  # noqa: BLE001 - an unreadable snapshot is replaced by the check below
            active = None
    if active and active.get("created_at") and row[3] and active["created_at"] > row[3]:
        return
    folder_auto_sync.sync_scope(str(row[0]), str(row[1]), str(row[2]).upper(), ACTOR, force=True)


def _run_registration(registration_id: str, deadline: float, tally: dict[str, int],
                      should_stop: Callable[[], bool] | None = None) -> None:
    """The PENDING jobs of one registration in one bounded, resumable drive read session."""
    from . import folder_discovery, folder_discovery_environment as environment_service
    from .drive import reads as drive_reads

    with drive_reads.bounded(deadline=deadline):
        _ensure_snapshot(registration_id)
    if drive_reads.stop_reason() is not None or time.monotonic() >= deadline:
        return

    stopped: set[str] = set()    # jobs interrupted in this session (kept across read rounds)
    outcomes: dict[str, str] = {}

    def body() -> None:
        cache: dict[Any, Any] = {}
        with connect() as conn:
            root = folder_discovery.configured_root(conn)
            while True:
                if should_stop is not None and should_stop():
                    return   # e.g. the administrator's rescan started: it runs the jobs itself
                marks = ",".join("?" for _ in stopped) or "''"
                row = conn.execute(
                    "SELECT id FROM folder_environment_capture_jobs WHERE registration_id=? AND status='PENDING' "
                    f"AND id NOT IN ({marks}) ORDER BY updated_at,id LIMIT 1", [registration_id, *stopped]).fetchone()
                if row is None:
                    return
                job_id = str(row[0])
                _live_begin(job_id)   # a read miss repeats this body: the progress of the round stays
                result = environment_service.capture_job(conn, job_id, root, actor=ACTOR, cache=cache)
                _live_end(job_id)
                outcomes[job_id] = str(result.get("status"))
                if result.get("status") == "INTERRUPTED":
                    stopped.add(job_id)
                    if drive_reads.stop_reason() is not None:
                        return   # the session's time is used up: the rest waits for the next pass
                elif result.get("status") == "SKIPPED":
                    stopped.add(job_id)

    with connect() as conn:
        jobs = int(conn.execute("SELECT count(*) FROM folder_environment_capture_jobs WHERE registration_id=? "
                                "AND status='PENDING'", [registration_id]).fetchone()[0])
    # One read round per Case (its result files) plus the request tree and slack.
    rounds = drive_reads.MAX_ROUNDS + 2 * jobs
    try:
        with drive_reads.bounded(deadline=deadline):
            drive_reads.run(body, priority="BACKGROUND", rounds=rounds, resumable=True)
    finally:
        _live_end()
    for status in outcomes.values():
        key = {"COMPLETED": "completed_now", "FAILED": "failed_now", "INTERRUPTED": "interrupted"}.get(status)
        if key:
            tally[key] += 1


def run_pending(*, budget_seconds: float = PASS_SECONDS, session_seconds: float = SESSION_SECONDS,
                wait_seconds: float = 0.0, cancel: threading.Event | None = None,
                observer: Callable[[dict[str, Any]], None] | None = None,
                registration_ids: list[str] | None = None,
                should_stop: Callable[[], bool] | None = None,
                progress: Callable[[dict[str, int]], None] | None = None) -> dict[str, Any]:
    """Run pending capture jobs until none is left or ``budget_seconds`` is used (never raises).

    One runner per process (``wait_seconds``: how long to wait for another one).  Returns what this run
    did (``completed_now``, ``failed_now``, ``interrupted``) and the counts after it (:func:`counts`;
    ``pending`` = 결과 받는 중 left for the next pass).
    """
    from .drive import reads as drive_reads

    tally = {"completed_now": 0, "failed_now": 0, "interrupted": 0}
    if not drive_reads.active():
        return {"status": "DISABLED", **tally}
    end = time.monotonic() + max(0.0, float(wait_seconds))
    acquired = False
    while not acquired:
        acquired = _run_lock.acquire(timeout=0.5)
        if acquired or time.monotonic() >= end or (cancel is not None and cancel.is_set()):
            break
    if not acquired:
        return {"status": "BUSY", **tally}
    status = "DONE"
    try:
        deadline = time.monotonic() + max(0.0, float(budget_seconds))
        tried: set[str] = set()
        while True:
            if (cancel is not None and cancel.is_set()) or (should_stop is not None and should_stop()):
                status = "STOPPED"
                break
            if time.monotonic() >= deadline - 1.0:
                status = "TIME_LIMIT"
                break
            with connect() as conn:
                waiting = _pending_registrations(conn, registration_ids, tried)
            if not waiting:
                break
            registration_id = waiting[0]
            tried.add(registration_id)
            try:
                with drive_reads.bounded(cancel=cancel, observer=observer):
                    _run_registration(registration_id, min(deadline, time.monotonic() + session_seconds), tally,
                                      should_stop)
            except Exception as exc:  # noqa: BLE001 - one registration never stops the others
                if getattr(exc, "code", None) == "DRIVE_BUSY":
                    # Issue #51: another background drive activity kept the worker; the jobs stay pending.
                    logger.info("Capture jobs of registration %s deferred: the drive is busy", registration_id)
                    status = "BUSY"
                    break
                logger.warning("Capture jobs of registration %s failed", registration_id, exc_info=True)
            if progress is not None:
                progress(dict(tally))
    finally:
        _run_lock.release()
    with connect() as conn:
        left = counts(conn, registration_ids=registration_ids)
    if any(tally.values()):
        logger.info("Capture jobs: %(completed_now)d completed, %(failed_now)d failed, %(interrupted)d interrupted",
                    tally)
    return {"status": status, **tally, **left}


# --- worker thread -------------------------------------------------------------------------------

def idle_seconds() -> int:
    raw = (os.getenv(ENV_IDLE) or "").strip()
    try:
        return int(float(raw)) if raw else DEFAULT_IDLE_SECONDS
    except ValueError:
        return DEFAULT_IDLE_SECONDS


def wake() -> None:
    """New pending jobs: the worker starts its next pass now (no-op without a worker thread)."""
    _wake.set()


def _loop(idle: int) -> None:
    from . import storage_rescan

    while not _stop.is_set():
        _wake.wait(idle)
        _wake.clear()
        if _stop.is_set():
            return
        if storage_rescan.running():
            continue   # the rescan runs the jobs in its CAPTURE phase
        try:
            cancel = storage_rescan.yield_signal(_stop)
            result = run_pending(cancel=cancel, should_stop=storage_rescan.running)
            if result.get("status") == "DONE":
                # Case results first; then the 소재물성 decks of the registered requests (issue #55).
                from . import materials_preparation

                materials_preparation.run_pending(cancel=cancel, should_stop=storage_rescan.running)
        except Exception:  # noqa: BLE001 - the thread never dies
            logger.warning("Capture worker pass failed", exc_info=True)


# Failures that a later version no longer makes: retried once when the server starts (no user action).
# DASHBOARD_CASE_INVALID: before 0.3.1.6 a Case with prefixed load-case folders (``completed_Drop``) was refused.
RETRY_ON_START_CODES = ("DASHBOARD_CASE_INVALID",)


def retry_fixed_failures(conn) -> int:
    """FAILED jobs of live registrations whose error a newer version fixed -> PENDING (returns the count)."""
    marks = ",".join("?" for _ in LIVE_STATES)
    codes = ",".join("?" for _ in RETRY_ON_START_CODES)
    cursor = conn.execute(
        "UPDATE folder_environment_capture_jobs SET status='PENDING',error_code=NULL,updated_at=? "
        f"WHERE status='FAILED' AND error_code IN ({codes}) AND registration_id IN "
        f"(SELECT id FROM folder_environment_registrations WHERE status IN ({marks}))",
        [_now_iso(), *RETRY_ON_START_CODES, *LIVE_STATES])
    return int(getattr(cursor, "rowcount", 0) or 0)


def _now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)   # same naive UTC as the other job writes


def start() -> bool:
    """Start the single capture worker thread (SCX drive mode only; idempotent)."""
    global _thread
    from .drive import reads as drive_reads

    idle = idle_seconds()
    if not AUTOSTART or idle <= 0 or not drive_reads.active():
        return False
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        try:
            with connect() as conn:
                retried = retry_fixed_failures(conn)
            if retried:
                logger.warning("Capture jobs: %d failed job(s) queued again (fixed in this version)", retried)
        except Exception:  # noqa: BLE001 - never blocks the worker
            logger.warning("Capture jobs: re-queueing fixed failures failed", exc_info=True)
        _wake.set()   # pending jobs of the last run continue after a restart
        _thread = threading.Thread(target=_loop, args=(idle,), name="capture-jobs", daemon=True)
        _thread.start()
    logger.info("Capture worker started (idle check every %d s)", idle)
    return True


def stop(timeout: float = 10.0) -> None:
    global _thread
    _stop.set()
    _wake.set()
    with _thread_lock:
        thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout)
