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
* Only exact SPDM request names are registered: ``[WR-<digits>]_[사용_환경]``,
  ``[WR-<digits>]_[유통_환경]`` and the legacy ``WR_<id>_SimType1|2``. A
  request-like name that is not an exact match (a copy, ``_old``, a rename) or
  whose WR number + environment is already linked in the same project is
  reported (``NAME_NOT_STANDARD`` / ``WR_ALREADY_LINKED``), never registered.
* A caller never waits for a run: the memo is checked and the run lock is
  taken without blocking before a DB connection is opened; a concurrent call
  gets the last result with ``status: RUNNING``. A run is bounded by
  ``RUN_TIME_BUDGET_SECONDS`` (checked before every step) and
  ``MAX_NEW_REQUESTS_PER_RUN``.
* A folder whose registration failed in a retryable way is retried with a
  per-folder backoff (``RETRY_BACKOFF_SECONDS``); ``force`` does not bypass it.
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

from ..database_connection import connect
from . import folder_discovery, folder_discovery_environment as environment_service, spdm_storage
from .folder_discovery_scan import root_identity

MIN_INTERVAL_SECONDS = 60.0
FORCE_MIN_INTERVAL_SECONDS = 10.0
# A request kept in needs_review is examined again when its top folders change
# or after this many seconds, so an unresolved folder is not deep-scanned every run.
NEEDS_REVIEW_RETRY_SECONDS = 600.0
MAX_CONTAINER_LEVELS = 2
MAX_LISTED_ENTRIES = 20000
MAX_NEW_REQUESTS_PER_RUN = 5
RUN_TIME_BUDGET_SECONDS = 45.0
RETRY_BACKOFF_SECONDS = (120.0, 300.0, 600.0)
ACTIVE_STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")

_PROJECT = re.compile(r"^(?:[A-Z0-9]{5}_PV|(?:project|prj)[_-].+)$", re.I)
# Anything that looks like a request folder (reported when it is not an exact name).
_REQUEST = re.compile(r"^(?:\[\s*WR[-_ ]?[A-Za-z0-9]|WR[-_ ][A-Za-z0-9]|WR\d)", re.I)
_STANDARD = re.compile(r"^\[WR-(\d+)\]_\[(사용|유통)_환경\]$")
_SIMTYPE = re.compile(r"^WR_([A-Za-z0-9._-]+)_SimType([12])$")
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
_memo: dict[str, Any] = {}
_review_memo: dict[tuple[str, str, str], dict[str, Any]] = {}
_retry_memo: dict[tuple[str, str, str], dict[str, Any]] = {}
_exclusion_cache: dict[str, Any] = {}


def reset_for_tests() -> None:
    with _state_lock:
        _memo.clear()
        _review_memo.clear()
        _retry_memo.clear()
        _exclusion_cache.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_identity(name: str) -> tuple[str, str] | None:
    """(WR number key, environment) for an exact SPDM request name, else None."""
    standard = _STANDARD.match(name)
    if standard:
        return standard.group(1), "USAGE" if standard.group(2) == "사용" else "DISTRIBUTION"
    simtype = _SIMTYPE.match(name)
    if simtype:
        return simtype.group(1).casefold(), "USAGE" if simtype.group(2) == "1" else "DISTRIBUTION"
    return None


