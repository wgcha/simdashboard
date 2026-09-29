"""Bounded, read-only access to Radioss materials stored in SPDM scenes."""
from __future__ import annotations

import re
import time
import os
from pathlib import Path, PurePosixPath
from typing import Any

from ..database_connection import ConnectionLike, rows
from ..parsers.radioss_deck_parser import RadiossDeckParser
from . import folder_discovery_scan, result_registration_paths, spdm_storage

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_PARSE_SECONDS = 20
MAX_FUNCTION_POINTS = 500_000
MAX_DECK_FILES = 5000
MAX_INCLUDE_DEPTH = 5
MAX_INCLUDE_FILES = 5000
_SCENE_NAME = re.compile(r"(scene|result|contour|animation)", re.I)
_DECK_EXTENSIONS = {".inc", ".rad"}
_MATERIAL_CARDS = {"BEGIN", "PARAMETER", "SUBSET", "PROP", "MAT", "FUNCT", "MOVE_FUNCT", "FAIL"}
_MAX_SNIFF_BYTES = 1024 * 1024
_MAX_CANDIDATE_SCAN_BYTES = 32 * 1024 * 1024
_INCLUDE_DIRECTIVE = re.compile(r"^/INCLUDE(?:/(?P<slash>.*)|[ \t]+(?P<space>.*))?[ \t]*$", re.I)


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
    row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    if not row:
        raise MaterialsCatalogError("RESULT_CONTEXT_INVALID", "기존 의뢰를 확인할 수 없습니다.", 404)
    project_id = str(row[0])
    scope = result_registration_paths._scope(conn, project_id, request_id, environment)
    root, root_id, root_key = result_registration_paths._root(conn)
    return project_id, scope, root, root_id, root_key


def _scan_request(root: Path, scope: dict[str, Any]) -> dict[str, Any]:
    try:
        scan = folder_discovery_scan.scan(root, scope["request_relative_path"])
    except (OSError, ValueError, spdm_storage.SpdmStorageError) as exc:
        raise MaterialsCatalogError("MATERIALS_SCAN_UNAVAILABLE", "의뢰 폴더를 안전하게 조사할 수 없습니다.") from exc
    if scan["status"] != "COMPLETE":
        issues = scan.get("issues", [])
        limited = any(str(item.get("code", "")).endswith("LIMIT") for item in issues)
        raise MaterialsCatalogError(
            "MATERIALS_SCAN_LIMIT" if limited else "MATERIALS_SCAN_INCOMPLETE",
            "씬 목록을 안전하게 모두 조사할 수 없습니다. 더 작은 의뢰 폴더를 지정하세요.",
            413 if limited else 422,
        )
    return scan


def _scene_paths(conn: ConnectionLike, root_key: str, scope: dict[str, Any], scan: dict[str, Any]) -> list[str]:
    request_path = str(scope["request_relative_path"])
    candidates = {
        str(node["relative_path"])
        for node in scan["nodes"]
        if _SCENE_NAME.search(str(node.get("name", "")))
    }
    prepared = rows(conn.execute(
        "SELECT relative_path FROM result_registration_paths "
        "WHERE root_key=? AND project_id=? AND request_id=? AND environment=? AND role_kind='SCENE' "
        "ORDER BY relative_path LIMIT ?",
        [root_key, scope["project_id"], scope["request_id"], scope["environment"], MAX_DECK_FILES + 1],
    ))
    if len(prepared) > MAX_DECK_FILES:
        raise MaterialsCatalogError("MATERIALS_SCENE_LIMIT", "씬 후보 수가 허용 한도를 초과했습니다.", 413)
    request_parts = tuple(part.casefold() for part in PurePosixPath(request_path).parts)
    for item in prepared:
        value = str(item["relative_path"])
        parts = PurePosixPath(value).parts
        if tuple(part.casefold() for part in parts[:len(request_parts)]) == request_parts:
            candidates.add(value)
    if len(candidates) > MAX_DECK_FILES:
        raise MaterialsCatalogError("MATERIALS_SCENE_LIMIT", "씬 후보 수가 허용 한도를 초과했습니다.", 413)
    return sorted(candidates, key=str.casefold)


