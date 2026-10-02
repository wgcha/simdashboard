"""Durable, capture-pinned publication of one confirmed Case to Final."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from uuid import uuid4

from ..database_connection import ConnectionLike
from ..config import security_settings
from . import dashboard_capture, folder_schema_locations, folder_schema_resolver, materials_catalog, result_registration_paths, spdm_storage

MAX_FILES = 500
MAX_TOTAL_BYTES = dashboard_capture.MAX_TOTAL_BYTES
MAX_FILE_BYTES = dashboard_capture.MAX_ASSET_BYTES
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_STATUS_ITEMS = 1000
MAX_STATUS_METADATA_BYTES = 64 * 1024 * 1024
MAX_STATUS_VERIFY_BYTES = 1024 * 1024 * 1024
RESULT_EXTENSIONS = {".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm"}
REPORT_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".xlsx"}
SCENE_REPORT_EXTENSIONS = REPORT_EXTENSIONS
DECK_EXTENSIONS = {".rad", ".inc"}
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
_EXCLUDED_DIRS = {"cad", "final", "validation", "library"}


class CaseFinalizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signed_record(record: dict[str, Any], field: str, domain: bytes) -> dict[str, Any]:
    settings = security_settings()
    if not settings.secret_key:
        raise CaseFinalizationError("FINALIZATION_SIGNING_UNAVAILABLE", "최종확정 서명 키가 설정되지 않았습니다.")
    canonical = {key: value for key, value in record.items() if key != field}
    signature = hmac.new(settings.secret_key.encode("utf-8"), domain + _encode(canonical), hashlib.sha256).hexdigest()
    return {**canonical, field: signature}


def _verify_signed_record(record: dict[str, Any], field: str, domain: bytes) -> bool:
    supplied = record.get(field)
    if not isinstance(supplied, str) or not re.fullmatch(r"[0-9a-f]{64}", supplied):
        return False
    try:
        expected = _signed_record(record, field, domain).get(field)
    except CaseFinalizationError:
        return False
    return isinstance(expected, str) and hmac.compare_digest(supplied, expected)


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise CaseFinalizationError("FINALIZATION_PATH_INVALID", "최종확정 상대 경로가 올바르지 않습니다.")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or len(path.parts) > 40:
        raise CaseFinalizationError("FINALIZATION_PATH_INVALID", "최종확정 상대 경로가 올바르지 않습니다.")
    for part in path.parts:
        if part in {"", ".", ".."} or (part != ".finalizations" and not spdm_storage._valid_windows_name(part)):
            raise CaseFinalizationError("FINALIZATION_PATH_INVALID", "최종확정 경로 이름이 Windows 파일 규칙에 맞지 않습니다.")
    return path.as_posix()


def _scope(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
           case_id: str, capture_id: str) -> dict[str, Any]:
    try:
        scope = result_registration_paths._scope(conn, project_id, request_id, environment)
        root, root_id, root_key = result_registration_paths._root(conn)
    except result_registration_paths.ResultRegistrationError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    case_row = conn.execute(
        "SELECT id,project_id,request_id,environment,relative_path,source_name,storage_root_id "
        "FROM dashboard_cases WHERE id=?",
        [case_id],
    ).fetchone()
    if not case_row:
        raise CaseFinalizationError("FINALIZATION_CASE_NOT_FOUND", "선택한 Case 결과를 찾을 수 없습니다.")
    if (str(case_row[1]), str(case_row[2]), str(case_row[3])) != (project_id, request_id, scope["environment"]):
        raise CaseFinalizationError("FINALIZATION_CASE_SCOPE_MISMATCH", "선택한 Case 결과가 프로젝트·의뢰·환경과 일치하지 않습니다.")
    if str(case_row[6]) != root_id:
        raise CaseFinalizationError("FINALIZATION_ROOT_CHANGED", "저장소 설정이 Case 결과를 만든 시점과 다릅니다.")
    case_path = _relative(str(case_row[4]))
    capture = conn.execute(
        "SELECT c.id,c.fingerprint,c.manifest_json,c.payload_json,c.case_id,dc.project_id,dc.request_id,dc.environment,dc.relative_path "
        "FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.id=?",
        [capture_id],
    ).fetchone()
    if not capture:
        raise CaseFinalizationError("FINALIZATION_CAPTURE_NOT_FOUND", "선택한 수집 버전을 찾을 수 없습니다.")
    if (str(capture[4]), str(capture[5]), str(capture[6]), str(capture[7]), str(capture[8])) != (
            case_id, project_id, request_id, scope["environment"], case_path):
        raise CaseFinalizationError("FINALIZATION_CAPTURE_SCOPE_MISMATCH", "수집 버전이 선택한 Case와 일치하지 않습니다.")
    try:
        schema = folder_schema_resolver.resolve_request_schema(
            conn, root, root_key, project_id, request_id, scope["environment"],
        )
        locations = folder_schema_locations.resolve_request_locations(
            conn, project_id, request_id, scope["environment"], schema=schema,
        )
    except folder_schema_resolver.FolderSchemaError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    case_nodes = [node for node in schema.get("nodes", [])
                  if node.get("role_kind") == "SIMULATION_CASE"
                  and str(node.get("relative_path", "")).casefold() == case_path.casefold()
                  and node.get("status") in {"CONFIRMED", "LINKED"}]
    if len(case_nodes) != 1:
        raise CaseFinalizationError("FINALIZATION_CASE_SCHEMA_UNCONFIRMED", "선택한 Case는 현재 Folder Schema에서 확정되지 않았습니다.")
    context = _decode(capture[3]) or {}
    capture_context = context.get("context") if isinstance(context, dict) else None
    if not isinstance(capture_context, dict):
        raise CaseFinalizationError("FINALIZATION_CAPTURE_SCHEMA_MISSING", "수집 버전에 Folder Schema 문맥이 없습니다. Case를 다시 수집하세요.")
    snapshot_id = locations.snapshot_id
    scene_role = "SCENE" if scope["environment"] == "DISTRIBUTION" else "EVALUATION"
    scene_locations = [item for item in locations.locations
                       if item.get("role_kind") == scene_role
                       and item.get("status") in {"CONFIRMED", "LINKED"}
                       and _under(case_path, str(item.get("relative_path") or ""))]
    capture_manifest = _decode(capture[2]) or []
    if not isinstance(capture_manifest, list):
        raise CaseFinalizationError("FINALIZATION_CAPTURE_MANIFEST_INVALID", "수집 버전 파일 목록을 읽을 수 없습니다.")
    capture_locations = capture_context.get("folder_schema_locations")
    if not isinstance(capture_locations, list):
        raise CaseFinalizationError("FINALIZATION_CAPTURE_SCHEMA_MISSING", "수집 버전에 확정 위치 목록이 없습니다. Case를 다시 수집하세요.")
    blocked = folder_schema_locations.blocked_paths_for_case(schema, case_path)
    if not snapshot_id:
        raise CaseFinalizationError("FINALIZATION_SCHEMA_UNAVAILABLE", "현재 확정 Folder Schema snapshot을 찾을 수 없습니다.")
    current_scenes = {str(item.get("relative_path") or "").casefold(): item for item in scene_locations}
    compatible_scene_paths: list[str] = []
    compatible = False
    for captured_scene in capture_locations:
        if not isinstance(captured_scene, dict) or captured_scene.get("role_kind") != scene_role or captured_scene.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        current_scene = current_scenes.get(str(captured_scene.get("relative_path") or "").casefold())
        if current_scene is None:
            continue
        if str(current_scene.get("target_id") or "").casefold() != str(captured_scene.get("target_id") or "").casefold():
            continue
        if _hierarchy_signature(current_scene.get("hierarchy")) != _hierarchy_signature(captured_scene.get("hierarchy")):
            continue
        compatible = True
        compatible_scene_paths.append(str(current_scene["relative_path"]))
    if not compatible:
        raise CaseFinalizationError("FINALIZATION_CAPTURE_SCHEMA_INCOMPATIBLE", "수집 버전의 Scene 위치가 현재 Folder Schema와 일치하지 않습니다. Case를 다시 수집하세요.")
    return {
        "root": root, "root_id": root_id, "root_key": root_key, "scope": scope,
        "case_id": case_id, "case_path": case_path, "case_label": str(case_row[5]),
        "capture_id": capture_id, "capture_fingerprint": str(capture[1]),
        "capture_manifest": capture_manifest, "capture_context": capture_context,
        "capture_locations": capture_locations, "snapshot_id": snapshot_id,
        "compatible_scene_paths": compatible_scene_paths,
        "scene_role": scene_role, "scene_locations": scene_locations, "blocked_paths": blocked,
        "schema": schema,
    }


def _under(parent: str, child: str) -> bool:
    parent_key = parent.rstrip("/").casefold()
    child_key = child.rstrip("/").casefold()
    return child_key == parent_key or child_key.startswith(parent_key + "/")


def _hierarchy_signature(value: Any) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(value, dict):
        return ()
    names = ("load_case", "execution_run", "run_option", "scene")
    signature = []
    for name in names:
        node = value.get(name)
        if isinstance(node, dict):
            signature.append((name, str(node.get("target_id") or "").casefold(),
                              str(node.get("relative_path") or "").casefold()))
    return tuple(signature)


def _blocked(path: str, blocked_paths: list[str]) -> bool:
    key = path.casefold().rstrip("/")
    return any(key == item.casefold().rstrip("/") or key.startswith(item.casefold().rstrip("/") + "/")
               for item in blocked_paths)


def _read_source(root: Path, relative: str) -> tuple[bytes, str]:
    try:
        path = result_registration_paths._safe_existing(root, relative)
        if not path.is_file():
            raise CaseFinalizationError("FINALIZATION_SOURCE_MISSING", "수집된 원본 파일을 찾을 수 없습니다.")
        data, _signature = spdm_storage.read_stable_bytes(path, max_bytes=MAX_FILE_BYTES)
        spdm_storage._assert_safe_existing(path, root)
        return data, _digest(data)
    except CaseFinalizationError:
        raise
    except result_registration_paths.ResultRegistrationError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "원본 파일을 안정적으로 읽을 수 없습니다.") from exc


def _capture_scenes(context_locations: list[Any], scope: dict[str, Any]) -> list[str]:
    allowed = []
    current = {path.casefold() for path in scope.get("compatible_scene_paths", [])}
    for item in context_locations:
        if not isinstance(item, dict) or item.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        if str(item.get("role_kind") or "") != scope["scene_role"]:
            continue
        path = item.get("relative_path")
        if isinstance(path, str) and path.casefold() in current and _under(scope["case_path"], path):
            allowed.append(_relative(path))
    return allowed


def _scan_scene_support_files(scope: dict[str, Any]) -> list[str]:
    root: Path = scope["root"]
    scene_roots = [str(path) for path in scope["compatible_scene_paths"]]
    found: list[str] = []
    visited = 0
    blocked = scope["blocked_paths"]
    try:
        for scene_relative in scene_roots:
            scene = result_registration_paths._safe_existing(root, scene_relative)
            if not scene.is_dir():
                continue
            stack: list[tuple[Path, int]] = [(scene, 0)]
            while stack:
                directory, depth = stack.pop()
                if depth > 16:
                    raise CaseFinalizationError("FINALIZATION_DEPTH_LIMIT", "Scene 입력 파일의 폴더 깊이 제한을 초과했습니다.")
                with os.scandir(directory) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > 20000:
                            raise CaseFinalizationError("FINALIZATION_SCAN_LIMIT", "Scene 입력 파일 조사 범위를 초과했습니다.")
                        if entry.name.startswith(".") or entry.name.casefold() in _EXCLUDED_DIRS:
                            continue
                        item_path = Path(entry.path)
                        spdm_storage._assert_safe_existing(item_path, root)
                        item_relative = item_path.relative_to(root).as_posix()
                        if _blocked(item_relative, blocked):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append((item_path, depth + 1))
                        elif entry.is_file(follow_symlinks=False) and item_path.suffix.casefold() in DECK_EXTENSIONS | SCENE_REPORT_EXTENSIONS:
                            found.append(_relative(item_relative))
                            if len(found) > MAX_FILES:
                                raise CaseFinalizationError("FINALIZATION_FILE_LIMIT", "최종확정 파일 수 제한을 초과했습니다.")
        return sorted(set(found), key=str.casefold)
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "Scene 입력 파일을 조사할 수 없습니다.") from exc


def _include_closure(scope: dict[str, Any], entry_files: list[str]) -> list[str]:
    request_path = str(scope["scope"]["request_relative_path"])
    allowed_scene_roots = [str(path) for path in scope["compatible_scene_paths"]]
    blocked = scope["blocked_paths"]
    budget = materials_catalog._ParseBudget()
    included: list[str] = []
    visited: set[str] = set()

    def visit(relative: str, depth: int) -> None:
        key = relative.casefold()
        if key in visited:
            return
        if depth > 5 or len(visited) >= 100:
            raise CaseFinalizationError("FINALIZATION_INCLUDE_LIMIT", "덱 include 참조가 깊이 또는 파일 수 제한을 넘었습니다.")
        if not any(_under(scene_root, relative) for scene_root in allowed_scene_roots) or _blocked(relative, blocked):
            raise CaseFinalizationError("FINALIZATION_INCLUDE_OUT_OF_SCOPE", f"덱 include 파일이 확정 Scene 범위 밖에 있습니다: {PurePosixPath(relative).name}")
        try:
            path = result_registration_paths._safe_existing(scope["root"], relative)
            spdm_storage._assert_safe_existing(path, scope["root"])
            if not path.is_file() or path.suffix.casefold() not in DECK_EXTENSIONS:
                raise CaseFinalizationError("FINALIZATION_INCLUDE_UNSUPPORTED", "include 참조는 확정 Scene 안의 .rad 또는 .inc 파일이어야 합니다.")
            size = path.stat().st_size
            budget.add_file(size)
            references = materials_catalog._scan_include_references(path, relative, size, budget)
            included.append(relative)
            visited.add(key)
            parse_scope = {"request_relative_path": request_path}
            for reference in references:
                try:
                    target = materials_catalog._include_target_relative(parse_scope, relative, reference)
                except materials_catalog.MaterialsCatalogError as exc:
                    raise CaseFinalizationError(exc.code, str(exc)) from exc
                visit(_relative(target), depth + 1)
        except CaseFinalizationError:
            raise
        except materials_catalog.MaterialsCatalogError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
        except result_registration_paths.ResultRegistrationError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
        except spdm_storage.SpdmStorageError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
        except OSError as exc:
            raise CaseFinalizationError("FINALIZATION_INCLUDE_UNAVAILABLE", "덱 include 참조를 읽을 수 없습니다.") from exc

    for entry in entry_files:
        visit(entry, 0)
    return sorted(set(included), key=str.casefold)


@contextmanager
def _pin_directory_chain(root: Path, directory: Path) -> Iterator[None]:
    """Pin every existing directory ancestor without allowing Windows rename/reparse swaps."""
    try:
        root_path = Path(os.path.abspath(root))
        directory_path = Path(os.path.abspath(directory))
        directory_path.relative_to(root_path)
    except (OSError, ValueError) as exc:
        raise CaseFinalizationError("FINALIZATION_PATH_UNSAFE", "최종확정 대상 폴더가 SPDM root 밖에 있습니다.") from exc
    if os.name != "nt":
        try:
            spdm_storage._assert_safe_existing(directory_path, root_path)
        except spdm_storage.SpdmStorageError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
        yield
        return

    import ctypes
    from ctypes import wintypes

    class _FileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD), ("ftCreationTimeLow", wintypes.DWORD),
            ("ftCreationTimeHigh", wintypes.DWORD), ("ftLastAccessTimeLow", wintypes.DWORD),
            ("ftLastAccessTimeHigh", wintypes.DWORD), ("ftLastWriteTimeLow", wintypes.DWORD),
            ("ftLastWriteTimeHigh", wintypes.DWORD), ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD), ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD), ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    relative_parts = directory_path.relative_to(root_path).parts
    chain: list[Path] = [root_path]
    cursor = root_path
    for part in relative_parts:
        cursor = cursor / part
        chain.append(cursor)
    handles: list[Any] = []
    try:
        for candidate in chain:
            handle = kernel32.CreateFileW(
                str(candidate), 0x00000081, 0x00000003, None, 3,
                0x02000000 | 0x00200000, None,
            )
            invalid = ctypes.c_void_p(-1).value
            if handle == invalid:
                raise CaseFinalizationError("FINALIZATION_PATH_BUSY", "최종확정 경로를 안전하게 고정할 수 없습니다.")
            handles.append(handle)
            info = _FileInformation()
            if (not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info))
                    or not info.dwFileAttributes & 0x10 or info.dwFileAttributes & 0x400):
                raise CaseFinalizationError("FINALIZATION_PATH_UNSAFE", "최종확정 경로에 reparse point 또는 일반 폴더가 아닌 항목이 있습니다.")
        yield
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


def _build_files(scope: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    root = scope["root"]
    scene_paths = _capture_scenes(scope["capture_locations"], scope)
    if not scene_paths:
        raise CaseFinalizationError("FINALIZATION_SCENE_UNCONFIRMED", "수집 버전에 연결된 확정 Scene이 없습니다.")
    allowed_scene_paths = {path.casefold() for path in scene_paths}
    blocked = scope["blocked_paths"]
    planned: dict[str, dict[str, Any]] = {}
    excluded_count = 0
    total = 0
    entries = sorted(scope["capture_manifest"], key=lambda item: str(item.get("relative_path", "")).casefold()
                     if isinstance(item, dict) else "")
    for item in entries:
        if not isinstance(item, dict):
            raise CaseFinalizationError("FINALIZATION_CAPTURE_MANIFEST_INVALID", "수집 버전 파일 목록이 올바르지 않습니다.")
        source = _relative(str(item.get("relative_path") or ""))
        suffix = PurePosixPath(source).suffix.casefold()
        if suffix not in RESULT_EXTENSIONS | REPORT_EXTENSIONS:
            continue
        if not _under(scope["case_path"], source):
            raise CaseFinalizationError("FINALIZATION_CAPTURE_SCOPE_MISMATCH", "수집 파일이 Case 바깥을 가리킵니다.")
        if not any(_under(scene_path, source) for scene_path in allowed_scene_paths) or _blocked(source, blocked):
            excluded_count += 1
            continue
        data, current_hash = _read_source(root, source)
        expected_hash = str(item.get("sha256") or "").casefold()
        try:
            expected_size = int(item.get("size", -1))
        except (TypeError, ValueError):
            expected_size = -1
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or current_hash != expected_hash or len(data) != expected_size:
            raise CaseFinalizationError("FINALIZATION_SOURCE_STALE", f"수집 후 원본이 바뀌었습니다: {source}")
        category = "Reports"
        relative_case_path = PurePosixPath(source).relative_to(PurePosixPath(scope["case_path"])).as_posix()
        key = relative_case_path.casefold()
        if key in planned:
            raise CaseFinalizationError("FINALIZATION_PATH_COLLISION", "대소문자만 다른 중복 파일이 있습니다.")
        total += len(data)
        planned[key] = {"source_relative_path": source, "case_relative_path": relative_case_path,
                        "category": category, "source_basis": "SELECTED_CAPTURE",
                        "size": len(data), "sha256": current_hash}
    extra_scene_files = _scan_scene_support_files(scope)
    deck_entries = [source for source in extra_scene_files if PurePosixPath(source).suffix.casefold() in DECK_EXTENSIONS]
    for source in _include_closure(scope, deck_entries):
        data, current_hash = _read_source(root, source)
        relative_case_path = PurePosixPath(source).relative_to(PurePosixPath(scope["case_path"])).as_posix()
        key = relative_case_path.casefold()
        if key in planned:
            continue
        total += len(data)
        planned[key] = {"source_relative_path": source, "case_relative_path": relative_case_path,
                        "category": "CAE", "source_basis": "CURRENT_CONFIRMED_SCENE",
                        "size": len(data), "sha256": current_hash}
    for source in extra_scene_files:
        if PurePosixPath(source).suffix.casefold() not in SCENE_REPORT_EXTENSIONS:
            continue
        data, current_hash = _read_source(root, source)
        relative_case_path = PurePosixPath(source).relative_to(PurePosixPath(scope["case_path"])).as_posix()
        key = relative_case_path.casefold()
        if key in planned:
            continue
        total += len(data)
        planned[key] = {"source_relative_path": source, "case_relative_path": relative_case_path,
                        "category": "Reports", "source_basis": "CURRENT_CONFIRMED_SCENE",
                        "size": len(data), "sha256": current_hash}
    if len(planned) > MAX_FILES:
        raise CaseFinalizationError("FINALIZATION_FILE_LIMIT", "최종확정 파일 수 제한을 초과했습니다.")
    if total > MAX_TOTAL_BYTES:
        raise CaseFinalizationError("FINALIZATION_TOTAL_SIZE_LIMIT", "최종확정 전체 파일은 256 MiB 이하여야 합니다.")
    return sorted(planned.values(), key=lambda item: item["case_relative_path"].casefold()), excluded_count


@contextmanager
def _request_lock(path: Path, root: Path) -> Iterator[None]:
    """Process-wide, OS-released lock; the persistent file is never deleted."""
    with _pin_directory_chain(root, path.parent):
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            class _LockFileInformation(ctypes.Structure):
                _fields_ = [
                    ("dwFileAttributes", wintypes.DWORD), ("ftCreationTimeLow", wintypes.DWORD),
                    ("ftCreationTimeHigh", wintypes.DWORD), ("ftLastAccessTimeLow", wintypes.DWORD),
                    ("ftLastAccessTimeHigh", wintypes.DWORD), ("ftLastWriteTimeLow", wintypes.DWORD),
                    ("ftLastWriteTimeHigh", wintypes.DWORD), ("dwVolumeSerialNumber", wintypes.DWORD),
                    ("nFileSizeHigh", wintypes.DWORD), ("nFileSizeLow", wintypes.DWORD),
                    ("nNumberOfLinks", wintypes.DWORD), ("nFileIndexHigh", wintypes.DWORD),
                    ("nFileIndexLow", wintypes.DWORD),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_LockFileInformation)]
            kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            file_handle = kernel32.CreateFileW(
                str(path), 0xC0000000, 0x00000003, None, 4,
                0x00000080 | 0x00200000, None,
            )
            invalid = ctypes.c_void_p(-1).value
            if file_handle == invalid:
                raise CaseFinalizationError("FINALIZATION_LOCK_UNAVAILABLE", "최종확정 잠금 파일을 안전하게 열 수 없습니다.")
            info = _LockFileInformation()
            if (not kernel32.GetFileInformationByHandle(file_handle, ctypes.byref(info))
                    or info.dwFileAttributes & (0x10 | 0x400) or info.nNumberOfLinks != 1):
                kernel32.CloseHandle(file_handle)
                raise CaseFinalizationError("FINALIZATION_LOCK_UNSAFE", "최종확정 잠금 파일이 일반 단일 연결 파일이 아닙니다.")
            descriptor = msvcrt.open_osfhandle(file_handle, os.O_RDWR)
        else:
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(descriptor)
                raise CaseFinalizationError("FINALIZATION_LOCK_UNSAFE", "최종확정 잠금 파일이 일반 단일 연결 파일이 아닙니다.")
        with os.fdopen(descriptor, "r+b", closefd=True) as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ensure_dir(root: Path, relative: str) -> Path:
    current = root
    with ExitStack() as pins:
        pins.enter_context(_pin_directory_chain(root, current))
        for part in PurePosixPath(_relative(relative)).parts:
            if part != ".finalizations" and not spdm_storage._valid_windows_name(part):
                raise CaseFinalizationError("FINALIZATION_PATH_INVALID", "최종확정 경로에 사용할 수 없는 이름이 있습니다.")
            collision = spdm_storage._case_collision(current, part)
            candidate = collision if collision is not None else current / part
            if candidate.exists():
                pins.enter_context(_pin_directory_chain(root, candidate))
                if not candidate.is_dir():
                    raise CaseFinalizationError("FINALIZATION_PATH_CONFLICT", "최종확정 폴더 경로에 파일이 있습니다.")
            else:
                try:
                    candidate.mkdir()
                except FileExistsError:
                    collision = spdm_storage._case_collision(current, part)
                    candidate = collision if collision is not None else candidate
                    if not candidate.is_dir():
                        raise CaseFinalizationError("FINALIZATION_PATH_CONFLICT", "최종확정 폴더를 안전하게 만들 수 없습니다.")
                pins.enter_context(_pin_directory_chain(root, candidate))
            current = candidate
        return current


def _final_paths(scope: dict[str, Any]) -> tuple[str, str]:
    request_path = _relative(str(scope["scope"]["request_relative_path"]))
    return f"{request_path}/Final", f"{request_path}/Final/.finalizations"


def _plan_hash(plan: dict[str, Any]) -> str:
    return _digest(_encode({key: value for key, value in plan.items() if key != "plan_signature"}))


def _read_json(path: Path, *, status_metadata_budget: list[int] | None = None) -> dict[str, Any] | None:
    try:
        before = path.lstat()
        attributes = getattr(before, "st_file_attributes", 0)
        if (not stat.S_ISREG(before.st_mode)
                or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            return None

        if os.name == "nt":
            reader = spdm_storage.open_stable_reader(path)
        else:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            reader = os.fdopen(descriptor, "rb", closefd=True)

        if os.name == "nt":
            stream_context = reader
        else:
            # POSIX open_stable_reader does not use O_NOFOLLOW; keep the
            # descriptor opened above so a symlink swap cannot redirect it.
            from contextlib import closing
            stream_context = closing(reader)

        with stream_context as stream:
            opened_before = os.fstat(stream.fileno())
            opened_attributes = getattr(opened_before, "st_file_attributes", 0)
            if (not stat.S_ISREG(opened_before.st_mode)
                    or opened_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    or opened_before.st_size > MAX_METADATA_BYTES):
                return None
            if status_metadata_budget is not None:
                if status_metadata_budget[0] + opened_before.st_size > MAX_STATUS_METADATA_BYTES:
                    raise CaseFinalizationError(
                        "FINALIZATION_STATUS_LIMIT",
                        f"최종확정 메타데이터 조회 누계가 {MAX_STATUS_METADATA_BYTES // (1024 * 1024)} MiB 제한을 초과했습니다. 이력을 안전하게 판정할 수 없습니다.",
                    )
                status_metadata_budget[0] += opened_before.st_size
            payload = stream.read(MAX_METADATA_BYTES + 1)
            if len(payload) > MAX_METADATA_BYTES:
                return None
            opened_after = os.fstat(stream.fileno())
            after = path.lstat()
            signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            after_attributes = getattr(after, "st_file_attributes", 0)
            if (not stat.S_ISREG(after.st_mode)
                    or after_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    or signature(opened_before) != signature(opened_after)
                    or signature(opened_after) != signature(after)
                    or len(payload) != opened_after.st_size):
                return None
        value = json.loads(payload.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except CaseFinalizationError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError, spdm_storage.SpdmStorageError):
        return None


def preview(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
            case_id: str, capture_id: str, actor: str) -> dict[str, Any]:
    scope = _scope(conn, project_id, request_id, environment, case_id, capture_id)
    final_relative, metadata_relative = _final_paths(scope)
    request_root = result_registration_paths._safe_existing(scope["root"], scope["scope"]["request_relative_path"])
    if not request_root.is_dir():
        raise CaseFinalizationError("FINALIZATION_REQUEST_FOLDER_INVALID", "확정된 의뢰 폴더를 찾을 수 없습니다.")
    _ensure_dir(scope["root"], final_relative)
    metadata_dir = _ensure_dir(scope["root"], metadata_relative)
    lock_path = metadata_dir / ".request.lock"
    with _request_lock(lock_path, scope["root"]):
        # Re-resolve the current schema after obtaining the request lock.
        scope = _scope(conn, project_id, request_id, environment, case_id, capture_id)
        files, excluded_count = _build_files(scope)
        operation_id = uuid4().hex
        operation_relative = f"{metadata_relative}/{operation_id}"
        operation_dir = _ensure_dir(scope["root"], operation_relative)
        counts = {"CAE": 0, "Reports": 0, "input_decks": 0, "rad_decks": 0, "inc_decks": 0, "reports": 0, "results": 0}
        for item in files:
            counts[item["category"]] += 1
            if item["category"] == "CAE":
                counts["input_decks"] += 1
                if PurePosixPath(item["source_relative_path"]).suffix.casefold() == ".rad":
                    counts["rad_decks"] += 1
                else:
                    counts["inc_decks"] += 1
            elif PurePosixPath(item["source_relative_path"]).suffix.casefold() in REPORT_EXTENSIONS:
                counts["reports"] += 1
            else:
                counts["results"] += 1
        missing = {"input_decks": counts["input_decks"] == 0, "rad_decks": counts["rad_decks"] == 0,
                   "inc_decks": counts["inc_decks"] == 0, "reports": counts["reports"] == 0}
        plan = {
            "schema_version": 1, "operation_id": operation_id, "status": "PREVIEW",
            "project_id": project_id, "request_id": request_id,
            "environment": str(environment).upper(), "case_id": case_id,
            "case_path": scope["case_path"], "case_label": scope["case_label"],
            "capture_id": capture_id, "capture_fingerprint": scope["capture_fingerprint"],
            "folder_schema_snapshot_id": scope["snapshot_id"],
            "scene_paths": sorted(set(scope["compatible_scene_paths"]), key=str.casefold),
            "metadata_relative_path": operation_relative, "final_relative_path": final_relative,
            "created_by": actor, "previewed_at": _now(), "excluded_capture_file_count": excluded_count,
            "counts": counts, "missing": missing, "files": files,
        }
        plan = _signed_record(plan, "plan_signature", b"case-finalization:plan:v1\0")
        plan_bytes = _encode(plan)
        if len(plan_bytes) > MAX_METADATA_BYTES:
            raise CaseFinalizationError("FINALIZATION_METADATA_LIMIT", "최종확정 계획이 메타데이터 크기 제한을 초과했습니다.")
        plan_path = operation_dir / "plan.json"
        with _pin_directory_chain(scope["root"], operation_dir):
            spdm_storage._assert_safe_existing(operation_dir, scope["root"])
            with plan_path.open("xb") as handle:
                handle.write(plan_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        return {**plan, "plan_sha256": _plan_hash(plan), "can_confirm": bool(files)}


def _verify_plan_scope(plan: dict[str, Any], scope: dict[str, Any], operation_id: str,
                       expected_files: list[dict[str, Any]], expected_excluded: int) -> None:
    if not _verify_signed_record(plan, "plan_signature", b"case-finalization:plan:v1\0"):
        raise CaseFinalizationError("FINALIZATION_PLAN_UNTRUSTED", "저장된 최종확정 계획의 서명을 확인할 수 없습니다. 새 미리보기를 만드세요.")
    expected = (operation_id, scope["scope"]["project_id"], scope["scope"]["request_id"],
                scope["scope"]["environment"], scope["case_id"], scope["capture_id"], scope["case_path"])
    actual = (str(plan.get("operation_id")), str(plan.get("project_id")), str(plan.get("request_id")),
              str(plan.get("environment")), str(plan.get("case_id")), str(plan.get("capture_id")), str(plan.get("case_path")))
    if actual != expected:
        raise CaseFinalizationError("FINALIZATION_OPERATION_SCOPE_MISMATCH", "최종확정 계획이 현재 Case 문맥과 일치하지 않습니다.")
    if plan.get("folder_schema_snapshot_id") != scope["snapshot_id"]:
        raise CaseFinalizationError("FINALIZATION_SCHEMA_STALE", "Folder Schema가 미리보기 이후 변경되었습니다. 새 미리보기를 만드세요.")
    if plan.get("capture_fingerprint") != scope["capture_fingerprint"]:
        raise CaseFinalizationError("FINALIZATION_CAPTURE_CHANGED", "수집 버전이 미리보기 이후 변경되었습니다.")
    final_relative, metadata_relative = _final_paths(scope)
    expected_scenes = sorted(set(scope["compatible_scene_paths"]), key=str.casefold)
    counts = {"CAE": 0, "Reports": 0, "input_decks": 0, "rad_decks": 0, "inc_decks": 0, "reports": 0, "results": 0}
    for item in expected_files:
        counts[item["category"]] += 1
        if item["category"] == "CAE":
            counts["input_decks"] += 1
            if PurePosixPath(item["source_relative_path"]).suffix.casefold() == ".rad":
                counts["rad_decks"] += 1
            else:
                counts["inc_decks"] += 1
        elif PurePosixPath(item["source_relative_path"]).suffix.casefold() in REPORT_EXTENSIONS:
            counts["reports"] += 1
        else:
            counts["results"] += 1
    expected = {
        "case_label": scope["case_label"], "final_relative_path": final_relative,
        "metadata_relative_path": f"{metadata_relative}/{operation_id}",
        "scene_paths": expected_scenes, "files": expected_files, "counts": counts,
        "missing": {"input_decks": counts["input_decks"] == 0, "rad_decks": counts["rad_decks"] == 0,
                     "inc_decks": counts["inc_decks"] == 0, "reports": counts["reports"] == 0},
        "excluded_capture_file_count": expected_excluded,
    }
    for key, value in expected.items():
        if plan.get(key) != value:
            raise CaseFinalizationError("FINALIZATION_PLAN_CHANGED", "저장된 미리보기 계획이 현재 확정 경로·파일 목록과 다릅니다. 새 미리보기를 만드세요.")


def _target_directory(scope: dict[str, Any], plan: dict[str, Any], category: str) -> tuple[str, Path]:
    case_label = str(plan.get("case_label") or "")
    if not spdm_storage._valid_windows_name(case_label):
        raise CaseFinalizationError("FINALIZATION_CASE_LABEL_INVALID", "Case 이름을 안전한 Windows 폴더 이름으로 사용할 수 없습니다.")
    relative = f"{plan['final_relative_path']}/{category}/{case_label}/{plan['operation_id']}"
    return relative, _ensure_dir(scope["root"], relative)


def _expected_output_paths(plan: dict[str, Any]) -> dict[str, str]:
    operation_id = str(plan.get("operation_id") or "")
    case_label = str(plan.get("case_label") or "")
    final_relative = _relative(str(plan.get("final_relative_path") or ""))
    if not _OPERATION_ID.fullmatch(operation_id) or not spdm_storage._valid_windows_name(case_label):
        raise CaseFinalizationError("FINALIZATION_PLAN_INVALID", "최종확정 계획의 출력 경로가 올바르지 않습니다.")
    return {category: f"{final_relative}/{category}/{case_label}/{operation_id}" for category in ("CAE", "Reports")}


def _copy_one(scope: dict[str, Any], plan: dict[str, Any], item: dict[str, Any], target_root: Path) -> str:
    root: Path = scope["root"]
    source = _relative(str(item["source_relative_path"]))
    data, current_hash = _read_source(root, source)
    if current_hash != str(item["sha256"]) or len(data) != int(item["size"]):
        raise CaseFinalizationError("FINALIZATION_SOURCE_STALE", f"미리보기 이후 원본이 바뀌었습니다: {source}")
    relative_path = _relative(str(item["case_relative_path"]))
    parts = PurePosixPath(relative_path).parts
    parent_relative = target_root.relative_to(root).as_posix()
    if len(parts) > 1:
        parent_relative += "/" + "/".join(parts[:-1])
        parent = _ensure_dir(root, parent_relative)
    else:
        parent = target_root
    with _pin_directory_chain(root, parent):
        return _publish_copy(root, parent, parts[-1], data, current_hash, plan["operation_id"], relative_path)


def _publish_copy(root: Path, parent: Path, filename: str, data: bytes, current_hash: str,
                  operation_id: str, relative_path: str) -> str:
    collision = spdm_storage._case_collision(parent, filename)
    destination = collision if collision is not None else parent / filename
    if destination.exists():
        spdm_storage._assert_safe_existing(destination, root)
        try:
            _existing_data, existing_hash = _read_source(root, destination.relative_to(root).as_posix())
        except CaseFinalizationError:
            raise CaseFinalizationError("FINALIZATION_DESTINATION_CONFLICT", f"기존 최종 파일을 덮어쓰지 않았습니다: {relative_path}")
        if not destination.is_file() or existing_hash != current_hash:
            raise CaseFinalizationError("FINALIZATION_DESTINATION_CONFLICT", f"기존 최종 파일을 덮어쓰지 않았습니다: {relative_path}")
        return destination.relative_to(root).as_posix()
    temporary = parent / f".codex-partial-{operation_id}-{uuid4().hex}"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        spdm_storage._assert_safe_existing(temporary, root)
        try:
            if os.name == "nt":
                # Windows rename is atomic and refuses to replace an existing destination.
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
                temporary.unlink()
        except FileExistsError:
            collision = spdm_storage._case_collision(parent, filename)
            destination = collision if collision is not None else destination
            try:
                _existing_data, existing_hash = _read_source(root, destination.relative_to(root).as_posix())
            except CaseFinalizationError:
                existing_hash = ""
            if not destination.is_file() or existing_hash != current_hash:
                raise CaseFinalizationError("FINALIZATION_DESTINATION_CONFLICT", f"기존 최종 파일을 덮어쓰지 않았습니다: {relative_path}")
        spdm_storage._assert_safe_existing(destination, root)
        return destination.relative_to(root).as_posix()
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _verify_outputs(scope: dict[str, Any], plan: dict[str, Any], output_paths: dict[str, str]) -> bool:
    root: Path = scope["root"]
    for item in plan["files"]:
        category = str(item["category"])
        if category not in output_paths:
            return False
        try:
            source = _relative(str(item["source_relative_path"]))
            case_relative = _relative(str(item["case_relative_path"]))
            case_path = _relative(str(plan["case_path"]))
        except (CaseFinalizationError, KeyError, TypeError):
            return False
        if not _under(case_path, source) or PurePosixPath(source).relative_to(PurePosixPath(case_path)).as_posix().casefold() != case_relative.casefold():
            return False
        base = result_registration_paths._safe_existing(root, output_paths[category])
        relative = f"{output_paths[category]}/{case_relative}"
        path = result_registration_paths._safe_existing(root, relative)
        if not path.is_file() or _read_source(root, relative)[1] != item["sha256"]:
            return False
        spdm_storage._assert_safe_existing(path, root)
        if not path.relative_to(base).as_posix():
            return False
    for relative in output_paths.values():
        path = result_registration_paths._safe_existing(root, relative)
        if not path.is_dir():
            return False
    return True


def _cleanup_partial_files(scope: dict[str, Any], output_paths: dict[str, str], operation_id: str) -> None:
    root: Path = scope["root"]
    for relative in output_paths.values():
        base = result_registration_paths._safe_existing(root, relative)
        for directory, dirs, files in os.walk(base, followlinks=False):
            directory_path = Path(directory)
            spdm_storage._assert_safe_existing(directory_path, root)
            safe_dirs = []
            for name in dirs:
                candidate_dir = directory_path / name
                try:
                    spdm_storage._assert_safe_existing(candidate_dir, root)
                    if candidate_dir.is_dir():
                        safe_dirs.append(name)
                except spdm_storage.SpdmStorageError:
                    continue
            dirs[:] = safe_dirs
            for name in files:
                if name.startswith(f".codex-partial-{operation_id}-"):
                    candidate = directory_path / name
                    try:
                        with _pin_directory_chain(root, directory_path):
                            spdm_storage._assert_safe_existing(candidate, root)
                            candidate.unlink()
                    except (OSError, CaseFinalizationError, spdm_storage.SpdmStorageError):
                        pass


def _completed_if_valid(conn: ConnectionLike, base_scope: dict[str, Any], *, project_id: str,
                        request_id: str, environment: str, case_id: str, capture_id: str,
                        operation_id: str, metadata_relative: str) -> dict[str, Any] | None:
    operation_relative = f"{metadata_relative}/{operation_id}"
    try:
        operation_dir = result_registration_paths._safe_existing(base_scope["root"], operation_relative)
        spdm_storage._assert_safe_existing(operation_dir, base_scope["root"])
        plan_path = operation_dir / "plan.json"
        complete_path = operation_dir / "complete.json"
        if not plan_path.exists() or not complete_path.exists():
            return None
        spdm_storage._assert_safe_existing(plan_path, base_scope["root"])
        spdm_storage._assert_safe_existing(complete_path, base_scope["root"])
        plan = _read_json(plan_path)
        complete = _read_json(complete_path)
        if (not plan or not complete
                or not _verify_signed_record(plan, "plan_signature", b"case-finalization:plan:v1\0")
                or not _verify_signed_record(complete, "complete_signature", b"case-finalization:complete:v1\0")):
            return None
        if (plan.get("operation_id"), plan.get("project_id"), plan.get("request_id"), plan.get("environment"),
                plan.get("case_id"), plan.get("capture_id"), plan.get("case_path"), plan.get("case_label")) != (
                operation_id, project_id, request_id, str(environment).upper(), case_id,
                capture_id, base_scope["case_path"], base_scope["case_label"]):
            return None
        final_relative, expected_metadata = _final_paths(base_scope)
        if plan.get("final_relative_path") != final_relative or plan.get("metadata_relative_path") != f"{expected_metadata}/{operation_id}":
            return None
        capture = conn.execute(
            "SELECT c.fingerprint,c.case_id,dc.project_id,dc.request_id,dc.environment,dc.relative_path "
            "FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.id=?",
            [capture_id],
        ).fetchone()
        if (not capture or (str(capture[1]), str(capture[2]), str(capture[3]), str(capture[4]), str(capture[5])) !=
                (case_id, project_id, request_id, str(environment).upper(), base_scope["case_path"])
                or str(capture[0]) != str(plan.get("capture_fingerprint"))):
            return None
        expected_outputs = _expected_output_paths(plan)
        if (complete.get("status") != "COMPLETE" or complete.get("operation_id") != operation_id
                or complete.get("project_id") != project_id or complete.get("request_id") != request_id
                or complete.get("environment") != str(environment).upper() or complete.get("case_id") != case_id
                or complete.get("capture_id") != capture_id or complete.get("files") != plan.get("files")
                or complete.get("output_paths") != expected_outputs
                or complete.get("plan_sha256") != _plan_hash(plan)):
            return None
        if not _verify_outputs(base_scope, plan, expected_outputs):
            return None
        return _completed_response(plan, complete)
    except (CaseFinalizationError, result_registration_paths.ResultRegistrationError,
            spdm_storage.SpdmStorageError, OSError, TypeError, ValueError, KeyError):
        return None


def confirm(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
            case_id: str, capture_id: str, operation_id: str, actor: str) -> dict[str, Any]:
    if not _OPERATION_ID.fullmatch(operation_id):
        raise CaseFinalizationError("FINALIZATION_OPERATION_ID_INVALID", "최종확정 요청 ID가 올바르지 않습니다.")
    base_scope = _scope_for_status(conn, project_id, request_id, environment, case_id)
    _final_relative, metadata_relative = _final_paths(base_scope)
    metadata_dir = result_registration_paths._safe_existing(base_scope["root"], metadata_relative, allow_missing_leaf=True)
    if not metadata_dir.is_dir():
        raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
    with _request_lock(metadata_dir / ".request.lock", base_scope["root"]):
        completed_record = _completed_if_valid(
            conn, base_scope, project_id=project_id, request_id=request_id,
            environment=environment, case_id=case_id, capture_id=capture_id,
            operation_id=operation_id, metadata_relative=metadata_relative,
        )
        if completed_record:
            return completed_record
        operation_relative = f"{metadata_relative}/{operation_id}"
        operation_dir = result_registration_paths._safe_existing(base_scope["root"], operation_relative)
        complete_path = operation_dir / "complete.json"
        if complete_path.exists():
            raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "기존 완료 표식과 파일이 일치하지 않습니다. 기존 자료를 보존하고 관리자에게 문의하세요.")
        scope = _scope(conn, project_id, request_id, environment, case_id, capture_id)
        operation_relative = f"{metadata_relative}/{operation_id}"
        operation_dir = result_registration_paths._safe_existing(scope["root"], operation_relative)
        plan_path = operation_dir / "plan.json"
        spdm_storage._assert_safe_existing(operation_dir, scope["root"])
        spdm_storage._assert_safe_existing(plan_path, scope["root"])
        plan = _read_json(plan_path)
        if not plan:
            raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
        expected_files, expected_excluded = _build_files(scope)
        _verify_plan_scope(plan, scope, operation_id, expected_files, expected_excluded)
        plan_hash = _plan_hash(plan)
        complete_path = operation_dir / "complete.json"
        if complete_path.exists():
            spdm_storage._assert_safe_existing(complete_path, scope["root"])
        complete = _read_json(complete_path)
        expected_outputs = _expected_output_paths(plan)
        if (complete and _verify_signed_record(complete, "complete_signature", b"case-finalization:complete:v1\0")
                and complete.get("plan_sha256") == plan_hash and complete.get("output_paths") == expected_outputs
                and _verify_outputs(scope, plan, expected_outputs)):
            return _completed_response(plan, complete)
        if complete_path.exists():
            raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "기존 완료 표식이 계획과 일치하지 않습니다. 파일을 보존하고 관리자에게 문의하세요.")
        if not plan["files"]:
            raise CaseFinalizationError("FINALIZATION_NO_FILES", "최종확정할 입력 또는 결과 파일이 없습니다.")
        output_paths: dict[str, str] = {}
        targets: dict[str, Path] = {}
        for category in ("CAE", "Reports"):
            relative, path = _target_directory(scope, plan, category)
            output_paths[category] = relative
            targets[category] = path
        try:
            for item in plan["files"]:
                _copy_one(scope, plan, item, targets[str(item["category"])])
            _cleanup_partial_files(scope, output_paths, operation_id)
            if not _verify_outputs(scope, plan, output_paths):
                raise CaseFinalizationError("FINALIZATION_OUTPUT_VERIFY_FAILED", "최종확정 파일 해시 검증에 실패했습니다. 같은 요청을 다시 시도하세요.")
            completed = {
                "schema_version": 1, "operation_id": operation_id, "status": "COMPLETE",
                "plan_sha256": plan_hash, "project_id": project_id, "request_id": request_id,
                "environment": str(environment).upper(), "case_id": case_id, "capture_id": capture_id,
                "capture_fingerprint": plan["capture_fingerprint"],
                "folder_schema_snapshot_id": plan["folder_schema_snapshot_id"],
                "output_paths": output_paths, "files": plan["files"],
                "counts": plan["counts"], "missing": plan["missing"],
                "created_by": actor, "confirmed_at": _now(),
            }
            completed = _signed_record(completed, "complete_signature", b"case-finalization:complete:v1\0")
            completed_bytes = _encode(completed)
            if len(completed_bytes) > MAX_METADATA_BYTES:
                raise CaseFinalizationError("FINALIZATION_METADATA_LIMIT", "최종확정 완료 기록이 메타데이터 크기 제한을 초과했습니다.")
            temporary = operation_dir / f".complete-{uuid4().hex}.tmp"
            with _pin_directory_chain(scope["root"], operation_dir):
                with temporary.open("xb") as handle:
                    handle.write(completed_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                if complete_path.exists():
                    raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "완료 표식이 동시에 생성되었습니다. 같은 요청을 다시 확인하세요.")
                if os.name == "nt":
                    os.rename(temporary, complete_path)
                else:
                    os.link(temporary, complete_path)
                    temporary.unlink()
            return _completed_response(plan, completed)
        except BaseException:
            # Version folders and copied files remain available for an exact retry.
            raise


def _completed_response(plan: dict[str, Any], completed: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": plan["operation_id"], "status": "COMPLETE",
        "case_id": plan["case_id"], "capture_id": plan["capture_id"],
        "case_label": plan["case_label"], "case_path": plan["case_path"],
        "capture_fingerprint": plan["capture_fingerprint"],
        "folder_schema_snapshot_id": plan["folder_schema_snapshot_id"],
        "output_paths": completed["output_paths"], "files": plan["files"],
        "counts": plan["counts"], "missing": plan["missing"],
        "excluded_capture_file_count": plan["excluded_capture_file_count"],
        "created_by": completed.get("created_by"), "confirmed_at": completed["confirmed_at"],
    }


def _status_operation(conn: ConnectionLike, scope: dict[str, Any], project_id: str,
                     request_id: str, environment: str, operation_dir: Path,
                     metadata_budget: list[int], verification_budget: list[int],
                     ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    """Read one signed operation without letting a malformed sibling hide valid history."""
    root: Path = scope["root"]
    spdm_storage._assert_safe_existing(operation_dir, root)
    operation_id = operation_dir.name
    plan_path = operation_dir / "plan.json"
    if not os.path.lexists(plan_path):
        return None, None, False
    spdm_storage._assert_safe_existing(plan_path, root)
    plan = _read_json(plan_path, status_metadata_budget=metadata_budget)
    if not plan or not _verify_signed_record(plan, "plan_signature", b"case-finalization:plan:v1\0"):
        return None, None, True
    final_relative, metadata_relative = _final_paths(scope)
    if ((plan.get("operation_id"), plan.get("project_id"), plan.get("request_id"), plan.get("environment")) !=
            (operation_id, project_id, request_id, str(environment).upper())
            or plan.get("final_relative_path") != final_relative
            or plan.get("metadata_relative_path") != f"{metadata_relative}/{operation_id}"):
        return None, None, True
    case_id = plan.get("case_id")
    if not isinstance(case_id, str) or not case_id or len(case_id) > 128:
        return None, None, True
    case_record = conn.execute(
        "SELECT project_id,request_id,environment,relative_path,source_name,storage_root_id FROM dashboard_cases WHERE id=?",
        [case_id],
    ).fetchone()
    if (not case_record or (str(case_record[0]), str(case_record[1]), str(case_record[2]), str(case_record[5])) !=
            (project_id, request_id, str(environment).upper(), scope["root_id"])
            or plan.get("case_path") != str(case_record[3]) or plan.get("case_label") != str(case_record[4])):
        return None, None, True
    capture_id = plan.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id or len(capture_id) > 128:
        return None, None, True
    capture = conn.execute(
        "SELECT c.fingerprint,c.case_id,dc.project_id,dc.request_id,dc.environment,dc.relative_path "
        "FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.id=?",
        [capture_id],
    ).fetchone()
    if (not capture or (str(capture[1]), str(capture[2]), str(capture[3]), str(capture[4]), str(capture[5])) !=
            (case_id, project_id, request_id, str(environment).upper(), str(case_record[3]))
            or str(capture[0]) != str(plan.get("capture_fingerprint"))):
        return None, None, True
    files = plan.get("files")
    if (not isinstance(files, list) or len(files) > MAX_FILES or any(
            not isinstance(file, dict) or file.get("category") not in {"CAE", "Reports"}
            or file.get("source_basis") not in {"SELECTED_CAPTURE", "CURRENT_CONFIRMED_SCENE"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(file.get("sha256") or ""))
            or type(file.get("size")) is not int or file["size"] < 0 or file["size"] > MAX_FILE_BYTES
            for file in files)):
        return None, None, True
    output_bytes = sum(file["size"] for file in files)
    if output_bytes > MAX_TOTAL_BYTES:
        return None, None, True
    try:
        expected_outputs = _expected_output_paths(plan)
    except (CaseFinalizationError, TypeError, ValueError):
        return None, None, True
    complete_path = operation_dir / "complete.json"
    if not os.path.lexists(complete_path):
        return plan, None, False
    spdm_storage._assert_safe_existing(complete_path, root)
    completed = _read_json(complete_path, status_metadata_budget=metadata_budget)
    if (not completed or not _verify_signed_record(completed, "complete_signature", b"case-finalization:complete:v1\0")
            or completed.get("status") != "COMPLETE" or completed.get("operation_id") != operation_id
            or completed.get("project_id") != project_id or completed.get("request_id") != request_id
            or completed.get("environment") != str(environment).upper() or completed.get("case_id") != case_id
            or completed.get("capture_id") != capture_id or completed.get("files") != files
            or completed.get("output_paths") != expected_outputs
            or completed.get("plan_sha256") != _plan_hash(plan)):
        return None, None, True
    if verification_budget[0] + output_bytes > MAX_STATUS_VERIFY_BYTES:
        raise CaseFinalizationError(
            "FINALIZATION_STATUS_LIMIT",
            f"최종확정 출력 검증 누계가 {MAX_STATUS_VERIFY_BYTES // (1024 * 1024)} MiB 제한을 초과했습니다. 이력을 안전하게 판정할 수 없습니다.",
        )
    verification_budget[0] += output_bytes
    if not _verify_outputs(scope, plan, expected_outputs):
        return None, None, True
    return plan, _completed_response(plan, completed), False


def status(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
           case_id: str) -> dict[str, Any]:
    scope = _scope_for_status(conn, project_id, request_id, environment, case_id)
    final_relative, metadata_relative = _final_paths(scope)
    metadata_dir = result_registration_paths._safe_existing(scope["root"], metadata_relative, allow_missing_leaf=True)
    latest = None
    selected_case_latest = None
    incomplete: list[dict[str, Any]] = []
    unverified_count = 0
    metadata_budget = [0]
    verification_budget = [0]
    if metadata_dir.is_dir():
        inspected_items = 0
        for item in metadata_dir.iterdir():
            inspected_items += 1
            if inspected_items > MAX_STATUS_ITEMS:
                raise CaseFinalizationError(
                    "FINALIZATION_STATUS_LIMIT",
                    f"최종확정 상태 조회 항목이 {MAX_STATUS_ITEMS}개 제한을 초과했습니다. 이력을 안전하게 판정할 수 없습니다.",
                )
            if not item.is_dir() or not _OPERATION_ID.fullmatch(item.name):
                continue
            try:
                plan, completed_record, unverified = _status_operation(
                    conn, scope, project_id, request_id, environment, item,
                    metadata_budget, verification_budget,
                )
            except CaseFinalizationError as exc:
                if exc.code == "FINALIZATION_STATUS_LIMIT":
                    raise
                plan, completed_record, unverified = None, None, True
            except (result_registration_paths.ResultRegistrationError,
                    spdm_storage.SpdmStorageError, OSError, TypeError, ValueError, KeyError):
                plan, completed_record, unverified = None, None, True
            if unverified:
                unverified_count += 1
            if completed_record:
                if latest is None or str(completed_record.get("confirmed_at", "")) > str(latest.get("confirmed_at", "")):
                    latest = completed_record
                if plan and plan.get("case_id") == case_id and (selected_case_latest is None or str(completed_record.get("confirmed_at", "")) > str(selected_case_latest.get("confirmed_at", ""))):
                    selected_case_latest = completed_record
            elif plan and plan.get("case_id") == case_id:
                incomplete.append({"operation_id": plan["operation_id"], "status": "RETRYABLE",
                                   "capture_id": plan.get("capture_id"), "previewed_at": plan.get("previewed_at")})
    return {"case_id": case_id, "request_id": request_id, "environment": str(environment).upper(),
            "final_relative_path": final_relative, "latest": latest, "selected_case_latest": selected_case_latest,
            "retryable_operations": sorted(incomplete, key=lambda row: row["previewed_at"], reverse=True),
            "unverified_records": unverified_count}


def _scope_for_status(conn: ConnectionLike, project_id: str, request_id: str,
                      environment: str, case_id: str) -> dict[str, Any]:
    try:
        scope_info = result_registration_paths._scope(conn, project_id, request_id, environment)
        root, root_id, root_key = result_registration_paths._root(conn)
    except result_registration_paths.ResultRegistrationError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    row = conn.execute("SELECT project_id,request_id,environment,relative_path,source_name,storage_root_id FROM dashboard_cases WHERE id=?", [case_id]).fetchone()
    if not row:
        raise CaseFinalizationError("FINALIZATION_CASE_NOT_FOUND", "선택한 Case 결과를 찾을 수 없습니다.")
    if (str(row[0]), str(row[1]), str(row[2])) != (project_id, request_id, str(environment).upper()) or str(row[5]) != root_id:
        raise CaseFinalizationError("FINALIZATION_CASE_SCOPE_MISMATCH", "선택한 Case 결과가 프로젝트·의뢰·환경과 일치하지 않습니다.")
    return {"root": root, "root_id": root_id, "root_key": root_key,
            "scope": scope_info, "case_id": case_id, "case_path": _relative(str(row[3])),
            "case_label": str(row[4]), "capture_id": ""}
