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
3. Re-apply the current depth schema (issue #55, 2026-10-20; depth-schema §8, D9): every registered
   request whose latest live registration was made with an older depth ``schema_set_id`` than the
   current one is reinterpreted (``folder_discovery_environment.reinterpret_request``: scan ->
   preview -> register with the current schema, then the snapshot is activated). A blocking deviation
   changes nothing and the deviations are reported. Workflow: edit the depth schema, press
   [저장 폴더 새로고침]. Drive mode: each request runs in its own drive read session (no DB connection is
   held while the drive is read); the phase has its own time budget (``REINTERPRET_BUDGET_SECONDS``),
   the requests left over stay stale and are reinterpreted by the next rescan.
4. Every request with a live folder registration (``folder_auto_sync.registered_scopes``) is checked
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
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..database_connection import connect

logger = logging.getLogger(__name__)

MAX_ROUNDS = 20
# Wait for a background discovery run (bounded by its own 45 s budget) instead of skipping a round.
ROUND_WAIT_SECONDS = 180.0
# One round may deep-scan one large request folder: allow more than the screens' 45 s budget.
ROUND_BUDGET_SECONDS = 300.0
# Reinterpreting registered requests with the current depth schema: time budget of the whole phase.
REINTERPRET_BUDGET_SECONDS = 1800.0
REINTERPRET_ACTIVE_STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")
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
            "reinterpreted": [], "reinterpret_blocked": [], "reinterpret_unchanged": 0, "reinterpret_deferred": 0,
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
        _run(actor, started_by)
    else:
        threading.Thread(target=_run, args=(actor, started_by), name="storage-rescan", daemon=True).start()
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
                    "reason": f"폴더 조사가 한 번의 확인(시간 {int(ROUND_BUDGET_SECONDS)}초·드라이브 조회 횟수) 안에 끝나지 않았습니다. "
                              "폴더 조사 화면에서 직접 등록하세요."}
        _update(rounds=round_number, created_projects=list(created_projects),
                created_requests=list(created_requests), needs_review=list(reviews.values()), partial=partial)
        if status != "CHECKED" or not partial or not progressed:
            break


@dataclass(frozen=True)
class _Actor:
    """The administrator who started the rescan (principal fields the registration reads)."""
    user_id: str
    username: str
    display_name: str
    is_global_admin: bool = True
    account_status: str = "ACTIVE"


def _schema_set_ids(conn, profile_ids: set[str]) -> dict[str, str | None]:
    """Depth ``schema_set_id`` of the profile rows (None for a profile that is not a depth schema)."""
    found: dict[str, str | None] = {}
    for profile_id in profile_ids:
        row = conn.execute("SELECT rules_json FROM folder_environment_profiles WHERE id=?", [profile_id]).fetchone()
        rules: Any = row[0] if row else None
        if isinstance(rules, (str, bytes)):
            try:
                rules = json.loads(rules)
            except ValueError:
                rules = None
        value = rules.get("schema_set_id") if isinstance(rules, dict) else None
        found[profile_id] = str(value) if value else None
    return found


def _stale_requests(conn) -> tuple[list[dict[str, Any]], int]:
    """(requests whose latest live registration used an older depth schema set, count of the others).

    One entry per request: the latest live registration decides. A registration whose profile is not a
    depth schema (nothing to compare) counts as up to date.
    """
    from . import environment_folder_profiles as depth_profiles

    try:
        current = str(depth_profiles.get_depth_schema(conn)["schema_set_id"])
    except Exception:  # noqa: BLE001 - no current depth schema: nothing to apply
        return [], 0
    marks = ",".join("?" for _ in REINTERPRET_ACTIVE_STATUSES)
    rows = conn.execute(
        "SELECT r.project_id,r.request_id,r.environment,s.profile_id FROM folder_environment_registrations r "
        "JOIN folder_environment_previews p ON p.id=r.preview_id JOIN folder_environment_scans s ON s.id=p.scan_id "
        f"WHERE r.status IN ({marks}) AND r.project_id IS NOT NULL AND r.request_id IS NOT NULL "
        "ORDER BY r.created_at DESC, r.id DESC", list(REINTERPRET_ACTIVE_STATUSES)).fetchall()
    latest: dict[str, tuple[str, str, str]] = {}
    for project_id, request_id, environment, profile_id in rows:
        latest.setdefault(str(request_id), (str(project_id), str(environment).upper(), str(profile_id)))
    sets = _schema_set_ids(conn, {item[2] for item in latest.values()})
    stale = [{"project_id": project_id, "request_id": request_id, "environment": environment}
             for request_id, (project_id, environment, profile_id) in latest.items()
             if sets.get(profile_id) and sets[profile_id] != current]
    projects, requests = _names(conn, [(item["project_id"], item["request_id"], item["environment"]) for item in stale])
    for item in stale:
        item["project_name"] = projects.get(item["project_id"], "")
        item["request_name"] = requests.get(item["request_id"], "")
    stale.sort(key=lambda item: (item["project_name"].casefold(), item["request_name"].casefold(), item["request_id"]))
    return stale, len(latest) - len(stale)


def _deviation_summary(deviations: Any, limit: int = 5) -> list[dict[str, Any]]:
    return [{"code": str(item.get("code") or ""), "message": str(item.get("message") or ""),
             "relative_path": str(item.get("relative_path") or ""), "blocking": bool(item.get("blocking"))}
            for item in list(deviations or [])[:limit] if isinstance(item, dict)]


