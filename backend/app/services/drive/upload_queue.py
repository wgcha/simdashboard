"""SCX drive upload queue (stage D3, integration 05 §5, 04 §2.3, contract §2.4).

Every drive write of the dashboard is a row of ``drive_upload_queue``.  Rows are
grouped in batches (one result drop publish, one Final designation, one
current-Final summary file) and run in ``seq`` order by **one** background worker
thread of the (single, ``dashboard.lock``) dashboard process:

======================  ==========================================================
``MKDIR``               ``gateway.mkdirs`` (existing folder is success)
``COPY``                ``gateway.copy_within`` (drive → drive); when the drive does
                        not support it (C7 unknown) ``download_to`` + ``upload_new``
                        (``transfer_method`` records which one was used)
``FILE``                ``gateway.upload_new`` of a server staging file
``COMPLETE_MARKER``     ``gateway.upload_new``; the batch's last item (built just in
                        time by the origin, e.g. Final ``complete.json``)
======================  ==========================================================

Rules (05 §5.2):

* Never delete, move or overwrite on the drive.  ``CONFLICT`` (target exists) is
  answered by a ``stat`` of the target: the same content is ``DONE``, anything else
  stops the item as ``CONFLICT`` (visible, admin decides).  "Same content" is the
  sha1 when both sides have it; otherwise the target is downloaded into a server
  ``xfer-`` folder (no DB connection held) and its sha256 compared with the staged
  file / planned sha256 — never the size alone.  A target above
  ``SIMDASH_DRIVE_VERIFY_MAX_BYTES`` (default 2 GiB) is not downloaded: ``CONFLICT``.
* Before a retry the target is ``stat``-ed first (an earlier attempt may have
  landed).  Retryable errors (BUSY, LOCKED, TIMEOUT, OVERLOADED, UNAVAILABLE) back
  off 30 s → 2 m → 10 m → 30 m → 1 h up to ``SIMDASH_DRIVE_UPLOAD_MAX_ATTEMPTS``.
* ``AUTH_REQUIRED`` blocks the item (``BLOCKED``) and pauses the whole queue until
  credentials are registered again (:func:`resume_blocked`) or an admin resumes it.
* Items with ``halt_on_error`` stop the rest of their batch when they end
  ``FAILED``/``CONFLICT``/``CANCELLED`` (a Final never gets its ``complete.json``);
  nothing already written is removed (an incomplete Final has no marker).
* Restart: rows left ``RUNNING`` become ``PENDING`` and are re-checked with ``stat``.
  The worker loop repeats this every minute for ``RUNNING`` rows no thread of this
  process is executing (a failed result recording), and runs the finish hook of
  ``PUBLISHING`` Finals whose batch already finished (:func:`sweep`).
* **No DB connection is held while the worker waits on the drive** (each step:
  short connection to claim → drive call without a connection → short connection
  to record the result).
* Server staging files are deleted only after their item is ``DONE`` (Final batches:
  only when the whole batch is ``DONE``, so a retry of the same Final ID still has
  its reports).  Staging of stopped batches / unfinished Finals is removed after
  ``STAGING_MAX_AGE_SECONDS`` without activity (hourly cleanup; never the drive).

Cancelling an item (admin) only marks the row; drive content is never touched.
"""
from __future__ import annotations

import importlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ...database_connection import connect, connection_held
from ..storage import server_local
from . import gateway as drive_gateway
from .. import notifications

logger = logging.getLogger("app.services.drive.upload_queue")

KINDS = ("MKDIR", "COPY", "FILE", "COMPLETE_MARKER")
STATES = ("PENDING", "RUNNING", "DONE", "CONFLICT", "FAILED", "BLOCKED", "CANCELLED")
OPEN_STATES = ("PENDING", "RUNNING", "BLOCKED")
STOP_STATES = ("FAILED", "CONFLICT", "CANCELLED")
RETRYABLE_CODES = frozenset({"BUSY", "LOCKED", "TIMEOUT", "OVERLOADED", "UNAVAILABLE"})
BACKOFF_SECONDS = (30, 120, 600, 1800, 3600)
# copy_within failures that mean "this drive cannot copy" (C7 unknown): fall back to download → upload.
COPY_UNSUPPORTED_CODES = frozenset({"INTERNAL"})
COPY_FALLBACK_CODES = COPY_UNSUPPORTED_CODES | frozenset({"INVALID_PATH", "FORBIDDEN"})
# INTERNAL may also be a transient server fault: only this many consecutive INTERNALs (each within
# COPY_FALLBACK_SECONDS of the previous one) switch every copy to download → upload, for
# COPY_FALLBACK_SECONDS; then copy_within is tried again.  Single INTERNALs fall back for that item only.
COPY_INTERNAL_STREAK = 3
COPY_FALLBACK_SECONDS = 3600.0
TRANSFER_MAX_BYTES = 64 * 1024 ** 3
IDLE_WAIT_SECONDS = 5.0
STAGING_PREFIX = "upload-"
ORPHAN_SECONDS = 24 * 3600
# Staging of batches waiting for an admin decision (CONFLICT/FAILED) or of unfinished Finals
# (PLANNED/STAGED/FAILED) is kept this long after their last activity.
STAGING_MAX_AGE_SECONDS = 7 * 24 * 3600
SWEEP_SECONDS = 60.0
CLEANUP_SECONDS = 3600.0
# Recording an outcome after the drive call is retried (short connections) before the row is
# left RUNNING for the sweep.
RECORD_RETRY_DELAYS = (0.5, 2.0, 5.0)
# Tests turn the background thread off and drive the queue with :func:`run_until_idle`.
AUTOSTART = True
# Origins whose module reacts to finished batches / builds just-in-time items.
_ORIGIN_MODULES = {
    "finalization": "app.services.case_finalization_drive",
    "final_designation": "app.services.case_finalization_drive",
    "result_drop": "app.services.result_drop_upload",
    "result_structure": "app.services.result_folder_structure",
}

