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
  (a copy, ``_old``) is reported (``WR_ALREADY_LINKED``), never registered.
  A rename (2026-10-17): when the linked folder is positively gone from storage
  and exactly one unlinked folder of the project has its WR number +
  environment, the existing request is re-linked to the new folder (a LINK
  registration that tombstones the old folder's registrations in the same
  transaction; request id, Cases and captures are kept) and reported in
  ``renamed_requests``. Two or more such folders are ``RENAME_AMBIGUOUS``.
  A renamed PROJECT folder (2026-10-17, :func:`_project_renames`): when every folder a project is
  linked at is positively gone and exactly one unlinked project folder carries its requests' WR
  numbers + environments (and no other project's), the new folder is linked to the same project
  and each request folder is followed as above; unmatched requests are registered under that
  project. Reported in ``renamed_projects``; ambiguity is ``PROJECT_RENAME_AMBIGUOUS``.
* A caller never waits for a run: the memo is checked and the run lock is
  taken without blocking before a DB connection is opened; a concurrent call
  gets the last result with ``status: RUNNING``. A run is bounded by
  ``RUN_TIME_BUDGET_SECONDS`` (checked before every step) and
  ``MAX_NEW_REQUESTS_PER_RUN``.
* A folder whose registration failed in a retryable way is retried with a
  per-folder backoff (``RETRY_BACKOFF_SECONDS``); ``force`` does not bypass it.
* Files are never moved, written or deleted. No client input reaches a path.
* SCX drive (issue #51, 2026-10-17): a run is one drive read session of ``DISCOVERY_READ_ROUNDS``
  rounds. Request folder listings are fetched in one round and a new request's tree in one round
  before its scan (``drive_reads.require_listings`` / ``require_subtree``); a pass repeated after a
  registration keeps that registration in the result (``carry``). A folder that cannot be read or
  raises becomes a needs-review item (``DRIVE_READ_LIMIT``, ``DRIVE_READ_FAILED``,
  ``DISCOVERY_FAILED``, ``FOLDER_NAME_INVALID``); the call never fails with 500.
* The administrator's full rescan (``storage_rescan``, [저장 폴더 새로고침]) calls
  :func:`discover` with ``fresh`` (no memo), ``wait_seconds`` (waits for a
  background run instead of returning ``RUNNING``) and a longer
  ``budget_seconds``, after :func:`clear_review_memos`, so every unregistered
  folder is examined again and reported with its reason.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
# SCX drive read rounds of one run (issue #51): upper levels + per new request one round for its folder
# tree (``drive_reads.require_subtree``) and one for its capture files (``require_content``), with slack.
DISCOVERY_READ_ROUNDS = drive_reads.MAX_ROUNDS + 3 * MAX_NEW_REQUESTS_PER_RUN
ACTIVE_STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")

# WR number of a request folder name (§8): the ASCII letters/digits right after ``WR-``/``WR_``
# (or ``WR`` + digit), optionally inside ``[...]``. The key stops at the first other character, so
# ``_``, spaces, ``]`` and Korean text never join it (``WR-0000806_유통환경시뮬레이션`` -> ``0000806``).
# Leading zeros are kept as written: the number is copied from SPDM, never retyped.
_WR_KEY = re.compile(r"\[?WR(?:[-_]([0-9A-Za-z]+)|(\d[0-9A-Za-z]*))", re.I)
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
# request. Reservations live in ``folder_link_reservations`` (migration 0041; review M3), so every
# worker process sees them and they survive a restart; ``expires_at`` bounds them.
PENDING_LINK_SECONDS = 24 * 3600.0


class LinkReserved(ValueError):
    """An unexpired reservation of the folder belongs to another request (review L1)."""

    def __init__(self, reservation: dict[str, Any]) -> None:
        self.reservation = reservation
        super().__init__("folder reserved for another request")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _path_key(request_path: str) -> str:
    return "/".join(part.casefold() for part in str(request_path).split("/") if part)


def _node_key(relative_path: str) -> str:
    """Comparison key of a scanned folder path: NFC + casefold per segment (one form for every source)."""
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in str(relative_path).split("/") if part)


def _reservation(conn, root_key: str, path_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT project_id, request_id, environment, created_by, expires_at FROM folder_link_reservations "
                       "WHERE root_key=? AND path_key=? AND expires_at>?", [root_key, path_key, _utc_now()]).fetchone()
    if not row:
        return None
    return {"project_id": str(row[0]), "request_id": str(row[1]), "environment": str(row[2]),
            "created_by": str(row[3]), "expires_at": row[4]}


def reserve_link(conn, root_key: str, request_path: str, project_id: str, request_id: str, environment: str,
                 *, created_by: str, seconds: float = PENDING_LINK_SECONDS) -> None:
    """Register ``request_path`` (once it can be) as the existing request ``request_id`` of ``environment``.

    Written in the caller's transaction. Raises :class:`LinkReserved` when an unexpired reservation of
    the same folder belongs to another (project, request, environment); an expired one is replaced.
    """
    now = _utc_now()
    key = _path_key(request_path)
    conn.execute("DELETE FROM folder_link_reservations WHERE root_key=? AND path_key=? AND expires_at<=?",
                 [root_key, key, now])
    conn.execute("INSERT INTO folder_link_reservations (root_key, path_key, project_id, request_id, environment, "
                 "created_by, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (root_key, path_key) DO NOTHING",
                 [root_key, key, str(project_id), str(request_id), str(environment), str(created_by), now,
                  now + timedelta(seconds=seconds)])
    found = _reservation(conn, root_key, key)
    if found and (found["project_id"], found["request_id"], found["environment"]) != (str(project_id), str(request_id), str(environment)):
        raise LinkReserved(found)
    conn.execute("UPDATE folder_link_reservations SET expires_at=? WHERE root_key=? AND path_key=?",
                 [now + timedelta(seconds=seconds), root_key, key])


def reserved_by_other(conn, root_key: str, request_path: str, project_id: str, request_id: str) -> dict[str, Any] | None:
    """The unexpired reservation of ``request_path`` when it belongs to another request (read only)."""
    found = _reservation(conn, root_key, _path_key(request_path))
    if found and (found["project_id"], found["request_id"]) != (str(project_id), str(request_id)):
        return found
    return None


def pending_link(conn, root_key: str, request_path: str) -> dict[str, Any] | None:
    return _reservation(conn, root_key, _path_key(request_path))


def drop_link(conn, root_key: str, request_path: str, *, project_id: str | None = None,
              request_id: str | None = None) -> None:
    """Remove the reservation of ``request_path`` (only this request's one when ``request_id`` is given)."""
    where, params = "root_key=? AND path_key=?", [root_key, _path_key(request_path)]
    if project_id is not None and request_id is not None:
        where += " AND project_id=? AND request_id=?"
        params += [str(project_id), str(request_id)]
    conn.execute(f"DELETE FROM folder_link_reservations WHERE {where}", params)


def reset_for_tests() -> None:
    with _state_lock:
        _memo.clear()
        _review_memo.clear()
        _retry_memo.clear()
        _exclusion_cache.clear()


def clear_review_memos() -> None:
    """Forget remembered needs-review results and retry backoffs (manual full rescan)."""
    with _state_lock:
        _review_memo.clear()
        _retry_memo.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def wr_number(name: str | None) -> str | None:
    """Casefolded WR number of a request folder name or title, or None (``[WR-0001]_[유통_환경]`` -> ``0001``)."""
    match = _WR_KEY.match(str(name or "").strip())
    return (match.group(1) or match.group(2)).casefold() if match else None


def request_identity(name: str) -> tuple[str, str | None, str | None]:
    """(WR key, environment, keyword deviation code) of a request folder name.

    The WR key is :func:`wr_number`, or the casefolded folder name when the name
    has no WR number. The environment comes from the name keyword only (D4);
    ``code`` is ``ENV_KEYWORD_BOTH`` / ``ENV_KEYWORD_NONE`` when it cannot be decided.
    """
    environment, code = depth_profiles.keyword_environment(name)
    key = wr_number(name) or str(name).strip().casefold()
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
        except (OSError, ValueError, spdm_storage.SpdmStorageError) as error:
            # A drive read miss (DriveReadMiss, a BaseException) passes through: the read session
            # lists the folder without a DB connection and runs the discovery again.
            self.issues.append(_listing_issue(relative, error))
        return sorted(found, key=lambda item: item[1].casefold())

    def mtime(self, relative: str) -> int | None:
        """Modification time for the needs-review memo stamp; None when it cannot be read.

        Never raises a storage error (issue #51: a drive read after the session's last round raised
        ``DRIVE_READ_NOT_PREPARED`` here and the screens' discovery call answered 500).
        """
        try:
            info = self.fs.stat("/".join(relative.split("/")), follow_links=True, missing_ok=False)
        except (OSError, ValueError, spdm_storage.SpdmStorageError):
            return None
        return info.modified_ns if info is not None else None


def _error_code(error: BaseException) -> str:
    return str(getattr(error, "code", None) or type(error).__name__)


def _listing_issue(relative: str, error: BaseException) -> dict[str, str]:
    """Lister issue of a folder that could not be listed: the drive's code and operation when known."""
    code = _error_code(error)
    if code == drive_reads.NOT_PREPARED:
        return {"relative_path": relative, "code": "DRIVE_READ_LIMIT"}
    issue = {"relative_path": relative, "code": "PATH_UNAVAILABLE"}
    if drive_reads.active() and isinstance(error, spdm_storage.SpdmStorageError):
        issue.update(code="DRIVE_LIST_FAILED", error_code=code, message=str(error)[:160])
    return issue


def _linked_state(conn, root_key: str) -> dict[str, Any]:
    """Folder paths already linked to a project or request (casefolded)."""
    requests: set[str] = set()
    request_paths: set[str] = set()
    projects: dict[str, set[str]] = {}
    # As written (a project folder rename is checked by listing, which is case-sensitive on Linux).
    project_paths: dict[str, str] = {}
    # Request folder -> projects it is bound to (pairing a renamed project folder, §8).
    request_projects: dict[str, set[str]] = {}
    keys: set[str] = set()
    for path, role, target, project_id in conn.execute(
            "SELECT g.relative_path,g.role_kind,g.target_id,r.project_id FROM folder_environment_registry g "
            "JOIN folder_environment_registrations r ON r.id=g.registration_id "
            "WHERE g.root_key=? AND g.role_kind IN ('PROJECT','REQUEST') AND r.status IN (?,?,?,?)",
            [root_key, *ACTIVE_STATUSES]).fetchall():
        if role == "REQUEST":
            requests.add(str(path).casefold())
            request_paths.add(str(path))
            request_projects.setdefault(str(path).casefold(), set()).add(str(project_id))
        else:
            projects.setdefault(str(path).casefold(), set()).add(str(target))
            project_paths.setdefault(str(path).casefold(), str(path))
    for project_folder, request_folder, project_id in conn.execute(
            "SELECT project_folder,request_folder,project_id FROM spdm_storage_request_parents").fetchall():
        requests.add(str(request_folder).casefold())
        request_paths.add(str(request_folder))
        request_projects.setdefault(str(request_folder).casefold(), set()).add(str(project_id))
        projects.setdefault(str(project_folder).casefold(), set()).add(str(project_id))
        project_paths.setdefault(str(project_folder).casefold(), str(project_folder))
    try:
        legacy_rows = conn.execute(
            "SELECT relative_path,role_kind,target_id,parent_target_id FROM folder_discovery_registry "
            "WHERE root_key=? AND role_kind IN ('PROJECT','REQUEST')", [root_key]).fetchall()
    except Exception:  # pragma: no cover - legacy table missing on a minimal schema
        legacy_rows = []
    for path, role, target, parent_target in legacy_rows:
        if role == "REQUEST":
            requests.add(str(path).casefold())
            request_paths.add(str(path))
            if parent_target:
                request_projects.setdefault(str(path).casefold(), set()).add(str(parent_target))
        else:
            projects.setdefault(str(path).casefold(), set()).add(str(target))
            project_paths.setdefault(str(path).casefold(), str(path))
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
    request_projects = {path: {target for target in targets if target in existing}
                        for path, targets in request_projects.items()}
    linked_ids = {target for targets in projects.values() for target in targets}
    unlinked_names: dict[str, str] = {}
    for project_id, name, product_name in conn.execute("SELECT id,name,product_name FROM projects").fetchall():
        if str(project_id) in linked_ids:
            continue
        for value in (name, product_name):
            if value:
                unlinked_names.setdefault(str(value).strip().casefold(), str(project_id))
    return {"requests": requests, "request_paths": request_paths, "projects": projects, "keys": keys, "deleted_keys": deleted_keys,
            "project_paths": project_paths, "request_projects": request_projects,
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
                        needs_review: list[dict[str, Any]],
                        listings: dict[str, set[str] | None] | None = None) -> list[tuple[str, str]]:
    """(name, relative path) of every non-hidden folder at the schema's PROJECT level (§8).

    Breadth-first by depth through the upper section; hidden/system folders,
    files and reparse points/symlinks are never followed (§5.1). ``listings`` (the run's
    :func:`_absent` cache) keeps every listing made here, so a rename check does not list again.
    """
    project_level, _request_level = _upper_levels(schema)
    level: list[tuple[str, str]] = [("", "")]
    for _depth in range(1, project_level + 1):
        next_level: list[tuple[str, str]] = []
        for _, relative in level:
            issues = len(lister.issues)
            children = lister.children(relative)
            if listings is not None:
                listings[relative] = (None if len(lister.issues) > issues
                                      else {_folded_name(name) for name, _ in children})
            next_level.extend((name, path) for name, path in children if not depth_profiles.is_ignored_name(name))
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
                      request_target: str | None = None, supersedes: tuple[str, ...] = ()) -> dict[str, Any]:
    """Run scan -> preview -> registration for one new request folder.

    ``supersedes`` (a renamed request folder, with ``request_target``): live registrations of the
    old folder that become tombstones in the registration's transaction.

    Roles come from the depth schema only. The preview carries just the
    assignments the depth schema allows (§6): ``EXCLUDE`` for sibling request
    folders and a ``LINK`` for an already linked project. A new project or
    request is created by the default registration plan. The Final branch gets
    roles from the schema but is never a capture source (handled by the core).
    """
    request_name = request_path.rsplit("/", 1)[-1]
    if time.monotonic() > deadline:
        return {"deferred": True}
    # SCX drive (issue #51): list the scanned tree (project folder without the sibling request folders)
    # in one read round before the scan; level-by-level misses used up the session's rounds on deep
    # trees and every unread folder became PATH_UNAVAILABLE. In the last round the folder waits.
    if not drive_reads.require_subtree(project_path, siblings):
        return {"deferred": True, "code": "DRIVE_READ_LIMIT"}
    try:
        scanned = environment_service.save_scan(
            conn, root, project_path, environment, None, None, None, SYSTEM.user_id, skip_paths=siblings,
        )
    except (ValueError, spdm_storage.SpdmStorageError) as exc:
        if _error_code(exc) == drive_reads.NOT_PREPARED:
            return {"deferred": True, "code": "DRIVE_READ_LIMIT"}
        return {"review": _review(request_path, "SCAN_FAILED", f"폴더를 조사할 수 없습니다: {str(exc)[:120]}", environment)}
    if scanned["status"] != "COMPLETE":
        if drive_reads.final_round() and any(item.get("error_code") == drive_reads.NOT_PREPARED
                                             for item in scanned["issues"]):
            return {"deferred": True, "code": "DRIVE_READ_LIMIT"}
        return {"review": _scan_issue_review(request_path, scanned["issues"], environment)}
    if time.monotonic() > deadline:
        return {"deferred": True}
    by_path = {_node_key(node["relative_path"]): node for node in scanned["nodes"]}
    project_node = by_path.get(_node_key(project_path))
    request_node = by_path.get(_node_key(request_path))
    if not project_node or not request_node:
        missing = "프로젝트" if not project_node else "의뢰"
        return {"review": _review(request_path, "SCAN_FAILED",
                                  f"조사 결과에 {missing} 폴더가 없습니다(폴더 {len(scanned['nodes'])}개 조사). "
                                  "폴더 이름이 조사 중에 바뀌었거나 드라이브가 다른 이름으로 돌려준 경우입니다.",
                                  environment)}
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
            environment_service.register(conn, built["id"], key, True, SYSTEM, root, creator_membership=False,
                                         supersedes=supersedes)
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
    "DRIVE_READ_LIMIT": "드라이브 조회 횟수 한도로 이번 확인에서 이 폴더를 읽지 못했습니다. 다음 확인에서 다시 읽습니다.",
}


