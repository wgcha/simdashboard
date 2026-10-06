"""Durable, hash-pinned publication of one confirmed Case to Final.

Layout (DEPTH_V1 D11/D12): ``Final/CAE/<Case>/<operation_id>/<Working Case mirror>``
holds every file under the basis Scenes (no format filter, W2); ``Final/Report/<Case>/<operation_id>/``
holds only the app-generated PPTX/HTML reports uploaded for the operation (§15 D21: the folder is
``Report``; a legacy ``Final/Reports`` folder is never read or written). The output-path key
``Reports`` is an internal record key and is kept.
Signed plan/report/job/progress/complete records live in ``Final/.finalizations/<operation_id>/``.

W2 (plan version 3): confirm records a signed job and returns at once; an in-process worker
(:mod:`.case_finalization_jobs`) streams every file into ``.finalizations/<id>/staging/{CAE,Report}``
(constant memory, SHA-256 while copying), verifies the staged bytes, renames both staged folders
to their Final places and writes ``complete.json`` last. Progress survives restarts and the same
operation resumes, skipping files already staged with a matching hash.
"""
from __future__ import annotations

import codecs
import hashlib
import hmac
import io
import json
import logging
import os
import posixpath
import re
import struct
import threading
import time
import unicodedata
import xml.etree.ElementTree as ElementTree
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterator
from uuid import uuid4

from ..database_connection import ConnectionLike
from ..config import security_settings
from . import case_finalization_jobs, dashboard_capture, folder_schema_locations, folder_schema_resolver, materials_catalog, result_registration_paths, spdm_storage
from .storage import local as storage_local
from .storage.local import LocalFsProvider
from .storage.provider import FINAL, StorageError

_LOG = logging.getLogger(__name__)

# Limits of the records written before W2 (plan versions 1 and 2): kept only to judge those
# records exactly as before. Version 3 (W2) has no CAE file-count or size cap.
MAX_LEGACY_FILES = 500
MAX_LEGACY_TOTAL_BYTES = dashboard_capture.MAX_TOTAL_BYTES
MAX_LEGACY_FILE_BYTES = dashboard_capture.MAX_ASSET_BYTES
MAX_SCENE_SOURCES = 5000
# Safety bounds of the Scene tree walk (pathological trees), not a copy cap.
MAX_SCAN_ENTRIES = 500_000
MAX_SCAN_DEPTH = 32
MAX_INCLUDE_FILES = 5000
MAX_METADATA_BYTES = 4 * 1024 * 1024
# plan.json / complete.json / copied.jsonl carry one entry per CAE file.
MAX_PLAN_BYTES = 64 * 1024 * 1024
MAX_STATUS_ITEMS = 1000
MAX_STATUS_METADATA_BYTES = 256 * 1024 * 1024
MAX_STATUS_VERIFY_BYTES = 1024 * 1024 * 1024
DECK_EXTENSIONS = {".rad", ".inc"}
# Scene-level documents kept next to the deck (counted separately in the preview).
SCENE_REPORT_EXTENSIONS = {".pdf", ".ppt", ".pptx", ".xlsx"}
# Not copied to CAE: Office lock/temp files, temp files and hidden/system entries.
_SKIPPED_PREFIXES = (".", "$", "~")
_SKIPPED_SUFFIXES = (".tmp",)
_OPERATION_ID = re.compile(r"^[0-9a-f]{32}$")
PLAN_VERSION = 3
CONFIRMABLE_PLAN_VERSIONS = {PLAN_VERSION}
# §15 D21: output-path key -> Final child folder name. ``Reports`` stays the record key.
OUTPUT_FOLDERS = {"CAE": "CAE", "Reports": "Report"}
LEGACY_REPORTS_FOLDER = "Reports"
PLAN_DOMAIN = b"case-finalization:plan:v1\0"
COMPLETE_DOMAIN = b"case-finalization:complete:v1\0"
REPORTS_DOMAIN = b"case-finalization:reports:v1\0"
JOB_DOMAIN = b"case-finalization:job:v1\0"
PROGRESS_DOMAIN = b"case-finalization:progress:v1\0"
COPIED_DOMAIN = b"case-finalization:copied:v1\0"
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
COPY_CHUNK_BYTES = storage_local.COPY_CHUNK_BYTES
_EXCLUDED_DIRS = {"cad", "final", "validation", "library"}
# Free space required on the Final volume before copying: remaining bytes + max(5 %, 1 GiB).
DISK_MARGIN_RATIO = 0.05
DISK_MARGIN_MIN_BYTES = 1024 * 1024 * 1024
# Staged CAE files are read back and hashed before publication (one extra read; W9 measures it).
VERIFY_STAGED_CONTENT = True
PROGRESS_WRITE_INTERVAL = 1.0
# Folder rename retries (Windows sharing violations from antivirus/indexer/SMB clients).
RENAME_RETRY_DELAYS = (0.2, 0.5, 1.0, 2.0, 4.0, 8.0)
JOB_STATES = ("QUEUED", "RUNNING", "FAILED", "COMPLETE")


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
        root, root_id, root_key = result_registration_paths.storage_context(conn)
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
    # D8: Usage DEPTH_V1 Scenes use SCENE; legacy Usage registrations use EVALUATION.
    scene_roles = {"SCENE"} if scope["environment"] == "DISTRIBUTION" else {"SCENE", "EVALUATION"}
    scene_locations = [item for item in locations.locations
                       if item.get("role_kind") in scene_roles
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
            "compatible": _compatible_scene_paths(context_locations, current_scenes, scene_roles),
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
        "scene_roles": sorted(scene_roles), "scene_locations": scene_locations, "blocked_paths": blocked,
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
                            scene_roles: set[str]) -> list[str]:
    compatible: list[str] = []
    for captured_scene in context_locations:
        if not isinstance(captured_scene, dict) or captured_scene.get("role_kind") not in scene_roles or captured_scene.get("status") not in {"CONFIRMED", "LINKED"}:
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


def _storage_error(exc: spdm_storage.SpdmStorageError, relative: str) -> CaseFinalizationError:
    """Source read failures during preview/copy, with the W2 stale/missing codes."""
    if exc.code in {"SPDM_FILE_CHANGED"}:
        return CaseFinalizationError("FINALIZATION_SOURCE_STALE", f"미리보기 이후 원본이 바뀌었습니다: {relative}")
    if exc.code in {"SPDM_FILE_MISSING"}:
        return CaseFinalizationError("FINALIZATION_SOURCE_MISSING", f"원본 파일을 찾을 수 없습니다: {relative}")
    if exc.code in {"SPDM_FILE_BUSY"}:
        return CaseFinalizationError("FINALIZATION_SOURCE_BUSY", f"다른 프로그램이 원본 파일을 쓰고 있습니다. 잠시 후 다시 시도하세요: {relative}")
    return CaseFinalizationError(exc.code, str(exc))


def _hash_source(root: Path, relative: str, *, on_progress: Callable[[int], None] | None = None) -> tuple[int, str]:
    """(size, sha256) streamed through the stable reader (constant memory)."""
    fs = LocalFsProvider(root)
    try:
        path = result_registration_paths._safe_existing(root, relative)
        if not fs.is_file(path):
            raise CaseFinalizationError("FINALIZATION_SOURCE_MISSING", "수집된 원본 파일을 찾을 수 없습니다.")
        return fs.hash_stable(path, on_progress=on_progress)
    except CaseFinalizationError:
        raise
    except result_registration_paths.ResultRegistrationError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except spdm_storage.SpdmStorageError as exc:
        raise _storage_error(exc, relative) from exc
    except OSError as exc:
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "원본 파일을 안정적으로 읽을 수 없습니다.") from exc


def _skipped_name(name: str, attributes: int) -> bool:
    """Office lock/temp (``~$*``), ``*.tmp``, hidden names (``.``/``$``/``~``) and Windows hidden/system entries."""
    folded = name.casefold()
    return (name.startswith(_SKIPPED_PREFIXES) or folded.endswith(_SKIPPED_SUFFIXES)
            or bool(attributes & (storage_local.FILE_ATTRIBUTE_HIDDEN | storage_local.FILE_ATTRIBUTE_SYSTEM)))


def _scan_scene_files(scope: dict[str, Any]) -> list[dict[str, Any]]:
    """Every file below the basis Scenes (listing + lstat only; no content read)."""
    root: Path = scope["root"]
    fs = LocalFsProvider(root)
    found: dict[str, dict[str, Any]] = {}
    visited = 0
    blocked = scope["blocked_paths"]
    try:
        for scene_relative in [str(path) for path in scope["compatible_scene_paths"]]:
            scene = result_registration_paths._safe_existing(root, scene_relative)
            if not fs.is_dir(scene):
                continue
            stack: list[tuple[str, int]] = [(scene, 0)]
            while stack:
                directory, depth = stack.pop()
                if depth > MAX_SCAN_DEPTH:
                    raise CaseFinalizationError("FINALIZATION_DEPTH_LIMIT", "Scene 폴더 깊이 제한을 초과했습니다.")
                for entry, attributes in fs.list_detailed(directory):
                    visited += 1
                    if visited > MAX_SCAN_ENTRIES:
                        raise CaseFinalizationError("FINALIZATION_SCAN_LIMIT", "Scene 파일 조사 범위를 초과했습니다.")
                    if _skipped_name(entry.name, attributes) or entry.name.casefold() in _EXCLUDED_DIRS:
                        continue
                    item_relative = fs.join(directory, entry.name)
                    if entry.is_link:
                        raise CaseFinalizationError("SPDM_PATH_UNSAFE", f"Scene 폴더에 바로가기(reparse point)가 있습니다: {entry.name}")
                    if _blocked(item_relative, blocked):
                        continue
                    if entry.kind == "dir":
                        stack.append((item_relative, depth + 1))
                    elif entry.kind == "file":
                        relative = _relative(item_relative)
                        found[relative.casefold()] = {"relative": relative, "size": int(entry.size or 0),
                                                      "modified_ns": int(entry.modified_ns or 0)}
        return sorted(found.values(), key=lambda item: item["relative"].casefold())
    except CaseFinalizationError:
        raise
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "Scene 파일을 조사할 수 없습니다.") from exc


_UNPARSED_DECK_CODES = {"MATERIALS_FILE_SIZE_LIMIT", "MATERIALS_TOTAL_SIZE_LIMIT", "MATERIALS_PARSE_TIME_LIMIT"}


def _include_closure(scope: dict[str, Any], entry_files: list[str]) -> tuple[list[str], list[str]]:
    """(deck files reached through /INCLUDE, decks too large/slow to parse at preview).

    Every Scene file is copied anyway; the closure rejects includes outside the basis
    Scenes and adds included decks kept in folders the Scene walk skips (``Library``).
    A deck over the parse limits is still copied but its includes are not followed
    (listed as ``include_unchecked``).
    """
    request_path = str(scope["scope"]["request_relative_path"])
    allowed_scene_roots = [str(path) for path in scope["compatible_scene_paths"]]
    blocked = scope["blocked_paths"]
    budget = materials_catalog._ParseBudget()
    included: list[str] = []
    unchecked: list[str] = []
    visited: set[str] = set()

    def visit(relative: str, depth: int) -> None:
        key = relative.casefold()
        if key in visited:
            return
        if depth > 5 or len(visited) >= MAX_INCLUDE_FILES:
            raise CaseFinalizationError("FINALIZATION_INCLUDE_LIMIT", "덱 include 참조가 깊이 또는 파일 수 제한을 넘었습니다.")
        if not any(_under(scene_root, relative) for scene_root in allowed_scene_roots) or _blocked(relative, blocked):
            raise CaseFinalizationError("FINALIZATION_INCLUDE_OUT_OF_SCOPE", f"덱 include 파일이 확정 Scene 범위 밖에 있습니다: {PurePosixPath(relative).name}")
        try:
            fs = LocalFsProvider(scope["root"])
            checked = result_registration_paths._safe_existing(scope["root"], relative)
            fs.assert_safe(checked)
            if not fs.is_file(checked) or PurePosixPath(checked).suffix.casefold() not in DECK_EXTENSIONS:
                raise CaseFinalizationError("FINALIZATION_INCLUDE_UNSUPPORTED", "include 참조는 확정 Scene 안의 .rad 또는 .inc 파일이어야 합니다.")
            size = fs.stat(checked, follow_links=True, missing_ok=False).size
            included.append(relative)
            visited.add(key)
            try:
                budget.add_file(size)
                references = materials_catalog._scan_include_references(fs.path(checked), relative, size, budget)
            except materials_catalog.MaterialsCatalogError as exc:
                if exc.code not in _UNPARSED_DECK_CODES:
                    raise
                unchecked.append(relative)
                return
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
    return sorted(set(included), key=str.casefold), sorted(set(unchecked), key=str.casefold)


