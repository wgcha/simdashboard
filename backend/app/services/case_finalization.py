"""Durable, hash-pinned publication of one confirmed Case to Final.

Layout (DEPTH_V1 D11/D12): ``Final/CAE/<Case>/<operation_id>/<Working Case mirror>``
holds every input and result file of the basis Scenes; ``Final/Reports/<Case>/<operation_id>/``
holds only the app-generated PPTX/HTML reports uploaded for the operation.
Signed plan/report/complete records live in ``Final/.finalizations/<operation_id>/``.
"""
from __future__ import annotations

import codecs
import hashlib
import hmac
import io
import json
import os
import posixpath
import re
import stat
import struct
import threading
import time
import unicodedata
import xml.etree.ElementTree as ElementTree
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator
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
# Scene-level documents that users keep next to the deck; they are CAE files, not app reports.
SCENE_REPORT_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".xlsx"}
REPORT_EXTENSIONS = SCENE_REPORT_EXTENSIONS
DECK_EXTENSIONS = {".rad", ".inc"}
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
PLAN_VERSION = 2
PLAN_DOMAIN = b"case-finalization:plan:v1\0"
COMPLETE_DOMAIN = b"case-finalization:complete:v1\0"
REPORTS_DOMAIN = b"case-finalization:reports:v1\0"
# App-generated reports (stage 5 builders). Order is the display/record order.
REPORT_FORMATS = ("pptx", "html")
MAX_REPORT_BYTES = {"pptx": 64 * 1024 * 1024, "html": 320 * 1024 * 1024}
MAX_PPTX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_PPTX_ENTRIES = 10_000
MAX_PPTX_RATIO = 200
MAX_CONTENT_TYPES_BYTES = 1024 * 1024
# Central directory read by zipfile before any entry check (memory/CPU bound).
MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_RELS_TOTAL_BYTES = 16 * 1024 * 1024
# pptxgenjs charts embed one small .xlsx workbook each (ppt/embeddings/*.xlsx).
MAX_EMBEDDED_WORKBOOK_BYTES = 16 * 1024 * 1024
MAX_EMBEDDED_WORKBOOK_ENTRIES = 1000
# Concurrent report uploads/validations per server process (non-blocking: 429 when full).
REPORT_UPLOAD_CONCURRENCY = 2
_REPORT_SLOTS = threading.BoundedSemaphore(REPORT_UPLOAD_CONCURRENCY)
_CHUNK = 1024 * 1024
_EXCLUDED_DIRS = {"cad", "final", "validation", "library"}


class CaseFinalizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _write_failed() -> CaseFinalizationError:
    """Storage write/permission failure: the operation stays retryable and nothing is overwritten."""
    return CaseFinalizationError(
        "FINALIZATION_WRITE_FAILED",
        "Final 폴더에 파일을 쓰지 못했습니다(권한 또는 저장소 상태). 기존 파일은 보존되었습니다. 잠시 후 다시 시도하세요.",
    )


@contextmanager
def report_upload_slot() -> Iterator[None]:
    """Per-process limit on concurrent report uploads/validations; never waits."""
    if not _REPORT_SLOTS.acquire(blocking=False):
        raise CaseFinalizationError("FINALIZATION_REPORT_BUSY", "다른 보고서 업로드를 검사하고 있습니다. 잠시 후 다시 시도하세요.")
    try:
        yield
    finally:
        _REPORT_SLOTS.release()


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
    """Resolve the finalization basis against the current confirmed Folder Schema.

    ``capture_id`` is either the merged latest view ``latest:<dashboard_case_id>``
    (what the Case results screen shows: each Scene keeps its newest captured
    result) or one concrete stored capture (backward compatible basis).
    """
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
    latest_basis = capture_id.startswith(dashboard_capture.LATEST_PREFIX)
    if latest_basis:
        if capture_id != dashboard_capture.latest_capture_id(case_id):
            raise CaseFinalizationError("FINALIZATION_CAPTURE_SCOPE_MISMATCH", "최신 결과 기준이 선택한 Case와 일치하지 않습니다.")
        capture_rows = conn.execute(
            "SELECT c.id,c.fingerprint,c.manifest_json,c.payload_json,c.case_id,dc.project_id,dc.request_id,dc.environment,dc.relative_path "
            "FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.case_id=? ORDER BY c.created_at,c.id",
            [case_id],
        ).fetchall()
    else:
        capture_rows = conn.execute(
            "SELECT c.id,c.fingerprint,c.manifest_json,c.payload_json,c.case_id,dc.project_id,dc.request_id,dc.environment,dc.relative_path "
            "FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.id=?",
            [capture_id],
        ).fetchall()
    if not capture_rows:
        raise CaseFinalizationError("FINALIZATION_CAPTURE_NOT_FOUND", "선택한 결과 버전을 찾을 수 없습니다.")
    for row in capture_rows:
        if (str(row[4]), str(row[5]), str(row[6]), str(row[7]), str(row[8])) != (
                case_id, project_id, request_id, scope["environment"], case_path):
            raise CaseFinalizationError("FINALIZATION_CAPTURE_SCOPE_MISMATCH", "결과 버전이 선택한 Case와 일치하지 않습니다.")
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
    snapshot_id = locations.snapshot_id
    if not snapshot_id:
        raise CaseFinalizationError("FINALIZATION_SCHEMA_UNAVAILABLE", "현재 확정 Folder Schema snapshot을 찾을 수 없습니다.")
    scene_role = "SCENE" if scope["environment"] == "DISTRIBUTION" else "EVALUATION"
    scene_locations = [item for item in locations.locations
                       if item.get("role_kind") == scene_role
                       and item.get("status") in {"CONFIRMED", "LINKED"}
                       and _under(case_path, str(item.get("relative_path") or ""))]
    blocked = folder_schema_locations.blocked_paths_for_case(schema, case_path)
    current_scenes = {str(item.get("relative_path") or "").casefold(): item for item in scene_locations}

    captures: dict[str, dict[str, Any]] = {}
    for row in capture_rows:
        payload = _decode(row[3]) or {}
        context = payload.get("context") if isinstance(payload, dict) else None
        manifest = _decode(row[2]) or []
        if not isinstance(manifest, list):
            raise CaseFinalizationError("FINALIZATION_CAPTURE_MANIFEST_INVALID", "결과 버전 파일 목록을 읽을 수 없습니다.")
        context_locations = context.get("folder_schema_locations") if isinstance(context, dict) else None
        schema_missing = not isinstance(context_locations, list)
        if schema_missing:
            if not latest_basis:
                raise CaseFinalizationError("FINALIZATION_CAPTURE_SCHEMA_MISSING", "결과 버전에 확정 위치 목록이 없습니다. Case를 다시 수집하세요.")
            context_locations = []
        captures[str(row[0])] = {
            "fingerprint": str(row[1]), "manifest": manifest, "schema_missing": schema_missing,
            "compatible": _compatible_scene_paths(context_locations, current_scenes, scene_role),
        }
    current_paths: list[str] = []
    for item in scene_locations:
        try:
            current_paths.append(_relative(str(item.get("relative_path") or "")))
        except CaseFinalizationError:
            continue
    sources: dict[str, tuple[str, str]] = {}
    excluded: dict[str, dict[str, Any]] = {}
    if latest_basis:
        # Single source of truth with the Case results screen: the same merge
        # (dashboard_capture.merge_latest_payload, same capture order as
        # get_latest_capture) decides which capture owns each Scene. A Scene whose
        # newest capture cannot be matched to the current schema is excluded and
        # reported; it never silently falls back to an older capture.
        merged = dashboard_capture.merge_latest_payload([(str(row[0]), row[3]) for row in capture_rows])
        for scene_path, source_id in _latest_scene_owners(merged, captures, current_paths):
            info = captures[source_id]
            key = scene_path.casefold()
            if any(path.casefold() == key for path in info["compatible"]):
                sources[key] = (scene_path, source_id)
            else:
                excluded[key] = {"scene_path": scene_path, "source_capture_id": source_id,
                                 "reason": "CAPTURE_SCHEMA_MISSING" if info["schema_missing"] else "CAPTURE_SCHEMA_INCOMPATIBLE"}
    else:
        for scene_path in captures[capture_id]["compatible"]:
            sources[scene_path.casefold()] = (scene_path, capture_id)
    for path in current_paths:
        key = path.casefold()
        if key not in sources and key not in excluded:
            excluded[key] = {"scene_path": path, "source_capture_id": None, "reason": "NO_CAPTURE"}
    excluded_scenes = sorted(excluded.values(), key=lambda item: item["scene_path"].casefold())
    if not sources:
        raise CaseFinalizationError("FINALIZATION_CAPTURE_SCHEMA_INCOMPATIBLE", "결과의 Scene 위치가 현재 Folder Schema와 일치하지 않습니다. Case를 다시 수집하세요.")
    scene_sources = [
        {"scene_path": path, "source_capture_id": source_id,
         "source_capture_fingerprint": captures[source_id]["fingerprint"]}
        for path, source_id in sorted(sources.values(), key=lambda item: item[0].casefold())
    ]
    if latest_basis:
        # Same aggregate identity as dashboard_capture.get_latest_capture: any new capture changes it.
        capture_fingerprint = hashlib.sha256(json.dumps([str(row[0]) for row in capture_rows]).encode()).hexdigest()
    else:
        capture_fingerprint = captures[capture_id]["fingerprint"]
    return {
        "root": root, "root_id": root_id, "root_key": root_key, "scope": scope,
        "case_id": case_id, "case_path": case_path, "case_label": str(case_row[5]),
        "capture_id": capture_id, "capture_fingerprint": capture_fingerprint,
        "basis": "LATEST" if latest_basis else "CAPTURE",
        "captures": captures, "scene_sources": scene_sources, "snapshot_id": snapshot_id,
        "compatible_scene_paths": [item["scene_path"] for item in scene_sources],
        "scene_role": scene_role, "scene_locations": scene_locations, "blocked_paths": blocked,
        "schema": schema, "excluded_scenes": excluded_scenes,
    }


