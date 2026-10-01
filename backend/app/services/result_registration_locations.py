"""Editable, schema-backed saved destinations for result registration.

These links are UI connection state. They are deliberately separate from the
path-role trace registry and from published result/capture history.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from ..database_connection import ConnectionLike, rows
from . import result_registration_paths as paths


def effective_assignment(schema: dict[str, Any], path_key: str) -> dict[str, Any] | None:
    """Return an explicit registered role or a confirmed profile interpretation."""
    node = next((item for item in schema.get("nodes", [])
                 if paths._root_casefold(str(item.get("relative_path", ""))) == path_key), None)
    if node and node.get("status") in {"EXCLUDED", "UNRESOLVED"}:
        # Current scan disposition must block a stale role snapshot from
        # making an excluded or unresolved path selectable again.
        return {"relative_path": node.get("relative_path"), "role_kind": node.get("role_kind"),
                "status": node.get("status"), "name": node.get("name"), "source": node.get("role_source")}
    assignment = schema.get("confirmed_roles", {}).get(path_key)
    if assignment:
        return assignment
    if not node:
        return None
    if node.get("status") == "CONTAINER" and node.get("role_source") == "DEFAULT" and not node.get("role_kind"):
        # Neutral containers may be traversed in the confirmed scan tree, but
        # this structural allowance never grants a semantic destination role.
        return {"relative_path": node.get("relative_path"), "role_kind": "CONTAINER",
                "status": "CONTAINER", "name": node.get("name"), "source": "STRUCTURE"}
    if (node.get("role_source") != "PROFILE" or node.get("role_basis") != "RULE" or
            node.get("status") not in {"CONFIRMED", "CONTAINER"}):
        return None
    return {"relative_path": node.get("relative_path"), "role_kind": node.get("role_kind") or "CONTAINER",
            "status": node.get("status"), "target_id": node.get("target_id"),
            "name": node.get("name"), "option_status": node.get("option_status"),
            "source": "PROFILE"}


def _resolver(conn: ConnectionLike, root, root_key: str, project_id: str,
              request_id: str, environment: str) -> dict[str, Any]:
    # Keep the resolver import local so schema bootstrap tools can import this
    # module while the independent Folder Schema service is being installed.
    from .folder_schema_resolver import FolderSchemaError, resolve_request_schema

    try:
        return resolve_request_schema(conn, root, root_key, project_id, request_id, environment)
    except FolderSchemaError as exc:
        raise paths.ResultRegistrationError(
            getattr(exc, "code", "RESULT_FOLDER_SCHEMA_REQUIRED"), str(exc)
        ) from exc


def _result_candidates(conn: ConnectionLike, root, root_key: str, project_id: str,
                       request_id: str, environment: str,
                       schema: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    schema = schema or _resolver(conn, root, root_key, project_id, request_id, environment)
    expected_parent_role = "EVALUATION" if environment == "USAGE" else "SCENE"
    nodes = {paths._root_casefold(str(node.get("relative_path", ""))): node
             for node in schema.get("nodes", [])}
    request_path = str(schema.get("request_relative_path") or "")
    root_id = paths.dashboard_capture._root_id(root)
    request_parts = PurePosixPath(request_path).parts if request_path else ()
    candidates: list[dict[str, Any]] = []
    for path_key, node in nodes.items():
        assignment = effective_assignment(schema, path_key)
        if not assignment or assignment.get("role_kind") != expected_parent_role:
            continue
        if assignment.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        parent_path = str(assignment.get("relative_path") or node.get("relative_path") or "")
        if not parent_path:
            continue
        if paths._has_owner_conflict(conn, root_id, root_key, parent_path,
                                    project_id, request_id, environment):
            continue
        # Prefer every direct Folder Schema RESULTS child, including a
        # profile-defined path/name. Only when no child was scanned may the
        # schema contract offer the conventional missing `results` leaf.
        result_nodes = []
        for child_key, child_node in nodes.items():
            if paths._root_casefold(str(child_node.get("parent_path") or "")) != paths._root_casefold(parent_path):
                continue
            child_assignment = effective_assignment(schema, child_key)
            if (child_assignment and child_assignment.get("role_kind") == "RESULTS" and
                    child_assignment.get("status") in {"CONFIRMED", "LINKED"}):
                result_nodes.append((child_node, child_assignment))
        destinations: list[tuple[str, bool]] = []
        if result_nodes:
            for result_node, result_assignment in result_nodes:
                result_path = str(result_assignment.get("relative_path") or result_node.get("relative_path") or "")
                try:
                    destination = paths._safe_existing(root, result_path)
                    if destination.is_dir() and not paths.spdm_storage._is_reparse(destination):
                        destinations.append((result_path, True))
                except paths.ResultRegistrationError:
                    continue
        else:
            result_path = f"{parent_path.rstrip('/')}/results"
            result_key = paths._root_casefold(result_path)
            result_node = nodes.get(result_key)
            if result_node:
                # A scanned but unresolved, excluded, or differently assigned
                # child must not be promoted to a result destination.
                continue
            try:
                destination = paths._safe_existing(root, result_path, allow_missing_leaf=True)
                if not destination.exists():
                    destinations.append((result_path, False))
            except paths.ResultRegistrationError:
                continue
        context = paths._empty_context()
        assignments: list[dict[str, Any]] = []
        parent_parts = PurePosixPath(parent_path).parts
        if (len(parent_parts) < len(request_parts) or
                tuple(part.casefold() for part in parent_parts[:len(request_parts)]) !=
                tuple(part.casefold() for part in request_parts)):
            continue
        prefix = list(request_parts)
        for name in parent_parts[len(request_parts):]:
            prefix.append(name)
            ancestor_path = PurePosixPath(*prefix).as_posix()
            ancestor = effective_assignment(schema, paths._root_casefold(ancestor_path))
            if not ancestor or ancestor.get("status") not in {"CONFIRMED", "LINKED", "CONTAINER"}:
                continue
            role = str(ancestor.get("role_kind") or "")
            if role in {"SIMULATION_CASE", "EVALUATION", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"}:
                assignments.append({"relative_path": ancestor_path, "role_kind": role,
                    "target_id": ancestor.get("target_id"), "raw_name": ancestor.get("name") or PurePosixPath(ancestor_path).name,
                    "option_status": ancestor.get("option_status")})
                context = paths._context_add(context, role, str(ancestor.get("name") or PurePosixPath(ancestor_path).name),
                                             ancestor_path, root_key, root_id,
                                             ancestor.get("option_status"), ancestor.get("target_id"))
        for result_path, exists in destinations:
            if paths._has_owner_conflict(conn, root_id, root_key, result_path,
                                         project_id, request_id, environment):
                continue
            candidates.append({
                "relative_path": result_path,
                "path_key": paths._root_casefold(result_path),
                "schema_parent_path": parent_path,
                "schema_role_kind": expected_parent_role,
                "schema_target_id": assignment.get("target_id"),
                "schema_scan_id": schema["scan"]["id"],
                "schema_profile_id": schema["profile"]["id"],
                "schema_profile_revision": int(schema["profile"]["revision"]),
                "exists": exists,
                "context": context,
                "_assignments": assignments,
            })
    candidates.sort(key=lambda item: (item["relative_path"].casefold(), item["relative_path"]))
    return candidates


def _scope_data(conn: ConnectionLike, project_id: str, request_id: str,
                environment: str) -> tuple[dict[str, Any], Any, str, str]:
    environment = paths._env(environment)
    root, _root_id, root_key = paths._root(conn)
    schema = _resolver(conn, root, root_key, project_id, request_id, environment)
    if (str(schema.get("project_id")) != project_id or str(schema.get("request_id")) != request_id or
            str(schema.get("environment")) != environment):
        raise paths.ResultRegistrationError("RESULT_FOLDER_SCHEMA_STALE", "선택한 프로젝트·의뢰·환경과 확인된 폴더 구조가 일치하지 않습니다.")
    request_path = paths._relative(str(schema.get("request_relative_path") or ""))
    scope = {"project_id": project_id, "request_id": request_id, "environment": environment,
             "request_relative_path": request_path, "_schema": schema}
    return scope, root, root_key, environment


def _base_scope_data(conn: ConnectionLike, project_id: str, request_id: str,
                     environment: str) -> tuple[dict[str, Any], Any, str, str]:
    """Validate the business owner and current storage root without requiring a live schema."""
    environment = paths._env(environment)
    owner = conn.execute(
        "SELECT 1 FROM projects p JOIN analysis_requests r ON r.project_id=p.id WHERE p.id=? AND r.id=?",
        [project_id, request_id],
    ).fetchone()
    if not owner:
        raise paths.ResultRegistrationError("RESULT_CONTEXT_INVALID", "기존 프로젝트와 의뢰의 연결을 확인할 수 없습니다.")
    root, _root_id, root_key = paths._root(conn)
    return {"project_id": project_id, "request_id": request_id, "environment": environment,
            "request_relative_path": ""}, root, root_key, environment


def list_links(conn: ConnectionLike, project_id: str, request_id: str,
               environment: str) -> dict[str, Any]:
    scope, root, root_key, environment = _base_scope_data(conn, project_id, request_id, environment)
    stored = rows(conn.execute(
        "SELECT id,root_key,relative_path,path_key,schema_parent_path,schema_role_kind,schema_target_id,"
        "schema_scan_id,schema_profile_id,schema_profile_revision,revision,created_by,created_at,updated_by,updated_at "
        "FROM result_registration_location_links WHERE project_id=? AND request_id=? AND environment=? "
        "ORDER BY relative_path,id",
        [project_id, request_id, environment],
    ))
    try:
        schema = _resolver(conn, root, root_key, project_id, request_id, environment)
        candidates = _result_candidates(conn, root, root_key, project_id, request_id, environment, schema)
        candidate_by_key = {item["path_key"]: item for item in candidates}
        schema_error = None
        request_relative_path = str(schema["request_relative_path"])
    except paths.ResultRegistrationError as exc:
        candidates = []
        candidate_by_key = {}
        schema_error = {"code": exc.code, "message": str(exc)}
        request_relative_path = ""
    links = []
    for item in stored:
        current = candidate_by_key.get(paths._root_casefold(str(item["relative_path"])))
        links.append({
            **item,
            "is_current": bool(str(item.get("root_key") or "") == root_key and current and
                str(item["schema_parent_path"]) == str(current["schema_parent_path"]) and
                str(item["schema_role_kind"]) == str(current["schema_role_kind"]) and
                str(item.get("schema_target_id") or "") == str(current.get("schema_target_id") or "") and
                str(item["schema_scan_id"]) == str(current["schema_scan_id"]) and
                str(item["schema_profile_id"]) == str(current["schema_profile_id"]) and
                int(item["schema_profile_revision"]) == int(current["schema_profile_revision"])),
        })
    return {
        "storage_root_id": paths.dashboard_capture._root_id(root),
        "project_id": project_id,
        "request_id": request_id,
        "environment": environment,
        "request_relative_path": request_relative_path,
        "candidates": [{key: value for key, value in candidate.items() if not key.startswith("_")}
                       for candidate in candidates],
        "links": links,
        "schema_error": schema_error,
    }


def _locked_candidates(conn: ConnectionLike, project_id: str, request_id: str,
                       environment: str) -> tuple[dict[str, Any], Any, str, list[dict[str, Any]]]:
    scope, root, root_key, environment = _scope_data(conn, project_id, request_id, environment)
    schema = scope.pop("_schema")
    # A shared row lock keeps profile changes from racing the provenance check
    # and insert/update on PostgreSQL. The DuckDB adapter serializes requests.
    if getattr(conn, "backend", None) == "postgresql":
        profile_id = schema.get("profile", {}).get("id")
        if profile_id:
            conn.execute("SELECT id FROM folder_environment_profiles WHERE id=? FOR SHARE", [profile_id]).fetchone()
            schema = _resolver(conn, root, root_key, project_id, request_id, environment)
    candidates = _result_candidates(conn, root, root_key, project_id, request_id, environment, schema)
    return scope, root, root_key, candidates


def _find_candidate(candidates: list[dict[str, Any]], relative_path: str) -> dict[str, Any]:
    normalized = paths._relative(relative_path)
    path_key = paths._root_casefold(normalized)
    for candidate in candidates:
        if candidate["path_key"] == path_key:
            return candidate
    raise paths.ResultRegistrationError(
        "RESULT_LOCATION_SCHEMA_INVALID",
        "결과 위치는 선택한 프로젝트·의뢰·환경의 확정된 Evaluation/Scene 아래 results 폴더여야 합니다.",
    )


def resolve_result_context(conn: ConnectionLike, project_id: str, request_id: str,
                           environment: str, case_relative_path: str,
                           result_relative_path: str) -> dict[str, Any] | None:
    """Resolve a result folder through Folder Schema; None means legacy-only path."""
    scope, root, root_key, environment = _scope_data(conn, project_id, request_id, environment)
    schema = scope.pop("_schema")
    candidates = _result_candidates(conn, root, root_key, project_id, request_id, environment, schema)
    normalized = paths._relative(result_relative_path)
    candidate = next((item for item in candidates if item["relative_path"] == normalized), None)
    if not candidate:
        return None
    if not candidate["exists"]:
        raise paths.ResultRegistrationError("SPDM_FOLDER_MISSING", "선택한 결과 위치가 아직 준비되지 않았습니다.")
    if paths._root_casefold(str(candidate["context"].get("simulation_case", {}).get("relative_path") or "")) != paths._root_casefold(case_relative_path):
        raise paths.ResultRegistrationError("RESULT_CONTEXT_CHANGED", "선택한 결과 위치가 다른 해석 Case에 속합니다.")
    return {"scope": scope, "root": root, "root_id": paths.dashboard_capture._root_id(root),
            "root_key": root_key, "context": candidate["context"],
            "assignments": candidate["_assignments"],
            "case_relative_path": case_relative_path,
            "result_relative_path": normalized}


def create_link(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
                relative_path: str, actor: str) -> dict[str, Any]:
    normalized = paths._relative(relative_path)
    scope, root, root_key, candidates = _locked_candidates(conn, project_id, request_id, environment)
    paths._lock_path(conn, root_key, normalized)
    candidate = _find_candidate(candidates, normalized)
    existing = conn.execute(
        "SELECT id FROM result_registration_location_links WHERE root_key=? AND project_id=? AND request_id=? AND environment=? AND path_key=?",
        [root_key, project_id, request_id, scope["environment"], candidate["path_key"]],
    ).fetchone()
    if existing:
        raise paths.ResultRegistrationError("RESULT_LOCATION_LINK_EXISTS", "이 결과 위치는 이미 연결되어 있습니다.")
    paths._owner_conflict(conn, paths.dashboard_capture._root_id(root), root_key,
                          candidate["schema_parent_path"], project_id, request_id, scope["environment"])
    paths._owner_conflict(conn, paths.dashboard_capture._root_id(root), root_key,
                          candidate["relative_path"], project_id, request_id, scope["environment"])
    now = paths._now()
    link_id = "result-registration-location-" + uuid4().hex
    conn.execute(
        "INSERT INTO result_registration_location_links "
        "(id,root_key,project_id,request_id,environment,relative_path,path_key,schema_parent_path,schema_role_kind,"
        "schema_target_id,schema_scan_id,schema_profile_id,schema_profile_revision,revision,created_by,created_at,updated_by,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
        [link_id, root_key, project_id, request_id, scope["environment"], candidate["relative_path"],
         candidate["path_key"], candidate["schema_parent_path"], candidate["schema_role_kind"],
         candidate.get("schema_target_id"), candidate["schema_scan_id"], candidate["schema_profile_id"],
         candidate["schema_profile_revision"], actor, now, actor, now],
    )
    return {"id": link_id, "relative_path": candidate["relative_path"], "revision": 1,
            "schema_parent_path": candidate["schema_parent_path"],
            "schema_role_kind": candidate["schema_role_kind"], "is_current": True}


def update_link(conn: ConnectionLike, link_id: str, project_id: str, request_id: str,
                environment: str, relative_path: str, expected_revision: int,
                actor: str) -> dict[str, Any]:
    normalized = paths._relative(relative_path)
    scope, root, root_key, candidates = _locked_candidates(conn, project_id, request_id, environment)
    current_row = conn.execute(
        "SELECT root_key,relative_path,path_key,revision FROM result_registration_location_links "
        "WHERE id=? AND project_id=? AND request_id=? AND environment=?" +
        (" FOR UPDATE" if getattr(conn, "backend", None) == "postgresql" else ""),
        [link_id, project_id, request_id, scope["environment"]],
    ).fetchone()
    if not current_row:
        raise paths.ResultRegistrationError("RESULT_LOCATION_LINK_NOT_FOUND", "저장한 결과 위치 연결을 찾을 수 없습니다.")
    current_root_key, current_path, current_key, current_revision = (
        str(current_row[0]), str(current_row[1]), str(current_row[2]), int(current_row[3]))
    if current_revision != expected_revision:
        raise paths.ResultRegistrationError("RESULT_LOCATION_LINK_STALE", "다른 화면에서 연결을 수정했습니다. 목록을 새로 고친 뒤 다시 시도하세요.")
    candidate = _find_candidate(candidates, normalized)
    lock_paths = {(current_root_key, current_path), (root_key, candidate["relative_path"])}
    for lock_root_key, path in sorted(lock_paths, key=lambda item: (item[0], paths._root_casefold(item[1]))):
        paths._lock_path(conn, lock_root_key, path)
    paths._owner_conflict(conn, paths.dashboard_capture._root_id(root), root_key,
                          candidate["schema_parent_path"], project_id, request_id, scope["environment"])
    paths._owner_conflict(conn, paths.dashboard_capture._root_id(root), root_key,
                          candidate["relative_path"], project_id, request_id, scope["environment"])
    duplicate = conn.execute(
        "SELECT id FROM result_registration_location_links WHERE root_key=? AND project_id=? AND request_id=? AND environment=? AND path_key=? AND id<>?",
        [root_key, project_id, request_id, scope["environment"], candidate["path_key"], link_id],
    ).fetchone()
    if duplicate:
        raise paths.ResultRegistrationError("RESULT_LOCATION_LINK_EXISTS", "이 결과 위치는 이미 연결되어 있습니다.")
    revision = expected_revision + 1
    conn.execute(
        "UPDATE result_registration_location_links SET root_key=?,relative_path=?,path_key=?,schema_parent_path=?,schema_role_kind=?,"
        "schema_target_id=?,schema_scan_id=?,schema_profile_id=?,schema_profile_revision=?,revision=?,updated_by=?,updated_at=? "
        "WHERE id=? AND root_key=? AND project_id=? AND request_id=? AND environment=? AND revision=?",
        [root_key, candidate["relative_path"], candidate["path_key"], candidate["schema_parent_path"],
         candidate["schema_role_kind"], candidate.get("schema_target_id"), candidate["schema_scan_id"],
         candidate["schema_profile_id"], candidate["schema_profile_revision"], revision, actor, paths._now(),
         link_id, current_root_key, project_id, request_id, scope["environment"], expected_revision],
    )
    check = conn.execute(
        "SELECT revision FROM result_registration_location_links WHERE id=? AND project_id=? AND request_id=? AND environment=?",
        [link_id, project_id, request_id, scope["environment"]],
    ).fetchone()
    if not check or int(check[0]) != revision:
        raise paths.ResultRegistrationError("RESULT_LOCATION_LINK_STALE", "다른 화면에서 연결을 수정했습니다. 목록을 새로 고친 뒤 다시 시도하세요.")
    return {"id": link_id, "relative_path": candidate["relative_path"], "revision": revision,
            "schema_parent_path": candidate["schema_parent_path"],
            "schema_role_kind": candidate["schema_role_kind"], "is_current": True}


def delete_link(conn: ConnectionLike, link_id: str, project_id: str, request_id: str,
                environment: str, expected_revision: int) -> dict[str, Any]:
    environment = paths._env(environment)
    owner = conn.execute(
        "SELECT 1 FROM projects p JOIN analysis_requests r ON r.project_id=p.id WHERE p.id=? AND r.id=?",
        [project_id, request_id],
    ).fetchone()
    if not owner:
        raise paths.ResultRegistrationError("RESULT_CONTEXT_INVALID", "기존 프로젝트와 의뢰의 연결을 확인할 수 없습니다.")
    row = conn.execute(
        "SELECT root_key,relative_path,revision FROM result_registration_location_links "
        "WHERE id=? AND project_id=? AND request_id=? AND environment=?" +
        (" FOR UPDATE" if getattr(conn, "backend", None) == "postgresql" else ""),
        [link_id, project_id, request_id, environment],
    ).fetchone()
    if not row:
        raise paths.ResultRegistrationError("RESULT_LOCATION_LINK_NOT_FOUND", "저장한 결과 위치 연결을 찾을 수 없습니다.")
    if int(row[2]) != expected_revision:
        raise paths.ResultRegistrationError("RESULT_LOCATION_LINK_STALE", "다른 화면에서 연결을 수정했습니다. 목록을 새로 고친 뒤 다시 시도하세요.")
    stored_root_key = str(row[0])
    paths._lock_path(conn, stored_root_key, str(row[1]))
    conn.execute(
        "DELETE FROM result_registration_location_links WHERE id=? AND root_key=? AND project_id=? AND request_id=? AND environment=? AND revision=?",
        [link_id, stored_root_key, project_id, request_id, environment, expected_revision],
    )
    return {"id": link_id, "deleted": True}
