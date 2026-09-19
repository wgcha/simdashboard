from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from ..database_connection import ConnectionLike, rows
from ..domains.dashboard.parser import _pick_usage, build_distribution_scene, fingerprint, parse_scene_name
from . import spdm_storage


MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
_USAGE_DIRS = {"settle", "wobble", "horizontal_force_angle", "slope_angle", "slope_angle_360"}
_DISTRIBUTION_DIRS = {"drop", "clamping"}


class DashboardCaptureError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\\" in value or "\x00" in value:
        raise DashboardCaptureError("DASHBOARD_PATH_INVALID", "대시보드 상대 경로가 올바르지 않습니다.")
    path = PurePosixPath(value.strip())
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} or not spdm_storage._valid_windows_name(part) for part in path.parts):
        raise DashboardCaptureError("DASHBOARD_PATH_INVALID", "대시보드 상대 경로가 올바르지 않습니다.")
    return path.as_posix()


def _root(conn: ConnectionLike) -> Path:
    storage = spdm_storage.storage_root(conn)
    if storage.root is None:
        raise DashboardCaptureError("DASHBOARD_ROOT_UNSET", "저장소 루트가 설정되지 않았습니다.")
    return storage.root


def _root_id(root: Path) -> str:
    return "dashboard-root-" + hashlib.sha256(spdm_storage._root_identity(root).encode()).hexdigest()


def _safe_target(root: Path, relative: str) -> Path:
    lexical = root.joinpath(*PurePosixPath(relative).parts)
    try:
        spdm_storage._assert_safe_existing(lexical, root)  # type: ignore[attr-defined]
    except Exception as exc:
        if isinstance(exc, spdm_storage.SpdmStorageError):
            raise DashboardCaptureError(exc.code, str(exc)) from exc
        raise
    try:
        target = lexical.resolve(strict=True)
    except FileNotFoundError as exc:
        raise DashboardCaptureError("DASHBOARD_SOURCE_MISSING", "원본 폴더를 찾을 수 없습니다.") from exc
    except OSError as exc:
        raise DashboardCaptureError("DASHBOARD_SOURCE_READ_ERROR", "원본 폴더를 읽을 수 없습니다.") from exc
    try:
        target.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise DashboardCaptureError("DASHBOARD_PATH_ESCAPE", "허용된 저장소 밖의 경로입니다.") from exc
    # Existing SPDM safety helper rejects symlinks/reparse points in every
    # ancestor. It is deliberately called only after relative validation.
    try:
        spdm_storage._assert_safe_existing(target, root)  # type: ignore[attr-defined]
    except Exception as exc:
        if isinstance(exc, spdm_storage.SpdmStorageError):
            raise DashboardCaptureError(exc.code, str(exc)) from exc
        raise
    return target


def _validate_case_root(root: Path, relative: str, environment: str) -> Path:
    target = _safe_target(root, relative)
    if not target.is_dir():
        raise DashboardCaptureError("DASHBOARD_CASE_INVALID", "capture root는 Simulation Case 폴더여야 합니다.")
    child_names = {item.name.casefold() for item in target.iterdir() if item.is_dir()}
    expected = _USAGE_DIRS if environment == "USAGE" else _DISTRIBUTION_DIRS
    if not child_names.intersection(expected):
        raise DashboardCaptureError("DASHBOARD_CASE_INVALID", "지정한 경로에서 해당 환경의 Simulation Case를 확인할 수 없습니다.")
    return target