def request_environment(name: str) -> str | None:
    """Environment of an exact SPDM request name, or None."""
    identity = request_identity(name)
    return identity[1] if identity else None


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
                    if entry.name.startswith((".", "~$")):
                        continue
                    child = Path(entry.path)
                    try:
                        if spdm_storage._is_reparse(child) or not entry.is_dir(follow_symlinks=False):
                            continue
                    except (OSError, spdm_storage.SpdmStorageError):
                        # One unreadable child must not hide its siblings.
                        self.issues.append({"relative_path": child.relative_to(self.root).as_posix(),
                                            "code": "PATH_UNAVAILABLE"})
                        continue
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
    request_paths: set[str] = set()
    projects: dict[str, set[str]] = {}
    keys: set[str] = set()
    for path, role, target in conn.execute(
            "SELECT g.relative_path,g.role_kind,g.target_id FROM folder_environment_registry g "
            "JOIN folder_environment_registrations r ON r.id=g.registration_id "
            "WHERE g.root_key=? AND g.role_kind IN ('PROJECT','REQUEST') AND r.status IN (?,?,?,?)",
            [root_key, *ACTIVE_STATUSES]).fetchall():
        if role == "REQUEST":
            requests.add(str(path).casefold())
            request_paths.add(str(path))
        else:
            projects.setdefault(str(path).casefold(), set()).add(str(target))
    for project_folder, request_folder, project_id in conn.execute(
            "SELECT project_folder,request_folder,project_id FROM spdm_storage_request_parents").fetchall():
        requests.add(str(request_folder).casefold())
        request_paths.add(str(request_folder))
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
            request_paths.add(str(path))
        else:
            projects.setdefault(str(path).casefold(), set()).add(str(target))
    for (key,) in conn.execute(
            "SELECT idempotency_key FROM folder_environment_registrations WHERE idempotency_key LIKE 'auto-discovery-%'"
    ).fetchall():
        keys.add(str(key))
    # Keep only project links whose project still exists.
    existing = {str(row[0]) for row in conn.execute("SELECT id FROM projects").fetchall()}
    projects = {path: {target for target in targets if target in existing} for path, targets in projects.items()}
    projects = {path: targets for path, targets in projects.items() if targets}
    linked_ids = {target for targets in projects.values() for target in targets}
    unlinked_names: dict[str, str] = {}
    for project_id, name, product_name in conn.execute("SELECT id,name,product_name FROM projects").fetchall():
        if str(project_id) in linked_ids:
            continue
        for value in (name, product_name):
            if value:
                unlinked_names.setdefault(str(value).strip().casefold(), str(project_id))
    return {"requests": requests, "request_paths": request_paths, "projects": projects, "keys": keys, "unlinked_project_names": unlinked_names,
            "excluded": _manual_exclusions(conn)}


def _manual_exclusions(conn) -> set[str]:
    """Folders an administrator explicitly excluded in an applied registration preview.

    Decoding every preview is costly, so the set is cached until the number or
    latest time of administrator registrations changes.
    """
    marker = tuple(conn.execute(
        "SELECT count(*),max(created_at) FROM folder_environment_registrations WHERE created_by<>?",
        [SYSTEM.user_id]).fetchone())
    with _state_lock:
        if _exclusion_cache.get("marker") == marker:
            return set(_exclusion_cache["paths"])
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
    with _state_lock:
        _exclusion_cache.update(marker=marker, paths=frozenset(excluded))
    return excluded


def _is_excluded(path: str, excluded: set[str]) -> bool:
    folded = path.casefold()
    return any(folded == item or folded.startswith(item.rstrip("/") + "/") for item in excluded)


