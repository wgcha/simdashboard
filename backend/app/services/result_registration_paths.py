"""Staged, reviewed publication of result files for an already-bound SPDM request."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from ..database_connection import ConnectionLike, rows
from ..services import dashboard_capture, folder_discovery_environment, spdm_storage
from . import usage_source_review
from .storage import get_storage_provider
from .storage.local import LocalFsProvider
from .storage.provider import LEGACY

ENVIRONMENTS = {"USAGE", "DISTRIBUTION"}
EVALUATIONS = ("Settle", "Wobble", "Horizontal_Force_Angle", "Slope_Angle", "Slope_Angle_360")
RESULT_EXTENSIONS = {".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm"}
MAX_FILE_BYTES = dashboard_capture.MAX_ASSET_BYTES
MAX_TOTAL_BYTES = dashboard_capture.MAX_TOTAL_BYTES
MAX_FILES = 500
MAX_CHILDREN = 5000
MAX_TREE_DEPTH = 10
WRITE_LOCK = threading.RLock()


class ResultRegistrationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _stable(prefix: str, root_key: str, path: str, role: str) -> str:
    return f"{prefix}-" + hashlib.sha256(f"{root_key}:{path.casefold()}:{role}".encode()).hexdigest()[:24]


def _env(environment: str) -> str:
    normalized = str(environment).upper()
    if normalized not in ENVIRONMENTS:
        raise ResultRegistrationError("RESULT_ENVIRONMENT_INVALID", "사용환경 또는 유통환경을 선택하세요.")
    return normalized


def _relative(value: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ResultRegistrationError("RESULT_PATH_INVALID", "폴더 경로가 올바르지 않습니다.")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or len(path.parts) > 32 or any(
        part in {"", ".", ".."} or not spdm_storage._valid_windows_name(part) for part in path.parts
    ):
        raise ResultRegistrationError("RESULT_PATH_INVALID", "폴더 경로가 올바르지 않습니다.")
    return path.as_posix()


def _is_final_branch(request_path: str, relative_path: str) -> bool:
    request_parts = PurePosixPath(request_path).parts
    path_parts = PurePosixPath(relative_path).parts
    if len(path_parts) <= len(request_parts) or tuple(
        part.casefold() for part in path_parts[:len(request_parts)]
    ) != tuple(part.casefold() for part in request_parts):
        return False
    return path_parts[len(request_parts)].casefold() == "final"


def _scope(conn: ConnectionLike, project_id: str, request_id: str, environment: str) -> dict[str, Any]:
    environment = _env(environment)
    row = conn.execute(
        "SELECT p.name, r.title FROM projects p JOIN analysis_requests r ON r.project_id=p.id WHERE p.id=? AND r.id=?",
        [project_id, request_id],
    ).fetchone()
    if not row:
        raise ResultRegistrationError("RESULT_CONTEXT_INVALID", "기존 프로젝트와 의뢰의 연결을 확인할 수 없습니다.")
    root_key = folder_discovery_environment.root_identity(storage_context(conn)[0])
    registered = rows(conn.execute(
        "SELECT r.id AS registration_id,g.relative_path,g.role_kind,g.target_id FROM folder_environment_registry g "
        "JOIN folder_environment_registrations r ON r.id=g.registration_id "
        "WHERE g.root_key=? AND r.project_id=? AND r.request_id=? AND r.environment=? "
        "AND g.role_kind IN ('PROJECT','REQUEST')",
        [root_key, project_id, request_id, environment],
    ))
    registrations: dict[str, list[dict[str, Any]]] = {}
    for item in registered:
        registrations.setdefault(str(item["registration_id"]), []).append(item)
    exact_pairs: set[tuple[str, str]] = set()
    all_pairs: set[tuple[str, str]] = set()
    for entries in registrations.values():
        project_paths = {str(item["relative_path"]) for item in entries if item["role_kind"] == "PROJECT"}
        request_rows = [item for item in entries if item["role_kind"] == "REQUEST"]
        for project_path in project_paths:
            for request_row in request_rows:
                pair = (project_path, str(request_row["relative_path"]))
                all_pairs.add(pair)
                if str(request_row.get("target_id") or "") == request_id:
                    exact_pairs.add(pair)
    selected_pairs = exact_pairs if exact_pairs else all_pairs
    if len(selected_pairs) == 1:
        project_folder, request_folder = next(iter(selected_pairs))
        bindings = [{"project_folder": project_folder, "request_folder": request_folder}]
    elif selected_pairs:
        raise ResultRegistrationError("SPDM_REQUEST_BINDING_AMBIGUOUS", "확인된 SPDM 프로젝트·의뢰 폴더 연결이 여러 개입니다.")
    else:
        bindings = rows(conn.execute(
        "SELECT p.project_folder, q.request_folder FROM spdm_storage_project_parents p "
        "JOIN spdm_storage_request_parents q ON q.project_folder=p.project_folder AND q.project_id=p.project_id "
        "WHERE p.project_id=? AND q.project_id=? AND q.request_id=?",
        [project_id, project_id, request_id],
        ))
    # A legacy load-case leaf alone cannot safely identify Project/WR for a
    # nested folder schema. Require an explicit parent binding or a confirmed
    # owner-scoped PROJECT + REQUEST registry instead of guessing first segments.
    if len(bindings) != 1:
        raise ResultRegistrationError("SPDM_REQUEST_BINDING_REQUIRED", "SPDM 프로젝트·의뢰 폴더 연결을 확인할 수 없습니다. 관리자에게 연결을 요청하세요.")
    project_folder = str(bindings[0]["project_folder"])
    request_folder = str(bindings[0]["request_folder"])
    project_parts = PurePosixPath(project_folder).parts
    request_parts = PurePosixPath(request_folder).parts
    if (not project_parts or len(request_parts) <= len(project_parts) or
            tuple(part.casefold() for part in request_parts[:len(project_parts)]) !=
            tuple(part.casefold() for part in project_parts)):
        raise ResultRegistrationError("SPDM_REQUEST_BINDING_INVALID", "SPDM 의뢰 상위 폴더 연결이 올바르지 않습니다.")
    return {"project_id": project_id, "project_name": str(row[0]), "request_id": request_id,
            "request_name": str(row[1]), "environment": environment,
            "spdm_project_folder": project_folder, "spdm_request_folder": request_folder,
            "request_relative_path": request_folder}


def storage_context(conn: ConnectionLike) -> tuple[Path, str, str]:
    """Configured provider root with its ``storage_root_id`` and ``root_key`` (``SPDM_ROOT_UNSET`` when absent)."""
    provider = get_storage_provider(conn, on_unset=lambda: ResultRegistrationError("SPDM_ROOT_UNSET", "SPDM 저장소 루트가 설정되지 않았습니다."))
    root = provider.root
    root_id = dashboard_capture._root_id(root)
    root_key = folder_discovery_environment.root_identity(root)
    return root, root_id, root_key


def _safe_existing(root: Path, relative: str, *, allow_missing_leaf: bool = False) -> str:
    """Root-relative path whose existing ancestors are plain folders (no reparse point)."""
    fs = LocalFsProvider(root)
    target = ""
    parts = PurePosixPath(relative).parts if relative else ()
    missing = False
    try:
        fs.assert_safe("")
        for index, part in enumerate(parts):
            target = fs.join(target, part)
            if missing:
                continue
            if fs.stat(target, follow_links=False, missing_ok=True) is None:
                if allow_missing_leaf:
                    missing = True
                    continue
                raise ResultRegistrationError("SPDM_FOLDER_MISSING", "선택한 폴더를 찾을 수 없습니다.")
            fs.assert_safe(target)
        return target
    except ResultRegistrationError:
        raise
    except spdm_storage.SpdmStorageError as exc:
        raise ResultRegistrationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise ResultRegistrationError("SPDM_FOLDER_UNAVAILABLE", "선택한 폴더에 접근할 수 없습니다.") from exc


def _root_casefold(value: str) -> str:
    return "/".join(part.casefold() for part in PurePosixPath(value).parts)


def _lock_path(conn: ConnectionLike, root_key: str, relative_path: str) -> None:
    if getattr(conn, "backend", None) == "postgresql":
        # Lock every ancestor in stable root-to-leaf order. Two workers claiming
        # overlapping Case/result trees therefore share at least one xact lock.
        parts = PurePosixPath(_root_casefold(relative_path)).parts
        for index in range(1, len(parts) + 1):
            key = "/".join(parts[:index])
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", [f"result-registration:{root_key}:{key}"])


def _owner_conflict(conn: ConnectionLike, root_id: str, root_key: str, relative_path: str,
                    project_id: str, request_id: str, environment: str, *,
                    include_descendants: bool = True) -> None:
    folded = _root_casefold(relative_path)
    def overlaps(other: str) -> bool:
        other_folded = _root_casefold(other)
        if include_descendants:
            return (other_folded == folded or other_folded.startswith(folded + "/") or
                    folded.startswith(other_folded + "/"))
        return other_folded == folded or folded.startswith(other_folded + "/")

    def foreign(item: dict[str, Any]) -> bool:
        return (str(item.get("project_id") or ""), str(item.get("request_id") or "")) != (project_id, request_id)

    def foreign_registration(item: dict[str, Any]) -> bool:
        owner_project = str(item.get("project_id") or "")
        owner_request = str(item.get("request_id") or "")
        role = str(item.get("role_kind") or "")
        # PROJECT is a shared ancestor for every request and environment in
        # that project. REQUEST is shared across environment profiles for the
        # same request. Semantic Case descendants remain environment-specific.
        if role == "PROJECT":
            return owner_project != project_id
        if role == "REQUEST":
            return (owner_project, owner_request) != (project_id, request_id)
        return (owner_project, owner_request) != (project_id, request_id) or str(item.get("environment") or "") != environment

    def foreign_result_context(item: dict[str, Any]) -> bool:
        return foreign(item) or str(item.get("environment") or "") != environment

    cases = rows(conn.execute("SELECT project_id,request_id,environment,relative_path FROM dashboard_cases WHERE storage_root_id=?", [root_id]))
    if any(foreign_result_context(item) and overlaps(str(item["relative_path"])) for item in cases):
        raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 업무의 결과에 연결되어 있습니다.")
    path_rows = rows(conn.execute("SELECT project_id,request_id,environment,relative_path FROM result_registration_paths WHERE root_key=?", [root_key]))
    if any(foreign_result_context(item) and overlaps(str(item["relative_path"])) for item in path_rows):
        raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 업무의 결과 경로로 예약되어 있습니다.")
    saved_locations = rows(conn.execute(
        "SELECT project_id,request_id,environment,relative_path FROM result_registration_location_links WHERE root_key=?",
        [root_key],
    ))
    if any(foreign_result_context(item) and overlaps(str(item["relative_path"])) for item in saved_locations):
        raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 업무의 저장 위치로 연결되어 있습니다.")
    registered = rows(conn.execute(
        "SELECT r.project_id,r.request_id,r.environment,g.relative_path,g.role_kind FROM folder_environment_registry g "
        "JOIN folder_environment_registrations r ON r.id=g.registration_id WHERE g.root_key=?", [root_key]))
    if any(foreign_registration(item) and overlaps(str(item["relative_path"])) for item in registered):
        raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 업무의 확인된 환경 경로와 겹칩니다.")
    bindings = rows(conn.execute("SELECT project_id,request_id,relative_path FROM spdm_storage_bindings"))
    if any((str(item["project_id"]), str(item["request_id"])) != (project_id, request_id) and
           overlaps(str(item["relative_path"])) for item in bindings):
        raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 의뢰의 기존 결과 경로와 겹칩니다.")
    semantic = rows(conn.execute("SELECT project_id,request_id,relative_path FROM semantic_folder_bindings"))
    if any(str(item["project_id"]) != project_id and overlaps(str(item["relative_path"])) or
           (item.get("request_id") is not None and str(item["request_id"]) != request_id and overlaps(str(item["relative_path"])))
           for item in semantic):
        raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 의뢰의 의미 매핑 경로와 겹칩니다.")
    discovered = rows(conn.execute(
        "SELECT g.relative_path,g.role_kind,COALESCE(p.id,a.project_id,ar.project_id) AS owner_project_id, "
        "COALESCE(a.id,l.request_id) AS owner_request_id "
        "FROM folder_discovery_registry g "
        "LEFT JOIN projects p ON g.role_kind='PROJECT' AND p.id=g.target_id "
        "LEFT JOIN analysis_requests a ON g.role_kind='REQUEST' AND a.id=g.target_id "
        "LEFT JOIN load_cases l ON g.role_kind='LOAD_CASE' AND l.id=g.target_id "
        "LEFT JOIN analysis_requests ar ON ar.id=l.request_id WHERE g.root_key=?", [root_key]))
    for item in discovered:
        if not overlaps(str(item["relative_path"])):
            continue
        owner_project, owner_request = item.get("owner_project_id"), item.get("owner_request_id")
        if owner_project is not None and str(owner_project) != project_id:
            raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 프로젝트의 명시적 탐색 경로와 겹칩니다.")
        if owner_request is not None and (str(owner_project or project_id), str(owner_request)) != (project_id, request_id):
            raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더가 다른 의뢰의 명시적 탐색 경로와 겹칩니다.")


def _has_owner_conflict(conn: ConnectionLike, root_id: str, root_key: str, relative_path: str,
                        project_id: str, request_id: str, environment: str) -> bool:
    try:
        _owner_conflict(conn, root_id, root_key, relative_path, project_id, request_id, environment)
    except ResultRegistrationError as exc:
        if exc.code == "RESULT_PATH_OWNERSHIP_CONFLICT":
            return True
        raise
    return False


def _registry_role(conn: ConnectionLike, root_key: str, path: str, project_id: str, request_id: str, environment: str):
    folded = _root_casefold(path)
    prepared = rows(conn.execute(
        "SELECT role_kind,target_id,raw_name,option_status FROM result_registration_paths "
        "WHERE root_key=? AND path_key=? AND project_id=? AND request_id=? AND environment=?",
        [root_key, folded, project_id, request_id, environment],
    ))
    registrations = rows(conn.execute(
        "SELECT r.project_id,r.request_id,r.environment,g.relative_path,g.role_kind,g.target_id,g.raw_name,g.option_status "
        "FROM folder_environment_registry g JOIN folder_environment_registrations r ON r.id=g.registration_id "
        "WHERE g.root_key=?", [root_key]))
    exact = [item for item in registrations if _root_casefold(str(item["relative_path"])) == folded]
    found = list(prepared)
    for item in exact:
        role = str(item["role_kind"])
        owner_project = str(item.get("project_id") or "")
        owner_request = str(item.get("request_id") or "")
        if role == "PROJECT":
            foreign = owner_project != project_id
        elif role == "REQUEST":
            foreign = (owner_project, owner_request) != (project_id, request_id)
        else:
            foreign = ((owner_project, owner_request) != (project_id, request_id) or
                       str(item.get("environment") or "") != environment)
        if foreign:
            raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "다른 업무의 환경 폴더 역할은 사용할 수 없습니다.")
        found.append(item)
    # The environment registry intentionally omits Evaluation and Scene rows
    # because they are folders, not dashboard entities. Their confirmed roles
    # still live in the registered preview's saved plan, tied to a profile
    # revision. Use that evidence when tracing existing paths.
    schemas = rows(conn.execute(
        "SELECT s.profile_id,s.profile_revision,p.rows_json,fp.revision AS current_revision "
        "FROM folder_environment_registrations r "
        "JOIN folder_environment_previews p ON p.id=r.preview_id "
        "JOIN folder_environment_scans s ON s.id=p.scan_id "
        "JOIN folder_environment_profiles fp ON fp.id=s.profile_id "
        "WHERE s.root_key=? AND r.project_id=? AND r.request_id=? AND r.environment=? "
        "AND s.environment=? AND s.status='COMPLETE' AND p.can_apply=true "
        "AND r.status IN ('REGISTERED','COMPLETED','CAPTURING','FAILED')",
        [root_key, project_id, request_id, environment, environment],
    ))
    for schema in schemas:
        if int(schema["profile_revision"]) != int(schema["current_revision"]):
            continue
        preview = folder_discovery_environment.preview_data(schema["rows_json"])
        for item in preview.get("rows", []):
            if _root_casefold(str(item.get("relative_path", ""))) != folded or item.get("role_kind") not in {"EVALUATION", "SCENE"}:
                continue
            found.append({"role_kind": item["role_kind"], "target_id": item.get("target_id"),
                          "raw_name": item.get("name"), "option_status": item.get("option_status")})
    kinds = {(str(item["role_kind"]), str(item.get("target_id") or ""), str(item.get("raw_name") or ""), item.get("option_status")) for item in found}
    if len(kinds) > 1:
        semantic = {(kind[0], kind[2], kind[3]) for kind in kinds}
        if len(semantic) != 1:
            raise ResultRegistrationError("RESULT_PATH_ROLE_CONFLICT", "같은 폴더에 서로 다른 역할이 연결되어 있습니다.")
        role, raw_name, option_status = next(iter(semantic))
        if role in {"PROJECT", "REQUEST"}:
            return (role, _stable(f"environment-{role.casefold()}", root_key, path, role), raw_name, option_status)
        target_ids = {kind[1] for kind in kinds if kind[1]}
        if len(target_ids) > 1:
            raise ResultRegistrationError("RESULT_PATH_ROLE_CONFLICT", "같은 폴더에 서로 다른 역할 식별자가 연결되어 있습니다.")
        target_id = next(iter(target_ids)) if target_ids else _stable(f"environment-{role.casefold()}", root_key, path, role)
        return (role, target_id, raw_name, option_status)
    return next(iter(kinds)) if kinds else None


def _validate_registered_schema_parent(conn: ConnectionLike, root: Path, root_key: str,
                                        scope: dict[str, Any], parent_path: str) -> None:
    """Require a live, registered schema snapshot for a newly created Case's parent."""
    candidates = rows(conn.execute(
        "SELECT s.id,s.relative_path,s.profile_id,s.profile_revision,s.tree_json,p.rows_json,"
        "fp.revision AS current_revision,r.created_at AS registration_created_at "
        "FROM folder_environment_registrations r "
        "JOIN folder_environment_previews p ON p.id=r.preview_id "
        "JOIN folder_environment_scans s ON s.id=p.scan_id "
        "JOIN folder_environment_profiles fp ON fp.id=s.profile_id "
        "WHERE s.root_key=? AND r.project_id=? AND r.request_id=? AND r.environment=? "
        "AND s.environment=? AND (s.project_id IS NULL OR s.project_id=?) "
        "AND (s.request_id IS NULL OR s.request_id=?) AND s.status='COMPLETE' "
        "AND p.can_apply=true AND r.status IN ('REGISTERED','COMPLETED','CAPTURING','FAILED')",
        [root_key, scope["project_id"], scope["request_id"], scope["environment"],
         scope["environment"], scope["project_id"], scope["request_id"]],
    ))
    parent_key = _root_casefold(parent_path)
    stale_match = False
    legacy_preview_match = False
    conflicting_match = False
    legacy_uncertain_candidates: list[dict[str, Any]] = []
    valid: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any]]] = []
    for candidate in candidates:
        nodes = _decode(candidate["tree_json"])
        if not isinstance(nodes, list):
            continue
        by_path = {_root_casefold(str(node.get("relative_path", ""))): node for node in nodes}
        parent_node = by_path.get(parent_key)
        if parent_node is None:
            continue
        if int(candidate["profile_revision"]) != int(candidate["current_revision"]):
            stale_match = True
            continue
        scan_root = str(candidate["relative_path"])
        scan_parts = PurePosixPath(scan_root).parts if scan_root else ()
        parent_parts = PurePosixPath(parent_path).parts
        if (len(parent_parts) < len(scan_parts) or
                tuple(part.casefold() for part in parent_parts[:len(scan_parts)]) !=
                tuple(part.casefold() for part in scan_parts)):
            continue
        chain_paths = [PurePosixPath(*parent_parts[:index]).as_posix()
                       for index in range(len(scan_parts), len(parent_parts) + 1)]
        if not chain_paths:
            chain_paths = [scan_root]
        previous = scan_root
        preview = folder_discovery_environment.preview_data(candidate["rows_json"])
        preview_roles = {_root_casefold(str(item.get("relative_path", ""))): item
                         for item in preview.get("rows", [])}
        preview_states = {_root_casefold(str(item.get("relative_path", ""))): item
                          for item in preview.get("node_states", [])}
        if not preview_states:
            # Older registrations stored only semantic rows. They cannot
            # prove that a structural container was included rather than
            # excluded. Defer that uncertainty so a later explicit proof from
            # the same profile can supersede it, while retaining it otherwise.
            if any(_root_casefold(chain_path) not in preview_roles for chain_path in chain_paths):
                legacy_preview_match = True
                legacy_uncertain_candidates.append(candidate)
                continue
            preview_states = {
                _root_casefold(chain_path): {
                    "relative_path": chain_path,
                    "parent_path": by_path[_root_casefold(chain_path)].get("parent_path"),
                    "role_kind": preview_roles[_root_casefold(chain_path)].get("role_kind"),
                    "status": preview_roles[_root_casefold(chain_path)].get("status") or "CONFIRMED",
                }
                for chain_path in chain_paths
            }
        chain_nodes: list[dict[str, Any]] = []
        chain_valid = True
        for index, chain_path in enumerate(chain_paths):
            node = by_path.get(_root_casefold(chain_path))
            if node is None or (index > 0 and _root_casefold(str(node.get("parent_path") or "")) != _root_casefold(previous)):
                conflicting_match = True
                chain_valid = False
                break
            confirmed_state = preview_states.get(_root_casefold(chain_path))
            if (not confirmed_state or confirmed_state.get("status") not in {"CONFIRMED", "CONTAINER"} or
                    (index > 0 and _root_casefold(str(confirmed_state.get("parent_path") or "")) != _root_casefold(previous))):
                conflicting_match = True
                chain_valid = False
                break
            saved_role = node.get("role_kind")
            confirmed_row = preview_roles.get(_root_casefold(chain_path))
            # The registered preview is authoritative: it may resolve an
            # ambiguous scan suggestion or deliberately assign a different
            # role. Raw scan labels only identify unresolved/excluded paths
            # when the confirmed preview contains no row for them.
            # Working is a structural request branch, so it is confirmed by
            # node_states even though it has no semantic result row. Excluded
            # states still fail above and saved EXCLUDE roles remain blocked.
            structural_branch = saved_role == "WORKING" and confirmed_state.get("status") in {"CONFIRMED", "CONTAINER"}
            if ((node.get("status") == "UNRESOLVED" or (saved_role and not structural_branch)) and not confirmed_row) or saved_role == "EXCLUDE":
                conflicting_match = True
                chain_valid = False
                break
            if confirmed_row and confirmed_row.get("status") not in {None, "CONFIRMED", "LINKED"}:
                conflicting_match = True
                chain_valid = False
                break
            chain_nodes.append(node)
            previous = chain_path
        if not chain_valid:
            continue
        # Confirm the saved chain still names the same physical directories.
        # Changes elsewhere in the scanned tree do not invalidate this parent.
        try:
            fs = LocalFsProvider(root)
            current = _safe_existing(root, scan_root) if scan_root else ""
            for index, node in enumerate(chain_nodes):
                expected_path = chain_paths[index]
                if str(node.get("relative_path", "")) != expected_path or str(node.get("name", "")) != PurePosixPath(expected_path).name:
                    chain_valid = False
                    break
                if index > 0:
                    expected_name = str(node["name"])
                    matches = [entry.name for entry in fs.list(current)
                               if entry.name.casefold() == expected_name.casefold() and entry.kind == "dir"]
                    if len(matches) != 1 or matches[0] != expected_name:
                        chain_valid = False
                        break
                    current = fs.join(current, matches[0])
                    fs.assert_safe(current)
                identity = str(node.get("identity") or "")
                if identity:
                    stat = fs.stat(current, follow_links=True, missing_ok=False)
                    if identity != stat.item_id:
                        chain_valid = False
                        break
        except (OSError, spdm_storage.SpdmStorageError, ResultRegistrationError):
            chain_valid = False
        if not chain_valid:
            stale_match = True
            continue
        role_rows = {(_root_casefold(str(item.get("relative_path", ""))), str(item.get("role_kind")),
                      str(item.get("name", ""))) for item in preview.get("rows", [])}
        signature = (str(candidate["profile_id"]), int(candidate["profile_revision"]),
                     tuple((str(node.get("relative_path")), str(preview_states[_root_casefold(str(node.get("relative_path", "")))].get("role_kind") or "CONTAINER"),
                            str(preview_states[_root_casefold(str(node.get("relative_path", "")))].get("status") or ""),
                            str(node.get("name") or "")) for node in chain_nodes),
                     tuple(sorted(item for item in role_rows if item[0] in {_root_casefold(str(node.get("relative_path", ""))) for node in chain_nodes})))
        valid.append((signature, candidate, parent_node))
    if not valid:
        if stale_match:
            raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_STALE", "확인된 폴더 구조가 변경되었거나 저장 규칙이 오래되었습니다. 환경 폴더를 다시 확인하세요.")
        if legacy_preview_match:
            raise ResultRegistrationError(
                "RESULT_FOLDER_SCHEMA_REFRESH_REQUIRED",
                "기존 등록 구조에는 컨테이너 확인 정보가 없습니다. 환경 폴더를 다시 조사하고 미리보기에서 확인한 뒤 한 번 더 등록하세요.",
            )
        raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_REQUIRED", "새 Case의 부모 폴더가 확인된 환경 폴더 구조에 없습니다. 저장 위치를 다시 찾아보거나 관리자에게 구조 확인을 요청하세요.")
    if conflicting_match:
        raise ResultRegistrationError(
            "RESULT_FOLDER_SCHEMA_AMBIGUOUS",
            "등록된 환경 구조가 선택한 부모 폴더를 서로 다르게 확인합니다. 폴더 구조를 다시 확인하고 충돌하는 등록을 정리하세요.",
        )
    for legacy_candidate in legacy_uncertain_candidates:
        legacy_created_at = legacy_candidate.get("registration_created_at")
        superseded = any(
            str(candidate["profile_id"]) == str(legacy_candidate["profile_id"])
            and int(candidate["profile_revision"]) == int(legacy_candidate["profile_revision"])
            and legacy_created_at is not None
            and candidate.get("registration_created_at") is not None
            and candidate["registration_created_at"] > legacy_created_at
            and bool(folder_discovery_environment.preview_data(candidate["rows_json"]).get("node_states"))
            for _, candidate, _ in valid
        )
        if not superseded:
            raise ResultRegistrationError(
                "RESULT_FOLDER_SCHEMA_AMBIGUOUS",
                "기존 등록 구조에는 컨테이너 확인 정보가 없고 이를 대체하는 최신 확인도 없습니다. 폴더 구조를 다시 확인하세요.",
            )
    if len({item[0] for item in valid}) != 1:
        raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_AMBIGUOUS", "이 부모 폴더에 적용할 확인된 환경 구조가 여러 개입니다. 관리자에게 구조를 확인해 주세요.")
    if str(valid[0][2].get("relative_path", "")) != parent_path:
        raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_STALE", "선택한 부모 폴더 경로의 대소문자가 확인된 구조와 다릅니다. 다시 찾아 선택하세요.")


