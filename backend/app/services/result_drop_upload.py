"""W8 simplified result registration: drag & drop files or whole folders into Working.

Analysts mostly copy results straight into the SPDM share with Explorer; the
dashboard follows within 30 s (``folder_auto_sync``) and assigns roles by folder
depth (DEPTH_V1, docs/contracts/depth-schema.md §3, §5). This module is the
browser equivalent of that copy, nothing more: no drafts, no automatic checks,
no approval. Contract: docs/features/result-registration.md.

Flow
  1. ``plan``: validate everything before any write. Target inside this request's
     ``Working`` tree and owned by it (``result_registration_paths._owner_conflict``),
     no link/reparse in the target chain, Windows-safe names, no ``..``/absolute
     paths, path length, DEPTH_V1 depth of every new folder, existing files
     (never overwritten), free space. Executable/script files are skipped.
  2. ``create_session``: the same plan, refused when it has errors; creates
     ``<request>/Working/.simdash-upload/<session>/`` (same volume, skipped by the
     folder scan and auto-sync, see ``folder_discovery_scan``).
  3. ``upload_chunk``: chunks (≤ 8 MiB) at an exact offset (resumable), SHA-256
     computed by the server while receiving; optional client chunk/file hashes.
  4. ``complete``: re-checks conflicts, creates folders one by one below pinned
     parents and publishes each staged file with ``rename_no_replace``.
  5. ``abort``: removes only this session's own staged ``<n>.part`` files.

Sessions live in this process (like the Final copy job thread); a server restart
drops them and their staging folder is removed by a later session of the same
request once it is older than ``SESSION_IDLE_SECONDS`` (60 min). Requires one backend
worker process (Windows service: a single uvicorn worker).
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from ..database_connection import ConnectionLike
from . import environment_folder_profiles, folder_name_warnings, spdm_storage
from . import result_registration_paths as paths
from .result_registration import _BLOCKED_EXTENSIONS
from .storage import provider_for_root
from .storage.local import LocalFsProvider
from .storage.provider import UPLOAD_STAGING_DIR, WORKING, StorageError, working_zone_allows

CHUNK_BYTES = 8 * 1024 * 1024
MAX_FILES = 20000
MAX_FOLDERS = 20000
MAX_DEPTH = 32
MAX_SEGMENT_CHARS = 255
# Explorer and most Windows tools still stop at MAX_PATH; checked for the server
# path and for the path users see (display root).
MAX_PATH_CHARS = 259
# Open (uploading / partially published) sessions: per user per request, and per request
# overall, so one user's stuck sessions cannot block the request for others (review M2).
MAX_ACTIVE_PER_USER = 2
MAX_ACTIVE_PER_REQUEST = 6
SESSION_IDLE_SECONDS = 60 * 60
TREE_NODE_LIMIT = 5000
KNOWN_NAME_SCAN_LIMIT = 20000
DISK_MARGIN_RATIO = 0.05
DISK_MARGIN_MIN_BYTES = 1024 * 1024 * 1024
RENAME_RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0)
ISSUE_PATH_LIMIT = 20
DISPLAY_ROOT_ENV = "SIMDASH_SPDM_DISPLAY_ROOT"

ROLE_LABELS = {
    "WORKING": "Working", "SIMULATION_CASE": "Case", "LOAD_CASE": "하중경우", "EXECUTION_RUN": "Run Case",
    "RUN_OPTION": "Run Option", "SCENE": "Scene", "CONTAINER": "폴더", "CONTENT": "Scene 안 폴더",
}
_ROLE_BEARING = frozenset({"SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"})
_DEFAULT_KNOWN = {"RUN_OPTION": {"individual", "cumulative"}}
_SYSTEM_FILES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows also reserves COM/LPT with superscript digits ¹²³ (local extension; the shared
# spdm_storage set is left unchanged for its other callers).
_RESERVED = frozenset({*spdm_storage._WINDOWS_RESERVED, "CONIN$", "CONOUT$",
                       *(f"{device}{digit}" for device in ("COM", "LPT") for digit in "¹²³")})
# Drag & drop superset of the registration block list: shell/launch formats, disk images
# and macro add-ins that Explorer would run or mount from the shared folder (W8 review M1).
DROP_BLOCKED_EXTENSIONS = frozenset({
    *_BLOCKED_EXTENSIONS,
    ".scf", ".url", ".lnk", ".library-ms", ".searchconnector-ms", ".msc", ".iso", ".img", ".vhd", ".vhdx",
    ".appref-ms", ".settingcontent-ms", ".application", ".wsc", ".sct", ".chm", ".inf", ".xll", ".ps1xml",
    ".hta", ".cpl", ".reg",
    # re-review N4
    ".msix", ".appx", ".ms-appinstaller", ".diagcab", ".website", ".mht", ".cab", ".psd1",
})
_SCENE_LIKE = re.compile(r"^\d+[_\-]")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_PART = re.compile(r"^\d+\.part$")


class DropUploadError(paths.ResultRegistrationError):
    """Controlled failure with an HTTP status and extra response fields."""

    def __init__(self, code: str, message: str, status: int = 422, **extra: Any) -> None:
        super().__init__(code, message)
        self.status = status
        self.extra = extra


# ---------------------------------------------------------------------------
# Names and paths
# ---------------------------------------------------------------------------

def name_problem(name: str) -> str | None:
    """Why ``name`` is not a safe Windows file/folder name (``None`` when it is)."""
    if not name or name in {".", ".."}:
        return "EMPTY"
    if len(name) > MAX_SEGMENT_CHARS:
        return "TOO_LONG"
    if _INVALID_CHARS.search(name):
        return "INVALID_CHAR"
    if name != name.rstrip(". "):
        return "TRAILING_DOT_SPACE"
    if name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED:
        return "RESERVED"
    return None


def _client_parts(value: Any) -> list[str]:
    """Relative path from the browser as segments; anything unsafe rejects the whole request."""
    invalid = DropUploadError("RESULT_DROP_PATH_INVALID",
                              f"올릴 수 없는 경로입니다: {str(value)[:200]}", 422, path=str(value)[:300])
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/") or "\x00" in value:
        raise invalid
    value = unicodedata.normalize("NFC", value)
    parts = value.split("/")
    if len(parts) > MAX_DEPTH or any(part in {"", ".", ".."} or name_problem(part) for part in parts):
        raise invalid
    return parts


def _ignored(name: str) -> bool:
    return environment_folder_profiles.is_ignored_name(name)


def _skip_reason(parts: list[str]) -> str | None:
    name = parts[-1]
    if any(_ignored(part) for part in parts[:-1]):
        return "HIDDEN_FOLDER"
    if name.casefold() in _SYSTEM_FILES or name.startswith("~$") or name.casefold() == UPLOAD_STAGING_DIR:
        return "SYSTEM_FILE"
    if extension_of(name) in DROP_BLOCKED_EXTENSIONS:
        return "BLOCKED_EXTENSION"
    return None


def extension_of(name: str) -> str:
    """``.ext`` (casefolded) after the last dot, also for dot-leading names like ``.bat``; ``""`` without a dot.

    Trailing dots/spaces are stripped first because Windows drops them (``a.exe.`` is ``a.exe``).
    """
    stripped = name.rstrip(". ")
    return "." + stripped.rsplit(".", 1)[-1].casefold() if "." in stripped else ""


def _fold(parts: list[str] | tuple[str, ...]) -> str:
    return "/".join(part.casefold() for part in parts)


def display_root(root) -> str:
    """Path users paste into Explorer: ``SIMDASH_SPDM_DISPLAY_ROOT`` (e.g. a UNC share) or the configured root."""
    configured = str(os.getenv(DISPLAY_ROOT_ENV) or "").strip()
    return configured or str(root)


def display_path(base: str, relative: str) -> str:
    windows = "\\" in base or bool(re.match(r"^[A-Za-z]:", base))
    separator = "\\" if windows else "/"
    base = base.rstrip("\\/") or base
    if not relative:
        return base
    return base + separator + relative.replace("/", separator)


# ---------------------------------------------------------------------------
# Request scope and the Working tree
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    root: Any
    root_id: str
    root_key: str
    project_id: str
    request_id: str
    environment: str
    request_relative_path: str
    working_relative_path: str
    roles: list[str]


def _lower_roles(conn: ConnectionLike, environment: str) -> list[str]:
    try:
        levels = environment_folder_profiles.get_depth_schema(conn)["environments"][environment]["lower"]["levels"]
    except Exception:  # noqa: BLE001 - schema unreadable: the contract's default tree
        levels = environment_folder_profiles.DEFAULT_LOWER[environment]["levels"]
    ordered = sorted(levels, key=lambda item: int(item["level"]))
    return [str(item["role"]) for item in ordered]


def _role_at(roles: list[str], level: int) -> str:
    return roles[level - 1] if 1 <= level <= len(roles) else "CONTENT"


def request_scope(conn: ConnectionLike, project_id: str, request_id: str, environment: str) -> Scope:
    root, root_id, root_key = paths.storage_context(conn)
    scope = paths._scope(conn, project_id, request_id, environment)
    request_rel = str(scope["request_relative_path"])
    fs = provider_for_root(root)
    try:
        paths._safe_existing(root, request_rel)
        working = next((entry.name for entry in fs.list(request_rel)
                        if entry.kind == "dir" and not entry.is_link and entry.name.casefold() == "working"), None)
    except (OSError, StorageError) as exc:
        raise DropUploadError("SPDM_FOLDER_UNAVAILABLE", "의뢰 폴더에 접근할 수 없습니다.", 409) from exc
    if working is None:
        raise DropUploadError("RESULT_DROP_WORKING_MISSING", "의뢰 폴더 아래에 Working 폴더가 없습니다.", 409)
    return Scope(root=root, root_id=root_id, root_key=root_key, project_id=project_id, request_id=request_id,
                 environment=str(scope["environment"]), request_relative_path=request_rel,
                 working_relative_path=f"{request_rel}/{working}", roles=_lower_roles(conn, str(scope["environment"])))


def _target_level(scope: Scope, target: str) -> int:
    request_parts = PurePosixPath(scope.request_relative_path).parts
    target_parts = PurePosixPath(target).parts
    if (len(target_parts) <= len(request_parts)
            or tuple(part.casefold() for part in target_parts[:len(request_parts)])
            != tuple(part.casefold() for part in request_parts)):
        raise DropUploadError("RESULT_DROP_TARGET_OUTSIDE_REQUEST", "선택한 위치가 이 의뢰 폴더 밖에 있습니다.", 422)
    if target_parts[len(request_parts)].casefold() != "working":
        raise DropUploadError("RESULT_DROP_TARGET_OUTSIDE_WORKING",
                              "결과는 이 의뢰의 Working 폴더 아래에만 올릴 수 있습니다.", 422)
    if any(_ignored(part) for part in target_parts[len(request_parts) + 1:]):
        raise DropUploadError("RESULT_DROP_TARGET_INVALID", "숨김·임시 폴더에는 올릴 수 없습니다.", 422)
    return len(target_parts) - len(request_parts)


def checked_target(conn: ConnectionLike, scope: Scope, target_relative_path: str) -> tuple[str, int]:
    """Existing, owned, link-free folder inside this request's Working tree, and its DEPTH_V1 level."""
    target = paths._relative(target_relative_path)
    level = _target_level(scope, target)
    if not working_zone_allows(f"{target}/{UPLOAD_STAGING_DIR}"):
        raise DropUploadError("RESULT_DROP_TARGET_OUTSIDE_WORKING",
                              "결과는 이 의뢰의 Working 폴더 아래에만 올릴 수 있습니다.", 422)
    fs = provider_for_root(scope.root)
    target = paths._safe_existing(scope.root, target)
    info = fs.stat(target, follow_links=False)
    if info is None or info.kind != "dir" or info.is_link:
        raise DropUploadError("SPDM_FOLDER_UNAVAILABLE", "선택한 위치가 폴더가 아닙니다.", 409)
    paths._owner_conflict(conn, scope.root_id, scope.root_key, target, scope.project_id,
                          scope.request_id, scope.environment)
    return target, level


