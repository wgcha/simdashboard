"""Isolated staged result uploads, review, approval, and capture publication."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from uuid import uuid4

from ..database_connection import ConnectionLike, rows
from ..media_policy import validate_media_metadata
from . import dashboard_capture, result_registration_locations, result_registration_paths as paths, spdm_storage, usage_source_review


MAX_FILE_BYTES = paths.MAX_FILE_BYTES
MAX_TOTAL_BYTES = paths.MAX_TOTAL_BYTES
MAX_FILES = paths.MAX_FILES
_EXTENSION_MEDIA = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}
_USAGE_SELECTION = {"json": True, "csv": True, "video": True, "image": True}
_PUBLIC_CONTEXT_KEYS = ("simulation_case", "evaluation", "load_case", "execution_run", "run_option", "scene")
_MUTABLE_DRAFT_STATES = {"DRAFT", "INSPECTED", "APPROVED", "PUBLISH_FAILED"}


class ResultRegistrationError(paths.ResultRegistrationError):
    """A controlled staged-registration failure."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _decode(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _begin(conn: ConnectionLike) -> None:
    if getattr(conn, "backend", None) != "postgresql":
        conn.execute("BEGIN TRANSACTION")


def _commit(conn: ConnectionLike) -> None:
    conn.execute("COMMIT")


def _rollback(conn: ConnectionLike) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception:
        pass