ERROR_MESSAGES = {
    "SPDM_CONFLICT": "같은 이름의 다른 파일이 이미 드라이브에 있습니다(덮어쓰지 않았습니다).",
    "DRIVE_AUTH_REQUIRED": "드라이브 인증이 필요합니다. 관리자가 토큰을 다시 등록하면 대기열이 이어서 진행됩니다.",
    "DRIVE_ROOT_CHANGED": "SPDM 루트가 바뀌어 이 항목을 쓰지 않았습니다.",
    "DRIVE_STAGING_MISSING": "서버 임시 파일이 없어 올릴 수 없습니다. 다시 올리세요.",
    "DRIVE_UPLOAD_MISMATCH": "드라이브에 올라간 파일 크기가 다릅니다.",
    "FINALIZATION_SOURCE_STALE": "Final 계획 이후 드라이브 원본이 바뀌었습니다.",
    "FINALIZATION_SOURCE_MISSING": "드라이브 원본 파일을 찾을 수 없습니다.",
    "DRIVE_QUEUE_CANCELLED": "관리자가 취소했습니다(드라이브 내용은 그대로입니다).",
    "DRIVE_STAGING_FULL": "서버 임시 저장 공간이 부족해 기다립니다. 공간이 생기면 이어서 진행합니다.",
    "SPDM_CONFLICT_UNVERIFIED": ("같은 이름의 파일이 드라이브에 있고 크기가 커서 내용을 비교하지 않았습니다"
                                 "(덮어쓰지 않았습니다). 관리자가 확인하세요."),
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()
    return str(value) if value else None


def new_batch_id() -> str:
    return uuid.uuid4().hex


# --- rows -------------------------------------------------------------------------------------

COLUMNS = ("id", "batch_id", "seq", "kind", "root_key", "staging_path", "src_rel", "src_version_token", "dst_rel_dir",
           "dst_name", "size_bytes", "sha256", "state", "halt_on_error", "attempts", "next_attempt_at", "last_error_code",
           "last_error_msg", "result_item_id", "result_size", "result_sha1", "transfer_method", "requested_by", "origin",
           "origin_ref", "project_id", "request_id", "environment", "created_at", "updated_at", "finished_at")
_SELECT = "SELECT " + ",".join(COLUMNS) + " FROM drive_upload_queue"


@dataclass
class Item:
    id: str
    batch_id: str
    seq: int
    kind: str
    root_key: str
    staging_path: str | None
    src_rel: str | None
    src_version_token: str | None
    dst_rel_dir: str
    dst_name: str | None
    size_bytes: int | None
    sha256: str | None
    state: str
    halt_on_error: bool
    attempts: int
    next_attempt_at: Any
    last_error_code: str | None
    last_error_msg: str | None
    result_item_id: str | None
    result_size: int | None
    result_sha1: str | None
    transfer_method: str | None
    requested_by: str
    origin: str
    origin_ref: str | None
    project_id: str | None
    request_id: str | None
    environment: str | None
    created_at: Any
    updated_at: Any
    finished_at: Any

    @property
    def target(self) -> str:
        return f"{self.dst_rel_dir}/{self.dst_name}" if self.dst_name else self.dst_rel_dir

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id, "batch_id": self.batch_id, "seq": self.seq, "kind": self.kind, "state": self.state,
            "target": self.target, "source": self.src_rel, "size": self.size_bytes, "attempts": self.attempts,
            "next_attempt_at": _iso(self.next_attempt_at), "error_code": self.last_error_code,
            "error_message": self.last_error_msg, "transfer_method": self.transfer_method,
            "requested_by": self.requested_by, "origin": self.origin, "origin_ref": self.origin_ref,
            "project_id": self.project_id, "request_id": self.request_id, "environment": self.environment,
            "created_at": _iso(self.created_at), "updated_at": _iso(self.updated_at), "finished_at": _iso(self.finished_at),
        }


def _item(row: Any) -> Item:
    values = dict(zip(COLUMNS, row))
    values["halt_on_error"] = bool(values["halt_on_error"])
    values["attempts"] = int(values["attempts"] or 0)
    values["seq"] = int(values["seq"])
    return Item(**values)


def get_item(conn: Any, item_id: str) -> Item | None:
    row = conn.execute(_SELECT + " WHERE id=?", [item_id]).fetchone()
    return _item(row) if row else None


def batch_items(conn: Any, batch_id: str) -> list[Item]:
    return [_item(row) for row in conn.execute(_SELECT + " WHERE batch_id=? ORDER BY seq", [batch_id]).fetchall()]


# --- enqueue ----------------------------------------------------------------------------------

def staging_dir_for(batch_id: str) -> Path:
    """Server staging folder of one batch (``<SIMDASH_SCX_STAGING_DIR>/upload-<batch>``)."""
    return drive_gateway.current_settings().staging_dir / f"{STAGING_PREFIX}{batch_id}"


def current_root_key() -> str:
    from ..storage.drive import drive_root_from_settings

    return drive_root_from_settings(drive_gateway.current_settings()).root_key()


def enqueue(conn: Any, *, origin: str, requested_by: str, items: Iterable[dict[str, Any]], batch_id: str | None = None,
            origin_ref: str | None = None, project_id: str | None = None, request_id: str | None = None,
            environment: str | None = None) -> str:
    """Insert one batch (in the caller's transaction); the worker is woken after the caller commits.

    ``items`` keys: ``kind``, ``dst_rel_dir`` (SPDM-root relative folder), optional ``dst_name``,
    ``staging_path``, ``src_rel``, ``src_version_token``, ``size_bytes``, ``sha256``, ``halt_on_error``.
    """
    batch = batch_id or new_batch_id()
    root_key = current_root_key()
    now = _now()
    seq = 0
    for item in items:
        kind = str(item["kind"])
        if kind not in KINDS:
            raise ValueError(f"unknown drive queue item kind {kind}")
        conn.execute(
            "INSERT INTO drive_upload_queue (id,batch_id,seq,kind,root_key,staging_path,src_rel,src_version_token,"
            "dst_rel_dir,dst_name,size_bytes,sha256,state,halt_on_error,attempts,requested_by,origin,origin_ref,"
            "project_id,request_id,environment,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',?,0,?,?,?,?,?,?,?,?)",
            [f"dq-{uuid.uuid4().hex}", batch, seq, kind, root_key, item.get("staging_path"), item.get("src_rel"),
             item.get("src_version_token"), str(item["dst_rel_dir"]), item.get("dst_name"), item.get("size_bytes"),
             item.get("sha256"), bool(item.get("halt_on_error", True)), str(requested_by), origin, origin_ref,
             project_id, request_id, environment, now, now],
        )
        seq += 1
    if seq == 0:
        raise ValueError("a drive queue batch needs at least one item")
    _after_commit_wake()
    return batch


def requeue_batch(conn: Any, batch_id: str) -> int:
    """Retry a stopped batch: its FAILED/CONFLICT/BLOCKED/CANCELLED items become PENDING again."""
    stopped = "batch_id=? AND state IN ('FAILED','CONFLICT','BLOCKED','CANCELLED')"
    count = int(conn.execute(f"SELECT count(*) FROM drive_upload_queue WHERE {stopped}", [batch_id]).fetchone()[0])
    conn.execute("UPDATE drive_upload_queue SET state='PENDING', attempts=0, next_attempt_at=NULL, finished_at=NULL, "
                 f"last_error_code=NULL, last_error_msg=NULL, updated_at=? WHERE {stopped}", [_now(), batch_id])
    _after_commit_wake()
    return count