@contextmanager
def _pin_directory_chain(root: Path, directory: str) -> Iterator[None]:
    """Pin every existing directory ancestor (storage provider) without allowing Windows rename/reparse swaps."""
    with ExitStack() as stack:
        try:
            stack.enter_context(LocalFsProvider(root).pin(directory))
        except spdm_storage.SpdmStorageError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
        yield


def _source_stat(root: Path, relative: str) -> tuple[int, int]:
    """(size, mtime ns) of a regular, link-free source file."""
    fs = LocalFsProvider(root)
    try:
        path = result_registration_paths._safe_existing(root, relative)
        info = fs.stat(path, follow_links=False, missing_ok=True)
    except result_registration_paths.ResultRegistrationError as exc:
        if exc.code == "SPDM_FOLDER_MISSING":
            raise CaseFinalizationError("FINALIZATION_SOURCE_MISSING", f"수집된 원본 파일을 찾을 수 없습니다: {relative}") from exc
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "원본 파일을 확인할 수 없습니다.") from exc
    if info is None:
        raise CaseFinalizationError("FINALIZATION_SOURCE_MISSING", f"수집된 원본 파일을 찾을 수 없습니다: {relative}")
    if info.is_link or info.kind != "file":
        raise CaseFinalizationError("SPDM_PATH_UNSAFE", f"원본은 일반 파일이어야 합니다: {relative}")
    return int(info.size or 0), int(info.modified_ns or 0)