def _empty_context() -> dict[str, Any]:
    return {"simulation_case": None, "evaluation": None, "load_case": None,
            "execution_run": None, "run_option": None, "scene": None}


def _role_fallback(environment: str, name: str, parent_role: str | None, depth: int) -> tuple[str | None, str | None]:
    lowered = name.casefold()
    if re.match(r"^(assy|package)[_-]", name, re.I):
        return "SIMULATION_CASE", None
    if lowered == "results" and parent_role in {"EVALUATION", "SCENE"}:
        return "RESULTS", None
    if environment == "USAGE":
        if parent_role == "SIMULATION_CASE" and lowered in {item.casefold() for item in EVALUATIONS}:
            return "EVALUATION", None
    else:
        if parent_role == "SIMULATION_CASE": return "LOAD_CASE", None
        if parent_role == "LOAD_CASE": return "EXECUTION_RUN", None
        if parent_role == "EXECUTION_RUN":
            if re.search(r"(scene|result|contour|animation)", name, re.I): return "SCENE", "ABSENT"
            if lowered in {"individual", "cumulative"}: return "RUN_OPTION", "PRESENT"
        if parent_role == "RUN_OPTION" and re.search(r"(scene|result|contour|animation)", name, re.I):
            return "SCENE", "PRESENT"
    return None, None