def _manifest_has(manifest: list[Any], scene_path: str) -> bool:
    return any(isinstance(item, dict) and _under(scene_path, str(item.get("relative_path") or "")) for item in manifest)


def _latest_scene_owners(merged: dict[str, Any], captures: dict[str, dict[str, Any]],
                         current_paths: list[str]) -> list[tuple[str, str]]:
    """(current Scene path, owning capture) pairs of the merged latest view the screen shows."""
    merged_ids = [str(item) for item in merged.get("merged_capture_ids") or []]
    if not merged.get("runs"):
        # Usage (no runs): the newest capture as is, exactly like merge_latest_payload.
        newest = merged_ids[-1] if merged_ids else ""
        if newest not in captures:
            return []
        return [(path, newest) for path in current_paths if _manifest_has(captures[newest]["manifest"], path)]
    owners: dict[str, tuple[str, str]] = {}
    for scene in merged.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        source = str(scene.get("source_capture_id") or "")
        if source not in captures:
            continue
        files = [str(item.get("source_path") or "") for item in scene.get("observations") or [] if isinstance(item, dict)]
        files += [str(item.get("relative_path") or "") for item in scene.get("media") or [] if isinstance(item, dict)]
        matched = [path for path in current_paths if any(name and _under(path, name) for name in files)]
        if not matched:
            # A Scene without parsed values or media: its folder name inside this capture's files.
            label = str(scene.get("label") or "").casefold()
            matched = [path for path in current_paths if PurePosixPath(path).name.casefold() == label
                       and _manifest_has(captures[source]["manifest"], path)]
        if len(matched) == 1:
            owners[matched[0].casefold()] = (matched[0], source)
    return list(owners.values())


def _compatible_scene_paths(context_locations: list[Any], current_scenes: dict[str, dict[str, Any]],
                            scene_role: str) -> list[str]:
    compatible: list[str] = []
    for captured_scene in context_locations:
        if not isinstance(captured_scene, dict) or captured_scene.get("role_kind") != scene_role or captured_scene.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        current_scene = current_scenes.get(str(captured_scene.get("relative_path") or "").casefold())
        if current_scene is None:
            continue
        if str(current_scene.get("target_id") or "").casefold() != str(captured_scene.get("target_id") or "").casefold():
            continue
        if _hierarchy_signature(current_scene.get("hierarchy")) != _hierarchy_signature(captured_scene.get("hierarchy")):
            continue
        compatible.append(_relative(str(current_scene["relative_path"])))
    return compatible


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
    """All input and result files of the basis Scenes, mirrored under Final/CAE (DEPTH_V1 D11/D12)."""
    root = scope["root"]
    scene_sources = scope["scene_sources"]
    if not scene_sources:
        raise CaseFinalizationError("FINALIZATION_SCENE_UNCONFIRMED", "결과에 연결된 확정 Scene이 없습니다.")
    blocked = scope["blocked_paths"]
    planned: dict[str, dict[str, Any]] = {}
    excluded_count = 0
    total = 0
    used_captures = list(dict.fromkeys(item["source_capture_id"] for item in scene_sources))
    for source_id in used_captures:
        info = scope["captures"][source_id]
        assigned = [item["scene_path"] for item in scene_sources if item["source_capture_id"] == source_id]
        entries = sorted(info["manifest"], key=lambda item: str(item.get("relative_path", "")).casefold()
                         if isinstance(item, dict) else "")
        for item in entries:
            if not isinstance(item, dict):
                raise CaseFinalizationError("FINALIZATION_CAPTURE_MANIFEST_INVALID", "결과 버전 파일 목록이 올바르지 않습니다.")
            source = _relative(str(item.get("relative_path") or ""))
            suffix = PurePosixPath(source).suffix.casefold()
            if suffix not in RESULT_EXTENSIONS | SCENE_REPORT_EXTENSIONS:
                continue
            if not _under(scope["case_path"], source):
                raise CaseFinalizationError("FINALIZATION_CAPTURE_SCOPE_MISMATCH", "결과 파일이 Case 바깥을 가리킵니다.")
            if _blocked(source, blocked) or not any(_under(path, source) for path in info["compatible"]):
                excluded_count += 1
                continue
            if not any(_under(path, source) for path in assigned):
                continue  # A newer capture supersedes this Scene in the latest result.
            data, current_hash = _read_source(root, source)
            expected_hash = str(item.get("sha256") or "").casefold()
            try:
                expected_size = int(item.get("size", -1))
            except (TypeError, ValueError):
                expected_size = -1
            if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or current_hash != expected_hash or len(data) != expected_size:
                raise CaseFinalizationError("FINALIZATION_SOURCE_STALE", f"결과 수집 후 원본이 바뀌었습니다: {source}")
            relative_case_path = PurePosixPath(source).relative_to(PurePosixPath(scope["case_path"])).as_posix()
            key = relative_case_path.casefold()
            if key in planned:
                raise CaseFinalizationError("FINALIZATION_PATH_COLLISION", "대소문자만 다른 중복 파일이 있습니다.")
            total += len(data)
            planned[key] = {"source_relative_path": source, "case_relative_path": relative_case_path,
                            "category": "CAE", "source_basis": "SOURCE_CAPTURE", "source_capture_id": source_id,
                            "size": len(data), "sha256": current_hash}
    extra_scene_files = _scan_scene_support_files(scope)
    deck_entries = [source for source in extra_scene_files if PurePosixPath(source).suffix.casefold() in DECK_EXTENSIONS]
    scene_reports = [source for source in extra_scene_files if PurePosixPath(source).suffix.casefold() in SCENE_REPORT_EXTENSIONS]
    for source in [*_include_closure(scope, deck_entries), *scene_reports]:
        data, current_hash = _read_source(root, source)
        relative_case_path = PurePosixPath(source).relative_to(PurePosixPath(scope["case_path"])).as_posix()
        key = relative_case_path.casefold()
        if key in planned:
            continue
        total += len(data)
        planned[key] = {"source_relative_path": source, "case_relative_path": relative_case_path,
                        "category": "CAE", "source_basis": "CURRENT_CONFIRMED_SCENE",
                        "size": len(data), "sha256": current_hash}
    if len(planned) > MAX_FILES:
        raise CaseFinalizationError("FINALIZATION_FILE_LIMIT", "최종확정 파일 수 제한을 초과했습니다.")
    if total > MAX_TOTAL_BYTES:
        raise CaseFinalizationError("FINALIZATION_TOTAL_SIZE_LIMIT", "최종확정 CAE 파일은 256 MiB 이하여야 합니다.")
    return sorted(planned.values(), key=lambda item: item["case_relative_path"].casefold()), excluded_count


def _counts(files: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, bool]]:
    counts = {"CAE": 0, "input_decks": 0, "rad_decks": 0, "inc_decks": 0, "results": 0, "scene_reports": 0}
    for item in files:
        counts["CAE"] += 1
        suffix = PurePosixPath(str(item["source_relative_path"])).suffix.casefold()
        if suffix in DECK_EXTENSIONS:
            counts["input_decks"] += 1
            counts["rad_decks" if suffix == ".rad" else "inc_decks"] += 1
        elif suffix in SCENE_REPORT_EXTENSIONS:
            counts["scene_reports"] += 1
        else:
            counts["results"] += 1
    missing = {"input_decks": counts["input_decks"] == 0, "rad_decks": counts["rad_decks"] == 0,
               "inc_decks": counts["inc_decks"] == 0}
    return counts, missing


def report_file_names(case_label: str) -> dict[str, str]:
    """Server-chosen, Windows-safe report names; client file names are never used."""
    base = unicodedata.normalize("NFC", case_label)
    base = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", "_", base)
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"_+", "_", base).strip("._ ")[:80].rstrip("._ ") or "Case"
    if base.split(".", 1)[0].upper() in spdm_storage._WINDOWS_RESERVED:
        base = "_" + base
    names = {fmt: f"{base}_report.{fmt}" for fmt in REPORT_FORMATS}
    if not all(spdm_storage._valid_windows_name(name) for name in names.values()):
        raise CaseFinalizationError("FINALIZATION_CASE_LABEL_INVALID", "Case 이름으로 보고서 파일 이름을 만들 수 없습니다.")
    return names


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
                except OSError as exc:
                    raise _write_failed() from exc
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
    if not spdm_storage._valid_windows_name(scope["case_label"]):
        raise CaseFinalizationError("FINALIZATION_CASE_LABEL_INVALID", "Case 이름을 안전한 Windows 폴더 이름으로 사용할 수 없습니다.")
    report_files = report_file_names(scope["case_label"])
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
        counts, missing = _counts(files)
        plan = {
            "schema_version": PLAN_VERSION, "operation_id": operation_id, "status": "PREVIEW",
            "project_id": project_id, "request_id": request_id,
            "environment": str(environment).upper(), "case_id": case_id,
            "case_path": scope["case_path"], "case_label": scope["case_label"],
            "basis": scope["basis"], "capture_id": capture_id, "capture_fingerprint": scope["capture_fingerprint"],
            "scene_sources": scope["scene_sources"],
            "folder_schema_snapshot_id": scope["snapshot_id"],
            "scene_paths": sorted(set(scope["compatible_scene_paths"]), key=str.casefold),
            # Current confirmed Scenes left out of the latest basis (no capture, or a capture
            # that does not match the current schema); informational, shown in the preview.
            "excluded_scenes": scope["excluded_scenes"],
            "metadata_relative_path": operation_relative, "final_relative_path": final_relative,
            "created_by": actor, "previewed_at": _now(), "excluded_capture_file_count": excluded_count,
            "counts": counts, "missing": missing, "files": files, "report_files": report_files,
        }
        plan = _signed_record(plan, "plan_signature", PLAN_DOMAIN)
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
        output_paths = _expected_output_paths(plan)
        return {**plan, "plan_sha256": _plan_hash(plan), "can_confirm": bool(files),
                "output_paths": output_paths,
                "report_paths": {fmt: f"{output_paths['Reports']}/{name}" for fmt, name in report_files.items()},
                "report_limits": dict(MAX_REPORT_BYTES)}