def _build_files(scope: dict[str, Any]) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Every file of the basis Scenes, mirrored under Final/CAE (DEPTH_V1 D11/D12; W2: no format filter).

    Preview cost is a listing plus ``lstat`` per file. Result files of the merged-latest
    capture are pinned to their captured SHA-256/size (size checked here, hash checked
    while copying); every other file is pinned to its size and modification time here and
    hashed while copying (the hash goes into ``complete.json``).
    Returns (files, excluded capture file count, decks whose includes were not followed).
    """
    root = scope["root"]
    scene_sources = scope["scene_sources"]
    if not scene_sources:
        raise CaseFinalizationError("FINALIZATION_SCENE_UNCONFIRMED", "결과에 연결된 확정 Scene이 없습니다.")
    blocked = scope["blocked_paths"]
    planned: dict[str, dict[str, Any]] = {}
    excluded_count = 0
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
            if not _under(scope["case_path"], source):
                raise CaseFinalizationError("FINALIZATION_CAPTURE_SCOPE_MISMATCH", "결과 파일이 Case 바깥을 가리킵니다.")
            if _blocked(source, blocked) or not any(_under(path, source) for path in info["compatible"]):
                excluded_count += 1
                continue
            if not any(_under(path, source) for path in assigned):
                continue  # A newer capture supersedes this Scene in the latest result.
            expected_hash = str(item.get("sha256") or "").casefold()
            try:
                expected_size = int(item.get("size", -1))
            except (TypeError, ValueError):
                expected_size = -1
            size, _modified = _source_stat(root, source)
            if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or size != expected_size:
                raise CaseFinalizationError("FINALIZATION_SOURCE_STALE", f"결과 수집 후 원본이 바뀌었습니다: {source}")
            relative_case_path = PurePosixPath(source).relative_to(PurePosixPath(scope["case_path"])).as_posix()
            key = relative_case_path.casefold()
            if key in planned:
                raise CaseFinalizationError("FINALIZATION_PATH_COLLISION", "대소문자만 다른 중복 파일이 있습니다.")
            planned[key] = {"source_relative_path": source, "case_relative_path": relative_case_path,
                            "category": "CAE", "source_basis": "SOURCE_CAPTURE", "source_capture_id": source_id,
                            "size": size, "sha256": expected_hash}
    scene_files = _scan_scene_files(scope)
    by_key = {item["relative"].casefold(): item for item in scene_files}
    deck_entries = [item["relative"] for item in scene_files if PurePosixPath(item["relative"]).suffix.casefold() in DECK_EXTENSIONS]
    included, unchecked = _include_closure(scope, deck_entries)
    for relative in included:
        if relative.casefold() not in by_key:
            size, modified = _source_stat(root, relative)
            by_key[relative.casefold()] = {"relative": relative, "size": size, "modified_ns": modified}
    for item in sorted(by_key.values(), key=lambda value: value["relative"].casefold()):
        source = item["relative"]
        if not _under(scope["case_path"], source):
            continue
        relative_case_path = PurePosixPath(source).relative_to(PurePosixPath(scope["case_path"])).as_posix()
        key = relative_case_path.casefold()
        if key in planned:
            continue
        planned[key] = {"source_relative_path": source, "case_relative_path": relative_case_path,
                        "category": "CAE", "source_basis": "CURRENT_CONFIRMED_SCENE",
                        "size": item["size"], "sha256": None, "modified_ns": item["modified_ns"]}
    return sorted(planned.values(), key=lambda item: item["case_relative_path"].casefold()), excluded_count, unchecked


def _counts(files: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, bool]]:
    counts = {"CAE": 0, "input_decks": 0, "rad_decks": 0, "inc_decks": 0, "results": 0, "scene_reports": 0,
              "other_files": 0, "total_bytes": 0}
    for item in files:
        counts["CAE"] += 1
        counts["total_bytes"] += int(item.get("size") or 0)
        suffix = PurePosixPath(str(item["source_relative_path"])).suffix.casefold()
        if suffix in DECK_EXTENSIONS:
            counts["input_decks"] += 1
            counts["rad_decks" if suffix == ".rad" else "inc_decks"] += 1
        elif item.get("source_basis") in {"SOURCE_CAPTURE", "SELECTED_CAPTURE"}:
            counts["results"] += 1
        elif suffix in SCENE_REPORT_EXTENSIONS:
            counts["scene_reports"] += 1
        else:
            counts["other_files"] += 1
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
def _request_lock(path: str, root: Path) -> Iterator[None]:
    """Process-wide, OS-released lock (storage provider); the persistent file is never deleted."""
    with ExitStack() as stack:
        try:
            stack.enter_context(LocalFsProvider(root).lock(path, zone=FINAL))
        except spdm_storage.SpdmStorageError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
        yield


def _ensure_dir(root: Path, relative: str) -> str:
    fs = LocalFsProvider(root)
    current = ""
    with ExitStack() as pins:
        pins.enter_context(_pin_directory_chain(root, current))
        for part in PurePosixPath(_relative(relative)).parts:
            if part != ".finalizations" and not spdm_storage._valid_windows_name(part):
                raise CaseFinalizationError("FINALIZATION_PATH_INVALID", "최종확정 경로에 사용할 수 없는 이름이 있습니다.")
            collision = fs.case_collision(current, part)
            candidate = collision if collision is not None else fs.join(current, part)
            if fs.exists(candidate):
                pins.enter_context(_pin_directory_chain(root, candidate))
                if not fs.is_dir(candidate):
                    raise CaseFinalizationError("FINALIZATION_PATH_CONFLICT", "최종확정 폴더 경로에 파일이 있습니다.")
            else:
                try:
                    fs.mkdirs(candidate, zone=FINAL, parents=False, exist_ok=False)
                except FileExistsError:
                    collision = fs.case_collision(current, part)
                    candidate = collision if collision is not None else candidate
                    if not fs.is_dir(candidate):
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


def _read_json(root: Path, path: str, *, status_metadata_budget: list[int] | None = None,
               max_bytes: int = MAX_METADATA_BYTES) -> dict[str, Any] | None:
    def charge(size: int) -> None:
        if status_metadata_budget is not None:
            if status_metadata_budget[0] + size > MAX_STATUS_METADATA_BYTES:
                raise CaseFinalizationError(
                    "FINALIZATION_STATUS_LIMIT",
                    f"최종확정 메타데이터 조회 누계가 {MAX_STATUS_METADATA_BYTES // (1024 * 1024)} MiB 제한을 초과했습니다. 이력을 안전하게 판정할 수 없습니다.",
                )
            status_metadata_budget[0] += size

    try:
        payload = LocalFsProvider(root).read_small_nofollow(path, max_bytes=max_bytes, before_read=charge)
        if payload is None:
            return None
        value = json.loads(payload.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except CaseFinalizationError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError, spdm_storage.SpdmStorageError):
        return None


def _disk_margin(required: int) -> int:
    return max(int(required * DISK_MARGIN_RATIO), DISK_MARGIN_MIN_BYTES)


def _disk_check(root: Path, final_relative: str, required: int) -> dict[str, Any]:
    """Free space on the Final volume against ``required`` + margin (``shutil.disk_usage``)."""
    margin = _disk_margin(required)
    try:
        free = LocalFsProvider(root).free_bytes(final_relative)
    except (OSError, spdm_storage.SpdmStorageError):
        free = None
    return {"required_bytes": required, "margin_bytes": margin, "free_bytes": free,
            "sufficient": free is None or free >= required + margin}


def _require_disk_space(root: Path, final_relative: str, required: int) -> None:
    if required <= 0:
        return
    check = _disk_check(root, final_relative, required)
    if check["free_bytes"] is None:
        raise CaseFinalizationError("FINALIZATION_DISK_UNAVAILABLE", "Final 폴더 드라이브의 남은 공간을 확인할 수 없습니다.")
    if not check["sufficient"]:
        gib = 1024 ** 3
        raise CaseFinalizationError(
            "FINALIZATION_DISK_SPACE",
            f"Final 폴더 드라이브의 남은 공간이 부족합니다(필요 {required / gib:.2f} GiB + 여유 {check['margin_bytes'] / gib:.2f} GiB, "
            f"남은 공간 {check['free_bytes'] / gib:.2f} GiB). 공간을 확보한 뒤 다시 시도하세요.")


def preview(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
            case_id: str, capture_id: str, actor: str) -> dict[str, Any]:
    scope = _scope(conn, project_id, request_id, environment, case_id, capture_id)
    final_relative, metadata_relative = _final_paths(scope)
    request_root = result_registration_paths._safe_existing(scope["root"], scope["scope"]["request_relative_path"])
    if not LocalFsProvider(scope["root"]).is_dir(request_root):
        raise CaseFinalizationError("FINALIZATION_REQUEST_FOLDER_INVALID", "확정된 의뢰 폴더를 찾을 수 없습니다.")
    if not spdm_storage._valid_windows_name(scope["case_label"]):
        raise CaseFinalizationError("FINALIZATION_CASE_LABEL_INVALID", "Case 이름을 안전한 Windows 폴더 이름으로 사용할 수 없습니다.")
    report_files = report_file_names(scope["case_label"])
    _ensure_dir(scope["root"], final_relative)
    metadata_dir = _ensure_dir(scope["root"], metadata_relative)
    lock_path = f"{metadata_dir}/.request.lock"
    with _request_lock(lock_path, scope["root"]):
        # Re-resolve the current schema after obtaining the request lock.
        scope = _scope(conn, project_id, request_id, environment, case_id, capture_id)
        files, excluded_count, include_unchecked = _build_files(scope)
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
            "include_unchecked": include_unchecked,
            "counts": counts, "missing": missing, "files": files, "report_files": report_files,
        }
        plan = _signed_record(plan, "plan_signature", PLAN_DOMAIN)
        plan_bytes = _encode(plan)
        if len(plan_bytes) > MAX_PLAN_BYTES:
            raise CaseFinalizationError("FINALIZATION_METADATA_LIMIT", "최종확정 계획이 메타데이터 크기 제한을 초과했습니다.")
        fs = LocalFsProvider(scope["root"])
        plan_path = fs.join(operation_dir, "plan.json")
        with _pin_directory_chain(scope["root"], operation_dir):
            fs.assert_safe(operation_dir)
            fs.create_exclusive(plan_path, plan_bytes, zone=FINAL)
        output_paths = _expected_output_paths(plan)
        return {**plan, "plan_sha256": _plan_hash(plan), "can_confirm": bool(files),
                "output_paths": output_paths,
                "report_paths": {fmt: f"{output_paths['Reports']}/{name}" for fmt, name in report_files.items()},
                "report_limits": dict(MAX_REPORT_BYTES),
                # Informational: rechecked when confirmed and when the copy starts.
                "disk": _disk_check(scope["root"], final_relative, counts["total_bytes"])}


def _verify_plan_scope(plan: dict[str, Any], scope: dict[str, Any], operation_id: str,
                       expected_files: list[dict[str, Any]], expected_excluded: int,
                       expected_unchecked: list[str]) -> None:
    if not _verify_signed_record(plan, "plan_signature", PLAN_DOMAIN):
        raise CaseFinalizationError("FINALIZATION_PLAN_UNTRUSTED", "저장된 최종확정 계획의 서명을 확인할 수 없습니다. 새 미리보기를 만드세요.")
    if plan.get("schema_version") not in CONFIRMABLE_PLAN_VERSIONS:
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
        "excluded_capture_file_count": expected_excluded, "include_unchecked": expected_unchecked,
        "report_files": report_file_names(scope["case_label"]),
    }
    for key, value in expected.items():
        if plan.get(key) != value:
            raise CaseFinalizationError("FINALIZATION_PLAN_CHANGED", "저장된 미리보기 계획이 현재 확정 경로·파일 목록과 다릅니다. 새 미리보기를 만드세요.")


def _expected_output_paths(plan: dict[str, Any]) -> dict[str, str]:
    operation_id = str(plan.get("operation_id") or "")
    case_label = str(plan.get("case_label") or "")
    final_relative = _relative(str(plan.get("final_relative_path") or ""))
    if not _OPERATION_ID.fullmatch(operation_id) or not spdm_storage._valid_windows_name(case_label):
        raise CaseFinalizationError("FINALIZATION_PLAN_INVALID", "최종확정 계획의 출력 경로가 올바르지 않습니다.")
    return {category: f"{final_relative}/{folder}/{case_label}/{operation_id}" for category, folder in OUTPUT_FOLDERS.items()}


def _legacy_reports_path(plan: dict[str, Any]) -> str:
    """Pre-§15 D21 ``Final/Reports/<Case>/<operation_id>`` of a plan, built from its components."""
    _expected_output_paths(plan)  # same component validation
    final_relative = _relative(str(plan.get("final_relative_path") or ""))
    return f"{final_relative}/{LEGACY_REPORTS_FOLDER}/{plan['case_label']}/{plan['operation_id']}"


def _recorded_outputs(plan: dict[str, Any], recorded: Any, files: list[dict[str, Any]],
                      reports: Any) -> tuple[dict[str, Any], dict[str, str], Any, dict[str, str]] | None:
    """(plan view, paths to verify, reports to verify, paths to show) for a completed record.

    Records signed before §15 D21 point their report key at ``Final/Reports``. That
    folder is not recognized any more: the record still stands on its CAE outputs and
    shows no reports (version-1 result mirrors under ``Final/Reports`` are dropped too).
    ``None`` when the signed paths match neither layout.
    """
    expected = _expected_output_paths(plan)
    if recorded == expected:
        return {**plan, "files": files}, expected, reports, expected
    legacy = {**expected, "Reports": _legacy_reports_path(plan)}
    if recorded != legacy:
        return None
    view = {**plan, "files": [item for item in files if isinstance(item, dict) and item.get("category") == "CAE"]}
    return view, {"CAE": expected["CAE"]}, ([] if isinstance(reports, list) else reports), expected


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


def _hash_path(path: str, root: Path, max_bytes: int) -> tuple[str, int]:
    fs = LocalFsProvider(root)
    try:
        fs.assert_safe(path)
        if not fs.is_file(path):
            raise CaseFinalizationError("FINALIZATION_OUTPUT_MISSING", "최종확정 파일을 찾을 수 없습니다.")
        digest = hashlib.sha256()
        size = 0
        with fs.open_read(path) as stream:
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


def _write_temp(parent: str, root: Path, source: BinaryIO, operation_id: str, max_bytes: int) -> tuple[str, str, int]:
    fs = LocalFsProvider(root)
    temporary = fs.join(parent, f".codex-partial-{operation_id}-{uuid4().hex}")
    digest = hashlib.sha256()
    size = 0

    def chunks() -> Iterator[bytes]:
        nonlocal size
        while chunk := source.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise CaseFinalizationError("FINALIZATION_REPORT_TOO_LARGE", "보고서 파일이 크기 제한을 넘습니다.")
            digest.update(chunk)
            yield chunk

    try:
        fs.create_exclusive(temporary, chunks(), zone=FINAL)
        fs.assert_safe(temporary)
    except BaseException:
        try:
            fs.remove(temporary, zone=FINAL, missing_ok=True)
        except OSError:
            pass
        raise
    return temporary, digest.hexdigest(), size


def _write_signed_metadata(operation_dir: str, root: Path, name: str, record: dict[str, Any],
                           *, max_bytes: int = MAX_METADATA_BYTES) -> None:
    fs = LocalFsProvider(root)
    payload = _encode(record)
    if len(payload) > max_bytes:
        raise CaseFinalizationError("FINALIZATION_METADATA_LIMIT", "최종확정 기록이 메타데이터 크기 제한을 초과했습니다.")
    temporary = fs.join(operation_dir, f".{name}-{uuid4().hex}.tmp")
    with _pin_directory_chain(root, operation_dir):
        try:
            fs.create_exclusive(temporary, payload, zone=FINAL)
            # reports.json is the mutable staging record of an unfinished operation.
            fs.replace(temporary, fs.join(operation_dir, name), zone=FINAL)
        except OSError as exc:
            raise _write_failed() from exc
        finally:
            try:
                fs.remove(temporary, zone=FINAL, missing_ok=True)
            except OSError:
                pass


def _read_staged_reports(operation_dir: str, root: Path, plan: dict[str, Any]) -> dict[str, Any] | None:
    fs = LocalFsProvider(root)
    path = fs.join(operation_dir, "reports.json")
    if not fs.exists(path, follow_links=False):
        return None
    try:
        fs.assert_safe(path)
    except spdm_storage.SpdmStorageError:
        return None
    record = _read_json(root, path)
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
    fs = LocalFsProvider(root)
    plan_path = fs.join(operation_dir, "plan.json")
    if not fs.exists(plan_path, follow_links=False):
        raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
    fs.assert_safe(operation_dir)
    fs.assert_safe(plan_path)
    plan = _read_json(root, plan_path, max_bytes=MAX_PLAN_BYTES)
    if not plan:
        raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
    if not _verify_signed_record(plan, "plan_signature", PLAN_DOMAIN):
        raise CaseFinalizationError("FINALIZATION_PLAN_UNTRUSTED", "저장된 최종확정 계획의 서명을 확인할 수 없습니다. 새 미리보기를 만드세요.")
    if plan.get("schema_version") not in CONFIRMABLE_PLAN_VERSIONS:
        raise CaseFinalizationError("FINALIZATION_PLAN_CHANGED", "이전 형식의 미리보기 계획입니다. 새 미리보기를 만드세요.")
    if (plan.get("operation_id"), plan.get("project_id"), plan.get("request_id"), plan.get("environment"),
            plan.get("case_id"), plan.get("capture_id"), plan.get("case_path"), plan.get("case_label")) != (
            operation_id, project_id, request_id, str(environment).upper(), case_id, capture_id,
            base_scope["case_path"], base_scope["case_label"]):
        raise CaseFinalizationError("FINALIZATION_OPERATION_SCOPE_MISMATCH", "최종확정 계획이 현재 Case 문맥과 일치하지 않습니다.")
    if plan.get("report_files") != report_file_names(base_scope["case_label"]):
        raise CaseFinalizationError("FINALIZATION_PLAN_CHANGED", "저장된 보고서 이름이 계획과 다릅니다. 새 미리보기를 만드세요.")
    if fs.exists(fs.join(operation_dir, "complete.json"), follow_links=False):
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
        if not LocalFsProvider(root).is_dir(metadata_dir) or not LocalFsProvider(root).is_dir(operation_dir):
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
    fs = LocalFsProvider(root)
    staging_dir = _ensure_dir(root, f"{target['operation_relative']}/reports")
    temporary: str | None = None
    try:
        with _pin_directory_chain(root, staging_dir):
            upload.seek(0)
            temporary, digest, size = _write_temp(staging_dir, root, upload, operation_id, MAX_REPORT_BYTES[report_format])
        if (digest, size) != (checked["sha256"], checked["size"]):
            raise CaseFinalizationError("FINALIZATION_REPORT_UPLOAD_CHANGED", "보고서 업로드가 검사 중에 바뀌었습니다. 다시 올리세요.")
        with _request_lock(f"{metadata_dir}/.request.lock", root), _operation_lock(root, operation_dir) as idle:
            if not idle:
                raise CaseFinalizationError("FINALIZATION_JOB_ACTIVE", "Final 복사가 진행 중입니다. 끝난 뒤 다시 시도하세요.")
            plan = _load_operation_plan(conn, base_scope, operation_dir, **identity)
            file_name = plan["report_files"][report_format]
            record = _read_staged_reports(operation_dir, root, plan) or {
                "schema_version": 1, "operation_id": operation_id, "plan_sha256": _plan_hash(plan),
                "reports": {}, "history": {},
            }
            record = {key: value for key, value in record.items() if key != "reports_signature"}
            with _pin_directory_chain(root, staging_dir):
                fs.replace(temporary, fs.join(staging_dir, file_name), zone=FINAL)
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
                fs.remove(temporary, zone=FINAL, missing_ok=True)
            except OSError:
                pass
    output_paths = _expected_output_paths(plan)
    return {"operation_id": operation_id, "format": report_format, "file_name": file_name,
            "size": size, "sha256": digest, "report_path": f"{output_paths['Reports']}/{file_name}",
            "status": "STAGED"}


def _valid_report_records(plan: dict[str, Any], reports: Any) -> bool:
    if plan.get("schema_version") == 1:
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
    elif plan.get("schema_version") in {2, PLAN_VERSION}:
        sources = plan.get("scene_sources")
        if not isinstance(sources, list) or not sources or len(sources) > MAX_SCENE_SOURCES:
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
    fs = LocalFsProvider(root)
    path = result_registration_paths._safe_existing(root, relative)
    fs.assert_safe(path)
    info = fs.stat(path, follow_links=False, missing_ok=False)
    return info.kind == "file" and info.size == size


def _verify_outputs(scope: dict[str, Any], plan: dict[str, Any], output_paths: dict[str, str],
                    reports: list[dict[str, Any]] | None = None, *, deep: bool = True) -> bool:
    """``deep`` re-hashes every output; otherwise existence and recorded size only."""
    root: Path = scope["root"]
    fs = LocalFsProvider(root)
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
        if not fs.is_file(path) or _hash_source(root, relative) != (item["size"], item["sha256"]):
            return False
        fs.assert_safe(path)
        if not PurePosixPath(fs.path(path)).relative_to(PurePosixPath(fs.path(base))).as_posix():
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
        if not fs.is_dir(path):
            return False
    return True


def _reports_directory_entries(root: Path, relative: str, operation_id: str) -> list[tuple[str, bool]]:
    """Entries of this operation's Final/Report folder, without its own partial temp files."""
    fs = LocalFsProvider(root)
    try:
        path = result_registration_paths._safe_existing(root, relative, allow_missing_leaf=True)
        if not fs.exists(path, follow_links=False):
            return []
        fs.assert_safe(path)
        if not fs.is_dir(path):
            raise CaseFinalizationError("FINALIZATION_REPORTS_UNEXPECTED_FILE", "Final/Report 보고서 폴더 자리에 다른 항목이 있습니다. 기존 자료를 보존하고 관리자에게 문의하세요.")
        entries: list[tuple[str, bool]] = []
        for entry in fs.list(path):
            is_file = entry.kind == "file"
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
        raise CaseFinalizationError("FINALIZATION_SOURCE_UNAVAILABLE", "Final/Report 보고서 폴더를 읽을 수 없습니다.") from exc


