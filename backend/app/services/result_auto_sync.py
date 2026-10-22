"""Server-side result auto-sync (user decision 2026-10-14: results appear without anyone viewing them).

Every ``SIMDASH_RESULT_AUTO_SYNC_SECONDS`` (default 300 s = 5 minutes; 0 turns it off;
values below 60 are raised to 60) one background thread:

1. registers new SPDM project/request folders (``folder_auto_discovery.discover``), then
2. checks every request with a live folder registration and environment
   (``folder_auto_sync.sync_scope``), the same quick-then-full check the screens use;
3. SCX drive: wakes the capture worker (``capture_jobs``), which downloads the result files of the
   Cases these steps queued (결과 받는 중) in its own bounded drive sessions.

A pass is skipped while the administrator's full rescan (``storage_rescan``) runs: it does both
steps itself, and a parallel pass only competes with it for the drive worker (issue #55).

New Cases/Scenes become one ``NEW_RESULTS`` notification per request (folder sync), sent to every
active user who may view the project.  The thread never holds a DB connection while it reads the
drive (``sync_scope`` delegates to ``sync_drive`` in scx mode), runs the scopes one after another
and never raises: failures are logged once per scope and code by ``folder_auto_sync.log_outcome``.
The screens' 60-second polling and [새 결과 확인] share the same per-scope coalescing, so the
server run and viewers never scan one request twice in a row.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from ..database_connection import connect

logger = logging.getLogger(__name__)

ACTOR = "system:auto-sync"
ENV_INTERVAL = "SIMDASH_RESULT_AUTO_SYNC_SECONDS"
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
FIRST_RUN_DELAY_SECONDS = 30.0
# Tests and tools turn the background thread off; the run itself stays callable (``run_once``).
AUTOSTART = True


def interval_seconds() -> int:
    raw = (os.getenv(ENV_INTERVAL) or "").strip()
    if not raw:
        return DEFAULT_INTERVAL_SECONDS
    try:
        value = int(float(raw))
    except ValueError:
        logger.warning("%s=%r is not a number; using %d s", ENV_INTERVAL, raw, DEFAULT_INTERVAL_SECONDS)
        return DEFAULT_INTERVAL_SECONDS
    if value <= 0:
        return 0
    return max(MIN_INTERVAL_SECONDS, value)


def run_once(stop_event: threading.Event | None = None) -> dict[str, Any]:
    """One pass over all registered request scopes; returns counts (never raises).

    ``stop_event`` (the background thread's) ends the pass early at shutdown."""
    from . import folder_auto_discovery, folder_auto_sync, storage_rescan

    summary = {"scopes": 0, "refreshed": 0, "failed": 0, "discovered": 0}
    if storage_rescan.running():
        # Issue #55 (2026-10-21): the administrator's rescan discovers and checks every request itself;
        # a parallel pass only competes with it for the drive worker (DRIVE_BUSY queue wait timeouts).
        summary["skipped"] = "STORAGE_RESCAN_RUNNING"
        return summary
    try:
        discovered = folder_auto_discovery.discover()
        summary["discovered"] = len(discovered.get("created_requests") or []) + len(discovered.get("created_projects") or [])
    except Exception:  # noqa: BLE001 - discovery problems must not stop the result checks
        logger.warning("Result auto-sync: folder discovery failed", exc_info=True)
    try:
        with connect() as conn:
            scopes = folder_auto_sync.registered_scopes(conn)
    except Exception:  # noqa: BLE001
        logger.warning("Result auto-sync: could not list registered requests", exc_info=True)
        return summary
    for project_id, request_id, environment in scopes:
        if stop_event is not None and stop_event.is_set():
            break
        summary["scopes"] += 1
        try:
            result = folder_auto_sync.sync_scope(project_id, request_id, environment, ACTOR)
        except Exception as exc:  # noqa: BLE001 - one request never stops the others
            summary["failed"] += 1
            logger.warning("Result auto-sync failed: project=%s request=%s environment=%s error=%s",
                           project_id, request_id, environment, type(exc).__name__)
            continue
        if result.get("changed") and not result.get("coalesced"):
            summary["refreshed"] += 1
        if result.get("status") in {"FAILED", "CONFLICT"}:
            summary["failed"] += 1
    from . import capture_jobs

    capture_jobs.wake()
    if summary["refreshed"] or summary["discovered"]:
        logger.info("Result auto-sync: %(scopes)d request scope(s), %(refreshed)d refreshed, "
                    "%(discovered)d new folder(s), %(failed)d failed", summary)
    return summary


_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _loop(interval: int) -> None:
    if _stop.wait(FIRST_RUN_DELAY_SECONDS):
        return
    while not _stop.is_set():
        started = time.monotonic()
        run_once(_stop)
        if _stop.wait(max(1.0, interval - (time.monotonic() - started))):
            return


def start() -> bool:
    """Start the background thread (idempotent); ``False`` when turned off."""
    global _thread
    interval = interval_seconds()
    if not AUTOSTART or interval <= 0:
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(interval,), name="result-auto-sync", daemon=True)
        _thread.start()
    logger.info("Result auto-sync started: every %d s", interval)
    return True


def stop(timeout: float = 10.0) -> None:
    global _thread
    _stop.set()
    with _lock:
        thread, _thread = _thread, None
    if thread is not None:
        thread.join(timeout)
