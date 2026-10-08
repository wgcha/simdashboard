"""Automatic Folder Schema sync for screens that are being viewed.

The Case results and materials screens call this when they open and then about
every 30 seconds while visible. Users copy files straight into the SPDM share,
so the screens must follow the folders without a manual Refresh button.

Two levels keep the periodic check cheap:

1. Quick check: walk the request folder and fingerprint names, sizes and
   modification times only (no file contents are read). If that equals the
   active snapshot and nothing else that a full refresh depends on changed
   (rule revision, folder rule profile revision, a newer schema registration),
   the active snapshot is current.
2. Full refresh: otherwise run the existing scoped ``refresh_scope`` (content
   fingerprint, role interpretation, captures).

Checks for the same request scope are coalesced: a check finished less than
``MIN_INTERVAL_SECONDS`` ago is returned again instead of scanning, so many
viewers of one request cause at most one scan per interval per process.

Only folders and result-relevant files (results, media, decks, reports; see
``RESULT_RELEVANT_EXTENSIONS``) count. A solver log that keeps growing inside
the request folder does not trigger a refresh. Known limit: a relevant file
whose contents change while its size and modification time stay identical is
not detected; a later change or a manual Refresh in the folder screen picks it up.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..database_connection import connect
from . import folder_discovery, folder_discovery_environment, spdm_storage
from .folder_discovery_scan import root_identity, scan, stat_fingerprint

MIN_INTERVAL_SECONDS = 20.0
FORCE_MIN_INTERVAL_SECONDS = 5.0

_state_lock = threading.Lock()
_scope_locks: dict[tuple[str, str, str, str], threading.Lock] = {}
# key -> {"checked_monotonic", "result", "snapshot_id", "stat_fingerprint"}
_memo: dict[tuple[str, str, str, str], dict[str, Any]] = {}


def request_in_project(conn, project_id: str, request_id: str) -> bool:
    row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    return bool(row) and str(row[0]) == str(project_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scope_lock(key: tuple[str, str, str, str]) -> threading.Lock:
    with _state_lock:
        lock = _scope_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _scope_locks[key] = lock
        return lock


def _result(status: str, *, changed: bool = False, snapshot_id: str | None = None,
            diff: dict[str, int] | None = None, code: str | None = None,
            message: str | None = None, mode: str = "QUICK") -> dict[str, Any]:
    return {"status": status, "changed": changed, "snapshot_id": snapshot_id,
            "diff": diff or {"added": 0, "removed": 0, "changed": 0},
            "code": code, "message": message, "check_mode": mode, "checked_at": _now_iso()}


def _quick_current(conn, root, root_key: str, project_id: str, request_id: str,
                   environment: str, memo: dict[str, Any] | None) -> tuple[bool, str | None, str | None]:
    """Return (current, snapshot_id, quick fingerprint) for the active snapshot."""
    from . import folder_schema_resolver as resolver

    previous = resolver.active_refresh_snapshot(conn, root_key, project_id, request_id, environment)
    if not previous:
        return False, None, None
    schema = resolver._decode(previous["schema_json"], code="FOLDER_SCHEMA_SNAPSHOT_INVALID",
                              message="저장된 폴더 구조를 읽을 수 없습니다.")
    if not isinstance(schema, dict):
        return False, previous["id"], None
    if schema.get("role_rules_revision") != folder_discovery_environment.ROLE_RULES_REVISION:
        return False, previous["id"], None
    profile = conn.execute("SELECT revision FROM folder_environment_profiles WHERE id=?",
                           [previous["profile_id"]]).fetchone()
    if not profile or int(profile[0]) != int(previous["profile_revision"]):
        return False, previous["id"], None
    newer_registration = conn.execute(
        # §13.5: a DELETED registration is never a sync target or a reason to refresh.
        "SELECT 1 FROM folder_environment_registrations WHERE project_id=? AND request_id=? AND environment=? "
        "AND status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED') AND created_at>? LIMIT 1",
        [project_id, request_id, environment, previous["created_at"]],
    ).fetchone()
    if newer_registration:
        return False, previous["id"], None
    request_path = str(previous["request_relative_path"])
    try:
        fresh = scan(root, request_path,
                     skip_descendants=folder_discovery_environment._skip_final_archive(request_path))
    except (OSError, ValueError, spdm_storage.SpdmStorageError) as exc:
        raise resolver.FolderSchemaError("FOLDER_SCHEMA_SCAN_UNAVAILABLE",
                                         "현재 의뢰 폴더를 안전하게 조사할 수 없습니다.", 422) from exc
    if fresh.get("status") != "COMPLETE":
        return False, previous["id"], None
    current_fp = stat_fingerprint(fresh)
    return schema.get("stat_fingerprint") == current_fp, previous["id"], current_fp


def _store_quick_fingerprint(conn, snapshot_id: str, fingerprint: str) -> None:
    """Record the quick fingerprint on an active snapshot that has none or an outdated one.

    Snapshots written before stage 2, or ones whose relevant files were only
    touched (same contents), would otherwise need a full content read on every
    check. The field is a cache used only by the quick check.
    """
    row = conn.execute("SELECT tree_json FROM folder_environment_scans WHERE id=?", [snapshot_id]).fetchone()
    if not row:
        return
    try:
        snapshot = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except (TypeError, ValueError):
        return
    schema = snapshot.get("schema") if isinstance(snapshot, dict) else None
    if not isinstance(schema, dict) or schema.get("stat_fingerprint") == fingerprint:
        return
    schema["stat_fingerprint"] = fingerprint
    conn.execute("UPDATE folder_environment_scans SET tree_json=? WHERE id=?",
                 [json.dumps(snapshot, ensure_ascii=False), snapshot_id])


def sync(conn, project_id: str, request_id: str, environment: str, actor: str, *,
         force: bool = False) -> dict[str, Any]:
    """Bring the active Folder Schema snapshot of one request scope up to date.

    ``force`` (the manual "check now") only skips the coalescing interval; it
    still uses the quick check, so it cannot be used to force repeated full
    content reads.  In scx drive mode use :func:`sync_drive` (no connection is
    held while the drive is read); this function then runs inside its session.
    """
    environment = str(environment).upper()
    root = folder_discovery.configured_root(conn)
    root_key = root_identity(root)
    key = (root_key, str(project_id), str(request_id), environment)
    interval = FORCE_MIN_INTERVAL_SECONDS if force else MIN_INTERVAL_SECONDS
    with _state_lock:
        memo = dict(_memo.get(key) or {})
    if memo and time.monotonic() - float(memo.get("checked_monotonic") or 0) < interval:
        return {**memo["result"], "coalesced": True}
    lock = _scope_lock(key)
    with lock:
        with _state_lock:
            memo = dict(_memo.get(key) or {})
        if memo and time.monotonic() - float(memo.get("checked_monotonic") or 0) < interval:
            return {**memo["result"], "coalesced": True}
        return _check_scope(conn, root, root_key, key, project_id, request_id, environment, actor, memo)


def _check_scope(conn, root, root_key: str, key, project_id: str, request_id: str, environment: str, actor: str,
                 memo: dict[str, Any]) -> dict[str, Any]:
    """One check of a request scope; the caller holds the scope lock."""
    from . import folder_schema_resolver as resolver

    snapshot_id = None
    try:
        current, snapshot_id, quick_fp = _quick_current(
            conn, root, root_key, project_id, request_id, environment, memo)
        if current:
            result = _result("UNCHANGED", snapshot_id=snapshot_id)
            _remember(key, result, snapshot_id, quick_fp)
            return {**result, "coalesced": False}
        if (memo and quick_fp and memo.get("result", {}).get("status") == "CONFLICT"
                and memo.get("stat_fingerprint") == quick_fp and memo.get("snapshot_id") == snapshot_id):
            # Same folders and rules as the last conflict: do not re-read
            # contents or audit again until something changes.
            result = {**memo["result"], "checked_at": _now_iso(), "check_mode": "QUICK"}
            _remember(key, result, snapshot_id, quick_fp)
            return {**result, "coalesced": False}
        with folder_discovery.WRITE_LOCK:
            refreshed = folder_discovery_environment.refresh_scope(
                conn, root, project_id, request_id, environment, actor, notify_new_results=True)
            refreshed_fp = refreshed.get("stat_fingerprint")
            if (refreshed.get("status") == "UNCHANGED" and refreshed.get("snapshot_id") and refreshed_fp):
                _store_quick_fingerprint(conn, str(refreshed["snapshot_id"]), str(refreshed_fp))
        if refreshed.get("activated") and refreshed.get("snapshot_id"):
            from . import result_registration
            result_registration.reconcile_schema_refresh_failures(
                conn, project_id, request_id, environment,
                refreshed["snapshot_id"], refreshed["status"], actor,
            )
        status = str(refreshed.get("status") or "")
        conflict = status == "CONFLICT"
        result = _result(
            "CONFLICT" if conflict else ("REFRESHED" if refreshed.get("changed") else "UNCHANGED"),
            changed=bool(refreshed.get("changed")) and not conflict,
            snapshot_id=refreshed.get("snapshot_id"), diff=refreshed.get("diff"), mode="FULL",
            code="FOLDER_SCHEMA_ROLE_CONFLICT" if conflict else None,
            message="폴더 역할이 모호한 항목이 있어 이전 구조를 유지합니다." if conflict else None,
        )
        # Remember the fingerprint of the exact scan the refresh used, never a
        # second scan: a copy finishing in between must not be marked current.
        _remember(key, result, refreshed.get("snapshot_id") if not conflict else snapshot_id, refreshed_fp)
        return {**result, "coalesced": False}
    except (ValueError, OSError, spdm_storage.SpdmStorageError) as exc:
        # FolderSchemaError is a ValueError. A file still being copied
        # (FOLDER_SCHEMA_FILE_BUSY) lands here and is retried next poll.
        result = _failed(exc, snapshot_id)
        _remember(key, result, None, None)
        return {**result, "coalesced": False}


def _failed(exc: BaseException, snapshot_id: str | None) -> dict[str, Any]:
    from . import folder_schema_resolver as resolver

    return _result("FAILED", snapshot_id=snapshot_id,
                   code=str(getattr(exc, "code", "FOLDER_SCHEMA_REFRESH_FAILED")),
                   message=str(exc) if isinstance(exc, resolver.FolderSchemaError)
                   else "폴더를 확인하지 못했습니다. 잠시 후 다시 확인합니다.",
                   mode="FULL")


def sync_drive(project_id: str, request_id: str, environment: str, actor: str, *,
               force: bool = False) -> dict[str, Any]:
    """scx drive mode: :func:`sync` without holding a DB connection while the drive is read (plan D2).

    Same coalescing as local mode.  A per-scope drive lock (taken without any DB
    connection) serialises the drive work of one request; inside it a drive read
    session first classifies the request folder (new files registered, changed
    files recorded as pending, deleted files MISSING; ``drive.sources``), then
    runs the ordinary check with the registered versions applied.  The check
    takes the connection first and the scope lock second, the same order as
    local mode and the registration delete (no lock-order inversion on DuckDB).
    The result carries ``drive`` counts for the pending-change badge.
    """
    from .drive import reads, sources

    environment = str(environment).upper()
    current = spdm_storage.drive_storage_root()
    if current is None or current.root is None:
        return {**_result("FAILED", code="SPDM_ROOT_UNSET", message="SIMDASH_SCX_DRIVE_ROOT가 설정되지 않았습니다.",
                          mode="FULL"), "coalesced": False}
    root = current.root
    root_key = root_identity(root)
    key = (root_key, str(project_id), str(request_id), environment)
    interval = FORCE_MIN_INTERVAL_SECONDS if force else MIN_INTERVAL_SECONDS

    def remembered() -> dict[str, Any] | None:
        with _state_lock:
            memo = dict(_memo.get(key) or {})
        if memo and time.monotonic() - float(memo.get("checked_monotonic") or 0) < interval:
            return {**memo["result"], "coalesced": True}
        return None

    cached = remembered()
    if cached is not None:
        return cached
    with _drive_lock(key):
        cached = remembered()
        if cached is not None:
            return cached
        classified: dict[str, Any] = {}

        def prepare(session) -> None:
            classified.update(sources.classify_request(session, project_id, request_id, environment, actor))

        def body() -> dict[str, Any]:
            with connect() as conn:
                with _scope_lock(key):
                    with _state_lock:
                        memo = dict(_memo.get(key) or {})
                    checked = _check_scope(conn, root, root_key, key, project_id, request_id, environment, actor, memo)
                counts = sources.summary(conn, root_key, str(project_id), str(request_id))
            return {**checked, "drive": {**counts, "token_kind": classified.get("token_kind"),
                                         "new_files": classified.get("new", 0)}}

        try:
            result = reads.run(body, prepare=prepare, priority="BACKGROUND")
        except (ValueError, OSError, spdm_storage.SpdmStorageError) as exc:
            result = {**_failed(exc, None), "coalesced": False}
            _remember(key, result, None, None)
            return result
        with _state_lock:
            if key in _memo:
                _memo[key]["result"] = {**_memo[key]["result"], "drive": result.get("drive")}
        return result


_drive_locks: dict[tuple[str, str, str, str], threading.Lock] = {}


def _drive_lock(key: tuple[str, str, str, str]) -> threading.Lock:
    """Per-scope lock for the drive phase of :func:`sync_drive` (never taken while holding a DB connection)."""
    with _state_lock:
        lock = _drive_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _drive_locks[key] = lock
        return lock


def _remember(key, result: dict[str, Any], snapshot_id: str | None, stat_fp: str | None) -> None:
    with _state_lock:
        _memo[key] = {"checked_monotonic": time.monotonic(), "result": result,
                      "snapshot_id": snapshot_id, "stat_fingerprint": stat_fp}


def invalidate(root_key: str, project_id: str, request_id: str, environment: str) -> None:
    """Forget the coalesced result of one scope so the next check scans (W8: after the app wrote files)."""
    with _state_lock:
        _memo.pop((root_key, str(project_id), str(request_id), str(environment).upper()), None)


def invalidate_request(root_key: str, project_id: str, request_id: str) -> None:
    """Forget the coalesced results of a request in every environment (drive source change decided)."""
    with _state_lock:
        for key in [key for key in _memo if key[:3] == (root_key, str(project_id), str(request_id))]:
            _memo.pop(key, None)


def reset_for_tests() -> None:
    with _state_lock:
        _memo.clear()
        _scope_locks.clear()
        _drive_locks.clear()