def _context_add(context: dict[str, Any], role: str, name: str, path: str,
                 root_key: str, root_id: str, option_status: str | None = None,
                 target_id: str | None = None) -> dict[str, Any]:
    updated = dict(context)
    if role == "SIMULATION_CASE":
        updated["simulation_case"] = {"id": dashboard_capture._case_id(root_id, path), "label": name, "relative_path": path}
    elif role == "EVALUATION":
        updated["evaluation"] = {"label": name, "relative_path": path}
    elif role == "LOAD_CASE":
        updated["load_case"] = {"id": target_id or _stable("environment-load_case", root_key, path, role), "label": name, "relative_path": path}
    elif role == "EXECUTION_RUN":
        updated["execution_run"] = {"id": target_id or _stable("environment-execution_run", root_key, path, role), "label": name, "relative_path": path}
    elif role == "RUN_OPTION":
        updated["run_option"] = {"id": target_id or _stable("environment-option", root_key, path, role), "label": name,
                                 "relative_path": path, "status": option_status or "PRESENT"}
    elif role == "SCENE":
        updated["scene"] = {"id": target_id or _stable("environment-scene", root_key, path, role), "label": name, "relative_path": path}
        if not updated.get("run_option") and updated.get("execution_run"):
            run_id = str(updated["execution_run"]["id"])
            absent_id = "option-" + hashlib.sha256(f"{run_id}:ABSENT:".encode()).hexdigest()[:24]
            updated["run_option"] = {"id": absent_id, "label": None,
                                     "relative_path": updated["execution_run"]["relative_path"], "status": "ABSENT"}
    return updated