def discover_cases(conn: ConnectionLike, relative_path: str = "", environment: str = "USAGE") -> dict[str, Any]:
    """Read-only discovery of Altair One Simulation Case directories."""
    if environment not in {"USAGE", "DISTRIBUTION"}:
        raise DashboardCaptureError("DASHBOARD_ENVIRONMENT_INVALID", "지원하지 않는 대시보드 환경입니다.")
    root = _root(conn)
    root_id = _root_id(root)
    base_relative = _relative(relative_path) if relative_path else ""
    base = _safe_target(root, base_relative) if base_relative else root
    if not base.is_dir():
        raise DashboardCaptureError("DASHBOARD_SOURCE_MISSING", "조사 경로를 찾을 수 없습니다.")
    expected = _USAGE_DIRS if environment == "USAGE" else _DISTRIBUTION_DIRS
    cases: list[dict[str, Any]] = []
    queue = deque([(base, 0)])
    visited = 0
    issues = set()
    excluded = {"cad", "report", "final", "validation", "library"}
    while queue:
        current, depth = queue.popleft()
        try:
            children = []
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 20000:
                        issues.add("SCAN_LIMIT_REACHED")
                        break
                    if not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False):
                        children.append(Path(entry.path))
        except OSError:
            issues.add("DIRECTORY_UNAVAILABLE")
            continue
        if visited > 20000:
            break
        names = {item.name.casefold() for item in children}
        if names.intersection(expected):
            rel = current.relative_to(root).as_posix()
            if not base_relative:
                parent_parts = current.relative_to(root).parts
                request_types = [re.match(r"^WR_[A-Za-z0-9._-]+_SimType([12])$", part, re.IGNORECASE) for part in parent_parts]
                expected_type = "1" if environment == "USAGE" else "2"
                if not any(match and match.group(1) == expected_type for match in request_types):
                    continue
            cases.append({"root_relative_path": rel, "source_name": current.name, "environment": environment, "storage_root_id": root_id})
            continue
        if depth >= 8:
            if children:
                issues.add("SCAN_DEPTH_LIMIT")
            continue
        for child in sorted(children, key=lambda item: item.name.casefold()):
            if child.name.casefold() in excluded:
                continue
            try:
                spdm_storage._assert_safe_existing(child, root)  # type: ignore[attr-defined]
            except spdm_storage.SpdmStorageError:
                issues.add("UNSAFE_DIRECTORY_SKIPPED")
                continue
            queue.append((child, depth + 1))
    cases.sort(key=lambda item: item["root_relative_path"].casefold())
    return {"contract_version": 1, "storage_root_id": root_id, "environment": environment, "cases": cases, "issues": sorted(issues)}


def _safe_issue_path(root: Path, path: Path) -> str:
    """Return a bounded, root-relative path suitable for a quality issue.

    Quality issues are persisted and returned to users, so they must never
    contain the configured storage root or control characters from a source
    filename.  The walk has already confined ``path`` below ``root``; this
    helper only makes that safe relative representation explicit.
    """
    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        value = path.name
    return _safe_issue_relative(value)


def _safe_issue_relative(value: str) -> str:
    value = "".join(char if ord(char) >= 0x20 and char != "\x7f" else "?" for char in str(value))
    return value[:512]


def _unprocessed_issue(root: Path, path: Path, reason: str) -> str:
    return f"UNPROCESSED_FILE:{_safe_issue_path(root, path)}:{reason}"