def _remove_staged_reports(root: Path, operation_dir: str, plan: dict[str, Any], history: dict[str, Any]) -> None:
    """After completion, drop this operation's own staged copies (hash in the signed history); best effort."""
    fs = LocalFsProvider(root)
    staging_dir = fs.join(operation_dir, "reports")
    try:
        if not fs.is_dir(staging_dir):
            return
        with _pin_directory_chain(root, staging_dir):
            for fmt, name in plan["report_files"].items():
                path = fs.join(staging_dir, str(name))
                if not fs.exists(path, follow_links=False):
                    continue
                try:
                    digest, _size = _hash_path(path, root, MAX_REPORT_BYTES[fmt])
                except CaseFinalizationError:
                    continue
                if digest in (history.get(fmt) or []):
                    fs.remove(path, zone=FINAL)
    except (OSError, CaseFinalizationError, spdm_storage.SpdmStorageError):
        pass


def _effective_files(plan: dict[str, Any], completed: dict[str, Any]) -> list[Any]:
    """Version 3 records carry the per-file hashes computed while copying in ``complete.json``."""
    if plan.get("schema_version") == PLAN_VERSION:
        files = completed.get("files")
        return files if isinstance(files, list) else []
    return plan.get("files") or []


def _files_match(plan: dict[str, Any], completed_files: Any) -> bool:
    """Version 3: the completed list is the plan list with every SHA-256 filled in."""
    planned = plan.get("files")
    if plan.get("schema_version") != PLAN_VERSION:
        return completed_files == planned
    if not isinstance(planned, list) or not isinstance(completed_files, list) or len(planned) != len(completed_files):
        return False
    for want, got in zip(planned, completed_files):
        if not isinstance(want, dict) or not isinstance(got, dict):
            return False
        if {key: value for key, value in want.items() if key != "sha256"} != {key: value for key, value in got.items() if key != "sha256"}:
            return False
        digest = got.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False
        if want.get("sha256") is not None and want["sha256"] != digest:
            return False
    return True


def _completed_if_valid(conn: ConnectionLike, base_scope: dict[str, Any], *, project_id: str,
                        request_id: str, environment: str, case_id: str, capture_id: str,
                        operation_id: str, metadata_relative: str, deep: bool = True) -> dict[str, Any] | None:
    """Signed, in-scope completed record; ``deep`` re-hashes every output, otherwise existence and size."""
    operation_relative = f"{metadata_relative}/{operation_id}"
    try:
        fs = LocalFsProvider(base_scope["root"])
        operation_dir = result_registration_paths._safe_existing(base_scope["root"], operation_relative)
        fs.assert_safe(operation_dir)
        plan_path = fs.join(operation_dir, "plan.json")
        complete_path = fs.join(operation_dir, "complete.json")
        if not fs.exists(plan_path) or not fs.exists(complete_path):
            return None
        fs.assert_safe(plan_path)
        fs.assert_safe(complete_path)
        plan = _read_json(base_scope["root"], plan_path, max_bytes=MAX_PLAN_BYTES)
        complete = _read_json(base_scope["root"], complete_path, max_bytes=MAX_PLAN_BYTES)
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
        reports = complete.get("reports")
        layout = _recorded_outputs(plan, complete.get("output_paths"), _effective_files(plan, complete), reports)
        if (complete.get("status") != "COMPLETE" or complete.get("operation_id") != operation_id
                or complete.get("project_id") != project_id or complete.get("request_id") != request_id
                or complete.get("environment") != str(environment).upper() or complete.get("case_id") != case_id
                or complete.get("capture_id") != capture_id or not _files_match(plan, complete.get("files"))
                or layout is None
                or complete.get("plan_sha256") != _plan_hash(plan)
                or not _valid_report_records(plan, reports)):
            return None
        view, verify_paths, verify_reports, shown_paths = layout
        if not _verify_outputs(base_scope, view, verify_paths, verify_reports, deep=deep):
            return None
        return _completed_response(view, {**complete, "output_paths": shown_paths, "reports": verify_reports})
    except (CaseFinalizationError, result_registration_paths.ResultRegistrationError,
            spdm_storage.SpdmStorageError, OSError, TypeError, ValueError, KeyError):
        return None


# ---------------------------------------------------------------------------
# W2: background copy job (staging -> verify -> rename -> complete.json).
# ---------------------------------------------------------------------------

def _operation_paths(operation_relative: str) -> dict[str, str]:
    staging = f"{operation_relative}/staging"
    return {"op": operation_relative, "staging": staging, "partial": f"{staging}/partial",
            "CAE": f"{staging}/CAE", "Reports": f"{staging}/Report",
            "copied": f"{operation_relative}/copied.jsonl", "job": f"{operation_relative}/job.json",
            "progress": f"{operation_relative}/progress.json", "lock": f"{operation_relative}/.job.lock"}


def _job_key(root: Path, relative: str) -> str:
    return f"{root}::{relative}"


@contextmanager
def _operation_lock(root: Path, operation_dir: str) -> Iterator[bool]:
    """Per-operation copy-job lock without waiting: yields ``False`` while a copy job holds it."""
    fs = LocalFsProvider(root)
    try:
        lock = fs.try_lock(_operation_paths(operation_dir)["lock"], zone=FINAL)
        lock.__enter__()
    except spdm_storage.SpdmStorageError as exc:
        if exc.code == "FINALIZATION_LOCK_BUSY":
            yield False
            return
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    try:
        yield True
    finally:
        lock.__exit__(None, None, None)


def _read_signed(root: Path, path: str, field: str, domain: bytes, *,
                 max_bytes: int = MAX_METADATA_BYTES) -> dict[str, Any] | None:
    fs = LocalFsProvider(root)
    try:
        if not fs.exists(path, follow_links=False):
            return None
        fs.assert_safe(path)
    except (OSError, spdm_storage.SpdmStorageError):
        return None
    record = _read_json(root, path, max_bytes=max_bytes)
    if not record or not _verify_signed_record(record, field, domain):
        return None
    return record


def _read_job(root: Path, operation_relative: str) -> dict[str, Any] | None:
    job = _read_signed(root, _operation_paths(operation_relative)["job"], "job_signature", JOB_DOMAIN)
    if (not job or job.get("schema_version") != 1 or job.get("operation_id") != PurePosixPath(operation_relative).name
            or job.get("metadata_relative_path") != operation_relative
            or not isinstance(job.get("reports"), list) or not job["reports"]):
        return None
    return job


def _read_progress(root: Path, operation_relative: str, job: dict[str, Any]) -> dict[str, Any] | None:
    record = _read_signed(root, _operation_paths(operation_relative)["progress"], "progress_signature", PROGRESS_DOMAIN)
    if (not record or record.get("operation_id") != job.get("operation_id")
            or record.get("plan_sha256") != job.get("plan_sha256") or record.get("state") not in JOB_STATES):
        return None
    return record


def _write_progress(root: Path, operation_relative: str, record: dict[str, Any]) -> None:
    _write_signed_metadata(operation_relative, root, "progress.json",
                           _signed_record({**record, "updated_at": _now()}, "progress_signature", PROGRESS_DOMAIN))


class _Progress:
    """Signed ``progress.json`` writer (throttled); never a completion record."""

    def __init__(self, root: Path, operation_relative: str, job: dict[str, Any], previous: dict[str, Any] | None) -> None:
        self.root = root
        self.operation_relative = operation_relative
        self.record: dict[str, Any] = {
            "schema_version": 1, "operation_id": job["operation_id"], "plan_sha256": job["plan_sha256"],
            "state": "RUNNING", "phase": "COPYING", "files_done": 0, "files_total": job["files_total"],
            "bytes_done": 0, "bytes_total": job["bytes_total"], "current_file": None, "error": None,
            "published": sorted((previous or {}).get("published") or []),
            "attempt": int((previous or {}).get("attempt") or 0) + 1, "started_at": _now(),
        }
        self._saved = 0.0

    def save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._saved < PROGRESS_WRITE_INTERVAL:
            return
        self._saved = now
        _write_progress(self.root, self.operation_relative, self.record)

    def update(self, *, force: bool = False, **fields: Any) -> None:
        self.record.update(fields)
        self.save(force)

    def add_bytes(self, count: int) -> None:
        self.record["bytes_done"] += count
        self.save()

    def file_done(self) -> None:
        self.record["files_done"] += 1
        self.save()

    def fail(self, code: str, message: str) -> None:
        self.record.update(state="FAILED", error={"code": code, "message": message}, current_file=None)
        try:
            self.save(force=True)
        except Exception:  # noqa: BLE001 - the failure itself is already being reported
            _LOG.exception("Final copy progress could not record a failure")


def _copied_line(job: dict[str, Any], case_relative_path: str, size: int, digest: str) -> bytes:
    record = _signed_record({"operation_id": job["operation_id"], "plan_sha256": job["plan_sha256"],
                             "path": case_relative_path, "size": size, "sha256": digest}, "sig", COPIED_DOMAIN)
    return _encode(record) + b"\n"