def _issue_reason(issue: dict[str, str]) -> str:
    if issue.get("code") == "DRIVE_LIST_FAILED":
        return (f"드라이브 어댑터가 이 폴더의 목록 조회(list_dir)에 실패했습니다({issue.get('error_code')}): "
                f"{issue.get('message') or ''} 대시보드에서는 고칠 수 없는 드라이브·어댑터 오류일 수 있습니다.")
    return _ISSUE_REASONS.get(issue["code"], "")


def _scan_issue_review(request_path: str, issues: list[dict[str, Any]], environment: str) -> dict[str, Any]:
    """Needs-review item of an INCOMPLETE scan: the first issue's folder, code and drive operation."""
    first = next((item for item in issues if item.get("code") == "PATH_UNAVAILABLE"), issues[0] if issues else {})
    where = str(first.get("relative_path") or request_path)
    operation = first.get("operation")
    if operation and drive_reads.active():
        error_code = str(first.get("error_code") or "-")
        reason = (f"드라이브 어댑터가 '{where}' 의 {operation} 호출에 실패했습니다({error_code}): "
                  f"{str(first.get('message') or '')[:120]} 대시보드에서는 고칠 수 없는 드라이브·어댑터 오류일 수 있습니다.")
        return {**_review(request_path, "DRIVE_READ_FAILED", reason, environment),
                "drive_operation": operation, "drive_error_code": error_code, "issue_path": where,
                "issue_count": len(issues)}
    reason = (f"조사 한도 초과 또는 접근할 수 없는 폴더가 있습니다({len(issues)}개, 첫 항목 "
              f"{first.get('code') or '-'}: {where}).")
    return {**_review(request_path, "SCAN_INCOMPLETE", reason, environment), "issue_path": where,
            "issue_count": len(issues)}