def _reinterpret_one(actor: _Actor, item: dict[str, Any]) -> dict[str, Any]:
    """Reinterpret one request in its own drive read session (local mode: a plain call)."""
    from . import folder_discovery, folder_discovery_environment as environment_service, folder_schema_resolver as resolver
    from .drive import reads as drive_reads
    from .folder_discovery_scan import root_identity

    def body() -> dict[str, Any]:
        with connect() as conn:
            root = folder_discovery.configured_root(conn)
            try:
                path = resolver._request_path(conn, root_identity(root), item["project_id"], item["request_id"],
                                              item["environment"])
            except Exception:  # noqa: BLE001 - reinterpret reports the missing binding itself
                path = None
            if path:
                drive_reads.require_subtree(path)   # drive mode: list the whole request folder in one round
            return environment_service.reinterpret_request(conn, actor, item["request_id"], root=root)

    return drive_reads.run(body, priority="BACKGROUND", rounds=drive_reads.MAX_ROUNDS)


def _reinterpret_all(actor: str, started_by: str | None = None) -> None:
    """Re-apply the current depth schema to every registered request on an older one (module docstring 3)."""
    with connect() as conn:
        stale, unchanged = _stale_requests(conn)
    _update(phase="REINTERPRET", progress={"done": 0, "total": len(stale)}, reinterpret_unchanged=unchanged)
    if not stale:
        return
    principal = _Actor(user_id=str(actor), username=str(started_by or actor), display_name=str(started_by or actor))
    started = time.monotonic()
    reinterpreted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for index, item in enumerate(stale, start=1):
        if time.monotonic() - started > REINTERPRET_BUDGET_SECONDS:
            _update(reinterpret_deferred=len(stale) - index + 1, partial=True)
            break
        base = {key: item[key] for key in ("project_id", "project_name", "request_id", "request_name", "environment")}
        try:
            result = _reinterpret_one(principal, item)
        except Exception as exc:  # noqa: BLE001 - one request never stops the others
            detail = getattr(exc, "detail", None)
            code = str(detail.get("code") if isinstance(detail, dict) and detail.get("code")
                       else getattr(exc, "code", None) or type(exc).__name__)
            message = str(detail.get("message") if isinstance(detail, dict) and detail.get("message") else exc)[:300]
            logger.warning("Storage rescan: reinterpret failed request=%s code=%s", item["request_id"], code)
            blocked.append({**base, "code": code, "message": message or "현재 깊이 스키마를 적용하지 못했습니다.",
                            "deviations": [], "deviation_count": 0})
        else:
            deviations = result.get("deviations") or []
            if result.get("registered"):
                reinterpreted.append({**base, "environment": result.get("environment") or item["environment"],
                                      "registration_id": result.get("registration_id"),
                                      "message": result.get("message") or "",
                                      "deviations": _deviation_summary(deviations), "deviation_count": len(deviations)})
            else:
                blocking = [entry for entry in deviations if isinstance(entry, dict) and entry.get("blocking")] or deviations
                first = blocking[0] if blocking and isinstance(blocking[0], dict) else {}
                blocked.append({**base, "code": str(first.get("code") or "REINTERPRET_BLOCKED"),
                                "message": str(result.get("message") or "깊이 스키마와 다른 폴더가 있어 적용하지 않았습니다."),
                                "deviations": _deviation_summary(blocking), "deviation_count": len(blocking)})
        _update(progress={"done": index, "total": len(stale)}, reinterpreted=list(reinterpreted),
                reinterpret_blocked=list(blocked))


def listed_reviews() -> dict[str, str]:
    """Node key -> needs-review code of the folders an administrator can see now (last rescan, last discovery)."""
    from . import folder_auto_discovery
    from .drive import reads as drive_reads

    found: dict[str, str] = {}
    for item in [*folder_auto_discovery.last_reviews(), *(state().get("needs_review") or [])]:
        found[drive_reads.path_key(str(item.get("relative_path") or ""))] = str(item.get("code") or "")
    return found


def mark_excluded(paths: list[str]) -> None:
    """The rescan result moves excluded folders to ``ADMIN_EXCLUDED`` (restorable) without another rescan."""
    from .drive import reads as drive_reads

    keys = {drive_reads.path_key(path) for path in paths}
    with _lock:
        for item in _state.get("needs_review") or []:
            if drive_reads.path_key(str(item.get("relative_path") or "")) in keys and item.get("code") != "ADMIN_EXCLUDED":
                item.update(previous_code=item.get("code"), previous_reason=item.get("reason"), code="ADMIN_EXCLUDED",
                            restorable=True, reason="관리자가 제외한 폴더입니다. [다시 포함]으로 되돌릴 수 있습니다.")


def mark_restored(paths: list[str]) -> None:
    """A restored folder returns to its earlier review item (or a note to rescan when it has none)."""
    from .drive import reads as drive_reads

    keys = {drive_reads.path_key(path) for path in paths}
    with _lock:
        for item in _state.get("needs_review") or []:
            if drive_reads.path_key(str(item.get("relative_path") or "")) not in keys or item.get("code") != "ADMIN_EXCLUDED":
                continue
            previous = item.pop("previous_code", None)
            reason = item.pop("previous_reason", None)
            item.pop("restorable", None)
            if previous:
                item.update(code=previous, reason=reason or "")
            else:
                item.update(code="EXCLUSION_REMOVED",
                            reason="제외를 해제했습니다. 다음 [저장 폴더 새로고침]에서 등록 여부를 확인합니다.")


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


def _run(actor: str, started_by: str | None = None) -> None:
    try:
        _discover_all()
        _reinterpret_all(actor, started_by)
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