def _verify_plan_scope(plan: dict[str, Any], scope: dict[str, Any], operation_id: str,
                       expected_files: list[dict[str, Any]], expected_excluded: int) -> None:
    if not _verify_signed_record(plan, "plan_signature", PLAN_DOMAIN):
        raise CaseFinalizationError("FINALIZATION_PLAN_UNTRUSTED", "저장된 최종확정 계획의 서명을 확인할 수 없습니다. 새 미리보기를 만드세요.")
    if plan.get("schema_version") != PLAN_VERSION:
        raise CaseFinalizationError("FINALIZATION_PLAN_CHANGED", "이전 형식의 미리보기 계획입니다. 새 미리보기를 만드세요.")
    expected = (operation_id, scope["scope"]["project_id"], scope["scope"]["request_id"],
                scope["scope"]["environment"], scope["case_id"], scope["capture_id"], scope["case_path"])
    actual = (str(plan.get("operation_id")), str(plan.get("project_id")), str(plan.get("request_id")),
              str(plan.get("environment")), str(plan.get("case_id")), str(plan.get("capture_id")), str(plan.get("case_path")))
    if actual != expected:
        raise CaseFinalizationError("FINALIZATION_OPERATION_SCOPE_MISMATCH", "최종확정 계획이 현재 Case 문맥과 일치하지 않습니다.")
    if plan.get("folder_schema_snapshot_id") != scope["snapshot_id"]:
        raise CaseFinalizationError("FINALIZATION_SCHEMA_STALE", "Folder Schema가 미리보기 이후 변경되었습니다. 새 미리보기를 만드세요.")
    if (plan.get("capture_fingerprint") != scope["capture_fingerprint"] or plan.get("basis") != scope["basis"]
            or plan.get("scene_sources") != scope["scene_sources"]):
        raise CaseFinalizationError("FINALIZATION_CAPTURE_CHANGED", "미리보기 이후 새 결과가 반영되었습니다. 새 미리보기를 만드세요.")
    final_relative, metadata_relative = _final_paths(scope)
    expected_scenes = sorted(set(scope["compatible_scene_paths"]), key=str.casefold)
    counts, missing = _counts(expected_files)
    expected = {
        "case_label": scope["case_label"], "final_relative_path": final_relative,
        "metadata_relative_path": f"{metadata_relative}/{operation_id}",
        "scene_paths": expected_scenes, "files": expected_files, "counts": counts, "missing": missing,
        "excluded_capture_file_count": expected_excluded,
        "report_files": report_file_names(scope["case_label"]),
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
    except OSError as exc:
        raise _write_failed() from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# App-generated reports (PPTX/HTML) uploaded by the browser for one operation.
# ---------------------------------------------------------------------------

def _upload_size(upload: BinaryIO) -> int:
    upload.seek(0, os.SEEK_END)
    size = upload.tell()
    upload.seek(0)
    return size


_EOCD = struct.Struct("<4s4H2LH")
_ZIP64_LOCATOR = struct.Struct("<4sLQL")
_ZIP64_EOCD = struct.Struct("<4sQ2H2L4Q")
_PPTX_INVALID = "FINALIZATION_REPORT_PPTX_INVALID"
_PPTX_ACTIVE = "FINALIZATION_REPORT_PPTX_ACTIVE_CONTENT"
_PPTX_MACRO = "FINALIZATION_REPORT_PPTX_MACRO"
# Relationship types (last URI segment) for content the app's pptxgenjs builder never emits.
_MACRO_RELATIONSHIPS = {"vbaproject", "vbaprojectsignature", "vbaprojectsignatureagile", "vbaprojectsignaturev3", "wordvbadata"}
_ACTIVE_RELATIONSHIPS = {"oleobject", "control", "activexcontrol", "activexcontrolbinary", "attachedtemplate",
                         "afchunk", "frame", "subdocument", "externallinkpath", "externallink"}
_ACTIVE_CONTENT_TOKENS = ("activex", "oleobject", "control+xml")
_EMBEDDED_WORKBOOK = re.compile(r"^ppt/embeddings/[^/]+\.xlsx$")
_XML_DECLARATION = re.compile(r"^<\?xml[^>]*\?>")
_XML_ENCODING = re.compile(rb"^\s*<\?xml[^>]*?encoding\s*=\s*[\"']([A-Za-z0-9._-]+)[\"']")


def _check_zip_directory(upload: BinaryIO, size: int, *, max_entries: int, max_directory_bytes: int) -> None:
    """Bound the central directory before zipfile reads it into memory (EOCD and ZIP64 EOCD)."""
    too_many = CaseFinalizationError("FINALIZATION_REPORT_PPTX_TOO_MANY_ENTRIES", "PPTX 보고서의 항목 수 또는 목록 크기가 제한을 넘습니다.")
    tail_length = min(size, _EOCD.size + 0xFFFF)
    upload.seek(size - tail_length)
    tail = upload.read(tail_length)
    # Same search order as zipfile._EndRecData: no comment first, then the last match.
    if len(tail) >= _EOCD.size and tail[-_EOCD.size:-_EOCD.size + 4] == b"PK\x05\x06" and tail[-2:] == b"\0\0":
        position = len(tail) - _EOCD.size
    else:
        position = tail.rfind(b"PK\x05\x06")
    if position < 0 or position + _EOCD.size > len(tail):
        raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서의 zip 목록을 찾을 수 없습니다.")
    _sig, disk, directory_disk, disk_entries, total_entries, directory_size, _offset, _comment = _EOCD.unpack_from(tail, position)
    if disk or directory_disk:
        raise CaseFinalizationError(_PPTX_INVALID, "분할 zip 형식의 PPTX 보고서는 저장할 수 없습니다.")
    record_at = size - tail_length + position
    locator_at = record_at - _ZIP64_LOCATOR.size
    if locator_at >= 0:
        upload.seek(locator_at)
        locator = upload.read(_ZIP64_LOCATOR.size)
        if len(locator) == _ZIP64_LOCATOR.size and locator[:4] == b"PK\x06\x07":
            # zipfile reads the ZIP64 record immediately before the locator.
            if locator_at < _ZIP64_EOCD.size:
                raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서의 ZIP64 목록이 올바르지 않습니다.")
            upload.seek(locator_at - _ZIP64_EOCD.size)
            record = upload.read(_ZIP64_EOCD.size)
            if len(record) != _ZIP64_EOCD.size or record[:4] != b"PK\x06\x06":
                raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서의 ZIP64 목록이 올바르지 않습니다.")
            (_sig, _record_size, _made, _needed, disk, directory_disk, disk_entries, total_entries,
             directory_size, _offset) = _ZIP64_EOCD.unpack(record)
            if disk or directory_disk:
                raise CaseFinalizationError(_PPTX_INVALID, "분할 zip 형식의 PPTX 보고서는 저장할 수 없습니다.")
    if max(disk_entries, total_entries) > max_entries or directory_size > max_directory_bytes:
        raise too_many
    upload.seek(0)


def _xml_root(data: bytes, what: str) -> ElementTree.Element:
    """Parse one small package XML part: UTF-8/UTF-16 by BOM or declaration, no DTD."""
    invalid = CaseFinalizationError(_PPTX_INVALID, f"PPTX 보고서의 {what}을(를) 읽을 수 없습니다.")
    try:
        if data.startswith(codecs.BOM_UTF8):
            text = data[len(codecs.BOM_UTF8):].decode("utf-8")
        elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text = data.decode("utf-16")
        elif data.startswith(b"<\x00"):
            text = data.decode("utf-16-le")
        elif data.startswith(b"\x00<"):
            text = data.decode("utf-16-be")
        else:
            declared = _XML_ENCODING.match(data[:256])
            if declared and declared.group(1).decode("ascii").casefold().replace("_", "-") not in {"utf-8", "utf8"}:
                raise invalid
            text = data.decode("utf-8")
    except UnicodeError as exc:
        raise invalid from exc
    text = text.lstrip("﻿ \t\r\n")
    folded = text.casefold()
    if "<!doctype" in folded or "<!entity" in folded:
        # Package parts never need a DTD; refusing it rules out entity expansion.
        raise CaseFinalizationError(_PPTX_INVALID, f"PPTX 보고서의 {what}에 DTD가 있습니다.")
    try:
        return ElementTree.fromstring(_XML_DECLARATION.sub("", text, count=1))
    except (ElementTree.ParseError, ValueError, RecursionError) as exc:
        raise invalid from exc


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _check_content_types(root_element: ElementTree.Element) -> None:
    for element in root_element.iter():
        content_type = str(element.get("ContentType") or "").casefold()
        if "macroenabled" in content_type or "vbaproject" in content_type:
            raise CaseFinalizationError(_PPTX_MACRO, "매크로가 포함된 PPTX 보고서는 저장할 수 없습니다.")
        if any(token in content_type for token in _ACTIVE_CONTENT_TOKENS):
            raise CaseFinalizationError(_PPTX_ACTIVE, "ActiveX·OLE 등 실행 가능한 내용이 포함된 PPTX 보고서는 저장할 수 없습니다.")


def _check_relationships(rels_name: str, root_element: ElementTree.Element) -> None:
    folder = posixpath.dirname(posixpath.dirname(rels_name))  # "<dir>/_rels/<part>.rels" -> "<dir>"
    for element in root_element.iter():
        if _local_name(element.tag) != "Relationship":
            continue
        if str(element.get("TargetMode") or "").casefold() == "external":
            raise CaseFinalizationError(_PPTX_ACTIVE, "외부 파일·주소를 참조하는 PPTX 보고서는 저장할 수 없습니다.")
        kind = str(element.get("Type") or "").rstrip("/").rsplit("/", 1)[-1].casefold()
        if kind in _MACRO_RELATIONSHIPS:
            raise CaseFinalizationError(_PPTX_MACRO, "매크로가 포함된 PPTX 보고서는 저장할 수 없습니다.")
        if kind in _ACTIVE_RELATIONSHIPS:
            raise CaseFinalizationError(_PPTX_ACTIVE, "ActiveX·OLE 등 실행 가능한 내용이 포함된 PPTX 보고서는 저장할 수 없습니다.")
        if kind == "package":
            target = str(element.get("Target") or "")
            resolved = posixpath.normpath(target[1:] if target.startswith("/") else posixpath.join(folder, target))
            if not _EMBEDDED_WORKBOOK.fullmatch(resolved.casefold()):
                raise CaseFinalizationError(_PPTX_ACTIVE, "차트 데이터 외의 포함 파일이 있는 PPTX 보고서는 저장할 수 없습니다.")


def _check_embedded_workbook(data: bytes) -> None:
    """A chart workbook (pptxgenjs) must itself be a plain .xlsx without macros or embeddings."""
    active = CaseFinalizationError(_PPTX_ACTIVE, "PPTX 보고서의 차트 데이터에 실행 가능한 내용이 있습니다.")
    if not data.startswith(b"PK\x03\x04"):
        raise active
    stream = io.BytesIO(data)
    _check_zip_directory(stream, len(data), max_entries=MAX_EMBEDDED_WORKBOOK_ENTRIES,
                         max_directory_bytes=MAX_ZIP_CENTRAL_DIRECTORY_BYTES)
    with zipfile.ZipFile(stream) as workbook:
        infos = workbook.infolist()
        if len(infos) > MAX_EMBEDDED_WORKBOOK_ENTRIES:
            raise active
        total = 0
        for info in infos:
            key = info.filename.casefold()
            total += info.file_size
            if (info.flag_bits & 0x1 or total > MAX_EMBEDDED_WORKBOOK_BYTES * 4
                    or any(token in key for token in ("vbaproject", "vbadata", "activex", "embeddings/", "oleobject"))):
                raise active
        content_types = next((info for info in infos if info.filename.casefold() == "[content_types].xml"), None)
        if content_types is None or content_types.file_size > MAX_CONTENT_TYPES_BYTES:
            raise active
        with workbook.open(content_types) as handle:
            _check_content_types(_xml_root(handle.read(MAX_CONTENT_TYPES_BYTES + 1), "차트 데이터 형식 목록"))


def _validate_pptx(upload: BinaryIO) -> None:
    upload.seek(0)
    if upload.read(4) != b"PK\x03\x04":
        raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서가 올바른 zip 파일이 아닙니다.")
    size = _upload_size(upload)
    try:
        _check_zip_directory(upload, size, max_entries=MAX_PPTX_ENTRIES,
                             max_directory_bytes=MAX_ZIP_CENTRAL_DIRECTORY_BYTES)
        with zipfile.ZipFile(upload) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_PPTX_ENTRIES:
                raise CaseFinalizationError("FINALIZATION_REPORT_PPTX_TOO_MANY_ENTRIES", "PPTX 보고서의 항목 수가 너무 많습니다.")
            names: dict[str, zipfile.ZipInfo] = {}
            total = 0
            for info in infos:
                name = info.filename
                parts = name.split("/")
                if (not name or "\\" in name or "\x00" in name or ":" in name or name.startswith("/")
                        or any(part in {"..", "."} for part in parts)):
                    raise CaseFinalizationError("FINALIZATION_REPORT_PPTX_UNSAFE_ENTRY", "PPTX 보고서에 안전하지 않은 항목 경로가 있습니다.")
                key = name.casefold()
                if key in names:
                    raise CaseFinalizationError("FINALIZATION_REPORT_PPTX_UNSAFE_ENTRY", "PPTX 보고서에 중복 항목이 있습니다.")
                names[key] = info
                if info.flag_bits & 0x1:
                    raise CaseFinalizationError(_PPTX_INVALID, "암호화된 PPTX 보고서는 저장할 수 없습니다.")
                if "vbaproject" in key or "vbadata" in key:
                    raise CaseFinalizationError(_PPTX_MACRO, "매크로가 포함된 PPTX 보고서는 저장할 수 없습니다.")
                if key.startswith("ppt/activex/") or (
                        key.startswith("ppt/embeddings/") and key != "ppt/embeddings/"
                        and not _EMBEDDED_WORKBOOK.fullmatch(key)):
                    raise CaseFinalizationError(_PPTX_ACTIVE, "ActiveX·OLE 등 실행 가능한 내용이 포함된 PPTX 보고서는 저장할 수 없습니다.")
                total += info.file_size
                if total > MAX_PPTX_UNCOMPRESSED_BYTES or (
                        info.file_size > _CHUNK and info.file_size > MAX_PPTX_RATIO * max(info.compress_size, 1)):
                    raise CaseFinalizationError("FINALIZATION_REPORT_PPTX_TOO_LARGE_UNCOMPRESSED", "PPTX 보고서의 압축 해제 크기가 제한을 넘습니다.")
            content_types = names.get("[content_types].xml")
            if content_types is None or "ppt/presentation.xml" not in names:
                raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서 구조([Content_Types].xml, ppt/presentation.xml)를 찾을 수 없습니다.")
            if content_types.file_size > MAX_CONTENT_TYPES_BYTES:
                raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서의 형식 목록이 너무 큽니다.")
            with archive.open(content_types) as handle:
                data = handle.read(MAX_CONTENT_TYPES_BYTES + 1)
            if len(data) > MAX_CONTENT_TYPES_BYTES:
                raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서의 형식 목록이 너무 큽니다.")
            _check_content_types(_xml_root(data, "형식 목록([Content_Types].xml)"))
            rels_total = 0
            for key, info in names.items():
                if not key.endswith(".rels"):
                    continue
                rels_total += info.file_size
                if info.file_size > MAX_CONTENT_TYPES_BYTES or rels_total > MAX_RELS_TOTAL_BYTES:
                    raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서의 관계 목록이 너무 큽니다.")
                with archive.open(info) as handle:
                    data = handle.read(MAX_CONTENT_TYPES_BYTES + 1)
                _check_relationships(key, _xml_root(data, "관계 목록(.rels)"))
            for key, info in names.items():
                if _EMBEDDED_WORKBOOK.fullmatch(key):
                    if info.file_size > MAX_EMBEDDED_WORKBOOK_BYTES:
                        raise CaseFinalizationError(_PPTX_ACTIVE, "PPTX 보고서의 차트 데이터가 너무 큽니다.")
                    with archive.open(info) as handle:
                        _check_embedded_workbook(handle.read(MAX_EMBEDDED_WORKBOOK_BYTES + 1))
    except CaseFinalizationError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, struct.error, RuntimeError, NotImplementedError,
            OSError, EOFError, ValueError) as exc:
        raise CaseFinalizationError(_PPTX_INVALID, "PPTX 보고서를 읽을 수 없습니다.") from exc