def _under(path: str, project_paths: list[str]) -> bool:
    folded = path.casefold()
    return any(folded.startswith(item.casefold().rstrip("/") + "/") for item in project_paths)


def _project_requests(linked: dict[str, Any], project_paths: list[str]) -> set[tuple[str, str]]:
    """(WR key, environment) of requests already linked below the project folders (the current one
    plus, for a followed project folder rename, the project's positively absent old folders)."""
    identities = set()
    for path in linked["request_paths"]:
        if _under(path, project_paths):
            key, environment, _code = request_identity(path.rsplit("/", 1)[-1])
            if environment:
                identities.add((key, environment))
    return identities


def _folded_name(name: str) -> str:
    return unicodedata.normalize("NFC", str(name)).casefold()


def _absent(lister: _Lister, relative_path: str, listings: dict[str, set[str] | None]) -> bool:
    """Whether ``relative_path`` is positively gone: a readable ancestor listing no longer holds it.

    A listing that failed or hit the entry limit is never taken as absence (a slow or unreachable
    drive must not look like a rename). ``listings`` caches one run's listings by folder.
    """
    parent = ""
    for part in [item for item in str(relative_path).split("/") if item]:
        if parent not in listings:
            issues = len(lister.issues)
            children = lister.children(parent)
            listings[parent] = None if len(lister.issues) > issues else {_folded_name(name) for name, _ in children}
        names = listings[parent]
        if names is None:
            return False
        if _folded_name(part) not in names:
            return True
        parent = f"{parent}/{part}" if parent else part
    return False


