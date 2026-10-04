"""Trusted Windows-friendly SPDM workspace storage adapter.

This adapter deliberately does not share the canonical result-import root.
SPDM folders contain source artefacts as well as optional supported result
files, so discovery is explicit, target-bound and fail-closed.  The only
automatic database creation is for an exact Issue #13 project/WR/CAE leaf.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal

from fastapi import HTTPException

from ..database_connection import ConnectionLike, rows
from .storage.local import LocalFsProvider, prepare_root, resolve_existing_or_none, resolve_root
from .storage.provider import LEGACY, SpdmStorageError


_PROJECT = re.compile(r"^Project_([A-Za-z0-9][A-Za-z0-9._-]*)_([A-Za-z0-9][A-Za-z0-9._-]*)_([A-Za-z0-9][A-Za-z0-9._-]*)$")
_WORK_REQUEST = re.compile(r"^WR_([A-Za-z0-9][A-Za-z0-9._-]*)_SimType([12])$")
_SAFE_SEGMENT = re.compile(r"^[^<>:\\/?*\x00-\x1f]+$")
_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})
_ANALYSES = frozenset({
    "CMS", "Modal", "2kgfPush", "Deflection", "Stiffness", "Settle", "Wobble",
    "Vertical_Wobble", "Horizontal_Force_Angle", "Slope_Angle", "Vibration",
    "ColdDrop", "StandFailure", "Drop", "Clamping",
})
_FOLDER_KINDS: dict[str, tuple[str, ...]] = {
    "results": (".csv", ".json"),
    "solver": (".h3d", ".txt", ".pkl"),
    "media": (".png", ".jpg", ".jpeg", ".svg", ".mp4", ".webm", ".glb", ".gltf"),
    "inputs": (".prt", ".x_t", ".fem", ".rad", ".xml", ".hm", ".mdl"),
    "reports": (".pdf", ".ppt", ".pptx"),
}
_storage_lock = threading.RLock()


@dataclass(frozen=True)
class StorageRoot:
    root: Path | None
    locked: bool
    configured: bool


@dataclass(frozen=True)
class Candidate:
    relative_path: str
    project_name: str
    work_request_name: str
    analysis_name: str
    analysis_type: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stable_id(kind: str, identity: str) -> str:
    return f"spdm-{kind}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def _normalise_relative(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\\" in value or "\x00" in value:
        raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 상대 경로가 올바르지 않습니다.")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 상대 경로가 올바르지 않습니다.")
    for part in path.parts:
        if not _valid_windows_name(part):
            raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 경로 이름이 Windows 파일 규칙에 맞지 않습니다.")
    return "/".join(path.parts)


def _valid_windows_name(name: str) -> bool:
    stem = name.rstrip(". ").split(".", 1)[0].upper()
    return (
        bool(_SAFE_SEGMENT.fullmatch(name))
        and name == name.rstrip(". ")
        and bool(stem)
        and stem not in _WINDOWS_RESERVED
        and ":" not in name
    )


def _ensure_directory(root: Path, relative: str) -> str:
    """Create the exact folder chain (LEGACY zone); returns the root-relative path."""
    fs = LocalFsProvider(root)
    target = ""
    for part in PurePosixPath(relative).parts:
        fs.assert_safe(target)
        existing = fs.case_collision(target, part)
        if existing is not None:
            if existing.rsplit("/", 1)[-1] != part or not fs.is_dir(existing) or fs.is_link(existing):
                raise SpdmStorageError("SPDM_PATH_COLLISION", "SPDM 저장 폴더 이름이 충돌하거나 안전하지 않습니다.")
            target = existing
            continue
        target = fs.join(target, part)
        fs.mkdirs(target, zone=LEGACY, parents=False, exist_ok=False)
    fs.assert_safe(target)
    return target


def _setting(conn: ConnectionLike) -> str | None:
    row = conn.execute("SELECT setting_value FROM spdm_storage_settings WHERE setting_key='root'").fetchone()
    return str(row[0]) if row and row[0] else None


def _root_identity(path: Path) -> str:
    """Raw root identity (``st_dev:st_ino:<casefold path>``) from the storage provider."""
    return LocalFsProvider(path).root_identity()


def _stored_identity(conn: ConnectionLike) -> str | None:
    row = conn.execute("SELECT setting_value FROM spdm_storage_settings WHERE setting_key='root_identity'").fetchone()
    return str(row[0]) if row and row[0] else None


def _lock_root_identity(conn: ConnectionLike, root: Path) -> None:
    """Persist the first observed identity before any binding is written."""
    current = _root_identity(root)
    stored = _stored_identity(conn)
    count = int(conn.execute("SELECT count(*) FROM spdm_storage_bindings").fetchone()[0])
    if count and not stored:
        raise SpdmStorageError("SPDM_ROOT_IDENTITY_UNVERIFIED", "기존 SPDM 연결의 root identity를 확인할 수 없습니다.")
    if stored and stored != current:
        raise SpdmStorageError("SPDM_ROOT_IDENTITY_DRIFT", "SPDM root의 파일시스템 identity가 변경되었습니다.")
    if stored is None:
        conn.execute(
            "INSERT INTO spdm_storage_settings(setting_key, setting_value, updated_at) VALUES ('root_identity', ?, ?) "
            "ON CONFLICT(setting_key) DO NOTHING",
            [current, utc_now()],
        )


def storage_root(conn: ConnectionLike) -> StorageRoot:
    raw_env = os.getenv("SIMDASH_SPDM_ROOT", "").strip()
    persisted = _setting(conn)
    raw = raw_env or persisted
    if not raw:
        return StorageRoot(None, bool(raw_env), False)
    resolved = resolve_root(raw)
    persisted_identity = _stored_identity(conn)
    current_identity = _root_identity(resolved)
    binding_count = int(conn.execute("SELECT count(*) FROM spdm_storage_bindings").fetchone()[0])
    if binding_count and not persisted_identity:
        raise SpdmStorageError("SPDM_ROOT_IDENTITY_UNVERIFIED", "기존 SPDM 연결의 root identity를 확인할 수 없습니다.")
    if binding_count and persisted_identity != current_identity:
        raise SpdmStorageError("SPDM_ROOT_IDENTITY_DRIFT", "SPDM root의 파일시스템 identity가 변경되었습니다.")
    if raw_env and persisted:
        persisted_path = resolve_existing_or_none(persisted)
        if persisted_path != resolved and conn.execute("SELECT count(*) FROM spdm_storage_bindings").fetchone()[0]:
            raise SpdmStorageError("SPDM_ROOT_BINDING_CONFLICT", "기존 SPDM 연결이 있어 환경 root를 변경할 수 없습니다.")
    return StorageRoot(resolved, bool(raw_env), True)


def set_storage_root(conn: ConnectionLike, value: str) -> StorageRoot:
    if os.getenv("SIMDASH_SPDM_ROOT", "").strip():
        raise SpdmStorageError("SPDM_ROOT_LOCKED", "환경 변수로 설정된 SPDM root는 여기서 변경할 수 없습니다.")
    resolved = prepare_root(value)
    persisted = _setting(conn)
    if persisted:
        previous = resolve_existing_or_none(persisted)
        if previous != resolved and conn.execute("SELECT count(*) FROM spdm_storage_bindings").fetchone()[0]:
            raise SpdmStorageError("SPDM_ROOT_BINDING_CONFLICT", "기존 SPDM 연결이 있어 root를 변경할 수 없습니다.")
    now = utc_now()
    conn.execute(
        "INSERT INTO spdm_storage_settings(setting_key, setting_value, updated_at) VALUES ('root', ?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at",
        [str(resolved), now],
    )
    conn.execute(
        "INSERT INTO spdm_storage_settings(setting_key, setting_value, updated_at) VALUES ('root_identity', ?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, updated_at=excluded.updated_at",
        [_root_identity(resolved), now],
    )
    return StorageRoot(resolved, False, True)


def rules(binding: dict[str, str] | None = None) -> list[dict[str, Any]]:
    labels = {"results": "구조화 결과", "solver": "Solver 원본", "media": "미디어 원본", "inputs": "입력 원본", "reports": "보고서 원본"}
    def folder_for(kind: str) -> str:
        if binding is None:
            return kind
        return _report_directory_relative(binding) if kind == "reports" else f"{binding['relative_path']}/{kind}"
    return [
        {"kind": kind, "label": labels[kind], "folder": folder_for(kind), "extensions": list(extensions), "description": "원본 파일은 index·다운로드만 제공하며 CSV/JSON 결과만 검증 적재합니다."}
        for kind, extensions in _FOLDER_KINDS.items()
    ] + [{"kind": "unknown", "label": "미분류 원본", "folder": "-", "extensions": [], "description": "계약 밖 파일은 보관·다운로드 목록에만 표시하고 해석 결과로 적재하지 않습니다."}]


def _child_directories(fs: LocalFsProvider, relative: str) -> list[str]:
    """Plain (non-link) child folders in listing order: ``is_dir() and not reparse``."""
    found: list[str] = []
    for entry in fs.list(relative):
        child = fs.join(relative, entry.name)
        if fs.is_dir(child) and not fs.is_link(child):
            found.append(child)
    return found


def _sorted_children(fs: LocalFsProvider, relative: str) -> list[str]:
    return sorted(_child_directories(fs, relative), key=lambda item: item.rsplit("/", 1)[-1].casefold())


def _name(relative: str) -> str:
    return relative.rsplit("/", 1)[-1]


def candidates(root: Path) -> list[Candidate]:
    fs = LocalFsProvider(root)
    found: list[Candidate] = []
    for project_dir in _sorted_children(fs, ""):
        match = _PROJECT.fullmatch(_name(project_dir))
        if match is None:
            continue
        for request_dir in _sorted_children(fs, project_dir):
            work = _WORK_REQUEST.fullmatch(_name(request_dir))
            if work is None:
                continue
            cae = fs.join(request_dir, "CAE")
            if not fs.is_dir(cae) or fs.is_link(cae):
                continue
            for case_dir in _sorted_children(fs, cae):
                for analysis_dir in _sorted_children(fs, case_dir):
                    if _name(analysis_dir) not in _ANALYSES:
                        continue
                    for relative in _analysis_leaves(fs, analysis_dir):
                        found.append(Candidate(relative, _name(project_dir), _name(request_dir), _name(analysis_dir), _analysis_type(_name(analysis_dir))))
    return found


def _request_parents(root: Path) -> Iterable[tuple[str, str]]:
    """Yield exact Project/WR folders even when CAE has no result leaf yet."""
    fs = LocalFsProvider(root)
    for project_dir in _sorted_children(fs, ""):
        if _PROJECT.fullmatch(_name(project_dir)) is None:
            continue
        for request_dir in _sorted_children(fs, project_dir):
            if _WORK_REQUEST.fullmatch(_name(request_dir)) is not None:
                yield _name(project_dir), _name(request_dir)


def _analysis_leaves(fs: LocalFsProvider, analysis_dir: str) -> Iterable[str]:
    # A non-drop analysis is itself its stable load-case root.  Descendant
    # ``results``/``solver``/``inputs`` folders are storage categories, never
    # separately discovered load cases.  Drop/Clamping is the one Issue #13
    # exception: its scene identity is exactly series/INDIVIDUAL|CUMULATIVE/
    # Scene below the analysis root.
    if _name(analysis_dir) not in {"Drop", "Clamping"}:
        return [analysis_dir]
    leaves: list[str] = []
    for series_entry in fs.list(analysis_dir):
        series = fs.join(analysis_dir, series_entry.name)
        if not fs.is_dir(series) or fs.is_link(series):
            continue
        modes = [series] if series_entry.name in {"INDIVIDUAL", "CUMULATIVE"} else [
            item for item in (fs.join(series, entry.name) for entry in fs.list(series))
            if fs.is_dir(item) and not fs.is_link(item) and _name(item) in {"INDIVIDUAL", "CUMULATIVE"}
        ]
        for mode in modes:
            for scene_entry in fs.list(mode):
                scene = fs.join(mode, scene_entry.name)
                if fs.is_dir(scene) and not fs.is_link(scene) and _valid_windows_name(scene_entry.name):
                    leaves.append(scene)
    return leaves


def _analysis_type(name: str) -> str:
    if name == "Drop":
        return "DROP"
    if name == "Clamping":
        return "SIDE_CLAMP"
    return f"SPDM_{name.upper()}"


def candidate_for(root: Path, relative_path: str) -> Candidate:
    normalized = _normalise_relative(relative_path)
    for item in candidates(root):
        if item.relative_path == normalized:
            return item
    # Binding is an explicit write operation.  Unlike discovery, it may create
    # a valid Issue #13 leaf in an empty root for an existing database target.
    # It still accepts only the exact canonical shape; a free-form folder never
    # becomes a load-case storage root.
    return _candidate_from_relative(normalized)


def _candidate_from_relative(relative_path: str) -> Candidate:
    parts = PurePosixPath(relative_path).parts
    if len(parts) < 5 or parts[2] != "CAE":
        return _raise_invalid_candidate()
    project = _PROJECT.fullmatch(parts[0])
    request = _WORK_REQUEST.fullmatch(parts[1])
    if project is None or request is None or not _valid_windows_name(parts[3]):
        return _raise_invalid_candidate()
    analysis = parts[4]
    if analysis not in _ANALYSES:
        return _raise_invalid_candidate()
    if analysis not in {"Drop", "Clamping"}:
        if len(parts) != 5:
            return _raise_invalid_candidate()
    elif len(parts) != 8 or parts[6] not in {"INDIVIDUAL", "CUMULATIVE"} or not _valid_windows_name(parts[5]) or not _valid_windows_name(parts[7]):
        return _raise_invalid_candidate()
    return Candidate(relative_path, parts[0], parts[1], analysis, _analysis_type(analysis))


def _raise_invalid_candidate() -> Candidate:
    raise SpdmStorageError("SPDM_BINDING_INVALID", "Issue #13 형식의 분석 leaf만 연결할 수 있습니다.")


def _target_for_binding(conn: ConnectionLike, load_case_id: str) -> tuple[str, str]:
    row = conn.execute("SELECT ar.project_id, lc.request_id FROM load_cases lc JOIN analysis_requests ar ON ar.id=lc.request_id WHERE lc.id=?", [load_case_id]).fetchone()
    if not row:
        raise SpdmStorageError("SPDM_LOAD_CASE_NOT_FOUND", "하중 경우를 찾을 수 없습니다.")
    return str(row[0]), str(row[1])


def _parent_folders(relative_path: str) -> tuple[str, str]:
    parts = PurePosixPath(relative_path).parts
    if len(parts) < 2:
        raise SpdmStorageError("SPDM_BINDING_INVALID", "SPDM parent 경로가 올바르지 않습니다.")
    return parts[0], "/".join(parts[:2])


def _legacy_parent_targets(conn: ConnectionLike, project_folder: str, request_folder: str) -> tuple[set[str], set[tuple[str, str]]]:
    project_ids: set[str] = set()
    request_targets: set[tuple[str, str]] = set()
    for row in rows(conn.execute("SELECT project_id, request_id, relative_path FROM spdm_storage_bindings")):
        try:
            bound_project, bound_request = _parent_folders(str(row["relative_path"]))
        except SpdmStorageError:
            continue
        if bound_project == project_folder:
            project_ids.add(str(row["project_id"]))
        if bound_request == request_folder:
            request_targets.add((str(row["project_id"]), str(row["request_id"])))
    return project_ids, request_targets


def _register_parent_target(conn: ConnectionLike, relative_path: str, project_id: str, request_id: str) -> None:
    """Bind exact external Project/WR segments to one database parent tuple."""
    project_folder, request_folder = _parent_folders(relative_path)
    project_row = conn.execute("SELECT project_id FROM spdm_storage_project_parents WHERE project_folder=?", [project_folder]).fetchone()
    request_row = conn.execute("SELECT project_id, request_id FROM spdm_storage_request_parents WHERE request_folder=?", [request_folder]).fetchone()
    reverse_project = conn.execute("SELECT project_folder FROM spdm_storage_project_parents WHERE project_id=?", [project_id]).fetchone()
    reverse_request = conn.execute("SELECT request_folder FROM spdm_storage_request_parents WHERE request_id=?", [request_id]).fetchone()
    legacy_projects, legacy_requests = _legacy_parent_targets(conn, project_folder, request_folder)
    if (
        (project_row and str(project_row[0]) != project_id)
        or (reverse_project and str(reverse_project[0]) != project_folder)
        or legacy_projects - {project_id}
        or (request_row and (str(request_row[0]), str(request_row[1])) != (project_id, request_id))
        or (reverse_request and str(reverse_request[0]) != request_folder)
        or legacy_requests - {(project_id, request_id)}
    ):
        raise SpdmStorageError("SPDM_PARENT_BINDING_CONFLICT", "같은 SPDM Project/WR 폴더는 하나의 프로젝트·의뢰에만 연결할 수 있습니다.")
    now = utc_now()
    conn.execute(
        "INSERT INTO spdm_storage_project_parents(project_folder, project_id, created_at, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_folder) DO NOTHING",
        [project_folder, project_id, now, now],
    )
    conn.execute(
        "INSERT INTO spdm_storage_request_parents(request_folder, project_folder, project_id, request_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(request_folder) DO NOTHING",
        [request_folder, project_folder, project_id, request_id, now, now],
    )


def _registered_parent_target(conn: ConnectionLike, project_name: str, work_request_name: str) -> tuple[str, str] | None:
    request_folder = f"{project_name}/{work_request_name}"
    request_row = conn.execute("SELECT project_id, request_id FROM spdm_storage_request_parents WHERE request_folder=?", [request_folder]).fetchone()
    if request_row:
        return str(request_row[0]), str(request_row[1])
    project_row = conn.execute("SELECT project_id FROM spdm_storage_project_parents WHERE project_folder=?", [project_name]).fetchone()
    legacy_projects, legacy_requests = _legacy_parent_targets(conn, project_name, request_folder)
    if len(legacy_requests) > 1 or len(legacy_projects) > 1:
        raise SpdmStorageError("SPDM_PARENT_BINDING_CONFLICT", "기존 SPDM parent 연결이 하나의 DB 대상으로 일치하지 않습니다.")
    if legacy_requests:
        project_id, request_id = next(iter(legacy_requests))
        _register_parent_target(conn, request_folder, project_id, request_id)
        return project_id, request_id
    if project_row is not None:
        return str(project_row[0]), _stable_id("request", request_folder)
    if legacy_projects:
        return next(iter(legacy_projects)), _stable_id("request", request_folder)
    return None


def bind_existing_target(conn: ConnectionLike, root: Path, load_case_id: str, relative_path: str) -> dict[str, Any]:
    candidate = candidate_for(root, relative_path)
    # The semantic mapper takes this same pair of table locks in this order.
    # It makes the path-owner decision serializable across the two collectors.
    with _ownership_transaction(conn):
        _lock_root_identity(conn, root)
        _reject_semantic_overlap(conn, candidate.relative_path, root)
        project_id, request_id = _target_for_binding(conn, load_case_id)
        _register_parent_target(conn, candidate.relative_path, project_id, request_id)
        existing_path = conn.execute("SELECT load_case_id, project_id, request_id FROM spdm_storage_bindings WHERE relative_path=?", [candidate.relative_path]).fetchone()
        if existing_path and str(existing_path[0]) != load_case_id:
            raise SpdmStorageError("SPDM_BINDING_CONFLICT", "이 SPDM 폴더는 다른 하중 경우에 이미 연결되어 있습니다.")
        existing_target = conn.execute("SELECT relative_path FROM spdm_storage_bindings WHERE load_case_id=?", [load_case_id]).fetchone()
        if existing_target and str(existing_target[0]) != candidate.relative_path:
            raise SpdmStorageError("SPDM_TARGET_ALREADY_BOUND", "하중 경우에는 하나의 SPDM 폴더만 연결할 수 있습니다.")
        # Publish the directory before the database binding.  A failed mkdir must
        # never leave a database row pointing at an absent external workspace.
        _ensure_directory(root, candidate.relative_path)
        now = utc_now()
        conn.execute(
            "INSERT INTO spdm_storage_bindings(load_case_id, project_id, request_id, relative_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(load_case_id) DO UPDATE SET relative_path=excluded.relative_path, updated_at=excluded.updated_at",
            [load_case_id, project_id, request_id, candidate.relative_path, now, now],
        )
        return binding_row(load_case_id, project_id, request_id, candidate.relative_path)


@contextmanager
def _ownership_transaction(conn: ConnectionLike):
    """Guard a legacy write against concurrent semantic folder ownership."""
    conn.execute("BEGIN TRANSACTION")
    try:
        if getattr(conn, "backend", "duckdb") == "postgresql":
            # Keep this order aligned with semantic_mapping.save_binding.
            conn.execute("LOCK TABLE semantic_folder_bindings IN SHARE ROW EXCLUSIVE MODE")
            conn.execute("LOCK TABLE spdm_storage_bindings IN SHARE ROW EXCLUSIVE MODE")
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _reject_semantic_overlap(conn: ConnectionLike, relative_path: str, root: Path | None = None) -> None:
    """Keep legacy discovery from claiming a semantic mapping boundary.

    Semantic parent tags may contain a child load-case path, so either
    ancestor/descendant overlap is owned by the semantic collector.  This
    check is deliberately at both manual and automatic legacy creation paths.
    """
    for row in conn.execute("SELECT relative_path FROM semantic_folder_bindings").fetchall():
        semantic_path = str(row[0]).casefold()
        legacy_path = relative_path.casefold()
        if legacy_path == semantic_path or legacy_path.startswith(semantic_path + "/") or semantic_path.startswith(legacy_path + "/"):
            raise SpdmStorageError("SPDM_SEMANTIC_PATH_OWNED", "의미 매핑이 소유한 경로는 기존 SPDM 수집에 연결할 수 없습니다.")
    # New explicit discovery owns its selected subtrees too.  Import lazily so
    # the storage adapter remains usable while the discovery feature is absent.
    if root is not None:
        from .folder_discovery_scan import root_identity
        from .folder_discovery_plan import overlaps
        key = root_identity(root)
        rows = conn.execute("SELECT relative_path FROM folder_discovery_registry WHERE root_key=?", [key]).fetchall()
        for row in rows:
            if overlaps(relative_path, str(row[0])):
                raise SpdmStorageError("SPDM_FOLDER_DISCOVERY_PATH_OWNED", "명시적 폴더 탐색이 소유한 경로는 기존 SPDM 수집에 연결할 수 없습니다.")


def binding_row(load_case_id: str, project_id: str, request_id: str, relative_path: str) -> dict[str, str]:
    return {"relative_path": relative_path, "project_id": project_id, "request_id": request_id, "load_case_id": load_case_id}


def _ensure_discovered_request_parent(
    conn: ConnectionLike,
    project_name: str,
    work_request_name: str,
    *,
    creator_id: str,
    creator_name: str,
    root: Path | None = None,
) -> None:
    """Create a discoverable request without guessing a load case or result."""
    existing = _registered_parent_target(conn, project_name, work_request_name)
    project_id, request_id = existing or (
        _stable_id("project", project_name),
        _stable_id("request", f"{project_name}/{work_request_name}"),
    )
    now = utc_now()
    conn.execute("BEGIN TRANSACTION")
    try:
        if getattr(conn, "backend", "duckdb") == "postgresql":
            conn.execute("LOCK TABLE semantic_folder_bindings, spdm_storage_bindings IN SHARE ROW EXCLUSIVE MODE")
        if root is not None:
            _reject_semantic_overlap(conn, f"{project_name}/{work_request_name}", root)
        conn.execute("INSERT INTO projects(id, name, product_name, description, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING", [project_id, project_name, project_name, "SPDM folder discovery", now])
        conn.execute("INSERT INTO product_information(id, project_id, category, name, value_text, file_path, metadata_json) VALUES (?, ?, 'MODEL', 'SPDM 프로젝트', ?, NULL, ?) ON CONFLICT(id) DO NOTHING", [_stable_id("product", project_id), project_id, project_name, json.dumps({"source": "SPDM"})])
        conn.execute("INSERT INTO project_memberships(id, project_id, user_id, role, created_by, created_at, updated_by, updated_at) VALUES (?, ?, ?, 'admin', ?, ?, ?, ?) ON CONFLICT(project_id, user_id) DO NOTHING", [_stable_id("membership", f"{project_id}/{creator_id}"), project_id, creator_id, creator_id, now, creator_id, now])
        conn.execute("INSERT INTO analysis_requests(id, project_id, title, status, owner, owner_user_id, requested_at, due_at, overall_note) VALUES (?, ?, ?, 'READY', ?, ?, ?, NULL, ?) ON CONFLICT(id) DO NOTHING", [request_id, project_id, work_request_name, creator_name, creator_id, now, f"SPDM: {work_request_name}"])
        actual_project = conn.execute("SELECT name FROM projects WHERE id=?", [project_id]).fetchone()
        actual_request = conn.execute("SELECT project_id, title FROM analysis_requests WHERE id=?", [request_id]).fetchone()
        # A manually bound parent owns its existing labels; only a new
        # deterministic parent must have names generated from the folder.
        if actual_project is None or actual_request is None or str(actual_request[0]) != project_id or (existing is None and (str(actual_project[0]) != project_name or str(actual_request[1]) != work_request_name)):
            raise SpdmStorageError("SPDM_DISCOVERY_ID_CONFLICT", "SPDM 폴더의 기존 DB 대상과 정본 관계가 일치하지 않습니다.")
        _register_parent_target(conn, f"{project_name}/{work_request_name}", project_id, request_id)
        from ..database import ensure_project_quality_thresholds, ensure_workspace_layouts
        ensure_project_quality_thresholds(conn)
        ensure_workspace_layouts(conn)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def discover_bindings(conn: ConnectionLike, root: Path, *, creator_id: str = "system", creator_name: str = "SPDM discovery") -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    _lock_root_identity(conn, root)
    # Project/WR discovery is independent from CAE/result discovery: a new
    # request remains visible as a zero-load-case workspace until files arrive.
    for project_name, work_request_name in _request_parents(root):
        _ensure_discovered_request_parent(
            conn, project_name, work_request_name,
            creator_id=creator_id, creator_name=creator_name,
            root=root,
        )
    for candidate in candidates(root):
        with _ownership_transaction(conn):
            existing = conn.execute("SELECT load_case_id FROM spdm_storage_bindings WHERE relative_path=?", [candidate.relative_path]).fetchone()
            if existing:
                continue
            _reject_semantic_overlap(conn, candidate.relative_path, root)
            parent = _registered_parent_target(conn, candidate.project_name, candidate.work_request_name)
            if parent is None:
                raise SpdmStorageError("SPDM_PARENT_BINDING_CONFLICT", "SPDM Project/WR parent registry를 확인할 수 없습니다.")
            project_id, request_id = parent
            load_case_id = _stable_id("loadcase", candidate.relative_path)
            now = utc_now()
            conn.execute("INSERT INTO load_cases(id, request_id, name, analysis_type, status, parameters_json, created_at) VALUES (?, ?, ?, ?, 'READY', ?, ?) ON CONFLICT(id) DO NOTHING", [load_case_id, request_id, candidate.relative_path.rsplit('/', 1)[-1], candidate.analysis_type, json.dumps({"source": "SPDM", "analysis": candidate.analysis_name}), now])
            actual_case = conn.execute("SELECT request_id FROM load_cases WHERE id=?", [load_case_id]).fetchone()
            if actual_case is None or str(actual_case[0]) != request_id:
                raise SpdmStorageError("SPDM_DISCOVERY_ID_CONFLICT", "SPDM 폴더의 기존 DB 대상과 정본 관계가 일치하지 않습니다.")
            conn.execute("INSERT INTO spdm_storage_bindings(load_case_id, project_id, request_id, relative_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", [load_case_id, project_id, request_id, candidate.relative_path, now, now])
            # Reuse the project defaults written by the standard project
            # service: threshold and workspace APIs must work immediately for
            # a discovered project, even though its request has no guessed plan.
            from ..database import ensure_project_quality_thresholds, ensure_workspace_layouts
            ensure_project_quality_thresholds(conn)
            ensure_workspace_layouts(conn)
        created.append(binding_row(load_case_id, project_id, request_id, candidate.relative_path))
    return created


def get_binding(conn: ConnectionLike, load_case_id: str) -> dict[str, str] | None:
    row = conn.execute("SELECT load_case_id, project_id, request_id, relative_path FROM spdm_storage_bindings WHERE load_case_id=?", [load_case_id]).fetchone()
    return binding_row(str(row[0]), str(row[1]), str(row[2]), str(row[3])) if row else None


def all_bindings(conn: ConnectionLike) -> list[dict[str, str]]:
    return [binding_row(str(row["load_case_id"]), str(row["project_id"]), str(row["request_id"]), str(row["relative_path"])) for row in rows(conn.execute("SELECT load_case_id, project_id, request_id, relative_path FROM spdm_storage_bindings ORDER BY relative_path"))]


def bound_paths(conn: ConnectionLike) -> set[str]:
    return {item["relative_path"] for item in all_bindings(conn)}


def file_locator(conn: ConnectionLike, file_id: str) -> dict[str, str] | None:
    values = rows(conn.execute("SELECT load_case_id, relative_path, name FROM spdm_storage_files WHERE id=?", [file_id]))
    if not values:
        return None
    return {"load_case_id": str(values[0]["load_case_id"]), "relative_path": str(values[0]["relative_path"]), "name": str(values[0]["name"])}


def _file_kind(relative_to_leaf: PurePosixPath) -> str | None:
    if not relative_to_leaf.parts:
        return None
    root = relative_to_leaf.parts[0]
    suffix = Path(relative_to_leaf.name).suffix.lower()
    if root in _FOLDER_KINDS and suffix in _FOLDER_KINDS[root]:
        return root
    return None


def _extension_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return next((kind for kind, extensions in _FOLDER_KINDS.items() if suffix in extensions), None)


def _binding_parent(binding: dict[str, str]) -> tuple[str, str]:
    parts = binding["relative_path"].split("/")
    if len(parts) < 2:
        raise SpdmStorageError("SPDM_BINDING_INVALID", "SPDM binding 경로가 올바르지 않습니다.")
    return parts[0], parts[1]


def _report_directory_relative(binding: dict[str, str]) -> str:
    parts = binding["relative_path"].split("/")
    if len(parts) < 5 or parts[2] != "CAE":
        raise SpdmStorageError("SPDM_BINDING_INVALID", "보고서 대상 SPDM leaf 형식이 올바르지 않습니다.")
    # Mirror every CAE identity segment.  Scene names repeat across parallel
    # and serial Drop/Clamping series, so a case/analysis/scene abbreviation
    # would make two valid report destinations collide.
    return "/".join((parts[0], parts[1], "보고서", *parts[3:]))


def _physical_relative(binding: dict[str, str], kind: str) -> str:
    return _report_directory_relative(binding) if kind == "reports" else f"{binding['relative_path']}/{kind}"


def _sha256(fs: LocalFsProvider, relative: str) -> tuple[str, int]:
    payload, checksum = fs.read_stable_digest(relative)
    return checksum, len(payload)


def _complete_sidecar(fs: LocalFsProvider, relative: str, checksum: str) -> bool:
    sidecar = relative + ".simdashboard-complete.json"
    try:
        with fs.open_read(sidecar) as stream:
            payload = json.loads(stream.read().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, SpdmStorageError):
        return False
    return isinstance(payload, dict) and payload.get("sha256") == checksum and payload.get("state") == "READY"


def refresh_binding_files(conn: ConnectionLike, root: Path, binding: dict[str, str]) -> list[dict[str, Any]]:
    fs = LocalFsProvider(root)
    leaf = "/".join(PurePosixPath(binding["relative_path"]).parts)
    # An external producer may remove an entire leaf.  Keep its immutable run
    # history and index rows, mark the current artefacts missing, and let a
    # global refresh continue with independent bindings.
    if not fs.exists(leaf):
        conn.execute(
            "UPDATE spdm_storage_files SET status='MISSING', updated_at=? WHERE load_case_id=?",
            [utc_now(), binding["load_case_id"]],
        )
        return list_files(conn, binding["load_case_id"])
    fs.assert_safe(leaf)
    if not fs.is_dir(leaf):
        raise SpdmStorageError("SPDM_BINDING_MISSING", "연결된 SPDM 폴더를 찾을 수 없습니다.")
    observed: set[str] = set()
    for current, dirs, files in fs.walk(leaf):
        dirs[:] = [name for name in dirs if not name.startswith('.') and not fs.is_link(fs.join(current, name))]
        for name in files:
            if name.startswith('.') or name.endswith('.simdashboard-complete.json') or name.endswith('.partial') or name.endswith('.tmp'):
                continue
            path = fs.join(current, name)
            if fs.is_link(path):
                continue
            relative_leaf = PurePosixPath(path).relative_to(leaf).as_posix()
            kind = _file_kind(PurePosixPath(relative_leaf))
            status = "READY" if kind is not None else "IGNORED"
            kind = kind or "unknown"
            try:
                checksum, size = _sha256(fs, path)
            except SpdmStorageError as error:
                observed.add(relative_leaf)
                if error.code == "SPDM_FILE_BUSY":
                    _upsert_file(conn, binding["load_case_id"], relative_leaf, name, kind, 0, "", "PENDING", error.code)
                continue
            observed.add(relative_leaf)
            _upsert_file(conn, binding["load_case_id"], relative_leaf, name, kind, size, checksum, status)
    report_directory = _physical_relative(binding, "reports")
    if fs.is_dir(report_directory) and not fs.is_link(report_directory):
        for path in fs.rglob(report_directory):
            name = _name(path)
            if not fs.is_file(path) or fs.is_link(path) or name.startswith('.'):
                continue
            if PurePosixPath(name).suffix.lower() not in _FOLDER_KINDS["reports"]:
                continue
            relative_leaf = "reports/" + PurePosixPath(path).relative_to(report_directory).as_posix()
            try:
                checksum, size = _sha256(fs, path)
            except SpdmStorageError as error:
                observed.add(relative_leaf)
                if error.code == "SPDM_FILE_BUSY":
                    _upsert_file(conn, binding["load_case_id"], relative_leaf, name, "reports", 0, "", "PENDING", error.code)
                continue
            observed.add(relative_leaf)
            _upsert_file(conn, binding["load_case_id"], relative_leaf, name, "reports", size, checksum, "READY")
    project_name, work_request_name = _binding_parent(binding)
    shared = (
        ("project-inputs", fs.join(project_name, "INPUT")),
        ("request-inputs", fs.join(project_name, work_request_name, "SimCAD")),
        ("request-test", fs.join(project_name, work_request_name, "TEST")),
    )
    for prefix, directory in shared:
        if not fs.is_dir(directory) or fs.is_link(directory):
            continue
        for path in fs.rglob(directory):
            name = _name(path)
            if not fs.is_file(path) or fs.is_link(path) or name.startswith('.'):
                continue
            relative_leaf = f"{prefix}/" + PurePosixPath(path).relative_to(directory).as_posix()
            kind = _extension_kind(Path(name)) or "unknown"
            try:
                checksum, size = _sha256(fs, path)
            except SpdmStorageError as error:
                observed.add(relative_leaf)
                if error.code == "SPDM_FILE_BUSY":
                    _upsert_file(conn, binding["load_case_id"], relative_leaf, name, kind, 0, "", "PENDING", error.code)
                continue
            observed.add(relative_leaf)
            _upsert_file(conn, binding["load_case_id"], relative_leaf, name, kind, size, checksum, "READY" if kind != "unknown" else "IGNORED")
    for indexed in rows(conn.execute("SELECT id, relative_path FROM spdm_storage_files WHERE load_case_id=?", [binding["load_case_id"]])):
        if str(indexed["relative_path"]) not in observed:
            conn.execute("UPDATE spdm_storage_files SET status='MISSING', updated_at=? WHERE id=?", [utc_now(), indexed["id"]])
    return list_files(conn, binding["load_case_id"])


def _upsert_file(conn: ConnectionLike, load_case_id: str, relative_path: str, name: str, kind: str, size: int, checksum: str, status: str, message: str | None = None) -> None:
    existing = conn.execute("SELECT id, checksum, status FROM spdm_storage_files WHERE load_case_id=? AND relative_path=?", [load_case_id, relative_path]).fetchone()
    now = utc_now()
    if existing:
        conn.execute("UPDATE spdm_storage_files SET name=?, kind=?, size_bytes=?, checksum=?, status=?, message=?, updated_at=? WHERE id=?", [name, kind, size, checksum, status, message, now, existing[0]])
        return
    conn.execute("INSERT INTO spdm_storage_files(id, load_case_id, relative_path, name, kind, size_bytes, checksum, status, run_id, message, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)", [_stable_id("file", f"{load_case_id}/{relative_path}"), load_case_id, relative_path, name, kind, size, checksum, status, message, now, now])


def list_files(conn: ConnectionLike, load_case_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in rows(conn.execute("SELECT id, name, relative_path, kind, size_bytes AS size, status, run_id, message FROM spdm_storage_files WHERE load_case_id=? ORDER BY relative_path", [load_case_id]))]


def safe_file_path(root: Path, binding: dict[str, str], relative_path: str) -> str:
    """Root-relative path of one indexed source file (checked: no reparse ancestor, regular file)."""
    fs = LocalFsProvider(root)
    relative = _normalise_relative(relative_path)
    parts = PurePosixPath(relative).parts
    if parts[0] == "reports":
        leaf = _physical_relative(binding, "reports")
        target = fs.join(leaf, *parts[1:])
    elif parts[0] in {"project-inputs", "request-inputs", "request-test"}:
        project_name, work_request_name = _binding_parent(binding)
        bases = {
            "project-inputs": fs.join(project_name, "INPUT"),
            "request-inputs": fs.join(project_name, work_request_name, "SimCAD"),
            "request-test": fs.join(project_name, work_request_name, "TEST"),
        }
        leaf = bases[parts[0]]
        target = fs.join(leaf, *parts[1:])
    else:
        leaf = "/".join(PurePosixPath(binding["relative_path"]).parts)
        target = fs.join(leaf, *parts)
    fs.assert_safe(target)
    if not fs.is_file(target):
        raise SpdmStorageError("SPDM_FILE_MISSING", "원본 파일을 찾을 수 없습니다.")
    return target


def write_upload(root: Path, binding: dict[str, str], kind: Literal["results", "solver", "media", "inputs", "reports"], filename: str, chunks: Iterable[bytes]) -> tuple[str, str, int, bool]:
    if kind not in _FOLDER_KINDS:
        raise SpdmStorageError("SPDM_FILE_KIND_INVALID", "지원하지 않는 SPDM 파일 종류입니다.")
    if not filename or Path(filename).name != filename or not _valid_windows_name(filename):
        raise SpdmStorageError("SPDM_FILENAME_INVALID", "파일 이름이 Windows 파일 규칙에 맞지 않습니다.")
    if Path(filename).suffix.lower() not in _FOLDER_KINDS[kind]:
        raise SpdmStorageError("SPDM_EXTENSION_INVALID", "선택한 종류에 허용되지 않는 확장자입니다.")
    fs = LocalFsProvider(root)
    with _storage_lock:
        parent_relative = _report_directory_relative(binding) if kind == "reports" else f"{binding['relative_path']}/{kind}"
        parent = _ensure_directory(root, parent_relative)
        staged = fs.join(parent, f".simdashboard-upload-{os.urandom(12).hex()}.tmp")
        digest = hashlib.sha256(); size = 0

        def checked_chunks() -> Iterable[bytes]:
            nonlocal size
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise SpdmStorageError("SPDM_UPLOAD_INVALID", "업로드 내용이 올바르지 않습니다.")
                size += len(chunk)
                if size > 512 * 1024 * 1024:
                    raise SpdmStorageError("SPDM_FILE_TOO_LARGE", "원본 파일은 512 MiB 이하여야 합니다.")
                digest.update(chunk)
                yield chunk

        try:
            fs.create_exclusive(staged, checked_chunks(), zone=LEGACY)
            checksum = digest.hexdigest()
            final_name = filename
            destination = fs.join(parent, final_name)
            collision = fs.case_collision(parent, final_name)
            if collision is not None:
                existing_checksum, _ = _sha256(fs, collision)
                if existing_checksum == checksum:
                    if kind == "results":
                        _ensure_ready_marker(fs, collision, checksum)
                    fs.remove(staged, zone=LEGACY, missing_ok=True)
                    return f"{kind}/{_name(collision)}", checksum, size, True
                final_name = f"{Path(filename).stem}--{checksum[:12]}{Path(filename).suffix}"
                destination = fs.join(parent, final_name)
                versioned = fs.case_collision(parent, final_name)
                if versioned is not None:
                    versioned_checksum, _ = _sha256(fs, versioned)
                    if versioned_checksum != checksum:
                        raise SpdmStorageError("SPDM_FILE_CONFLICT", "같은 이름의 다른 원본 파일이 이미 있습니다.")
                    fs.remove(staged, zone=LEGACY, missing_ok=True)
                    return f"{kind}/{_name(versioned)}", checksum, size, True
            fs.move_no_overwrite(staged, destination, zone=LEGACY)
            fs.remove(staged, zone=LEGACY, missing_ok=True)
            if kind == "results":
                _ensure_ready_marker(fs, destination, checksum)
            return f"{kind}/{final_name}", checksum, size, False
        except SpdmStorageError:
            fs.remove(staged, zone=LEGACY, missing_ok=True); raise
        except OSError as exc:
            fs.remove(staged, zone=LEGACY, missing_ok=True)
            raise SpdmStorageError("SPDM_FILE_PUBLISH_FAILED", "원본 파일을 SPDM 저장 폴더에 게시하지 못했습니다.") from exc


def ingest_ready_results(
    conn: ConnectionLike,
    root: Path,
    binding: dict[str, str],
    *,
    principal_user_id: str,
    actor_name: str,
    authorize: Any,
    audit: Any,
) -> list[dict[str, Any]]:
    """Import only marker-complete supported result files through the UoW.

    A filesystem publication is intentionally retained on a database failure.
    The file index records FAILED, and a later refresh retries the immutable
    bytes with the same stable source identity.
    """
    from ..adapters.persistence.result_ingestion import SQLResultIngestionQuery, bound_result_ingestion_transaction
    from ..application.results.ingestion import ManualResultImportInput, ResultIngestionTarget, ResultImportPreparationError, run_manual_import
    from ..application.results.queries import get_manual_result_import_context
    from ..application.results.commands import utc_identifier

    context = get_manual_result_import_context(SQLResultIngestionQuery(conn), binding["load_case_id"])
    if context is None:
        raise SpdmStorageError("SPDM_TARGET_INVALID", "연결된 하중 경우의 결과 적재 문맥이 올바르지 않습니다.")
    outcomes: list[dict[str, Any]] = []
    for item in list_files(conn, binding["load_case_id"]):
        if item["kind"] != "results" or item["status"] not in {"READY", "FAILED"}:
            continue
        try:
            path = safe_file_path(root, binding, str(item["relative_path"]))
            payload, checksum = LocalFsProvider(root).read_stable_digest(path, max_bytes=5_000_000)
            content = payload.decode("utf-8")
            stable_name = f"spdm-{hashlib.sha256(str(item['relative_path']).encode('utf-8')).hexdigest()[:12]}-{Path(str(item['name'])).name}"
            execution = run_manual_import(
                ManualResultImportInput(
                    target=ResultIngestionTarget(binding["project_id"], binding["request_id"], binding["load_case_id"]),
                    filename=stable_name, content=content, author="SPDM storage",
                    principal_user_id=principal_user_id, actor_name=actor_name, source_run_id=None,
                    conflict_policy="SKIP", chassis_threshold=context.chassis_threshold,
                    open_cell_threshold=context.open_cell_threshold, catalog=context.catalog, validate_only=False,
                ),
                transaction=lambda: bound_result_ingestion_transaction(conn, utc_identifier, authorize=lambda command, transaction_connection: authorize(command, transaction_connection)),
                audit=audit,
                now=utc_now,
            )
            assert execution.outcome is not None
            outcome = dict(execution.outcome)
            status = "IMPORTED" if outcome["status"] == "IMPORTED" else "SKIPPED"
            conn.execute("UPDATE spdm_storage_files SET status=?, run_id=?, message=NULL, updated_at=? WHERE id=?", [status, outcome["analysis_run_id"], utc_now(), item["id"]])
            summary = dict(execution.parsed.get("summary") or {})
            outcomes.append({
                "file_id": item["id"], "relative_path": item["relative_path"], "status": status,
                "run_id": outcome["analysis_run_id"], "run_no": outcome["run_no"], "filename": item["name"],
                "summary": summary, "scalar_count": summary.get("scalar_count"), "node_count": summary.get("node_count"),
                "element_count": summary.get("element_count"), "frame_count": summary.get("frame_count"),
                "final_time": summary.get("final_time"), "time_series_count": summary.get("time_series_count"),
                "open_cell_count": summary.get("open_cell_count"), "chassis_rear_count": summary.get("chassis_rear_count"),
                "fail_count": summary.get("fail_count"), "overall_verdict": summary.get("overall_verdict"),
                "source_format": execution.parsed.get("source_format"), "results": execution.parsed.get("scalars", []),
                "warnings": execution.parsed.get("warnings", []), "operation": outcome["operation"],
                "reason_code": outcome["reason_code"], "existing_run_id": outcome.get("existing_analysis_run_id"),
                "replaced_run_id": outcome.get("replaced_analysis_run_id"), "source_revision": outcome.get("source_revision"),
            })
        except HTTPException:
            raise
        except ResultImportPreparationError as exc:
            message = str(exc).strip()
            if not message or len(message) > 500 or re.search(r"(?:[A-Za-z]:[\\/]|^/|\\\\)", message):
                message = "결과 파일 형식을 확인할 수 없습니다."
            conn.execute("UPDATE spdm_storage_files SET status='FAILED', message=?, updated_at=? WHERE id=?", [message, utc_now(), item["id"]])
            outcomes.append({"file_id": item["id"], "relative_path": item["relative_path"], "status": "FAILED", "message": message})
        except Exception as exc:
            message = exc.code if isinstance(exc, SpdmStorageError) else "SPDM_RESULT_IMPORT_FAILED"
            conn.execute("UPDATE spdm_storage_files SET status='FAILED', message=?, updated_at=? WHERE id=?", [message, utc_now(), item["id"]])
            outcomes.append({"file_id": item["id"], "relative_path": item["relative_path"], "status": "FAILED", "message": message})
    return outcomes


def _ensure_ready_marker(fs: LocalFsProvider, relative: str, checksum: str) -> None:
    """Publish the completion marker last, with an injectable recovery seam."""
    marker = relative + ".simdashboard-complete.json"
    if fs.exists(marker):
        if _complete_sidecar(fs, relative, checksum):
            return
        raise SpdmStorageError("SPDM_RESULT_MARKER_CONFLICT", "결과 완료 marker가 기존 파일과 일치하지 않습니다.")
    payload = json.dumps({"state": "READY", "sha256": checksum}, sort_keys=True).encode("utf-8")
    staged = fs.join(str(PurePosixPath(marker).parent), f".simdashboard-marker-{os.urandom(12).hex()}.tmp")
    try:
        fs.create_exclusive(staged, payload, zone=LEGACY)
        fs.move_no_overwrite(staged, marker, zone=LEGACY)
    except FileExistsError:
        if not _complete_sidecar(fs, relative, checksum):
            raise SpdmStorageError("SPDM_RESULT_MARKER_CONFLICT", "결과 완료 marker가 기존 파일과 일치하지 않습니다.")
    except OSError as exc:
        raise SpdmStorageError("SPDM_RESULT_MARKER_PUBLISH_FAILED", "결과 완료 marker를 게시하지 못했습니다.") from exc
    finally:
        fs.remove(staged, zone=LEGACY, missing_ok=True)
