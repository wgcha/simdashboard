"""Bounded, read-only access to Radioss materials stored in distribution results."""
from __future__ import annotations

import re
import time
from pathlib import Path, PurePosixPath
from typing import Any

from ..database_connection import ConnectionLike, rows
from ..parsers.radioss_deck_parser import RadiossDeckParser
from . import (folder_discovery_environment, folder_schema_hierarchy, folder_discovery_scan, folder_schema_resolver,
               result_registration_paths, spdm_storage)
from .storage.local import LocalFsProvider

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_PARSE_SECONDS = 20
MAX_FUNCTION_POINTS = 500_000
MAX_DECK_FILES = 5000
MAX_INCLUDE_DEPTH = 5
MAX_INCLUDE_FILES = 5000
_DECK_EXTENSIONS = {".inc", ".rad"}
# BEGIN, PARAMETER, and SUBSET can appear in a parts include without any
# material definitions. They remain parseable, but do not identify a material
# source by themselves.
_MATERIAL_CARDS = {"PROP", "MAT", "FUNCT", "MOVE_FUNCT", "FAIL"}
_MAX_SNIFF_BYTES = 1024 * 1024
_MAX_CANDIDATE_SCAN_BYTES = 32 * 1024 * 1024
_INCLUDE_DIRECTIVE = re.compile(r"^/INCLUDE(?:/(?P<slash>.*)|[ \t]+(?P<space>.*))?[ \t]*$", re.I)
_MATERIALS_ENVIRONMENT = "DISTRIBUTION"
_MAX_OWNERSHIP_CHECKS = 256


def _explicit_results_role(node: dict[str, Any]) -> bool:
    """Require a saved rule or explicit/apply-confirmed evidence for standalone results."""
    return (node.get("role_source") == "PROFILE" and node.get("role_basis") == "RULE"
            or node.get("role_evidence_source") == "REGISTRATION"
            or (node.get("role_evidence_source") == "PREVIEW"
                and node.get("role_basis") in {"RULE", "PREVIEW"}))


def _schema_scene_role(node: dict[str, Any]) -> bool:
    return ((node.get("role_source") == "PROFILE" and node.get("role_basis") in {"RULE", "PATTERN", "DEPTH_SCHEMA"})
            or (node.get("role_source") == "INHERITED" and node.get("role_basis") == "LEVEL")
            or (node.get("role_source") == "MANUAL" and node.get("role_basis") == "MANUAL")
            or node.get("role_evidence_source") in {"PREVIEW", "REGISTRATION"})


class MaterialsCatalogError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _include_token(value: str) -> str:
    text = value.strip()
    if not text:
        raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", "빈 /INCLUDE 경로를 사용할 수 없습니다.")
    if text[0] in {'"', "'"}:
        quote = text[0]
        end = text.find(quote, 1)
        if end < 0 or text[end + 1:].strip() and not text[end + 1:].lstrip().startswith("#"):
            raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", "/INCLUDE 경로의 인용부호가 올바르지 않습니다.")
        token = text[1:end]
    else:
        token = text.split("#", 1)[0].strip().split(maxsplit=1)[0] if text.split("#", 1)[0].strip() else ""
    if not token:
        raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", "빈 /INCLUDE 경로를 사용할 수 없습니다.")
    return token


def _include_target_relative(scope: dict[str, Any], including_relative: str, include_value: str) -> str:
    if not include_value or "\\" in include_value or "\x00" in include_value or ":" in include_value:
        raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", "/INCLUDE 경로 형식이 안전하지 않습니다.")
    if include_value.startswith("/") or include_value.startswith("//"):
        raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", "/INCLUDE 경로는 root 기준 절대 경로일 수 없습니다.")
    request_parts = list(PurePosixPath(scope["request_relative_path"]).parts)
    target_parts = list(PurePosixPath(including_relative).parent.parts)
    request_folded = tuple(part.casefold() for part in request_parts)
    if tuple(part.casefold() for part in target_parts[:len(request_parts)]) != request_folded:
        raise MaterialsCatalogError("MATERIALS_INCLUDE_OUTSIDE_REQUEST", "/INCLUDE 기준 파일이 현재 의뢰 경계 밖에 있습니다.")
    components = include_value.split("/")
    if any(component == "" for component in components):
        raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", "/INCLUDE 경로에 빈 구성요소가 있습니다.")
    for component in components:
        if component == ".":
            continue
        if component == "..":
            if len(target_parts) <= len(request_parts):
                raise MaterialsCatalogError("MATERIALS_INCLUDE_OUTSIDE_REQUEST", "/INCLUDE 경로가 의뢰 폴더 밖을 가리킵니다.")
            target_parts.pop()
            continue
        if not spdm_storage._valid_windows_name(component):
            raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", "/INCLUDE 경로에 사용할 수 없는 파일 이름이 있습니다.")
        target_parts.append(component)
    if (len(target_parts) <= len(request_parts)
            or tuple(part.casefold() for part in target_parts[:len(request_parts)]) != request_folded):
        raise MaterialsCatalogError("MATERIALS_INCLUDE_OUTSIDE_REQUEST", "/INCLUDE 경로가 의뢰 폴더 밖을 가리킵니다.")
    return PurePosixPath(*target_parts).as_posix()