def _live_request_folders(conn, root_key: str) -> dict[str, dict[str, Any]]:
    """Request folders of live folder registrations: casefolded path -> binding and registration ids."""
    found: dict[str, dict[str, Any]] = {}
    for path, registration_id, project_id, request_id, environment in conn.execute(
            "SELECT g.relative_path,r.id,r.project_id,r.request_id,r.environment FROM folder_environment_registry g "
            "JOIN folder_environment_registrations r ON r.id=g.registration_id "
            "WHERE g.root_key=? AND g.role_kind='REQUEST' AND g.target_id=r.request_id AND r.status IN (?,?,?,?)",
            [root_key, *ACTIVE_STATUSES]).fetchall():
        scope = (str(project_id), str(request_id), str(environment))
        item = found.setdefault(str(path).casefold(), {"path": str(path), "scope": scope, "registration_ids": [],
                                                       "conflict": False})
        item["conflict"] = item["conflict"] or item["scope"] != scope
        item["registration_ids"].append(str(registration_id))
    return found


def _bound_elsewhere(conn, live: dict[str, dict[str, Any]], old_key: str, scope: tuple[str, str, str]) -> bool:
    """The request has another folder binding (SPDM parent binding, legacy registry, another live path)."""
    _project_id, request_id, environment = scope
    if any(key != old_key and item["scope"][1:] == (request_id, environment) for key, item in live.items()):
        return True
    if conn.execute("SELECT 1 FROM spdm_storage_request_parents WHERE request_id=? LIMIT 1", [request_id]).fetchone():
        return True
    try:
        return bool(conn.execute("SELECT 1 FROM folder_discovery_registry WHERE role_kind='REQUEST' AND target_id=? LIMIT 1",
                                 [request_id]).fetchone())
    except Exception:  # pragma: no cover - legacy table missing on a minimal schema
        return False


def _superseded_registrations(conn, root_key: str, holder: dict[str, Any]) -> tuple[str, ...]:
    """Live registrations of the old folder: its REQUEST rows plus scans at or below it (re-interpretations)."""
    project_id, request_id, environment = holder["scope"]
    prefix = holder["path"].casefold().rstrip("/") + "/"
    ids = list(holder["registration_ids"])
    for registration_id, scan_path in conn.execute(
            "SELECT r.id,s.relative_path FROM folder_environment_registrations r "
            "JOIN folder_environment_previews p ON p.id=r.preview_id JOIN folder_environment_scans s ON s.id=p.scan_id "
            "WHERE s.root_key=? AND r.project_id=? AND r.request_id=? AND r.environment=? AND r.status IN (?,?,?,?)",
            [root_key, project_id, request_id, environment, *ACTIVE_STATUSES]).fetchall():
        if (str(scan_path).casefold().rstrip("/") + "/").startswith(prefix):
            ids.append(str(registration_id))
    return tuple(dict.fromkeys(ids))


_RENAME_AMBIGUOUS = ("이전 의뢰 폴더가 없어졌고 같은 의뢰번호·환경의 새 폴더가 여러 개입니다. "
                     "하나만 남기거나 폴더 조사에서 기존 의뢰에 연결하세요.")
_RENAME_UNSUPPORTED = ("이전 의뢰 폴더가 없어졌지만 이 의뢰의 연결은 자동으로 옮길 수 없습니다. "
                       "폴더 조사에서 기존 의뢰에 연결하세요.")


def _rename_source(conn, lister: _Lister, root_key: str, linked: dict[str, Any], live: dict[str, dict[str, Any]],
                   listings: dict[str, set[str] | None], project_paths: list[str], project_targets: set[str],
                   identity: tuple[str, str], request_path: str, same_identity: list[str]) -> dict[str, Any] | None:
    """The registered request a new folder of an already linked (WR key, environment) was renamed from.

    ``project_paths``: the new folder's project folder plus, for a followed project folder rename,
    the project's positively absent old folders (where the old request folders were).
    None keeps the copy rule (``WR_ALREADY_LINKED``): a linked folder of that identity still exists
    or its absence cannot be confirmed. ``{"review": ...}`` says why a missing folder is not followed.
    """
    holders: dict[str, str] = {}
    for path in linked["request_paths"]:
        if _under(path, project_paths) and request_identity(path.rsplit("/", 1)[-1])[:2] == identity:
            holders.setdefault(path.casefold(), path)
    if not holders or any(not _absent(lister, path, listings) for path in holders.values()):
        return None
    environment = identity[1]
    if len(holders) > 1 or len(same_identity) > 1:
        return {"review": _review(request_path, "RENAME_AMBIGUOUS", _RENAME_AMBIGUOUS, environment)}
    old_key = next(iter(holders))
    holder = live.get(old_key)
    if (not holder or holder["conflict"] or holder["scope"][2] != environment
            or holder["scope"][0] not in project_targets or [path.casefold() for path in same_identity] != [request_path.casefold()]
            or _bound_elsewhere(conn, live, old_key, holder["scope"])):
        return {"review": _review(request_path, "WR_ALREADY_LINKED", _RENAME_UNSUPPORTED, environment)}
    return {"project_id": holder["scope"][0], "request_id": holder["scope"][1], "old_path": holder["path"],
            "registration_ids": _superseded_registrations(conn, root_key, holder)}