# --- batch summary ----------------------------------------------------------------------------

def summarize(items: list[Item]) -> dict[str, Any]:
    """Batch state for screens: QUEUED, RUNNING, PAUSED, DONE, PARTIAL, CONFLICT, FAILED, CANCELLED."""
    counts = {state: 0 for state in STATES}
    for item in items:
        counts[item.state] += 1
    halted = next((item for item in items if item.state in STOP_STATES and item.halt_on_error), None)
    running = counts["RUNNING"] > 0
    if counts["BLOCKED"]:
        state = "PAUSED"
    elif halted is not None and not running:
        state = halted.state
    elif counts["PENDING"] or running:
        started = running or counts["DONE"] or any(item.attempts for item in items)
        state = "RUNNING" if started else "QUEUED"
    elif counts["DONE"] == len(items):
        state = "DONE"
    elif counts["DONE"] == 0 and counts["CANCELLED"] == len(items):
        state = "CANCELLED"
    elif counts["DONE"]:
        state = "PARTIAL"
    else:
        state = "CONFLICT" if counts["CONFLICT"] else "FAILED"
    files = [item for item in items if item.kind in {"FILE", "COPY", "COMPLETE_MARKER"}]
    current = next((item for item in items if item.state in {"RUNNING", "PENDING", "BLOCKED"}), None)
    problems = [item for item in items if item.state in {"FAILED", "CONFLICT", "BLOCKED"}]
    retry_at = min((item.next_attempt_at for item in items if item.state == "PENDING" and item.next_attempt_at),
                   default=None)
    methods: dict[str, int] = {}
    for item in items:
        if item.transfer_method:
            methods[item.transfer_method] = methods.get(item.transfer_method, 0) + 1
    return {
        "batch_id": items[0].batch_id if items else None, "state": state,
        "finished": state in {"DONE", "PARTIAL", "CONFLICT", "FAILED", "CANCELLED"},
        "paused": bool(counts["BLOCKED"]), "counts": counts, "items_total": len(items),
        "files_total": len(files), "files_done": sum(1 for item in files if item.state == "DONE"),
        "bytes_total": sum(int(item.size_bytes or 0) for item in files),
        "bytes_done": sum(int(item.size_bytes or 0) for item in files if item.state == "DONE"),
        "current": current.target if current else None,
        "current_kind": current.kind if current else None,
        "next_retry_at": _iso(retry_at),
        "errors": [{"id": item.id, "target": item.target, "state": item.state, "code": item.last_error_code,
                    "message": item.last_error_msg} for item in problems[:20]],
        "transfer_methods": methods,
        "origin": items[0].origin if items else None, "origin_ref": items[0].origin_ref if items else None,
        "requested_by": items[0].requested_by if items else None,
        "project_id": items[0].project_id if items else None, "request_id": items[0].request_id if items else None,
        "created_at": _iso(items[0].created_at) if items else None,
        "updated_at": _iso(max((item.updated_at for item in items), default=None)) if items else None,
    }


def batch_summary(conn: Any, batch_id: str) -> dict[str, Any] | None:
    items = batch_items(conn, batch_id)
    return summarize(items) if items else None


def queue_paused(conn: Any) -> bool:
    return bool(conn.execute("SELECT count(*) FROM drive_upload_queue WHERE state='BLOCKED'").fetchone()[0])


# --- admin ------------------------------------------------------------------------------------

def admin_list(conn: Any, *, state: str | None = None, limit: int = 200) -> dict[str, Any]:
    counts = {state_name: 0 for state_name in STATES}
    for row in conn.execute("SELECT state, count(*) FROM drive_upload_queue GROUP BY state").fetchall():
        counts[str(row[0])] = int(row[1])
    where, params = "", []
    if state:
        where, params = " WHERE state=?", [state]
    else:
        where = " WHERE state <> 'DONE' OR finished_at >= ?"
        params = [_now() - timedelta(days=1)]
    rows = conn.execute(_SELECT + where + " ORDER BY created_at DESC, batch_id, seq LIMIT ?", [*params, int(limit)]).fetchall()
    return {"counts": counts, "paused": counts["BLOCKED"] > 0, "items": [_item(row).view() for row in rows],
            "worker_running": worker_running()}


def retry_item(conn: Any, item_id: str) -> Item:
    item = get_item(conn, item_id)
    if item is None:
        raise LookupError(item_id)
    if item.state not in {"FAILED", "CONFLICT", "BLOCKED", "CANCELLED", "PENDING"}:
        raise ValueError(item.state)
    conn.execute("UPDATE drive_upload_queue SET state='PENDING', attempts=0, next_attempt_at=NULL, finished_at=NULL, "
                 "last_error_code=NULL, last_error_msg=NULL, updated_at=? WHERE id=? "
                 "AND state IN ('FAILED','CONFLICT','BLOCKED','CANCELLED','PENDING')", [_now(), item_id])
    _after_commit_wake()
    return get_item(conn, item_id)  # type: ignore[return-value]


def cancel_item(conn: Any, item_id: str) -> Item:
    """Mark a not-yet-done item CANCELLED (drive content is never touched)."""
    item = get_item(conn, item_id)
    if item is None:
        raise LookupError(item_id)
    if item.state not in {"PENDING", "BLOCKED", "FAILED", "CONFLICT"}:
        raise ValueError(item.state)
    now = _now()
    # Conditional: the worker may have claimed the item meanwhile (then it runs to its end, never overwritten).
    conn.execute("UPDATE drive_upload_queue SET state='CANCELLED', last_error_code='DRIVE_QUEUE_CANCELLED', "
                 "last_error_msg=?, finished_at=?, updated_at=? WHERE id=? AND state IN ('PENDING','BLOCKED','FAILED','CONFLICT')",
                 [ERROR_MESSAGES["DRIVE_QUEUE_CANCELLED"], now, now, item_id])
    if (get_item(conn, item_id) or item).state != "CANCELLED":
        raise ValueError("RUNNING")
    _notify_batch_finished(conn, item.batch_id)
    # The finish hook runs after the caller committed (:func:`request_finish`; the sweep covers a lost call).
    return get_item(conn, item_id)  # type: ignore[return-value]


def request_finish(batch_id: str) -> None:
    """After the caller committed a change of a batch's items (admin cancel): run its finish hook."""
    _pending_finish.add(str(batch_id))
    _worker.wake.set()


def resume_blocked(conn: Any | None = None) -> int:
    """Credentials registered again (or admin): BLOCKED items continue."""
    def run(target: Any) -> int:
        count = int(target.execute("SELECT count(*) FROM drive_upload_queue WHERE state='BLOCKED'").fetchone()[0])
        target.execute("UPDATE drive_upload_queue SET state='PENDING', next_attempt_at=NULL, updated_at=? "
                       "WHERE state='BLOCKED'", [_now()])
        return count

    if conn is not None:
        count = run(conn)
    else:
        with connect() as own:
            count = run(own)
    _after_commit_wake()
    return count