def _request_scope(conn: ConnectionLike, request_id: str, environment: str):
    if str(environment).upper() != _MATERIALS_ENVIRONMENT:
        raise MaterialsCatalogError("MATERIALS_ENVIRONMENT_UNSUPPORTED", "소재 덱은 유통환경에서만 조회할 수 있습니다.")
    row = conn.execute("SELECT project_id,title FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    if not row:
        raise MaterialsCatalogError("RESULT_CONTEXT_INVALID", "기존 의뢰를 확인할 수 없습니다.", 404)
    project_id = str(row[0])
    root, root_id, root_key = result_registration_paths.storage_context(conn)
    try:
        locations = folder_schema_resolver.resolve_request_locations(
            conn, project_id, request_id, environment,
        )
    except folder_schema_resolver.FolderSchemaError as exc:
        raise MaterialsCatalogError(exc.code, str(exc), exc.status_code) from exc
    schema = locations.schema
    scope = {
        "project_id": project_id,
        "request_id": request_id,
        "environment": _MATERIALS_ENVIRONMENT,
        "request_name": str(row[1] or ""),
        "request_relative_path": schema["request_relative_path"],
        "schema": schema,
        "locations": locations,
    }
    return project_id, scope, root, root_id, root_key, schema


def _candidate_directories(scene: dict[str, Any], scope: dict[str, Any]) -> list[str]:
    """Return only directories whose semantic role comes from Folder Schema."""
    scene_path = str(scene["relative_path"])
    if scene.get("kind") == "RESULTS":
        return [scene_path]
    schema = scope["schema"]
    hierarchy = scene.get("hierarchy", {})
    option = hierarchy.get("run_option")
    execution = hierarchy.get("execution_run")
    candidates = [scene_path]
    if isinstance(option, dict):
        candidates.append(str(option["relative_path"]))
    if isinstance(execution, dict):
        candidates.append(str(execution["relative_path"]))
    projected_paths = list(scene.get("input_paths") or []) + list(scene.get("result_paths") or [])
    projected_paths.sort(key=lambda item: (int(item.get("priority", 99)), str(item.get("relative_path", "")).casefold()))
    for item in projected_paths:
        if not isinstance(item, dict) or item.get("role_kind") not in {"INPUT", "RESULTS"}:
            continue
        candidates.append(str(item["relative_path"]))
    request_path = str(scope["request_relative_path"])
    request_parts = tuple(part.casefold() for part in PurePosixPath(request_path).parts)
    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        normalized = result_registration_paths._relative(value)
        parts = tuple(part.casefold() for part in PurePosixPath(normalized).parts)
        if parts[:len(request_parts)] != request_parts:
            raise MaterialsCatalogError("MATERIALS_PATH_OUTSIDE_REQUEST", "소재 탐색 후보가 의뢰 폴더 밖입니다.")
        key = normalized.casefold()
        if key not in seen:
            unique.append(normalized)
            seen.add(key)
    return unique


def _open_deck(path: Path):
    """Stable reader for one deck file through the storage provider (rooted at its folder)."""
    return LocalFsProvider(path.parent).open_read(path.name)


def _file_roles(path: Path, budget: dict[str, Any] | None = None) -> set[str]:
    name = path.stem.casefold().replace("-", "_").replace(" ", "_")
    roles: set[str] = set()
    if re.search(r"(?:^|_)parts?(?:_|$)", name):
        roles.add("parts")
    if re.search(r"(?:^|_)(?:mat(?:erial)?s?|props?|curves?|functions?)(?:_|$)", name):
        roles.add("materials")
    try:
        with _open_deck(path) as stream:
            scanned = 0
            for raw_line in stream:
                if budget is not None and time.monotonic() - budget["started"] > folder_discovery_scan.MAX_SECONDS:
                    raise MaterialsCatalogError("MATERIALS_CANDIDATE_SCAN_LIMIT", "덱 후보 내용 조사 한도를 초과했습니다.", 413)
                scanned += len(raw_line)
                if scanned > _MAX_SNIFF_BYTES:
                    break
                line = raw_line.decode("latin-1", errors="replace").lstrip()
                if not line.startswith("/"):
                    continue
                pieces = [part for part in line[1:].split("/") if part]
                keyword = pieces[0].split()[0].upper() if pieces else ""
                if keyword == "PART":
                    roles.add("parts")
                if keyword in _MATERIAL_CARDS:
                    roles.add("materials")
                if len(roles) == 2:
                    break
    except spdm_storage.SpdmStorageError as exc:
        raise MaterialsCatalogError(exc.code, str(exc)) from exc
    return roles


def _path_owner_conflict(conn: ConnectionLike, root_id: str, root_key: str, relative_path: str,
                         project_id: str, request_id: str, environment: str) -> None:
    """Reject foreign ownership of a path or its ancestors, ignoring children."""
    folded = result_registration_paths._root_casefold(relative_path)
    parts = PurePosixPath(folded).parts
    ancestors = {"/".join(parts[:index]) for index in range(1, len(parts) + 1)}

    def is_ancestor(other: Any) -> bool:
        return result_registration_paths._root_casefold(str(other)) in ancestors

    cases = rows(conn.execute(
        "SELECT project_id,request_id,environment,relative_path FROM dashboard_cases WHERE storage_root_id=?",
        [root_id],
    ))
    if any((str(item.get("project_id") or ""), str(item.get("request_id") or ""),
            str(item.get("environment") or "")) != (project_id, request_id, environment)
           and is_ancestor(item["relative_path"]) for item in cases):
        raise result_registration_paths.ResultRegistrationError(
            "RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더 또는 상위 폴더가 다른 업무에 연결되어 있습니다.",
        )

    path_rows = rows(conn.execute(
        "SELECT project_id,request_id,environment,relative_path FROM result_registration_paths WHERE root_key=?",
        [root_key],
    ))
    if any((str(item.get("project_id") or ""), str(item.get("request_id") or ""),
            str(item.get("environment") or "")) != (project_id, request_id, environment)
           and is_ancestor(item["relative_path"]) for item in path_rows):
        raise result_registration_paths.ResultRegistrationError(
            "RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더 또는 상위 폴더가 다른 업무의 결과 경로입니다.",
        )

    registered = rows(conn.execute(
        "SELECT r.project_id,r.request_id,r.environment,g.relative_path,g.role_kind "
        "FROM folder_environment_registry g JOIN folder_environment_registrations r ON r.id=g.registration_id "
        "WHERE g.root_key=?",
        [root_key],
    ))
    for item in registered:
        if not is_ancestor(item["relative_path"]):
            continue
        role = str(item.get("role_kind") or "")
        owner_project, owner_request = str(item.get("project_id") or ""), str(item.get("request_id") or "")
        foreign = (owner_project != project_id if role == "PROJECT" else
                   (owner_project, owner_request) != (project_id, request_id) if role == "REQUEST" else
                   (owner_project, owner_request) != (project_id, request_id)
                   or str(item.get("environment") or "") != environment)
        if foreign:
            raise result_registration_paths.ResultRegistrationError(
                "RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더 또는 상위 폴더가 다른 의뢰의 확인 경로입니다.",
            )

    bindings = rows(conn.execute("SELECT project_id,request_id,relative_path FROM spdm_storage_bindings"))
    if any((str(item["project_id"]), str(item["request_id"])) != (project_id, request_id)
           and is_ancestor(item["relative_path"]) for item in bindings):
        raise result_registration_paths.ResultRegistrationError(
            "RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더 또는 상위 폴더가 다른 의뢰에 연결되어 있습니다.",
        )

    semantic = rows(conn.execute("SELECT project_id,request_id,relative_path FROM semantic_folder_bindings"))
    if any((str(item["project_id"]) != project_id
            or item.get("request_id") is not None and str(item["request_id"]) != request_id)
           and is_ancestor(item["relative_path"]) for item in semantic):
        raise result_registration_paths.ResultRegistrationError(
            "RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더 또는 상위 폴더가 다른 의뢰의 의미 매핑 경로입니다.",
        )

    discovered = rows(conn.execute(
        "SELECT g.relative_path,g.role_kind,COALESCE(p.id,a.project_id,ar.project_id) AS owner_project_id, "
        "COALESCE(a.id,l.request_id) AS owner_request_id "
        "FROM folder_discovery_registry g "
        "LEFT JOIN projects p ON g.role_kind='PROJECT' AND p.id=g.target_id "
        "LEFT JOIN analysis_requests a ON g.role_kind='REQUEST' AND a.id=g.target_id "
        "LEFT JOIN load_cases l ON g.role_kind='LOAD_CASE' AND l.id=g.target_id "
        "LEFT JOIN analysis_requests ar ON ar.id=l.request_id WHERE g.root_key=?",
        [root_key],
    ))
    for item in discovered:
        if not is_ancestor(item["relative_path"]):
            continue
        owner_project, owner_request = item.get("owner_project_id"), item.get("owner_request_id")
        if ((owner_project is not None and str(owner_project) != project_id)
                or (owner_request is not None
                    and (str(owner_project or project_id), str(owner_request)) != (project_id, request_id))):
            raise result_registration_paths.ResultRegistrationError(
                "RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더 또는 상위 폴더가 다른 의뢰의 탐색 경로입니다.",
            )


def _candidate_is_owned(conn: ConnectionLike, root: Path, root_id: str, root_key: str,
                        scope: dict[str, Any], relative_path: str) -> bool:
    try:
        _path_owner_conflict(
            conn, root_id, root_key, relative_path, scope["project_id"], scope["request_id"], scope["environment"],
        )
    except result_registration_paths.ResultRegistrationError as exc:
        if exc.code in {"RESULT_PATH_OWNERSHIP_CONFLICT", "RESULT_PATH_ROLE_CONFLICT", "RESULT_PATH_INVALID",
                        "RESULT_PATH_OUTSIDE_REQUEST"}:
            return False
        raise MaterialsCatalogError(exc.code, str(exc)) from exc
    request_parts = tuple(part.casefold() for part in PurePosixPath(scope["request_relative_path"]).parts)
    path_parts = tuple(part.casefold() for part in PurePosixPath(relative_path).parts)
    return len(path_parts) > len(request_parts) and path_parts[:len(request_parts)] == request_parts


def _candidate_sources(scene: dict[str, Any], root: Path, scope: dict[str, Any],
                       budget: dict[str, Any] | None = None, *, conn: ConnectionLike,
                       root_id: str, root_key: str) -> tuple[str | None, list[tuple[str, Path, int]], list[dict[str, Any]]]:
    budget = budget if budget is not None else {
        "started": time.monotonic(), "entries": 0, "sniff_bytes": 0,
        "owned_directories": {}, "ownership_checks": 0,
    }
    for directory_relative in _candidate_directories(scene, scope):
        try:
            directory = result_registration_paths._safe_existing(root, directory_relative, allow_missing_leaf=True)
        except result_registration_paths.ResultRegistrationError as exc:
            raise MaterialsCatalogError(exc.code, str(exc)) from exc
        fs = LocalFsProvider(root)
        if not fs.exists(directory):
            continue
        if not fs.is_dir(directory):
            continue
        deck_files: list[tuple[str, Path, int]] = []
        try:
            for entry in fs.list(directory):
                budget["entries"] += 1
                if budget["entries"] > folder_discovery_scan.MAX_ENTRIES or time.monotonic() - budget["started"] > folder_discovery_scan.MAX_SECONDS:
                    raise MaterialsCatalogError("MATERIALS_CANDIDATE_SCAN_LIMIT", "덱 후보 조사 한도를 초과했습니다.", 413)
                relative = fs.join(directory, entry.name)
                path = fs.path(relative)
                if fs.is_link(relative):
                    raise MaterialsCatalogError("MATERIALS_PATH_UNSAFE", "덱 후보에 reparse 또는 symbolic link가 있습니다.")
                if entry.kind != "file" or path.suffix.casefold() not in _DECK_EXTENSIONS:
                    continue
                deck_files.append((relative, path, fs.stat(relative, follow_links=True, missing_ok=False).size))
        except MaterialsCatalogError:
            raise
        except (OSError, spdm_storage.SpdmStorageError) as exc:
            raise MaterialsCatalogError("MATERIALS_SCAN_INCOMPLETE", "덱 후보 폴더를 읽을 수 없습니다.") from exc
        if not deck_files:
            continue
        directory_key = directory_relative.casefold()
        ownership_cache = budget.setdefault("owned_directories", {})
        if directory_key in ownership_cache:
            owned = ownership_cache[directory_key]
        else:
            budget["ownership_checks"] = int(budget.get("ownership_checks", 0)) + 1
            if budget["ownership_checks"] > _MAX_OWNERSHIP_CHECKS:
                raise MaterialsCatalogError("MATERIALS_OWNERSHIP_SCAN_LIMIT", "소유권을 확인할 덱 후보 폴더 수가 허용 한도를 초과했습니다.", 413)
            owned = _candidate_is_owned(conn, root, root_id, root_key, scope, directory_relative)
            ownership_cache[directory_key] = owned
        if not owned:
            continue
        parts: list[tuple[str, Path, int]] = []
        materials: list[tuple[str, Path, int]] = []
        try:
            for relative, path, size in deck_files:
                if time.monotonic() - budget["started"] > folder_discovery_scan.MAX_SECONDS:
                    raise MaterialsCatalogError("MATERIALS_CANDIDATE_SCAN_LIMIT", "덱 후보 내용 조사 한도를 초과했습니다.", 413)
                budget["sniff_bytes"] += min(size, _MAX_SNIFF_BYTES)
                if budget["sniff_bytes"] > _MAX_CANDIDATE_SCAN_BYTES:
                    raise MaterialsCatalogError("MATERIALS_CANDIDATE_SCAN_LIMIT", "덱 후보 내용 조사 한도를 초과했습니다.", 413)
                roles = _file_roles(path, budget)
                if time.monotonic() - budget["started"] > folder_discovery_scan.MAX_SECONDS:
                    raise MaterialsCatalogError("MATERIALS_CANDIDATE_SCAN_LIMIT", "덱 후보 내용 조사 한도를 초과했습니다.", 413)
                item = (relative, path, size)
                if "parts" in roles:
                    parts.append(item)
                if "materials" in roles:
                    materials.append(item)
        except MaterialsCatalogError:
            raise
        except (OSError, spdm_storage.SpdmStorageError) as exc:
            raise MaterialsCatalogError("MATERIALS_SCAN_INCOMPLETE", "덱 후보 폴더를 읽을 수 없습니다.") from exc
        parts.sort(key=lambda item: item[0].casefold())
        materials.sort(key=lambda item: item[0].casefold())
        if not parts or not materials:
            continue
        chosen = {item[0].casefold(): item for item in (parts[0], materials[0])}
        selected = sorted(chosen.values(), key=lambda item: item[0].casefold())
        warnings = []
        if len(parts) > 1:
            warnings.append({"code": "MATERIALS_MULTIPLE_PART_CANDIDATES", "relative_paths": [item[0] for item in parts]})
        if len(materials) > 1:
            warnings.append({"code": "MATERIALS_MULTIPLE_MATERIAL_CANDIDATES", "relative_paths": [item[0] for item in materials]})
        if len([item for item in selected if item[1].suffix.casefold() == ".rad"]) > 1:
            warnings.append({"code": "MATERIALS_MULTIPLE_STARTER_CANDIDATES", "relative_paths": [item[0] for item in selected if item[1].suffix.casefold() == ".rad"]})
        return directory_relative, selected, warnings
    return None, [], []


def _catalog_items(conn: ConnectionLike, root: Path, root_id: str, root_key: str,
                   scope: dict[str, Any], schema: dict[str, Any],
                   conflicts: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    budget = {
        "started": time.monotonic(), "entries": 0, "sniff_bytes": 0,
        "owned_directories": {}, "ownership_checks": 0,
    }
    items = []
    projected = {
        (str(item.get("role_kind")), str(item.get("relative_path", "")).casefold()): item
        for item in scope.get("locations").locations
    } if scope.get("locations") else {}
    for node in schema["nodes"]:
        role = str(node.get("role_kind") or "")
        location = projected.get((role, str(node.get("relative_path", "")).casefold()))
        if role not in {"SCENE", "RESULTS"} or node.get("status") == "EXCLUDED":
            continue
        if not location:
            continue
        if role == "SCENE" and not _schema_scene_role(node):
            continue
        hierarchy = node.get("hierarchy", {})
        required = ("simulation_case", "load_case", "execution_run")
        if any(not isinstance(hierarchy.get(key), dict) for key in required):
            continue
        if role == "RESULTS":
            # A Scene's result child belongs to that Scene and is searched as
            # one of its candidate directories. Only separately confirmed
            # Folder Schema results locations become their own catalog entry.
            if isinstance(hierarchy.get("scene"), dict):
                continue
            if not _explicit_results_role(node):
                continue
        # Catalog entries must be under their schema-confirmed run branch. The parent
        # chain is read from Folder Schema's role tree, never from result paths.
        scene_path = str(node["relative_path"])
        try:
            _path_owner_conflict(
                conn, root_id, root_key, scene_path,
                scope["project_id"], scope["request_id"], scope["environment"],
            )
            scene_path = result_registration_paths._relative(scene_path)
        except result_registration_paths.ResultRegistrationError as exc:
            if exc.code in {"RESULT_PATH_OWNERSHIP_CONFLICT", "RESULT_PATH_ROLE_CONFLICT",
                            "RESULT_PATH_INVALID", "RESULT_PATH_OUTSIDE_REQUEST"}:
                # Never silent: the screen explains why a Scene the Case results
                # show is missing here (e.g. the folder is linked to two requests).
                if conflicts is not None and len(conflicts) < 20:
                    conflicts.append({"relative_path": scene_path, "code": exc.code, "message": str(exc)})
                continue
            raise MaterialsCatalogError(exc.code, str(exc)) from exc
        semantic_hierarchy = {
            key: {**value, "label": value.get("name")}
            for key, value in hierarchy.items()
            if key in {"simulation_case", "load_case", "execution_run", "run_option"}
        }
        entry = {
            "scene_id": str(location.get("scene_id") or location.get("target_id") or location["location_id"]),
            "label": str(node.get("name") or PurePosixPath(scene_path).name),
            "relative_path": scene_path,
            "hierarchy": semantic_hierarchy,
            "kind": role,
            "has_deck": False,
            "input_paths": list(location.get("input_paths") or []),
            "result_paths": list(location.get("result_paths") or []),
        }
        _, candidate_files, _ = _candidate_sources(
            entry, root, scope, budget, conn=conn, root_id=root_id, root_key=root_key,
        )
        entry["has_deck"] = bool(candidate_files)
        items.append(entry)
    items.sort(key=lambda item: (item["label"].casefold(), item["relative_path"].casefold()))
    return items


def catalog(conn: ConnectionLike, request_id: str, environment: str) -> dict[str, Any]:
    project_id, scope, root, root_id, root_key, schema = _request_scope(conn, request_id, environment)
    conflicts: list[dict[str, str]] = []
    items = _catalog_items(conn, root, root_id, root_key, scope, schema, conflicts)
    # Case results and materials share one hierarchy projection, so the same URL
    # selection (case/case_load/case_run/case_option) means the same folders.
    projected = folder_schema_hierarchy.project_schema_hierarchy(schema, scope["locations"], root_key)
    by_path = {str(item.get("relative_path") or "").casefold(): item for item in projected["scenes"]}
    for item in items:
        location = by_path.get(str(item["relative_path"]).casefold())
        if location is None:
            # Explicit RESULTS locations are not Scene locations; derive the
            # same ids from their own Folder Schema hierarchy.
            node = next((candidate for candidate in schema.get("nodes", [])
                         if str(candidate.get("relative_path") or "").casefold()
                         == str(item["relative_path"]).casefold()), None)
            location = folder_schema_hierarchy.context_ids(
                schema, root_key, (node or {}).get("hierarchy") or {})
        for key in ("case_id", "load_case_id", "execution_run_id", "run_option_id"):
            item[key] = str(location.get(key) or "")
    hierarchy = {
        "cases": [
            {"id": case["id"], "label": case["label"], "relative_path": case["relative_path"]}
            for case in projected["cases"].values()
        ],
        "load_cases": projected["load_cases"],
        "execution_runs": projected["execution_runs"],
        "run_options": projected["run_options"],
    }
    return {"request_id": request_id, "environment": scope["environment"], "scenes": items,
            "hierarchy": hierarchy, "conflicts": conflicts}


def _resolve_scene(conn: ConnectionLike, request_id: str, environment: str,
                   scene_id: str | None, relative_path: str | None) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if not scene_id and not relative_path:
        raise MaterialsCatalogError("MATERIALS_SCENE_REQUIRED", "scene_id 또는 relative_path를 선택하세요.")
    project_id, scope, root, root_id, root_key, schema = _request_scope(conn, request_id, environment)
    catalog_items = _catalog_items(conn, root, root_id, root_key, scope, schema)
    by_id = {item["scene_id"]: item for item in catalog_items}
    by_path = {item["relative_path"].casefold(): item for item in catalog_items}
    by_id_entry = by_id.get(scene_id) if scene_id else None
    by_path_entry = None
    if relative_path:
        try:
            normalized = result_registration_paths._relative(relative_path)
        except result_registration_paths.ResultRegistrationError as exc:
            raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 경로가 이 의뢰에 속하지 않습니다.") from exc
        by_path_entry = by_path.get(normalized.casefold())
        if by_path_entry is None:
            raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 경로가 이 의뢰 스키마의 Scene이 아닙니다.")
    if scene_id and by_id_entry is None:
        raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 씬이 이 의뢰와 환경에 속하지 않습니다.", 404)
    if relative_path and by_path_entry is None:
        raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 경로가 이 의뢰와 환경에 속하지 않습니다.", 404)
    if by_id_entry and by_path_entry and by_id_entry["relative_path"].casefold() != by_path_entry["relative_path"].casefold():
        raise MaterialsCatalogError("MATERIALS_SCENE_MISMATCH", "scene_id와 relative_path가 서로 다른 씬을 가리킵니다.")
    selected = by_id_entry or by_path_entry
    assert selected is not None
    try:
        scene_relative = result_registration_paths._safe_existing(root, selected["relative_path"])
    except result_registration_paths.ResultRegistrationError as exc:
        raise MaterialsCatalogError(exc.code, str(exc)) from exc
    fs = LocalFsProvider(root)
    scene_path = fs.path(scene_relative)
    if not fs.is_dir(scene_relative):
        raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 Scene 폴더를 찾을 수 없습니다.", 404)
    return selected, scene_path, scope


class _ParseBudget:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.total_bytes = 0
        self.stream_bytes = 0
        self.stream_file_bytes: dict[str, int] = {}
        self.function_point_rows = 0

    def check_time(self) -> None:
        if time.monotonic() - self.started > MAX_PARSE_SECONDS:
            raise MaterialsCatalogError("MATERIALS_PARSE_TIME_LIMIT", "덱 파싱이 20초 한도를 초과했습니다.", 413)

    def add_file(self, size: int) -> None:
        if size > MAX_FILE_BYTES:
            raise MaterialsCatalogError("MATERIALS_FILE_SIZE_LIMIT", "소재 파일은 64 MiB 이하여야 합니다.", 413)
        self.total_bytes += size
        if self.total_bytes > MAX_TOTAL_BYTES:
            raise MaterialsCatalogError("MATERIALS_TOTAL_SIZE_LIMIT", "소재 덱 전체는 256 MiB 이하여야 합니다.", 413)
        self.check_time()

    def on_input_line(self, filename: str, line_number: int, raw_line: str | bytes) -> None:
        del line_number
        self.check_time()
        size = len(raw_line) if isinstance(raw_line, bytes) else len(raw_line.encode("utf-8"))
        key = filename.casefold()
        file_size = self.stream_file_bytes.get(key, 0) + size
        if file_size > MAX_FILE_BYTES:
            raise MaterialsCatalogError("MATERIALS_FILE_SIZE_LIMIT", "소재 파일은 64 MiB 이하여야 합니다.", 413)
        self.stream_file_bytes[key] = file_size
        self.stream_bytes += size
        if self.stream_bytes > MAX_TOTAL_BYTES:
            raise MaterialsCatalogError("MATERIALS_TOTAL_SIZE_LIMIT", "소재 덱 전체는 256 MiB 이하여야 합니다.", 413)

    def on_function_point(self, filename: str, line_number: int) -> None:
        del filename, line_number
        self.check_time()
        self.function_point_rows += 1
        if self.function_point_rows > MAX_FUNCTION_POINTS:
            raise MaterialsCatalogError("MATERIALS_FUNCTION_POINT_LIMIT", "FUNCT 곡선 점 전체는 500,000개 이하여야 합니다.", 413)


def _scan_include_references(path: Path, relative: str, expected_size: int,
                             budget: _ParseBudget) -> list[str]:
    references: list[str] = []
    pending_path = False
    actual_size = 0
    ended = False
    try:
        with _open_deck(path) as stream:
            for raw_line in stream:
                actual_size += len(raw_line)
                if actual_size > MAX_FILE_BYTES:
                    raise MaterialsCatalogError("MATERIALS_FILE_SIZE_LIMIT", "소재 파일은 64 MiB 이하여야 합니다.", 413)
                budget.check_time()
                line = RadiossDeckParser._decode_line(raw_line)
                if line.startswith("\ufeff"):
                    line = line[1:]
                stripped = line.strip()
                if ended:
                    continue
                if not stripped or stripped.startswith("#"):
                    continue
                if pending_path:
                    references.append(_include_token(stripped))
                    pending_path = False
                    continue
                if stripped.upper().startswith("/END"):
                    ended = True
                    continue
                match = _INCLUDE_DIRECTIVE.fullmatch(stripped)
                if match is None:
                    continue
                value = match.group("slash") if match.group("slash") is not None else match.group("space")
                if value is None or not value.strip():
                    pending_path = True
                else:
                    references.append(_include_token(value))
    except MaterialsCatalogError:
        raise
    except spdm_storage.SpdmStorageError as exc:
        raise MaterialsCatalogError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise MaterialsCatalogError("MATERIALS_INCLUDE_UNAVAILABLE", f"include 참조를 읽을 수 없습니다: {relative}") from exc
    if pending_path:
        raise MaterialsCatalogError("MATERIALS_INCLUDE_INVALID", f"{relative} 파일의 /INCLUDE에 경로가 없습니다.")
    if actual_size != expected_size:
        raise MaterialsCatalogError("MATERIALS_FILE_CHANGED", "include 탐색 중 소재 파일 크기가 변경되었습니다.")
    budget.check_time()
    return references


def _include_sources(files: list[tuple[str, Path, int]], root: Path, scope: dict[str, Any],
                     budget: _ParseBudget) -> list[tuple[str, Path, int]]:
    sources: list[tuple[str, Path, int]] = []
    visited: set[str] = set()
    active: set[str] = set()

    def visit(relative: str, depth: int) -> None:
        budget.check_time()
        key = relative.casefold()
        if key in active:
            raise MaterialsCatalogError("MATERIALS_INCLUDE_CYCLE", "/INCLUDE 파일 사이에 순환 참조가 있습니다.")
        if key in visited:
            return
        if depth > MAX_INCLUDE_DEPTH:
            raise MaterialsCatalogError("MATERIALS_INCLUDE_DEPTH_LIMIT", "덱 include 깊이는 5단계 이하여야 합니다.", 413)
        if len(sources) >= MAX_INCLUDE_FILES:
            raise MaterialsCatalogError("MATERIALS_INCLUDE_FILE_LIMIT", "덱 include 파일 수가 허용 한도를 초과했습니다.", 413)
        try:
            fs = LocalFsProvider(root)
            checked = result_registration_paths._safe_existing(root, relative)
            fs.assert_safe(checked)
            if not fs.is_file(checked):
                raise MaterialsCatalogError("MATERIALS_INCLUDE_NOT_FILE", "/INCLUDE 대상은 일반 파일이어야 합니다.")
            size = fs.stat(checked, follow_links=True, missing_ok=False).size
            path = fs.path(checked)
        except MaterialsCatalogError:
            raise
        except result_registration_paths.ResultRegistrationError as exc:
            if exc.code == "SPDM_FOLDER_MISSING":
                raise MaterialsCatalogError("MATERIALS_INCLUDE_NOT_FOUND", f"/INCLUDE 파일을 찾을 수 없습니다: {relative}") from exc
            raise MaterialsCatalogError(exc.code, str(exc)) from exc
        except (OSError, spdm_storage.SpdmStorageError) as exc:
            raise MaterialsCatalogError("MATERIALS_INCLUDE_UNAVAILABLE", f"/INCLUDE 파일을 안전하게 읽을 수 없습니다: {relative}") from exc
        if size > MAX_FILE_BYTES:
            raise MaterialsCatalogError("MATERIALS_FILE_SIZE_LIMIT", "소재 파일은 64 MiB 이하여야 합니다.", 413)
        budget.add_file(size)
        sources.append((relative, path, size))
        active.add(key)
        references = _scan_include_references(path, relative, size, budget)
        for include_value in references:
            include_relative = _include_target_relative(scope, relative, include_value)
            visit(include_relative, depth + 1)
        active.remove(key)
        visited.add(key)

    for relative, _, _ in files:
        visit(relative, 0)
    return sources


def _parse_scene(selected: dict[str, Any], scene_path: Path, root: Path,
                 scope: dict[str, Any], conn: ConnectionLike, root_id: str,
                 root_key: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    del scene_path  # The chosen candidate directory may be above the selected scene.
    _, files, warnings = _candidate_sources(selected, root, scope, conn=conn, root_id=root_id, root_key=root_key)
    if not files:
        raise MaterialsCatalogError("MATERIALS_DECK_NOT_FOUND", "씬 우선순위 경로에서 Parts와 Materials 덱을 모두 찾을 수 없습니다.", 404)
    budget = _ParseBudget()
    sources = _include_sources(files, root, scope, budget)

    def stream_lines(path: Path, expected_size: int):
        actual = 0
        try:
            with _open_deck(path) as stream:
                for line in stream:
                    actual += len(line)
                    if actual > MAX_FILE_BYTES:
                        raise MaterialsCatalogError("MATERIALS_FILE_SIZE_LIMIT", "소재 파일은 64 MiB 이하여야 합니다.", 413)
                    budget.check_time()
                    yield line
        except spdm_storage.SpdmStorageError:
            raise
        if actual != expected_size:
            raise MaterialsCatalogError("MATERIALS_FILE_CHANGED", "파싱 중 소재 파일 크기가 변경되었습니다.")

    parser = RadiossDeckParser()
    source_streams = ((stream_lines(path, size), relative) for relative, path, size in sources)
    try:
        parsed = parser.parse_lines(
            (), (), source_streams=source_streams,
            on_input_line=budget.on_input_line,
            on_function_point=budget.on_function_point,
        )
    except MaterialsCatalogError:
        raise
    except spdm_storage.SpdmStorageError as exc:
        raise MaterialsCatalogError(exc.code, str(exc)) from exc
    budget.check_time()
    point_count = sum(len(function.get("raw_points", [])) for function in parsed.get("functions", []))
    if point_count > MAX_FUNCTION_POINTS:
        raise MaterialsCatalogError("MATERIALS_FUNCTION_POINT_LIMIT", "FUNCT 곡선 점 전체는 500,000개 이하여야 합니다.", 413)
    parsed["functions"] = [
        {key: value for key, value in function.items() if key != "raw_points"}
        | {"point_count": len(function.get("points", []))}
        for function in parsed.get("functions", [])
    ]
    return parsed, warnings, [{"relative_path": relative, "size_bytes": size} for relative, _, size in sources]


def deck(conn: ConnectionLike, request_id: str, environment: str,
         scene_id: str | None = None, relative_path: str | None = None) -> dict[str, Any]:
    selected, scene_path, scope = _resolve_scene(conn, request_id, environment, scene_id, relative_path)
    root, root_id, root_key = result_registration_paths.storage_context(conn)
    parsed, candidate_warnings, files = _parse_scene(selected, scene_path, root, scope, conn, root_id, root_key)
    return {"request_id": request_id, "environment": scope["environment"], "scene": selected,
            "files": files, "candidate_warnings": candidate_warnings, "deck": parsed}