def _trace_path(conn: ConnectionLike, root: Path, root_id: str, root_key: str,
                scope: dict[str, Any], relative_path: str, *, require_directory: bool = True) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = _relative(relative_path)
    wr_parts = PurePosixPath(scope["request_relative_path"]).parts
    parts = PurePosixPath(normalized).parts
    if len(parts) < len(wr_parts) or tuple(x.casefold() for x in parts[:len(wr_parts)]) != tuple(x.casefold() for x in wr_parts):
        raise ResultRegistrationError("RESULT_PATH_OUTSIDE_REQUEST", "선택한 경로가 연결된 SPDM 의뢰 폴더 아래에 없습니다.")
    target = _safe_existing(root, normalized)
    if require_directory and not LocalFsProvider(root).is_dir(target):
        raise ResultRegistrationError("RESULT_PATH_NOT_DIRECTORY", "선택한 경로가 폴더가 아닙니다.")
    path_nodes: list[dict[str, Any]] = []
    context = _empty_context()
    parent_role: str | None = "REQUEST"
    prefix_parts = list(wr_parts)
    for name in parts[len(wr_parts):]:
        prefix_parts.append(name)
        path = PurePosixPath(*prefix_parts).as_posix()
        info = _registry_role(conn, root_key, path, scope["project_id"], scope["request_id"], scope["environment"])
        if info:
            role, target_id, registered_name, option_status = info
            raw_name = registered_name or name
        else:
            role, option_status = _role_fallback(scope["environment"], name, parent_role, len(prefix_parts) - len(wr_parts))
            target_id, raw_name = None, name
        if role:
            context = _context_add(context, role, raw_name, path, root_key, root_id, option_status, target_id)
            parent_role = role
            context_key = {"SIMULATION_CASE": "simulation_case", "EVALUATION": "evaluation",
                           "LOAD_CASE": "load_case", "EXECUTION_RUN": "execution_run",
                           "RUN_OPTION": "run_option", "SCENE": "scene"}.get(role, "")
            context_item = context.get(context_key) if context_key else None
            node_target_id = target_id or (context_item.get("id") if isinstance(context_item, dict) else None)
            if not node_target_id:
                node_target_id = _stable(f"environment-{role.casefold()}", root_key, path, role)
            path_nodes.append({"relative_path": path, "role_kind": role, "target_id": node_target_id,
                "raw_name": raw_name, "option_status": option_status})
        elif name.casefold() == "results" and parent_role in {"EVALUATION", "SCENE"}:
            path_nodes.append({"relative_path": path, "role_kind": "RESULTS", "target_id": _stable("environment-results", root_key, path, "RESULTS"),
                               "raw_name": name, "option_status": None})
        else:
            # Containers keep the latest semantic parent for subsequent children.
            path_nodes.append({"relative_path": path, "role_kind": "CONTAINER", "target_id": None, "raw_name": name, "option_status": None})
    return context, path_nodes