# --- worker -----------------------------------------------------------------------------------

class _Worker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.wake = threading.Event()
        # copy_within missing in the adapter: download → upload for the process lifetime.
        self.copy_unsupported = False
        # Consecutive copy_within INTERNALs (review #7) and the global fallback window they opened.
        self.internal_streak = 0
        self.internal_last = 0.0
        self.copy_fallback_until = 0.0


_worker = _Worker()
# Batches whose items were stopped outside the worker (admin cancel): finish hooks run on the worker thread.
_pending_finish: set[str] = set()
# Items a thread of this process is executing right now (the sweep never resets those to PENDING).
_executing: set[str] = set()
_executing_lock = threading.Lock()


def _after_commit_wake() -> None:
    _worker.wake.set()


def wake() -> None:
    """Callers wake the worker after their transaction committed (enqueue happens inside it)."""
    _worker.wake.set()


def wait_batch(batch_id: str, timeout: float, *, poll: float = 0.2) -> dict[str, Any] | None:
    """Wait (no DB connection held between polls) until the batch finished or ``timeout`` passed."""
    if connection_held():
        raise RuntimeError("wait_batch must not hold a database connection")
    wake()
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        with connect() as conn:
            summary = batch_summary(conn, batch_id)
        if summary is None or summary["finished"] or summary["paused"] or time.monotonic() >= deadline:
            return summary
        time.sleep(poll)


def worker_running() -> bool:
    thread = _worker.thread
    return bool(thread and thread.is_alive())


def recover_interrupted() -> int:
    """Startup: items left RUNNING by a stopped process are re-checked from PENDING (05 §5.2)."""
    with connect() as conn:
        count = int(conn.execute("SELECT count(*) FROM drive_upload_queue WHERE state='RUNNING'").fetchone()[0])
        conn.execute("UPDATE drive_upload_queue SET state='PENDING', next_attempt_at=NULL, updated_at=? "
                     "WHERE state='RUNNING'", [_now()])
        return count


def start() -> bool:
    """Start the single worker thread (scx mode with drive writes enabled); idempotent."""
    from .writes import drive_writes_active

    if not AUTOSTART or not drive_writes_active():
        return False
    with _worker.lock:
        if worker_running():
            return True
        try:
            resumed = recover_interrupted()
            if resumed:
                logger.info("SCX drive upload queue: %d interrupted item(s) resume", resumed)
            # A stop between a batch's last item and its finish hook leaves the origin (a Final) unfinished.
            with connect() as conn:
                for row in conn.execute("SELECT upload_batch_id FROM finalization_operations "
                                        "WHERE status='PUBLISHING' AND upload_batch_id IS NOT NULL").fetchall():
                    _pending_finish.add(str(row[0]))
            _cleanup_orphans()
        except Exception:  # noqa: BLE001 - startup continues; the loop retries the DB later
            logger.warning("SCX drive upload queue recovery failed", exc_info=True)
        _worker.stop.clear()
        _worker.thread = threading.Thread(target=_loop, name="drive-upload-queue", daemon=True)
        _worker.thread.start()
        return True


def stop(timeout: float = 10.0) -> None:
    with _worker.lock:
        thread = _worker.thread
        _worker.stop.set()
        _worker.wake.set()
    if thread is not None:
        thread.join(timeout)
    with _worker.lock:
        _worker.thread = None
        _reset_copy_state()


def _reset_copy_state() -> None:
    _worker.copy_unsupported = False
    _worker.internal_streak = 0
    _worker.internal_last = 0.0
    _worker.copy_fallback_until = 0.0


def reset_for_tests() -> None:
    stop()
    _pending_finish.clear()
    with _executing_lock:
        _executing.clear()
    _reset_copy_state()


def _loop() -> None:
    last_sweep = 0.0
    last_cleanup = time.monotonic()      # start() already cleaned up
    while not _worker.stop.is_set():
        moment = time.monotonic()
        if moment - last_sweep >= SWEEP_SECONDS:
            last_sweep = moment
            cleanup = moment - last_cleanup >= CLEANUP_SECONDS
            if cleanup:
                last_cleanup = moment
            try:
                sweep(cleanup=cleanup)
            except Exception:  # noqa: BLE001 - retried on the next sweep
                logger.warning("SCX drive upload queue sweep failed", exc_info=True)
        try:
            worked = process_next()
        except Exception:  # noqa: BLE001 - the worker must survive a DB/drive hiccup
            logger.warning("SCX drive upload queue step failed", exc_info=True)
            worked = False
        if worked:
            continue
        _worker.wake.wait(min(_idle_wait(), SWEEP_SECONDS))
        _worker.wake.clear()


def sweep(*, cleanup: bool = False) -> dict[str, int]:
    """Periodic repair (worker loop, every ``SWEEP_SECONDS``; no drive call).

    * ``RUNNING`` rows that no thread of this process executes (the result of a drive call could
      not be recorded) become ``PENDING``; the pre-retry ``stat`` recognises a write that landed.
    * ``PUBLISHING`` Finals whose batch already finished get their finish hook again (a hook
      that failed, an admin cancel whose wake-up was lost).
    * ``cleanup``: server staging cleanup (:func:`_cleanup_orphans`).
    """
    if connection_held():
        raise RuntimeError("the drive upload queue sweep must run without an open database connection")
    with connect() as conn:
        running = [str(row[0]) for row in conn.execute("SELECT id FROM drive_upload_queue WHERE state='RUNNING'").fetchall()]
        with _executing_lock:
            stale = [item_id for item_id in running if item_id not in _executing]
        resumed = 0
        for item_id in stale:
            if conn.execute("UPDATE drive_upload_queue SET state='PENDING', next_attempt_at=NULL, updated_at=? "
                            "WHERE id=? AND state='RUNNING' RETURNING id", [_now(), item_id]).fetchone():
                resumed += 1
        publishing = [str(row[0]) for row in conn.execute(
            "SELECT upload_batch_id FROM finalization_operations WHERE status='PUBLISHING' "
            "AND upload_batch_id IS NOT NULL").fetchall()]
    if resumed:
        logger.warning("SCX drive upload queue: %d item(s) left RUNNING resume from PENDING", resumed)
    for batch_id in publishing:
        _finish_if_done(batch_id)
    removed = _cleanup_orphans() if cleanup else 0
    return {"resumed": resumed, "publishing": len(publishing), "staging_removed": removed}


