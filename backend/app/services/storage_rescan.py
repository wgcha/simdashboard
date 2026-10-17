"""Full storage rescan behind the global administrator's [저장 폴더 새로고침] (user decision 2026-10-17).

The button used to call the legacy ``POST /api/storage/refresh`` (fixed-root load-case bindings),
which does not know the depth schema and was disabled in SCX drive mode. Request folders that the
automatic discovery did not register stayed invisible and nobody could see why. The button now
runs this rescan instead:

1. Forget the remembered needs-review results and retry backoffs
   (``folder_auto_discovery.clear_review_memos``) so every unregistered folder is examined again.
2. Discovery rounds (``folder_auto_discovery.discover(force, fresh, wait_seconds, budget_seconds)``)
   with the current depth schema until a round is not ``partial`` (every folder examined) or made
   no progress (nothing created and no new reason found), at most ``MAX_ROUNDS``. One round still
   registers at most ``MAX_NEW_REQUESTS_PER_RUN`` requests, so several rounds cover a backlog.
3. Every request with a live folder registration (``folder_auto_sync.registered_scopes``) is checked
   with ``folder_auto_sync.sync_scope(force=True)``, the same check the 5-minute server auto-sync
   and the screens use (drive mode: no DB connection is held while the drive is read).

The result reports every unregistered folder with its reason (path, code, Korean message), merged
over the rounds and deduplicated by path + code, and every request whose check failed or kept a
conflict. The drive and the storage are only read: DB rows are the only writes, so the rescan is
available in SCX drive mode too.

One rescan runs at a time per process, in a daemon thread (``INLINE`` runs it in the caller for
tests). The thread never raises: an unexpected error ends the run as ``FAILED`` with a message.
The state of the current or last run is kept in memory only (a restart forgets it).
"""
from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from ..database_connection import connect

logger = logging.getLogger(__name__)

MAX_ROUNDS = 20
# Wait for a background discovery run (bounded by its own 45 s budget) instead of skipping a round.
ROUND_WAIT_SECONDS = 180.0
# One round may deep-scan one large request folder: allow more than the screens' 45 s budget.
ROUND_BUDGET_SECONDS = 300.0
# Runs in a daemon thread; tests set it to True to run inline.
INLINE = False

class RescanError(RuntimeError):
    """A rescan stopped for a reason the administrator can act on (message shown as is)."""


_lock = threading.Lock()
_state: dict[str, Any] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _initial(started_by: str | None = None) -> dict[str, Any]:
    return {"status": "IDLE", "started_at": None, "finished_at": None, "started_by": started_by,
            "phase": None, "progress": {"done": 0, "total": 0}, "created_projects": [], "created_requests": [],
            "needs_review": [], "rounds": 0, "scopes": 0, "refreshed": 0, "failed": [], "partial": False,
            "error": None}


def state() -> dict[str, Any]:
    """The current or last rescan (``IDLE`` before the first one in this process)."""
    with _lock:
        return copy.deepcopy(_state) if _state else _initial()


def _update(**fields: Any) -> None:
    with _lock:
        _state.update(fields)


def reset_for_tests() -> None:
    with _lock:
        _state.clear()


def start(actor: str, started_by: str | None = None) -> tuple[dict[str, Any], bool]:
    """Start a rescan unless one is running; returns (state, started)."""
    with _lock:
        if _state.get("status") == "RUNNING":
            return copy.deepcopy(_state), False
        _state.clear()
        _state.update(_initial(started_by), status="RUNNING", started_at=_now_iso(), phase="DISCOVER")
    if INLINE:
        _run(actor)
    else:
        threading.Thread(target=_run, args=(actor,), name="storage-rescan", daemon=True).start()
    return state(), True


def _review_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("relative_path") or "").casefold(), str(item.get("code") or "")


