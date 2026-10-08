"""Automatic discovery of new SPDM project and request folders.

The company SPDM system creates project folders (``75R9J_PV``, ``prj_*``) and
request folders (``[WR-0001]_[유통_환경]``, ``WR_x_SimType1``) in the shared
storage root. This module finds the ones that are not linked yet and runs the
same scan -> preview -> registration pipeline the administrator flow uses, so
they appear in the project and request selectors without a manual step.

Scope and safety:

* Folder roles come from the current depth schema (``DEPTH_V1``, see
  ``docs/contracts/depth-schema.md`` §8). Only the upper section is listed:
  root -> (CONTAINER levels) -> PROJECT level -> ... -> REQUEST level, by
  depth. Every non-hidden folder at the project level is a project candidate
  and every folder at the request level is a request candidate. Only a NEW
  request folder is scanned in depth, once; sibling requests keep their
  descendants unread and are excluded from its preview.
* The environment comes only from the request folder name keyword (D4):
  ``사용`` -> USAGE, ``유통`` -> DISTRIBUTION. Both or none is reported
  (``ENV_KEYWORD_BOTH`` / ``ENV_KEYWORD_NONE``), never registered. A request
  whose scan has a blocking depth deviation (e.g. ``WORKING_MISSING``) is
  reported as ``DEPTH_DEVIATION`` and not registered; warnings do not block.
* A project is created only together with its first registered request, in
  the same registration transaction, and without any membership grant. An
  already linked project folder is passed as a ``LINK`` assignment.
* Idempotent: a folder that is already linked (live environment registry,
  SPDM request binding, legacy folder registry) or whose deterministic
  idempotency key (path + schema set) was already used by a live
  registration is skipped. Registrations with status ``DELETED`` never count
  (§13.5), so a deleted request folder is registered again with the current
  schema. A process lock serialises runs and the registration's unique
  idempotency key guards concurrent processes.
* Runs are throttled per process and storage root (``MIN_INTERVAL_SECONDS``;
  ``force`` only shortens it to ``FORCE_MIN_INTERVAL_SECONDS``).
* A request whose WR key + environment is already linked in the same project
  (a copy, ``_old``, a rename) is reported (``WR_ALREADY_LINKED``), never
  registered.
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
from . import environment_folder_profiles as depth_profiles
from . import folder_discovery, folder_discovery_environment as environment_service, spdm_storage
from .drive import reads as drive_reads
from .folder_discovery_scan import root_identity
from .storage import provider_for_root

MIN_INTERVAL_SECONDS = 60.0
FORCE_MIN_INTERVAL_SECONDS = 10.0
# A request kept in needs_review is examined again when its top folders change
# or after this many seconds, so an unresolved folder is not deep-scanned every run.
NEEDS_REVIEW_RETRY_SECONDS = 600.0
MAX_LISTED_ENTRIES = 20000
MAX_NEW_REQUESTS_PER_RUN = 5
RUN_TIME_BUDGET_SECONDS = 45.0
RETRY_BACKOFF_SECONDS = (120.0, 300.0, 600.0)
ACTIVE_STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")

# WR key of a request folder name (§8); the casefolded folder name otherwise.
_WR_KEY = re.compile(r"\[?WR[-_]?(\w+)", re.I)
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
# "폴더 구조 만들기" (result_folder_structure): a request folder whose skeleton was just created
# for an existing dashboard request is registered as a LINK to that request instead of a new
# request. (root_key, casefolded path) -> {project_id, request_id, environment, until}. In-process
# only; after a restart the folder is registered by the ordinary rules.
PENDING_LINK_SECONDS = 24 * 3600.0
_pending_links: dict[tuple[str, str], dict[str, Any]] = {}


def reserve_link(root_key: str, request_path: str, project_id: str, request_id: str, environment: str,
                 *, seconds: float = PENDING_LINK_SECONDS) -> None:
    """Register ``request_path`` (once it can be) as the existing request ``request_id`` of ``environment``."""
    with _state_lock:
        _pending_links[(root_key, request_path.casefold())] = {
            "project_id": str(project_id), "request_id": str(request_id), "environment": str(environment),
            "until": time.monotonic() + seconds}


def pending_link(root_key: str, request_path: str) -> dict[str, Any] | None:
    key = (root_key, request_path.casefold())
    with _state_lock:
        found = _pending_links.get(key)
        if found and found["until"] < time.monotonic():
            _pending_links.pop(key, None)
            return None
        return dict(found) if found else None


def _drop_link(root_key: str, request_path: str) -> None:
    with _state_lock:
        _pending_links.pop((root_key, request_path.casefold()), None)


def reset_for_tests() -> None:
    with _state_lock:
        _pending_links.clear()
        _memo.clear()
        _review_memo.clear()
        _retry_memo.clear()
        _exclusion_cache.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_identity(name: str) -> tuple[str, str | None, str | None]:
    """(WR key, environment, keyword deviation code) of a request folder name.

    The environment comes from the name keyword only (D4); ``code`` is
    ``ENV_KEYWORD_BOTH`` / ``ENV_KEYWORD_NONE`` when it cannot be decided.
    """
    environment, code = depth_profiles.keyword_environment(name)
    match = _WR_KEY.match(str(name).strip())
    key = (match.group(1) if match else str(name).strip()).casefold()
    return key, environment, code


def request_environment(name: str) -> str | None:
    """Environment of a request folder name, or None when the keyword is missing or ambiguous."""
    return request_identity(name)[1]


def idempotency_key(root_key: str, relative_path: str, schema_set_id: str, generation: int = 0) -> str:
    """``auto-discovery-<sha(path, schema_set_id)>`` (§13.5).

    ``generation`` > 0 is used only when an earlier key of the same path and
    schema belongs to a DELETED registration, so the folder can be registered
    again (D17) while concurrent processes still derive the same key.
    """
    material = f"{root_key}:{relative_path.casefold()}:{schema_set_id}"
    if generation:
        material += f"#{generation}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"auto-discovery-{digest[:48]}"


def _live_key(root_key: str, relative_path: str, schema_set_id: str, deleted_keys: set[str]) -> str:
    generation = 0
    while True:
        key = idempotency_key(root_key, relative_path, schema_set_id, generation)
        if key not in deleted_keys:
            return key
        generation += 1


class _Lister:
    """Bounded, non-following listing of direct child folders."""

    def __init__(self, root: Path):
        self.root = root
        self.fs = provider_for_root(root)
        self.entries = 0
        self.issues: list[dict[str, str]] = []

    def children(self, relative: str) -> list[tuple[str, str]]:
        path = "/".join(relative.split("/")) if relative else ""
        found: list[tuple[str, str]] = []
        try:
            self.fs.assert_safe(path)
            for entry in self.fs.list(path):
                self.entries += 1
                if self.entries > MAX_LISTED_ENTRIES:
                    self.issues.append({"relative_path": relative, "code": "LIST_LIMIT"})
                    break
                if entry.name.startswith((".", "~$")):
                    continue
                child = self.fs.join(path, entry.name)
                try:
                    if self.fs.is_link(child) or entry.kind != "dir":
                        continue
                except (OSError, spdm_storage.SpdmStorageError):
                    # One unreadable child must not hide its siblings.
                    self.issues.append({"relative_path": child, "code": "PATH_UNAVAILABLE"})
                    continue
                found.append((entry.name, child))
        except (OSError, ValueError, spdm_storage.SpdmStorageError):
            self.issues.append({"relative_path": relative, "code": "PATH_UNAVAILABLE"})
        return sorted(found, key=lambda item: item[1].casefold())

    def mtime(self, relative: str) -> int | None:
        try:
            info = self.fs.stat("/".join(relative.split("/")), follow_links=True, missing_ok=False)
        except OSError:
            return None
        return info.modified_ns


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
    deleted_keys: set[str] = set()
    for key, status in conn.execute(
            "SELECT idempotency_key,status FROM folder_environment_registrations WHERE idempotency_key LIKE 'auto-discovery-%'"
    ).fetchall():
        # §13.5: a DELETED registration's key never blocks a new registration.
        (deleted_keys if str(status) == "DELETED" else keys).add(str(key))
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
    return {"requests": requests, "request_paths": request_paths, "projects": projects, "keys": keys, "deleted_keys": deleted_keys,
            "unlinked_project_names": unlinked_names,
            "excluded": _manual_exclusions(conn)}


def _manual_exclusions(conn) -> set[str]:
    """Folders an administrator explicitly excluded in an applied registration preview.

    Decoding every preview is costly, so the set is cached until the number or
    latest time of live administrator registrations changes (a DELETED
    registration no longer excludes anything, §13.5).
    """
    marker = tuple(conn.execute(
        "SELECT count(*),max(created_at) FROM folder_environment_registrations WHERE created_by<>? "
        "AND status IN (?,?,?,?)", [SYSTEM.user_id, *ACTIVE_STATUSES]).fetchone())
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


def _upper_levels(schema: dict[str, Any]) -> tuple[int, int]:
    """(project_level, request_level) of the current depth schema's upper section."""
    roles = [item["role"] for item in schema["upper"]["levels"]]
    return roles.index("PROJECT") + 1, len(roles)