class _DirIndex:
    """One listing per existing parent folder, as a casefold map (W8 review M3).

    ``lookup`` answers "is there already an entry with this name (any case)?" in O(1)
    instead of re-reading the folder per file. Built fresh for each plan / complete.
    """

    def __init__(self, fs: LocalFsProvider) -> None:
        self.fs = fs
        self._maps: dict[str, dict[str, Any]] = {}

    def lookup(self, parent_rel: str, name: str):
        """Existing entry (``Entry``) named ``name`` case-insensitively, or ``None``; ``OSError`` when unreadable."""
        found = self._maps.get(parent_rel)
        if found is None:
            found = {}
            for entry in self.fs.list(parent_rel):
                found.setdefault(entry.name.casefold(), entry)
            self._maps[parent_rel] = found
        return found.get(name.casefold())


def _children(fs: LocalFsProvider, relative: str) -> list[str]:
    names = []
    for entry in fs.list(relative):
        if entry.kind != "dir" or entry.is_link or _ignored(entry.name):
            continue
        names.append(entry.name)
    return sorted(names, key=str.casefold)


def walk_working(scope: Scope, *, limit: int = TREE_NODE_LIMIT) -> tuple[list[dict[str, Any]], bool]:
    """Role-bearing folders of the Working tree (levels 2..last DEPTH_V1 level), breadth first."""
    fs = provider_for_root(scope.root)
    nodes: list[dict[str, Any]] = []
    queue: list[tuple[str, int]] = [(scope.working_relative_path, 1)]
    truncated = False
    while queue:
        relative, level = queue.pop(0)
        if level >= len(scope.roles):
            continue
        try:
            names = _children(fs, relative)
        except (OSError, StorageError):
            continue
        for name in names:
            if len(nodes) >= limit:
                return nodes, True
            child = f"{relative}/{name}"
            nodes.append({"relative_path": child, "name": name, "parent_path": relative,
                          "level": level + 1, "role": _role_at(scope.roles, level + 1)})
            queue.append((child, level + 1))
    return nodes, truncated