def _idle_wait() -> float:
    try:
        with connect() as conn:
            row = conn.execute("SELECT min(next_attempt_at) FROM drive_upload_queue WHERE state='PENDING'").fetchone()
    except Exception:  # noqa: BLE001
        return IDLE_WAIT_SECONDS
    due = row[0] if row else None
    if not isinstance(due, datetime):
        return IDLE_WAIT_SECONDS
    return max(0.05, min(IDLE_WAIT_SECONDS, (due - _now()).total_seconds()))


def run_until_idle(*, max_steps: int = 100_000, now: datetime | None = None) -> int:
    """Process ready items on the calling thread until none is ready (tests, admin tools)."""
    steps = 0
    while steps < max_steps and process_next(now=now):
        steps += 1
    return steps


_CLAIM_SQL = (
    _SELECT + " q WHERE q.state='PENDING' AND (q.next_attempt_at IS NULL OR q.next_attempt_at<=?) "
    "AND NOT EXISTS (SELECT 1 FROM drive_upload_queue p WHERE p.batch_id=q.batch_id AND p.seq<q.seq "
    "AND (p.state IN ('PENDING','RUNNING','BLOCKED') OR (p.state IN ('FAILED','CONFLICT','CANCELLED') AND p.halt_on_error))) "
    "ORDER BY q.created_at, q.batch_id, q.seq LIMIT 1"
)


def process_next(*, now: datetime | None = None) -> bool:
    """Run one ready item.  Returns ``False`` when nothing is ready (or the queue is paused)."""
    if connection_held():
        raise RuntimeError("the drive upload queue must run without an open database connection")
    for batch_id in list(_pending_finish):
        _pending_finish.discard(batch_id)
        _finish_if_done(batch_id)
    moment = now or _now()
    claimed = False
    item: Item | None = None
    try:
        with connect() as conn:
            if queue_paused(conn):
                return False
            row = conn.execute(_CLAIM_SQL, [moment]).fetchone()
            if row is None:
                return False
            item = _item(row)
            if item.root_key != current_root_key():
                _record(conn, item, "FAILED", code="DRIVE_ROOT_CHANGED")
                _notify_batch_finished(conn, item.batch_id)
            else:
                # Review #8: our own claim is verified by the row the conditional UPDATE returns.
                got = conn.execute("UPDATE drive_upload_queue SET state='RUNNING', attempts=attempts+1, updated_at=? "
                                   "WHERE id=? AND state='PENDING' RETURNING attempts", [_now(), item.id]).fetchone()
                claimed = got is not None
                if claimed:
                    item.attempts = int(got[0])
                    item.state = "RUNNING"
                    with _executing_lock:
                        _executing.add(item.id)
                    if item.origin == "finalization" and item.origin_ref:
                        conn.execute("UPDATE drive_locks SET expires_at=? WHERE owner=?",
                                     [_now() + timedelta(minutes=30), item.origin_ref])
    except BaseException:
        if item is not None:
            with _executing_lock:
                _executing.discard(item.id)
        raise
    assert item is not None
    if not claimed:
        _finish_if_done(item.batch_id)
        return True
    try:
        outcome = _execute(item)            # drive calls: no DB connection held here
        _record_outcome(item, outcome)
    finally:
        with _executing_lock:
            _executing.discard(item.id)
    if (outcome.state == "DONE" and item.staging_path and item.kind in {"FILE", "COMPLETE_MARKER"}
            and item.origin != "finalization"):
        # A Final keeps its staging (reports, plan.json) until the whole batch is DONE: a retry of the
        # same Final ID re-checks its reports (review #2); _finish_if_done removes the folder.
        server_local.discard_file(Path(item.staging_path))
    _finish_if_done(item.batch_id)
    return True


def _record_outcome(item: Item, outcome: "_Outcome") -> None:
    """Record the drive result with short connections, retried; a final failure leaves the row RUNNING
    (the sweep turns it PENDING and the pre-retry ``stat`` recognises what landed)."""
    delays = tuple(RECORD_RETRY_DELAYS)
    for attempt in range(len(delays) + 1):
        try:
            with connect() as conn:
                _apply(conn, item, outcome)
            return
        except Exception:  # noqa: BLE001 - DB hiccup after the drive call
            if attempt >= len(delays):
                raise
            logger.warning("SCX drive queue: recording %s of item %s failed; retrying", outcome.state, item.id,
                           exc_info=True)
            _worker.stop.wait(delays[attempt])


@dataclass
class _Outcome:
    state: str
    code: str | None = None
    message: str | None = None
    entry: Any = None
    method: str | None = None
    staging: tuple[str, int, str] | None = None


def _record(conn: Any, item: Item, state: str, *, code: str | None = None, message: str | None = None) -> None:
    now = _now()
    conn.execute("UPDATE drive_upload_queue SET state=?, last_error_code=?, last_error_msg=?, updated_at=?, "
                 "finished_at=? WHERE id=?",
                 [state, code, (message or ERROR_MESSAGES.get(code or "", code or ""))[:400] if code else None, now,
                  now if state in {"DONE", *STOP_STATES} else None, item.id])


def _apply(conn: Any, item: Item, outcome: _Outcome) -> None:
    """Record ``outcome`` and, in the same transaction, the notifications it causes."""
    paused_before = queue_paused(conn) if outcome.state == "BLOCKED" else True
    _apply_outcome(conn, item, outcome)
    if not paused_before:
        notifications.drive_queue_paused(conn)
    if outcome.state in {"DONE", *STOP_STATES} or (outcome.state == "RETRY" and item.attempts
                                                     >= drive_gateway.current_settings().upload_max_attempts):
        _notify_batch_finished(conn, item.batch_id)


def _notify_batch_finished(conn: Any, batch_id: str) -> None:
    """A finished result drop batch notifies the user who started it (Finals: ``case_finalization_drive``)."""
    items = batch_items(conn, batch_id)
    if not items or items[0].origin != "result_drop":
        return
    summary = summarize(items)
    if summary["finished"]:
        notifications.drive_upload_finished(conn, summary)