def _scene_entry(conn: ConnectionLike, root: Path, root_id: str, root_key: str,
                 scope: dict[str, Any], relative_path: str) -> dict[str, Any] | None:
    try:
        context, nodes = result_registration_paths._trace_path(
            conn, root, root_id, root_key, scope, relative_path, require_directory=True,
        )
    except result_registration_paths.ResultRegistrationError as exc:
        if exc.code in {"RESULT_PATH_OWNERSHIP_CONFLICT", "RESULT_PATH_ROLE_CONFLICT"}:
            return None
        raise
    scene = context.get("scene")
    if not isinstance(scene, dict) or str(scene.get("relative_path", "")).casefold() != relative_path.casefold():
        return None
    hierarchy: dict[str, Any] = {}
    for key in ("simulation_case", "load_case", "execution_run", "run_option"):
        item = context.get(key)
        if item is not None:
            hierarchy[key] = item
    # The path and role are revalidated through the same trace used by result registration.
    if not nodes or nodes[-1].get("role_kind") != "SCENE":
        return None
    return {
        "scene_id": str(scene["id"]),
        "label": str(scene.get("label") or PurePosixPath(relative_path).name),
        "relative_path": relative_path,
        "hierarchy": hierarchy,
        "has_deck": False,
    }


def _candidate_directories(scene: dict[str, Any], scope: dict[str, Any]) -> list[str]:
    scene_path = str(scene["relative_path"])
    hierarchy = scene.get("hierarchy", {})
    option = hierarchy.get("run_option")
    execution = hierarchy.get("execution_run")
    candidates = [scene_path, f"{scene_path}/INPUT"]
    if isinstance(option, dict) and option.get("status") != "ABSENT":
        candidates.append(str(option["relative_path"]))
    if isinstance(execution, dict):
        execution_path = str(execution["relative_path"])
        candidates.extend((execution_path, f"{execution_path}/INPUT", f"{execution_path}/deck", f"{execution_path}/solver"))
    request_parts = tuple(part.casefold() for part in PurePosixPath(scope["request_relative_path"]).parts)
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


def _file_roles(path: Path) -> set[str]:
    name = path.stem.casefold().replace("-", "_").replace(" ", "_")
    roles: set[str] = set()
    if re.search(r"(?:^|_)parts?(?:_|$)", name):
        roles.add("parts")
    if re.search(r"(?:^|_)(?:mat(?:erial)?s?|props?|curves?|functions?)(?:_|$)", name):
        roles.add("materials")
    try:
        with spdm_storage.open_stable_reader(path) as stream:
            scanned = 0
            for raw_line in stream:
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


def _candidate_sources(scene: dict[str, Any], root: Path, scope: dict[str, Any],
                       budget: dict[str, Any] | None = None) -> tuple[str | None, list[tuple[str, Path, int]], list[dict[str, Any]]]:
    budget = budget if budget is not None else {"started": time.monotonic(), "entries": 0, "sniff_bytes": 0}
    for directory_relative in _candidate_directories(scene, scope):
        try:
            directory = result_registration_paths._safe_existing(root, directory_relative, allow_missing_leaf=True)
        except result_registration_paths.ResultRegistrationError as exc:
            raise MaterialsCatalogError(exc.code, str(exc)) from exc
        if not directory.exists():
            continue
        if not directory.is_dir():
            continue
        parts: list[tuple[str, Path, int]] = []
        materials: list[tuple[str, Path, int]] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    budget["entries"] += 1
                    if budget["entries"] > folder_discovery_scan.MAX_ENTRIES or time.monotonic() - budget["started"] > folder_discovery_scan.MAX_SECONDS:
                        raise MaterialsCatalogError("MATERIALS_CANDIDATE_SCAN_LIMIT", "덱 후보 조사 한도를 초과했습니다.", 413)
                    path = Path(entry.path)
                    if spdm_storage._is_reparse(path):
                        raise MaterialsCatalogError("MATERIALS_PATH_UNSAFE", "덱 후보에 reparse 또는 symbolic link가 있습니다.")
                    if not entry.is_file(follow_symlinks=False) or path.suffix.casefold() not in _DECK_EXTENSIONS:
                        continue
                    relative = path.relative_to(root).as_posix()
                    size = path.stat().st_size
                    roles = _file_roles(path)
                    budget["sniff_bytes"] += min(size, _MAX_SNIFF_BYTES)
                    if budget["sniff_bytes"] > _MAX_CANDIDATE_SCAN_BYTES:
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


def _scene_items(conn: ConnectionLike, root: Path, root_id: str, root_key: str,
                 scope: dict[str, Any], scan: dict[str, Any]) -> list[dict[str, Any]]:
    budget = {"started": time.monotonic(), "entries": 0, "sniff_bytes": 0}
    items = []
    for path in _scene_paths(conn, root_key, scope, scan):
        entry = _scene_entry(conn, root, root_id, root_key, scope, path)
        if entry is None:
            continue
        _, candidates, _ = _candidate_sources(entry, root, scope, budget)
        entry["has_deck"] = bool(candidates)
        items.append(entry)
    items.sort(key=lambda item: (item["label"].casefold(), item["relative_path"].casefold()))
    return items