def _known_names(scope: Scope) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {role: set(names) for role, names in _DEFAULT_KNOWN.items()}
    nodes, _ = walk_working(scope, limit=KNOWN_NAME_SCAN_LIMIT)
    for node in nodes:
        known.setdefault(str(node["role"]), set()).add(str(node["name"]).casefold())
    return known


def tree(conn: ConnectionLike, project_id: str, request_id: str, environment: str) -> dict[str, Any]:
    scope = request_scope(conn, project_id, request_id, environment)
    paths._owner_conflict(conn, scope.root_id, scope.root_key, scope.working_relative_path, project_id,
                          request_id, scope.environment)
    nodes, truncated = walk_working(scope)
    base = display_root(scope.root)
    for node in nodes:
        node["display_path"] = display_path(base, node["relative_path"])
    return {
        "project_id": project_id, "request_id": request_id, "environment": scope.environment,
        "request_relative_path": scope.request_relative_path,
        "working_relative_path": scope.working_relative_path,
        "working_display_path": display_path(base, scope.working_relative_path),
        "display_root": base,
        "levels": [{"level": index + 1, "role": role, "label": ROLE_LABELS.get(role, role)}
                   for index, role in enumerate(scope.roles)],
        "nodes": nodes, "truncated": truncated,
        "chunk_bytes": CHUNK_BYTES,
        "blocked_extensions": sorted(DROP_BLOCKED_EXTENSIONS),
        "active_uploads": _active_count(project_id, request_id),
    }


# ---------------------------------------------------------------------------
# Depth checks shared by the plan and "새 폴더 만들기"
# ---------------------------------------------------------------------------

def _issue(issues: dict[str, dict[str, Any]], code: str, severity: str, message: str, path: str | None = None) -> None:
    item = issues.setdefault(code, {"code": code, "severity": severity, "message": message, "paths": [], "count": 0})
    item["count"] += 1
    if path and len(item["paths"]) < ISSUE_PATH_LIMIT:
        item["paths"].append(path)


def _depth_findings(scope: Scope, known: dict[str, set[str]], name: str, parent_name: str,
                    level: int) -> list[tuple[str, str, str]]:
    """(code, severity, message) for a new folder ``name`` at ``level`` below ``parent_name``."""
    role = _role_at(scope.roles, level)
    folded = name.casefold()
    if role not in _ROLE_BEARING:
        return []  # content inside a Scene: any name (also final/Working) is allowed
    found: list[tuple[str, str, str]] = []
    label = ROLE_LABELS.get(role, role)
    parent_label = ROLE_LABELS.get(_role_at(scope.roles, level - 1), "폴더")
    if folded in {"working", "final"}:
        found.append(("DEPTH_REQUEST_FOLDER", "error",
                      f"'{name}' 폴더는 의뢰 폴더 바로 아래에만 있습니다. 그 안의 Case 폴더를 올리세요."))
    if folded == parent_name.casefold():
        found.append(("DEPTH_REPEATED_NAME", "error",
                      f"같은 이름 폴더가 겹칩니다({parent_name}/{name}). 한 단계 위 폴더를 선택하거나 안쪽 폴더만 올리세요."))
    others = sorted({other for other, names in known.items()
                     if other != role and other in _ROLE_BEARING and folded in names})
    if others and folded not in known.get(role, set()):
        where = "·".join(ROLE_LABELS.get(other, other) for other in others)
        found.append(("DEPTH_ROLE_MISMATCH", "error",
                      f"'{name}'은(는) 이 의뢰에서 {where} 이름인데 {label} 자리({parent_label} 바로 아래)에 놓입니다. "
                      "위치를 한 단계 맞춰 선택하세요."))
    elif (scope.environment == "DISTRIBUTION" and role in {"LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION"}
          and _SCENE_LIKE.match(name) and folded not in known.get(role, set())):
        found.append(("DEPTH_SCENE_LIKE", "warning",
                      f"'{name}'은(는) Scene 이름처럼 보이지만 {label} 자리에 놓입니다. Scene 폴더는 Run Option 폴더 아래에 넣으세요."))
    return found


def _similar_names(name: str, siblings: list[str]) -> list[tuple[str, str]]:
    found = []
    for sibling in siblings:
        result = folder_name_warnings._similar(name, sibling)
        if result:
            found.append((sibling, result[0]))
    return found


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _margin(required: int) -> int:
    return max(int(required * DISK_MARGIN_RATIO), DISK_MARGIN_MIN_BYTES)