def _read_copied(root: Path, operation_relative: str, job: dict[str, Any]) -> dict[str, tuple[int, str]]:
    """Signed lines of files already staged by earlier attempts (torn or foreign lines are ignored)."""
    path = _operation_paths(operation_relative)["copied"]
    fs = LocalFsProvider(root)
    try:
        if not fs.exists(path, follow_links=False):
            return {}
        payload = fs.read_small_nofollow(path, max_bytes=MAX_PLAN_BYTES)
    except (OSError, spdm_storage.SpdmStorageError):
        return {}
    copied: dict[str, tuple[int, str]] = {}
    for line in (payload or b"").split(b"\n"):
        try:
            record = json.loads(line.decode("utf-8")) if line.strip() else None
        except (ValueError, UnicodeError, RecursionError):
            continue
        if (not isinstance(record, dict) or not _verify_signed_record(record, "sig", COPIED_DOMAIN)
                or record.get("operation_id") != job["operation_id"] or record.get("plan_sha256") != job["plan_sha256"]
                or not isinstance(record.get("path"), str) or type(record.get("size")) is not int
                or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256") or ""))):
            continue
        copied[record["path"].casefold()] = (record["size"], record["sha256"])
    return copied


def _report_identity(reports: list[dict[str, Any]]) -> list[tuple[str, str, int, str]]:
    return sorted((str(item["format"]), str(item["file_name"]), int(item["size"]), str(item["sha256"])) for item in reports)


def _job_view(root: Path, operation_relative: str, job: dict[str, Any]) -> dict[str, Any]:
    progress = _read_progress(root, operation_relative, job) or {}
    state = progress.get("state") or "QUEUED"
    return {
        "operation_id": job["operation_id"], "state": state, "phase": progress.get("phase"),
        "files_done": int(progress.get("files_done") or 0), "files_total": job["files_total"],
        "bytes_done": int(progress.get("bytes_done") or 0), "bytes_total": job["bytes_total"],
        "current_file": progress.get("current_file"), "error": progress.get("error"),
        "attempt": int(progress.get("attempt") or 0), "queued_at": job.get("queued_at"),
        "started_at": progress.get("started_at"), "updated_at": progress.get("updated_at"),
        "case_id": job.get("case_id"), "capture_id": job.get("capture_id"),
        "reports": [{"format": item["format"], "file_name": item["file_name"], "size": item["size"], "sha256": item["sha256"]}
                    for item in job["reports"]],
        "output_paths": job.get("output_paths"),
        "active": case_finalization_jobs.is_active(_job_key(root, operation_relative)),
        "record": None,
    }


def _submit(root: Path, operation_relative: str) -> None:
    case_finalization_jobs.submit(
        _job_key(root, operation_relative), _job_key(root, posixpath.dirname(operation_relative)),
        lambda: run_job(root, operation_relative),
    )


def _resume_if_interrupted(root: Path, operation_relative: str, view: dict[str, Any]) -> dict[str, Any]:
    """A QUEUED/RUNNING job that no worker of this process runs was interrupted: resume it."""
    if view["state"] in {"QUEUED", "RUNNING"} and not view["active"]:
        _submit(root, operation_relative)
        return {**view, "active": True}
    return view


def confirm(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
            case_id: str, capture_id: str, operation_id: str, actor: str,
            report_formats: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """Validate, record a signed copy job and return at once (W2); the worker publishes CAE and reports.

    Returns the job view (``state`` QUEUED/RUNNING, or COMPLETE with ``record`` for a finished operation).
    """
    if not _OPERATION_ID.fullmatch(operation_id):
        raise CaseFinalizationError("FINALIZATION_OPERATION_ID_INVALID", "최종확정 요청 ID가 올바르지 않습니다.")
    if any(fmt not in REPORT_FORMATS for fmt in report_formats):
        raise CaseFinalizationError("FINALIZATION_REPORT_FORMAT_INVALID", "보고서 형식은 PPTX 또는 HTML이어야 합니다.")
    formats = [fmt for fmt in REPORT_FORMATS if fmt in set(report_formats)]
    base_scope = _scope_for_status(conn, project_id, request_id, environment, case_id)
    _final_relative, metadata_relative = _final_paths(base_scope)
    base_fs = LocalFsProvider(base_scope["root"])
    metadata_dir = result_registration_paths._safe_existing(base_scope["root"], metadata_relative, allow_missing_leaf=True)
    if not base_fs.is_dir(metadata_dir):
        raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
    with _request_lock(f"{metadata_dir}/.request.lock", base_scope["root"]):
        completed_record = _completed_if_valid(
            conn, base_scope, project_id=project_id, request_id=request_id,
            environment=environment, case_id=case_id, capture_id=capture_id,
            operation_id=operation_id, metadata_relative=metadata_relative, deep=False,
        )
        if completed_record:
            # Completed operations are immutable: a repeated request returns the signed record.
            return _complete_view(completed_record)
        operation_relative = f"{metadata_relative}/{operation_id}"
        operation_dir = result_registration_paths._safe_existing(base_scope["root"], operation_relative, allow_missing_leaf=True)
        if not base_fs.is_dir(operation_dir):
            raise CaseFinalizationError("FINALIZATION_PLAN_NOT_FOUND", "미리보기 계획을 찾을 수 없습니다. 새 미리보기를 만드세요.")
        complete_path = base_fs.join(operation_dir, "complete.json")
        if base_fs.exists(complete_path, follow_links=False):
            raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "기존 완료 표식과 파일이 일치하지 않습니다. 기존 자료를 보존하고 관리자에게 문의하세요.")
        if not formats:
            raise CaseFinalizationError("FINALIZATION_REPORT_REQUIRED", "PPTX 또는 HTML 보고서를 하나 이상 선택하세요.")
        with _operation_lock(base_scope["root"], operation_dir) as idle:
            if not idle:
                running = _read_job(base_scope["root"], operation_dir)
                if running is None:
                    raise CaseFinalizationError("FINALIZATION_JOB_ACTIVE", "Final 복사가 진행 중입니다.")
                return _job_view(base_scope["root"], operation_dir, running)
            job = _prepare_job(conn, base_scope, operation_dir, formats=formats, actor=actor, identity={
                "operation_id": operation_id, "project_id": project_id, "request_id": request_id,
                "environment": environment, "case_id": case_id, "capture_id": capture_id})
    _submit(base_scope["root"], operation_dir)
    return _job_view(base_scope["root"], operation_dir, job)


def _prepare_job(conn: ConnectionLike, base_scope: dict[str, Any], operation_dir: str, *, formats: list[str],
                 actor: str, identity: dict[str, str]) -> dict[str, Any]:
    """Every synchronous check of confirm, then the signed ``job.json`` and a QUEUED progress record."""
    operation_id = identity["operation_id"]
    scope = _scope(conn, identity["project_id"], identity["request_id"], identity["environment"],
                   identity["case_id"], identity["capture_id"])
    root: Path = scope["root"]
    fs = LocalFsProvider(root)
    plan = _load_operation_plan(conn, base_scope, operation_dir, **identity)
    expected_files, expected_excluded, expected_unchecked = _build_files(scope)
    _verify_plan_scope(plan, scope, operation_id, expected_files, expected_excluded, expected_unchecked)
    plan_hash = _plan_hash(plan)
    staged = _read_staged_reports(operation_dir, root, plan)
    reports: list[dict[str, Any]] = []
    for fmt in formats:
        entry = (staged or {}).get("reports", {}).get(fmt)
        if not isinstance(entry, dict) or entry.get("file_name") != plan["report_files"][fmt]:
            raise CaseFinalizationError("FINALIZATION_REPORT_NOT_STAGED", f"{fmt.upper()} 보고서가 올라가지 않았습니다. 보고서를 다시 올리세요.")
        report = {"format": fmt, "file_name": entry["file_name"], "size": entry.get("size"), "sha256": entry.get("sha256")}
        staged_path = fs.join(operation_dir, "reports", entry["file_name"])
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
    # §15 D21: an attempt by an earlier build may have published into the legacy
    # Final/Reports folder; completing now would leave those reports unrecorded.
    if _reports_directory_entries(root, _legacy_reports_path(plan), operation_id):
        raise CaseFinalizationError(
            "FINALIZATION_LEGACY_REPORTS_PRESENT",
            "이전 버전이 Final/Reports에 보고서를 남겼습니다. 폴더를 Final/Report로 옮기거나 정리한 뒤 다시 시도하세요.")
    previous_job = _read_job(root, operation_dir)
    if previous_job is not None and previous_job.get("plan_sha256") != plan_hash:
        previous_job = None
    previous = _read_progress(root, operation_dir, previous_job) if previous_job else None
    published = set((previous or {}).get("published") or [])
    if "Reports" in published and previous_job and _report_identity(previous_job["reports"]) != _report_identity(reports):
        raise CaseFinalizationError(
            "FINALIZATION_REPORT_FORMATS_MISMATCH",
            "이 Final 지정의 보고서는 이미 Final/Report에 공개되었습니다. 같은 보고서 형식으로 다시 시도하세요.")
    outputs = _expected_output_paths(plan)
    paths = _operation_paths(operation_dir)
    for category in ("CAE", "Reports"):
        if category in published:
            continue
        if fs.exists(outputs[category], follow_links=False) and (previous_job is None or fs.exists(paths[category], follow_links=False)):
            raise CaseFinalizationError(
                "FINALIZATION_DESTINATION_CONFLICT",
                f"Final/{OUTPUT_FOLDERS[category]}/{plan['case_label']}/{operation_id} 폴더가 이미 있습니다. 기존 자료를 보존했습니다. 새 미리보기로 다시 지정하세요.")
    required = 0
    if "CAE" not in published:
        copied = _read_copied(root, operation_dir, previous_job) if previous_job else {}
        required += sum(int(item["size"]) for item in plan["files"]
                        if copied.get(str(item["case_relative_path"]).casefold(), (None,))[0] != item["size"])
    if "Reports" not in published:
        required += sum(int(item["size"]) for item in reports)
    _require_disk_space(root, plan["final_relative_path"], required)
    job = _signed_record({
        "schema_version": 1, "operation_id": operation_id, "plan_sha256": plan_hash,
        "project_id": identity["project_id"], "request_id": identity["request_id"],
        "environment": str(identity["environment"]).upper(), "case_id": identity["case_id"],
        "capture_id": identity["capture_id"], "case_label": plan["case_label"],
        "metadata_relative_path": operation_dir, "final_relative_path": plan["final_relative_path"],
        "output_paths": outputs, "reports": reports,
        "files_total": len(plan["files"]), "bytes_total": sum(int(item["size"]) for item in plan["files"]),
        "created_by": actor, "queued_at": _now(),
    }, "job_signature", JOB_DOMAIN)
    _write_signed_metadata(operation_dir, root, "job.json", job)
    _write_progress(root, operation_dir, {
        "schema_version": 1, "operation_id": operation_id, "plan_sha256": plan_hash, "state": "QUEUED",
        "phase": None, "files_done": 0, "files_total": job["files_total"], "bytes_done": 0,
        "bytes_total": job["bytes_total"], "current_file": None, "error": None,
        "published": sorted(published), "attempt": int((previous or {}).get("attempt") or 0),
        "started_at": None,
    })
    return job


def run_job(root: Path, operation_relative: str) -> str:
    """Worker entry: run (or resume) one recorded copy job; returns the outcome for logs/tests."""
    try:
        operation_dir = result_registration_paths._safe_existing(root, operation_relative)
        with _operation_lock(root, operation_dir) as idle:
            if not idle:
                return "BUSY"  # Another worker (or process) runs this operation.
            return _run_job_locked(root, operation_dir)
    except (CaseFinalizationError, result_registration_paths.ResultRegistrationError,
            spdm_storage.SpdmStorageError, OSError) as exc:
        _LOG.warning("Final copy job %s could not start: %s", operation_relative, exc)
        return "ERROR"


def _run_job_locked(root: Path, operation_dir: str) -> str:
    fs = LocalFsProvider(root)
    paths = _operation_paths(operation_dir)
    plan = _read_signed(root, f"{operation_dir}/plan.json", "plan_signature", PLAN_DOMAIN, max_bytes=MAX_PLAN_BYTES)
    job = _read_job(root, operation_dir)
    if (not plan or not job or plan.get("schema_version") != PLAN_VERSION
            or plan.get("metadata_relative_path") != operation_dir or job.get("plan_sha256") != _plan_hash(plan)
            or job.get("output_paths") != _expected_output_paths(plan) or not _valid_report_records(plan, job["reports"])):
        _LOG.warning("Final copy job %s has no valid signed plan/job record", operation_dir)
        return "INVALID"
    previous = _read_progress(root, operation_dir, job)
    if fs.exists(fs.join(operation_dir, "complete.json"), follow_links=False):
        if (previous or {}).get("state") != "COMPLETE":
            _write_progress(root, operation_dir, {**(previous or {}), "operation_id": job["operation_id"],
                                                  "plan_sha256": job["plan_sha256"], "state": "COMPLETE", "error": None})
        _cleanup_after_complete(root, plan, operation_dir)
        return "COMPLETE"
    progress = _Progress(root, operation_dir, job, previous)
    try:
        progress.save(force=True)
        _execute_job(root, plan, job, progress, paths)
        return "COMPLETE"
    except CaseFinalizationError as exc:
        progress.fail(exc.code, str(exc))
    except StorageError as exc:  # SpdmStorageError and provider zone refusals
        progress.fail(exc.code, str(exc))
    except FileExistsError:
        progress.fail("FINALIZATION_DESTINATION_CONFLICT", "Final 대상 경로에 이미 항목이 있어 덮어쓰지 않았습니다.")
    except OSError:
        progress.fail(_write_failed().code, str(_write_failed()))
    except Exception:  # noqa: BLE001 - a worker must always leave a FAILED record
        _LOG.exception("Final copy job %s failed", operation_dir)
        progress.fail("FINALIZATION_INTERNAL_ERROR", "Final 복사 중 예기치 않은 오류가 발생했습니다. 다시 시도하세요.")
    return "FAILED"


def _ensure_staging_dir(root: Path, relative: str, ensured: set[str]) -> None:
    """Folders inside this operation's own staging tree (``.finalizations/<id>/staging``)."""
    if relative in ensured:
        return
    fs = LocalFsProvider(root)
    try:
        fs.mkdirs(relative, zone=FINAL, parents=True, exist_ok=True)
        fs.assert_safe(relative)
    except FileExistsError as exc:
        raise CaseFinalizationError("FINALIZATION_PATH_CONFLICT", "임시 폴더 자리에 파일이 있습니다.") from exc
    except spdm_storage.SpdmStorageError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise _write_failed() from exc
    if not fs.is_dir(relative):
        raise CaseFinalizationError("FINALIZATION_PATH_CONFLICT", "임시 폴더를 안전하게 만들 수 없습니다.")
    ensured.add(relative)


def _stage_file(root: Path, source_relative: str, staged_relative: str, partial_dir: str, *,
                expected_size: int, expected_sha256: str | None, expected_modified_ns: int | None,
                on_progress: Callable[[int], None] | None, ensured: set[str]) -> str:
    """Stream one source into ``partial/``, check its pin, then move it into the staging tree."""
    fs = LocalFsProvider(root)
    source = _relative(source_relative)
    try:
        source_path = result_registration_paths._safe_existing(root, source)
    except result_registration_paths.ResultRegistrationError as exc:
        if exc.code == "SPDM_FOLDER_MISSING":
            raise CaseFinalizationError("FINALIZATION_SOURCE_MISSING", f"원본 파일을 찾을 수 없습니다: {source}") from exc
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    temporary: str | None = f"{partial_dir}/{uuid4().hex}"
    try:
        try:
            result = fs.copy_stream(source_path, temporary, zone=FINAL, on_progress=on_progress)
        except spdm_storage.SpdmStorageError as exc:
            raise _storage_error(exc, source) from exc
        if (result.size != expected_size or (expected_sha256 is not None and result.sha256 != expected_sha256)
                or (expected_modified_ns is not None and result.modified_ns != expected_modified_ns)):
            raise CaseFinalizationError("FINALIZATION_SOURCE_STALE", f"미리보기 이후 원본이 바뀌었습니다: {source}")
        _ensure_staging_dir(root, posixpath.dirname(staged_relative), ensured)
        existing = fs.stat(staged_relative, follow_links=False, missing_ok=True)
        if existing is not None:
            if existing.kind != "file" or existing.is_link:
                raise CaseFinalizationError("FINALIZATION_PATH_CONFLICT", "임시 폴더에 예상하지 않은 항목이 있습니다.")
            fs.remove(staged_relative, zone=FINAL)
        fs.replace(temporary, staged_relative, zone=FINAL)
        temporary = None
        return result.sha256
    except OSError as exc:
        raise _write_failed() from exc
    finally:
        if temporary is not None:
            try:
                fs.remove(temporary, zone=FINAL, missing_ok=True)
            except OSError:
                pass


def _clear_partial(root: Path, partial_dir: str) -> None:
    """Partial files of interrupted attempts (always this operation's own; redone from the start)."""
    fs = LocalFsProvider(root)
    for entry in fs.list(partial_dir):
        if entry.kind == "file" and not entry.is_link and re.fullmatch(r"[0-9a-f]{32}", entry.name):
            fs.remove(fs.join(partial_dir, entry.name), zone=FINAL, missing_ok=True)


def _execute_job(root: Path, plan: dict[str, Any], job: dict[str, Any], progress: _Progress,
                 paths: dict[str, str]) -> None:
    fs = LocalFsProvider(root)
    operation_dir = paths["op"]
    operation_id = str(plan["operation_id"])
    outputs = _expected_output_paths(plan)
    files: list[dict[str, Any]] = plan["files"]
    reports: list[dict[str, Any]] = job["reports"]
    published = set(progress.record["published"])
    done = _read_copied(root, operation_dir, job)
    hashes: dict[str, str] = {}
    for item in files:
        key = str(item["case_relative_path"]).casefold()
        if item.get("sha256"):
            hashes[key] = str(item["sha256"])
        elif key in done and done[key][0] == item["size"]:
            hashes[key] = done[key][1]

    # 1) Folders published by an earlier attempt of this same operation.
    for category in ("CAE", "Reports"):
        destination_exists = fs.exists(outputs[category], follow_links=False)
        if category in published:
            if not destination_exists:
                raise CaseFinalizationError("FINALIZATION_OUTPUT_MISSING", f"이미 공개한 Final/{OUTPUT_FOLDERS[category]} 폴더를 찾을 수 없습니다. 관리자에게 문의하세요.")
            continue
        if destination_exists:
            # Interrupted between the rename and its progress record: adopt only an exact, hash-verified match.
            if fs.exists(paths[category], follow_links=False) or not _published_matches(root, plan, reports, category, outputs[category], hashes):
                raise CaseFinalizationError(
                    "FINALIZATION_DESTINATION_CONFLICT",
                    f"Final/{OUTPUT_FOLDERS[category]}/{plan['case_label']}/{operation_id} 폴더가 이미 있습니다. 기존 자료를 보존했습니다.")
            published.add(category)
            progress.update(published=sorted(published), force=True)

    # 2) Stage everything that is not published yet.
    ensured: set[str] = set()
    if published != {"CAE", "Reports"}:
        required = 0
        if "CAE" not in published:
            required += sum(int(item["size"]) for item in files
                            if done.get(str(item["case_relative_path"]).casefold(), (None,))[0] != item["size"])
        if "Reports" not in published:
            required += sum(int(item["size"]) for item in reports)
        _require_disk_space(root, plan["final_relative_path"], required)
        _ensure_staging_dir(root, paths["partial"], ensured)
        _clear_partial(root, paths["partial"])
    if "CAE" not in published:
        _stage_cae(root, files, job, progress, paths, done, hashes, ensured)
    if "Reports" not in published:
        _stage_reports(root, reports, progress, paths, ensured)
    if "CAE" not in published:
        _verify_staged_cae(root, files, job, progress, paths, hashes, ensured)

    # 3) Publish: same-volume folder renames, CAE first; never over an existing folder.
    progress.update(phase="PUBLISHING", current_file=None, force=True)
    for category in ("CAE", "Reports"):
        if category in published:
            continue
        _ensure_dir(root, posixpath.dirname(outputs[category]))
        _rename_with_retry(fs, paths[category], outputs[category])
        published.add(category)
        progress.update(published=sorted(published), force=True)

    # 4) complete.json only after both folders are in place and checked.
    completed_files = []
    for item in files:
        digest = hashes.get(str(item["case_relative_path"]).casefold())
        if not digest:
            raise CaseFinalizationError("FINALIZATION_OUTPUT_VERIFY_FAILED", "복사한 파일의 해시 기록이 없습니다. 다시 시도하세요.")
        completed_files.append({**item, "sha256": digest})
    _verify_published(root, plan, completed_files, reports, outputs)
    metadata_dir = posixpath.dirname(operation_dir)
    with _request_lock(f"{metadata_dir}/.request.lock", root):
        _write_complete(root, plan, job, completed_files, reports, outputs)
    progress.update(state="COMPLETE", phase=None, current_file=None, error=None, force=True)
    _cleanup_after_complete(root, plan, operation_dir)


def _stage_cae(root: Path, files: list[dict[str, Any]], job: dict[str, Any], progress: _Progress,
               paths: dict[str, str], done: dict[str, tuple[int, str]], hashes: dict[str, str], ensured: set[str]) -> None:
    fs = LocalFsProvider(root)
    progress.update(phase="COPYING", files_done=0, bytes_done=0, force=True)
    _ensure_staging_dir(root, paths["CAE"], ensured)
    for item in files:
        key = str(item["case_relative_path"]).casefold()
        staged = f"{paths['CAE']}/{_relative(str(item['case_relative_path']))}"
        record = done.get(key)
        if record and record[0] == item["size"] and (not item.get("sha256") or record[1] == item["sha256"]):
            info = fs.stat(staged, follow_links=False, missing_ok=True)
            if info is not None and info.kind == "file" and not info.is_link and info.size == item["size"]:
                progress.record["bytes_done"] += int(item["size"])
                progress.file_done()
                continue  # Staged by an earlier attempt; its hash is checked again before publication.
        progress.record["current_file"] = item["case_relative_path"]
        digest = _stage_file(root, item["source_relative_path"], staged, paths["partial"],
                             expected_size=int(item["size"]), expected_sha256=item.get("sha256") or None,
                             expected_modified_ns=item.get("modified_ns") if item.get("source_basis") == "CURRENT_CONFIRMED_SCENE" else None,
                             on_progress=progress.add_bytes, ensured=ensured)
        hashes[key] = digest
        done[key] = (int(item["size"]), digest)
        fs.append_bytes(paths["copied"], _copied_line(job, item["case_relative_path"], int(item["size"]), digest), zone=FINAL)
        progress.file_done()
    fs.append_bytes(paths["copied"], b"", zone=FINAL, fsync=True)


def _stage_reports(root: Path, reports: list[dict[str, Any]], progress: _Progress, paths: dict[str, str],
                   ensured: set[str]) -> None:
    fs = LocalFsProvider(root)
    report_dir = paths["Reports"]
    _ensure_staging_dir(root, report_dir, ensured)
    wanted = {str(item["file_name"]).casefold() for item in reports}
    for entry in fs.list(report_dir):
        if entry.name.casefold() in wanted:
            continue
        if entry.kind != "file" or entry.is_link:
            raise CaseFinalizationError("FINALIZATION_STAGING_UNEXPECTED", "보고서 임시 폴더에 예상하지 않은 항목이 있습니다.")
        fs.remove(fs.join(report_dir, entry.name), zone=FINAL)  # An earlier report choice of this operation.
    for report in reports:
        staged = f"{report_dir}/{report['file_name']}"
        progress.update(current_file=str(report["file_name"]))
        if fs.exists(staged, follow_links=False):
            try:
                if _hash_path(staged, root, MAX_REPORT_BYTES[report["format"]]) == (report["sha256"], report["size"]):
                    continue
            except CaseFinalizationError:
                pass
            fs.remove(staged, zone=FINAL)
        try:
            _stage_file(root, f"{paths['op']}/reports/{report['file_name']}", staged, paths["partial"],
                        expected_size=int(report["size"]), expected_sha256=str(report["sha256"]),
                        expected_modified_ns=None, on_progress=None, ensured=ensured)
        except CaseFinalizationError as exc:
            if exc.code in {"FINALIZATION_SOURCE_STALE", "FINALIZATION_SOURCE_MISSING"}:
                raise CaseFinalizationError("FINALIZATION_REPORT_STAGE_INVALID", "올린 보고서가 기록과 다릅니다. 보고서를 다시 올리세요.") from exc
            raise


def _verify_staged_cae(root: Path, files: list[dict[str, Any]], job: dict[str, Any], progress: _Progress,
                       paths: dict[str, str], hashes: dict[str, str], ensured: set[str]) -> None:
    """Read every staged file back (constant memory) and compare with the pinned/copied hash."""
    fs = LocalFsProvider(root)
    progress.update(phase="VERIFYING", files_done=0, bytes_done=0, current_file=None, force=True)
    expected: set[str] = set()
    for item in files:
        key = str(item["case_relative_path"]).casefold()
        staged = f"{paths['CAE']}/{_relative(str(item['case_relative_path']))}"
        expected.add(staged.casefold())
        progress.record["current_file"] = item["case_relative_path"]
        for attempt in (0, 1):
            if VERIFY_STAGED_CONTENT:
                try:
                    actual = fs.hash_stable(staged, on_progress=progress.add_bytes)
                except (spdm_storage.SpdmStorageError, OSError):
                    actual = (-1, "")
            else:
                info = fs.stat(staged, follow_links=False, missing_ok=True)
                actual = (int(info.size or 0) if info and info.kind == "file" else -1, hashes.get(key, ""))
                progress.add_bytes(max(actual[0], 0))
            if actual == (item["size"], hashes.get(key)):
                break
            if attempt:
                raise CaseFinalizationError("FINALIZATION_OUTPUT_VERIFY_FAILED", f"임시 폴더에 복사한 파일의 해시가 다릅니다: {item['case_relative_path']}. 다시 시도하세요.")
            # Damaged or incomplete staged copy: copy this file once more from its pinned source.
            digest = _stage_file(root, item["source_relative_path"], staged, paths["partial"],
                                 expected_size=int(item["size"]), expected_sha256=item.get("sha256") or hashes.get(key),
                                 expected_modified_ns=item.get("modified_ns") if item.get("source_basis") == "CURRENT_CONFIRMED_SCENE" else None,
                                 on_progress=None, ensured=ensured)
            hashes[key] = digest
            fs.append_bytes(paths["copied"], _copied_line(job, item["case_relative_path"], int(item["size"]), digest), zone=FINAL)
        progress.file_done()
    for directory, dirs, names in fs.walk(paths["CAE"]):
        for name in names:
            if fs.join(directory, name).casefold() not in expected:
                raise CaseFinalizationError("FINALIZATION_STAGING_UNEXPECTED", f"임시 폴더에 계획에 없는 파일이 있습니다: {name}. 관리자에게 문의하세요.")


def _published_matches(root: Path, plan: dict[str, Any], reports: list[dict[str, Any]], category: str,
                       destination: str, hashes: dict[str, str]) -> bool:
    """An existing destination is this operation's own output: exactly the expected files, hash-verified."""
    fs = LocalFsProvider(root)
    try:
        fs.assert_safe(destination)
        if not fs.is_dir(destination):
            return False
        found: set[str] = set()
        for directory, _dirs, names in fs.walk(destination):
            for name in names:
                found.add(fs.join(directory, name)[len(destination) + 1:].casefold())
        if category == "Reports":
            if found != {str(item["file_name"]).casefold() for item in reports}:
                return False
            return all(_hash_path(f"{destination}/{item['file_name']}", root, MAX_REPORT_BYTES[item["format"]]) == (item["sha256"], item["size"])
                       for item in reports)
        if found != {str(item["case_relative_path"]).casefold() for item in plan["files"]}:
            return False
        for item in plan["files"]:
            digest = hashes.get(str(item["case_relative_path"]).casefold())
            if not digest or fs.hash_stable(f"{destination}/{item['case_relative_path']}") != (item["size"], digest):
                return False
        return True
    except (CaseFinalizationError, spdm_storage.SpdmStorageError, OSError):
        return False


def _rename_with_retry(fs: LocalFsProvider, source: str, destination: str) -> None:
    """Folder rename into Final; sharing violations (antivirus, indexer, SMB clients) are retried."""
    for delay in (0.0, *RENAME_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            fs.rename_no_replace(source, destination, zone=FINAL)
            return
        except FileExistsError as exc:
            raise CaseFinalizationError("FINALIZATION_DESTINATION_CONFLICT", "Final 대상 폴더가 이미 있어 덮어쓰지 않았습니다. 기존 자료를 보존했습니다.") from exc
        except PermissionError:
            continue
        except spdm_storage.SpdmStorageError as exc:
            raise CaseFinalizationError(exc.code, str(exc)) from exc
    raise CaseFinalizationError(
        "FINALIZATION_PUBLISH_BUSY",
        "다른 프로그램(백신·탐색기·공유 연결 등)이 임시 폴더를 사용하고 있어 Final 폴더로 옮기지 못했습니다. 잠시 후 다시 시도하세요.")


def _verify_published(root: Path, plan: dict[str, Any], completed_files: list[dict[str, Any]],
                      reports: list[dict[str, Any]], outputs: dict[str, str]) -> None:
    """CAE: every file present with its size (content was verified before the rename); Report: exact and hashed."""
    view = {**plan, "files": completed_files}
    if not _verify_outputs({"root": root}, view, outputs, None, deep=False):
        raise CaseFinalizationError("FINALIZATION_OUTPUT_VERIFY_FAILED", "공개한 Final 파일 확인에 실패했습니다. 다시 시도하세요.")
    entries = _reports_directory_entries(root, outputs["Reports"], str(plan["operation_id"]))
    expected = sorted(str(item["file_name"]).casefold() for item in reports)
    if not all(is_file for _name, is_file in entries) or sorted(name.casefold() for name, _ in entries) != expected:
        raise CaseFinalizationError("FINALIZATION_REPORTS_UNEXPECTED_FILE", "Final/Report 보고서 폴더의 파일이 기록할 보고서와 다릅니다. 기존 자료를 보존하고 관리자에게 문의하세요.")
    for item in reports:
        if _hash_path(f"{outputs['Reports']}/{item['file_name']}", root, MAX_REPORT_BYTES[item["format"]]) != (item["sha256"], item["size"]):
            raise CaseFinalizationError("FINALIZATION_OUTPUT_VERIFY_FAILED", "공개한 보고서 해시 확인에 실패했습니다. 다시 시도하세요.")


def _write_complete(root: Path, plan: dict[str, Any], job: dict[str, Any], completed_files: list[dict[str, Any]],
                    reports: list[dict[str, Any]], outputs: dict[str, str]) -> None:
    fs = LocalFsProvider(root)
    operation_dir = str(plan["metadata_relative_path"])
    complete_path = fs.join(operation_dir, "complete.json")
    completed = _signed_record({
        "schema_version": PLAN_VERSION, "operation_id": plan["operation_id"], "status": "COMPLETE",
        "plan_sha256": _plan_hash(plan), "project_id": plan["project_id"], "request_id": plan["request_id"],
        "environment": plan["environment"], "case_id": plan["case_id"], "capture_id": plan["capture_id"],
        "capture_fingerprint": plan["capture_fingerprint"],
        "folder_schema_snapshot_id": plan["folder_schema_snapshot_id"],
        "output_paths": outputs, "files": completed_files, "reports": reports,
        "counts": plan["counts"], "missing": plan["missing"],
        "created_by": job.get("created_by"), "queued_at": job.get("queued_at"), "confirmed_at": _now(),
    }, "complete_signature", COMPLETE_DOMAIN)
    completed_bytes = _encode(completed)
    if len(completed_bytes) > MAX_PLAN_BYTES:
        raise CaseFinalizationError("FINALIZATION_METADATA_LIMIT", "최종확정 완료 기록이 메타데이터 크기 제한을 초과했습니다.")
    temporary = fs.join(operation_dir, f".complete-{uuid4().hex}.tmp")
    with _pin_directory_chain(root, operation_dir):
        try:
            fs.create_exclusive(temporary, completed_bytes, zone=FINAL)
            if fs.exists(complete_path):
                raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "완료 표식이 동시에 생성되었습니다. 같은 요청을 다시 확인하세요.")
            fs.move_no_overwrite(temporary, complete_path, zone=FINAL)
        except FileExistsError as exc:
            raise CaseFinalizationError("FINALIZATION_MARKER_CONFLICT", "완료 표식이 동시에 생성되었습니다. 같은 요청을 다시 확인하세요.") from exc
        except OSError as exc:
            raise _write_failed() from exc
        finally:
            try:
                fs.remove(temporary, zone=FINAL, missing_ok=True)
            except OSError:
                pass


def _cleanup_after_complete(root: Path, plan: dict[str, Any], operation_dir: str) -> None:
    """Best effort: this operation's staged report uploads and its (now empty) staging folders."""
    fs = LocalFsProvider(root)
    staged = _read_staged_reports(operation_dir, root, plan)
    _remove_staged_reports(root, operation_dir, plan, dict((staged or {}).get("history") or {}))
    paths = _operation_paths(operation_dir)
    try:
        if fs.is_dir(paths["partial"]):
            _clear_partial(root, paths["partial"])
        for folder in (paths["partial"], paths["CAE"], paths["Reports"], paths["staging"]):
            if fs.is_dir(folder) and not fs.is_link(folder) and not fs.list(folder):
                fs.remove(folder, zone=FINAL, directory=True)
    except (OSError, spdm_storage.SpdmStorageError):
        pass


def _complete_view(record: dict[str, Any]) -> dict[str, Any]:
    total = sum(int(item.get("size") or 0) for item in record.get("files") or [])
    return {"operation_id": record["operation_id"], "state": "COMPLETE", "phase": None,
            "files_done": len(record.get("files") or []), "files_total": len(record.get("files") or []),
            "bytes_done": total, "bytes_total": total, "current_file": None, "error": None, "attempt": None,
            "queued_at": record.get("queued_at"), "started_at": None, "updated_at": record.get("confirmed_at"),
            "case_id": record.get("case_id"), "capture_id": record.get("capture_id"),
            "reports": record.get("reports") or [], "output_paths": record.get("output_paths"),
            "active": False, "record": record}


def job_status(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
               case_id: str, operation_id: str) -> dict[str, Any]:
    """Progress of one copy job (job.json/progress.json only); resumes an interrupted job."""
    if not _OPERATION_ID.fullmatch(operation_id):
        raise CaseFinalizationError("FINALIZATION_OPERATION_ID_INVALID", "최종확정 요청 ID가 올바르지 않습니다.")
    base_scope = _scope_for_status(conn, project_id, request_id, environment, case_id)
    root: Path = base_scope["root"]
    _final_relative, metadata_relative = _final_paths(base_scope)
    not_found = CaseFinalizationError("FINALIZATION_JOB_NOT_FOUND", "Final 복사 작업을 찾을 수 없습니다.")
    try:
        operation_dir = result_registration_paths._safe_existing(root, f"{metadata_relative}/{operation_id}")
    except (result_registration_paths.ResultRegistrationError, spdm_storage.SpdmStorageError) as exc:
        raise not_found from exc
    job = _read_job(root, operation_dir)
    if (not job or (job.get("project_id"), job.get("request_id"), job.get("environment"), job.get("case_id")) !=
            (project_id, request_id, str(environment).upper(), case_id)):
        raise not_found
    fs = LocalFsProvider(root)
    if fs.exists(fs.join(operation_dir, "complete.json"), follow_links=False):
        record = _completed_if_valid(conn, base_scope, project_id=project_id, request_id=request_id,
                                     environment=environment, case_id=case_id, capture_id=str(job.get("capture_id")),
                                     operation_id=operation_id, metadata_relative=metadata_relative, deep=False)
        if record:
            return {**_job_view(root, operation_dir, job), **{key: value for key, value in _complete_view(record).items()
                                                              if key in {"state", "phase", "record", "active", "error", "current_file"}}}
        return {**_job_view(root, operation_dir, job), "state": "FAILED", "active": False,
                "error": {"code": "FINALIZATION_COMPLETE_UNVERIFIED", "message": "완료 기록을 확인할 수 없습니다. 기존 자료를 보존하고 관리자에게 문의하세요."}}
    return _resume_if_interrupted(root, operation_dir, _job_view(root, operation_dir, job))


def resume_incomplete_jobs(conn: ConnectionLike) -> list[str]:
    """Startup: queue every recorded job left QUEUED/RUNNING (interrupted) under the configured root.

    Cheap: one DB query for the requests with Case results, then one listing of each
    request's ``Final/.finalizations`` and the small job/progress records. FAILED jobs wait
    for the user's retry.
    """
    targets = _resume_targets(conn)
    return _resume_in(*targets) if targets else []


def _resume_targets(conn: ConnectionLike) -> tuple[Path, list[str]] | None:
    try:
        root, root_id, _root_key = result_registration_paths.storage_context(conn)
    except (result_registration_paths.ResultRegistrationError, spdm_storage.SpdmStorageError, OSError):
        return None
    metadata_dirs: set[str] = set()
    for row in conn.execute("SELECT DISTINCT project_id,request_id,environment FROM dashboard_cases WHERE storage_root_id=?",
                            [root_id]).fetchall():
        try:
            scope_info = result_registration_paths._scope(conn, str(row[0]), str(row[1]), str(row[2]))
            metadata_dirs.add(_final_paths({"scope": scope_info})[1])
        except (CaseFinalizationError, result_registration_paths.ResultRegistrationError, spdm_storage.SpdmStorageError, OSError):
            continue
    return root, sorted(metadata_dirs)


def _resume_in(root: Path, metadata_dirs: list[str]) -> list[str]:
    fs = LocalFsProvider(root)
    submitted: list[str] = []
    for metadata_relative in metadata_dirs:
        try:
            metadata_dir = result_registration_paths._safe_existing(root, metadata_relative)
            if not fs.is_dir(metadata_dir):
                continue
            for entry in fs.list(metadata_dir):
                if entry.kind != "dir" or entry.is_link or not _OPERATION_ID.fullmatch(entry.name):
                    continue
                operation_dir = fs.join(metadata_dir, entry.name)
                if fs.exists(fs.join(operation_dir, "complete.json"), follow_links=False):
                    continue
                job = _read_job(root, operation_dir)
                if not job:
                    continue
                state = (_read_progress(root, operation_dir, job) or {}).get("state") or "QUEUED"
                if state in {"QUEUED", "RUNNING"}:
                    _submit(root, operation_dir)
                    submitted.append(operation_dir)
        except (CaseFinalizationError, result_registration_paths.ResultRegistrationError,
                spdm_storage.SpdmStorageError, OSError):
            continue
    return submitted


def start_resume_scan() -> threading.Thread | None:
    """Run :func:`resume_incomplete_jobs` once in a daemon thread (app startup stays non-blocking)."""
    if os.environ.get("SIMDASH_FINALIZATION_RESUME_SCAN", "1").strip() == "0":
        return None

    def scan() -> None:
        try:
            from ..database_connection import connect
            with connect() as conn:
                targets = _resume_targets(conn)
            resumed = _resume_in(*targets) if targets else []
            if resumed:
                _LOG.info("Resumed %d interrupted Final copy job(s)", len(resumed))
        except Exception:  # noqa: BLE001 - startup must never fail because of this scan
            _LOG.warning("Final copy resume scan failed", exc_info=True)

    thread = threading.Thread(target=scan, name="final-copy-resume-scan", daemon=True)
    thread.start()
    return thread


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
        "include_unchecked": plan.get("include_unchecked") or [],
        "created_by": completed.get("created_by"), "queued_at": completed.get("queued_at"),
        "confirmed_at": completed["confirmed_at"],
    }