def targets(conn: ConnectionLike, environment: str, principal_projects: set[str] | None = None) -> dict[str, Any]:
    environment = _env(environment)
    root, root_id, root_key = storage_context(conn)
    from .folder_schema_resolver import FolderSchemaError, resolve_request_schema
    from .result_registration_locations import effective_assignment, _result_candidates

    records = rows(conn.execute(
        "SELECT p.project_id,pr.name AS project_name,r.request_id,a.title AS request_name "
        "FROM spdm_storage_project_parents p JOIN spdm_storage_request_parents r "
        "ON r.project_folder=p.project_folder AND r.project_id=p.project_id "
        "JOIN projects pr ON pr.id=p.project_id "
        "JOIN analysis_requests a ON a.id=r.request_id AND a.project_id=r.project_id "
        "WHERE p.project_id=r.project_id ORDER BY p.project_folder,r.request_folder"
    ))
    identities = {(str(item["project_id"]), str(item["request_id"])) for item in records}
    identities.update((str(item["project_id"]), str(item["request_id"])) for item in rows(conn.execute(
        "SELECT DISTINCT project_id,request_id FROM folder_environment_registrations "
        "WHERE project_id IS NOT NULL AND request_id IS NOT NULL AND status<>'DELETED'")))
    identities.update((str(item["project_id"]), str(item["request_id"])) for item in rows(conn.execute(
        "SELECT DISTINCT project_id,request_id FROM spdm_storage_bindings")))
    identities.update((str(item["project_id"]), str(item["request_id"])) for item in rows(conn.execute(
        "SELECT DISTINCT project_id,request_id FROM folder_environment_scans "
        "WHERE root_key=? AND environment=? AND project_id IS NOT NULL AND request_id IS NOT NULL",
        [root_key, environment])))
    identities.update((str(item["project_id"]), str(item["request_id"])) for item in rows(conn.execute(
        "SELECT DISTINCT project_id,request_id FROM result_registration_location_links "
        "WHERE root_key=? AND environment=?", [root_key, environment])))
    identities.update((str(item["project_id"]), str(item["request_id"])) for item in rows(conn.execute(
        "SELECT DISTINCT project_id,request_id FROM result_registration_drafts WHERE environment=?",
        [environment])))
    if identities:
        names = rows(conn.execute(
            f"SELECT p.id AS project_id,p.name AS project_name,a.id AS request_id,a.title AS request_name "
            f"FROM projects p JOIN analysis_requests a ON a.project_id=p.id"
        ))
        records = [item for item in names if (str(item["project_id"]), str(item["request_id"])) in identities]
    items: list[dict[str, Any]] = []
    for record in records:
        if principal_projects is not None and str(record["project_id"]) not in principal_projects:
            continue
        project_id, request_id = str(record["project_id"]), str(record["request_id"])
        try:
            schema = resolve_request_schema(conn, root, root_key, project_id, request_id, environment)
        except FolderSchemaError as exc:
            # Keep old or stale links visible in the request picker so their
            # connection rows can still be inspected or removed. They never
            # make a filesystem path selectable without a live schema.
            binding_codes = {"SPDM_REQUEST_BINDING_REQUIRED", "SPDM_REQUEST_BINDING_INVALID",
                             "FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED", "FOLDER_SCHEMA_REQUEST_BINDING_AMBIGUOUS",
                             "FOLDER_SCHEMA_REQUEST_BINDING_INVALID"}
            items.append({
                "project_id": project_id, "project_name": str(record["project_name"]),
                "request_id": request_id, "request_name": str(record["request_name"]),
                "environment": environment, "spdm_project_folder": None, "spdm_request_folder": None,
                "request_relative_path": None, "storage_root_id": root_id, "root_key": root_key,
                "status": "BINDING_REQUIRED" if exc.code in binding_codes else "UNAVAILABLE",
                "reason": {"code": exc.code, "message": str(exc)}, "cases": [],
            })
            continue
        wr = str(schema["request_relative_path"])
        scope = {"project_id": project_id, "request_id": request_id, "environment": environment,
                 "request_relative_path": wr}
        scope["spdm_project_folder"] = PurePosixPath(wr).parent.as_posix()
        scope["spdm_request_folder"] = wr
        try:
            wr_path = _safe_existing(root, wr)
        except ResultRegistrationError as exc:
            items.append({**scope, "storage_root_id": root_id, "root_key": root_key,
                          "status": "MISSING", "reason": {"code": exc.code, "message": str(exc)}, "cases": []})
            continue
        if not LocalFsProvider(root).is_dir(wr_path):
            items.append({**scope, "storage_root_id": root_id, "root_key": root_key,
                          "status": "MISSING", "reason": {"code": "SPDM_FOLDER_UNAVAILABLE",
                          "message": "확정된 의뢰 경로가 폴더가 아닙니다."}, "cases": []})
            continue

        schema_nodes = {_root_casefold(str(node.get("relative_path", ""))): node
                        for node in schema.get("nodes", [])}
        valid_assignments = {key: effective_assignment(schema, key) for key in schema_nodes}
        result_candidates = _result_candidates(conn, root, root_key, project_id, request_id, environment, schema)
        cases: list[dict[str, Any]] = []
        request_parts = PurePosixPath(wr).parts
        for path_key, schema_node in schema_nodes.items():
            assignment = valid_assignments[path_key]
            if (not assignment or assignment.get("role_kind") != "SIMULATION_CASE" or
                    assignment.get("status") not in {"CONFIRMED", "LINKED"}):
                continue
            relative = str(assignment.get("relative_path") or schema_node.get("relative_path") or "")
            if _is_final_branch(wr, relative):
                continue
            parts = PurePosixPath(relative).parts
            if (len(parts) <= len(request_parts) or
                    tuple(part.casefold() for part in parts[:len(request_parts)]) !=
                    tuple(part.casefold() for part in request_parts)):
                continue
            if _has_owner_conflict(conn, root_id, root_key, relative, project_id, request_id, environment):
                continue
            try:
                case_target = _safe_existing(root, relative)
                if not LocalFsProvider(root).is_dir(case_target):
                    continue
            except ResultRegistrationError:
                continue
            context = _empty_context()
            prefix = list(request_parts)
            valid_chain = True
            for name in parts[len(request_parts):]:
                prefix.append(name)
                current = PurePosixPath(*prefix).as_posix()
                current_key = _root_casefold(current)
                current_assignment = valid_assignments.get(current_key)
                if not current_assignment or current_assignment.get("status") not in {"CONFIRMED", "LINKED", "CONTAINER"}:
                    valid_chain = False
                    break
                role = str(current_assignment.get("role_kind") or "")
                if role in {"SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"}:
                    context = _context_add(context, role,
                        str(current_assignment.get("name") or PurePosixPath(current).name), current,
                        root_key, root_id, current_assignment.get("option_status"), current_assignment.get("target_id"))
            if not valid_chain or context.get("simulation_case", {}).get("relative_path", "").casefold() != relative.casefold():
                continue
            case_results = [candidate for candidate in result_candidates
                            if candidate["context"].get("simulation_case", {}).get("relative_path", "").casefold() == relative.casefold()]
            state = "PRESENT" if any(candidate["exists"] for candidate in case_results) else "MISSING"
            cases.append({"relative_path": relative,
                          "name": str(assignment.get("name") or schema_node.get("name") or PurePosixPath(relative).name),
                          "role_kind": "SIMULATION_CASE", "status": "READY", "result_state": state,
                          "context": context})
            if len(cases) >= MAX_CHILDREN:
                break
        items.append({**scope, "storage_root_id": root_id, "root_key": root_key,
                      "status": "READY", "cases": cases})
    return {"storage_root_id": root_id, "environment": environment, "targets": items}