def _validate_html(upload: BinaryIO) -> None:
    upload.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    prefix = ""
    checked = False
    try:
        while True:
            chunk = upload.read(_CHUNK)
            text = decoder.decode(chunk, final=not chunk)
            if "\x00" in text:
                raise CaseFinalizationError("FINALIZATION_REPORT_HTML_INVALID", "HTML 보고서에 NUL 문자가 있습니다.")
            if not checked:
                prefix += text
                if len(prefix) >= 4096 or not chunk:
                    head = prefix.lstrip("﻿").lstrip(" \t\r\n")
                    if not head[:14].casefold().startswith("<!doctype html"):
                        raise CaseFinalizationError("FINALIZATION_REPORT_HTML_INVALID", "HTML 보고서는 <!doctype html>로 시작해야 합니다.")
                    checked = True
                    prefix = ""
            if not chunk:
                break
    except UnicodeDecodeError as exc:
        raise CaseFinalizationError("FINALIZATION_REPORT_HTML_INVALID", "HTML 보고서는 UTF-8 텍스트여야 합니다.") from exc


def validate_report(report_format: str, upload: BinaryIO) -> dict[str, Any]:
    """Type, structure and size checks for one uploaded report; returns size and SHA-256."""
    if report_format not in REPORT_FORMATS:
        raise CaseFinalizationError("FINALIZATION_REPORT_FORMAT_INVALID", "보고서 형식은 PPTX 또는 HTML이어야 합니다.")
    size = _upload_size(upload)
    if size == 0:
        raise CaseFinalizationError("FINALIZATION_REPORT_EMPTY", "보고서 파일이 비어 있습니다.")
    if size > MAX_REPORT_BYTES[report_format]:
        raise CaseFinalizationError("FINALIZATION_REPORT_TOO_LARGE", f"{report_format.upper()} 보고서는 {MAX_REPORT_BYTES[report_format] // (1024 * 1024)} MiB 이하여야 합니다.")
    if report_format == "pptx":
        _validate_pptx(upload)
    else:
        _validate_html(upload)
    upload.seek(0)
    digest = hashlib.sha256()
    while chunk := upload.read(_CHUNK):
        digest.update(chunk)
    upload.seek(0)
    return {"size": size, "sha256": digest.hexdigest()}