def _status_operation(conn: ConnectionLike, scope: dict[str, Any], project_id: str,
                     request_id: str, environment: str, operation_dir: str,
                     metadata_budget: list[int],
                     ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool, dict[str, Any] | None]:
    """Read one signed operation without letting a malformed sibling hide valid history.

    Outputs are checked for existence and recorded size here; the hash check of the
    records status actually shows is done by ``status`` (``deep`` argument tuple).
    """
    root: Path = scope["root"]
    fs = LocalFsProvider(root)
    fs.assert_safe(operation_dir)
    operation_id = PurePosixPath(operation_dir).name
    plan_path = fs.join(operation_dir, "plan.json")
    if not fs.exists(plan_path, follow_links=False):
        return None, None, False, None
    fs.assert_safe(plan_path)
    plan = _read_json(root, plan_path, status_metadata_budget=metadata_budget, max_bytes=MAX_PLAN_BYTES)
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
    # Version 1 (before 2026-10-03) mirrored results/Scene reports into Final/Reports (not recognized since §15 D21).
    categories = {"CAE", "Reports"} if version == 1 else {"CAE"}
    bases = ({"SELECTED_CAPTURE", "CURRENT_CONFIRMED_SCENE"} if version == 1
             else {"SOURCE_CAPTURE", "CURRENT_CONFIRMED_SCENE"})
    files = plan.get("files")
    legacy = version in {1, 2}
    if (not isinstance(files, list) or (legacy and len(files) > MAX_LEGACY_FILES) or any(
            not isinstance(file, dict) or file.get("category") not in categories
            or file.get("source_basis") not in bases
            # Version 3 pins non-capture files by size/mtime; their hash is in complete.json.
            or not (re.fullmatch(r"[0-9a-f]{64}", str(file.get("sha256") or ""))
                    or (version == PLAN_VERSION and file.get("sha256") is None
                        and file.get("source_basis") == "CURRENT_CONFIRMED_SCENE"))
            or type(file.get("size")) is not int or file["size"] < 0
            or (legacy and file["size"] > MAX_LEGACY_FILE_BYTES)
            for file in files)):
        return None, None, True, None
    if legacy and sum(file["size"] for file in files) > MAX_LEGACY_TOTAL_BYTES:
        return None, None, True, None
    try:
        expected_outputs = _expected_output_paths(plan)
    except (CaseFinalizationError, TypeError, ValueError):
        return None, None, True, None
    complete_path = fs.join(operation_dir, "complete.json")
    if not fs.exists(complete_path, follow_links=False):
        return plan, None, False, None
    fs.assert_safe(complete_path)
    completed = _read_json(root, complete_path, status_metadata_budget=metadata_budget, max_bytes=MAX_PLAN_BYTES)
    reports = completed.get("reports") if completed else None
    layout = (_recorded_outputs(plan, completed.get("output_paths"), _effective_files(plan, completed), reports)
              if completed else None)
    if (not completed or not _verify_signed_record(completed, "complete_signature", COMPLETE_DOMAIN)
            or completed.get("status") != "COMPLETE" or completed.get("operation_id") != operation_id
            or completed.get("project_id") != project_id or completed.get("request_id") != request_id
            or completed.get("environment") != str(environment).upper() or completed.get("case_id") != case_id
            or completed.get("capture_id") != plan.get("capture_id") or not _files_match(plan, completed.get("files"))
            or layout is None
            or completed.get("plan_sha256") != _plan_hash(plan)
            or not _valid_report_records(plan, reports)):
        return None, None, True, None
    view, verify_paths, verify_reports, shown_paths = layout
    output_bytes = sum(file["size"] for file in view["files"]) + sum(item["size"] for item in verify_reports or [])
    if not _verify_outputs(scope, view, verify_paths, verify_reports, deep=False):
        return None, None, True, None
    deep = {"plan": view, "expected_outputs": verify_paths, "reports": verify_reports, "output_bytes": output_bytes}
    response = _completed_response(view, {**completed, "output_paths": shown_paths, "reports": verify_reports})
    return plan, response, False, deep