def _apply_outcome(conn: Any, item: Item, outcome: _Outcome) -> None:
    now = _now()
    settings = drive_gateway.current_settings()
    if outcome.staging is not None:
        path, size, digest = outcome.staging
        conn.execute("UPDATE drive_upload_queue SET staging_path=?, size_bytes=?, sha256=? WHERE id=?",
                     [path, size, digest, item.id])
        item.staging_path = path
    if outcome.state == "RETRY":
        if item.attempts >= settings.upload_max_attempts:
            _record(conn, item, "FAILED", code=outcome.code, message=outcome.message)
            return
        delay = BACKOFF_SECONDS[min(item.attempts - 1, len(BACKOFF_SECONDS) - 1)]
        conn.execute("UPDATE drive_upload_queue SET state='PENDING', next_attempt_at=?, last_error_code=?, "
                     "last_error_msg=?, updated_at=? WHERE id=?",
                     [now + timedelta(seconds=delay), outcome.code, (outcome.message or "")[:400], now, item.id])
        return
    if outcome.state == "DONE":
        entry = outcome.entry
        size = getattr(entry, "size", None)
        conn.execute("UPDATE drive_upload_queue SET state='DONE', result_item_id=?, result_size=?, result_sha1=?, "
                     "transfer_method=COALESCE(?, transfer_method), last_error_code=NULL, last_error_msg=NULL, "
                     "next_attempt_at=NULL, updated_at=?, finished_at=? WHERE id=?",
                     [str(getattr(entry, "item_id", "") or "") or None, int(size) if size is not None else None,
                      (str(getattr(entry, "sha1", "") or "").lower() or None), outcome.method, now, now, item.id])
        return
    if outcome.method:
        conn.execute("UPDATE drive_upload_queue SET transfer_method=? WHERE id=?", [outcome.method, item.id])
    _record(conn, item, outcome.state, code=outcome.code, message=outcome.message)


def _finish_if_done(batch_id: str) -> None:
    with connect() as conn:
        items = batch_items(conn, batch_id)
    if not items:
        return
    summary = summarize(items)
    if summary["state"] == "DONE":
        staging = staging_dir_for(batch_id)
        server_local.remove_tree(staging)
    if not summary["finished"]:
        return
    module_name = _ORIGIN_MODULES.get(items[0].origin)
    if module_name is None:
        return
    hook = getattr(importlib.import_module(module_name), "on_drive_batch_finished", None)
    if hook is None:
        return
    try:
        hook(items[0].origin, batch_id, summary)
    except Exception:  # noqa: BLE001 - the queue state is already recorded; the origin can be retried
        logger.warning("SCX drive queue finish hook failed for %s batch %s", items[0].origin, batch_id, exc_info=True)


# --- drive steps (no DB connection) -------------------------------------------------------------

def _drive_path(rel: str) -> str:
    root = str(drive_gateway.current_settings().spdm_root or "")
    return f"{root}/{rel}" if rel else root


def _code(error: BaseException) -> str | None:
    return drive_gateway.drive_error_code(error)


def _storage_code(code: str) -> str:
    return drive_gateway.DRIVE_ERROR_MAP.get(code, drive_gateway.DRIVE_ERROR_MAP["INTERNAL"]).storage_code


def _failure(error: BaseException) -> _Outcome:
    code = _code(error)
    if code is None:
        logger.warning("SCX drive queue step raised %s", type(error).__name__, exc_info=True)
        return _Outcome("FAILED", "DRIVE_INTERNAL", type(error).__name__)
    message = drive_gateway.safe_error_message(error)
    if code == "AUTH_REQUIRED":
        return _Outcome("BLOCKED", "DRIVE_AUTH_REQUIRED", ERROR_MESSAGES["DRIVE_AUTH_REQUIRED"])
    if code in RETRYABLE_CODES:
        return _Outcome("RETRY", _storage_code(code), message)
    return _Outcome("FAILED", _storage_code(code), message)


def _staging_has_room(size: int) -> bool:
    from . import reads

    free = server_local.disk_free(drive_gateway.current_settings().staging_dir)
    return free is None or free - int(size) >= reads.STAGING_MIN_FREE_BYTES


def _download_sha256(gateway: Any, rel: str, size: int) -> tuple[str, str] | "_Outcome":
    """Download ``rel`` (at most ``size`` bytes) into a fresh ``xfer-`` folder: (sha256, sha1); folder removed."""
    if not _staging_has_room(size):
        return _Outcome("RETRY", "DRIVE_STAGING_FULL", ERROR_MESSAGES["DRIVE_STAGING_FULL"])
    work = server_local.new_dir(drive_gateway.current_settings().staging_dir, "xfer-")
    try:
        try:
            result = gateway.download_to(_drive_path(rel), work, max_bytes=int(size))
        except Exception as error:  # noqa: BLE001
            if _code(error) == "LIMIT":           # larger than expected: not the same content
                return "", ""
            raise
        local = Path(result.local_path)
        return str(result.sha256).lower(), server_local.file_sha1(local)
    finally:
        server_local.remove_tree(work)


def _existing_outcome(gateway: Any, item: Item, existing: Any, *, size: int | None, sha1: str | None,
                      sha256: Any = None, method: str | None = None, staging: tuple[str, int, str] | None = None) -> "_Outcome":
    """Is the drive file ``existing`` (same name as the target) the content we meant to write (05 §5.2)?

    ``DONE`` only when the content is shown to be the same: equal sha1 when both sides have it, else
    the target is downloaded (bounded by ``size``, server ``xfer-`` staging, no DB connection) and its
    sha256 compared with ``sha256`` (a value, or a callable computing it lazily).  An equal size alone
    is never enough (review #1).  A target above ``verify_max_bytes`` stays ``CONFLICT`` for an admin.
    """
    def conflict(code: str = "SPDM_CONFLICT") -> _Outcome:
        return _Outcome("CONFLICT", code, ERROR_MESSAGES[code], method=method, staging=staging)

    if existing is None or getattr(existing, "kind", None) != "file":
        return conflict()
    existing_size = getattr(existing, "size", None)
    if size is not None and existing_size is not None and int(existing_size) != int(size):
        return conflict()
    existing_sha1 = str(getattr(existing, "sha1", "") or "").lower() or None
    if existing_sha1 and sha1:
        if existing_sha1 == sha1.lower():
            return _Outcome("DONE", entry=existing, method=method, staging=staging)
        return conflict()
    expected_size = size if size is not None else existing_size
    if expected_size is None:
        return conflict()
    if int(expected_size) > drive_gateway.current_settings().verify_max_bytes:
        return conflict("SPDM_CONFLICT_UNVERIFIED")
    expected = sha256() if callable(sha256) else sha256
    if isinstance(expected, _Outcome):
        expected.method = expected.method or method
        expected.staging = expected.staging or staging
        return expected
    if not expected:
        return conflict("SPDM_CONFLICT_UNVERIFIED")
    downloaded = _download_sha256(gateway, item.target, int(expected_size))
    if isinstance(downloaded, _Outcome):
        downloaded.method, downloaded.staging = method, staging
        return downloaded
    digest, target_sha1 = downloaded
    if not digest or digest != str(expected).lower():
        return conflict()
    return _Outcome("DONE", entry=_with_sha1(existing, target_sha1), method=method, staging=staging)