def _hash_path(path: Path, root: Path, max_bytes: int) -> tuple[str, int]:
    try:
        spdm_storage._assert_safe_existing(path, root)
        if not path.is_file():
            raise CaseFinalizationError("FINALIZATION_OUTPUT_MISSING", "최종확정 파일을 찾을 수 없습니다.")
        digest = hashlib.sha256()
        size = 0
        with spdm_storage.open_stable_reader(path) as stream:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise CaseFinalizationError("FINALIZATION_OUTPUT_TOO_LARGE", "최종확정 파일이 크기 제한을 넘습니다.")
                digest.update(chunk)
        return digest.hexdigest(), size
    except CaseFinalizationError:
        raise
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "파일을 안정적으로 읽을 수 없습니다.") from exc


def _write_temp(parent: Path, root: Path, source: BinaryIO, operation_id: str, max_bytes: int) -> tuple[Path, str, int]:
    temporary = parent / f".codex-partial-{operation_id}-{uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as handle:
            while chunk := source.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise CaseFinalizationError("FINALIZATION_REPORT_TOO_LARGE", "보고서 파일이 크기 제한을 넘습니다.")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        spdm_storage._assert_safe_existing(temporary, root)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return temporary, digest.hexdigest(), size


def _write_signed_metadata(operation_dir: Path, root: Path, name: str, record: dict[str, Any]) -> None:
    payload = _encode(record)
    if len(payload) > MAX_METADATA_BYTES:
        raise CaseFinalizationError("FINALIZATION_METADATA_LIMIT", "최종확정 기록이 메타데이터 크기 제한을 초과했습니다.")
    temporary = operation_dir / f".{name}-{uuid4().hex}.tmp"
    with _pin_directory_chain(root, operation_dir):
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # reports.json is the mutable staging record of an unfinished operation.
            os.replace(temporary, operation_dir / name)
        except OSError as exc:
            raise _write_failed() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _read_staged_reports(operation_dir: Path, root: Path, plan: dict[str, Any]) -> dict[str, Any] | None:
    path = operation_dir / "reports.json"
    if not os.path.lexists(path):
        return None
    try:
        spdm_storage._assert_safe_existing(path, root)
    except spdm_storage.SpdmStorageError:
        return None
    record = _read_json(path)
    if (not record or not _verify_signed_record(record, "reports_signature", REPORTS_DOMAIN)
            or record.get("operation_id") != plan.get("operation_id")
            or record.get("plan_sha256") != _plan_hash(plan)
            or not isinstance(record.get("reports"), dict) or not isinstance(record.get("history"), dict)):
        return None
    return record


def _load_operation_plan(conn: ConnectionLike, base_scope: dict[str, Any], operation_dir: Path, *,
                         operation_id: str, project_id: str, request_id: str, environment: str,
                         case_id: str, capture_id: str) -> dict[str, Any]:
    root: Path = base_scope["root"]
    plan_path = operation_dir / "plan.json"
    if not os.path.lexists(plan_path):
        raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
    spdm_storage._assert_safe_existing(operation_dir, root)
    spdm_storage._assert_safe_existing(plan_path, root)
    plan = _read_json(plan_path)
    if not plan:
        raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
    if not _verify_signed_record(plan, "plan_signature", PLAN_DOMAIN):
        raise CaseFinalizationError("FINALIZATION_PLAN_UNTRUSTED", "저장된 최종확정 계획의 서명을 확인할 수 없습니다. 새 미리보기를 만드세요.")
    if plan.get("schema_version") != PLAN_VERSION:
        raise CaseFinalizationError("FINALIZATION_PLAN_CHANGED", "이전 형식의 미리보기 계획입니다. 새 미리보기를 만드세요.")
    if (plan.get("operation_id"), plan.get("project_id"), plan.get("request_id"), plan.get("environment"),
            plan.get("case_id"), plan.get("capture_id"), plan.get("case_path"), plan.get("case_label")) != (
            operation_id, project_id, request_id, str(environment).upper(), case_id, capture_id,
            base_scope["case_path"], base_scope["case_label"]):
        raise CaseFinalizationError("FINALIZATION_OPERATION_SCOPE_MISMATCH", "최종확정 계획이 현재 Case 문맥과 일치하지 않습니다.")
    if plan.get("report_files") != report_file_names(base_scope["case_label"]):
        raise CaseFinalizationError("FINALIZATION_PLAN_CHANGED", "저장된 보고서 이름이 계획과 다릅니다. 새 미리보기를 만드세요.")
    if os.path.lexists(operation_dir / "complete.json"):
        raise CaseFinalizationError("FINALIZATION_ALREADY_COMPLETED", "이미 완료된 Final 지정입니다. 완료된 파일은 바꿀 수 없습니다.")
    return plan


def _report_operation(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
                      case_id: str, capture_id: str, operation_id: str) -> dict[str, Any]:
    """Resolve an unfinished operation: signed plan for this exact scope and no completion marker."""
    if not _OPERATION_ID.fullmatch(operation_id):
        raise CaseFinalizationError("FINALIZATION_OPERATION_ID_INVALID", "최종확정 요청 ID가 올바르지 않습니다.")
    base_scope = _scope_for_status(conn, project_id, request_id, environment, case_id)
    root: Path = base_scope["root"]
    _final_relative, metadata_relative = _final_paths(base_scope)
    operation_relative = f"{metadata_relative}/{operation_id}"
    try:
        metadata_dir = result_registration_paths._safe_existing(root, metadata_relative, allow_missing_leaf=True)
        operation_dir = result_registration_paths._safe_existing(root, operation_relative, allow_missing_leaf=True)
        if not metadata_dir.is_dir() or not operation_dir.is_dir():
            raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
        identity = {"operation_id": operation_id, "project_id": project_id, "request_id": request_id,
                    "environment": environment, "case_id": case_id, "capture_id": capture_id}
        plan = _load_operation_plan(conn, base_scope, operation_dir, **identity)
    except result_registration_paths.ResultRegistrationError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    return {"base_scope": base_scope, "root": root, "metadata_dir": metadata_dir, "identity": identity,
            "operation_relative": operation_relative, "operation_dir": operation_dir, "plan": plan}


def check_report_target(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
                        case_id: str, capture_id: str, operation_id: str) -> None:
    """Cheap pre-check before an upload body is read (signed plan, scope, not completed)."""
    _report_operation(conn, project_id=project_id, request_id=request_id, environment=environment,
                      case_id=case_id, capture_id=capture_id, operation_id=operation_id)


def stage_report(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
                 case_id: str, capture_id: str, operation_id: str, report_format: str,
                 upload: BinaryIO, actor: str) -> dict[str, Any]:
    """Validate and stage one report for an unfinished operation (replaces an earlier upload of that format)."""
    if not _OPERATION_ID.fullmatch(operation_id):
        raise CaseFinalizationError("FINALIZATION_OPERATION_ID_INVALID", "최종확정 요청 ID가 올바르지 않습니다.")
    target = _report_operation(conn, project_id=project_id, request_id=request_id, environment=environment,
                               case_id=case_id, capture_id=capture_id, operation_id=operation_id)
    checked = validate_report(report_format, upload)
    base_scope, root, metadata_dir = target["base_scope"], target["root"], target["metadata_dir"]
    operation_dir, identity, plan = target["operation_dir"], target["identity"], target["plan"]
    staging_dir = _ensure_dir(root, f"{target['operation_relative']}/reports")
    temporary: Path | None = None
    try:
        with _pin_directory_chain(root, staging_dir):
            upload.seek(0)
            temporary, digest, size = _write_temp(staging_dir, root, upload, operation_id, MAX_REPORT_BYTES[report_format])
        if (digest, size) != (checked["sha256"], checked["size"]):
            raise CaseFinalizationError("FINALIZATION_REPORT_UPLOAD_CHANGED", "보고서 업로드가 검사 중에 바뀌었습니다. 다시 올리세요.")
        with _request_lock(metadata_dir / ".request.lock", root):
            plan = _load_operation_plan(conn, base_scope, operation_dir, **identity)
            file_name = plan["report_files"][report_format]
            record = _read_staged_reports(operation_dir, root, plan) or {
                "schema_version": 1, "operation_id": operation_id, "plan_sha256": _plan_hash(plan),
                "reports": {}, "history": {},
            }
            record = {key: value for key, value in record.items() if key != "reports_signature"}
            with _pin_directory_chain(root, staging_dir):
                os.replace(temporary, staging_dir / file_name)
                temporary = None
            history = list(record["history"].get(report_format) or [])
            if digest not in history:
                history.append(digest)
            record["history"] = {**record["history"], report_format: history[-50:]}
            record["reports"] = {**record["reports"], report_format: {
                "file_name": file_name, "size": size, "sha256": digest,
                "staged_at": _now(), "staged_by": actor,
            }}
            _write_signed_metadata(operation_dir, root, "reports.json",
                                   _signed_record(record, "reports_signature", REPORTS_DOMAIN))
    except OSError as exc:
        raise _write_failed() from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    output_paths = _expected_output_paths(plan)
    return {"operation_id": operation_id, "format": report_format, "file_name": file_name,
            "size": size, "sha256": digest, "report_path": f"{output_paths['Reports']}/{file_name}",
            "status": "STAGED"}


