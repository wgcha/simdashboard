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

Known limit (same as refresh fingerprints for non-content files): a file whose
contents change while its size and modification time stay identical is not
detected by the quick check. The manual check (``force``) runs a full refresh.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from . import folder_discovery, folder_discovery_environment, spdm_storage
from .folder_discovery_scan import root_identity, scan, stat_fingerprint

MIN_INTERVAL_SECONDS = 20.0
FORCE_MIN_INTERVAL_SECONDS = 5.0

_state_lock = threading.Lock()
_scope_locks: dict[tuple[str, str, str, str], threading.Lock] = {}
# key -> {"checked_monotonic", "result", "snapshot_id", "stat_fingerprint"}
_memo: dict[tuple[str, str, str, str], dict[str, Any]] = {}


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
    """Return (current, snapshot_id, stat_fp) for the active snapshot."""
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
        "SELECT 1 FROM folder_environment_registrations WHERE project_id=? AND request_id=? AND environment=? "
        "AND created_at>? LIMIT 1",
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
    stored = schema.get("stat_fingerprint")
    if stored == current_fp:
        return True, previous["id"], current_fp
    if memo and memo.get("snapshot_id") == previous["id"] and memo.get("stat_fingerprint") == current_fp:
        return True, previous["id"], current_fp
    return False, previous["id"], current_fp


def sync(conn, project_id: str, request_id: str, environment: str, actor: str, *,
         force: bool = False) -> dict[str, Any]:
    """Bring the active Folder Schema snapshot of one request scope up to date."""
    from . import folder_schema_resolver as resolver

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
        stat_fp = None
        snapshot_id = None
        try:
            if not force:
                current, snapshot_id, stat_fp = _quick_current(
                    conn, root, root_key, project_id, request_id, environment, memo)
                if current:
                    result = _result("UNCHANGED", snapshot_id=snapshot_id)
                    _remember(key, result, snapshot_id, stat_fp)
                    return {**result, "coalesced": False}
            with folder_discovery.WRITE_LOCK:
                refreshed = folder_discovery_environment.refresh_scope(
                    conn, root, project_id, request_id, environment, actor)
            if refreshed.get("activated") and refreshed.get("snapshot_id"):
                from . import result_registration
                result_registration.reconcile_schema_refresh_failures(
                    conn, project_id, request_id, environment,
                    refreshed["snapshot_id"], refreshed["status"], actor,
                )
            status = str(refreshed.get("status") or "")
            result = _result(
                "CONFLICT" if status == "CONFLICT" else ("REFRESHED" if refreshed.get("changed") else "UNCHANGED"),
                changed=bool(refreshed.get("changed")), snapshot_id=refreshed.get("snapshot_id"),
                diff=refreshed.get("diff"), mode="FULL",
                code="FOLDER_SCHEMA_ROLE_CONFLICT" if status == "CONFLICT" else None,
                message="폴더 역할이 모호한 항목이 있어 이전 구조를 유지합니다." if status == "CONFLICT" else None,
            )
            if status != "CONFLICT":
                if stat_fp is None:
                    try:
                        request_path = resolver._request_path(conn, root_key, project_id, request_id, environment)
                        stat_fp = stat_fingerprint(scan(
                            root, request_path,
                            skip_descendants=folder_discovery_environment._skip_final_archive(request_path)))
                    except (OSError, ValueError, spdm_storage.SpdmStorageError, resolver.FolderSchemaError):
                        stat_fp = None
                _remember(key, result, refreshed.get("snapshot_id"), stat_fp)
            else:
                _remember(key, result, None, None)
            return {**result, "coalesced": False}
        except (ValueError, spdm_storage.SpdmStorageError) as exc:
            # FolderSchemaError is a ValueError. A file still being copied
            # (FOLDER_SCHEMA_FILE_BUSY) lands here and is retried next poll.
            result = _result("FAILED", snapshot_id=snapshot_id,
                             code=str(getattr(exc, "code", "FOLDER_SCHEMA_REFRESH_FAILED")), message=str(exc),
                             mode="QUICK" if not force else "FULL")
            _remember(key, result, None, None)
            return {**result, "coalesced": False}


def _remember(key, result: dict[str, Any], snapshot_id: str | None, stat_fp: str | None) -> None:
    with _state_lock:
        _memo[key] = {"checked_monotonic": time.monotonic(), "result": result,
                      "snapshot_id": snapshot_id, "stat_fingerprint": stat_fp}


def reset_for_tests() -> None:
    with _state_lock:
        _memo.clear()
        _scope_locks.clear()