def _execute(item: Item) -> _Outcome:
    if connection_held():
        raise RuntimeError("drive write while the thread holds a database connection")
    try:
        gateway = drive_gateway.get_drive_gateway()
    except drive_gateway.DriveStartupError as exc:
        return _Outcome("RETRY", "DRIVE_UNAVAILABLE", str(exc)[:200])
    if gateway is None:
        return _Outcome("FAILED", "DRIVE_MODE_DISABLED", "SCX 드라이브 모드가 아닙니다.")
    try:
        if item.kind == "MKDIR":
            return _Outcome("DONE", entry=gateway.mkdirs(_drive_path(item.target)))
        if item.kind == "COPY":
            return _copy(gateway, item)
        return _upload(gateway, item)
    except Exception as error:  # noqa: BLE001 - adapter DriveError carries the code
        return _failure(error)


def _staged_file(item: Item) -> tuple[Path, int, str, str] | _Outcome:
    staging = item.staging_path
    materialized: tuple[str, int, str] | None = None
    if not staging and item.kind == "COMPLETE_MARKER":
        module_name = _ORIGIN_MODULES.get(item.origin)
        builder = getattr(importlib.import_module(module_name), "materialize_drive_item", None) if module_name else None
        if builder is None:
            return _Outcome("FAILED", "DRIVE_STAGING_MISSING")
        materialized = builder(item)
        staging = materialized[0]
    path = Path(str(staging or ""))
    if not staging or not server_local.file_exists(path):
        return _Outcome("FAILED", "DRIVE_STAGING_MISSING", staging=materialized)
    size, digest = server_local.file_sha256(path)
    if item.sha256 and digest != item.sha256:
        return _Outcome("FAILED", "DRIVE_STAGING_MISSING", "서버 임시 파일이 바뀌었습니다. 다시 올리세요.", staging=materialized)
    sha1 = server_local.file_sha1(path)
    return path, size, digest, sha1  # type: ignore[return-value]


def _upload(gateway: Any, item: Item) -> _Outcome:
    staged = _staged_file(item)
    if isinstance(staged, _Outcome):
        return staged
    path, size, digest, sha1 = staged
    staging_note = (str(path), size, digest) if not item.staging_path else None
    target = _drive_path(item.target)
    if item.attempts > 1:
        existing = gateway.stat(target)
        if existing is not None:
            return _existing_outcome(gateway, item, existing, size=size, sha1=sha1, sha256=digest, staging=staging_note)
    try:
        entry = gateway.upload_new(path, _drive_path(item.dst_rel_dir), name=item.dst_name)
    except Exception as error:  # noqa: BLE001
        if _code(error) != "CONFLICT":
            outcome = _failure(error)
            outcome.staging = staging_note
            return outcome
        return _existing_outcome(gateway, item, gateway.stat(target), size=size, sha1=sha1, sha256=digest,
                                 staging=staging_note)
    if getattr(entry, "size", None) is not None and int(entry.size) != size:
        return _Outcome("RETRY", "DRIVE_UPLOAD_MISMATCH", ERROR_MESSAGES["DRIVE_UPLOAD_MISMATCH"], staging=staging_note)
    return _Outcome("DONE", entry=entry, staging=staging_note)


def _copy(gateway: Any, item: Item) -> _Outcome:
    from . import reads

    source = gateway.stat(_drive_path(str(item.src_rel)))
    if source is None or getattr(source, "kind", None) != "file":
        return _Outcome("FAILED", "FINALIZATION_SOURCE_MISSING", f"{ERROR_MESSAGES['FINALIZATION_SOURCE_MISSING']} {item.src_rel}")
    live = reads.version_of(source)
    if item.src_version_token:
        planned = _token_version(item.src_version_token)
        if planned is not None and not live.same_as(planned):
            return _Outcome("FAILED", "FINALIZATION_SOURCE_STALE", f"{ERROR_MESSAGES['FINALIZATION_SOURCE_STALE']} {item.src_rel}")
    target = _drive_path(item.target)

    def source_sha256() -> str | _Outcome | None:
        """The planned sha256, or the source's (downloaded, bounded) when the plan has none."""
        if item.sha256:
            return item.sha256
        size = live.size if live.size is not None else item.size_bytes
        if size is None:
            return None
        downloaded = _download_sha256(gateway, str(item.src_rel), int(size))
        return downloaded if isinstance(downloaded, _Outcome) else downloaded[0]

    if item.attempts > 1:
        existing = gateway.stat(target)
        if existing is not None:
            return _existing_outcome(gateway, item, existing, size=live.size, sha1=live.sha1, sha256=source_sha256)
    if _copy_within_allowed():
        try:
            entry = gateway.copy_within(_drive_path(str(item.src_rel)), _drive_path(item.dst_rel_dir), new_name=item.dst_name)
        except (AttributeError, NotImplementedError):
            _mark_copy_unsupported("copy_within missing", permanent=True)
        except Exception as error:  # noqa: BLE001
            code = _code(error)
            if code == "CONFLICT":
                return _existing_outcome(gateway, item, gateway.stat(target), size=live.size, sha1=live.sha1,
                                         sha256=source_sha256, method="COPY_WITHIN")
            if code not in COPY_FALLBACK_CODES:
                outcome = _failure(error)
                outcome.method = "COPY_WITHIN"
                return outcome
            if code in COPY_UNSUPPORTED_CODES:
                _note_copy_internal(code)
            else:
                logger.info("SCX drive copy_within refused (%s) for one item; trying download → upload", code)
        else:
            _worker.internal_streak = 0
            if live.size is not None and getattr(entry, "size", None) is not None and int(entry.size) != int(live.size):
                return _Outcome("RETRY", "DRIVE_UPLOAD_MISMATCH", ERROR_MESSAGES["DRIVE_UPLOAD_MISMATCH"], method="COPY_WITHIN")
            return _Outcome("DONE", entry=entry, method="COPY_WITHIN")
    return _download_upload(gateway, item, live)


def _copy_within_allowed() -> bool:
    if _worker.copy_unsupported:
        return False
    if _worker.copy_fallback_until and time.monotonic() < _worker.copy_fallback_until:
        return False
    if _worker.copy_fallback_until:
        logger.info("SCX drive copy_within fallback window ended; trying copy_within again")
        _worker.copy_fallback_until = 0.0
        _worker.internal_streak = 0
    return True


def _note_copy_internal(code: str) -> None:
    """One copy_within INTERNAL: that item falls back; ``COPY_INTERNAL_STREAK`` in a row switch all copies."""
    moment = time.monotonic()
    if _worker.internal_streak and moment - _worker.internal_last > COPY_FALLBACK_SECONDS:
        _worker.internal_streak = 0
    _worker.internal_streak += 1
    _worker.internal_last = moment
    if _worker.internal_streak >= COPY_INTERNAL_STREAK:
        _mark_copy_unsupported(f"{code} ×{_worker.internal_streak}")
    else:
        logger.info("SCX drive copy_within %s (%d/%d) for one item; trying download → upload", code,
                    _worker.internal_streak, COPY_INTERNAL_STREAK)