def _publish_report(root: Path, parent: Path, report: dict[str, Any], staged_path: Path,
                    operation_id: str, history: list[str]) -> str:
    report_format = str(report["format"])
    cap = MAX_REPORT_BYTES[report_format]
    file_name = str(report["file_name"])
    with _pin_directory_chain(root, parent):
        collision = spdm_storage._case_collision(parent, file_name)
        destination = collision if collision is not None else parent / file_name
        replace = False
        if os.path.lexists(destination):
            existing_hash, _size = _hash_path(destination, root, cap)
            if existing_hash == report["sha256"]:
                return destination.relative_to(root).as_posix()
            if existing_hash not in history:
                raise CaseFinalizationError("FINALIZATION_DESTINATION_CONFLICT", f"기존 최종 보고서를 덮어쓰지 않았습니다: {file_name}")
            # Only an earlier upload of this same unfinished operation may be replaced.
            replace = True
        temporary: Path | None = None
        try:
            spdm_storage._assert_safe_existing(staged_path, root)
            with spdm_storage.open_stable_reader(staged_path) as source:
                temporary, digest, size = _write_temp(parent, root, source, operation_id, cap)
            if digest != report["sha256"] or size != report["size"]:
                raise CaseFinalizationError("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서가 기록과 다릅니다. 보고서를 다시 올리세요.")
            if replace:
                os.replace(temporary, destination)
            elif os.name == "nt":
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
                temporary.unlink()
            temporary = None
        except FileExistsError:
            existing_hash, _size = _hash_path(destination, root, cap)
            if existing_hash != report["sha256"]:
                raise CaseFinalizationError("FINALIZATION_DESTINATION_CONFLICT", f"기존 최종 보고서를 덮어쓰지 않았습니다: {file_name}")
        except spdm_storage.SpdmStorageError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
        except OSError as exc:
            raise _write_failed() from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        spdm_storage._assert_safe_existing(destination, root)
        return destination.relative_to(root).as_posix()


def _valid_report_records(plan: dict[str, Any], reports: Any) -> bool:
    if plan.get("schema_version") != PLAN_VERSION:
        return reports is None
    names = plan.get("report_files")
    if not isinstance(reports, list) or not reports or not isinstance(names, dict):
        return False
    seen: set[str] = set()
    for item in reports:
        if (not isinstance(item, dict) or item.get("format") not in REPORT_FORMATS or item["format"] in seen
                or item.get("file_name") != names.get(item["format"])
                or not isinstance(item.get("file_name"), str) or not spdm_storage._valid_windows_name(item["file_name"])
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
                or type(item.get("size")) is not int or not 0 < item["size"] <= MAX_REPORT_BYTES[item["format"]]):
            return False
        seen.add(item["format"])
    return True


def _plan_sources_valid(conn: ConnectionLike, plan: dict[str, Any], *, project_id: str, request_id: str,
                        environment: str, case_id: str, case_path: str) -> bool:
    """Every capture the plan was pinned to still exists unchanged for this Case."""
    capture_id = plan.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id or len(capture_id) > 160:
        return False
    if plan.get("schema_version") == 1:
        pinned = [(capture_id, plan.get("capture_fingerprint"))]
    elif plan.get("schema_version") == PLAN_VERSION:
        sources = plan.get("scene_sources")
        if not isinstance(sources, list) or not sources or len(sources) > MAX_FILES:
            return False
        if plan.get("basis") == "LATEST":
            if capture_id != dashboard_capture.latest_capture_id(case_id):
                return False
        elif plan.get("basis") != "CAPTURE":
            return False
        pinned_map: dict[str, Any] = {}
        for item in sources:
            if not isinstance(item, dict) or not isinstance(item.get("source_capture_id"), str):
                return False
            if plan.get("basis") == "CAPTURE" and item["source_capture_id"] != capture_id:
                return False
            if pinned_map.setdefault(item["source_capture_id"], item.get("source_capture_fingerprint")) != item.get("source_capture_fingerprint"):
                return False
        pinned = list(pinned_map.items())
    else:
        return False
    for source_id, fingerprint in pinned:
        if len(source_id) > 128:
            return False
        capture = conn.execute(
            "SELECT c.fingerprint,c.case_id,dc.project_id,dc.request_id,dc.environment,dc.relative_path "
            "FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.id=?",
            [source_id],
        ).fetchone()
        if (not capture or (str(capture[1]), str(capture[2]), str(capture[3]), str(capture[4]), str(capture[5])) !=
                (case_id, project_id, request_id, str(environment).upper(), case_path)
                or str(capture[0]) != str(fingerprint)):
            return False
    return True


def _shallow_matches(root: Path, relative: str, size: int) -> bool:
    """Existence and recorded size only (status history that is not displayed)."""
    path = result_registration_paths._safe_existing(root, relative)
    spdm_storage._assert_safe_existing(path, root)
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and info.st_size == size


def _verify_outputs(scope: dict[str, Any], plan: dict[str, Any], output_paths: dict[str, str],
                    reports: list[dict[str, Any]] | None = None, *, deep: bool = True) -> bool:
    """``deep`` re-hashes every output; otherwise existence and recorded size only."""
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
        if not deep:
            if not _shallow_matches(root, relative, item["size"]):
                return False
            continue
        path = result_registration_paths._safe_existing(root, relative)
        if not path.is_file() or _read_source(root, relative)[1] != item["sha256"]:
            return False
        spdm_storage._assert_safe_existing(path, root)
        if not path.relative_to(base).as_posix():
            return False
    for report in reports or []:
        relative = f"{output_paths['Reports']}/{report['file_name']}"
        if not deep:
            if not _shallow_matches(root, relative, report["size"]):
                return False
            continue
        path = result_registration_paths._safe_existing(root, relative)
        if _hash_path(path, root, MAX_REPORT_BYTES[report["format"]]) != (report["sha256"], report["size"]):
            return False
    for relative in output_paths.values():
        path = result_registration_paths._safe_existing(root, relative)
        if not path.is_dir():
            return False
    return True


def _reports_directory_entries(root: Path, relative: str, operation_id: str) -> list[tuple[str, bool]]:
    """Entries of this operation's Final/Reports folder, without its own partial temp files."""
    try:
        path = result_registration_paths._safe_existing(root, relative, allow_missing_leaf=True)
        if not os.path.lexists(path):
            return []
        spdm_storage._assert_safe_existing(path, root)
        if not path.is_dir():
            raise CaseFinalizationError("FINALIZATION_REPORTS_UNEXPECTED_FILE", "Final/Reports 보고서 폴더 자리에 다른 항목이 있습니다. 기존 자료를 보존하고 관리자에게 문의하세요.")
        entries: list[tuple[str, bool]] = []
        with os.scandir(path) as scanned:
            for entry in scanned:
                is_file = entry.is_file(follow_symlinks=False)
                if is_file and entry.name.startswith(f".codex-partial-{operation_id}-"):
                    continue
                entries.append((entry.name, is_file))
                if len(entries) > 2 * len(REPORT_FORMATS):
                    break
        return entries
    except CaseFinalizationError:
        raise
    except result_registration_paths.ResultRegistrationError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "Final/Reports 보고서 폴더를 읽을 수 없습니다.") from exc


def _check_reports_before_publish(root: Path, plan: dict[str, Any], operation_id: str, formats: list[str],
                                  history: dict[str, Any]) -> None:
    """A retry must keep every format an earlier attempt of this operation already published."""
    relative = _expected_output_paths(plan)["Reports"]
    known = {str(name).casefold(): fmt for fmt, name in plan["report_files"].items()}
    for name, is_file in _reports_directory_entries(root, relative, operation_id):
        fmt = known.get(name.casefold())
        if not is_file or fmt is None:
            raise CaseFinalizationError("FINALIZATION_REPORTS_UNEXPECTED_FILE", f"Final/Reports 보고서 폴더에 이 Final 지정이 만들지 않은 항목이 있습니다: {name}. 기존 자료를 보존하고 관리자에게 문의하세요.")
        if fmt in formats:
            continue  # Replaced or kept by _publish_report under its own history rule.
        digest, _size = _hash_path(root / relative / name, root, MAX_REPORT_BYTES[fmt])
        if digest in (history.get(fmt) or []):
            raise CaseFinalizationError("FINALIZATION_REPORT_FORMATS_MISMATCH", f"이전 시도에서 {fmt.upper()} 보고서가 이미 저장되었습니다. {fmt.upper()}를 포함해 다시 시도하세요.")
        raise CaseFinalizationError("FINALIZATION_REPORTS_UNEXPECTED_FILE", f"Final/Reports 보고서 폴더에 이 Final 지정이 만들지 않은 파일이 있습니다: {name}. 기존 자료를 보존하고 관리자에게 문의하세요.")