def _project_candidates(lister: _Lister, linked: dict[str, Any],
                        needs_review: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(name, relative path) of project folders: by name pattern or an existing project link."""
    found: dict[str, tuple[str, str]] = {}
    level = [("", "")]
    for depth in range(MAX_CONTAINER_LEVELS + 1):
        next_level = []
        for _, relative in level:
            for name, child in lister.children(relative):
                if child.casefold() in linked["requests"]:
                    continue
                if _is_excluded(child, linked["excluded"]):
                    if _PROJECT.match(name):
                        needs_review.append(_review(child, "ADMIN_EXCLUDED", "관리자가 제외한 폴더입니다. 필요하면 폴더 조사에서 다시 연결하세요."))
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
                      project_targets: set[str], deadline: float) -> dict[str, Any]:
    """Run scan -> preview -> registration for one new request folder."""
    request_name = request_path.rsplit("/", 1)[-1]
    if time.monotonic() > deadline:
        return {"deferred": True}
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
    if time.monotonic() > deadline:
        return {"deferred": True}
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
    if time.monotonic() > deadline:
        return {"deferred": True}
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
        "SELECT project_id,request_id,created_by,preview_id FROM folder_environment_registrations WHERE idempotency_key=?",
        [key]).fetchone()
    if not stored:
        return {"review": failure or _review(request_path, "REGISTRATION_FAILED", "등록하지 못했습니다.", environment),
                "retry": True}
    if str(stored[2]) != SYSTEM.user_id or str(stored[3]) != str(built["id"]):
        # Another process won the race with its own preview: not created by this run.
        return {"skipped": True}
    return {"registration": {"project_id": stored[0], "request_id": stored[1]}, "request_name": request_name,
            "project_name": project_name, "project_created": not project_existed}


_ISSUE_REASONS = {
    "LIST_LIMIT": "폴더 항목 수가 많아 일부 폴더를 확인하지 못했습니다.",
    "PATH_UNAVAILABLE": "폴더에 접근할 수 없습니다.",
}


def _project_requests(linked: dict[str, Any], project_path: str) -> set[tuple[str, str]]:
    """(WR number, environment) of requests already linked directly under a project folder."""
    prefix = project_path.casefold().rstrip("/") + "/"
    identities = set()
    for path in linked["request_paths"]:
        if path.casefold().startswith(prefix) and "/" not in path[len(prefix):]:
            identity = request_identity(path[len(prefix):])
            if identity:
                identities.add(identity)
    return identities


def _discover(conn, root) -> dict[str, Any]:
    root_key = root_identity(root)
    linked = _linked_state(conn, root_key)
    lister = _Lister(root)
    created_projects: list[dict[str, str]] = []
    created_requests: list[dict[str, str]] = []
    needs_review: list[dict[str, Any]] = []
    deadline = time.monotonic() + RUN_TIME_BUDGET_SECONDS
    attempts = 0
    stopped = False
    for project_name, project_path in _project_candidates(lister, linked, needs_review):
        if stopped:
            break
        children = lister.children(project_path)
        project_targets = set(linked["projects"].get(project_path.casefold(), set()))
        if len(project_targets) > 1:
            needs_review.append(_review(project_path, "PROJECT_AMBIGUOUS", "프로젝트 폴더가 여러 프로젝트에 연결되어 있습니다."))
            continue
        if not project_targets and project_name.strip().casefold() in linked["unlinked_project_names"]:
            needs_review.append(_review(project_path, "PROJECT_NAME_EXISTS",
                                        "같은 이름의 프로젝트가 이미 있습니다. 폴더 조사에서 기존 프로젝트에 연결하세요."))
            continue
        known = _project_requests(linked, project_path)
        for name, request_path in children:
            if not _REQUEST.match(name):
                continue
            folded = request_path.casefold()
            if folded in linked["requests"]:
                continue
            if _is_excluded(request_path, linked["excluded"]):
                needs_review.append(_review(request_path, "ADMIN_EXCLUDED",
                                            "관리자가 제외한 폴더입니다. 필요하면 폴더 조사에서 다시 연결하세요."))
                continue
            identity = request_identity(name)
            if identity is None:
                needs_review.append(_review(request_path, "NAME_NOT_STANDARD",
                                            "의뢰 폴더 이름이 SPDM 형식([WR-번호]_[사용_환경]/[유통_환경])과 다릅니다."))
                continue
            environment = identity[1]
            if idempotency_key(root_key, request_path, environment) in linked["keys"]:
                continue
            if identity in known:
                needs_review.append(_review(request_path, "WR_ALREADY_LINKED",
                                            "같은 의뢰번호·환경의 다른 폴더가 이미 연결되어 있습니다.", environment))
                continue
            memo_key = (root_key, folded, environment)
            now = time.monotonic()
            with _state_lock:
                waiting = _retry_memo.get(memo_key)
                remembered = _review_memo.get(memo_key)
            if waiting and now < waiting["until"]:
                needs_review.append(waiting["review"])
                continue
            stamp = (lister.mtime(request_path), lister.mtime(request_path + "/Working"))
            if remembered and remembered["stamp"] == stamp and now - remembered["at"] < NEEDS_REVIEW_RETRY_SECONDS:
                needs_review.append(remembered["review"])
                continue
            if attempts >= MAX_NEW_REQUESTS_PER_RUN or now > deadline:
                stopped = True
                break
            attempts += 1
            siblings = [path for _, path in children if path.casefold() != folded]
            outcome = _register_request(conn, root, root_key, project_name, project_path, siblings,
                                        request_path, environment, project_targets, deadline)
            if outcome.get("deferred"):
                stopped = True
                break
            if outcome.get("skipped"):
                linked["requests"].add(folded)
                known.add(identity)
                continue
            if "review" in outcome:
                needs_review.append(outcome["review"])
                with _state_lock:
                    if outcome.get("retry"):
                        failures = int((waiting or {}).get("failures", 0))
                        delay = RETRY_BACKOFF_SECONDS[min(failures, len(RETRY_BACKOFF_SECONDS) - 1)]
                        _retry_memo[memo_key] = {"until": time.monotonic() + delay, "failures": failures + 1,
                                                 "review": outcome["review"]}
                    else:
                        _review_memo[memo_key] = {"stamp": stamp, "at": time.monotonic(), "review": outcome["review"]}
                continue
            with _state_lock:
                _review_memo.pop(memo_key, None)
                _retry_memo.pop(memo_key, None)
            registered = outcome["registration"]
            project_id, request_id = str(registered["project_id"]), str(registered["request_id"])
            if outcome["project_created"]:
                created_projects.append({"id": project_id, "name": project_name})
            project_targets = {project_id}
            linked["projects"][project_path.casefold()] = {project_id}
            linked["requests"].add(folded)
            known.add(identity)
            created_requests.append({"id": request_id, "name": outcome["request_name"],
                                     "environment": environment, "project_id": project_id})
    for issue in lister.issues:
        needs_review.append(_review(issue["relative_path"], issue["code"], _ISSUE_REASONS.get(issue["code"], "")))
    return {"created_projects": created_projects, "created_requests": created_requests,
            "needs_review": needs_review, "checked_at": _now_iso(), "coalesced": False,
            "partial": stopped}


def enabled() -> bool:
    """Automatic discovery is on unless SIMDASH_AUTO_DISCOVERY is 0/false/off.

    Only the isolated e2e runner turns it off so its fixture folders are not
    registered behind the tests' backs; deployments keep the default.
    """
    return os.environ.get("SIMDASH_AUTO_DISCOVERY", "1").strip().casefold() not in {"0", "false", "off", "no"}


def _empty(status: str) -> dict[str, Any]:
    return {"created_projects": [], "created_requests": [], "needs_review": [],
            "checked_at": _now_iso(), "coalesced": True, "status": status}


def discover(*, force: bool = False, connection_factory=None) -> dict[str, Any]:
    """Register new SPDM project/request folders under the configured storage root.

    Never blocks on another run and opens a DB connection only for an actual run.
    """
    if not enabled():
        return _empty("DISABLED")
    interval = FORCE_MIN_INTERVAL_SECONDS if force else MIN_INTERVAL_SECONDS

    def remembered() -> dict[str, Any] | None:
        with _state_lock:
            memo = dict(_memo)
        if memo and time.monotonic() - memo["at"] < interval:
            return {**memo["result"], "coalesced": True}
        return None

    cached = remembered()
    if cached:
        return cached
    if not _run_lock.acquire(blocking=False):
        with _state_lock:
            last = _memo.get("result")
        return {**last, "coalesced": True, "status": "RUNNING"} if last else _empty("RUNNING")
    try:
        cached = remembered()
        if cached:
            return cached
        with (connection_factory or connect)() as conn:
            try:
                root = folder_discovery.configured_root(conn)
            except HTTPException:
                return {**_empty("ROOT_UNSET"), "coalesced": False}
            result = {**_discover(conn, root), "status": "CHECKED"}
        with _state_lock:
            _memo.update(at=time.monotonic(), result=result)
        return result
    finally:
        _run_lock.release()


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