def _audit_rename(conn, item: dict[str, Any], superseded: tuple[str, ...] | None,
                  action: str = "FOLDER_REQUEST_FOLDER_RENAMED") -> None:
    """Audit event of a followed rename, written by the discovery (background runs have no HTTP request)."""
    detail = dict(item) if superseded is None else {**item, "superseded_registration_ids": list(superseded)}
    try:
        conn.execute(
            "INSERT INTO audit_events (id, occurred_at, user_id, username, role, action, method, path, status_code, "
            "request_id, client_ip, user_agent, detail_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [str(uuid4()), _utc_now(), SYSTEM.user_id, SYSTEM.username, None, action,
             "SYSTEM", "folder-auto-discovery", 200, str(uuid4()), None, "",
             json.dumps(detail, ensure_ascii=False)])
    except Exception as exc:  # the re-link is durable; a missing audit row must not undo or repeat it
        _logger.warning("Rename audit event could not be written target_id=%s error_type=%s",
                        item.get("id"), type(exc).__name__)


# --- renamed project folder (user decision 2026-10-17, depth-schema §8) ---------------------------

_PROJECT_RENAME_AMBIGUOUS = ("연결된 프로젝트 폴더가 없어졌고 이 폴더의 의뢰번호·환경이 여러 프로젝트 또는 여러 새 "
                             "프로젝트 폴더와 맞습니다. 하나만 남기거나 폴더 조사에서 기존 프로젝트에 연결하세요.")
_PROJECT_RENAME_UNSUPPORTED = ("이름이 바뀐 프로젝트 폴더로 보이지만 기존 프로젝트가 이전 방식(SPDM 폴더 연결·구 폴더 "
                               "등록)으로 연결되어 있어 자동으로 옮길 수 없습니다. 폴더 조사에서 기존 프로젝트에 연결하세요.")


def _wr_identity(name: str) -> tuple[str, str] | None:
    """(WR number, environment) of a request folder name that has both, else None (pairing only)."""
    number = wr_number(name)
    environment, code = depth_profiles.keyword_environment(name)
    return (number, environment) if number and environment and not code else None


def _project_bound_by_legacy(conn, project_id: str) -> bool:
    """The project or one of its requests is bound via the SPDM parent tables or the legacy folder registry."""
    for sql in ("SELECT 1 FROM spdm_storage_project_parents WHERE project_id=? LIMIT 1",
                "SELECT 1 FROM spdm_storage_request_parents WHERE project_id=? LIMIT 1"):
        if conn.execute(sql, [project_id]).fetchone():
            return True
    try:
        return bool(conn.execute(
            "SELECT 1 FROM folder_discovery_registry WHERE (role_kind='PROJECT' AND target_id=?) OR "
            "(role_kind='REQUEST' AND (parent_target_id=? OR target_id IN "
            "(SELECT id FROM analysis_requests WHERE project_id=?))) LIMIT 1",
            [project_id, project_id, project_id]).fetchone())
    except Exception:  # pragma: no cover - legacy table missing on a minimal schema
        return False


def _project_paths_by_id(linked: dict[str, Any]) -> dict[str, list[str]]:
    """Project id -> the project folders it is linked at (as written)."""
    found: dict[str, list[str]] = {}
    for folded, targets in linked["projects"].items():
        for target in targets:
            found.setdefault(target, []).append(linked["project_paths"].get(folded, folded))
    return found