def _assert_reports_exact(root: Path, plan: dict[str, Any], operation_id: str, reports: list[dict[str, Any]]) -> None:
    """Before complete.json: the Reports folder holds exactly the recorded reports (nothing is deleted)."""
    entries = _reports_directory_entries(root, _expected_output_paths(plan)["Reports"], operation_id)
    expected = sorted(str(item["file_name"]).casefold() for item in reports)
    if not all(is_file for _name, is_file in entries) or sorted(name.casefold() for name, _ in entries) != expected:
        raise CaseFinalizationError("FINALIZATION_REPORTS_UNEXPECTED_FILE", "Final/Reports 보고서 폴더의 파일이 기록할 보고서와 다릅니다. 기존 자료를 보존하고 관리자에게 문의하세요.")


def _remove_staged_reports(root: Path, operation_dir: Path, plan: dict[str, Any], history: dict[str, Any]) -> None:
    """After completion, drop this operation's own staged copies (hash in the signed history); best effort."""
    staging_dir = operation_dir / "reports"
    try:
        if not staging_dir.is_dir():
            return
        with _pin_directory_chain(root, staging_dir):
            for fmt, name in plan["report_files"].items():
                path = staging_dir / str(name)
                if not os.path.lexists(path):
                    continue
                try:
                    digest, _size = _hash_path(path, root, MAX_REPORT_BYTES[fmt])
                except CaseFinalizationError:
                    continue
                if digest in (history.get(fmt) or []):
                    path.unlink()
    except (OSError, CaseFinalizationError, spdm_storage.SpdmStorageError):
        pass


def _cleanup_partial_files(scope: dict[str, Any], output_paths: dict[str, str], operation_id: str) -> None:
    root: Path = scope["root"]
    for relative in output_paths.values():
        base = result_registration_paths._safe_existing(root, relative, allow_missing_leaf=True)
        if not base.is_dir():
            continue
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
                or not _verify_signed_record(plan, "plan_signature", PLAN_DOMAIN)
                or not _verify_signed_record(complete, "complete_signature", COMPLETE_DOMAIN)):
            return None
        if (plan.get("operation_id"), plan.get("project_id"), plan.get("request_id"), plan.get("environment"),
                plan.get("case_id"), plan.get("capture_id"), plan.get("case_path"), plan.get("case_label")) != (
                operation_id, project_id, request_id, str(environment).upper(), case_id,
                capture_id, base_scope["case_path"], base_scope["case_label"]):
            return None
        final_relative, expected_metadata = _final_paths(base_scope)
        if plan.get("final_relative_path") != final_relative or plan.get("metadata_relative_path") != f"{expected_metadata}/{operation_id}":
            return None
        if not _plan_sources_valid(conn, plan, project_id=project_id, request_id=request_id,
                                   environment=environment, case_id=case_id, case_path=base_scope["case_path"]):
            return None
        expected_outputs = _expected_output_paths(plan)
        reports = complete.get("reports")
        if (complete.get("status") != "COMPLETE" or complete.get("operation_id") != operation_id
                or complete.get("project_id") != project_id or complete.get("request_id") != request_id
                or complete.get("environment") != str(environment).upper() or complete.get("case_id") != case_id
                or complete.get("capture_id") != capture_id or complete.get("files") != plan.get("files")
                or complete.get("output_paths") != expected_outputs
                or complete.get("plan_sha256") != _plan_hash(plan)
                or not _valid_report_records(plan, reports)):
            return None
        if not _verify_outputs(base_scope, plan, expected_outputs, reports):
            return None
        return _completed_response(plan, complete)
    except (CaseFinalizationError, result_registration_paths.ResultRegistrationError,
            spdm_storage.SpdmStorageError, OSError, TypeError, ValueError, KeyError):
        return None