def _scan_operations(conn: ConnectionLike, scope: dict[str, Any], project_id: str, request_id: str,
                     environment: str, case_id: str | None,
                     ) -> tuple[list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]], list[dict[str, Any]], int]:
    """Read every operation folder (signatures, scope, output existence/size); no output hashing."""
    _final_relative, metadata_relative = _final_paths(scope)
    metadata_dir = result_registration_paths._safe_existing(scope["root"], metadata_relative, allow_missing_leaf=True)
    incomplete: list[dict[str, Any]] = []
    candidates: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    unverified_count = 0
    metadata_budget = [0]
    fs = LocalFsProvider(scope["root"])
    if fs.is_dir(metadata_dir):
        inspected_items = 0
        for entry in fs.list(metadata_dir):
            item = fs.join(metadata_dir, entry.name)
            inspected_items += 1
            if inspected_items > MAX_STATUS_ITEMS:
                raise CaseFinalizationError(
                    "FINALIZATION_STATUS_LIMIT",
                    f"최종확정 상태 조회 항목이 {MAX_STATUS_ITEMS}개 제한을 초과했습니다. 이력을 안전하게 판정할 수 없습니다.",
                )
            if not fs.is_dir(item) or not _OPERATION_ID.fullmatch(entry.name):
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
            elif plan and plan.get("case_id") == case_id and plan.get("schema_version") in CONFIRMABLE_PLAN_VERSIONS:
                # Unfinished previews of earlier plan versions cannot be confirmed any more; a new preview replaces them.
                job = _read_job(scope["root"], item)
                view = _job_view(scope["root"], item, job) if job and job.get("plan_sha256") == _plan_hash(plan) else None
                if view is not None:
                    view = _resume_if_interrupted(scope["root"], item, view)
                incomplete.append({"operation_id": plan["operation_id"], "status": "RETRYABLE",
                                   "capture_id": plan.get("capture_id"), "previewed_at": plan.get("previewed_at"),
                                   "job": view})
    candidates.sort(key=lambda row: str(row[1].get("confirmed_at", "")), reverse=True)
    return candidates, incomplete, unverified_count