def folders(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
            parent_relative_path: str | None = None) -> dict[str, Any]:
    environment = _env(environment)
    root, root_id, root_key = storage_context(conn)
    # The step-02 explorer is driven only by a current Folder Schema snapshot.
    # The legacy trace remains available for immutable drafts and older APIs,
    # but its name-based fallback must not invent selectable context here.
    from .folder_schema_resolver import FolderSchemaError, resolve_request_schema
    from .result_registration_locations import effective_assignment
    try:
        schema = resolve_request_schema(conn, root, root_key, project_id, request_id, environment)
    except FolderSchemaError as exc:
        raise ResultRegistrationError(getattr(exc, "code", "RESULT_FOLDER_SCHEMA_REQUIRED"), str(exc)) from exc
    if (str(schema.get("project_id")) != project_id or str(schema.get("request_id")) != request_id or
            str(schema.get("environment")) != environment):
        raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_STALE", "선택한 프로젝트·의뢰·환경과 확인된 폴더 구조가 일치하지 않습니다.")
    scope = {"project_id": project_id, "request_id": request_id, "environment": environment,
             "request_relative_path": str(schema["request_relative_path"])}
    parent = scope["request_relative_path"] if parent_relative_path is None else _relative(parent_relative_path)
    parent_target = _safe_existing(root, parent)
    if not LocalFsProvider(root).is_dir(parent_target):
        raise ResultRegistrationError("SPDM_FOLDER_UNAVAILABLE", "선택한 경로가 폴더가 아닙니다.")
    wr_parts = PurePosixPath(scope["request_relative_path"]).parts
    parent_parts = PurePosixPath(parent).parts
    if (len(parent_parts) < len(wr_parts) or
            tuple(part.casefold() for part in parent_parts[:len(wr_parts)]) !=
            tuple(part.casefold() for part in wr_parts)):
        raise ResultRegistrationError("RESULT_PATH_OUTSIDE_REQUEST", "선택한 폴더가 연결된 SPDM 의뢰 폴더 아래에 없습니다.")
    # A conflict at this folder or an owning ancestor blocks traversal. A
    # foreign child is filtered individually below so it does not hide safe
    # siblings at this request-level parent.
    _owner_conflict(conn, root_id, root_key, parent, project_id, request_id, environment,
                    include_descendants=False)
    schema_nodes = {_root_casefold(str(item.get("relative_path", ""))): item for item in schema.get("nodes", [])}
    parent_context = _empty_context()
    prefix = list(wr_parts)
    for name in parent_parts[len(wr_parts):]:
        prefix.append(name)
        current = PurePosixPath(*prefix).as_posix()
        key = _root_casefold(current)
        node = schema_nodes.get(key)
        assignment = effective_assignment(schema, key)
        if not node or not assignment or assignment.get("status") not in {"CONFIRMED", "LINKED", "CONTAINER"} or assignment.get("role_kind") == "EXCLUDE":
            raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_STALE", "선택한 위치가 현재 확정된 Folder Schema에 없습니다. 구조를 다시 확인하세요.")
        if _root_casefold(str(node.get("parent_path") or "")) != _root_casefold(str(PurePosixPath(*prefix[:-1]).as_posix())):
            raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_STALE", "확정된 Folder Schema의 상위 경로가 일치하지 않습니다.")
        role = str(assignment.get("role_kind") or "")
        if role in {"SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"}:
            parent_context = _context_add(parent_context, role, str(assignment.get("name") or node.get("name") or name),
                                          current, root_key, root_id, assignment.get("option_status"),
                                          assignment.get("target_id"))
    parent_key = _root_casefold(parent)
    children = [item for item in schema.get("nodes", [])
                if _root_casefold(str(item.get("parent_path") or "")) == parent_key]
    children.sort(key=lambda item: (str(item.get("name", "")).casefold(), str(item.get("name", ""))))
    if len(children) > MAX_CHILDREN:
        raise ResultRegistrationError("SPDM_SCAN_LIMIT", "한 폴더에서 확인할 수 있는 하위 폴더 수를 초과했습니다.")
    nodes: list[dict[str, Any]] = []
    for schema_node in children:
        child_relative = str(schema_node.get("relative_path") or "")
        child_key = _root_casefold(child_relative)
        assignment = effective_assignment(schema, child_key)
        if (not assignment or assignment.get("status") not in {"CONFIRMED", "LINKED", "CONTAINER"} or
                assignment.get("role_kind") == "EXCLUDE"):
            continue
        if _has_owner_conflict(conn, root_id, root_key, child_relative, project_id, request_id, environment):
            continue
        role = str(assignment.get("role_kind") or "CONTAINER")
        child_name = str(assignment.get("name") or schema_node.get("name") or PurePosixPath(child_relative).name)
        try:
            child_absolute = _safe_existing(root, child_relative)
            if not LocalFsProvider(root).is_dir(child_absolute):
                continue
        except ResultRegistrationError:
            continue
        context = dict(parent_context)
        if role in {"SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"}:
            context = _context_add(context, role, child_name, child_relative, root_key, root_id,
                                   assignment.get("option_status"), assignment.get("target_id"))
        suggested = None
        can_prepare = False
        result_state = "NOT_APPLICABLE"
        if role in {"EVALUATION", "SCENE"}:
            confirmed_results = []
            for result_node in schema_nodes.values():
                result_path = str(result_node.get("relative_path") or "")
                result_assignment = effective_assignment(schema, _root_casefold(result_path))
                if (_root_casefold(str(result_node.get("parent_path") or "")) == child_key and
                        result_assignment and result_assignment.get("role_kind") == "RESULTS" and
                        result_assignment.get("status") in {"CONFIRMED", "LINKED"}):
                    confirmed_results.append(str(result_assignment.get("relative_path") or result_path))
            if confirmed_results:
                result_state = "PRESENT"
                if len(confirmed_results) == 1:
                    suggested = confirmed_results[0]
            elif role == "SCENE" and scope["environment"] == "DISTRIBUTION":
                # Distribution stores inputs and results directly in the
                # confirmed Scene folder; never propose a results sub-folder.
                suggested, result_state = child_relative, "PRESENT"
            else:
                result_candidate = f"{child_relative.rstrip('/')}/results"
                result_key = _root_casefold(result_candidate)
                result_node = schema_nodes.get(result_key)
                if result_node:
                    # A scanned child without a confirmed RESULTS role cannot
                    # be silently promoted to a destination.
                    result_state = "UNAVAILABLE"
                else:
                    try:
                        result_target = _safe_existing(root, result_candidate, allow_missing_leaf=True)
                        if LocalFsProvider(root).exists(result_target):
                            # Existing folders must have an explicit schema
                            # RESULTS role; only a missing standard leaf is a
                            # safe proposal for the folder preparation flow.
                            result_state = "UNAVAILABLE"
                        else:
                            suggested = result_candidate
                            can_prepare = True
                            result_state = "MISSING"
                    except ResultRegistrationError as exc:
                        result_state = "UNAVAILABLE" if exc.code != "SPDM_FOLDER_MISSING" else "MISSING"
                        if result_state == "MISSING":
                            suggested, can_prepare = result_candidate, True
        elif role == "RESULTS":
            suggested, can_prepare, result_state = child_relative, True, "PRESENT"
        has_children = any(
            _root_casefold(str(item.get("parent_path") or "")) == child_key and
            (child_assignment := effective_assignment(
                schema, _root_casefold(str(item.get("relative_path") or "")))) is not None and
            child_assignment.get("status") in {"CONFIRMED", "LINKED", "CONTAINER"} and
            child_assignment.get("role_kind") != "EXCLUDE"
            for item in schema.get("nodes", [])
        )
        nodes.append({"relative_path": child_relative, "name": child_name, "role_kind": role,
                      "context": context, "result_state": result_state, "can_prepare": can_prepare,
                      "suggested_relative_path": suggested, "children_available": has_children,
                      "selectable": role in {"SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"}})
    return {"storage_root_id": root_id, "project_id": project_id, "request_id": request_id,
            "environment": scope["environment"], "parent_relative_path": parent,
            "parent_context": parent_context, "nodes": nodes}