def _draft_row(conn: ConnectionLike, draft_id: str, *, lock: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE" if lock and getattr(conn, "backend", None) == "postgresql" else ""
    result = rows(conn.execute(
        "SELECT id,project_id,request_id,environment,storage_root_id,case_relative_path,result_relative_path,"
        "context_json,manifest_json,inspection_json,source_revision,inspection_revision,approval_json,"
        "publish_idempotency_key,case_id,capture_id,status,mirror_status,error_json,revision,created_by,"
        "updated_by,approved_by,published_at,created_at,updated_at FROM result_registration_drafts WHERE id=?" + suffix,
        [draft_id],
    ))
    if not result:
        raise ResultRegistrationError("RESULT_DRAFT_NOT_FOUND", "결과 등록 초안을 찾을 수 없습니다.")
    row = result[0]
    for key in ("context_json", "manifest_json", "inspection_json", "approval_json", "error_json"):
        row[key] = _decode(row[key]) if row[key] is not None else None
    return row


def draft_scope(conn: ConnectionLike, draft_id: str) -> dict[str, str]:
    row = _draft_row(conn, draft_id)
    return {"project_id": str(row["project_id"]), "request_id": str(row["request_id"])}


def _file_rows(conn: ConnectionLike, draft_id: str) -> list[dict[str, Any]]:
    found = rows(conn.execute(
        "SELECT relative_path,sha256,size_bytes,media_type,content FROM result_registration_files "
        "WHERE draft_id=? ORDER BY relative_path", [draft_id]
    ))
    for item in found:
        item["content"] = bytes(item["content"])
        item["size_bytes"] = int(item["size_bytes"])
    return found


def _public_context(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value.get(key) for key in _PUBLIC_CONTEXT_KEYS}


def _current_target(
    conn: ConnectionLike,
    *,
    project_id: str,
    request_id: str,
    environment: str,
    case_relative_path: str,
    result_relative_path: str,
    expected_root_id: str | None = None,
    expected_context: dict[str, Any] | None = None,
    require_schema_roles: bool = False,
) -> dict[str, Any]:
    root, root_id, root_key = paths._root(conn)
    schema = None
    if require_schema_roles:
        # New drafts follow the same request boundary as step 02. The SPDM
        # parent/registry tables are only a compatibility fallback for old
        # drafts and are not a prerequisite for a Folder Schema request.
        scope, _schema_root, _schema_root_key, _environment = result_registration_locations._scope_data(
            conn, project_id, request_id, environment)
        schema = scope.pop("_schema")
    else:
        # Existing draft inspection/publication must remain readable when its
        # original schema has since changed or disappeared. Prefer the current
        # schema scope, then retain the established owner-checked scope fallback.
        try:
            scope, _schema_root, _schema_root_key, _environment = result_registration_locations._scope_data(
                conn, project_id, request_id, environment)
            scope.pop("_schema")
        except ResultRegistrationError:
            scope = paths._scope(conn, project_id, request_id, environment)
    if expected_root_id and expected_root_id != root_id:
        raise ResultRegistrationError("RESULT_ROOT_CHANGED", "등록 시작 뒤 SPDM 저장소가 변경되었습니다. 다시 검수하세요.")
    case_relative_path = paths._relative(case_relative_path)
    result_relative_path = paths._relative(result_relative_path)
    if (paths._is_final_branch(str(scope.get("request_relative_path") or ""), case_relative_path)
            or paths._is_final_branch(str(scope.get("request_relative_path") or ""), result_relative_path)
            or (schema is not None and (
                result_registration_locations.folder_schema_locations.is_final_branch(schema, case_relative_path)
                or result_registration_locations.folder_schema_locations.is_final_branch(schema, result_relative_path)))):
        raise ResultRegistrationError("RESULT_FINAL_BRANCH_BLOCKED", "Final 폴더는 일반 결과 등록 위치로 사용할 수 없습니다.")
    case_prefix = case_relative_path.rstrip("/") + "/"
    if not result_relative_path.casefold().startswith(case_prefix.casefold()):
        raise ResultRegistrationError("RESULT_PATH_OUTSIDE_CASE", "결과 경로가 선택한 Simulation Case 안에 없습니다.")
    schema_target = None
    if schema is not None:
        candidates = result_registration_locations._result_candidates(
            conn, root, root_key, project_id, request_id, scope["environment"], schema)
        candidate = next((item for item in candidates if item["relative_path"] == result_relative_path), None)
        if candidate:
            if not candidate["exists"]:
                raise ResultRegistrationError("SPDM_FOLDER_MISSING", "선택한 결과 위치가 아직 준비되지 않았습니다.")
            if (paths._root_casefold(str(candidate["context"].get("simulation_case", {}).get("relative_path") or "")) !=
                    paths._root_casefold(case_relative_path)):
                raise ResultRegistrationError("RESULT_CONTEXT_CHANGED", "선택한 결과 위치가 다른 해석 Case에 속합니다.")
            schema_target = {"scope": scope, "root": root, "root_id": root_id, "root_key": root_key,
                             "context": candidate["context"], "assignments": candidate["_assignments"],
                             "folder_schema_locations": schema.get("locations", []),
                             "folder_schema_blocked_paths": result_registration_locations.folder_schema_locations.blocked_paths_for_case(
                                 schema, case_relative_path,
                             ),
                             "folder_schema_scoped": True,
                             "folder_schema_snapshot_id": schema.get("folder_schema_snapshot_id"),
                             "case_relative_path": case_relative_path,
                             "result_relative_path": result_relative_path}
    else:
        try:
            schema_target = result_registration_locations.resolve_result_context(
                conn, project_id, request_id, scope["environment"], case_relative_path, result_relative_path,
            )
        except ResultRegistrationError:
            if require_schema_roles:
                raise
    if schema_target:
        case_context = schema_target["context"]
        context = schema_target["context"]
        result_nodes = [*schema_target["assignments"],
                        {"relative_path": result_relative_path, "role_kind": "RESULTS"}]
    else:
        if require_schema_roles:
            # A path created through the explicit path builder may not yet be
            # present as a semantic node in the live Folder Schema scan. Its
            # semantic roles still need explicit prepare confirmations, and a
            # live schema exclusion or conflicting semantic role wins.
            context, result_nodes = paths._trace_path(conn, root, root_id, root_key, scope, result_relative_path)
            case_context, _case_nodes = paths._trace_path(conn, root, root_id, root_key, scope, case_relative_path)
            for node in result_nodes:
                role = str(node.get("role_kind") or "")
                if role not in {"SIMULATION_CASE", "EVALUATION", "SCENE"}:
                    continue
                evidence = result_registration_locations.effective_assignment(
                    schema, paths._root_casefold(str(node["relative_path"]))) if schema else None
                if evidence and evidence.get("source") != "STRUCTURE" and (
                        evidence.get("status") in {"EXCLUDED", "UNRESOLVED"} or evidence.get("role_kind") != role):
                    raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_STALE", "결과 경로의 역할이 현재 Folder Schema에서 제외되었거나 달라졌습니다.")
                found = conn.execute(
                    "SELECT 1 FROM result_registration_paths WHERE root_key=? AND path_key=? AND project_id=? AND request_id=? AND environment=? AND role_kind=?",
                    [root_key, paths._root_casefold(str(node["relative_path"])), project_id, request_id,
                     scope["environment"], role],
                ).fetchone()
                if not found:
                    raise ResultRegistrationError("RESULT_FOLDER_SCHEMA_REQUIRED", "결과 경로의 역할이 Folder Schema 또는 명시적인 저장 위치 확인에 연결되지 않았습니다.")
        else:
            case_context, _case_nodes = paths._trace_path(conn, root, root_id, root_key, scope, case_relative_path)
            context, result_nodes = paths._trace_path(conn, root, root_id, root_key, scope, result_relative_path)
    if not case_context.get("simulation_case") or case_context["simulation_case"].get("relative_path", "").casefold() != case_relative_path.casefold():
        raise ResultRegistrationError("RESULT_CASE_INVALID", "선택한 경로가 연결된 Simulation Case가 아닙니다.")
    if context.get("simulation_case") != case_context.get("simulation_case"):
        raise ResultRegistrationError("RESULT_CONTEXT_CHANGED", "선택한 결과 폴더의 Simulation Case 문맥이 변경되었습니다.")
    if not result_nodes or result_nodes[-1].get("role_kind") != "RESULTS":
        raise ResultRegistrationError("RESULT_FOLDER_INVALID", "선택한 폴더는 확인된 results 경로여야 합니다.")
    expected_parent_role = "EVALUATION" if scope["environment"] == "USAGE" else "SCENE"
    if len(result_nodes) < 2 or result_nodes[-2].get("role_kind") != expected_parent_role:
        raise ResultRegistrationError("RESULT_FOLDER_INVALID", "결과 폴더는 선택한 평가 항목 또는 Scene 바로 아래여야 합니다.")
    if expected_context is not None and _public_context(context) != _public_context(expected_context):
        raise ResultRegistrationError("RESULT_CONTEXT_CHANGED", "검수한 업무 문맥이 현재 경로 연결과 달라졌습니다. 다시 검수하세요.")
    target = paths._safe_existing(root, result_relative_path)
    if not target.is_dir():
        raise ResultRegistrationError("SPDM_FOLDER_UNAVAILABLE", "선택한 결과 경로가 폴더가 아닙니다.")
    paths._owner_conflict(conn, root_id, root_key, case_relative_path, project_id, request_id, scope["environment"])
    paths._owner_conflict(conn, root_id, root_key, result_relative_path, project_id, request_id, scope["environment"])
    relevant = {"SIMULATION_CASE", "LOAD_CASE", "EXECUTION_RUN", "RUN_OPTION", "SCENE"}
    assignments = [dict(node) for node in result_nodes if node.get("role_kind") in relevant]
    return {
        "scope": scope,
        "root": root,
        "root_id": root_id,
        "root_key": root_key,
        "context": _public_context(context),
        "assignments": assignments,
        "folder_schema_locations": schema_target.get("folder_schema_locations", []) if schema_target else [],
        "folder_schema_blocked_paths": schema_target.get("folder_schema_blocked_paths", []) if schema_target else [],
        "folder_schema_scoped": bool(schema_target and schema_target.get("folder_schema_scoped")),
        "folder_schema_snapshot_id": schema_target.get("folder_schema_snapshot_id") if schema_target else None,
        "case_relative_path": case_relative_path,
        "result_relative_path": result_relative_path,
    }


def targets(conn: ConnectionLike, environment: str, principal_projects: set[str] | None = None) -> dict[str, Any]:
    result = paths.targets(conn, environment, principal_projects)
    for target in result.get("targets", []):
        for case in target.get("cases", []):
            case["can_prepare"] = False
            case["suggested_relative_path"] = None
    return result


def target_project_ids(conn: ConnectionLike) -> set[str]:
    return {str(item["project_id"]) for item in rows(conn.execute("SELECT DISTINCT project_id FROM analysis_requests"))}


def folders(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
            parent_relative_path: str | None = None) -> dict[str, Any]:
    return paths.folders(conn, project_id, request_id, environment, parent_relative_path)


def prepare_folders(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
                    parent_relative_path: str | None, segments: list[dict[str, str]],
                    confirm_create: bool, actor: str) -> dict[str, Any]:
    parent = parent_relative_path
    if parent is None:
        parent = paths._scope(conn, project_id, request_id, environment)["request_relative_path"]
    result = paths.prepare_folders(conn, project_id, request_id, environment, parent, segments, confirm_create, actor)
    if confirm_create and result.get("created"):
        result["schema_refresh"] = _refresh_schema(conn, project_id, request_id, environment, actor)
    else:
        result["schema_refresh"] = {"status": "UNCHANGED", "message": None}
    return result


def _refresh_schema(conn: ConnectionLike, project_id: str, request_id: str,
                    environment: str, actor: str, *, capture_cases: bool = True) -> dict[str, Any]:
    from . import folder_discovery_environment, folder_schema_resolver

    root, _root_id, root_key = paths._root(conn)
    previous = folder_schema_resolver.active_refresh_snapshot(conn, root_key, project_id, request_id, environment)
    try:
        result = folder_discovery_environment.refresh_scope(
            conn, root, project_id, request_id, environment, actor,
            capture_cases=capture_cases,
        )
        if result["status"] == "CONFLICT":
            return {"status": "FAILED", "code": "FOLDER_SCHEMA_ROLE_CONFLICT",
                    "message": "폴더 역할이 모호하여 새로고침을 활성화하지 못했습니다.",
                    "prior_snapshot_id": result.get("snapshot_id")}
        return {"status": result["status"], "snapshot_id": result["snapshot_id"], "message": None}
    except Exception as exc:
        code = str(getattr(exc, "code", "FOLDER_SCHEMA_REFRESH_FAILED"))
        message = str(exc) if isinstance(exc, folder_schema_resolver.FolderSchemaError) else "폴더 스키마 새로고침에 실패했습니다. 기존 등록은 유지됩니다."
        return {"status": "FAILED", "code": code, "message": message,
                "prior_snapshot_id": str(previous["id"]) if previous else None}


def reconcile_schema_refresh_failures(conn: ConnectionLike, project_id: str, request_id: str,
                                      environment: str, snapshot_id: str, status: str,
                                      actor: str) -> int:
    """Clear stored publication warnings after a later scoped refresh succeeds."""
    from . import folder_discovery_environment, folder_schema_resolver

    root, _root_id, root_key = paths._root(conn)
    current = folder_schema_resolver.active_refresh_snapshot(conn, root_key, project_id, request_id, environment)
    if not current or str(current["id"]) != snapshot_id:
        return 0
    drafts = rows(conn.execute(
        "SELECT id,approval_json FROM result_registration_drafts "
        "WHERE project_id=? AND request_id=? AND environment=? AND approval_json IS NOT NULL",
        [project_id, request_id, environment],
    ))
    changed = 0
    for draft in drafts:
        approval = draft["approval_json"]
        if isinstance(approval, str):
            approval = json.loads(approval)
        publication = (approval or {}).get("publication") or {}
        previous = publication.get("schema_refresh") or {}
        if previous.get("status") != "FAILED":
            continue
        publication["schema_refresh"] = {
            "status": status, "snapshot_id": snapshot_id,
            "message": None, "recovered_from_failure": True,
        }
        approval["publication"] = publication
        conn.execute(
            "UPDATE result_registration_drafts SET approval_json=?,revision=revision+1,updated_by=?,updated_at=? WHERE id=?",
            [_json(approval), actor, _now(), str(draft["id"])],
        )
        _event(conn, str(draft["id"]), "SCHEMA_REFRESH_RECOVERED",
               {"status": status, "snapshot_id": snapshot_id}, actor)
        changed += 1
    return changed


def _manifest_input(items: Iterable[dict[str, Any]], result_relative_path: str) -> list[dict[str, Any]]:
    supplied = list(items)
    if not supplied or len(supplied) > MAX_FILES:
        raise ResultRegistrationError("RESULT_FILE_COUNT_LIMIT", f"파일은 1~{MAX_FILES}개까지 등록할 수 있습니다.")
    seen: set[str] = set()
    total = 0
    manifest: list[dict[str, Any]] = []
    for raw in supplied:
        local_path = paths._relative(str(raw.get("relative_path") or ""))
        if len(local_path) > 1024 or len(PurePosixPath(local_path).parts) > 12:
            raise ResultRegistrationError("RESULT_FILE_PATH_INVALID", "파일 상대 경로가 너무 깁니다.")
        folded = local_path.casefold()
        if folded in seen:
            raise ResultRegistrationError("RESULT_FILE_PATH_DUPLICATE", "같은 파일 경로를 두 번 지정할 수 없습니다.")
        seen.add(folded)
        suffix = PurePosixPath(local_path).suffix.casefold()
        media_type = _EXTENSION_MEDIA.get(suffix)
        if media_type is None:
            raise ResultRegistrationError("RESULT_FILE_TYPE_UNSUPPORTED", "CSV, JSON, JPG, PNG, MP4, WEBM 파일만 등록할 수 있습니다.")
        size = raw.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_FILE_BYTES:
            raise ResultRegistrationError("RESULT_FILE_SIZE_LIMIT", "파일은 32 MiB 이하여야 합니다.")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ResultRegistrationError("RESULT_TOTAL_SIZE_LIMIT", "초안의 전체 파일은 256 MiB 이하여야 합니다.")
        supplied_hash = raw.get("sha256")
        if supplied_hash is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", str(supplied_hash)):
            raise ResultRegistrationError("RESULT_FILE_HASH_INVALID", "SHA-256 값 형식이 올바르지 않습니다.")
        declared_type = str(raw.get("media_type") or "").strip().casefold()
        if declared_type and declared_type != media_type:
            raise ResultRegistrationError("RESULT_MEDIA_TYPE_INVALID", "파일 확장자와 미디어 형식이 일치하지 않습니다.")
        manifest.append({"relative_path": local_path, "size": size,
                         "sha256": str(supplied_hash).lower() if supplied_hash else None,
                         # Retain the optional client assertion separately from
                         # the server-canonical digest. Replacements are allowed
                         # only while mutable and will invalidate inspection;
                         # the original client assertion must not become an
                         # accidental pin to the first uploaded bytes.
                         "client_sha256": str(supplied_hash).lower() if supplied_hash else None,
                         "media_type": media_type, "upload_status": "PENDING", "inspection_status": "PENDING"})
    return sorted(manifest, key=lambda item: item["relative_path"].casefold())


def _event(conn: ConnectionLike, draft_id: str, action: str, detail: dict[str, Any], actor: str) -> None:
    conn.execute(
        "INSERT INTO result_registration_events(id,draft_id,action,detail_json,actor,occurred_at) VALUES(?,?,?,?,?,?)",
        ["result-registration-event-" + uuid4().hex, draft_id, action, _json(detail), actor, _now()],
    )


def create_draft(conn: ConnectionLike, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    project_id, request_id = str(payload["project_id"]), str(payload["request_id"])
    environment = paths._env(str(payload["environment"]))
    target = _current_target(conn, project_id=project_id, request_id=request_id, environment=environment,
                             case_relative_path=str(payload["case_relative_path"]), result_relative_path=str(payload["result_relative_path"]),
                             expected_context=payload.get("context") or {}, require_schema_roles=True)
    manifest = _manifest_input(payload.get("files") or [], target["result_relative_path"])
    draft_id = "result-registration-draft-" + uuid4().hex
    now = _now()
    source_revision = _hash({"context": target["context"], "environment": environment,
                              "case_relative_path": target["case_relative_path"],
                              "result_relative_path": target["result_relative_path"], "manifest": manifest})
    stored_context = {**target["context"], "_registration": {"hierarchy_assignments": target["assignments"]}}
    _begin(conn)
    try:
        conn.execute("""INSERT INTO result_registration_drafts
            (id,project_id,request_id,environment,storage_root_id,case_relative_path,result_relative_path,
             context_json,manifest_json,source_revision,status,revision,created_by,updated_by,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,'DRAFT',1,?,?,?,?)""",
            [draft_id, project_id, request_id, environment, target["root_id"], target["case_relative_path"],
             target["result_relative_path"], _json(stored_context), _json(manifest), source_revision,
             actor, actor, now, now])
        _event(conn, draft_id, "DRAFT_CREATED", {"environment": environment, "file_count": len(manifest),
                                                   "source_revision": source_revision}, actor)
        _commit(conn)
    except BaseException:
        _rollback(conn)
        raise
    return {"draft_id": draft_id, "status": "DRAFT", "inspection_revision": None}


def _load_declared(row: dict[str, Any]) -> list[dict[str, Any]]:
    value = row["manifest_json"] or []
    return [dict(item) for item in value]


def _read_manifest(row: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = _load_declared(row)
    inspected = (row.get("inspection_json") or {}).get("manifest") or []
    statuses = {str(item.get("relative_path") or "").casefold(): item.get("inspection_status") for item in inspected}
    for item in manifest:
        item["inspection_status"] = statuses.get(str(item.get("relative_path") or "").casefold(), "PENDING")
    return manifest


def _source_revision(row: dict[str, Any], manifest: list[dict[str, Any]]) -> str:
    context = row["context_json"] or {}
    return _hash({"context": _public_context(context), "environment": row["environment"],
                  "case_relative_path": row["case_relative_path"], "result_relative_path": row["result_relative_path"],
                  "manifest": manifest})


def upload_files(conn: ConnectionLike, draft_id: str, files: list[tuple[str, bytes]], actor: str) -> dict[str, Any]:
    if not files:
        raise ResultRegistrationError("RESULT_UPLOAD_EMPTY", "업로드할 파일이 없습니다.")
    if len(files) > MAX_FILES:
        raise ResultRegistrationError("RESULT_FILE_COUNT_LIMIT", f"한 번에 업로드할 수 있는 파일은 {MAX_FILES}개입니다.")
    seen: set[str] = set()
    total_request = 0
    normalized: list[tuple[str, bytes]] = []
    for relative, content in files:
        path = paths._relative(str(relative))
        if path.casefold() in seen:
            raise ResultRegistrationError("RESULT_FILE_PATH_DUPLICATE", "업로드 요청에 같은 상대 경로가 두 번 있습니다.")
        seen.add(path.casefold())
        content = bytes(content)
        if len(content) > MAX_FILE_BYTES:
            raise ResultRegistrationError("RESULT_FILE_SIZE_LIMIT", "파일은 32 MiB 이하여야 합니다.")
        total_request += len(content)
        if total_request > MAX_TOTAL_BYTES:
            raise ResultRegistrationError("RESULT_TOTAL_SIZE_LIMIT", "한 번의 업로드는 256 MiB 이하여야 합니다.")
        normalized.append((path, content))
    _begin(conn)
    try:
        row = _draft_row(conn, draft_id, lock=True)
        if row["status"] not in _MUTABLE_DRAFT_STATES:
            raise ResultRegistrationError("RESULT_DRAFT_IMMUTABLE", "승인되어 등록이 시작된 초안은 파일을 변경할 수 없습니다.")
        context = row["context_json"] or {}
        target = _current_target(conn, project_id=str(row["project_id"]), request_id=str(row["request_id"]),
                                 environment=str(row["environment"]), case_relative_path=str(row["case_relative_path"]),
                                 result_relative_path=str(row["result_relative_path"]), expected_root_id=str(row["storage_root_id"]),
                                 expected_context=context)
        manifest = _load_declared(row)
        expected = {item["relative_path"].casefold(): item for item in manifest}
        if any(path.casefold() not in expected for path, _ in normalized):
            raise ResultRegistrationError("RESULT_UPLOAD_NOT_DECLARED", "초안 생성 때 지정한 파일만 업로드할 수 있습니다.")
        prior = {item["relative_path"].casefold(): item for item in _file_rows(conn, draft_id)}
        total_existing = sum(item["size_bytes"] for item in prior.values())
        replacement_savings = sum(prior[path.casefold()]["size_bytes"] for path, _ in normalized if path.casefold() in prior)
        if total_existing - replacement_savings + total_request > MAX_TOTAL_BYTES:
            raise ResultRegistrationError("RESULT_TOTAL_SIZE_LIMIT", "초안의 전체 파일은 256 MiB 이하여야 합니다.")
        now = _now()
        for relative, content in normalized:
            item = expected[relative.casefold()]
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != int(item["size"]):
                raise ResultRegistrationError("RESULT_FILE_SIZE_MISMATCH", f"업로드한 파일 크기가 선언과 다릅니다: {relative}")
            if item.get("client_sha256") and digest != str(item["client_sha256"]).casefold():
                raise ResultRegistrationError("RESULT_FILE_HASH_MISMATCH", f"업로드한 파일 해시가 선언과 다릅니다: {relative}")
            item.update({"size": len(content), "sha256": digest, "media_type": _EXTENSION_MEDIA[PurePosixPath(relative).suffix.casefold()],
                         "upload_status": "UPLOADED", "inspection_status": "PENDING"})
            conn.execute("""INSERT INTO result_registration_files
                (draft_id,relative_path,sha256,size_bytes,media_type,content,uploaded_by,uploaded_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(draft_id,relative_path) DO UPDATE SET
                sha256=excluded.sha256,size_bytes=excluded.size_bytes,media_type=excluded.media_type,
                content=excluded.content,uploaded_by=excluded.uploaded_by,uploaded_at=excluded.uploaded_at""",
                [draft_id, relative, digest, len(content), item["media_type"], content, actor, now])
        # A replacement or late upload makes every prior item classification
        # stale, even when only one item changed.
        for item in manifest:
            item["inspection_status"] = "PENDING"
        manifest.sort(key=lambda item: item["relative_path"].casefold())
        source_revision = _source_revision(row, manifest)
        conn.execute("""UPDATE result_registration_drafts SET manifest_json=?,source_revision=?,inspection_json=NULL,
            inspection_revision=NULL,approval_json=NULL,status='DRAFT',mirror_status=NULL,error_json=NULL,
            revision=revision+1,updated_by=?,updated_at=? WHERE id=?""",
            [_json(manifest), source_revision, actor, now, draft_id])
        _event(conn, draft_id, "FILES_UPLOADED", {"files": [{"relative_path": path, "size": len(data),
                   "sha256": hashlib.sha256(data).hexdigest()} for path, data in normalized],
                   "source_revision": source_revision}, actor)
        _commit(conn)
    except BaseException:
        _rollback(conn)
        raise
    return {"draft_id": draft_id, "status": "DRAFT"}


def _root_file_manifest(row: dict[str, Any], manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prefix = str(row["result_relative_path"]).rstrip("/") + "/"
    return [{**item, "relative_path": prefix + str(item["relative_path"])} for item in manifest]


def _file_payloads(row: dict[str, Any], manifest: list[dict[str, Any]], file_rows: list[dict[str, Any]],
                   excluded: set[str] | None = None) -> list[tuple[str, bytes, str]]:
    excluded = excluded or set()
    by_path = {str(item["relative_path"]).casefold(): item for item in file_rows}
    prefix = str(row["result_relative_path"]).rstrip("/") + "/"
    result: list[tuple[str, bytes, str]] = []
    for item in manifest:
        local = str(item["relative_path"])
        if local.casefold() in excluded:
            continue
        stored = by_path.get(local.casefold())
        if stored is None:
            continue
        content = stored["content"]
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != int(item["size"]) or digest != str(item.get("sha256") or "").casefold():
            raise ResultRegistrationError("RESULT_SOURCE_STALE", "검수한 원본과 초안 저장 바이트가 달라졌습니다. 다시 검수하세요.")
        result.append((prefix + local, content, str(item["media_type"])))
    return sorted(result, key=lambda item: item[0].casefold())


def _unit(metric: str | None) -> str | None:
    if not metric:
        return None
    match = re.search(r"\(([^()]*)\)\s*$", metric)
    return match.group(1) if match else None


def _inspect_usage(files: list[tuple[str, bytes, str]], manifest: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = usage_source_review.review([(path, data) for path, data, _ in files], selected=_USAGE_SELECTION)
    sources = {item["source"]: item["sha256"] for item in reviewed["contract"].get("sources", [])}
    metrics: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    used_sources: set[str] = set()
    for entry in reviewed["entries"]:
        source_path = entry.get("source")
        if source_path:
            used_sources.add(str(source_path))
        for metric in entry.get("metrics", []):
            metric_path = str(metric.get("key") or "")
            status = str(metric.get("status") or "UNKNOWN")
            metrics.append({"evaluation": entry.get("evaluation"), "metric": metric_path, "key": f"{entry.get('evaluation')}:{entry.get('direction')}:{metric_path}",
                            "value": metric.get("value"), "value_type": metric.get("value_type"), "unit": _unit(metric_path),
                            "source_path": source_path, "source_sha256": sources.get(str(source_path)) if source_path else None,
                            "status": status})
            if status not in {"READY", "MISSING_SOURCE"}:
                issues.append({"severity": "ERROR", "code": status, "message": "결과 값의 원본 또는 자료형을 확인할 수 없습니다.",
                               "source_path": source_path, "detail": metric_path})
            elif status == "MISSING_SOURCE":
                issues.append({"severity": "WARNING", "code": "MISSING_RESULT", "message": "이 결과 항목에 등록된 원본 파일이 없습니다.",
                               "source_path": None, "detail": metric_path})
    recognized = set(sources)
    media = []
    for path, data, media_type in files:
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix not in usage_source_review.MEDIA_SUFFIXES:
            continue
        local = next((str(item["relative_path"]) for item in manifest if path.casefold().endswith("/" + str(item["relative_path"]).casefold())), path)
        media.append({"relative_path": local, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                      "media_type": media_type, "kind": "VIDEO" if suffix in {".mp4", ".webm"} else "IMAGE",
                      "title": PurePosixPath(path).name, "status": "READY"})
        recognized.add(path)
    pending = [item["relative_path"] for item in manifest if item.get("upload_status") != "UPLOADED"]
    issues.extend({"severity": "WARNING", "code": "FILE_NOT_UPLOADED", "message": "선언한 파일이 아직 업로드되지 않았습니다.",
                   "source_path": path} for path in pending)
    for item in manifest:
        if item.get("upload_status") == "UPLOADED" and item["relative_path"] not in recognized and not any(
                full.endswith("/" + item["relative_path"]) for full in recognized):
            issues.append({"severity": "INFO", "code": "FILE_NOT_USED", "message": "검사한 결과 항목이나 미디어에 연결되지 않은 파일입니다.",
                           "source_path": item["relative_path"]})
    return {"metrics": metrics, "media": media, "issues": issues,
            "missing_count": int(reviewed["missing_count"]) + len(pending),
            "blocking_count": int(reviewed["blocking_count"]), "review_contract": reviewed["contract"],
            "metric_count": len(metrics), "result_count": sum(1 for item in metrics if item["status"] == "READY")}


def _inspect_distribution(row: dict[str, Any], files: list[tuple[str, bytes, str]], manifest: list[dict[str, Any]],
                          context: dict[str, Any]) -> dict[str, Any]:
    # _distribution_payload expects root-relative names and the assignment chain
    # captured when the selected Case/Scene was inspected.
    capture_context = {**context, "hierarchy_assignments": context.get("_registration", {}).get("hierarchy_assignments", [])}
    try:
        parsed = dashboard_capture._distribution_payload(str(row["case_relative_path"]), files,
                                                         capture_context, str(row["storage_root_id"]))
    except Exception as exc:
        raise ResultRegistrationError("RESULT_INSPECTION_FAILED", "유통 결과 파일을 검사하지 못했습니다.") from exc
    metrics: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    media_by_path: dict[str, dict[str, Any]] = {}
    quality_codes: set[str] = {str(item) for item in parsed.get("quality_issues", [])}
    for scene in parsed.get("scenes", []):
        for media in scene.get("media", []):
            media_by_path[str(media.get("relative_path"))] = media
        for observation in scene.get("observations", []):
            source = str(observation.get("source_path") or "")
            item = next((entry for entry in manifest if source.endswith("/" + str(entry["relative_path"]))), None)
            detail = ":".join(str(observation.get(key)) for key in ("kind", "position", "line_index") if observation.get(key) is not None)
            metrics.append({"evaluation": scene.get("label"), "metric": detail or str(observation.get("kind") or "result"),
                            "key": f"{scene.get('label')}:{observation.get('component_id')}:{detail}",
                            "value": observation.get("value"), "value_type": "number" if isinstance(observation.get("value"), (int, float)) else "unknown",
                            "unit": None, "source_path": source, "source_sha256": item.get("sha256") if item else None,
                            "status": "READY"})
        quality_codes.update(str(code) for code in scene.get("quality_issues", []))
    for code in sorted(quality_codes):
        blocking = (code == "SOURCE_PARSE_ERROR" or code == "CORNER_SUMMARY_DETAIL_MISMATCH" or
                    "UNPROCESSED_FILE" in code)
        source_path = None
        if code.startswith("UNPROCESSED_FILE:"):
            source_path = code.split(":", 2)[1]
        issues.append({"severity": "ERROR" if blocking else "WARNING", "code": code,
                       "message": "일부 결과 파일을 해석하지 못했거나 정합성 확인이 필요합니다.",
                       "source_path": source_path})
    media = []
    for path, data, media_type in files:
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix not in usage_source_review.MEDIA_SUFFIXES:
            continue
        # `_file_payloads` already supplies the root-relative path that the
        # distribution parser reports; adding the result-folder prefix again
        # would incorrectly mark selected-scene media as unlinked.
        root_path = path
        local_path = next((str(item["relative_path"]) for item in manifest
                           if path.casefold().endswith("/" + str(item["relative_path"]).casefold())), path)
        media.append({"relative_path": local_path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                      "media_type": media_type, "kind": "VIDEO" if suffix in {".mp4", ".webm"} else "IMAGE",
                      "title": PurePosixPath(path).name, "status": "READY" if root_path in media_by_path else "UNLINKED"})
    pending = [item["relative_path"] for item in manifest if item.get("upload_status") != "UPLOADED"]
    issues.extend({"severity": "WARNING", "code": "FILE_NOT_UPLOADED", "message": "선언한 파일이 아직 업로드되지 않았습니다.",
                   "source_path": path} for path in pending)
    if media and not metrics:
        issues.append({"severity": "WARNING", "code": "NO_PARSED_RESULTS",
                       "message": "결과 수치 없이 미디어만 등록합니다. 부분 등록을 확인하세요.", "source_path": None})
    bad = sum(1 for item in issues if item["severity"] == "ERROR")
    connected_media_count = sum(1 for item in media if item["status"] == "READY")
    return {"metrics": metrics, "media": media, "issues": issues,
            "missing_count": len(pending) + (1 if media and not metrics else 0), "blocking_count": bad,
            "metric_count": len(metrics), "result_count": len(metrics),
            "media_count": len(media), "connected_media_count": connected_media_count}


def _path_matches(source_path: Any, relative_path: str) -> bool:
    source = str(source_path or "").replace("\\", "/").casefold()
    relative = relative_path.replace("\\", "/").casefold()
    return source == relative or source.endswith("/" + relative)


def _manifest_inspection_status(item: dict[str, Any], inspection: dict[str, Any], excluded: set[str]) -> str:
    relative = str(item["relative_path"])
    folded = relative.casefold()
    if folded in excluded:
        return "EXCLUDED"
    if item.get("upload_status") != "UPLOADED":
        return "PENDING"

    matching_issues = [issue for issue in inspection.get("issues", [])
                       if _path_matches(issue.get("source_path"), relative) or
                       _path_matches(issue.get("detail"), relative)]
    if any(str(issue.get("severity") or "").upper() == "ERROR" for issue in matching_issues):
        unsupported = any("UNSUPPORTED_DISTRIBUTION_FORMAT" in str(issue.get("code")) or
                          "UNRECOGNIZED_RESULT_FILE" in str(issue.get("code")) for issue in matching_issues)
        return "UNSUPPORTED" if unsupported else "ERROR"
    if PurePosixPath(relative).suffix.casefold() in {".json", ".csv"} and any(
            str(issue.get("severity") or "").upper() == "ERROR" and not issue.get("source_path")
            for issue in inspection.get("issues", [])):
        return "ERROR"

    matching_media = [media for media in inspection.get("media", [])
                      if _path_matches(media.get("relative_path"), relative)]
    if matching_media:
        return "READY" if any(media.get("status") == "READY" for media in matching_media) else "UNSUPPORTED"
    if any(_path_matches(metric.get("source_path"), relative) and metric.get("status") == "READY"
           for metric in inspection.get("metrics", [])):
        return "READY"
    contract_sources = (inspection.get("review_contract") or {}).get("sources", [])
    if any(_path_matches(source.get("source"), relative) for source in contract_sources if isinstance(source, dict)):
        return "READY"
    return "UNSUPPORTED"


def _build_inspection(row: dict[str, Any], manifest: list[dict[str, Any]], file_rows: list[dict[str, Any]],
                      exclusions: list[dict[str, str]] | None = None) -> dict[str, Any]:
    exclusions = exclusions or []
    excluded = {item["relative_path"].casefold() for item in exclusions}
    available = _file_payloads(row, manifest, file_rows, excluded)
    active_manifest = [item for item in manifest if str(item["relative_path"]).casefold() not in excluded]
    pending = sum(1 for item in active_manifest if item.get("upload_status") != "UPLOADED")
    if row["environment"] == "USAGE":
        inspected = _inspect_usage(available, active_manifest)
    else:
        context = row["context_json"] or {}
        # Feed root-relative names into the same parser used by published captures.
        inspected = _inspect_distribution(row, available, active_manifest, context)
    inspection_manifest = []
    for item in manifest:
        status = _manifest_inspection_status(item, inspected, excluded)
        inspection_manifest.append({
            key: item.get(key) for key in ("relative_path", "size", "sha256", "media_type", "upload_status")
        } | {"inspection_status": status})
        item["inspection_status"] = status
    inspected["draft_id"] = str(row["id"])
    inspected["status"] = "INSPECTED"
    inspected["source_revision"] = str(row["source_revision"])
    inspected["manifest"] = inspection_manifest
    inspected["exclusions"] = exclusions
    inspected["blocking_count"] = int(inspected.get("blocking_count", 0))
    inspected["missing_count"] = max(int(inspected.get("missing_count", 0)), pending)
    inspected["metric_count"] = len(inspected.get("metrics", []))
    inspected["result_count"] = sum(1 for item in inspected.get("metrics", []) if item.get("status") == "READY")
    inspected["media_count"] = len(inspected.get("media", []))
    inspected["connected_media_count"] = sum(1 for item in inspected.get("media", []) if item.get("status") == "READY")
    return inspected


def inspect_draft(conn: ConnectionLike, draft_id: str, actor: str,
                  exclusions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    _begin(conn)
    try:
        row = _draft_row(conn, draft_id, lock=True)
        if row["status"] in {"PUBLISHED", "MIRROR_PENDING", "MIRROR_CONFLICT", "MIRROR_FAILED"}:
            raise ResultRegistrationError("RESULT_DRAFT_IMMUTABLE", "게시가 시작된 초안은 다시 검사할 수 없습니다.")
        target = _current_target(conn, project_id=str(row["project_id"]), request_id=str(row["request_id"]),
                                 environment=str(row["environment"]), case_relative_path=str(row["case_relative_path"]),
                                 result_relative_path=str(row["result_relative_path"]), expected_root_id=str(row["storage_root_id"]),
                                 expected_context=row["context_json"] or {})
        if target["assignments"] != (row["context_json"] or {}).get("_registration", {}).get("hierarchy_assignments", []):
            raise ResultRegistrationError("RESULT_CONTEXT_CHANGED", "결과 계층 연결이 검수 뒤 변경되었습니다. 새 초안을 만드세요.")
        manifest = _load_declared(row)
        excluded = _normalize_exclusions(exclusions or [], manifest)
        file_rows = _file_rows(conn, draft_id)
        actual_total = sum(item["size_bytes"] for item in file_rows)
        if actual_total > MAX_TOTAL_BYTES:
            raise ResultRegistrationError("RESULT_TOTAL_SIZE_LIMIT", "초안에 저장된 파일이 전체 용량 제한을 초과합니다.")
        inspection = _build_inspection(row, manifest, file_rows, excluded)
        revision = _hash({"source_revision": row["source_revision"], "inspection": inspection,
                          "exclusions": excluded})
        inspection["inspection_revision"] = revision
        conn.execute("""UPDATE result_registration_drafts SET inspection_json=?,inspection_revision=?,approval_json=NULL,
            status='INSPECTED',error_json=NULL,revision=revision+1,updated_by=?,updated_at=? WHERE id=?""",
            [_json(inspection), revision, actor, _now(), draft_id])
        _event(conn, draft_id, "INSPECTED", {"inspection_revision": revision,
                   "blocking_count": inspection["blocking_count"], "missing_count": inspection["missing_count"],
                   "metric_count": inspection.get("metric_count", len(inspection.get("metrics", []))),
                   "media_count": inspection.get("media_count", len(inspection.get("media", []))),
                   "exclusions": excluded}, actor)
        _commit(conn)
        return inspection
    except BaseException:
        _rollback(conn)
        raise


def _normalize_exclusions(exclusions: list[dict[str, Any]], manifest: list[dict[str, Any]]) -> list[dict[str, str]]:
    declared = {str(item["relative_path"]).casefold(): str(item["relative_path"]) for item in manifest}
    seen: set[str] = set()
    result = []
    if len(exclusions) > MAX_FILES:
        raise ResultRegistrationError("RESULT_EXCLUSIONS_INVALID", "제외 항목 수가 초안 파일 수를 초과했습니다.")
    for item in exclusions:
        path = paths._relative(str(item.get("relative_path") or ""))
        reason = str(item.get("reason") or "").strip()
        canonical_path = declared.get(path.casefold())
        if canonical_path is None or path.casefold() in seen or not reason or len(reason) > 500:
            raise ResultRegistrationError("RESULT_EXCLUSIONS_INVALID", "제외 경로와 사유를 확인하세요.")
        seen.add(path.casefold())
        result.append({"relative_path": canonical_path, "reason": reason})
    return sorted(result, key=lambda item: item["relative_path"].casefold())


def approve_draft(conn: ConnectionLike, draft_id: str, inspection_revision: str | int,
                  acknowledge_partial: bool, exclusions: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    _begin(conn)
    try:
        row = _draft_row(conn, draft_id, lock=True)
        if row["status"] != "INSPECTED" or not row["inspection_json"]:
            raise ResultRegistrationError("RESULT_INSPECTION_REQUIRED", "초안을 먼저 자동 검사하세요.")
        if str(inspection_revision) != str(row["inspection_revision"]):
            raise ResultRegistrationError("RESULT_INSPECTION_STALE", "검수 화면이 최신 자료와 다릅니다. 다시 검사하세요.")
        if _source_revision(row, _load_declared(row)) != str(row["source_revision"]):
            raise ResultRegistrationError("RESULT_SOURCE_STALE", "업로드 자료가 검수 뒤 변경되었습니다. 다시 검사하세요.")
        manifest = _load_declared(row)
        excluded = _normalize_exclusions(exclusions, manifest)
        excluded_paths = {item["relative_path"].casefold() for item in excluded}
        file_rows = _file_rows(conn, draft_id)
        reviewed_exclusions = _normalize_exclusions((row["inspection_json"] or {}).get("exclusions", []), manifest)
        if excluded != reviewed_exclusions:
            raise ResultRegistrationError("RESULT_EXCLUSIONS_STALE", "제외 목록이 검사한 내용과 다릅니다. 같은 제외 목록으로 다시 검사하세요.")
        available_manifest = [item for item in manifest if item.get("upload_status") == "UPLOADED" and item["relative_path"].casefold() not in excluded_paths]
        if not available_manifest:
            raise ResultRegistrationError("RESULT_APPROVAL_EMPTY", "게시할 파일이 하나 이상 있어야 합니다.")
        recalculated = _build_inspection(row, manifest, file_rows, reviewed_exclusions)
        stored_inspection = dict(row["inspection_json"] or {})
        stored_inspection.pop("inspection_revision", None)
        if _hash(recalculated) != _hash(stored_inspection):
            raise ResultRegistrationError("RESULT_INSPECTION_STALE", "저장한 검사 결과와 현재 자료가 다릅니다. 다시 검사하세요.")
        if int(recalculated.get("blocking_count", 0)):
            raise ResultRegistrationError("RESULT_INSPECTION_BLOCKING", "필수 결과 오류를 해결하거나 문제가 있는 파일을 명시적으로 제외하세요.")
        if int(recalculated.get("result_count", 0)) == 0 and int(recalculated.get("connected_media_count", 0)) == 0:
            raise ResultRegistrationError("RESULT_APPROVAL_EMPTY", "인식된 결과값이나 연결된 지원 미디어가 없어 부분 등록할 수 없습니다.")
        needs_partial_ack = bool(int(recalculated.get("missing_count", 0)) or excluded)
        if needs_partial_ack and not acknowledge_partial:
            raise ResultRegistrationError("RESULT_PARTIAL_ACK_REQUIRED", "누락 또는 제외를 확인하고 부분 등록을 명시적으로 승인하세요.")
        target = _current_target(conn, project_id=str(row["project_id"]), request_id=str(row["request_id"]),
                                 environment=str(row["environment"]), case_relative_path=str(row["case_relative_path"]),
                                 result_relative_path=str(row["result_relative_path"]), expected_root_id=str(row["storage_root_id"]),
                                 expected_context=row["context_json"] or {})
        if target["assignments"] != (row["context_json"] or {}).get("_registration", {}).get("hierarchy_assignments", []):
            raise ResultRegistrationError("RESULT_CONTEXT_CHANGED", "결과 계층 연결이 변경되었습니다. 새 초안을 만드세요.")
        approved_manifest = [dict(item) for item in available_manifest]
        frozen_inspection = dict(row["inspection_json"] or {})
        frozen_counts = {key: int(frozen_inspection.get(key, 0)) for key in (
            "metric_count", "result_count", "media_count", "connected_media_count", "missing_count", "blocking_count"
        )}
        approval = {"inspection_revision": str(row["inspection_revision"]), "source_revision": str(row["source_revision"]),
                    "acknowledge_partial": bool(acknowledge_partial), "exclusions": excluded,
                    "files": approved_manifest, "inspection": frozen_inspection, "counts": frozen_counts,
                    "approved_by": actor, "approved_at": _now().isoformat()}
        conn.execute("""UPDATE result_registration_drafts SET approval_json=?,status='APPROVED',approved_by=?,
            error_json=NULL,revision=revision+1,updated_by=?,updated_at=? WHERE id=?""",
            [_json(approval), actor, actor, _now(), draft_id])
        _event(conn, draft_id, "APPROVED", {"inspection_revision": str(row["inspection_revision"]),
                   "acknowledge_partial": bool(acknowledge_partial), "exclusions": excluded,
                   "approved_files": [{"relative_path": item["relative_path"], "sha256": item.get("sha256"), "size": item["size"]} for item in approved_manifest]}, actor)
        _commit(conn)
        return {"draft_id": draft_id, "status": "APPROVED", "exclusions": excluded,
                "result_count": frozen_counts["result_count"], "media_count": frozen_counts["connected_media_count"]}
    except BaseException:
        _rollback(conn)
        raise


def _publish_result(row: dict[str, Any]) -> dict[str, Any]:
    approval = row["approval_json"] or {}
    publication = approval.get("publication") or {}
    manifest = approval.get("files") or []
    inspection = approval.get("inspection") or row["inspection_json"] or {}
    counts = approval.get("counts") or {}
    media_count = sum(1 for item in manifest if PurePosixPath(str(item["relative_path"])).suffix.casefold() in usage_source_review.MEDIA_SUFFIXES)
    image_count = sum(1 for item in manifest if PurePosixPath(str(item["relative_path"])).suffix.casefold() in {".jpg", ".jpeg", ".png"})
    video_count = media_count - image_count
    return {"draft_id": str(row["id"]), "status": str(row["status"]), "case_id": row.get("case_id"),
            "capture_id": row.get("capture_id"), "environment": row["environment"], "project_id": row["project_id"],
            "request_id": row["request_id"], "context": _public_context(row["context_json"] or {}),
            "asset_count": len(manifest), "result_count": int(publication.get("result_count", counts.get("result_count", inspection.get("result_count", 0)))),
            "media_count": media_count, "image_count": image_count, "video_count": video_count,
            "schema_refresh": publication.get("schema_refresh"),
            "mirror_status": row.get("mirror_status"), "error": row.get("error_json"),
            "idempotency_key": row.get("publish_idempotency_key"), "published_at": row.get("published_at")}


def _capture_payload(row: dict[str, Any], target: dict[str, Any], approval: dict[str, Any],
                     approved_files: list[tuple[str, bytes, str]], approved_manifest: list[dict[str, Any]]) -> dict[str, Any]:
    context = row["context_json"] or {}
    body = {"project_id": row["project_id"], "request_id": row["request_id"],
            "root_relative_path": row["case_relative_path"], "environment": row["environment"],
            "storage_root_id": row["storage_root_id"],
            "simulation_case_id": context.get("simulation_case", {}).get("id"),
            "hierarchy_assignments": target.get("assignments") or context.get("_registration", {}).get("hierarchy_assignments", []),
            "folder_schema_locations": target.get("folder_schema_locations", []),
            "folder_schema_blocked_paths": target.get("folder_schema_blocked_paths", []),
            "folder_schema_scoped": bool(target.get("folder_schema_scoped")),
            "folder_schema_snapshot_id": target.get("folder_schema_snapshot_id")}
    if row["environment"] == "USAGE":
        review = usage_source_review.review([(path, content) for path, content, _ in approved_files], selected=_USAGE_SELECTION)
        body["usage_source_review"] = {**review["contract"], "acknowledge_partial": bool(approval.get("acknowledge_partial"))}
    else:
        body.update({"load_case_id": context.get("load_case", {}).get("id"),
                     "execution_run_id": context.get("execution_run", {}).get("id"),
                     "run_display_name": context.get("execution_run", {}).get("label"),
                     "run_option_id": context.get("run_option", {}).get("id"),
                     "option_label": context.get("run_option", {}).get("label"),
                     "option_status": context.get("run_option", {}).get("status")})
    return body


def _mirror_files(conn: ConnectionLike, row: dict[str, Any], approval: dict[str, Any], file_rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    target = _current_target(conn, project_id=str(row["project_id"]), request_id=str(row["request_id"]),
                             environment=str(row["environment"]), case_relative_path=str(row["case_relative_path"]),
                             result_relative_path=str(row["result_relative_path"]), expected_root_id=str(row["storage_root_id"]),
                             expected_context=row["context_json"] or {})
    root: Path = target["root"]
    result_relative = str(row["result_relative_path"])
    paths._lock_path(conn, target["root_key"], result_relative)
    file_by_path = {str(item["relative_path"]).casefold(): item for item in file_rows}
    reused: list[str] = []
    copied: list[str] = []
    for approved in approval.get("files") or []:
        local = paths._relative(str(approved["relative_path"]))
        stored = file_by_path.get(local.casefold())
        if stored is None:
            return "MIRROR_FAILED", {"code": "RESULT_SOURCE_MISSING", "message": "승인한 DB 원본 파일을 찾을 수 없습니다.", "relative_path": local}
        content = stored["content"]
        digest = hashlib.sha256(content).hexdigest()
        if digest != str(approved.get("sha256") or "").casefold() or len(content) != int(approved["size"]):
            return "MIRROR_FAILED", {"code": "RESULT_SOURCE_STALE", "message": "승인한 원본 바이트가 변경되었습니다.", "relative_path": local}
        full_relative = f"{result_relative}/{local}"
        paths._lock_path(conn, target["root_key"], full_relative)
        paths._owner_conflict(conn, target["root_id"], target["root_key"], full_relative,
                              str(row["project_id"]), str(row["request_id"]), str(row["environment"]))
        parts = PurePosixPath(local).parts
        parent_relative = result_relative
        parent = paths._safe_existing(root, parent_relative)
        for part in parts[:-1]:
            parent_relative = f"{parent_relative}/{part}"
            candidate = parent / part
            try:
                candidate.lstat()
            except FileNotFoundError:
                try:
                    candidate.mkdir()
                except FileExistsError:
                    pass
            spdm_storage._assert_safe_existing(candidate, root)
            if not candidate.is_dir():
                return "MIRROR_CONFLICT", {"code": "MIRROR_PATH_CONFLICT", "message": "결과 저장 경로의 하위 항목이 폴더가 아닙니다.", "relative_path": parent_relative}
            parent = candidate
        destination = parent / parts[-1]
        try:
            destination.lstat()
        except FileNotFoundError:
            pass
        else:
            spdm_storage._assert_safe_existing(destination, root)
            if not destination.is_file():
                return "MIRROR_CONFLICT", {"code": "MIRROR_PATH_CONFLICT", "message": "같은 이름의 결과 저장 항목이 파일이 아닙니다.", "relative_path": full_relative}
            existing, _ = spdm_storage.read_stable_bytes(destination, max_bytes=MAX_FILE_BYTES)
            if hashlib.sha256(existing).hexdigest() == digest and len(existing) == len(content):
                reused.append(local)
                continue
            return "MIRROR_CONFLICT", {"code": "MIRROR_CONFLICT", "message": "같은 이름에 다른 내용의 파일이 있어 원본 폴더에 저장하지 않았습니다.", "relative_path": full_relative}
        spdm_storage._assert_safe_existing(parent, root)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(destination, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            copied.append(local)
        except FileExistsError:
            # A concurrent writer may have won after our preflight. Re-read and
            # reuse only byte-identical content; never overwrite it.
            try:
                spdm_storage._assert_safe_existing(destination, root)
                existing, _ = spdm_storage.read_stable_bytes(destination, max_bytes=MAX_FILE_BYTES)
            except Exception:
                return "MIRROR_CONFLICT", {"code": "MIRROR_PATH_CONFLICT", "message": "결과 저장 경로가 동시에 변경되었습니다.", "relative_path": full_relative}
            if hashlib.sha256(existing).hexdigest() == digest and len(existing) == len(content):
                reused.append(local)
                continue
            return "MIRROR_CONFLICT", {"code": "MIRROR_CONFLICT", "message": "같은 이름에 다른 내용의 파일이 있어 원본 폴더에 저장하지 않았습니다.", "relative_path": full_relative}
        except OSError as exc:
            return "MIRROR_FAILED", {"code": "MIRROR_WRITE_FAILED", "message": "DB 등록은 완료됐지만 SPDM 폴더에 원본을 저장하지 못했습니다.",
                                      "relative_path": full_relative}
    return "PUBLISHED", {"copied": copied, "reused": reused}


def _mirror_after_capture(conn: ConnectionLike, draft_id: str, actor: str) -> dict[str, Any]:
    _begin(conn)
    try:
        row = _draft_row(conn, draft_id, lock=True)
        if row["status"] not in {"MIRROR_PENDING", "MIRROR_CONFLICT", "MIRROR_FAILED", "PUBLISHED"} or not row.get("capture_id"):
            raise ResultRegistrationError("RESULT_MIRROR_NOT_READY", "DB 등록이 완료된 초안만 원본 폴더에 저장할 수 있습니다.")
        approval = row["approval_json"] or {}
        try:
            status, detail = _mirror_files(conn, row, approval, _file_rows(conn, draft_id))
        except paths.ResultRegistrationError as exc:
            status, detail = "MIRROR_FAILED", {"code": exc.code, "message": str(exc)}
        except Exception:
            status, detail = "MIRROR_FAILED", {"code": "MIRROR_WRITE_FAILED",
                                                "message": "DB 등록은 완료됐지만 SPDM 폴더에 원본을 저장하지 못했습니다."}
        mirror_status = "READY" if status == "PUBLISHED" else ("CONFLICT" if status == "MIRROR_CONFLICT" else "FAILED")
        next_status = "PUBLISHED" if status == "PUBLISHED" else status
        error = None if status == "PUBLISHED" else detail
        approval["publication"] = {**(approval.get("publication") or {}), "mirror": detail}
        conn.execute("""UPDATE result_registration_drafts SET status=?,mirror_status=?,error_json=?,approval_json=?,
            revision=revision+1,updated_by=?,updated_at=? WHERE id=?""",
            [next_status, mirror_status, _json(error) if error else None, _json(approval), actor, _now(), draft_id])
        _event(conn, draft_id, "MIRRORED" if status == "PUBLISHED" else "MIRROR_FAILED",
               {"status": status, "detail": detail, "capture_id": row["capture_id"]}, actor)
        _commit(conn)
        published = _publish_result(_draft_row(conn, draft_id))
        if status == "PUBLISHED":
            schema_refresh = _refresh_schema(
                conn, str(row["project_id"]), str(row["request_id"]), str(row["environment"]), actor,
                capture_cases=False,
            )
            _begin(conn)
            latest = _draft_row(conn, draft_id, lock=True)
            latest_approval = latest["approval_json"] or {}
            latest_publication = latest_approval.get("publication") or {}
            latest_publication["schema_refresh"] = schema_refresh
            latest_approval["publication"] = latest_publication
            conn.execute(
                "UPDATE result_registration_drafts SET approval_json=?,revision=revision+1,updated_by=?,updated_at=? WHERE id=?",
                [_json(latest_approval), actor, _now(), draft_id],
            )
            _event(conn, draft_id, "SCHEMA_REFRESHED" if schema_refresh["status"] != "FAILED" else "SCHEMA_REFRESH_FAILED",
                   {"status": schema_refresh["status"], "snapshot_id": schema_refresh.get("snapshot_id"),
                    "code": schema_refresh.get("code")}, actor)
            _commit(conn)
            return _publish_result(_draft_row(conn, draft_id))
        return published
    except BaseException:
        _rollback(conn)
        raise


def publish_draft(conn: ConnectionLike, draft_id: str, inspection_revision: str | int,
                  idempotency_key: str, actor: str) -> dict[str, Any]:
    if len(idempotency_key) < 8 or len(idempotency_key) > 128:
        raise ResultRegistrationError("RESULT_IDEMPOTENCY_KEY_INVALID", "등록 요청 식별자는 8~128자여야 합니다.")
    _begin(conn)
    capture_attempted = False
    try:
        row = _draft_row(conn, draft_id, lock=True)
        if row.get("capture_id"):
            if str(row.get("publish_idempotency_key") or "") != idempotency_key:
                raise ResultRegistrationError("RESULT_IDEMPOTENCY_CONFLICT", "이 초안은 다른 등록 요청 식별자로 이미 처리되었습니다.")
            _commit(conn)
            return _mirror_after_capture(conn, draft_id, actor)
        if row["status"] not in {"APPROVED", "PUBLISH_FAILED"} or not row["approval_json"]:
            raise ResultRegistrationError("RESULT_APPROVAL_REQUIRED", "사용자 검수를 완료한 초안만 등록할 수 있습니다.")
        if str(inspection_revision) != str(row["inspection_revision"]):
            raise ResultRegistrationError("RESULT_INSPECTION_STALE", "검수 화면이 최신 자료와 다릅니다. 다시 검사하고 승인하세요.")
        approval = row["approval_json"]
        if (str(approval.get("inspection_revision")) != str(row["inspection_revision"]) or
                str(approval.get("source_revision")) != str(row["source_revision"]) or
                _source_revision(row, _load_declared(row)) != str(row["source_revision"])):
            raise ResultRegistrationError("RESULT_APPROVAL_STALE", "승인한 파일 또는 문맥이 변경되었습니다. 다시 검사하세요.")
        existing_key = rows(conn.execute("SELECT id FROM result_registration_drafts WHERE publish_idempotency_key=?", [idempotency_key]))
        if existing_key and str(existing_key[0]["id"]) != draft_id:
            raise ResultRegistrationError("RESULT_IDEMPOTENCY_CONFLICT", "같은 등록 요청 식별자를 다른 초안에서 사용했습니다.")
        _root, _root_id, root_key = paths._root(conn)
        paths._lock_path(conn, root_key, str(row["case_relative_path"]))
        target = _current_target(conn, project_id=str(row["project_id"]), request_id=str(row["request_id"]),
                                 environment=str(row["environment"]), case_relative_path=str(row["case_relative_path"]),
                                 result_relative_path=str(row["result_relative_path"]), expected_root_id=str(row["storage_root_id"]),
                                 expected_context=row["context_json"] or {})
        if target["assignments"] != (row["context_json"] or {}).get("_registration", {}).get("hierarchy_assignments", []):
            raise ResultRegistrationError("RESULT_CONTEXT_CHANGED", "검수한 결과 계층이 현재 연결과 달라졌습니다. 다시 검사하세요.")
        full_manifest = []
        file_map = {str(item["relative_path"]).casefold(): item for item in _file_rows(conn, draft_id)}
        approved_files: list[tuple[str, bytes, str]] = []
        prefix = str(row["result_relative_path"]).rstrip("/") + "/"
        for item in approval.get("files") or []:
            stored = file_map.get(str(item["relative_path"]).casefold())
            if stored is None:
                raise ResultRegistrationError("RESULT_SOURCE_MISSING", "승인된 원본 파일을 찾을 수 없습니다.")
            data = stored["content"]
            digest = hashlib.sha256(data).hexdigest()
            if digest != str(item.get("sha256") or "").casefold() or len(data) != int(item["size"]):
                raise ResultRegistrationError("RESULT_SOURCE_STALE", "승인된 파일 바이트가 변경되었습니다. 다시 검사하세요.")
            root_relative = prefix + str(item["relative_path"])
            approved_files.append((root_relative, data, str(item["media_type"])))
            full_manifest.append({"relative_path": root_relative, "sha256": digest, "size": len(data), "media_type": str(item["media_type"])})
        if not approved_files:
            raise ResultRegistrationError("RESULT_APPROVAL_EMPTY", "게시할 승인 파일이 없습니다.")
        capture_payload = _capture_payload(row, target, approval, approved_files, full_manifest)
        capture_attempted = True
        try:
            capture = dashboard_capture.create_capture(conn, capture_payload, actor=actor,
                                                        approved_files=approved_files, approved_manifest=full_manifest)
        except dashboard_capture.DashboardCaptureError as exc:
            # Parser/capture messages are not persisted verbatim in draft
            # recovery state: keep that state safe to expose to the uploader.
            raise ResultRegistrationError(
                "RESULT_CAPTURE_FAILED",
                "결과를 DB에 등록하지 못했습니다. 승인된 파일과 문맥을 확인한 뒤 다시 시도하세요.",
            ) from exc
        case_id, capture_id = str(capture["case_id"]), str(capture["id"])
        frozen_counts = approval.get("counts") or {}
        result_count = int(frozen_counts.get("result_count", (approval.get("inspection") or row["inspection_json"] or {}).get("result_count", 0)))
        approval["publication"] = {"result_count": result_count, "asset_count": len(approved_files),
                                    "case_id": case_id, "capture_id": capture_id, "mirror": None}
        conn.execute("""UPDATE result_registration_drafts SET publish_idempotency_key=?,case_id=?,capture_id=?,
            status='MIRROR_PENDING',mirror_status='PENDING',error_json=NULL,approval_json=?,published_at=?,
            revision=revision+1,updated_by=?,updated_at=? WHERE id=?""",
            [idempotency_key, case_id, capture_id, _json(approval), _now(), actor, _now(), draft_id])
        _event(conn, draft_id, "CAPTURE_PUBLISHED", {"case_id": case_id, "capture_id": capture_id,
                   "inspection_revision": str(row["inspection_revision"]), "asset_count": len(approved_files),
                   "result_count": result_count}, actor)
        _commit(conn)
    except BaseException as exc:
        _rollback(conn)
        # Preserve a retryable failed state after the capture transaction has
        # rolled back. The approved files and inspection remain available.
        if capture_attempted:
            try:
                _draft_row(conn, draft_id)
                _begin(conn)
                safe_code = getattr(exc, "code", "RESULT_PUBLISH_FAILED") if isinstance(exc, paths.ResultRegistrationError) else "RESULT_PUBLISH_FAILED"
                safe_message = str(exc) if isinstance(exc, paths.ResultRegistrationError) else "결과를 DB에 등록하지 못했습니다. 초안을 다시 검사한 뒤 시도하세요."
                conn.execute("UPDATE result_registration_drafts SET status='PUBLISH_FAILED',error_json=?,updated_by=?,updated_at=? WHERE id=? AND capture_id IS NULL AND status IN ('APPROVED','PUBLISH_FAILED')",
                             [_json({"code": safe_code, "message": safe_message}), actor, _now(), draft_id])
                _event(conn, draft_id, "PUBLISH_FAILED", {"code": safe_code}, actor)
                _commit(conn)
            except Exception:
                _rollback(conn)
        raise
    return _mirror_after_capture(conn, draft_id, actor)


def retry_mirror(conn: ConnectionLike, draft_id: str, actor: str) -> dict[str, Any]:
    return _mirror_after_capture(conn, draft_id, actor)


def read_draft(conn: ConnectionLike, draft_id: str) -> dict[str, Any]:
    row = _draft_row(conn, draft_id)
    approval = row["approval_json"] or {}
    inspection = row["inspection_json"] or {}
    published = _publish_result(row)
    return {"draft_id": str(row["id"]), "status": str(row["status"]), "revision": int(row["revision"]),
            "project_id": str(row["project_id"]), "request_id": str(row["request_id"]),
            "environment": str(row["environment"]), "storage_root_id": str(row["storage_root_id"]),
            "case_relative_path": str(row["case_relative_path"]), "result_relative_path": str(row["result_relative_path"]),
            "context": _public_context(row["context_json"] or {}), "inspection_revision": row.get("inspection_revision"),
            "source_revision": row.get("source_revision"), "manifest": _read_manifest(row),
            "exclusions": inspection.get("exclusions", approval.get("exclusions", [])),
            "inspection": {key: inspection.get(key, default) for key, default in (("metrics", []), ("media", []), ("issues", []),
                           ("exclusions", []), ("missing_count", 0), ("blocking_count", 0), ("metric_count", 0),
                           ("result_count", 0), ("media_count", 0), ("connected_media_count", 0))},
            "mirror_status": row.get("mirror_status"), "error": row.get("error_json"),
            "capture_id": row.get("capture_id"), "case_id": row.get("case_id"),
            "asset_count": published["asset_count"] if row.get("capture_id") else 0,
            "schema_refresh": published.get("schema_refresh") if row.get("capture_id") else None,
            "result_count": published["result_count"] if row.get("capture_id") else 0,
            "media_count": published["media_count"] if row.get("capture_id") else 0,
            "image_count": published["image_count"] if row.get("capture_id") else 0,
            "video_count": published["video_count"] if row.get("capture_id") else 0,
            "idempotency_key": row.get("publish_idempotency_key"), "approved_by": row.get("approved_by"),
            "published_at": row.get("published_at"), "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
            "approved_files": approval.get("files", [])}


def preview_media(conn: ConnectionLike, draft_id: str, relative_path: str) -> dict[str, Any]:
    row = _draft_row(conn, draft_id)
    relative = paths._relative(relative_path)
    found = rows(conn.execute("""SELECT relative_path,sha256,size_bytes,media_type,content FROM result_registration_files
        WHERE draft_id=? AND relative_path=?""", [draft_id, relative]))
    if not found:
        raise ResultRegistrationError("RESULT_FILE_NOT_FOUND", "미디어 미리보기 파일을 찾을 수 없습니다.")
    item = found[0]
    suffix = PurePosixPath(relative).suffix.casefold()
    if suffix not in usage_source_review.MEDIA_SUFFIXES:
        raise ResultRegistrationError("RESULT_MEDIA_PREVIEW_UNSUPPORTED", "이미지 또는 영상 파일만 미리 볼 수 있습니다.")
    data = bytes(item["content"])
    if hashlib.sha256(data).hexdigest() != str(item["sha256"]).casefold():
        raise ResultRegistrationError("RESULT_SOURCE_STALE", "저장된 미디어의 해시가 일치하지 않습니다.")
    asset_type = "VIDEO" if suffix in {".mp4", ".webm"} else "CONTOUR_IMAGE"
    try:
        validate_media_metadata(asset_type, relative, int(item["size_bytes"]))
    except ValueError as exc:
        raise ResultRegistrationError("RESULT_MEDIA_PREVIEW_UNSUPPORTED", "허용되지 않는 이미지 또는 영상 형식입니다.") from exc
    return {"content": data, "media_type": str(item["media_type"]), "size": int(item["size_bytes"]),
            "filename": PurePosixPath(relative).name, "draft_id": str(row["id"])}