def _visible(lister: _Lister, relative: str) -> list[tuple[str, str]]:
    return [(name, path) for name, path in lister.children(relative) if not depth_profiles.is_ignored_name(name)]


def _project_candidates(lister: _Lister, linked: dict[str, Any], schema: dict[str, Any],
                        needs_review: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(name, relative path) of every non-hidden folder at the schema's PROJECT level (§8).

    Breadth-first by depth through the upper section; hidden/system folders,
    files and reparse points/symlinks are never followed (§5.1).
    """
    project_level, _request_level = _upper_levels(schema)
    level: list[tuple[str, str]] = [("", "")]
    for _depth in range(1, project_level + 1):
        next_level: list[tuple[str, str]] = []
        for _, relative in level:
            next_level.extend(_visible(lister, relative))
        level = next_level
    found: list[tuple[str, str]] = []
    for name, path in level:
        if _is_excluded(path, linked["excluded"]):
            needs_review.append(_review(path, "ADMIN_EXCLUDED", "관리자가 제외한 폴더입니다. 필요하면 폴더 조사에서 다시 연결하세요."))
            continue
        found.append((name, path))
    return sorted(found, key=lambda item: item[1].casefold())


def _request_candidates(lister: _Lister, schema: dict[str, Any], project_path: str) -> list[tuple[str, str]]:
    """(name, relative path) of request-level folders below one project folder."""
    project_level, request_level = _upper_levels(schema)
    level = [("", project_path)]
    for _depth in range(project_level + 1, request_level + 1):
        next_level: list[tuple[str, str]] = []
        for _, relative in level:
            next_level.extend(_visible(lister, relative))
        level = next_level
    # §15 D22: project-level CAD/Report folders are manual work areas, never requests.
    return [(name, path) for name, path in level if not depth_profiles.is_project_shared_name(name)]


def _review(relative_path: str, code: str, reason: str, environment: str | None = None) -> dict[str, Any]:
    return {"relative_path": relative_path, "code": code, "reason": reason, "environment": environment}


def _register_request(conn, root, root_key: str, project_name: str, project_path: str,
                      siblings: list[str], request_path: str, environment: str,
                      project_targets: set[str], key: str, deadline: float,
                      request_target: str | None = None) -> dict[str, Any]:
    """Run scan -> preview -> registration for one new request folder.

    Roles come from the depth schema only. The preview carries just the
    assignments the depth schema allows (§6): ``EXCLUDE`` for sibling request
    folders and a ``LINK`` for an already linked project. A new project or
    request is created by the default registration plan. The Final branch gets
    roles from the schema but is never a capture source (handled by the core).
    """
    request_name = request_path.rsplit("/", 1)[-1]
    if time.monotonic() > deadline:
        return {"deferred": True}
    try:
        scanned = environment_service.save_scan(
            conn, root, project_path, environment, None, None, None, SYSTEM.user_id, skip_paths=siblings,
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
    if project_node.get("role_kind") != "PROJECT" or request_node.get("role_kind") != "REQUEST":
        return {"review": _review(request_path, "ROLES_UNRESOLVED",
                                  "깊이 스키마로 프로젝트·의뢰 역할을 정할 수 없습니다. 깊이 스키마의 상위 구조를 확인하세요.",
                                  environment)}
    assignments: list[dict[str, Any]] = []
    if project_targets:
        assignments.append({"node_id": project_node["id"], "role_kind": "PROJECT", "confirm": True,
                            "target_mode": "LINK", "target_id": next(iter(project_targets))})
    if request_target:
        # A skeleton created for an existing request ("폴더 구조 만들기"): link instead of creating one.
        assignments.append({"node_id": request_node["id"], "role_kind": "REQUEST", "confirm": True,
                            "target_mode": "LINK", "target_id": request_target})
    sibling_keys = {path.casefold() for path in siblings}
    for node in scanned["nodes"]:
        if node["relative_path"].casefold() in sibling_keys:
            assignments.append({"node_id": node["id"], "role_kind": "EXCLUDE", "confirm": True})
    try:
        built = environment_service.preview(conn, scanned["id"], assignments, SYSTEM.user_id,
                                            allow_without_cases=True)
    except (ValueError, HTTPException) as exc:
        detail = getattr(exc, "detail", None)
        message = detail.get("message") if isinstance(detail, dict) else str(exc)
        return {"review": _review(request_path, "PREVIEW_FAILED", f"등록 미리보기를 만들 수 없습니다: {str(message)[:120]}", environment)}
    blocking = [item for item in built.get("deviations") or [] if item.get("blocking")]
    if blocking:
        first = blocking[0]
        return {"review": {**_review(request_path, "DEPTH_DEVIATION",
                                     f"깊이 스키마와 다른 폴더 {len(blocking)}개: {first['message']}", environment),
                           "deviation_code": first["code"], "deviation_count": len(blocking),
                           "deviation_path": first["relative_path"]}}
    if not built["can_apply"]:
        return {"review": _review(request_path, "ROLES_UNRESOLVED",
                                  str(built.get("message") or
                                      f"역할을 확인할 수 없는 폴더 {built['unresolved_count']}개가 있습니다. 깊이 스키마를 확인하세요."),
                                  environment)}
    if time.monotonic() > deadline:
        return {"deferred": True}
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
        "SELECT project_id,request_id,created_by,preview_id,status FROM folder_environment_registrations WHERE idempotency_key=?",
        [key]).fetchone()
    if not stored:
        return {"review": failure or _review(request_path, "REGISTRATION_FAILED", "등록하지 못했습니다.", environment),
                "retry": True}
    if str(stored[2]) != SYSTEM.user_id or str(stored[3]) != str(built["id"]) or str(stored[4]) == "DELETED":
        # Another process won the race with its own preview: not created by this run.
        return {"skipped": True}
    return {"registration": {"project_id": stored[0], "request_id": stored[1]}, "request_name": request_name,
            "project_name": project_name, "project_created": not project_existed}


_ISSUE_REASONS = {
    "LIST_LIMIT": "폴더 항목 수가 많아 일부 폴더를 확인하지 못했습니다.",
    "PATH_UNAVAILABLE": "폴더에 접근할 수 없습니다.",
}


def _project_requests(linked: dict[str, Any], project_path: str) -> set[tuple[str, str]]:
    """(WR key, environment) of requests already linked below a project folder."""
    prefix = project_path.casefold().rstrip("/") + "/"
    identities = set()
    for path in linked["request_paths"]:
        if path.casefold().startswith(prefix):
            key, environment, _code = request_identity(path.rsplit("/", 1)[-1])
            if environment:
                identities.add((key, environment))
    return identities


def _discover(conn, root) -> dict[str, Any]:
    root_key = root_identity(root)
    lister = _Lister(root)
    created_projects: list[dict[str, str]] = []
    created_requests: list[dict[str, str]] = []
    needs_review: list[dict[str, Any]] = []
    try:
        schema = depth_profiles.get_depth_schema(conn)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        needs_review.append(_review("", str(detail.get("code") or "DEPTH_SCHEMA_MISSING"),
                                    str(detail.get("message") or "현재 깊이 스키마가 없습니다.")))
        return {"created_projects": [], "created_requests": [], "needs_review": needs_review,
                "checked_at": _now_iso(), "coalesced": False, "partial": False}
    schema_set_id = str(schema["schema_set_id"])
    linked = _linked_state(conn, root_key)
    deadline = time.monotonic() + RUN_TIME_BUDGET_SECONDS
    attempts = 0
    stopped = False
    for project_name, project_path in _project_candidates(lister, linked, schema, needs_review):
        if stopped:
            break
        project_targets = set(linked["projects"].get(project_path.casefold(), set()))
        if len(project_targets) > 1:
            needs_review.append(_review(project_path, "PROJECT_AMBIGUOUS", "프로젝트 폴더가 여러 프로젝트에 연결되어 있습니다."))
            continue
        candidates = _request_candidates(lister, schema, project_path)
        unlinked = [(name, path) for name, path in candidates if path.casefold() not in linked["requests"]]
        if not unlinked:
            continue
        pending = {path.casefold(): link for _, path in unlinked if (link := pending_link(root_key, path))}
        if not project_targets and pending:
            project_targets = {next(iter(pending.values()))["project_id"]}
        if not project_targets and project_name.strip().casefold() in linked["unlinked_project_names"]:
            needs_review.append(_review(project_path, "PROJECT_NAME_EXISTS",
                                        "같은 이름의 프로젝트가 이미 있습니다. 폴더 조사에서 기존 프로젝트에 연결하세요."))
            continue
        known = _project_requests(linked, project_path)
        all_paths = [path for _, path in candidates]
        for name, request_path in unlinked:
            folded = request_path.casefold()
            if _is_excluded(request_path, linked["excluded"]):
                needs_review.append(_review(request_path, "ADMIN_EXCLUDED",
                                            "관리자가 제외한 폴더입니다. 필요하면 폴더 조사에서 다시 연결하세요."))
                continue
            wr_key, environment, keyword_code = request_identity(name)
            if keyword_code:
                needs_review.append(_review(request_path, keyword_code, depth_profiles.DEVIATION_MESSAGES[keyword_code]))
                continue
            key = _live_key(root_key, request_path, schema_set_id, linked["deleted_keys"])
            if key in linked["keys"]:
                continue
            identity = (wr_key, environment)
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
            stamp = (schema_set_id, lister.mtime(request_path), lister.mtime(request_path + "/Working"))
            if remembered and remembered["stamp"] == stamp and now - remembered["at"] < NEEDS_REVIEW_RETRY_SECONDS:
                needs_review.append(remembered["review"])
                continue
            if attempts >= MAX_NEW_REQUESTS_PER_RUN or now > deadline:
                stopped = True
                break
            attempts += 1
            siblings = [path for path in all_paths if path.casefold() != folded]
            link = pending.get(folded)
            request_target = (link["request_id"] if link and link["environment"] == environment
                              and link["project_id"] in project_targets else None)
            outcome = _register_request(conn, root, root_key, project_name, project_path, siblings,
                                        request_path, environment, project_targets, key, deadline,
                                        request_target=request_target)
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
            if request_target:
                _drop_link(root_key, request_path)
            if outcome["project_created"]:
                created_projects.append({"id": project_id, "name": project_name})
            project_targets = {project_id}
            linked["projects"][project_path.casefold()] = {project_id}
            linked["requests"].add(folded)
            linked["keys"].add(key)
            known.add(identity)
            created_requests.append({"id": request_id, "name": outcome["request_name"],
                                     "environment": environment, "project_id": project_id,
                                     "linked": bool(request_target)})
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
        def run_once() -> dict[str, Any]:
            with (connection_factory or connect)() as conn:
                try:
                    root = folder_discovery.configured_root(conn)
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    # Not silent: administrators see why nothing is discovered.
                    return {**_empty("ROOT_UNSET"), "coalesced": False,
                            "needs_review": [_review("", "SPDM_ROOT_UNSET", str(
                                detail.get("message") or "SPDM 저장소 루트가 설정되지 않아 자동 탐색을 하지 않았습니다."))]}
                return {**_discover(conn, root), "status": "CHECKED"}

        # SCX drive (D2): listings and capture files are fetched between rounds without a DB
        # connection; a round repeats only reads and idempotent steps (local mode: one plain call).
        result = drive_reads.run(run_once, priority="BACKGROUND")
        if result.get("status") != "CHECKED":
            return result
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