def _role_transition(environment: str, parent_role: str, role: str, name: str) -> str | None:
    allowed = {
        "USAGE": {"REQUEST": {"SIMULATION_CASE"}, "SIMULATION_CASE": {"EVALUATION"}, "EVALUATION": {"RESULTS"}},
        "DISTRIBUTION": {"REQUEST": {"SIMULATION_CASE"}, "SIMULATION_CASE": {"LOAD_CASE"},
                         "LOAD_CASE": {"EXECUTION_RUN"}, "EXECUTION_RUN": {"RUN_OPTION", "SCENE"},
                         "RUN_OPTION": {"SCENE"}, "SCENE": {"RESULTS"}},
    }
    if role not in allowed[environment].get(parent_role, set()):
        raise ResultRegistrationError("RESULT_HIERARCHY_INVALID", f"{parent_role} 아래에 {role} 폴더를 둘 수 없습니다.")
    if role == "EVALUATION" and name.casefold() not in {item.casefold() for item in EVALUATIONS}:
        raise ResultRegistrationError("RESULT_EVALUATION_INVALID", "지원하는 평가 항목을 선택하세요.")
    if role == "RESULTS" and name.casefold() != "results":
        raise ResultRegistrationError("RESULT_FOLDER_NAME_INVALID", "결과 폴더 이름은 results로 고정합니다.")
    if role not in {"RESULTS", "EVALUATION"} and (len(name) > 128 or name.startswith(".")):
        raise ResultRegistrationError("RESULT_FOLDER_NAME_INVALID", "폴더 이름은 1~128자의 일반 Windows 폴더 이름이어야 합니다.")
    return role


def _preview_target(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
                    parent_relative_path: str, segments: list[dict[str, str]]) -> dict[str, Any]:
    scope = _scope(conn, project_id, request_id, environment)
    root, root_id, root_key = storage_context(conn)
    parent_relative_path = _relative(parent_relative_path)
    if _is_final_branch(scope["request_relative_path"], parent_relative_path):
        raise ResultRegistrationError("RESULT_FINAL_BRANCH_BLOCKED", "Final 폴더는 일반 결과 등록 위치로 사용할 수 없습니다.")
    parent_context, parent_nodes = _trace_path(conn, root, root_id, root_key, scope, parent_relative_path)
    wr_parts = PurePosixPath(scope["request_relative_path"]).parts
    parent_parts = PurePosixPath(parent_relative_path).parts
    if len(parent_parts) < len(wr_parts) or tuple(p.casefold() for p in parent_parts[:len(wr_parts)]) != tuple(p.casefold() for p in wr_parts):
        raise ResultRegistrationError("RESULT_PATH_OUTSIDE_REQUEST", "선택 경로가 연결된 의뢰 폴더 밖에 있습니다.")
    semantic_roles = {"SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE", "RESULTS"}
    parent_role = next((str(node["role_kind"]) for node in reversed(parent_nodes)
                       if node["role_kind"] in semantic_roles), "REQUEST")
    if len(segments) > 8 or (not segments and not (scope["environment"] == "DISTRIBUTION" and parent_role == "SCENE")):
        raise ResultRegistrationError("RESULT_HIERARCHY_INVALID", "결과 경로 계층은 1~8개 폴더로 지정하세요.")
    if any(str(segment.get("role_kind") or "") == "SIMULATION_CASE" for segment in segments):
        _validate_registered_schema_parent(conn, root, root_key, scope, parent_relative_path)
    current_context = parent_context
    current_path = parent_relative_path
    proposed: list[dict[str, Any]] = []
    all_roles = list(parent_nodes)
    for segment in segments:
        role = str(segment.get("role_kind") or "")
        name = str(segment.get("name") or "")
        if not name or any(ord(ch) < 32 for ch in name) or not spdm_storage._valid_windows_name(name):
            raise ResultRegistrationError("RESULT_FOLDER_NAME_INVALID", "폴더 이름은 안전한 Windows 폴더 이름이어야 합니다.")
        _role_transition(scope["environment"], parent_role, role, name)
        current_path = f"{current_path}/{name}"
        if len(current_path) > 2048:
            raise ResultRegistrationError("RESULT_PATH_INVALID", "결과 경로가 너무 깁니다.")
        _owner_conflict(conn, root_id, root_key, current_path, project_id, request_id, scope["environment"])
        parent_relative = PurePosixPath(current_path).parent.as_posix()
        fs = LocalFsProvider(root)
        parent_dir = _safe_existing(root, parent_relative, allow_missing_leaf=True)
        collisions = {}
        if fs.is_dir(parent_dir):
            try:
                collisions = {item.name.casefold(): item.name for item in fs.list(parent_dir)}
            except OSError as exc:
                raise ResultRegistrationError("SPDM_FOLDER_UNAVAILABLE", "결과 폴더의 상위 경로에 접근할 수 없습니다.") from exc
        existed_by_name = name.casefold() in collisions
        actual_name = collisions.get(name.casefold(), name)
        actual_path = f"{parent_relative}/{actual_name}"
        if existed_by_name:
            target = _safe_existing(root, actual_path)
            if not fs.is_dir(target):
                raise ResultRegistrationError("RESULT_PATH_CONFLICT", "결과 폴더 경로에 파일이 있습니다.")
        # A confirmed semantic role remains authoritative even when the disk
        # folder is temporarily absent and is being repaired.
        registered = _registry_role(conn, root_key, actual_path, project_id, request_id, scope["environment"])
        # If the physical folder is missing, use the registered spelling as
        # the canonical target path. This keeps case-insensitive Windows paths
        # aligned with the persisted identity and avoids creating a parallel
        # casing variant on case-sensitive test/development filesystems.
        if not existed_by_name and registered and registered[2]:
            actual_name = registered[2]
            actual_path = f"{parent_relative}/{actual_name}"
        if registered and registered[0] != role:
            raise ResultRegistrationError("RESULT_PATH_ROLE_CONFLICT", "기존 확인된 폴더 역할과 다른 역할로 사용할 수 없습니다.")
        target_id = registered[1] if registered else None
        registered_name = registered[2] if registered else None
        option_status = registered[3] if registered else ("PRESENT" if role == "RUN_OPTION" else None)
        raw_name = registered_name or actual_name
        current_context = _context_add(current_context, role, raw_name, actual_path, root_key, root_id, option_status, target_id)
        parent_role = role
        current_path = actual_path
        context_key = {"SIMULATION_CASE": "simulation_case", "EVALUATION": "evaluation", "LOAD_CASE": "load_case",
                       "EXECUTION_RUN": "execution_run", "RUN_OPTION": "run_option", "SCENE": "scene"}.get(role, "")
        context_item = current_context.get(context_key) if context_key else None
        node_target_id = target_id or (context_item.get("id") if isinstance(context_item, dict) else None)
        if not node_target_id:
            node_target_id = _stable(f"environment-{role.casefold()}", root_key, actual_path, role)
        node = {"relative_path": actual_path, "role_kind": role, "target_id": node_target_id,
                "raw_name": raw_name, "option_status": option_status}
        all_roles.append(node)
        proposed.append({"relative_path": actual_path, "role_kind": role, "name": actual_name,
                         "exists": _path_exists(root, actual_path)})
    if not current_context.get("simulation_case"):
        raise ResultRegistrationError("RESULT_CASE_REQUIRED", "결과 폴더 계층에 Simulation Case가 필요합니다.")
    direct_scene_leaf = (scope["environment"] == "DISTRIBUTION" and parent_role == "SCENE"
                         and all_roles and all_roles[-1]["role_kind"] == "SCENE")
    if not direct_scene_leaf:
        expected_leaf = "EVALUATION" if scope["environment"] == "USAGE" else "SCENE"
        if parent_role != "RESULTS" or (len(all_roles) < 2 or all_roles[-2]["role_kind"] != expected_leaf):
            raise ResultRegistrationError("RESULT_HIERARCHY_INVALID", "결과 폴더는 평가 항목 또는 Scene 아래의 results여야 합니다.")
    result_relative_path = current_path
    case_relative_path = current_context["simulation_case"]["relative_path"]
    return {"scope": scope, "root": root, "root_id": root_id, "root_key": root_key,
            "case_relative_path": case_relative_path, "result_relative_path": result_relative_path,
            "context": current_context, "path_nodes": all_roles, "proposed_paths": proposed}