def build_plan(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
               target_relative_path: str, files: list[dict[str, Any]],
               folders: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(public plan, internal plan). Reads only; never writes."""
    if len(files) > MAX_FILES or len(folders or []) > MAX_FOLDERS:
        raise DropUploadError("RESULT_DROP_TOO_MANY_ITEMS", "한 번에 올릴 수 있는 항목 수를 넘었습니다. 나눠서 올리세요.", 413)
    scope = request_scope(conn, project_id, request_id, environment)
    target, target_level = checked_target(conn, scope, target_relative_path)
    fs = provider_for_root(scope.root)
    base = display_root(scope.root)
    issues: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    directories: dict[tuple[str, ...], None] = {}

    for index, item in enumerate(files):
        parts = _client_parts(item.get("relative_path"))
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise DropUploadError("RESULT_DROP_SIZE_INVALID", "파일 크기가 올바르지 않습니다.", 422)
        sha = item.get("sha256")
        if sha is not None and not (isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha)):
            raise DropUploadError("RESULT_DROP_HASH_INVALID", "SHA-256 값 형식이 올바르지 않습니다.", 422)
        relative = "/".join(parts)
        reason = _skip_reason(parts)
        if reason:
            skipped.append({"client_index": index, "relative_path": relative, "size": size, "reason": reason})
            continue
        key = _fold(parts)
        if key in seen:
            raise DropUploadError("RESULT_DROP_DUPLICATE_PATH", f"같은 경로가 두 번 있습니다: {relative}", 422)
        seen.add(key)
        accepted.append({"client_index": index, "parts": parts, "relative_path": relative, "size": size,
                         "sha256": sha.lower() if sha else None})
        for depth in range(1, len(parts)):
            directories[tuple(parts[:depth])] = None
    for value in folders or []:
        parts = _client_parts(value)
        if any(_ignored(part) for part in parts):
            continue
        for depth in range(1, len(parts) + 1):
            directories[tuple(parts[:depth])] = None
    if not accepted and not directories:
        raise DropUploadError("RESULT_DROP_EMPTY", "올릴 파일이나 폴더가 없습니다. 실행 파일·스크립트는 올리지 않습니다.", 422,
                              skipped=skipped)
    for key in directories:
        if _fold(key) in seen:
            raise DropUploadError("RESULT_DROP_DUPLICATE_PATH", f"같은 이름의 파일과 폴더가 있습니다: {'/'.join(key)}", 422)

    known = _known_names(scope)
    target_name = PurePosixPath(target).name
    resolved: dict[tuple[str, ...], dict[str, Any]] = {(): {"relative_path": target, "exists": True, "level": target_level}}
    folders_to_create: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    new_siblings: dict[str, list[str]] = {}
    sibling_cache: dict[str, list[str]] = {}
    index = _DirIndex(fs)

    def existing_siblings(parent_rel: str) -> list[str]:
        if parent_rel not in sibling_cache:
            try:
                sibling_cache[parent_rel] = _children(fs, parent_rel)
            except (OSError, StorageError):
                sibling_cache[parent_rel] = []
        return sibling_cache[parent_rel]

    def zone_check(relative: str, client_path: str) -> None:
        # Same rule the storage provider enforces at write time (re-review N1): report at plan time.
        if not working_zone_allows(relative):
            _issue(issues, "PATH_NOT_ALLOWED", "error", "이 경로에는 쓸 수 없습니다(Working 폴더 밖).", client_path)

    def length_check(relative: str, client_path: str) -> None:
        zone_check(relative, client_path)
        if (len(str(fs.path(relative))) > MAX_PATH_CHARS
                or len(display_path(base, relative)) > MAX_PATH_CHARS):
            _issue(issues, "PATH_TOO_LONG", "error",
                   f"경로가 Windows 길이 한도({MAX_PATH_CHARS}자)를 넘습니다. 폴더 이름을 줄이세요.", client_path)

    for key in sorted(directories, key=lambda item: (len(item), _fold(item))):
        parent = resolved[key[:-1]]
        name = key[-1]
        relative_client = "/".join(key)
        level = target_level + len(key)
        role = _role_at(scope.roles, level)
        entry: dict[str, Any] = {"relative_path": f"{parent['relative_path']}/{name}", "exists": False, "level": level}
        if parent["exists"]:
            try:
                info = index.lookup(parent["relative_path"], name)
            except (OSError, StorageError):
                info = None
                _issue(issues, "PATH_UNAVAILABLE", "error", "대상 폴더를 확인할 수 없습니다.", relative_client)
            collision = f"{parent['relative_path']}/{info.name}" if info is not None else None
            if collision and info is not None:
                if info.is_link:
                    _issue(issues, "PATH_UNSAFE", "error", "대상 경로에 연결 폴더(링크)가 있어 올릴 수 없습니다.", relative_client)
                elif info.kind != "dir":
                    conflicts.append({"relative_path": relative_client, "destination_relative_path": collision,
                                      "reason": "FILE_IN_PLACE_OF_FOLDER"})
                else:
                    entry = {"relative_path": collision, "exists": True, "level": level}
                    if collision.rsplit("/", 1)[-1] != name:
                        _issue(issues, "NAME_CASE_DIFFERS", "warning",
                               "이미 있는 폴더와 대소문자만 다릅니다. 기존 폴더에 넣습니다.", relative_client)
        resolved[key] = entry
        length_check(entry["relative_path"], relative_client)
        if entry["exists"]:
            continue
        folders_to_create.append({"relative_path": entry["relative_path"], "client_path": relative_client,
                                  "level": level, "role": role})
        parent_name = key[-2] if len(key) > 1 else target_name
        for code, severity, message in _depth_findings(scope, known, name, parent_name, level):
            _issue(issues, code, severity, message, relative_client)
        if role == "SCENE":
            siblings = (existing_siblings(parent["relative_path"]) if parent["exists"] else []) \
                + new_siblings.get(parent["relative_path"], [])
            for sibling, _kind in _similar_names(name, siblings):
                _issue(issues, "NAME_SIMILAR", "warning",
                       f"'{name}'이(가) 같은 위치의 '{sibling}'와 거의 같은 이름입니다. 결과가 다른 Scene으로 나뉩니다.",
                       relative_client)
            new_siblings.setdefault(parent["relative_path"], []).append(name)

    scene_level = len(scope.roles)
    planned_files: list[dict[str, Any]] = []
    for item in accepted:
        parent = resolved[tuple(item["parts"][:-1])]
        name = item["parts"][-1]
        destination = f"{parent['relative_path']}/{name}"
        if parent["exists"]:
            try:
                info = index.lookup(parent["relative_path"], name)
            except (OSError, StorageError):
                info = None
                _issue(issues, "PATH_UNAVAILABLE", "error", "대상 폴더를 확인할 수 없습니다.", item["relative_path"])
            collision = f"{parent['relative_path']}/{info.name}" if info is not None else None
            if collision:
                conflicts.append({"relative_path": item["relative_path"], "destination_relative_path": collision,
                                  "reason": "EXISTS"})
        length_check(destination, item["relative_path"])
        if parent["level"] < scene_level:
            _issue(issues, "FILE_ABOVE_SCENE", "warning",
                   "Scene 폴더보다 위에 있는 파일은 Case 결과에 표시되지 않습니다(입력 파일이면 그대로 두어도 됩니다).",
                   item["relative_path"])
        planned_files.append({**item, "destination_relative_path": destination, "level": parent["level"] + 1})

    total = sum(int(item["size"]) for item in planned_files)
    margin = _margin(total)
    zone_check(f"{scope.working_relative_path}/{UPLOAD_STAGING_DIR}/{'0' * 32}/0.part", "(임시 업로드 폴더)")
    reserved = _reserved_bytes(scope.root_key)
    free = _free_after_reservations(fs, scope)
    if free is None:
        _issue(issues, "FREE_SPACE_UNKNOWN", "error", "대상 드라이브의 남은 공간을 확인할 수 없습니다.")
    elif free < total + margin:
        gib = 1024 ** 3
        _issue(issues, "FREE_SPACE", "error",
               f"대상 드라이브 공간이 부족합니다(필요 {(total + margin) / gib:.2f} GiB, 남은 공간 {free / gib:.2f} GiB).")
    if conflicts:
        _issue(issues, "CONFLICT", "error", "같은 이름의 파일이 이미 있습니다. 덮어쓰지 않으므로 이름을 바꾸거나 빼고 올리세요.")
    ordered = sorted(issues.values(), key=lambda item: (item["severity"] != "error", item["code"]))
    can_upload = not any(item["severity"] == "error" for item in ordered) and bool(planned_files or folders_to_create)
    target_role = _role_at(scope.roles, target_level)
    public = {
        "project_id": project_id, "request_id": request_id, "environment": scope.environment,
        "target_relative_path": target, "target_display_path": display_path(base, target),
        "target_level": target_level, "target_role": target_role,
        "target_role_label": ROLE_LABELS.get(target_role, target_role),
        "files": [{"client_index": item["client_index"], "relative_path": item["relative_path"],
                   "destination_relative_path": item["destination_relative_path"], "size": item["size"],
                   "role": _role_at(scope.roles, item["level"] - 1)} for item in planned_files],
        "folders_to_create": [{"relative_path": item["relative_path"], "client_path": item["client_path"],
                               "level": item["level"], "role": item["role"],
                               "role_label": ROLE_LABELS.get(item["role"], item["role"])} for item in folders_to_create],
        "skipped": skipped, "conflicts": conflicts, "issues": ordered,
        "file_count": len(planned_files), "folder_count": len(folders_to_create), "total_bytes": total,
        "free_bytes": free, "reserved_bytes": reserved, "required_bytes": total + margin, "can_upload": can_upload,
    }
    internal = {"scope": scope, "target": target, "files": planned_files, "folders": folders_to_create}
    return public, internal


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

@dataclass
class _File:
    index: int
    client_index: int
    relative_path: str
    destination: str
    size: int
    expected_sha256: str | None
    staged: str
    received: int = 0
    sha256: str | None = None
    published: bool = False
    hasher: Any = field(default_factory=hashlib.sha256)

    @property
    def done(self) -> bool:
        return self.sha256 is not None


@dataclass
class _Session:
    id: str
    user_id: str
    scope: Scope
    target: str
    staging: str
    files: list[_File]
    folders: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    total_bytes: int
    state: str = "UPLOADING"
    created_at: float = field(default_factory=time.time)
    touched: float = field(default_factory=time.monotonic)
    created_folders: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


_registry_lock = threading.Lock()
_sessions: dict[str, _Session] = {}
_publish_locks: dict[tuple[str, str], threading.Lock] = {}
_OPEN_STATES = {"UPLOADING", "PUBLISHING", "PARTIAL"}
_create_lock = threading.Lock()


def _active_count(project_id: str, request_id: str, user_id: str | None = None) -> int:
    with _registry_lock:
        return sum(1 for item in _sessions.values()
                   if item.scope.project_id == project_id and item.scope.request_id == request_id
                   and item.state in _OPEN_STATES and (user_id is None or item.user_id == str(user_id)))


def _reserved_bytes(root_key: str) -> int:
    """Bytes other open sessions on the same root still need (review L3: planned free space is net of them)."""
    with _registry_lock:
        return sum(item.size for session in _sessions.values()
                   if session.scope.root_key == root_key and session.state in _OPEN_STATES
                   for item in session.files if not item.published)


def _free_after_reservations(fs: LocalFsProvider, scope: Scope) -> int | None:
    try:
        return max(fs.free_bytes(scope.working_relative_path) - _reserved_bytes(scope.root_key), 0)
    except OSError:
        return None


def _busy_reason(project_id: str, request_id: str, user_id: str) -> str | None:
    if _active_count(project_id, request_id, user_id) >= MAX_ACTIVE_PER_USER:
        return (f"이 의뢰에서 이미 내 업로드 {MAX_ACTIVE_PER_USER}개가 열려 있습니다. "
                "끝내거나 중지한 뒤 다시 시도하세요(멈춘 업로드는 60분 뒤 자동 정리).")
    if _active_count(project_id, request_id) >= MAX_ACTIVE_PER_REQUEST:
        return f"이 의뢰에서 업로드 {MAX_ACTIVE_PER_REQUEST}개가 진행 중입니다. 끝난 뒤 다시 시도하세요."
    return None


def open_sessions(project_id: str, request_id: str, user_id: str, *, admin: bool) -> list[dict[str, Any]]:
    """Open sessions of a request: the caller's own, or every one for a global admin."""
    _expire_sessions()
    with _registry_lock:
        found = [item for item in _sessions.values()
                 if item.scope.project_id == project_id and item.scope.request_id == request_id
                 and item.state in _OPEN_STATES and (admin or item.user_id == str(user_id))]
    return [{"session_id": item.id, "state": item.state, "user_id": item.user_id, "own": item.user_id == str(user_id),
             "target_relative_path": item.target, "file_count": len(item.files), "total_bytes": item.total_bytes,
             "published_files": sum(1 for file in item.files if file.published),
             "idle_seconds": int(time.monotonic() - item.touched)} for item in found]


def _publish_lock(scope: Scope) -> threading.Lock:
    with _registry_lock:
        return _publish_locks.setdefault((scope.root_key, scope.request_id), threading.Lock())


def _remove_staging(session: _Session) -> None:
    """Remove only this session's staged ``<n>.part`` files and its (then empty) folder."""
    fs = provider_for_root(session.scope.root)
    for item in session.files:
        if item.published:
            continue
        try:
            fs.remove(item.staged, zone=WORKING, missing_ok=True)
        except (OSError, StorageError):
            pass
    for folder in (session.staging, session.staging.rsplit("/", 1)[0]):
        try:
            fs.remove(folder, zone=WORKING, directory=True)
        except (OSError, StorageError):
            pass  # not empty (another session) or already gone


def _remove_empty_created(session: _Session) -> None:
    """Review L5: folders this session created that are still empty (deepest first). A folder that
    received anything meanwhile (a published file, an Explorer copy) is not empty and stays."""
    fs = provider_for_root(session.scope.root)
    kept = []
    for folder in sorted(session.created_folders, key=lambda value: value.count("/"), reverse=True):
        try:
            fs.remove(folder, zone=WORKING, directory=True)
        except (OSError, StorageError):
            kept.append(folder)
    session.created_folders = [folder for folder in session.created_folders if folder in kept]


def _expire_sessions() -> None:
    """Idle sessions (uploading or partially published) expire after ``SESSION_IDLE_SECONDS``."""
    now = time.monotonic()
    with _registry_lock:
        expired = [key for key, item in _sessions.items()
                   if item.state != "PUBLISHING" and now - item.touched > SESSION_IDLE_SECONDS]
        dropped = [_sessions.pop(key) for key in expired]
    for item in dropped:
        with item.lock:
            _remove_staging(item)
            _remove_empty_created(item)
            item.state = "EXPIRED"


def _clean_orphans(fs: LocalFsProvider, staging_root: str) -> None:
    """Staging folders of sessions lost with a restart (older than the idle limit): own ``<n>.part`` only."""
    try:
        if fs.stat(staging_root, follow_links=False) is None:
            return
        entries = fs.list(staging_root, stat=True)
    except (OSError, StorageError):
        return
    with _registry_lock:
        live = set(_sessions)
    cutoff_ns = (time.time() - SESSION_IDLE_SECONDS) * 1_000_000_000
    for entry in entries:
        if entry.kind != "dir" or entry.is_link or not _HEX32.match(entry.name) or entry.name in live:
            continue
        if entry.modified_ns is None or entry.modified_ns > cutoff_ns:
            continue
        folder = f"{staging_root}/{entry.name}"
        try:
            for part in fs.list(folder):
                if part.kind == "file" and not part.is_link and _PART.match(part.name):
                    fs.remove(f"{folder}/{part.name}", zone=WORKING, missing_ok=True)
            fs.remove(folder, zone=WORKING, directory=True)
        except (OSError, StorageError):
            continue


def _session_view(session: _Session) -> dict[str, Any]:
    received = sum(item.received for item in session.files)
    return {
        "session_id": session.id, "state": session.state,
        "project_id": session.scope.project_id, "request_id": session.scope.request_id,
        "environment": session.scope.environment, "target_relative_path": session.target,
        "chunk_bytes": CHUNK_BYTES, "file_count": len(session.files), "total_bytes": session.total_bytes,
        "received_bytes": received,
        "completed_files": sum(1 for item in session.files if item.done),
        "files": [{"index": item.index, "client_index": item.client_index, "relative_path": item.relative_path,
                   "destination_relative_path": item.destination, "size": item.size, "received": item.received,
                   "complete": item.done, "published": item.published} for item in session.files],
        "folders_to_create": [item["relative_path"] for item in session.folders],
        "skipped": session.skipped, "conflicts": session.conflicts,
    }


def create_session(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
                   target_relative_path: str, files: list[dict[str, Any]], folders: list[str] | None,
                   user_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the whole plan, then open a staging folder. Returns (session view, plan)."""
    _expire_sessions()
    reason = _busy_reason(project_id, request_id, user_id)
    if reason:
        raise DropUploadError("RESULT_DROP_BUSY", reason, 429)
    plan, internal = build_plan(conn, project_id, request_id, environment, target_relative_path, files, folders)
    if not plan["can_upload"]:
        raise DropUploadError("RESULT_DROP_PLAN_BLOCKED", "올리기 전에 확인할 문제가 있습니다.", 409, plan=plan)
    scope: Scope = internal["scope"]
    fs = provider_for_root(scope.root)
    staging_root = f"{scope.working_relative_path}/{UPLOAD_STAGING_DIR}"
    session_id = uuid4().hex
    staging = f"{staging_root}/{session_id}"
    try:
        if fs.mkdir_pinned(staging_root, zone=WORKING):
            fs.set_hidden(staging_root, zone=WORKING)
        _clean_orphans(fs, staging_root)
        fs.mkdir_pinned(staging, zone=WORKING)
    except (OSError, StorageError) as exc:
        raise DropUploadError("RESULT_DROP_STAGING_UNAVAILABLE", "임시 업로드 폴더를 만들 수 없습니다.", 409) from exc
    session_files = [
        _File(index=index, client_index=item["client_index"], relative_path=item["relative_path"],
              destination=item["destination_relative_path"], size=int(item["size"]),
              expected_sha256=item["sha256"], staged=f"{staging}/{index}.part")
        for index, item in enumerate(internal["files"])
    ]
    session = _Session(id=session_id, user_id=str(user_id), scope=scope, target=internal["target"],
                       staging=staging, files=session_files, folders=internal["folders"],
                       skipped=plan["skipped"], total_bytes=plan["total_bytes"])
    with _create_lock:
        reason = _busy_reason(project_id, request_id, user_id)
        if reason is None:
            # Another session may have reserved space since planning (re-review info).
            free = _free_after_reservations(fs, scope)
            if free is None or free < plan["required_bytes"]:
                _remove_staging(session)
                raise DropUploadError("RESULT_DROP_FREE_SPACE", "다른 업로드가 공간을 예약해 남은 공간이 부족합니다. 잠시 후 다시 시도하세요.", 507)
        if reason is None:
            with _registry_lock:
                _sessions[session_id] = session
    if reason:
        _remove_staging(session)
        raise DropUploadError("RESULT_DROP_BUSY", reason, 429)
    return _session_view(session), plan


def session_scope(session_id: str) -> dict[str, str]:
    with _registry_lock:
        session = _sessions.get(session_id)
    if session is None:
        raise DropUploadError("RESULT_DROP_SESSION_NOT_FOUND", "업로드를 찾을 수 없습니다. 처음부터 다시 올리세요.", 404)
    return {"project_id": session.scope.project_id, "request_id": session.scope.request_id,
            "environment": session.scope.environment, "owner_user_id": session.user_id}


def _session(session_id: str, user_id: str, *, admin: bool = False) -> _Session:
    with _registry_lock:
        session = _sessions.get(session_id)
    if session is None or (session.user_id != str(user_id) and not admin):
        raise DropUploadError("RESULT_DROP_SESSION_NOT_FOUND", "업로드를 찾을 수 없습니다. 처음부터 다시 올리세요.", 404)
    if session.user_id == str(user_id):
        session.touched = time.monotonic()
    return session


def read_session(session_id: str, user_id: str) -> dict[str, Any]:
    session = _session(session_id, user_id)
    with session.lock:
        return _session_view(session)


def upload_chunk(session_id: str, user_id: str, index: int, offset: int, data: bytes,
                 chunk_sha256: str | None = None) -> dict[str, Any]:
    session = _session(session_id, user_id)
    with session.lock:
        if session.state != "UPLOADING":
            raise DropUploadError("RESULT_DROP_SESSION_CLOSED", "이미 끝났거나 중지한 업로드입니다.", 409)
        if index < 0 or index >= len(session.files):
            raise DropUploadError("RESULT_DROP_FILE_NOT_FOUND", "업로드 목록에 없는 파일입니다.", 404)
        item = session.files[index]
        if item.done:
            raise DropUploadError("RESULT_DROP_FILE_COMPLETE", "이미 받은 파일입니다.", 409, expected_offset=item.received)
        if offset != item.received:
            raise DropUploadError("RESULT_DROP_OFFSET_MISMATCH", "이어 올릴 위치가 다릅니다.", 409,
                                  expected_offset=item.received)
        if len(data) > CHUNK_BYTES:
            raise DropUploadError("RESULT_DROP_CHUNK_TOO_LARGE", "조각이 너무 큽니다.", 413)
        if item.received + len(data) > item.size:
            raise DropUploadError("RESULT_DROP_SIZE_EXCEEDED", "선언한 파일 크기보다 많은 데이터를 받았습니다.", 422)
        if not data and item.size:
            raise DropUploadError("RESULT_DROP_CHUNK_EMPTY", "빈 조각입니다.", 422)
        if chunk_sha256 is not None:
            if not re.fullmatch(r"[0-9a-fA-F]{64}", chunk_sha256):
                raise DropUploadError("RESULT_DROP_HASH_INVALID", "SHA-256 값 형식이 올바르지 않습니다.", 422)
            if hashlib.sha256(data).hexdigest() != chunk_sha256.lower():
                raise DropUploadError("RESULT_DROP_CHUNK_HASH_MISMATCH", "조각이 전송 중 바뀌었습니다. 같은 조각을 다시 보내세요.", 422,
                                      expected_offset=item.received)
        fs = provider_for_root(session.scope.root)
        try:
            fs.write_chunk(item.staged, offset, data, zone=WORKING)
        except StorageError as exc:
            if exc.code == "SPDM_CHUNK_OFFSET":
                raise DropUploadError("RESULT_DROP_STAGING_CHANGED", "임시 업로드 파일이 바뀌었습니다. 업로드를 중지하고 다시 올리세요.", 409) from exc
            raise DropUploadError("RESULT_DROP_STAGING_UNSAFE", str(exc), 409) from exc
        except OSError as exc:
            raise DropUploadError("RESULT_DROP_WRITE_FAILED", "임시 업로드 파일을 쓰지 못했습니다. 잠시 후 이어서 올리세요.", 503,
                                  expected_offset=item.received) from exc
        item.hasher.update(data)
        item.received += len(data)
        if item.received == item.size:
            digest = item.hasher.hexdigest()
            if item.expected_sha256 and digest != item.expected_sha256:
                try:
                    fs.remove(item.staged, zone=WORKING, missing_ok=True)
                except (OSError, StorageError):
                    pass
                item.received = 0
                item.hasher = hashlib.sha256()
                raise DropUploadError("RESULT_DROP_HASH_MISMATCH", "받은 파일의 SHA-256이 원본과 다릅니다. 이 파일을 처음부터 다시 보내세요.", 422,
                                      expected_offset=0)
            item.sha256 = digest
        return {"index": index, "received": item.received, "size": item.size, "complete": item.done,
                "sha256": item.sha256}


def _rename_with_retry(fs: LocalFsProvider, source: str, destination: str) -> str:
    """``PUBLISHED`` | ``EXISTS`` | ``BUSY`` — never replaces ``destination``."""
    for delay in (0.0, *RENAME_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            fs.rename_no_replace(source, destination, zone=WORKING)
            return "PUBLISHED"
        except FileExistsError:
            return "EXISTS"
        except PermissionError:
            continue
    return "BUSY"


def _case_links(conn: ConnectionLike, scope: Scope, destinations: list[str]) -> list[dict[str, Any]]:
    working_parts = len(PurePosixPath(scope.working_relative_path).parts)
    cases: dict[str, str] = {}
    for destination in destinations:
        parts = PurePosixPath(destination).parts
        if len(parts) > working_parts + 1:
            case_path = "/".join(parts[:working_parts + 1])
            cases.setdefault(case_path.casefold(), case_path)
    ids: dict[str, str] = {}
    try:
        from . import folder_schema_resolver

        schema = folder_schema_resolver.resolve_request_locations(
            conn, scope.project_id, scope.request_id, scope.environment).schema
        for node in schema.get("nodes", []):
            if node.get("role_kind") == "SIMULATION_CASE" and node.get("target_id"):
                ids[str(node.get("relative_path") or "").casefold()] = str(node["target_id"])
    except Exception:  # noqa: BLE001 - links are a convenience; the Case results screen still lists every Case
        pass
    return [{"case_relative_path": path, "case_name": PurePosixPath(path).name, "case_id": ids.get(key)}
            for key, path in sorted(cases.items())]


def complete(conn: ConnectionLike, session_id: str, user_id: str) -> dict[str, Any]:
    """Publish every received file without replacing anything, then sync the request's folders."""
    from . import folder_auto_sync

    session = _session(session_id, user_id)
    with session.lock:
        if session.state not in {"UPLOADING", "PARTIAL"}:
            raise DropUploadError("RESULT_DROP_SESSION_CLOSED", "이미 끝났거나 중지한 업로드입니다.", 409)
        scope = session.scope
        fs = provider_for_root(scope.root)
        for item in session.files:
            if not item.done and item.size == 0 and not item.published:
                try:
                    fs.write_chunk(item.staged, 0, b"", zone=WORKING)
                except FileExistsError:
                    pass
                except (OSError, StorageError) as exc:
                    raise DropUploadError("RESULT_DROP_STAGING_UNSAFE", "임시 업로드 파일을 만들 수 없습니다.", 409) from exc
                item.sha256 = hashlib.sha256(b"").hexdigest()
        missing = [item.relative_path for item in session.files if not item.done]
        if missing:
            raise DropUploadError("RESULT_DROP_INCOMPLETE", f"아직 받지 못한 파일이 {len(missing)}개 있습니다.", 409,
                                  missing=missing[:ISSUE_PATH_LIMIT])
        # The request, its Working folder and ownership may have changed while uploading.
        current = request_scope(conn, scope.project_id, scope.request_id, scope.environment)
        if (current.root_key != scope.root_key
                or current.working_relative_path.casefold() != scope.working_relative_path.casefold()):
            raise DropUploadError("RESULT_DROP_SCOPE_CHANGED", "업로드 중 의뢰 폴더 연결이 바뀌었습니다. 업로드를 중지하고 다시 올리세요.", 409)
        checked_target(conn, scope, session.target)
        session.state = "PUBLISHING"
        try:
            with _publish_lock(scope):
                pending = [item for item in session.files if not item.published]
                for item in pending:
                    info = fs.stat(item.staged, follow_links=False)
                    if info is None or info.kind != "file" or info.is_link or info.size != item.size:
                        raise DropUploadError("RESULT_DROP_STAGING_CHANGED", "임시 업로드 파일이 바뀌었습니다. 업로드를 중지하고 다시 올리세요.", 409)
                conflicts = []
                session.conflicts = []
                index = _DirIndex(fs)

                def existing(relative: str):
                    parent, name = relative.rsplit("/", 1)
                    try:
                        return parent, index.lookup(parent, name)
                    except (FileNotFoundError, NotADirectoryError):
                        return parent, None  # parent folder not created yet
                    except (OSError, StorageError) as exc:
                        raise DropUploadError("RESULT_DROP_TARGET_UNAVAILABLE",
                                              "대상 폴더를 읽을 수 없습니다. 잠시 후 다시 옮기세요.", 503) from exc

                for folder in session.folders:
                    parent, info = existing(folder["relative_path"])
                    if info is not None and (info.kind != "dir" or info.is_link):
                        conflicts.append({"relative_path": folder["client_path"],
                                          "destination_relative_path": f"{parent}/{info.name}",
                                          "reason": "FILE_IN_PLACE_OF_FOLDER"})
                for item in pending:
                    parent, info = existing(item.destination)
                    if info is not None:
                        conflicts.append({"relative_path": item.relative_path,
                                          "destination_relative_path": f"{parent}/{info.name}", "reason": "EXISTS"})
                if conflicts:
                    session.conflicts = conflicts
                    raise DropUploadError("RESULT_DROP_CONFLICT", "올리는 동안 같은 이름의 파일이 생겼습니다. 덮어쓰지 않았습니다.", 409,
                                          conflicts=conflicts)
                for folder in sorted(session.folders, key=lambda value: (value["level"], value["relative_path"].casefold())):
                    try:
                        if fs.mkdir_pinned(folder["relative_path"], zone=WORKING):
                            session.created_folders.append(folder["relative_path"])
                    except (OSError, StorageError) as exc:
                        raise DropUploadError("RESULT_DROP_FOLDER_FAILED", f"폴더를 만들지 못했습니다: {folder['client_path']}", 409) from exc
                busy: list[str] = []
                for item in pending:
                    try:
                        outcome = _rename_with_retry(fs, item.staged, item.destination)
                    except (OSError, StorageError) as exc:
                        raise DropUploadError("RESULT_DROP_PUBLISH_FAILED", f"파일을 옮기지 못했습니다: {item.relative_path}", 409) from exc
                    if outcome == "PUBLISHED":
                        item.published = True
                    elif outcome == "EXISTS":
                        session.conflicts.append({"relative_path": item.relative_path,
                                                  "destination_relative_path": item.destination, "reason": "EXISTS"})
                    else:
                        busy.append(item.relative_path)
        except BaseException:
            if session.state == "PUBLISHING":
                session.state = "UPLOADING" if not any(item.published for item in session.files) else "PARTIAL"
            _remove_empty_created(session)
            raise
        published = [item for item in session.files if item.published]
        session.state = "PUBLISHED" if len(published) == len(session.files) else "PARTIAL"
        if session.state == "PUBLISHED":
            _remove_staging(session)
        # PARTIAL keeps every folder (also explicitly requested empty ones, re-review N3); empty
        # created folders are removed only on failure, abort or expiry.
    folder_auto_sync.invalidate(scope.root_key, scope.project_id, scope.request_id, scope.environment)
    try:
        sync = folder_auto_sync.sync(conn, scope.project_id, scope.request_id, scope.environment, str(user_id), force=True)
    except Exception:  # noqa: BLE001 - files are published; the 30 s poll retries
        sync = {"status": "FAILED", "code": "FOLDER_SCHEMA_REFRESH_FAILED",
                "message": "폴더를 확인하지 못했습니다. 1분 안에 다시 확인합니다."}
    result = _session_view(session)
    result.update({
        "published_files": len(published), "published_bytes": sum(item.size for item in published),
        "created_folders": list(session.created_folders), "busy": busy,
        "sync": {key: sync.get(key) for key in ("status", "changed", "code", "message", "check_mode")},
        "cases": _case_links(conn, scope, [item.destination for item in published] + session.created_folders),
    })
    if session.state == "PUBLISHED":
        with _registry_lock:
            _sessions.pop(session.id, None)
    return result


def abort(session_id: str, user_id: str, *, admin: bool = False) -> dict[str, Any]:
    """Stop a session: its own staged files and still-empty created folders are removed.

    The owner may abort; a global admin may abort anyone's session (review M2), so a stuck
    upload never blocks the request until it expires.
    """
    session = _session(session_id, user_id, admin=admin)
    with session.lock:
        if session.state == "PUBLISHING":
            raise DropUploadError("RESULT_DROP_SESSION_BUSY", "파일을 옮기는 중에는 중지할 수 없습니다.", 409)
        _remove_staging(session)
        _remove_empty_created(session)
        session.state = "ABORTED"
        view = _session_view(session)
    with _registry_lock:
        _sessions.pop(session.id, None)
    return view


# ---------------------------------------------------------------------------
# "새 폴더 만들기"
# ---------------------------------------------------------------------------

def create_folder(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
                  parent_relative_path: str, name: str, *, confirm: bool, user_id: str) -> dict[str, Any]:
    from . import folder_auto_sync

    name = unicodedata.normalize("NFC", str(name or "").strip())
    if name_problem(name) or _ignored(name):
        raise DropUploadError("RESULT_DROP_FOLDER_NAME_INVALID",
                              "폴더 이름에 쓸 수 없는 문자나 이름입니다(\\ / : * ? \" < > |, 끝 마침표·공백, CON 등, . $ ~ 시작).", 422)
    scope = request_scope(conn, project_id, request_id, environment)
    parent, parent_level = checked_target(conn, scope, parent_relative_path)
    level = parent_level + 1
    role = _role_at(scope.roles, level)
    if role not in _ROLE_BEARING:
        raise DropUploadError("RESULT_DROP_FOLDER_LEVEL_INVALID",
                              "Scene 폴더 안에는 새 폴더를 만들지 않습니다. 결과 파일은 Scene 폴더에 바로 넣으세요.", 422)
    fs = provider_for_root(scope.root)
    relative = f"{parent}/{name}"
    if not working_zone_allows(relative):
        raise DropUploadError("RESULT_DROP_FOLDER_LEVEL_INVALID", "이 위치에는 폴더를 만들 수 없습니다(Working 폴더 밖).", 422)
    if len(str(fs.path(relative))) > MAX_PATH_CHARS or len(display_path(display_root(scope.root), relative)) > MAX_PATH_CHARS:
        raise DropUploadError("RESULT_DROP_PATH_TOO_LONG", "경로가 Windows 길이 한도를 넘습니다. 이름을 줄이세요.", 422)
    existing_entry = _DirIndex(fs).lookup(parent, name)
    existing = f"{parent}/{existing_entry.name}" if existing_entry is not None else None
    if existing:
        raise DropUploadError("RESULT_DROP_FOLDER_EXISTS", "같은 이름의 폴더가 이미 있습니다.", 409,
                              relative_path=existing)
    findings = _depth_findings(scope, _known_names(scope), name, PurePosixPath(parent).name, level)
    errors = [message for _code, severity, message in findings if severity == "error"]
    if errors:
        raise DropUploadError("RESULT_DROP_FOLDER_DEPTH_INVALID", errors[0], 422)
    siblings = _children(fs, parent)
    similar = _similar_names(name, siblings)
    if any(kind == "SCENE_NAME_CASE" for _sibling, kind in similar):
        sibling = next(sibling for sibling, kind in similar if kind == "SCENE_NAME_CASE")
        raise DropUploadError("RESULT_DROP_FOLDER_NAME_DUPLICATE",
                              f"'{sibling}'와 대소문자·구분 기호만 다른 이름입니다. 기존 폴더를 쓰세요.", 409)
    warnings = [message for _code, severity, message in findings if severity == "warning"]
    warnings += [f"같은 위치의 '{sibling}'와 거의 같은 이름입니다. 결과가 다른 {ROLE_LABELS.get(role, role)}로 나뉩니다."
                 for sibling, _kind in similar]
    if warnings and not confirm:
        raise DropUploadError("RESULT_DROP_FOLDER_NAME_WARNING", warnings[0], 409, warnings=warnings)
    try:
        created = fs.mkdir_pinned(relative, zone=WORKING)
    except (OSError, StorageError) as exc:
        raise DropUploadError("RESULT_DROP_FOLDER_FAILED", "폴더를 만들지 못했습니다.", 409) from exc
    if not created:
        raise DropUploadError("RESULT_DROP_FOLDER_EXISTS", "같은 이름의 폴더가 이미 있습니다.", 409, relative_path=relative)
    folder_auto_sync.invalidate(scope.root_key, project_id, request_id, scope.environment)
    try:
        sync = folder_auto_sync.sync(conn, project_id, request_id, scope.environment, str(user_id), force=True)
    except Exception:  # noqa: BLE001
        sync = {"status": "FAILED"}
    return {"relative_path": relative, "display_path": display_path(display_root(scope.root), relative),
            "level": level, "role": role, "role_label": ROLE_LABELS.get(role, role), "warnings": warnings,
            "sync": {key: sync.get(key) for key in ("status", "changed", "code", "message")}}


def reset_for_tests() -> None:
    with _registry_lock:
        _sessions.clear()
        _publish_locks.clear()
