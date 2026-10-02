"""Automatic discovery of new SPDM project and request folders.

The company SPDM system creates project folders (``75R9J_PV``, ``prj_*``) and
request folders (``[WR-0001]_[유통_환경]``, ``WR_x_SimType1``) in the shared
storage root. This module finds the ones that are not linked yet and runs the
same scan -> preview -> registration pipeline the administrator flow uses, so
they appear in the project and request selectors without a manual step.

Scope and safety:

* Only shallow levels are listed: root -> up to ``MAX_CONTAINER_LEVELS``
  container folders -> project -> request. Only a NEW request folder is scanned
  in depth, once, with the environment's active folder rule profile; sibling
  requests and the request ``Final`` folder keep their descendants unread.
* The environment comes only from the request folder name. A request-like name
  without a determinable environment, a preview with unresolved roles or an
  incomplete scan is not registered and is returned in ``needs_review``.
* A project is created only together with its first registered request, in
  the same registration transaction, and without any membership grant.
* Idempotent: a folder that is already linked (environment registry, SPDM
  request binding, legacy folder registry) or whose deterministic idempotency
  key was already used is skipped. A process lock serialises runs and the
  registration's unique idempotency key guards concurrent processes.
* Runs are throttled per process and storage root (``MIN_INTERVAL_SECONDS``;
  ``force`` only shortens it to ``FORCE_MIN_INTERVAL_SECONDS``).
* Files are never moved, written or deleted. No client input reaches a path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import folder_discovery, folder_discovery_environment as environment_service, spdm_storage
from .folder_discovery_scan import root_identity

MIN_INTERVAL_SECONDS = 60.0
FORCE_MIN_INTERVAL_SECONDS = 10.0
# A request kept in needs_review is examined again when its top folders change
# or after this many seconds, so an unresolved folder is not deep-scanned every run.
NEEDS_REVIEW_RETRY_SECONDS = 600.0
MAX_CONTAINER_LEVELS = 2
MAX_LISTED_ENTRIES = 20000
MAX_NEW_REQUESTS_PER_RUN = 20
RUN_TIME_BUDGET_SECONDS = 60.0
ACTIVE_STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")

_PROJECT = re.compile(r"^(?:[A-Z0-9]{5}_PV|(?:project|prj)[_-].+)$", re.I)
_REQUEST = re.compile(r"^(?:\[WR[-_][A-Za-z0-9]+\]|WR[-_][A-Za-z0-9]+)(?:_|\s|$)", re.I)
_SIMTYPE = re.compile(r"^WR_[A-Za-z0-9._-]+_SimType([12])$", re.I)
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SystemPrincipal:
    """Actor recorded on rows the discovery creates (never granted membership)."""
    user_id: str = "system-folder-discovery"
    username: str = "folder-discovery"
    display_name: str = "폴더 자동 확인"
    is_global_admin: bool = False
    account_status: str = "ACTIVE"


SYSTEM = SystemPrincipal()

_run_lock = threading.Lock()
_state_lock = threading.Lock()
_memo: dict[str, dict[str, Any]] = {}
_review_memo: dict[tuple[str, str, str], dict[str, Any]] = {}


def reset_for_tests() -> None:
    with _state_lock:
        _memo.clear()
        _review_memo.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_environment(name: str) -> str | None:
    """Environment from a request folder name, or None when it is not determinable."""
    simtype = _SIMTYPE.match(name)
    if simtype:
        return "USAGE" if simtype.group(1) == "1" else "DISTRIBUTION"
    usage, distribution = "사용" in name, "유통" in name
    if usage == distribution:
        return None
    return "USAGE" if usage else "DISTRIBUTION"


def idempotency_key(root_key: str, relative_path: str, environment: str) -> str:
    digest = hashlib.sha256(f"{root_key}:{relative_path.casefold()}:{environment}".encode("utf-8")).hexdigest()
    return f"auto-discovery-{digest[:48]}"


class _Lister:
    """Bounded, non-following listing of direct child folders."""

    def __init__(self, root: Path):
        self.root = root
        self.entries = 0
        self.issues: list[dict[str, str]] = []

    def children(self, relative: str) -> list[tuple[str, str]]:
        path = self.root.joinpath(*relative.split("/")) if relative else self.root
        found: list[tuple[str, str]] = []
        try:
            spdm_storage._assert_safe_existing(path, self.root)
            with os.scandir(path) as iterator:
                for entry in iterator:
                    self.entries += 1
                    if self.entries > MAX_LISTED_ENTRIES:
                        self.issues.append({"relative_path": relative, "code": "LIST_LIMIT"})
                        break
                    child = Path(entry.path)
                    if entry.name.startswith((".", "~$")) or spdm_storage._is_reparse(child):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        found.append((entry.name, child.relative_to(self.root).as_posix()))
        except (OSError, ValueError, spdm_storage.SpdmStorageError):
            self.issues.append({"relative_path": relative, "code": "PATH_UNAVAILABLE"})
        return sorted(found, key=lambda item: item[1].casefold())

    def mtime(self, relative: str) -> int | None:
        try:
            info = self.root.joinpath(*relative.split("/")).stat()
        except OSError:
            return None
        return int(getattr(info, "st_mtime_ns", info.st_mtime * 1_000_000_000))


def _linked_state(conn, root_key: str) -> dict[str, Any]:
    """Folder paths already linked to a project or request (casefolded)."""
    requests: set[str] = set()
    projects: dict[str, set[str]] = {}
    keys: set[str] = set()
    for path, role, target in conn.execute(
            "SELECT g.relative_path,g.role_kind,g.target_id FROM folder_environment_registry g "
            "JOIN folder_environment_registrations r ON r.id=g.registration_id "
            "WHERE g.root_key=? AND g.role_kind IN ('PROJECT','REQUEST') AND r.status IN (?,?,?,?)",
            [root_key, *ACTIVE_STATUSES]).fetchall():
        if role == "REQUEST":
            requests.add(str(path).casefold())
        else:
            projects.setdefault(str(path).casefold(), set()).add(str(target))
    for project_folder, request_folder, project_id in conn.execute(
            "SELECT project_folder,request_folder,project_id FROM spdm_storage_request_parents").fetchall():
        requests.add(str(request_folder).casefold())
        projects.setdefault(str(project_folder).casefold(), set()).add(str(project_id))
    try:
        legacy_rows = conn.execute(
            "SELECT relative_path,role_kind,target_id FROM folder_discovery_registry "
            "WHERE root_key=? AND role_kind IN ('PROJECT','REQUEST')", [root_key]).fetchall()
    except Exception:  # pragma: no cover - legacy table missing on a minimal schema
        legacy_rows = []
    for path, role, target in legacy_rows:
        if role == "REQUEST":
            requests.add(str(path).casefold())
        else:
            projects.setdefault(str(path).casefold(), set()).add(str(target))
    for (key,) in conn.execute(
            "SELECT idempotency_key FROM folder_environment_registrations WHERE idempotency_key LIKE 'auto-discovery-%'"
    ).fetchall():
        keys.add(str(key))
    # Keep only project links whose project still exists.
    existing = {str(row[0]) for row in conn.execute("SELECT id FROM projects").fetchall()}
    projects = {path: {target for target in targets if target in existing} for path, targets in projects.items()}
    return {"requests": requests, "projects": {path: targets for path, targets in projects.items() if targets},
            "keys": keys, "excluded": _manual_exclusions(conn)}


def _manual_exclusions(conn) -> set[str]:
    """Folders an administrator explicitly excluded in an applied registration preview."""
    excluded: set[str] = set()
    for (value,) in conn.execute(
            "SELECT p.rows_json FROM folder_environment_registrations r "
            "JOIN folder_environment_previews p ON p.id=r.preview_id "
            "WHERE r.created_by<>? AND r.status IN (?,?,?,?)", [SYSTEM.user_id, *ACTIVE_STATUSES]).fetchall():
        try:
            saved = environment_service.preview_data(value)
        except (TypeError, ValueError):
            continue
        for state in saved.get("node_states") or []:
            if (isinstance(state, dict) and state.get("status") == "EXCLUDED"
                    and state.get("role_source") == "PREVIEW" and state.get("relative_path")):
                excluded.add(str(state["relative_path"]).casefold())
    return excluded


def _is_excluded(path: str, excluded: set[str]) -> bool:
    folded = path.casefold()
    return any(folded == item or folded.startswith(item.rstrip("/") + "/") for item in excluded)


def _project_candidates(lister: _Lister, linked: dict[str, Any]) -> list[tuple[str, str]]:
    """(name, relative path) of project folders: by name pattern or an existing project link."""
    found: dict[str, tuple[str, str]] = {}
    level = [("", "")]
    for depth in range(MAX_CONTAINER_LEVELS + 1):
        next_level = []
        for _, relative in level:
            for name, child in lister.children(relative):
                if _is_excluded(child, linked["excluded"]) or child.casefold() in linked["requests"]:
                    continue
                if _PROJECT.match(name) or child.casefold() in linked["projects"]:
                    found[child.casefold()] = (name, child)
                elif depth < MAX_CONTAINER_LEVELS and not _REQUEST.match(name):
                    next_level.append((name, child))
        level = next_level
    return sorted(found.values(), key=lambda item: item[1].casefold())


def _review(relative_path: str, code: str, reason: str, environment: str | None = None) -> dict[str, Any]:
    return {"relative_path": relative_path, "code": code, "reason": reason, "environment": environment}


def _register_request(conn, root, root_key: str, project_name: str, project_path: str,
                      siblings: list[str], request_path: str, environment: str,
                      project_targets: set[str]) -> dict[str, Any]:
    """Run scan -> preview -> registration for one new request folder."""
    request_name = request_path.rsplit("/", 1)[-1]
    final_paths = [child for name, child in _Lister(root).children(request_path) if name.casefold() == "final"]
    try:
        scanned = environment_service.save_scan(
            conn, root, project_path, environment, None, None, None, SYSTEM.user_id,
            skip_paths=[*siblings, *final_paths],
        )
    except (ValueError, spdm_storage.SpdmStorageError) as exc:
        return {"review": _review(request_path, "SCAN_FAILED", f"폴더를 조사할 수 없습니다: {str(exc)[:120]}", environment)}
    if scanned["status"] != "COMPLETE":
        return {"review": _review(request_path, "SCAN_INCOMPLETE", "조사 한도 초과 또는 접근할 수 없는 폴더가 있습니다.", environment)}
    by_path = {node["relative_path"].casefold(): node for node in scanned["nodes"]}
    project_node = by_path.get(project_path.casefold())
    request_node = by_path.get(request_path.casefold())
    if not project_node or not request_node:
        return {"review": _review(request_path, "SCAN_FAILED", "프로젝트 또는 의뢰 폴더를 찾을 수 없습니다.", environment)}
    assignments: list[dict[str, Any]] = []
    if project_targets:
        assignments.append({"node_id": project_node["id"], "role_kind": "PROJECT", "confirm": True,
                            "target_mode": "LINK", "target_id": next(iter(project_targets))})
    elif project_node.get("role_kind") != "PROJECT":
        assignments.append({"node_id": project_node["id"], "role_kind": "PROJECT", "confirm": True})
    if request_node.get("role_kind") != "REQUEST":
        assignments.append({"node_id": request_node["id"], "role_kind": "REQUEST", "confirm": True})
    sibling_keys = {path.casefold() for path in siblings}
    for node in scanned["nodes"]:
        if node["relative_path"].casefold() in sibling_keys:
            assignments.append({"node_id": node["id"], "role_kind": "EXCLUDE", "confirm": True})
    try:
        built = environment_service.preview(conn, scanned["id"], assignments, SYSTEM.user_id,
                                            allow_without_cases=True)
    except (ValueError, HTTPException) as exc:
        return {"review": _review(request_path, "PREVIEW_FAILED", f"등록 미리보기를 만들 수 없습니다: {str(exc)[:120]}", environment)}
    if not built["can_apply"]:
        return {"review": _review(request_path, "ROLES_UNRESOLVED",
                                  f"역할을 확인할 수 없는 폴더 {built['unresolved_count']}개가 있습니다. 폴더 규칙을 확인하세요.",
                                  environment)}
    key = idempotency_key(root_key, request_path, environment)
    project_row = next((row for row in built["rows"] if row.get("role_kind") == "PROJECT"), None)
    project_existed = bool(project_row and project_row.get("target_id") and conn.execute(
        "SELECT 1 FROM projects WHERE id=?", [project_row["target_id"]]).fetchone())
    failure: dict[str, Any] | None = None
    try:
        with folder_discovery.WRITE_LOCK:
            environment_service.register(conn, built["id"], key, True, SYSTEM, root, creator_membership=False)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        failure = _review(request_path, str(detail.get("code") or "REGISTRATION_FAILED"),
                          str(detail.get("message") or "등록하지 못했습니다.")[:160], environment)
    except Exception as exc:  # e.g. a concurrent process inserted the same key or project first
        _logger.warning("Automatic request registration failed path_hash=%s error_type=%s",
                        hashlib.sha256(request_path.encode("utf-8")).hexdigest()[:12], type(exc).__name__)
        failure = _review(request_path, "REGISTRATION_FAILED", "등록하지 못했습니다. 다음 확인에서 다시 시도합니다.", environment)
    # The registration is durable before its capture phase; report what was
    # actually stored even when a later step raised.
    stored = conn.execute(
        "SELECT project_id,request_id,created_by FROM folder_environment_registrations WHERE idempotency_key=?",
        [key]).fetchone()
    if not stored:
        return {"review": failure or _review(request_path, "REGISTRATION_FAILED", "등록하지 못했습니다.", environment),
                "retry": True}
    if str(stored[2]) != SYSTEM.user_id:
        return {"skipped": True}
    return {"registration": {"project_id": stored[0], "request_id": stored[1]}, "request_name": request_name,
            "project_name": project_name, "project_created": not project_existed}


def _discover(conn, root) -> dict[str, Any]:
    root_key = root_identity(root)
    linked = _linked_state(conn, root_key)
    lister = _Lister(root)
    created_projects: list[dict[str, str]] = []
    created_requests: list[dict[str, str]] = []
    needs_review: list[dict[str, Any]] = []
    started = time.monotonic()
    attempts = 0
    for project_name, project_path in _project_candidates(lister, linked):
        children = lister.children(project_path)
        project_targets = set(linked["projects"].get(project_path.casefold(), set()))
        if len(project_targets) > 1:
            needs_review.append(_review(project_path, "PROJECT_AMBIGUOUS", "프로젝트 폴더가 여러 프로젝트에 연결되어 있습니다."))
            continue
        for name, request_path in children:
            if not _REQUEST.match(name) and not _SIMTYPE.match(name):
                continue
            folded = request_path.casefold()
            if folded in linked["requests"] or _is_excluded(request_path, linked["excluded"]):
                continue
            environment = request_environment(name)
            if environment is None:
                needs_review.append(_review(request_path, "ENVIRONMENT_UNKNOWN",
                                            "폴더 이름에서 사용환경·유통환경을 확인할 수 없습니다."))
                continue
            if idempotency_key(root_key, request_path, environment) in linked["keys"]:
                continue
            memo_key = (root_key, folded, environment)
            stamp = (lister.mtime(request_path), lister.mtime(request_path + "/Working"))
            with _state_lock:
                remembered = _review_memo.get(memo_key)
            if (remembered and remembered["stamp"] == stamp
                    and time.monotonic() - remembered["at"] < NEEDS_REVIEW_RETRY_SECONDS):
                needs_review.append(remembered["review"])
                continue
            if attempts >= MAX_NEW_REQUESTS_PER_RUN or time.monotonic() - started > RUN_TIME_BUDGET_SECONDS:
                break
            attempts += 1
            siblings = [path for _, path in children if path.casefold() != folded]
            outcome = _register_request(conn, root, root_key, project_name, project_path, siblings,
                                        request_path, environment, project_targets)
            if outcome.get("skipped"):
                continue
            if "review" in outcome:
                needs_review.append(outcome["review"])
                if not outcome.get("retry"):
                    with _state_lock:
                        _review_memo[memo_key] = {"stamp": stamp, "at": time.monotonic(), "review": outcome["review"]}
                continue
            with _state_lock:
                _review_memo.pop(memo_key, None)
            registered = outcome["registration"]
            project_id, request_id = str(registered["project_id"]), str(registered["request_id"])
            if outcome["project_created"]:
                created_projects.append({"id": project_id, "name": project_name})
            project_targets = {project_id}
            linked["projects"][project_path.casefold()] = {project_id}
            linked["requests"].add(folded)
            created_requests.append({"id": request_id, "name": outcome["request_name"],
                                     "environment": environment, "project_id": project_id})
    return {"created_projects": created_projects, "created_requests": created_requests,
            "needs_review": needs_review, "checked_at": _now_iso(), "coalesced": False}


def discover(conn, *, force: bool = False) -> dict[str, Any]:
    """Register new SPDM project/request folders under the configured storage root."""
    try:
        root = folder_discovery.configured_root(conn)
    except HTTPException:
        return {"created_projects": [], "created_requests": [], "needs_review": [],
                "checked_at": _now_iso(), "coalesced": False, "status": "ROOT_UNSET"}
    root_key = root_identity(root)
    interval = FORCE_MIN_INTERVAL_SECONDS if force else MIN_INTERVAL_SECONDS

    def remembered() -> dict[str, Any] | None:
        with _state_lock:
            memo = _memo.get(root_key)
        if memo and time.monotonic() - memo["at"] < interval:
            return {**memo["result"], "coalesced": True}
        return None

    cached = remembered()
    if cached:
        return cached
    with _run_lock:
        cached = remembered()
        if cached:
            return cached
        result = {**_discover(conn, root), "status": "CHECKED"}
        with _state_lock:
            _memo[root_key] = {"at": time.monotonic(), "result": result}
        return result


def visible_result(result: dict[str, Any], *, is_global_admin: bool) -> dict[str, Any]:
    """Folder paths needing review are shown to global administrators only."""
    if is_global_admin:
        return result
    return {**result, "needs_review": []}


def audit_detail(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps({
        "created_projects": result["created_projects"],
        "created_requests": result["created_requests"],
        "needs_review_count": len(result["needs_review"]),
    }, ensure_ascii=False))