def _path_exists(root: Path, relative_path: str) -> bool:
    try:
        return LocalFsProvider(root).is_dir(_safe_existing(root, relative_path))
    except ResultRegistrationError as exc:
        if exc.code == "SPDM_FOLDER_MISSING":
            return False
        raise


def _prepare_folders_impl(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
                          parent_relative_path: str, segments: list[dict[str, str]], confirm_create: bool,
                          actor: str) -> dict[str, Any]:
    plan = _preview_target(conn, project_id, request_id, environment, parent_relative_path, segments)
    result_path = plan["result_relative_path"]
    _lock_path(conn, plan["root_key"], result_path)
    _owner_conflict(conn, plan["root_id"], plan["root_key"], result_path, project_id, request_id, plan["scope"]["environment"])
    missing = [item["relative_path"] for item in plan["proposed_paths"] if not _path_exists(plan["root"], item["relative_path"])]
    if not confirm_create:
        return {"status": "CONFIRM_REQUIRED" if missing else "EXISTS", "created": False,
                "case_relative_path": plan["case_relative_path"], "result_relative_path": result_path,
                "context": plan["context"], "proposed_paths": plan["proposed_paths"],
                "created_paths": []}
    fs = LocalFsProvider(plan["root"])
    created: list[str] = []
    if missing:
        try:
            for relative in missing:
                destination = _safe_existing(plan["root"], relative, allow_missing_leaf=True)
                if fs.exists(destination):
                    if not fs.is_dir(destination) or fs.is_link(destination):
                        raise ResultRegistrationError("RESULT_PATH_CONFLICT", "결과 경로가 안전한 폴더가 아닙니다.")
                    continue
                fs.mkdirs(destination, zone=LEGACY, parents=False, exist_ok=False)
                created.append(destination)
                fs.assert_safe(destination)
        except BaseException:
            for directory in reversed(created):
                try:
                    fs.remove(directory, zone=LEGACY, directory=True)
                except OSError:
                    pass
            raise
    # Persist only the explicit role decisions; no dashboard/business entities
    # are written here. Case ownership is additionally protected by path_key.
    try:
        for node in plan["path_nodes"]:
            role = node["role_kind"]
            if role in {"CONTAINER", "RESULTS"}:
                continue
            _lock_path(conn, plan["root_key"], node["relative_path"])
            _owner_conflict(conn, plan["root_id"], plan["root_key"], node["relative_path"], project_id, request_id, plan["scope"]["environment"])
            context_item = plan["context"].get({"SIMULATION_CASE":"simulation_case", "EVALUATION":"evaluation", "LOAD_CASE":"load_case",
                                                 "EXECUTION_RUN":"execution_run", "RUN_OPTION":"run_option", "SCENE":"scene"}.get(role, ""))
            target_id = node.get("target_id") or (context_item.get("id") if isinstance(context_item, dict) else None) or _stable(
                f"environment-{role.casefold()}", plan["root_key"], node["relative_path"], role)
            conn.execute("""INSERT INTO result_registration_paths
                (id,root_key,project_id,request_id,environment,relative_path,path_key,parent_relative_path,role_kind,target_id,raw_name,option_status,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(root_key,path_key) DO NOTHING""",
                ["result-registration-path-" + uuid4().hex, plan["root_key"], project_id, request_id, plan["scope"]["environment"],
                 node["relative_path"], _root_casefold(node["relative_path"]), PurePosixPath(node["relative_path"]).parent.as_posix(),
                 role, target_id, node["raw_name"], node.get("option_status"), actor, _now()])
            stored = conn.execute("SELECT project_id,request_id,environment,role_kind,target_id FROM result_registration_paths WHERE root_key=? AND path_key=?",
                                  [plan["root_key"], _root_casefold(node["relative_path"])]).fetchone()
            if not stored or (str(stored[0]),str(stored[1]),str(stored[2]),str(stored[3]),str(stored[4])) != (project_id,request_id,plan["scope"]["environment"],role,str(target_id)):
                raise ResultRegistrationError("RESULT_PATH_OWNERSHIP_CONFLICT", "폴더 역할이 다른 업무의 연결과 충돌합니다.")
    except BaseException:
        for directory in reversed(created):
            try:
                fs.remove(directory, zone=LEGACY, directory=True)
            except OSError:
                pass
        raise
    return {"status": "PREPARED", "created": bool(missing), "created_paths": missing,
            "case_relative_path": plan["case_relative_path"], "result_relative_path": result_path,
            "context": plan["context"], "proposed_paths": plan["proposed_paths"],
            "_created_absolute_paths": created, "_created_root": plan["root"]}


def prepare_folders(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
                    parent_relative_path: str, segments: list[dict[str, str]], confirm_create: bool,
                    actor: str) -> dict[str, Any]:
    """Prepare a result-only path and registry atomically on the supplied connection.

    The caller owns `connect()` lifetime; this public function owns a transaction
    boundary for confirmed creation and compensates newly created empty folders
    if any database write or commit fails. Previews are read-only and do not
    begin a transaction. Call it only while holding the connection for the full
    request so PostgreSQL path advisory xact locks cover the ownership checks.
    """
    if not confirm_create:
        return _prepare_folders_impl(conn, project_id, request_id, environment,
                                     parent_relative_path, segments, False, actor)
    is_duck = getattr(conn, "backend", None) != "postgresql"
    if is_duck:
        conn.execute("BEGIN TRANSACTION")
    created: list[str] = []
    created_root: Path | None = None
    try:
        result = _prepare_folders_impl(conn, project_id, request_id, environment,
                                       parent_relative_path, segments, True, actor)
        created = result.pop("_created_absolute_paths", [])
        created_root = result.pop("_created_root", None)
        conn.execute("COMMIT")
        return result
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        if created_root is not None:
            fs = LocalFsProvider(created_root)
            for directory in reversed(created):
                try:
                    fs.remove(directory, zone=LEGACY, directory=True)
                except OSError:
                    pass
        raise
