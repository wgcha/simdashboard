"""Resolve the saved, request-scoped Folder Schema for read-only consumers.

The resolver is intentionally independent of result registration path tracing.
It selects the latest request-linked scan, validates its saved profile version,
re-scans that scan root safely, and overlays applicable confirmed roles.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path, PurePosixPath
from typing import Any

from ..database_connection import ConnectionLike, rows
from . import (environment_folder_profiles, folder_discovery, folder_discovery_environment,
               folder_discovery_scan, spdm_storage)

_APPLIED_STATUSES = ("REGISTERED", "CAPTURING", "COMPLETED", "FAILED")
_ROLE_KINDS = {
    "USAGE": {"PROJECT", "REQUEST", "SIMULATION_CASE", "EVALUATION", "RESULTS", "INPUT", "CONTAINER"},
    "DISTRIBUTION": {"PROJECT", "REQUEST", "SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION",
                     "SCENE", "RESULTS", "INPUT", "CONTAINER"},
}


class FolderSchemaError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)
from .folder_schema_locations import EnvironmentLocations, resolve_request_locations


def _decode(value: Any, *, code: str, message: str) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise FolderSchemaError(code, message) from exc


def _normal(value: Any, *, allow_root: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_root) or "\\" in value or "\x00" in value:
        raise FolderSchemaError("FOLDER_SCHEMA_PATH_INVALID", "폴더 스키마 경로가 올바르지 않습니다.")
    try:
        return folder_discovery_scan.normal(value)
    except (ValueError, spdm_storage.SpdmStorageError) as exc:
        raise FolderSchemaError("FOLDER_SCHEMA_PATH_INVALID", "폴더 스키마 경로가 올바르지 않습니다.") from exc


def _fold(value: str) -> str:
    return "/".join(part.casefold() for part in PurePosixPath(value).parts)


def _is_ancestor(path: str, descendant: str) -> bool:
    if not path:
        return True
    folded_path, folded_descendant = _fold(path), _fold(descendant)
    return folded_descendant == folded_path or folded_descendant.startswith(folded_path.rstrip("/") + "/")


def scan_fingerprints(result: dict[str, Any], root: Path | None = None, *,
                      deadline: float | None = None) -> tuple[str, str]:
    """Fingerprint directory structure and relevant file bytes under scan limits."""
    structure = sorted((
        _fold(str(item.get("relative_path") or "")),
        _fold(str(item.get("parent_path") or "")),
        str(item.get("identity") or ""),
    ) for item in result.get("nodes", []))
    file_state = result.get("file_state", [])
    content = []
    content_extensions = {".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm", ".inc", ".rad"}
    hashed_files = 0
    total_bytes = 0
    started = time.monotonic()
    for item in file_state:
        relative_path = str(item.get("relative_path") or "")
        size = int(item.get("size") or 0)
        modified_ns = int(item.get("modified_ns") or 0)
        digest = None
        suffix = Path(relative_path).suffix.casefold()
        if root is not None and suffix in content_extensions:
            hashed_files += 1
            if hashed_files > 10_000:
                raise FolderSchemaError("FOLDER_SCHEMA_CONTENT_FILE_LIMIT", "내용 fingerprint 파일 수 한도를 초과했습니다.", 413)
            max_file_bytes = 64 * 1024 * 1024 if suffix in {".inc", ".rad"} else 32 * 1024 * 1024
            if size > max_file_bytes:
                raise FolderSchemaError("FOLDER_SCHEMA_CONTENT_FILE_LIMIT", "내용 fingerprint 대상 파일이 허용 크기를 초과했습니다.", 413)
            file_path = root.joinpath(*PurePosixPath(relative_path).parts)
            try:
                spdm_storage._assert_safe_existing(file_path, root)
                before = file_path.stat()
                if before.st_size != size or int(before.st_mtime_ns) != modified_ns:
                    raise FolderSchemaError("FOLDER_SCHEMA_FILE_BUSY", "조사 도중 파일이 변경되었습니다. 작성 완료 후 다시 새로고침하세요.", 409)
                digest_state = hashlib.sha256()
                read_size = 0
                with spdm_storage.open_stable_reader(file_path) as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest_state.update(chunk)
                        read_size += len(chunk)
                        total_bytes += len(chunk)
                        if total_bytes > 256 * 1024 * 1024:
                            raise FolderSchemaError("FOLDER_SCHEMA_CONTENT_TOTAL_LIMIT", "내용 fingerprint 전체 크기 한도를 초과했습니다.", 413)
                        if (time.monotonic() - started > folder_discovery_scan.MAX_SECONDS
                                or (deadline is not None and time.monotonic() > deadline)):
                            raise FolderSchemaError("FOLDER_SCHEMA_CONTENT_TIME_LIMIT", "내용 fingerprint 시간 한도를 초과했습니다.", 413)
                after = file_path.stat()
                if (read_size != size or after.st_size != size or int(after.st_mtime_ns) != modified_ns
                        or getattr(after, "st_ino", None) != getattr(before, "st_ino", None)):
                    raise FolderSchemaError("FOLDER_SCHEMA_FILE_BUSY", "조사 도중 파일이 변경되었습니다. 작성 완료 후 다시 새로고침하세요.", 409)
                digest = digest_state.hexdigest()
            except FolderSchemaError:
                raise
            except spdm_storage.SpdmStorageError as exc:
                raise FolderSchemaError(exc.code, str(exc), 409 if exc.code == "SPDM_FILE_BUSY" else 422) from exc
            except OSError as exc:
                raise FolderSchemaError("FOLDER_SCHEMA_FILE_UNAVAILABLE", "내용 fingerprint 대상 파일을 읽을 수 없습니다.", 422) from exc
        content.append((_fold(relative_path), size, modified_ns, digest))
    if (time.monotonic() - started > folder_discovery_scan.MAX_SECONDS
            or (deadline is not None and time.monotonic() > deadline)):
        raise FolderSchemaError("FOLDER_SCHEMA_CONTENT_TIME_LIMIT", "내용 fingerprint 시간 한도를 초과했습니다.", 413)
    content.sort()
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encode(structure)).hexdigest(), hashlib.sha256(encode(content)).hexdigest()


def active_refresh_snapshot(conn: ConnectionLike, root_key: str, project_id: str,
                            request_id: str, environment: str) -> dict[str, Any] | None:
    found = rows(conn.execute(
        "SELECT id,root_key,project_id,request_id,environment,relative_path,profile_id,profile_revision,"
        "tree_json,created_by,created_at FROM folder_environment_scans "
        "WHERE root_key=? AND project_id=? AND request_id=? AND environment=? "
        "AND status='COMPLETE' AND id LIKE 'folder-refresh-%' ORDER BY created_at DESC,id DESC LIMIT 1",
        [root_key, project_id, request_id, environment],
    ))
    if not found:
        return None
    record = found[0]
    snapshot = _decode(record.get("tree_json"), code="FOLDER_SCHEMA_SNAPSHOT_INVALID",
                       message="저장된 폴더 새로고침 내용을 읽을 수 없습니다.")
    if not isinstance(snapshot, dict) or snapshot.get("kind") != "FOLDER_SCHEMA_REFRESH" or snapshot.get("version") != 1:
        raise FolderSchemaError("FOLDER_SCHEMA_SNAPSHOT_INVALID", "저장된 폴더 새로고침 형식이 올바르지 않습니다.")
    return {
        "id": str(record["id"]), "root_key": str(record["root_key"]),
        "project_id": str(record["project_id"]), "request_id": str(record["request_id"]),
        "environment": str(record["environment"]), "request_relative_path": str(record["relative_path"]),
        "profile_id": str(record["profile_id"]), "profile_revision": int(record["profile_revision"]),
        "structure_fingerprint": str(snapshot.get("structure_fingerprint") or ""),
        "content_fingerprint": str(snapshot.get("content_fingerprint") or ""),
        "schema_json": snapshot.get("schema"), "created_by": record.get("created_by"),
        "created_at": record.get("created_at"),
    }


def _request_path(conn: ConnectionLike, root_key: str, project_id: str, request_id: str,
                  environment: str) -> str:
    request = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    if not request or str(request[0]) != project_id:
        raise FolderSchemaError("FOLDER_SCHEMA_REQUEST_INVALID", "프로젝트와 의뢰의 연결을 확인할 수 없습니다.", 404)

    bindings = rows(conn.execute(
        "SELECT project_folder,request_folder FROM spdm_storage_request_parents "
        "WHERE project_id=? AND request_id=?",
        [project_id, request_id],
    ))
    paths = {(str(item["project_folder"]), str(item["request_folder"])) for item in bindings}
    if not paths:
        registered = rows(conn.execute(
            "SELECT r.id,g.relative_path,g.role_kind FROM folder_environment_registry g "
            "JOIN folder_environment_registrations r ON r.id=g.registration_id "
            "WHERE g.root_key=? AND r.project_id=? AND r.request_id=? "
            "AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED') "
            "AND g.role_kind IN ('PROJECT','REQUEST')",
            [root_key, project_id, request_id],
        ))
        project_paths: dict[str, set[str]] = {}
        request_paths: dict[str, set[str]] = {}
        for item in registered:
            target = project_paths if item["role_kind"] == "PROJECT" else request_paths
            target.setdefault(str(item["id"]), set()).add(str(item["relative_path"]))
        paths = {
            (project_path, request_path)
            for registration_id, project_set in project_paths.items()
            for project_path in project_set
            for request_path in request_paths.get(registration_id, set())
        }
    normalized: set[tuple[str, str]] = set()
    for project_folder, request_folder in paths:
        project_path, request_path = _normal(project_folder), _normal(request_folder)
        if not _is_ancestor(project_path, request_path) or project_path.casefold() == request_path.casefold():
            raise FolderSchemaError("FOLDER_SCHEMA_REQUEST_BINDING_INVALID", "프로젝트와 의뢰 폴더 연결이 올바르지 않습니다.")
        normalized.add((project_path, request_path))
    if len(normalized) == 1:
        return next(iter(normalized))[1]
    if len(normalized) > 1:
        raise FolderSchemaError("FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED",
                                "의뢰에 연결된 저장소 경로가 여러 개입니다. 관리자에게 연결을 요청하세요.")

    # Folder Schema registration may have skipped registry rows for an
    # already-selected request. Recover only an explicitly confirmed REQUEST
    # row that targets this request, never from a Case path or name heuristic.
    associations = rows(conn.execute(
        "SELECT s.id,s.relative_path,s.tree_json,s.project_id AS scan_project_id,s.request_id AS scan_request_id,"
        "p.rows_json,p.can_apply,r.id AS registration_id,r.status AS registration_status,"
        "r.project_id AS registration_project_id,r.request_id AS registration_request_id "
        "FROM folder_environment_scans s LEFT JOIN folder_environment_previews p ON p.scan_id=s.id "
        "LEFT JOIN folder_environment_registrations r ON r.preview_id=p.id "
        "WHERE s.root_key=? AND s.environment=? AND s.status='COMPLETE' AND "
        "((s.project_id=? AND s.request_id=?) OR (r.project_id=? AND r.request_id=? "
        "AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED'))) "
        "ORDER BY s.created_at DESC,s.id DESC,p.created_at DESC,p.id DESC",
        [root_key, environment, project_id, request_id, project_id, request_id],
    ))
    explicit_paths: set[str] = set()
    linked_request_paths: set[str] = set()
    for association in associations:
        if (str(association.get("scan_project_id") or "") == project_id
                and str(association.get("scan_request_id") or "") == request_id):
            tree = _decode(association.get("tree_json"), code="FOLDER_SCHEMA_SCAN_INVALID",
                           message="저장된 조사 트리를 읽을 수 없습니다.")
            if isinstance(tree, list):
                for item in tree:
                    if isinstance(item, dict) and item.get("role_kind") == "REQUEST" and item.get("relative_path"):
                        linked_request_paths.add(_normal(str(item["relative_path"])))
        if not association.get("rows_json") or not association.get("can_apply"):
            continue
        if association.get("registration_id") and (
            str(association.get("registration_project_id") or "") != project_id
            or str(association.get("registration_request_id") or "") != request_id
            or association.get("registration_status") not in _APPLIED_STATUSES
        ):
            continue
        saved = _decode(association["rows_json"], code="FOLDER_SCHEMA_PREVIEW_INVALID",
                        message="저장된 의뢰 폴더 역할을 읽을 수 없습니다.")
        plan = saved.get("rows", []) if isinstance(saved, dict) else saved
        if not isinstance(plan, list):
            continue
        for item in plan:
            if (isinstance(item, dict) and item.get("role_kind") == "REQUEST"
                    and item.get("status") == "CONFIRMED" and item.get("relative_path")):
                request_role_path = _normal(str(item["relative_path"]))
                if str(item.get("target_id") or "") == request_id:
                    explicit_paths.add(request_role_path)
                linked_request_paths.add(request_role_path)
    if len(explicit_paths) == 1:
        return next(iter(explicit_paths))
    if len(explicit_paths) > 1:
        raise FolderSchemaError("FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED",
                                "선택한 의뢰에 여러 REQUEST 경로가 연결되어 있습니다.")
    if len(linked_request_paths) == 1:
        return next(iter(linked_request_paths))
    if len(linked_request_paths) > 1:
        raise FolderSchemaError("FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED",
                                "선택한 의뢰 조사에 여러 REQUEST 경로가 있습니다. 역할을 확인하세요.")

    # A request-linked scan rooted exactly at one child of the stored Project
    # folder is sufficient to recover a request root. A Case-root or storage-
    # root scan is deliberately not promoted to the Request boundary.
    project_folders = rows(conn.execute(
        "SELECT project_folder FROM spdm_storage_project_parents WHERE project_id=?",
        [project_id],
    ))
    linked_scan_roots = {
        _normal(str(item.get("relative_path")), allow_root=True)
        for item in associations
        if item.get("relative_path")
    }
    recoverable = set()
    for scan_root in linked_scan_roots:
        for project_record in project_folders:
            project_folder = _normal(str(project_record["project_folder"]))
            scan_parts = PurePosixPath(scan_root).parts
            project_parts = PurePosixPath(project_folder).parts
            if len(scan_parts) == len(project_parts) + 1 and tuple(
                part.casefold() for part in scan_parts[:len(project_parts)]
            ) == tuple(part.casefold() for part in project_parts):
                recoverable.add(scan_root)
    if len(recoverable) == 1:
        return next(iter(recoverable))
    raise FolderSchemaError("FOLDER_SCHEMA_REQUEST_BINDING_REQUIRED",
                            "의뢰 폴더 연결 또는 확인된 REQUEST 경로가 없습니다. 관리자에게 연결을 요청하세요.")


def _profile_for_scan(scan: dict[str, Any], environment: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not scan.get("profile_id") or scan.get("profile_environment") != environment:
        raise FolderSchemaError("FOLDER_SCHEMA_PROFILE_INVALID", "조사에 연결된 환경 프로필을 확인할 수 없습니다.")
    if scan.get("current_revision") is None:
        raise FolderSchemaError("FOLDER_SCHEMA_PROFILE_MISSING", "조사에 연결된 저장 규칙을 찾을 수 없습니다.")
    if int(scan["profile_revision"]) != int(scan["current_revision"]):
        raise FolderSchemaError("FOLDER_SCHEMA_PROFILE_STALE", "저장 규칙 버전이 조사 후 변경되었습니다. 의뢰 폴더를 다시 조사하세요.")
    raw_rules = _decode(scan.get("rules_json"), code="FOLDER_SCHEMA_PROFILE_INVALID",
                        message="저장 규칙 JSON을 읽을 수 없습니다.")
    if not isinstance(raw_rules, dict):
        raise FolderSchemaError("FOLDER_SCHEMA_PROFILE_INVALID", "저장 규칙 형식이 올바르지 않습니다.")
    try:
        rules = environment_folder_profiles.validate_rules(environment, raw_rules)
    except (TypeError, ValueError) as exc:
        raise FolderSchemaError("FOLDER_SCHEMA_PROFILE_INVALID", "저장 규칙 형식이 올바르지 않습니다.") from exc
    profile = {"id": str(scan["profile_id"]), "revision": int(scan["current_revision"]),
               "name": str(scan.get("profile_name") or ""), "rules": rules}
    return profile, rules


def _preview_roles(preview_value: Any, request_path: str, environment: str,
                   source: str) -> dict[str, dict[str, Any]]:
    saved = _decode(preview_value, code="FOLDER_SCHEMA_PREVIEW_INVALID", message="저장된 폴더 역할을 읽을 수 없습니다.")
    if isinstance(saved, list):
        saved = {"rows": saved}
    if not isinstance(saved, dict):
        raise FolderSchemaError("FOLDER_SCHEMA_PREVIEW_INVALID", "저장된 폴더 역할 형식이 올바르지 않습니다.")
    plan_rows = saved.get("rows", [])
    states = saved.get("node_states", [])
    if not isinstance(plan_rows, list) or not isinstance(states, list):
        raise FolderSchemaError("FOLDER_SCHEMA_PREVIEW_INVALID", "저장된 폴더 역할 형식이 올바르지 않습니다.")
    result: dict[str, dict[str, Any]] = {}

    def add(item: Any, *, state_row: bool = False) -> None:
        if not isinstance(item, dict):
            raise FolderSchemaError("FOLDER_SCHEMA_PREVIEW_INVALID", "저장된 폴더 역할 경로가 올바르지 않습니다.")
        raw_path = item.get("relative_path")
        if not raw_path:
            if state_row and (item.get("status") == "CONTAINER" or item.get("role_kind") == "CONTAINER"):
                return
            raise FolderSchemaError("FOLDER_SCHEMA_PREVIEW_INVALID", "저장된 폴더 역할 경로가 올바르지 않습니다.")
        path = _normal(str(raw_path), allow_root=True)
        if not path:
            return
        if not _is_ancestor(request_path, path):
            return
        status = str(item.get("status") or "")
        role = str(item.get("role_kind") or "")
        if status == "EXCLUDED" or role == "EXCLUDE":
            role, status = "EXCLUDE", "EXCLUDED"
        elif status == "CONTAINER" or role == "CONTAINER" or (state_row and not role and status == "CONFIRMED"):
            role, status = "CONTAINER", "CONTAINER"
        elif status != "CONFIRMED" or role not in _ROLE_KINDS[environment]:
            return
        item_role = {
            "relative_path": path,
            "role_kind": role,
            "status": status,
            "target_id": str(item.get("target_id")) if item.get("target_id") else None,
            "name": str(item.get("name") or PurePosixPath(path).name),
            "option_status": str(item.get("option_status") or "") or None,
            "source": source,
            "role_basis": str(item.get("role_basis") or "") or None,
        }
        key = _fold(path)
        existing = result.get(key)
        if existing and (
                existing["role_kind"] != item_role["role_kind"] or
                existing["status"] != item_role["status"] or
                (existing.get("target_id") and item_role.get("target_id") and
                 existing["target_id"] != item_role["target_id"])):
            raise FolderSchemaError("FOLDER_SCHEMA_ROLE_CONFLICT", "같은 경로에 서로 다른 확인 역할이 저장되어 있습니다.")
        if existing:
            # Semantic plan rows carry linked target IDs; node_states repeat
            # the disposition without those IDs. Keep the richer duplicate.
            if existing.get("target_id") and not item_role.get("target_id"):
                item_role["target_id"] = existing["target_id"]
            if existing.get("role_basis") and not item_role.get("role_basis"):
                item_role["role_basis"] = existing["role_basis"]
            if existing.get("option_status") and not item_role.get("option_status"):
                item_role["option_status"] = existing["option_status"]
        result[key] = item_role

    # Semantic plan rows preserve confirmed roles in old previews. Structural
    # inclusion/exclusion is available in node_states for current previews.
    for item in plan_rows:
        add(item)
    for item in states:
        add(item, state_row=True)
    return result


def _merge_roles(roles: dict[str, dict[str, Any]], additions: dict[str, dict[str, Any]]) -> None:
    for key, item in additions.items():
        previous = roles.get(key)
        if previous and (
                previous["role_kind"] != item["role_kind"] or
                previous["status"] != item["status"] or
                (previous.get("target_id") and item.get("target_id") and
                 previous["target_id"] != item["target_id"])):
            raise FolderSchemaError("FOLDER_SCHEMA_ROLE_CONFLICT", "같은 의뢰 경로에 서로 다른 폴더 역할이 적용되어 있습니다.")
        if previous and previous["source"] == "REGISTRATION" and item["source"] == "PREVIEW":
            continue
        if previous and not item.get("target_id") and previous.get("target_id"):
            item = {**item, "target_id": previous["target_id"]}
        roles[key] = item


def _role_evidence(conn: ConnectionLike, root_key: str, project_id: str, request_id: str,
                   environment: str, selected_scan: dict[str, Any], profile: dict[str, Any],
                   request_path: str) -> dict[str, dict[str, Any]]:
    records = rows(conn.execute(
        "SELECT s.id AS scan_id,s.profile_id,s.profile_revision,p.id AS preview_id,p.rows_json,p.can_apply,"
        "p.created_at AS preview_created_at,r.id AS registration_id,r.project_id AS registration_project_id,"
        "r.request_id AS registration_request_id,r.environment AS registration_environment,r.status AS registration_status "
        "FROM folder_environment_scans s JOIN folder_environment_previews p ON p.scan_id=s.id "
        "LEFT JOIN folder_environment_registrations r ON r.preview_id=p.id "
        "WHERE s.root_key=? AND s.environment=? AND s.profile_id=? AND s.profile_revision=? AND s.status='COMPLETE' "
        "AND p.can_apply=TRUE AND ((s.id=?) OR (r.project_id=? AND r.request_id=? AND r.environment=? "
        "AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED'))) "
        "ORDER BY p.created_at,p.id",
        [root_key, environment, profile["id"], profile["revision"], selected_scan["id"],
         project_id, request_id, environment],
    ))
    eligible: list[tuple[dict[str, Any], bool, dict[str, dict[str, Any]]]] = []
    active = set(_APPLIED_STATUSES)
    for record in records:
        registration_id = record.get("registration_id")
        is_applied = bool(registration_id and record.get("registration_status") in active
                          and str(record.get("registration_project_id") or "") == project_id
                          and str(record.get("registration_request_id") or "") == request_id
                          and str(record.get("registration_environment") or "") == environment)
        is_selected_scan_preview = (str(record["scan_id"]) == str(selected_scan["id"])
                                     and not registration_id)
        if not is_applied and not is_selected_scan_preview:
            continue
        if not record.get("can_apply"):
            continue
        source = "REGISTRATION" if is_applied else "PREVIEW"
        additions = _preview_roles(record.get("rows_json"), request_path, environment, source)
        selected = str(record["scan_id"]) == str(selected_scan["id"])
        eligible.append((record, selected, additions))

    selected_scan_roles: dict[str, dict[str, Any]] = {}
    for _, selected, additions in eligible:
        if selected:
            _merge_roles(selected_scan_roles, additions)

    roles: dict[str, dict[str, Any]] = {}
    for _, selected, additions in eligible:
        if selected:
            continue
        # A newer scan's saved disposition is the current answer for the
        # paths it actually reviewed. Preserve older registrations for other
        # paths and retain their conflict checks where the new scan has no
        # disposition.
        historical = {key: item for key, item in additions.items() if key not in selected_scan_roles}
        _merge_roles(roles, historical)
    roles.update(selected_scan_roles)
    return roles


def _add_hierarchy(nodes: list[dict[str, Any]]) -> None:
    contexts: dict[str, dict[str, dict[str, Any]]] = {}
    role_keys = {"SIMULATION_CASE": "simulation_case", "EVALUATION": "evaluation", "LOAD_CASE": "load_case",
                 "EXECUTION_RUN": "execution_run", "RUN_OPTION": "run_option", "SCENE": "scene"}
    for node in sorted(nodes, key=lambda item: (int(item.get("depth", 0)), _fold(str(item["relative_path"])) )):
        parent_context = contexts.get(_fold(str(node.get("parent_path") or "")), {})
        context = dict(parent_context)
        node["hierarchy"] = {key: dict(value) for key, value in context.items()}
        role = str(node.get("role_kind") or "")
        key = role_keys.get(role)
        if key:
            summary = {field: node.get(field) for field in
                       ("relative_path", "parent_path", "name", "role_kind", "status", "target_id", "option_status")}
            context[key] = summary
            node["hierarchy"] = {name: dict(value) for name, value in context.items()}
        contexts[_fold(str(node["relative_path"]))] = context


def resolve_request_schema(conn: ConnectionLike, root: Path, root_key: str,
                           project_id: str, request_id: str, environment: str, *,
                           registered_scan_id: str | None = None) -> dict[str, Any]:
    """Return the validated current role tree for a request's saved Folder Schema.

    A profile is selected only through a complete scan explicitly linked to this
    request. The scan's saved profile revision must still be current. Applied
    previews for that profile contribute durable confirmations outside paths
    dispositioned by the selected scan; the selected scan's preview is current
    for its own paths. An unapplied preview contributes only when it belongs to
    the selected scan.
    """
    environment = str(environment).upper()
    if environment not in _ROLE_KINDS:
        raise FolderSchemaError("FOLDER_SCHEMA_ENVIRONMENT_INVALID", "지원하지 않는 폴더 환경입니다.")
    if folder_discovery_environment.root_identity(root) != root_key:
        raise FolderSchemaError("FOLDER_SCHEMA_ROOT_MISMATCH", "조사된 저장소가 현재 저장소와 다릅니다.")
    request_path = _request_path(conn, root_key, project_id, request_id, environment)
    active = active_refresh_snapshot(conn, root_key, project_id, request_id, environment)
    if active and registered_scan_id is None:
        profile_row = conn.execute(
            "SELECT id,environment FROM folder_environment_profiles WHERE id=?",
            [active["profile_id"]],
        ).fetchone()
        if not profile_row or str(profile_row[1]) != environment:
            raise FolderSchemaError("FOLDER_SCHEMA_PROFILE_MISSING", "현재 폴더 규칙을 찾을 수 없습니다.")
        snapshot = _decode(active["schema_json"], code="FOLDER_SCHEMA_SNAPSHOT_INVALID",
                           message="저장된 폴더 구조를 읽을 수 없습니다.")
        if (not isinstance(snapshot, dict) or str(snapshot.get("project_id")) != project_id
                or str(snapshot.get("request_id")) != request_id
                or str(snapshot.get("environment")) != environment
                or str(snapshot.get("request_relative_path")) != request_path
                or not isinstance(snapshot.get("nodes"), list)):
            raise FolderSchemaError("FOLDER_SCHEMA_SNAPSHOT_INVALID", "저장된 폴더 구조의 의뢰 문맥이 올바르지 않습니다.")
        saved_profile = snapshot.get("profile")
        if (not isinstance(saved_profile, dict) or str(saved_profile.get("id")) != str(active["profile_id"])
                or str(saved_profile.get("revision")) != str(active["profile_revision"])):
            raise FolderSchemaError("FOLDER_SCHEMA_SNAPSHOT_INVALID", "저장된 폴더 구조의 규칙 문맥이 올바르지 않습니다.")
        raw_rules = saved_profile.get("rules")
        try:
            rules = environment_folder_profiles.validate_rules(environment, raw_rules)
        except (TypeError, ValueError) as exc:
            raise FolderSchemaError("FOLDER_SCHEMA_PROFILE_INVALID", "저장 규칙 형식이 올바르지 않습니다.") from exc
        profile = {"id": str(profile_row[0]), "revision": int(active["profile_revision"]),
                   "name": str(saved_profile.get("name") or ""), "rules": rules}
        return {**snapshot, "profile": profile,
                "scan": {"id": str(active["id"]), "relative_path": request_path,
                         "profile_id": profile["id"], "profile_revision": profile["revision"],
                         "status": "COMPLETE"},
                "structure_fingerprint": str(active["structure_fingerprint"]),
                "content_fingerprint": str(active["content_fingerprint"])}
    if registered_scan_id is not None:
        scan_records = rows(conn.execute(
            "SELECT DISTINCT s.id,s.root_key,s.relative_path,s.environment,s.profile_id,s.profile_revision,s.project_id,s.request_id,"
            "s.status,s.tree_json,s.issues_json,s.created_at,fp.environment AS profile_environment,fp.name AS profile_name,"
            "fp.revision AS current_revision,fp.rules_json "
            "FROM folder_environment_scans s JOIN folder_environment_previews p ON p.scan_id=s.id "
            "JOIN folder_environment_registrations r ON r.preview_id=p.id "
            "LEFT JOIN folder_environment_profiles fp ON fp.id=s.profile_id "
            "WHERE s.id=? AND s.root_key=? AND s.environment=? AND r.project_id=? AND r.request_id=? "
            "AND r.environment=? AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED')",
            [registered_scan_id, root_key, environment, project_id, request_id, environment],
        ))
    else:
        scan_records = rows(conn.execute(
        "SELECT DISTINCT s.id,s.root_key,s.relative_path,s.environment,s.profile_id,s.profile_revision,s.project_id,s.request_id,"
        "s.status,s.tree_json,s.issues_json,s.created_at,fp.environment AS profile_environment,fp.name AS profile_name,"
        "fp.revision AS current_revision,fp.rules_json "
        "FROM folder_environment_scans s LEFT JOIN folder_environment_profiles fp ON fp.id=s.profile_id "
        "LEFT JOIN folder_environment_previews p ON p.scan_id=s.id "
        "LEFT JOIN folder_environment_registrations r ON r.preview_id=p.id "
        "WHERE s.root_key=? AND s.environment=? AND "
        "((s.project_id=? AND s.request_id=?) OR (r.project_id=? AND r.request_id=? "
        "AND r.environment=? AND r.status IN ('REGISTERED','CAPTURING','COMPLETED','FAILED'))) "
        "ORDER BY s.created_at DESC,s.id DESC",
        [root_key, environment, project_id, request_id, project_id, request_id, environment],
        ))
    if not scan_records:
        raise FolderSchemaError("FOLDER_SCHEMA_REQUEST_SCAN_REQUIRED",
                                "이 의뢰에 연결된 폴더 스키마 조사가 없습니다. 저장 규칙을 선택해 의뢰 폴더를 조사하세요.")
    selected_scan = scan_records[0]
    latest_created_at = selected_scan.get("created_at")
    latest_ties = [item for item in scan_records if item.get("created_at") == latest_created_at]
    if len({str(item["id"]) for item in latest_ties}) > 1:
        raise FolderSchemaError("FOLDER_SCHEMA_SCAN_AMBIGUOUS",
                                "같은 시각에 여러 폴더 조사가 이 의뢰에 연결되어 있습니다. 규칙을 선택해 다시 조사하세요.")
    scan_path = _normal(str(selected_scan.get("relative_path") or ""), allow_root=True)
    if not (_is_ancestor(scan_path, request_path) or _is_ancestor(request_path, scan_path)):
        raise FolderSchemaError("FOLDER_SCHEMA_SCAN_SCOPE_INVALID",
                                "최근 폴더 조사 범위가 선택한 의뢰와 겹치지 않습니다. 의뢰 폴더 또는 그 하위 폴더를 조사하세요.")
    if selected_scan.get("root_key") != root_key or selected_scan.get("environment") != environment:
        raise FolderSchemaError("FOLDER_SCHEMA_SCAN_INVALID", "선택한 폴더 조사의 저장소 또는 환경이 일치하지 않습니다.")
    if str(selected_scan.get("status")) != "COMPLETE":
        raise FolderSchemaError("FOLDER_SCHEMA_SCAN_INCOMPLETE", "최근 폴더 조사가 완료되지 않았습니다. 의뢰 폴더를 다시 조사하세요.")
    profile, rules = _profile_for_scan(selected_scan, environment)

    try:
        current_scan = folder_discovery_scan.scan(root, scan_path)
    except (OSError, ValueError, spdm_storage.SpdmStorageError) as exc:
        raise FolderSchemaError("FOLDER_SCHEMA_SCAN_UNAVAILABLE", "현재 의뢰 폴더를 안전하게 조사할 수 없습니다.", 422) from exc
    if current_scan.get("status") != "COMPLETE":
        raise FolderSchemaError("FOLDER_SCHEMA_SCAN_INCOMPLETE", "현재 의뢰 폴더를 안전하게 모두 조사할 수 없습니다.", 422)
    interpreted = folder_discovery_environment._interpret(
        current_scan["nodes"], root_key, environment, project_id, request_id, rules,
        seed_request_path=request_path,
    )
    request_folded = _fold(request_path)
    scoped_nodes = [node for node in interpreted if _is_ancestor(request_path, str(node["relative_path"]))]
    has_request_node = any(_fold(str(node["relative_path"])) == request_folded for node in scoped_nodes)
    scan_is_inside_request = (_fold(scan_path) != request_folded and _is_ancestor(request_path, scan_path))
    if not has_request_node and not scan_is_inside_request:
        raise FolderSchemaError("FOLDER_SCHEMA_SCAN_SCOPE_INVALID", "조사 트리에 의뢰 폴더가 없습니다.")

    confirmed_roles = _role_evidence(conn, root_key, project_id, request_id, environment,
                                     selected_scan, profile, request_path)
    by_path = {_fold(str(node["relative_path"])): node for node in scoped_nodes}
    for key, role in confirmed_roles.items():
        node = by_path.get(key)
        if node is None:
            continue
        node["role_kind"] = None if role["role_kind"] in {"CONTAINER", "EXCLUDE"} else role["role_kind"]
        node["status"] = role["status"]
        node["target_id"] = role.get("target_id") or node.get("target_id")
        node["name"] = role.get("name") or node.get("name")
        node["option_status"] = role.get("option_status") or node.get("option_status")
        node["confirmed"] = role["status"] in {"CONFIRMED", "CONTAINER", "EXCLUDED"}
        node["role_evidence_source"] = role["source"]
        node["role_source"] = role["source"]
        node["role_basis"] = role.get("role_basis") or node.get("role_basis")
    excluded = [str(item["relative_path"]) for item in scoped_nodes if item.get("status") == "EXCLUDED"]
    for node in scoped_nodes:
        if any(_is_ancestor(path, str(node["relative_path"])) for path in excluded):
            node["status"] = "EXCLUDED"
            node["role_kind"] = None
            node["confirmed"] = True
            node["role_source"] = "REGISTRATION"
        else:
            node.setdefault("confirmed", False)
            node.setdefault("role_source", "PROFILE")
    _add_hierarchy(scoped_nodes)
    return {
        "project_id": project_id,
        "request_id": request_id,
        "environment": environment,
        "request_relative_path": request_path,
        "profile": profile,
        "scan": {"id": str(selected_scan["id"]), "relative_path": scan_path,
                 "profile_id": profile["id"], "profile_revision": profile["revision"],
                 "status": str(selected_scan["status"])},
        "nodes": scoped_nodes,
        "confirmed_roles": confirmed_roles,
        "issues": current_scan.get("issues", []),
    }