def _project_renames(conn, lister: _Lister, root_key: str, linked: dict[str, Any], schema: dict[str, Any],
                     projects: list[tuple[str, str]], paths_of: dict[str, list[str]],
                     listings: dict[str, set[str] | None], request_lists: dict[str, list[tuple[str, str]]],
                     needs_review: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Unlinked project folders that are a renamed linked project folder.

    Pairing rule: an unlinked project folder C is the renamed folder of project X when
    (1) every folder X is linked at is positively absent (:func:`_absent`),
    (2) at least one unlinked request folder of C has the (WR number, environment) of a request
        folder linked to X and none has one of a request folder linked to any other project,
    (3) no other unlinked project folder shares a (WR number, environment) with X's request folders,
    (4) C holds no "폴더 구조 만들기" reservation (a reservation decides its project).
    A folder matching an absent project but failing (2)/(3) is ``PROJECT_RENAME_AMBIGUOUS``; a
    pairing with a project bound via the SPDM parent tables or the legacy registry is
    ``PROJECT_RENAME_UNSUPPORTED``. Both are skipped this run: nothing is registered for them.

    Returns casefolded C path -> ``{"project_id", "old_paths"}`` or ``{"skip": True}``. Request
    candidates listed here are kept in ``request_lists`` for the main loop.
    """
    unlinked = [(name, path) for name, path in projects if path.casefold() not in linked["projects"]]
    if not unlinked:
        return {}
    project_level, _request_level = _upper_levels(schema)
    owners: dict[tuple[str, str], set[str]] = {}
    for path in linked["request_paths"]:
        parts = [part for part in str(path).split("/") if part]
        identity = _wr_identity(parts[-1]) if parts else None
        if not identity:
            continue
        targets = set(linked["request_projects"].get(path.casefold(), set()))
        if len(parts) > project_level:
            targets |= linked["projects"].get("/".join(parts[:project_level]).casefold(), set())
        owners.setdefault(identity, set()).update(targets)
    if not owners:
        return {}
    gone: dict[str, bool] = {}

    def is_gone(project_id: str) -> bool:
        if project_id not in gone:
            paths = paths_of.get(project_id) or []
            gone[project_id] = bool(paths) and all(_absent(lister, path, listings) for path in paths)
        return gone[project_id]

    matches: dict[str, tuple[str, set[str]]] = {}
    claimants: dict[str, list[str]] = {}
    for _name, path in unlinked:
        requests = _request_candidates(lister, schema, path)
        request_lists[path.casefold()] = requests
        identities = {identity for request_name, request_path in requests
                      if request_path.casefold() not in linked["requests"]
                      and not _is_excluded(request_path, linked["excluded"])
                      and (identity := _wr_identity(request_name))}
        matched: set[str] = set().union(*(owners.get(identity, set()) for identity in identities))
        absent = {project_id for project_id in matched if is_gone(project_id)}
        if not absent:
            continue  # no absent project shares a request: a new project (or a copy) as before
        if any(pending_link(conn, root_key, request_path) for _, request_path in requests):
            continue
        matches[path.casefold()] = (path, matched)
        for project_id in absent:
            claimants.setdefault(project_id, []).append(path.casefold())
    plans: dict[str, dict[str, Any]] = {}
    for folded, (path, matched) in matches.items():
        project_id = next(iter(matched))
        if len(matched) != 1 or claimants.get(project_id) != [folded]:
            needs_review.append(_review(path, "PROJECT_RENAME_AMBIGUOUS", _PROJECT_RENAME_AMBIGUOUS))
            plans[folded] = {"skip": True}
        elif _project_bound_by_legacy(conn, project_id):
            needs_review.append(_review(path, "PROJECT_RENAME_UNSUPPORTED", _PROJECT_RENAME_UNSUPPORTED))
            plans[folded] = {"skip": True}
        else:
            plans[folded] = {"project_id": project_id, "old_paths": sorted(paths_of[project_id], key=str.casefold)}
    return plans


def _old_project_paths(lister: _Lister, paths_of: dict[str, list[str]], project_path: str,
                       project_targets: set[str], listings: dict[str, set[str] | None]) -> list[str]:
    """Positively absent other folders of the project linked at ``project_path``.

    After a followed project folder rename the old folder keeps the links of request folders that
    were not followed yet (run limits, a failed registration); they are followed on a later run.
    """
    if len(project_targets) != 1:
        return []
    others = [path for path in paths_of.get(next(iter(project_targets)), [])
              if path.casefold() != project_path.casefold()]
    return [path for path in others if _absent(lister, path, listings)]


def _project_name(conn, project_id: str) -> str:
    row = conn.execute("SELECT name FROM projects WHERE id=?", [project_id]).fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def _discover(conn, root, budget_seconds: float | None = None, carry: dict[str, Any] | None = None) -> dict[str, Any]:
    """One discovery pass. ``carry`` (one per :func:`discover` run) keeps what earlier read rounds of the
    same run already registered (SCX drive: a read miss repeats this pass after a registration committed,
    issue #51) and the run's deadline, so the per-run limits hold; reviews are found again by each pass."""
    carry = {} if carry is None else carry
    root_key = root_identity(root)
    lister = _Lister(root)
    created_projects: list[dict[str, str]] = carry.setdefault("created_projects", [])
    created_requests: list[dict[str, str]] = carry.setdefault("created_requests", [])
    renamed_requests: list[dict[str, Any]] = carry.setdefault("renamed_requests", [])
    renamed_projects: list[dict[str, Any]] = carry.setdefault("renamed_projects", [])
    needs_review: list[dict[str, Any]] = []
    try:
        schema = depth_profiles.get_depth_schema(conn)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        needs_review.append(_review("", str(detail.get("code") or "DEPTH_SCHEMA_MISSING"),
                                    str(detail.get("message") or "현재 깊이 스키마가 없습니다.")))
        return {"created_projects": [], "created_requests": [], "renamed_requests": [], "renamed_projects": [],
                "needs_review": needs_review, "checked_at": _now_iso(), "coalesced": False, "partial": False,
                "attempted": 0, "deferred": []}
    schema_set_id = str(schema["schema_set_id"])
    linked = _linked_state(conn, root_key)
    deadline = carry.setdefault("deadline", time.monotonic() + (
        RUN_TIME_BUDGET_SECONDS if budget_seconds is None else float(budget_seconds)))
    # Request folders left for a later run because the time budget ran out (reported by the rescan).
    deferred: list[str] = []
    listings: dict[str, set[str] | None] = {}
    projects = _project_candidates(lister, linked, schema, needs_review, listings)
    paths_of = _project_paths_by_id(linked)
    request_lists: dict[str, list[tuple[str, str]]] = {}
    project_renames = _project_renames(conn, lister, root_key, linked, schema, projects, paths_of, listings,
                                       request_lists, needs_review)
    # SCX drive (issue #51): list every unlinked request folder of every project in ONE read round (the
    # needs-review stamp reads ``<request>/Working``); one stat miss per folder used up the rounds.
    for _name, project_path in projects:
        if project_path.casefold() not in request_lists:
            request_lists[project_path.casefold()] = _request_candidates(lister, schema, project_path)
    drive_reads.require_listings([path for items in request_lists.values() for _name, path in items
                                  if path.casefold() not in linked["requests"]])
    # Registrations of earlier rounds of this run count against MAX_NEW_REQUESTS_PER_RUN.
    state = {"attempts": len(created_requests) + len(renamed_requests), "stopped": False}
    for project_name, project_path in projects:
        if state["stopped"]:
            break
        try:
            _discover_project(conn, root, root_key, lister, linked, schema_set_id, schema, project_name, project_path,
                              project_renames, request_lists, paths_of, listings, deadline, state, deferred,
                              created_projects, created_requests, renamed_requests, renamed_projects, needs_review)
        except Exception as exc:  # noqa: BLE001 - one bad folder must never fail the whole run (no 500)
            _logger.warning("Automatic discovery of one project folder failed path_hash=%s error=%s",
                            hashlib.sha256(project_path.encode("utf-8")).hexdigest()[:12], _error_code(exc),
                            exc_info=True)
            needs_review.append({**_review(project_path, "DISCOVERY_FAILED",
                                           f"이 프로젝트 폴더를 확인하지 못했습니다({_error_code(exc)}): {str(exc)[:120]} "
                                           "다음 확인에서 다시 시도합니다."), "error_code": _error_code(exc)})
    attempts, stopped = state["attempts"], state["stopped"]
    for issue in lister.issues:
        item = _review(issue["relative_path"], issue["code"], _issue_reason(issue))
        if issue.get("error_code"):
            item.update(drive_operation="list_dir", drive_error_code=issue["error_code"])
        needs_review.append(item)
    return {"created_projects": list(created_projects), "created_requests": list(created_requests),
            "renamed_requests": list(renamed_requests), "renamed_projects": list(renamed_projects),
            "needs_review": needs_review, "checked_at": _now_iso(), "coalesced": False,
            "partial": stopped, "attempted": attempts, "deferred": deferred}


_NAME_INVALID = ("폴더 이름이 Windows 파일 규칙에 맞지 않아(끝의 공백·마침표, 금지 문자 <>:\"\\|?* 또는 예약 이름) "
                 "등록할 수 없습니다. 드라이브에서 폴더 이름을 고치면 다음 확인에서 등록됩니다.")


def _discover_project(conn, root, root_key: str, lister: _Lister, linked: dict[str, Any], schema_set_id: str,
                      schema: dict[str, Any], project_name: str, project_path: str,
                      project_renames: dict[str, dict[str, Any]], request_lists: dict[str, list[tuple[str, str]]],
                      paths_of: dict[str, list[str]], listings: dict[str, set[str] | None], deadline: float,
                      state: dict[str, Any], deferred: list[str], created_projects: list[dict[str, str]],
                      created_requests: list[dict[str, str]], renamed_requests: list[dict[str, Any]],
                      renamed_projects: list[dict[str, Any]], needs_review: list[dict[str, Any]]) -> None:
    """One project folder of :func:`_discover` (its unlinked request folders, in name order)."""
    live_folders: dict[str, dict[str, Any]] | None = None  # read once, only when a rename is possible
    if not spdm_storage._valid_windows_name(project_name):
        needs_review.append(_review(project_path, "FOLDER_NAME_INVALID", _NAME_INVALID))
        return
    project_rename = project_renames.get(project_path.casefold())
    if project_rename and project_rename.get("skip"):
        return
    project_targets = set(linked["projects"].get(project_path.casefold(), set()))
    if project_rename:
        project_targets = {project_rename["project_id"]}  # keep the project; link the new folder to it
    if len(project_targets) > 1:
        needs_review.append(_review(project_path, "PROJECT_AMBIGUOUS", "프로젝트 폴더가 여러 프로젝트에 연결되어 있습니다."))
        return
    candidates = request_lists.get(project_path.casefold())
    if candidates is None:
        candidates = _request_candidates(lister, schema, project_path)
    unlinked = [(name, path) for name, path in candidates if path.casefold() not in linked["requests"]]
    if not unlinked:
        return
    pending = {path.casefold(): link for _, path in unlinked if (link := pending_link(conn, root_key, path))}
    if not project_targets and pending:
        project_targets = {next(iter(pending.values()))["project_id"]}
    if not project_targets and project_name.strip().casefold() in linked["unlinked_project_names"]:
        needs_review.append(_review(project_path, "PROJECT_NAME_EXISTS",
                                    "같은 이름의 프로젝트가 이미 있습니다. 폴더 조사에서 기존 프로젝트에 연결하세요."))
        return
    # Request folders of a renamed project folder were linked below its (absent) old folder.
    project_paths = [project_path, *_old_project_paths(lister, paths_of, project_path, project_targets, listings)]
    known = _project_requests(linked, project_paths)
    all_paths = [path for _, path in candidates]
    # Unlinked folders per (WR number, environment): a rename is followed only when exactly one exists.
    same_identity: dict[tuple[str, str | None], list[str]] = {}
    for other_name, other_path in unlinked:
        other_key, other_environment, other_code = request_identity(other_name)
        if (not other_code and wr_number(other_name) and not _is_excluded(other_path, linked["excluded"])
                and _live_key(root_key, other_path, schema_set_id, linked["deleted_keys"]) not in linked["keys"]):
            same_identity.setdefault((other_key, other_environment), []).append(other_path)
    for name, request_path in unlinked:
        folded = request_path.casefold()
        if _is_excluded(request_path, linked["excluded"]):
            needs_review.append(_review(request_path, "ADMIN_EXCLUDED",
                                        "관리자가 제외한 폴더입니다. 필요하면 폴더 조사에서 다시 연결하세요."))
            continue
        if not spdm_storage._valid_windows_name(name):
            needs_review.append(_review(request_path, "FOLDER_NAME_INVALID", _NAME_INVALID))
            continue
        wr_key, environment, keyword_code = request_identity(name)
        if keyword_code:
            needs_review.append(_review(request_path, keyword_code, depth_profiles.DEVIATION_MESSAGES[keyword_code]))
            continue
        key = _live_key(root_key, request_path, schema_set_id, linked["deleted_keys"])
        if key in linked["keys"]:
            continue
        identity = (wr_key, environment)
        rename: dict[str, Any] | None = None
        if identity in known:
            if wr_number(name) and folded not in pending:
                if live_folders is None:
                    live_folders = _live_request_folders(conn, root_key)
                rename = _rename_source(conn, lister, root_key, linked, live_folders, listings, project_paths,
                                        project_targets, identity, request_path, same_identity.get(identity, []))
            if rename is None or "review" in rename:
                needs_review.append(rename["review"] if rename else _review(
                    request_path, "WR_ALREADY_LINKED", "같은 의뢰번호·환경의 다른 폴더가 이미 연결되어 있습니다.",
                    environment))
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
        if state["attempts"] >= MAX_NEW_REQUESTS_PER_RUN or now > deadline:
            if now > deadline:
                deferred.append(request_path)
            state["stopped"] = True
            break
        state["attempts"] += 1
        siblings = [path for path in all_paths if path.casefold() != folded]
        link = pending.get(folded)
        request_target = (link["request_id"] if link and link["environment"] == environment
                          and link["project_id"] in project_targets else None)
        if rename:
            request_target = rename["request_id"]
        outcome = _register_request(conn, root, root_key, project_name, project_path, siblings,
                                    request_path, environment, project_targets, key, deadline,
                                    request_target=request_target,
                                    supersedes=rename["registration_ids"] if rename else ())
        if outcome.get("deferred"):
            deferred.append(request_path)
            state["stopped"] = True
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
        if link and request_target:
            drop_link(conn, root_key, request_path)
        if outcome["project_created"]:
            created_projects.append({"id": project_id, "name": project_name})
        if project_rename and not project_rename.get("reported"):
            # The first registration linked the new project folder (its PROJECT registry row makes
            # later runs see it as linked). The project's display name is left unchanged.
            project_rename["reported"] = True
            moved = {"id": project_id, "name": _project_name(conn, project_id),
                     "old_path": project_rename["old_paths"][0], "new_path": project_path}
            renamed_projects.append(moved)
            _audit_rename(conn, {**moved, "old_paths": project_rename["old_paths"]}, None,
                          action="FOLDER_PROJECT_FOLDER_RENAMED")
        project_targets = {project_id}
        linked["projects"][project_path.casefold()] = {project_id}
        linked["requests"].add(folded)
        linked["keys"].add(key)
        known.add(identity)
        if rename:
            item = {"id": request_id, "name": outcome["request_name"], "environment": environment,
                    "project_id": project_id, "old_path": rename["old_path"], "new_path": request_path}
            renamed_requests.append(item)
            _audit_rename(conn, item, rename["registration_ids"])
            continue
        created_requests.append({"id": request_id, "name": outcome["request_name"],
                                 "environment": environment, "project_id": project_id,
                                 "linked": bool(request_target)})


def enabled() -> bool:
    """Automatic discovery is on unless SIMDASH_AUTO_DISCOVERY is 0/false/off.

    Only the isolated e2e runner turns it off so its fixture folders are not
    registered behind the tests' backs; deployments keep the default.
    """
    return os.environ.get("SIMDASH_AUTO_DISCOVERY", "1").strip().casefold() not in {"0", "false", "off", "no"}


def _empty(status: str) -> dict[str, Any]:
    return {"created_projects": [], "created_requests": [], "renamed_requests": [], "renamed_projects": [],
            "needs_review": [], "checked_at": _now_iso(), "coalesced": True, "status": status}


def discover(*, force: bool = False, connection_factory=None, fresh: bool = False,
             wait_seconds: float = 0.0, budget_seconds: float | None = None) -> dict[str, Any]:
    """Register new SPDM project/request folders under the configured storage root.

    Never blocks on another run and opens a DB connection only for an actual run.
    The manual rescan passes ``fresh`` (skip the memo), ``wait_seconds`` (wait up
    to that long for a run in progress instead of returning ``RUNNING``) and
    ``budget_seconds`` (instead of ``RUN_TIME_BUDGET_SECONDS``); the defaults keep
    the screens' behaviour.
    """
    if not enabled():
        return _empty("DISABLED")
    interval = FORCE_MIN_INTERVAL_SECONDS if force else MIN_INTERVAL_SECONDS

    def remembered() -> dict[str, Any] | None:
        if fresh:
            return None
        with _state_lock:
            memo = dict(_memo)
        if memo and time.monotonic() - memo["at"] < interval:
            return {**memo["result"], "coalesced": True}
        return None

    cached = remembered()
    if cached:
        return cached
    acquired = (_run_lock.acquire(timeout=float(wait_seconds)) if wait_seconds and wait_seconds > 0
                else _run_lock.acquire(blocking=False))
    if not acquired:
        with _state_lock:
            last = _memo.get("result")
        return {**last, "coalesced": True, "status": "RUNNING"} if last else _empty("RUNNING")
    try:
        cached = remembered()
        if cached:
            return cached
        carry: dict[str, Any] = {}

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
                try:
                    return {**_discover(conn, root, budget_seconds, carry), "status": "CHECKED"}
                except Exception as exc:  # noqa: BLE001 - never a 500 for the screens (DriveReadMiss passes)
                    _logger.warning("Automatic discovery failed error=%s", _error_code(exc), exc_info=True)
                    return {**_empty("FAILED"), "coalesced": False,
                            "needs_review": [{**_review("", "DISCOVERY_FAILED",
                                                        f"폴더 자동 확인 중 오류가 났습니다({_error_code(exc)}). "
                                                        "다음 확인에서 다시 시도합니다."),
                                              "error_code": _error_code(exc)}]}

        # SCX drive (D2): listings and capture files are fetched between rounds without a DB
        # connection; a round repeats only reads and idempotent steps (local mode: one plain call).
        # Issue #51: a run needs about one round per new request for its tree and one for its capture
        # files, plus a few for the upper levels (DISCOVERY_READ_ROUNDS).
        result = drive_reads.run(run_once, priority="BACKGROUND", rounds=DISCOVERY_READ_ROUNDS)
        if result.get("status") != "CHECKED":
            return result
        with _state_lock:
            _memo.update(at=time.monotonic(), result=result)
        return result
    finally:
        _run_lock.release()


def visible_result(result: dict[str, Any], *, is_global_admin: bool) -> dict[str, Any]:
    """Folder paths (needs review, a followed rename's old/new path) are shown to global administrators only."""
    if is_global_admin:
        return result

    def stripped(items):
        return [{key: value for key, value in item.items() if key not in {"old_path", "new_path"}} for item in items or []]

    return {**result, "needs_review": [], "renamed_requests": stripped(result.get("renamed_requests")),
            "renamed_projects": stripped(result.get("renamed_projects"))}


def audit_detail(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps({
        "created_projects": result["created_projects"],
        "created_requests": result["created_requests"],
        "renamed_requests": result.get("renamed_requests") or [],
        "renamed_projects": result.get("renamed_projects") or [],
        "needs_review_count": len(result["needs_review"]),
    }, ensure_ascii=False))