def _mark_copy_unsupported(reason: str, *, permanent: bool = False) -> None:
    if not _worker.copy_unsupported and not _worker.copy_fallback_until:
        logger.warning("SCX drive copy_within unsupported (%s): Final copies fall back to download → upload "
                       "(contract C7; record in the acceptance log)", reason)
    if permanent:
        _worker.copy_unsupported = True
    else:
        _worker.copy_fallback_until = time.monotonic() + COPY_FALLBACK_SECONDS


def _download_upload(gateway: Any, item: Item, live: Any) -> _Outcome:
    method = "DOWNLOAD_UPLOAD"
    size = live.size if live.size is not None else item.size_bytes
    if size is not None and not _staging_has_room(int(size)):
        return _Outcome("RETRY", "DRIVE_STAGING_FULL", ERROR_MESSAGES["DRIVE_STAGING_FULL"], method=method)
    work = server_local.new_dir(drive_gateway.current_settings().staging_dir, "xfer-")
    try:
        result = gateway.download_to(_drive_path(str(item.src_rel)), work, max_bytes=TRANSFER_MAX_BYTES)
        if item.sha256 and str(result.sha256).lower() != item.sha256.lower():
            return _Outcome("FAILED", "FINALIZATION_SOURCE_STALE",
                            f"{ERROR_MESSAGES['FINALIZATION_SOURCE_STALE']} {item.src_rel}", method=method)
        local = Path(result.local_path)
        sha1 = server_local.file_sha1(local)
        try:
            entry = gateway.upload_new(local, _drive_path(item.dst_rel_dir), name=item.dst_name)
        except Exception as error:  # noqa: BLE001
            if _code(error) != "CONFLICT":
                outcome = _failure(error)
                outcome.method = method
                return outcome
            return _existing_outcome(gateway, item, gateway.stat(_drive_path(item.target)), size=int(result.size),
                                     sha1=sha1, sha256=str(result.sha256), method=method)
        return _Outcome("DONE", entry=_with_sha1(entry, sha1), method=method)
    except Exception as error:  # noqa: BLE001
        outcome = _failure(error)
        outcome.method = method
        return outcome
    finally:
        server_local.remove_tree(work)


@dataclass(frozen=True)
class _EntryWithSha1:
    item_id: Any
    size: Any
    sha1: str | None
    kind: str = "file"


def _with_sha1(entry: Any, sha1: str) -> Any:
    if getattr(entry, "sha1", None):
        return entry
    return _EntryWithSha1(getattr(entry, "item_id", None), getattr(entry, "size", None), sha1)


def _token_version(token: str) -> Any:
    from . import reads

    parts = token.split(":")
    try:
        if parts[0] == "sha1" and len(parts) == 3:
            return reads.Version(token=token, size=None if parts[2] == "?" else int(parts[2]), modified_us=None, sha1=parts[1])
        if parts[0] == "t" and len(parts) == 3:
            return reads.Version(token=token, size=None if parts[1] == "?" else int(parts[1]),
                                 modified_us=None if parts[2] == "?" else int(parts[2]), sha1=None)
    except ValueError:
        return None
    return None


# --- staging cleanup (05 §7) ------------------------------------------------------------------

def _cleanup_orphans() -> int:
    """Server staging cleanup (05 §7; never the drive).  Folders older than a day are removed unless

    * a batch that can still progress (``summarize`` state ``QUEUED``/``RUNNING``/``PAUSED``) or a
      ``PUBLISHING`` Final uses them, or
    * they belong to a batch waiting for an admin decision (``CONFLICT``/``FAILED`` items, or halted by a
      ``halt_on_error`` item so its later items stay ``PENDING``) or to an
      unfinished Final (``PLANNED``/``STAGED``/``FAILED``, retry keeps its reports) whose last activity is
      younger than ``STAGING_MAX_AGE_SECONDS`` (review #9: such staging no longer stays forever).
    """
    settings = drive_gateway.current_settings()
    candidates = server_local.old_dirs(settings.staging_dir, STAGING_PREFIX, ORPHAN_SECONDS)
    candidates += server_local.old_dirs(settings.staging_dir, "xfer-", ORPHAN_SECONDS)
    if not candidates:
        return 0
    cutoff = _now() - timedelta(seconds=STAGING_MAX_AGE_SECONDS)
    activity: dict[str, Any] = {}

    def note_activity(key: Any, moment: Any) -> None:
        key = str(key)
        known = activity.get(key)
        if not isinstance(known, datetime) or (isinstance(moment, datetime) and moment > known):
            activity[key] = moment

    names = {folder.name[len(STAGING_PREFIX):] for folder in candidates if folder.name.startswith(STAGING_PREFIX)}
    live: set[str] = set()
    waiting: set[str] = set()
    with connect() as conn:
        live |= {str(row[0]) for row in conn.execute(
            "SELECT operation_id FROM finalization_operations WHERE status='PUBLISHING'").fetchall()}
        batches = {name: batch_items(conn, name) for name in names}
        for name, items in batches.items():
            if not items:
                continue
            summary = summarize(items)
            if summary["state"] in {"QUEUED", "RUNNING", "PAUSED"}:
                live.add(name)
            elif summary["counts"]["FAILED"] or summary["counts"]["CONFLICT"] or summary["counts"]["PENDING"]:
                # waiting for an admin decision, including a batch halted by a halt_on_error item whose
                # later items stay PENDING until retried (re-review #1: no longer treated as live forever)
                waiting.add(name)
                for item in items:
                    note_activity(name, item.updated_at)
        for row in conn.execute("SELECT operation_id, updated_at FROM finalization_operations "
                                "WHERE status IN ('PLANNED','STAGED','FAILED')").fetchall():
            if str(row[0]) in names:
                waiting.add(str(row[0]))
                note_activity(row[0], row[1])
                for item in batches.get(str(row[0])) or []:
                    note_activity(row[0], item.updated_at)
    removed = 0
    for folder in candidates:
        name = folder.name[len(STAGING_PREFIX):] if folder.name.startswith(STAGING_PREFIX) else None
        if name is not None and name in live:
            continue
        if name is not None and name in waiting:
            last = activity.get(name)
            if not isinstance(last, datetime) or last > cutoff:
                continue
        server_local.remove_tree(folder)
        removed += 1
    if removed:
        logger.info("SCX drive upload queue: removed %d server staging folder(s)", removed)
    return removed


__all__ = ["AUTOSTART", "Item", "admin_list", "wait_batch", "wake", "batch_items", "batch_summary", "cancel_item", "enqueue", "get_item",
           "new_batch_id", "process_next", "queue_paused", "recover_interrupted", "requeue_batch", "resume_blocked",
           "request_finish", "retry_item", "run_until_idle", "staging_dir_for", "start", "stop", "summarize", "sweep",
           "worker_running"]