def status(conn: ConnectionLike, *, project_id: str, request_id: str, environment: str,
           case_id: str) -> dict[str, Any]:
    """Request-wide and selected-Case latest completed records.

    Every completed record is checked for signatures, scope and output existence/size.
    Only the records status returns (request latest, selected Case latest) are hash
    verified; a record that fails is counted as unverified and the next newer-to-older
    candidate is tried, so damaged outputs are never shown as normal while large report
    history cannot exhaust the per-call verification budget. W2: a shown record larger
    than the remaining hash budget (multi-GB CAE) is shown verified by existence and size
    only (``verification: "SIZE"``) instead of failing the whole status call.
    ``active_operations`` lists the selected Case's copy jobs (QUEUED/RUNNING/FAILED);
    interrupted jobs are resumed while the status is read.
    """
    scope = _scope_for_status(conn, project_id, request_id, environment, case_id)
    final_relative, _metadata_relative = _final_paths(scope)
    candidates, incomplete, unverified_count = _scan_operations(conn, scope, project_id, request_id,
                                                                environment, case_id)
    verified: dict[str, bool] = {}
    size_only: set[str] = set()
    verification_budget = [0]

    def deep_ok(plan: dict[str, Any], deep: dict[str, Any]) -> bool:
        nonlocal unverified_count
        operation_id = str(plan["operation_id"])
        if operation_id not in verified:
            if verification_budget[0] + deep["output_bytes"] > MAX_STATUS_VERIFY_BYTES:
                # Existence and recorded sizes were already checked for every record.
                size_only.add(operation_id)
                verified[operation_id] = True
                return True
            verification_budget[0] += deep["output_bytes"]
            try:
                verified[operation_id] = _verify_outputs(scope, deep["plan"], deep["expected_outputs"], deep["reports"])
            except (CaseFinalizationError, result_registration_paths.ResultRegistrationError,
                    spdm_storage.SpdmStorageError, OSError, TypeError, ValueError, KeyError):
                verified[operation_id] = False
            if not verified[operation_id]:
                unverified_count += 1
        return verified[operation_id]

    latest = next((record for plan, record, deep in candidates if deep_ok(plan, deep)), None)
    selected_case_latest = next((record for plan, record, deep in candidates
                                 if plan.get("case_id") == case_id and deep_ok(plan, deep)), None)

    def shown(record: dict[str, Any] | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {**record, "verification": "SIZE" if record["operation_id"] in size_only else "SHA256"}

    retryable = sorted(incomplete, key=lambda row: str(row["previewed_at"] or ""), reverse=True)
    active = sorted((row["job"] for row in retryable if row.get("job") and row["job"]["state"] in {"QUEUED", "RUNNING", "FAILED"}),
                    key=lambda job: str(job.get("queued_at") or ""), reverse=True)
    return {"case_id": case_id, "request_id": request_id, "environment": str(environment).upper(),
            "final_relative_path": final_relative, "latest": shown(latest), "selected_case_latest": shown(selected_case_latest),
            "retryable_operations": retryable, "active_operations": active,
            "unverified_records": unverified_count}


def latest_completed(conn: ConnectionLike, *, project_id: str, request_id: str,
                     environment: str) -> tuple[dict[str, Any] | None, list[str] | None]:
    """Request-wide newest completed Final record and the file names in its ``Final/Report`` folder.

    The names are ``None`` when that folder cannot be read; the record still stands. A legacy
    ``Final/Reports`` folder is never read (§15 D21), so such a record has no reports.

    Same signed-record, scope and output existence/size checks as ``status`` but no
    content hashing (folder-request-progress.md P5: names and sizes only, no writes).
    """
    try:
        scope_info = result_registration_paths._scope(conn, project_id, request_id, environment)
        root, root_id, root_key = result_registration_paths.storage_context(conn)
    except result_registration_paths.ResultRegistrationError as exc:
        raise CaseFinalizationError(exc.code, str(exc)) from exc
    scope = {"root": root, "root_id": root_id, "root_key": root_key, "scope": scope_info,
             "case_id": None, "case_path": "", "case_label": "", "capture_id": ""}
    candidates, _incomplete, _unverified = _scan_operations(conn, scope, project_id, request_id,
                                                            environment, None)
    if not candidates:
        return None, []
    plan, record, _deep = candidates[0]
    try:
        entries = _reports_directory_entries(root, _expected_output_paths(plan)["Reports"], str(plan["operation_id"]))
    except (CaseFinalizationError, OSError):
        return record, None
    return record, [name for name, is_file in entries if is_file]


def _scope_for_status(conn: ConnectionLike, project_id: str, request_id: str,
                      environment: str, case_id: str) -> dict[str, Any]:
    try:
        scope_info = result_registration_paths._scope(conn, project_id, request_id, environment)
        root, root_id, root_key = result_registration_paths.storage_context(conn)
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