def confirm(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
            case_id: str, capture_id: str, operation_id: str, actor: str,
            report_formats: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Publish CAE files and the staged reports; completion requires at least one report."""
    if not _OPERATION_ID.fullmatch(operation_id):
        raise CaseFinalizationError("FINALIZATION_OPERATION_ID_INVALID", "최종확정 요청 ID가 올바르지 않습니다.")
    if any(fmt not in REPORT_FORMATS for fmt in report_formats):
        raise CaseFinalizationError("FINALIZATION_REPORT_FORMAT_INVALID", "보고서 형식은 PPTX 또는 HTML이어야 합니다.")
    formats = [fmt for fmt in REPORT_FORMATS if fmt in set(report_formats)]
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
            # Completed operations are immutable: a repeated request returns the signed record.
            return completed_record
        operation_relative = f"{metadata_relative}/{operation_id}"
        operation_dir = result_registration_paths._safe_existing(base_scope["root"], operation_relative, allow_missing_leaf=True)
        if not operation_dir.is_dir():
            raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
        complete_path = operation_dir / "complete.json"
        if os.path.lexists(complete_path):
            raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "기존 완료 표식과 파일이 일치하지 않습니다. 기존 자료를 보존하고 관리자에게 문의하세요.")
        if not formats:
            raise CaseFinalizationError("FINALIZATION_REPORT_REQUIRED", "PPTX 또는 HTML 보고서를 하나 이상 선택하세요.")
        scope = _scope(conn, project_id, request_id, environment, case_id, capture_id)
        root: Path = scope["root"]
        plan = _load_operation_plan(conn, base_scope, operation_dir, operation_id=operation_id,
                                    project_id=project_id, request_id=request_id, environment=environment,
                                    case_id=case_id, capture_id=capture_id)
        expected_files, expected_excluded = _build_files(scope)
        _verify_plan_scope(plan, scope, operation_id, expected_files, expected_excluded)
        plan_hash = _plan_hash(plan)
        staged = _read_staged_reports(operation_dir, root, plan)
        reports: list[dict[str, Any]] = []
        for fmt in formats:
            entry = (staged or {}).get("reports", {}).get(fmt)
            if not isinstance(entry, dict) or entry.get("file_name") != plan["report_files"][fmt]:
                raise CaseFinalizationError("FINALIZATION_REPORT_NOT_STAGED", f"{fmt.upper()} 보고서가 올라가지 않았습니다. 보고서를 다시 올리세요.")
            report = {"format": fmt, "file_name": entry["file_name"], "size": entry.get("size"), "sha256": entry.get("sha256")}
            staged_path = operation_dir / "reports" / entry["file_name"]
            try:
                actual = _hash_path(staged_path, root, MAX_REPORT_BYTES[fmt])
            except CaseFinalizationError as exc:
                raise CaseFinalizationError("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서를 읽을 수 없습니다. 보고서를 다시 올리세요.") from exc
            if actual != (report["sha256"], report["size"]):
                raise CaseFinalizationError("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서가 기록과 다릅니다. 보고서를 다시 올리세요.")
            reports.append(report)
        if not _valid_report_records(plan, reports):
            raise CaseFinalizationError("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서 기록이 올바르지 않습니다. 보고서를 다시 올리세요.")
        if not plan["files"]:
            raise CaseFinalizationError("FINALIZATION_NO_FILES", "최종확정할 입력 또는 결과 파일이 없습니다.")
        history = dict((staged or {}).get("history") or {})
        # Before anything is copied: an earlier attempt of this operation may already have
        # published a report; completing without it would leave an unrecorded file behind.
        _check_reports_before_publish(root, plan, operation_id, formats, history)
        output_paths: dict[str, str] = {}
        targets: dict[str, Path] = {}
        relative, path = _target_directory(scope, plan, "CAE")
        output_paths["CAE"], targets["CAE"] = relative, path
        for item in plan["files"]:
            _copy_one(scope, plan, item, targets["CAE"])
        # Reports are published last so a CAE failure leaves nothing in Final/Reports.
        relative, path = _target_directory(scope, plan, "Reports")
        output_paths["Reports"], targets["Reports"] = relative, path
        for report in reports:
            _publish_report(root, targets["Reports"], report, operation_dir / "reports" / report["file_name"],
                            operation_id, list(history.get(report["format"]) or []))
        _cleanup_partial_files(scope, output_paths, operation_id)
        if output_paths != _expected_output_paths(plan) or not _verify_outputs(scope, plan, output_paths, reports):
            raise CaseFinalizationError("FINALIZATION_OUTPUT_VERIFY_FAILED", "최종확정 파일 해시 검증에 실패했습니다. 같은 요청을 다시 시도하세요.")
        _assert_reports_exact(root, plan, operation_id, reports)
        completed = {
            "schema_version": PLAN_VERSION, "operation_id": operation_id, "status": "COMPLETE",
            "plan_sha256": plan_hash, "project_id": project_id, "request_id": request_id,
            "environment": str(environment).upper(), "case_id": case_id, "capture_id": capture_id,
            "capture_fingerprint": plan["capture_fingerprint"],
            "folder_schema_snapshot_id": plan["folder_schema_snapshot_id"],
            "output_paths": output_paths, "files": plan["files"], "reports": reports,
            "counts": plan["counts"], "missing": plan["missing"],
            "created_by": actor, "confirmed_at": _now(),
        }
        completed = _signed_record(completed, "complete_signature", COMPLETE_DOMAIN)
        completed_bytes = _encode(completed)
        if len(completed_bytes) > MAX_METADATA_BYTES:
            raise CaseFinalizationError("FINALIZATION_METADATA_LIMIT", "최종확정 완료 기록이 메타데이터 크기 제한을 초과했습니다.")
        temporary = operation_dir / f".complete-{uuid4().hex}.tmp"
        with _pin_directory_chain(root, operation_dir):
            try:
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
            except FileExistsError as exc:
                raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "완료 표식이 동시에 생성되었습니다. 같은 요청을 다시 확인하세요.") from exc
            except OSError as exc:
                raise _write_failed() from exc
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        _remove_staged_reports(root, operation_dir, plan, history)
        return _completed_response(plan, completed)


def _completed_response(plan: dict[str, Any], completed: dict[str, Any]) -> dict[str, Any]:
    reports = completed.get("reports") or []
    output_paths = completed["output_paths"]
    return {
        "schema_version": plan.get("schema_version", 1),
        "operation_id": plan["operation_id"], "status": "COMPLETE",
        "case_id": plan["case_id"], "capture_id": plan["capture_id"],
        "basis": plan.get("basis", "CAPTURE"), "scene_sources": plan.get("scene_sources") or [],
        "case_label": plan["case_label"], "case_path": plan["case_path"],
        "capture_fingerprint": plan["capture_fingerprint"],
        "folder_schema_snapshot_id": plan["folder_schema_snapshot_id"],
        "output_paths": output_paths, "files": plan["files"],
        "reports": [{**item, "relative_path": f"{output_paths['Reports']}/{item['file_name']}"} for item in reports],
        "counts": plan["counts"], "missing": plan["missing"],
        "excluded_capture_file_count": plan["excluded_capture_file_count"],
        "created_by": completed.get("created_by"), "confirmed_at": completed["confirmed_at"],
    }


def _status_operation(conn: ConnectionLike, scope: dict[str, Any], project_id: str,
                     request_id: str, environment: str, operation_dir: Path,
                     metadata_budget: list[int],
                     ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, dict[str, Any] | None]:
    """Read one signed operation without letting a malformed sibling hide valid history.

    Outputs are checked for existence and recorded size here; the hash check of the
    records status actually shows is done by ``status`` (``deep`` argument tuple).
    """
    root: Path = scope["root"]
    spdm_storage._assert_safe_existing(operation_dir, root)
    operation_id = operation_dir.name
    plan_path = operation_dir / "plan.json"
    if not os.path.lexists(plan_path):
        return None, None, False, None
    spdm_storage._assert_safe_existing(plan_path, root)
    plan = _read_json(plan_path, status_metadata_budget=metadata_budget)
    if not plan or not _verify_signed_record(plan, "plan_signature", PLAN_DOMAIN):
        return None, None, True, None
    final_relative, metadata_relative = _final_paths(scope)
    if ((plan.get("operation_id"), plan.get("project_id"), plan.get("request_id"), plan.get("environment")) !=
            (operation_id, project_id, request_id, str(environment).upper())
            or plan.get("final_relative_path") != final_relative
            or plan.get("metadata_relative_path") != f"{metadata_relative}/{operation_id}"):
        return None, None, True, None
    case_id = plan.get("case_id")
    if not isinstance(case_id, str) or not case_id or len(case_id) > 128:
        return None, None, True, None
    case_record = conn.execute(
        "SELECT project_id,request_id,environment,relative_path,source_name,storage_root_id FROM dashboard_cases WHERE id=?",
        [case_id],
    ).fetchone()
    if (not case_record or (str(case_record[0]), str(case_record[1]), str(case_record[2]), str(case_record[5])) !=
            (project_id, request_id, str(environment).upper(), scope["root_id"])
            or plan.get("case_path") != str(case_record[3]) or plan.get("case_label") != str(case_record[4])):
        return None, None, True, None
    if not _plan_sources_valid(conn, plan, project_id=project_id, request_id=request_id,
                               environment=environment, case_id=case_id, case_path=str(case_record[3])):
        return None, None, True, None
    version = plan.get("schema_version")
    # Version 1 (before 2026-10-03) mirrored results/Scene reports into Final/Reports.
    categories = {"CAE", "Reports"} if version == 1 else {"CAE"}
    bases = ({"SELECTED_CAPTURE", "CURRENT_CONFIRMED_SCENE"} if version == 1
             else {"SOURCE_CAPTURE", "CURRENT_CONFIRMED_SCENE"})
    files = plan.get("files")
    if (not isinstance(files, list) or len(files) > MAX_FILES or any(
            not isinstance(file, dict) or file.get("category") not in categories
            or file.get("source_basis") not in bases
            or not re.fullmatch(r"[0-9a-f]{64}", str(file.get("sha256") or ""))
            or type(file.get("size")) is not int or file["size"] < 0 or file["size"] > MAX_FILE_BYTES
            for file in files)):
        return None, None, True, None
    output_bytes = sum(file["size"] for file in files)
    if output_bytes > MAX_TOTAL_BYTES:
        return None, None, True, None
    try:
        expected_outputs = _expected_output_paths(plan)
    except (CaseFinalizationError, TypeError, ValueError):
        return None, None, True, None
    complete_path = operation_dir / "complete.json"
    if not os.path.lexists(complete_path):
        return plan, None, False, None
    spdm_storage._assert_safe_existing(complete_path, root)
    completed = _read_json(complete_path, status_metadata_budget=metadata_budget)
    reports = completed.get("reports") if completed else None
    if (not completed or not _verify_signed_record(completed, "complete_signature", COMPLETE_DOMAIN)
            or completed.get("status") != "COMPLETE" or completed.get("operation_id") != operation_id
            or completed.get("project_id") != project_id or completed.get("request_id") != request_id
            or completed.get("environment") != str(environment).upper() or completed.get("case_id") != case_id
            or completed.get("capture_id") != plan.get("capture_id") or completed.get("files") != files
            or completed.get("output_paths") != expected_outputs
            or completed.get("plan_sha256") != _plan_hash(plan)
            or not _valid_report_records(plan, reports)):
        return None, None, True, None
    output_bytes += sum(item["size"] for item in reports or [])
    if not _verify_outputs(scope, plan, expected_outputs, reports, deep=False):
        return None, None, True, None
    deep = {"expected_outputs": expected_outputs, "reports": reports, "output_bytes": output_bytes}
    return plan, _completed_response(plan, completed), False, deep


def status(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
           case_id: str) -> dict[str, Any]:
    """Request-wide and selected-Case latest completed records.

    Every completed record is checked for signatures, scope and output existence/size.
    Only the records status returns (request latest, selected Case latest) are hash
    verified; a record that fails is counted as unverified and the next newer-to-older
    candidate is tried, so damaged outputs are never shown as normal while large report
    history cannot exhaust the per-call verification budget.
    """
    scope = _scope_for_status(conn, project_id, request_id, environment, case_id)
    final_relative, metadata_relative = _final_paths(scope)
    metadata_dir = result_registration_paths._safe_existing(scope["root"], metadata_relative, allow_missing_leaf=True)
    incomplete: list[dict[str, Any]] = []
    candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    unverified_count = 0
    metadata_budget = [0]
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
                plan, completed_record, unverified, deep = _status_operation(
                    conn, scope, project_id, request_id, environment, item, metadata_budget,
                )
            except CaseFinalizationError as exc:
                if exc.code == "FINALIZATION_STATUS_LIMIT":
                    raise
                plan, completed_record, unverified, deep = None, None, True, None
            except (result_registration_paths.ResultRegistrationError,
                    spdm_storage.SpdmStorageError, OSError, TypeError, ValueError, KeyError):
                plan, completed_record, unverified, deep = None, None, True, None
            if unverified:
                unverified_count += 1
            if completed_record and plan and deep:
                candidates.append((plan, completed_record, deep))
            elif plan and plan.get("case_id") == case_id and plan.get("schema_version") == PLAN_VERSION:
                # Unfinished version-1 previews cannot be confirmed any more; a new preview replaces them.
                incomplete.append({"operation_id": plan["operation_id"], "status": "RETRYABLE",
                                   "capture_id": plan.get("capture_id"), "previewed_at": plan.get("previewed_at")})
    candidates.sort(key=lambda row: str(row[1].get("confirmed_at", "")), reverse=True)
    verified: dict[str, bool] = {}
    verification_budget = [0]

    def deep_ok(plan: dict[str, Any], deep: dict[str, Any]) -> bool:
        nonlocal unverified_count
        operation_id = str(plan["operation_id"])
        if operation_id not in verified:
            if verification_budget[0] + deep["output_bytes"] > MAX_STATUS_VERIFY_BYTES:
                raise CaseFinalizationError(
                    "FINALIZATION_STATUS_LIMIT",
                    f"최종확정 출력 검증 누계가 {MAX_STATUS_VERIFY_BYTES // (1024 * 1024)} MiB 제한을 초과했습니다. 이력을 안전하게 판정할 수 없습니다.",
                )
            verification_budget[0] += deep["output_bytes"]
            try:
                verified[operation_id] = _verify_outputs(scope, plan, deep["expected_outputs"], deep["reports"])
            except (CaseFinalizationError, result_registration_paths.ResultRegistrationError,
                    spdm_storage.SpdmStorageError, OSError, TypeError, ValueError, KeyError):
                verified[operation_id] = False
            if not verified[operation_id]:
                unverified_count += 1
        return verified[operation_id]

    latest = next((record for plan, record, deep in candidates if deep_ok(plan, deep)), None)
    selected_case_latest = next((record for plan, record, deep in candidates
                                 if plan.get("case_id") == case_id and deep_ok(plan, deep)), None)
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