def _walk(
    root: Path,
    relative: str,
    *,
    issues: list[str] | None = None,
) -> list[tuple[str, bytes, str]]:
    base = _safe_target(root, relative)
    excluded = {"cad", "report", "reports", "final", "validation", "library"}
    allowed = {".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm"}
    items, stack = [], [(base, 0)]
    visited = 0
    try:
        while stack:
            directory, depth = stack.pop()
            if depth > 16:
                raise DashboardCaptureError("DASHBOARD_DEPTH_LIMIT", "결과 폴더 깊이 제한을 초과했습니다.")
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 20000:
                        raise DashboardCaptureError("DASHBOARD_SCAN_LIMIT", "결과 파일 조사 범위를 초과했습니다.")
                    path = Path(entry.path)
                    if entry.name.startswith("."):
                        continue
                    if entry.name.casefold() in excluded:
                        if issues is not None:
                            issues.append(_unprocessed_issue(root, path, "EXCLUDED_DIRECTORY"))
                        continue
                    spdm_storage._assert_safe_existing(path, root)
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((path, depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        if path.suffix.casefold() in allowed:
                            items.append(path)
                            if len(items) > 10000:
                                raise DashboardCaptureError("DASHBOARD_CAPTURE_TOO_LARGE", "결과 파일 수 제한을 초과했습니다.")
                        elif issues is not None:
                            issues.append(_unprocessed_issue(root, path, "UNSUPPORTED_EXTENSION"))
        result, total, signatures = [], 0, []
        for item in sorted(items, key=lambda path: path.as_posix()):
            spdm_storage._assert_safe_existing(item, root)
            if item.stat().st_size > MAX_ASSET_BYTES:
                raise DashboardCaptureError("DASHBOARD_ASSET_TOO_LARGE", "원본 자산은 32 MiB 이하여야 합니다.")
            data, _ = spdm_storage.read_stable_bytes(item, max_bytes=min(MAX_ASSET_BYTES, MAX_TOTAL_BYTES - total))
            spdm_storage._assert_safe_existing(item, root)
            signature = item.stat()
            signatures.append((item, signature.st_size, signature.st_mtime_ns, signature.st_ino))
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise DashboardCaptureError("DASHBOARD_CAPTURE_TOO_LARGE", "수집 버전은 256 MiB 이하여야 합니다.")
            path = item.relative_to(root).as_posix()
            media_type = mimetypes.guess_type(item.name)[0] or "application/octet-stream"
            result.append((path, data, media_type))
        for item, size, modified, inode in signatures:
            current = item.stat()
            if (current.st_size, current.st_mtime_ns, current.st_ino) != (size, modified, inode):
                raise DashboardCaptureError("DASHBOARD_SOURCE_CHANGED", "수집 도중 원본이 변경되었습니다. 다시 수집하세요.")
        return result
    except spdm_storage.SpdmStorageError as exc:
        raise DashboardCaptureError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise DashboardCaptureError("DASHBOARD_SOURCE_READ_ERROR", "결과 원본을 안정적으로 읽을 수 없습니다.") from exc


def _case_id(storage_root_id: str, relative_path: str) -> str:
    return "dashboard-case-" + hashlib.sha256(f"{storage_root_id}:{relative_path}".encode()).hexdigest()[:24]


def _capture_id(case_id: str, digest: str) -> str:
    return "dashboard-capture-" + hashlib.sha256(f"{case_id}:{digest}".encode()).hexdigest()[:24]


def _verify_context(conn: ConnectionLike, project_id: str, request_id: str) -> None:
    row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    if not row or str(row[0]) != project_id:
        raise DashboardCaptureError("DASHBOARD_CONTEXT_INVALID", "의뢰와 프로젝트 문맥이 일치하지 않습니다.")


def create_capture(conn: ConnectionLike, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
    _verify_context(conn, str(payload["project_id"]), str(payload["request_id"]))
    relative = _relative(str(payload["root_relative_path"]))
    root = _root(conn)
    if storage_root_id := str(payload.get("storage_root_id") or ""):
        if storage_root_id != _root_id(root):
            raise DashboardCaptureError("DASHBOARD_ROOT_ID_INVALID", "설정된 저장소와 storage_root_id가 일치하지 않습니다.")
    _validate_case_root(root, relative, str(payload["environment"]))
    walk_issues: list[str] = []
    files = _walk(root, relative, issues=walk_issues)
    manifest = [{"relative_path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "media_type": media_type} for path, data, media_type in files]
    recipe_version = "dashboard-v2"
    fingerprint_context = {key: payload.get(key) for key in ("project_id", "request_id", "simulation_case_id", "load_case_id", "execution_run_id", "mode", "component_id")}
    if walk_issues:
        fingerprint_context["quality_issues"] = sorted(set(walk_issues))
    digest = fingerprint({"files": manifest, "context": fingerprint_context}, recipe_version)
    storage_root_id = str(payload["storage_root_id"])
    case_id = _case_id(storage_root_id, relative)
    existing_case = conn.execute("SELECT id,project_id,request_id,environment,source_name,metadata_json FROM dashboard_cases WHERE id=?", [case_id]).fetchone()
    if existing_case and (str(existing_case[1]) != str(payload["project_id"]) or str(existing_case[2]) != str(payload["request_id"]) or str(existing_case[3]) != str(payload["environment"])):
        raise DashboardCaptureError("DASHBOARD_CASE_CONFLICT", "저장소 경로가 다른 업무 문맥에 연결되어 있습니다.")
    if not existing_case:
        conn.execute("INSERT INTO dashboard_cases(id,project_id,request_id,storage_root_id,relative_path,environment,source_name,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)", [case_id, payload["project_id"], payload["request_id"], storage_root_id, relative, payload["environment"], PurePosixPath(relative).name, _json({"source_name": PurePosixPath(relative).name}), _now()])
    for prior in conn.execute("SELECT payload_json FROM dashboard_captures WHERE case_id=? ORDER BY created_at LIMIT 1", [case_id]).fetchall():
        prior_context = _decode(prior[0]).get("context", {})
        if prior_context.get("simulation_case_id") and payload.get("simulation_case_id") and str(prior_context["simulation_case_id"]) != str(payload["simulation_case_id"]):
            raise DashboardCaptureError("DASHBOARD_CONTEXT_CONFLICT", "같은 Simulation Case 경로를 다른 문맥으로 재사용할 수 없습니다.")
    existing = conn.execute("SELECT id,payload_json FROM dashboard_captures WHERE case_id=? AND fingerprint=?", [case_id, digest]).fetchone()
    if existing:
        old_payload = _decode(existing[1])
        return {"id": str(existing[0]), "case_id": case_id, "fingerprint": digest, "status": old_payload.get("status", "READY"), "context": old_payload.get("context", payload), "payload": old_payload}

    capture_id = _capture_id(case_id, digest)
    context = {key: payload.get(key) for key in ("project_id", "request_id", "simulation_case_id", "load_case_id", "execution_run_id", "run_display_name", "mode", "capture_id", "component_id")}
    context["capture_id"] = capture_id
    if payload["environment"] == "USAGE":
        grouped: dict[str, list[tuple[str, bytes]]] = {}
        for path, data, _ in files:
            grouped.setdefault(PurePosixPath(path).parts[0] if PurePosixPath(path).parts else "", []).append((path, data))
        parsed = {"environment": "USAGE", "context": context, **_usage_payload(grouped)}
        if walk_issues:
            parsed["quality_issues"] = sorted(set(walk_issues))
    else:
        parsed = {"environment": "DISTRIBUTION", "context": context, **_distribution_payload(
            relative, files, context, storage_root_id, scan_issues=walk_issues
        )}
    if payload["environment"] == "USAGE":
        from .dashboard_queries import usage
        status = usage({"id": capture_id, "case_id": case_id, "environment": "USAGE", "payload": parsed}, case_id)["status"]
        if any(item.get("status") == "SOURCE_PARSE_ERROR" for item in parsed["evaluations"]):
            status = "SOURCE_PARSE_ERROR"
    else:
        status = "PARTIAL"  # Completeness is evaluated per selected basis/edge scope at query time.
        if any("SOURCE_PARSE_ERROR" in item.get("quality_issues", []) for item in parsed["scenes"]):
            status = "SOURCE_PARSE_ERROR"
    payload_json = {**parsed, "capture_id": capture_id, "status": status, "contract_version": 1}
    media_asset_ids: list[tuple[str, str, bytes, str, dict[str, Any]]] = []
    for path, data, media_type in files:
        asset_id = "dashboard-asset-" + hashlib.sha256(f"{capture_id}:{path}".encode()).hexdigest()[:24]
        metadata = next(item for item in manifest if item["relative_path"] == path)
        media_asset_ids.append((asset_id, path, data, media_type, metadata))
        _replace_asset_id(payload_json, path, asset_id)
    if payload["environment"] == "DISTRIBUTION":
        # Persist each observation once. Derived maxima depend on the caller's
        # chosen basis/scope and are computed by the query service.
        for run in payload_json["runs"]:
            for scene in run["scenes"]:
                for key in ("component_results", "peak", "edge_peaks"):
                    scene.pop(key, None)
        payload_json.pop("scenes", None)
        payload_json.pop("run", None)
    conn.execute("INSERT INTO dashboard_captures(id,case_id,fingerprint,recipe_version,manifest_json,payload_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", [capture_id, case_id, digest, recipe_version, _json(manifest), _json(payload_json), actor, _now()])
    for asset_id, path, data, media_type, metadata in media_asset_ids:
        conn.execute("INSERT INTO dashboard_assets(id,capture_id,relative_path,sha256,media_type,content,metadata_json) VALUES(?,?,?,?,?,?,?)", [asset_id, capture_id, path, metadata["sha256"], media_type, data, _json({"asset_id": asset_id, "relative_path": path})])
    return {"id": capture_id, "case_id": case_id, "fingerprint": digest, "status": status, "context": context, "payload": payload_json}


def _replace_asset_id(payload: dict[str, Any], path: str, asset_id: str) -> None:
    for scene in payload.get("scenes", []):
        for media in scene.get("media", []):
            if media.get("relative_path") == path:
                media["asset_id"] = asset_id
    for scene in payload.get("scenes", []):
        for observation in scene.get("observations", []):
            if observation.get("source_path") == path:
                observation["source_ref"] = {"asset_id": asset_id, "row": observation.get("row"), "column": observation.get("column")}
    for evaluation in payload.get("evaluations", []):
        for media in evaluation.get("media", []):
            if media.get("relative_path") == path:
                media["asset_id"] = asset_id
        if evaluation.get("source") == path:
            evaluation["source_ref"] = {"asset_id": asset_id}
def _usage_identity(path: str, evaluation: str):
    stem = PurePosixPath(path).stem.removesuffix("_result")
    patterns = {
        "Settle": r"^(?P<condition>.+)_settle$",
        "Wobble": r"^(?P<condition>.+)_wobble_center_(?P<direction>front|back)$",
        "Horizontal_Force_Angle": r"^(?P<condition>.+)_horizontal_force_angle_(?P<direction>front|back)$",
        "Slope_Angle": r"^(?P<condition>.+)_slope_angle_(?P<direction>front|back)$",
        "Slope_Angle_360": r"^(?P<condition>.+)_slope_angle_(?P<direction>front|back)_360$",
    }
    match = re.fullmatch(patterns[evaluation], stem, re.I)
    if not match:
        return None
    return match.groupdict().get("direction", "common").lower(), match.group("condition")


def _usage_payload(grouped: dict[str, list[tuple[str, bytes]]]) -> dict[str, Any]:
    files = [(path, data) for entries in grouped.values() for path, data in entries]
    picked = _pick_usage([(path, data) for path, data in files if PurePosixPath(path).suffix.casefold() in {".json", ".csv"}])
    result = []
    used_media = set()
    for item in picked["evaluations"]:
        evaluation, source = item["evaluation"], str(item.get("source") or "")
        identity = _usage_identity(source, evaluation)
        item["direction"], item["condition"] = identity or ("common", None)
        item["media"] = []
        for path, _ in files:
            source_path, media_path = PurePosixPath(source), PurePosixPath(path)
            if media_path.suffix.casefold() not in {".mp4", ".webm", ".jpg", ".jpeg", ".png"}:
                continue
            if media_path.parent == source_path.parent and media_path.stem == source_path.stem.removesuffix("_result"):
                used_media.add(path)
                item["media"].append({"relative_path": path, "title": evaluation + " · " + item["direction"],
                    "kind": "VIDEO" if media_path.suffix.casefold() in {".mp4", ".webm"} else "IMAGE", "status": "READY", "frame_role": "UNKNOWN"})
        if len(item["media"]) > 1:
            item["media"] = [{**media, "status": "AMBIGUOUS"} for media in item["media"]]
        result.append(item)
    # Media-only evaluations stay visible; missing numbers are never filled
    # from a different source or capture.
    for path, _ in files:
        media_path = PurePosixPath(path)
        if path in used_media or media_path.suffix.casefold() not in {".mp4", ".webm", ".jpg", ".jpeg", ".png"}:
            continue
        evaluation = next((name for name in picked["evaluation_names"] if name in media_path.parts), None)
        identity = _usage_identity(path, evaluation) if evaluation else None
        if identity:
            direction, condition = identity
            result.append({"evaluation": evaluation, "direction": direction, "condition": condition, "status": "MISSING", "values": {},
                "media": [{"relative_path": path, "title": evaluation + " · " + direction,
                    "kind": "VIDEO" if media_path.suffix.casefold() in {".mp4", ".webm"} else "IMAGE", "status": "READY", "frame_role": "UNKNOWN"}]})
    return {"evaluation_names": picked["evaluation_names"], "evaluations": result}


def _distribution_payload(
    relative: str,
    files: list[tuple[str, bytes, str]],
    context: dict[str, Any] | None = None,
    storage_root_id: str = "",
    *,
    scan_issues: list[str] | None = None,
) -> dict[str, Any]:
    base = PurePosixPath(relative)
    context = context or {}
    scene_files: dict[tuple[str, str, str], list[tuple[str, bytes]]] = {}
    run_meta: dict[tuple[str, str], dict[str, Any]] = {}
    quality_issues = set(scan_issues or ())
    for path, data, _ in files:
        try:
            tail = PurePosixPath(path).relative_to(base).parts
        except ValueError:
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:OUTSIDE_CAPTURE_ROOT")
            continue
        directories = list(tail[:-1])
        # Production capture roots are Cases. The pure helper also accepts a
        # Run root for isolated parser fixtures; it never changes persisted Case identity.
        if directories and directories[0].casefold() in {"drop", "clamping"}:
            if len(directories) < 3:
                quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:INCOMPLETE_RESULT_PATH")
                continue
            load_case, run_name = directories[:2]
            remaining = directories[2:]
        elif context.get("simulation_case_id"):
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:UNKNOWN_LOAD_CASE_DIRECTORY")
            continue
        else:
            load_case, run_name = base.parent.name, base.name
            remaining = directories
        if remaining and remaining[0].casefold() in {"individual", "cumulative"}:
            mode = remaining[0].upper()
            remaining = remaining[1:]
        else:
            mode = "UNKNOWN"
        if len(remaining) != 1:
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:UNEXPECTED_RESULT_PATH_DEPTH")
            continue
        scene = remaining[0]
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix == ".json":
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:UNSUPPORTED_DISTRIBUTION_FORMAT")
        run_identity = f"{storage_root_id}:{relative}:{load_case}/{run_name}"
        run_id = "run-" + hashlib.sha256(run_identity.encode()).hexdigest()[:24]
        load_id = "load-" + hashlib.sha256(f"{storage_root_id}:{relative}:{load_case}".encode()).hexdigest()[:24]
        run_meta.setdefault((run_id, mode), {"id": run_id, "source_name": run_name, "load_case_id": load_id,
            "load_case_name": load_case, "mode": mode, "modes": [mode]})
        scene_files.setdefault((run_id, mode, scene), []).append((path, data))
    scenes = []
    runs: dict[tuple[str, str], list[dict[str, Any]]] = {key: [] for key in run_meta}
    for (run_id, mode, scene), grouped in sorted(scene_files.items(), key=lambda item: (parse_scene_name(item[0][2]).get("scene_sequence_number") is None, parse_scene_name(item[0][2]).get("scene_sequence_number") or 0, item[0][2].casefold())):
        ignored_sources: list[str] = []
        item = build_distribution_scene(scene, grouped, ignored_sources)
        quality_issues.update(
            f"UNPROCESSED_FILE:{_safe_issue_relative(source)}:UNRECOGNIZED_RESULT_FILE"
            for source in ignored_sources
        )
        item["id"] = "scene-" + hashlib.sha256(f"{run_id}|{mode}|{scene}".encode()).hexdigest()[:24]
        item["label"] = scene
        item["run_id"] = run_id
        item["mode"] = mode
        scenes.append(item)
        runs[(run_id, mode)].append(item)
    run_rows = [{**meta, "scenes": runs[key]} for key, meta in run_meta.items()]
    return {"run": run_rows[0] if len(run_rows) == 1 else {"source_name": base.name}, "runs": run_rows,
            "scenes": scenes, "quality_issues": sorted(quality_issues)}


def get_capture(conn: ConnectionLike, capture_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT c.id,c.case_id,c.fingerprint,c.payload_json,dc.project_id,dc.request_id,dc.environment,dc.source_name FROM dashboard_captures c JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.id=?", [capture_id]).fetchone()
    if not row:
        return None
    payload = _decode(row[3])
    payload.setdefault("context", {}).update({"project_id": row[4], "request_id": row[5], "capture_id": capture_id})
    return {"id": row[0], "case_id": row[1], "fingerprint": row[2], "environment": row[6], "source_name": row[7], "payload": payload}


def find_asset(conn: ConnectionLike, asset_id: str) -> dict[str, Any] | None:
    result = rows(conn.execute("SELECT a.id,a.capture_id,a.relative_path,a.sha256,a.media_type,a.content,a.metadata_json,c.case_id,dc.project_id,dc.request_id FROM dashboard_assets a JOIN dashboard_captures c ON c.id=a.capture_id JOIN dashboard_cases dc ON dc.id=c.case_id WHERE a.id=?", [asset_id]))
    if not result:
        return None
    item = result[0]
    item["metadata"] = _decode(item.pop("metadata_json"))
    return item