def catalog(conn: ConnectionLike, request_id: str, environment: str) -> dict[str, Any]:
    project_id, scope, root, root_id, root_key = _request_scope(conn, request_id, environment)
    scan = _scan_request(root, scope)
    items = _scene_items(conn, root, root_id, root_key, scope, scan)
    return {"request_id": request_id, "environment": scope["environment"], "scenes": items}


def _resolve_scene(conn: ConnectionLike, request_id: str, environment: str,
                   scene_id: str | None, relative_path: str | None) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    if not scene_id and not relative_path:
        raise MaterialsCatalogError("MATERIALS_SCENE_REQUIRED", "scene_id 또는 relative_path를 선택하세요.")
    project_id, scope, root, root_id, root_key = _request_scope(conn, request_id, environment)
    scan = _scan_request(root, scope)
    catalog_items = _scene_items(conn, root, root_id, root_key, scope, scan)
    by_id = {item["scene_id"]: item for item in catalog_items}
    by_path = {item["relative_path"].casefold(): item for item in catalog_items}
    by_id_entry = by_id.get(scene_id) if scene_id else None
    by_path_entry = None
    if relative_path:
        normalized = result_registration_paths._relative(relative_path)
        by_path_entry = by_path.get(normalized.casefold())
        if by_path_entry is None:
            # Distinguish a valid folder with the wrong semantic role from a path outside this request.
            context, nodes = result_registration_paths._trace_path(
                conn, root, root_id, root_key, scope, normalized, require_directory=True,
            )
            if not context.get("scene") or str(context["scene"].get("relative_path", "")).casefold() != normalized.casefold() or not nodes or nodes[-1].get("role_kind") != "SCENE":
                raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 경로가 확인된 씬 폴더가 아닙니다.")
            by_path_entry = {
                "scene_id": str(context["scene"]["id"]), "label": str(context["scene"].get("label") or PurePosixPath(normalized).name),
                "relative_path": normalized, "hierarchy": {key: context[key] for key in ("simulation_case", "load_case", "execution_run", "run_option") if context.get(key)},
                "has_deck": False,
            }
            _, candidates, _ = _candidate_sources(by_path_entry, root, scope)
            by_path_entry["has_deck"] = bool(candidates)
    if scene_id and by_id_entry is None:
        raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 씬이 이 의뢰와 환경에 속하지 않습니다.", 404)
    if relative_path and by_path_entry is None:
        raise MaterialsCatalogError("MATERIALS_SCENE_INVALID", "선택한 경로가 이 의뢰와 환경에 속하지 않습니다.", 404)
    if by_id_entry and by_path_entry and by_id_entry["relative_path"].casefold() != by_path_entry["relative_path"].casefold():
        raise MaterialsCatalogError("MATERIALS_SCENE_MISMATCH", "scene_id와 relative_path가 서로 다른 씬을 가리킵니다.")
    selected = by_id_entry or by_path_entry
    assert selected is not None
    scene_path = result_registration_paths._safe_existing(root, selected["relative_path"])
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
        with spdm_storage.open_stable_reader(path) as stream:
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
            path = result_registration_paths._safe_existing(root, relative)
            spdm_storage._assert_safe_existing(path, root)
            if not path.is_file():
                raise MaterialsCatalogError("MATERIALS_INCLUDE_NOT_FILE", "/INCLUDE 대상은 일반 파일이어야 합니다.")
            size = path.stat().st_size
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
                 scope: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    del scene_path  # The chosen candidate directory may be above the selected scene.
    _, files, warnings = _candidate_sources(selected, root, scope)
    if not files:
        raise MaterialsCatalogError("MATERIALS_DECK_NOT_FOUND", "씬 우선순위 경로에서 Parts와 Materials 덱을 모두 찾을 수 없습니다.", 404)
    budget = _ParseBudget()
    sources = _include_sources(files, root, scope, budget)

    def stream_lines(path: Path, expected_size: int):
        actual = 0
        try:
            with spdm_storage.open_stable_reader(path) as stream:
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
    root, _, _ = result_registration_paths._root(conn)
    parsed, candidate_warnings, files = _parse_scene(selected, scene_path, root, scope)
    return {"request_id": request_id, "environment": scope["environment"], "scene": selected,
            "files": files, "candidate_warnings": candidate_warnings, "deck": parsed}