def _discover_all() -> None:
    from . import folder_auto_discovery

    folder_auto_discovery.clear_review_memos()
    reviews: dict[tuple[str, str], dict[str, Any]] = {}
    created_projects: list[dict[str, Any]] = []
    created_requests: list[dict[str, Any]] = []
    partial = False
    for round_number in range(1, MAX_ROUNDS + 1):
        result = folder_auto_discovery.discover(force=True, fresh=True, wait_seconds=ROUND_WAIT_SECONDS,
                                                budget_seconds=ROUND_BUDGET_SECONDS)
        status = result.get("status")
        if status == "DISABLED":
            reviews[("", "AUTO_DISCOVERY_DISABLED")] = {
                "relative_path": "", "code": "AUTO_DISCOVERY_DISABLED", "environment": None,
                "reason": "자동 탐색이 꺼져 있어(SIMDASH_AUTO_DISCOVERY) 새 폴더를 찾지 않았습니다."}
            break
        if status == "RUNNING":
            raise RescanError("다른 폴더 확인이 끝나지 않아 새 폴더를 찾지 못했습니다. 잠시 후 다시 실행하세요.")
        known = len(reviews)
        for item in result.get("needs_review") or []:
            reviews[_review_key(item)] = dict(item)
        created_projects.extend(result.get("created_projects") or [])
        created_requests.extend(result.get("created_requests") or [])
        partial = bool(result.get("partial"))
        progressed = bool(result.get("created_requests")) or len(reviews) > known
        if partial and not progressed:
            # The same folder ran out of the round's time again: report it instead of looping.
            for path in result.get("deferred") or []:
                reviews[(str(path).casefold(), "DISCOVERY_TIME_BUDGET")] = {
                    "relative_path": path, "code": "DISCOVERY_TIME_BUDGET", "environment": None,
                    "reason": f"폴더 조사가 한 번의 확인 시간({int(ROUND_BUDGET_SECONDS)}초) 안에 끝나지 않았습니다. "
                              "폴더 조사 화면에서 직접 등록하세요."}
        _update(rounds=round_number, created_projects=list(created_projects),
                created_requests=list(created_requests), needs_review=list(reviews.values()), partial=partial)
        if status != "CHECKED" or not partial or not progressed:
            break


def _names(conn, scopes: list[tuple[str, str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    projects = {str(row[0]): str(row[1] or "") for row in conn.execute("SELECT id,name FROM projects").fetchall()}
    requests: dict[str, str] = {}
    for request_id in {scope[1] for scope in scopes}:
        row = conn.execute("SELECT title FROM analysis_requests WHERE id=?", [request_id]).fetchone()
        requests[request_id] = str(row[0] or "") if row else ""
    return projects, requests


def _sync_all(actor: str) -> None:
    from . import folder_auto_sync

    with connect() as conn:
        scopes = folder_auto_sync.registered_scopes(conn)
        projects, requests = _names(conn, scopes)
    _update(phase="SYNC", scopes=len(scopes), progress={"done": 0, "total": len(scopes)})
    refreshed = 0
    failed: list[dict[str, Any]] = []
    for index, (project_id, request_id, environment) in enumerate(scopes, start=1):
        base = {"project_id": project_id, "project_name": projects.get(project_id, ""), "request_id": request_id,
                "request_name": requests.get(request_id, ""), "environment": environment}
        try:
            result = folder_auto_sync.sync_scope(project_id, request_id, environment, actor, force=True)
        except Exception as exc:  # noqa: BLE001 - one request never stops the others
            logger.warning("Storage rescan: sync failed project=%s request=%s environment=%s error=%s",
                           project_id, request_id, environment, type(exc).__name__)
            failed.append({**base, "code": type(exc).__name__, "message": "의뢰 폴더를 확인하지 못했습니다."})
        else:
            if result.get("changed") and not result.get("coalesced"):
                refreshed += 1
            if result.get("status") in {"FAILED", "CONFLICT"}:
                failed.append({**base, "code": str(result.get("code") or result.get("status")),
                               "message": str(result.get("message") or "")})
        _update(progress={"done": index, "total": len(scopes)}, refreshed=refreshed, failed=list(failed))


def _run(actor: str) -> None:
    try:
        _discover_all()
        _sync_all(actor)
        _update(status="DONE", finished_at=_now_iso())
        current = state()
        logger.info("Storage rescan done: %d round(s), %d new request(s), %d need review, %d scope(s), "
                    "%d refreshed, %d failed", current["rounds"], len(current["created_requests"]),
                    len(current["needs_review"]), current["scopes"], current["refreshed"], len(current["failed"]))
    except Exception as exc:  # noqa: BLE001 - never raise out of the thread
        logger.warning("Storage rescan failed", exc_info=True)
        message = str(exc) if isinstance(exc, RescanError) else "저장 폴더를 확인하지 못했습니다."
        _update(status="FAILED", finished_at=_now_iso(), error=message)
