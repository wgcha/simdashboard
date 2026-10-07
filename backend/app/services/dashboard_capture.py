from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from ..database_connection import ConnectionLike, rows
from ..domains.dashboard.parser import EVALUATIONS, _pick_usage, build_distribution_scene, fingerprint, parse_scene_name
from . import spdm_storage
from . import usage_source_review
from .storage import get_storage_provider, provider_for_root
from .storage.local import LocalFsProvider


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


def _provider(conn: ConnectionLike) -> LocalFsProvider:
    return get_storage_provider(conn, on_unset=lambda: DashboardCaptureError("DASHBOARD_ROOT_UNSET", "저장소 루트가 설정되지 않았습니다."))


def _root_id(root: Path) -> str:
    return "dashboard-root-" + hashlib.sha256(provider_for_root(root).root_identity().encode()).hexdigest()


def _safe_target(root: Path, relative: str) -> str:
    """Root-relative, strictly resolved Case path (no reparse ancestor, confined to the root)."""
    fs = provider_for_root(root)
    lexical = "/".join(PurePosixPath(relative).parts)
    try:
        fs.assert_safe(lexical)
    except Exception as exc:
        if isinstance(exc, spdm_storage.SpdmStorageError):
            raise DashboardCaptureError(exc.code, str(exc)) from exc
        raise
    try:
        target = fs.resolve(lexical)
    except FileNotFoundError as exc:
        raise DashboardCaptureError("DASHBOARD_SOURCE_MISSING", "원본 폴더를 찾을 수 없습니다.") from exc
    except OSError as exc:
        raise DashboardCaptureError("DASHBOARD_SOURCE_READ_ERROR", "원본 폴더를 읽을 수 없습니다.") from exc
    except ValueError as exc:
        raise DashboardCaptureError("DASHBOARD_PATH_ESCAPE", "허용된 저장소 밖의 경로입니다.") from exc
    # Existing SPDM safety helper rejects symlinks/reparse points in every
    # ancestor. It is deliberately called only after relative validation.
    try:
        fs.assert_safe(target)
    except Exception as exc:
        if isinstance(exc, spdm_storage.SpdmStorageError):
            raise DashboardCaptureError(exc.code, str(exc)) from exc
        raise
    return target


def _validate_case_root(root: Path, relative: str, environment: str) -> str:
    fs = provider_for_root(root)
    target = _safe_target(root, relative)
    if not fs.is_dir(target):
        raise DashboardCaptureError("DASHBOARD_CASE_INVALID", "capture root는 Simulation Case 폴더여야 합니다.")
    child_names = {entry.name.casefold() for entry in fs.list(target) if fs.is_dir(fs.join(target, entry.name))}
    expected = _USAGE_DIRS if environment == "USAGE" else _DISTRIBUTION_DIRS
    if not child_names.intersection(expected):
        raise DashboardCaptureError("DASHBOARD_CASE_INVALID", "지정한 경로에서 해당 환경의 Simulation Case를 확인할 수 없습니다.")
    return target


def discover_cases(conn: ConnectionLike, relative_path: str = "", environment: str = "USAGE") -> dict[str, Any]:
    """Read-only discovery of Altair One Simulation Case directories."""
    if environment not in {"USAGE", "DISTRIBUTION"}:
        raise DashboardCaptureError("DASHBOARD_ENVIRONMENT_INVALID", "지원하지 않는 대시보드 환경입니다.")
    fs = _provider(conn)
    root = fs.root
    root_id = _root_id(root)
    base_relative = _relative(relative_path) if relative_path else ""
    base = _safe_target(root, base_relative) if base_relative else ""
    if not fs.is_dir(base):
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
            for entry in fs.list(current):
                visited += 1
                if visited > 20000:
                    issues.add("SCAN_LIMIT_REACHED")
                    break
                if not entry.name.startswith(".") and entry.kind == "dir":
                    children.append(fs.join(current, entry.name))
        except OSError:
            issues.add("DIRECTORY_UNAVAILABLE")
            continue
        if visited > 20000:
            break
        names = {PurePosixPath(item).name.casefold() for item in children}
        if names.intersection(expected):
            rel = current if current else "."
            if not base_relative:
                parent_parts = PurePosixPath(current).parts
                request_types = [re.match(r"^WR_[A-Za-z0-9._-]+_SimType([12])$", part, re.IGNORECASE) for part in parent_parts]
                expected_type = "1" if environment == "USAGE" else "2"
                if not any(match and match.group(1) == expected_type for match in request_types):
                    continue
            cases.append({"root_relative_path": rel, "source_name": fs.path(current).name, "environment": environment, "storage_root_id": root_id})
            continue
        if depth >= 8:
            if children:
                issues.add("SCAN_DEPTH_LIMIT")
            continue
        for child in sorted(children, key=lambda item: PurePosixPath(item).name.casefold()):
            if PurePosixPath(child).name.casefold() in excluded:
                continue
            try:
                fs.assert_safe(child)
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
        # A drive root (scx mode) is not a path; its provider paths sit below ``root.virtual``.
        value = path.relative_to(getattr(root, "virtual", root)).as_posix()
    except (ValueError, TypeError):
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
    include_path=None,
    excluded_files: list[dict[str, str]] | None = None,
) -> list[tuple[str, bytes, str]]:
    from .drive import reads as drive_reads

    # SCX drive (D2): have the Case's capture files downloaded before reading (no-op in local mode).
    drive_reads.require_content([relative])
    fs = provider_for_root(root)
    base = _safe_target(root, relative)
    excluded = {"cad", "report", "reports", "final", "validation", "library"}
    allowed = {".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm"}
    items, stack = [], [(base, 0)]
    visited = 0

    def signature_of(item: str) -> tuple[int, int, str]:
        info = fs.stat(item, follow_links=True, missing_ok=False)
        return int(info.size), int(info.modified_ns), info.item_id.rsplit(":", 1)[-1]

    try:
        while stack:
            directory, depth = stack.pop()
            if depth > 16:
                raise DashboardCaptureError("DASHBOARD_DEPTH_LIMIT", "결과 폴더 깊이 제한을 초과했습니다.")
            for entry in fs.list(directory):
                visited += 1
                if visited > 20000:
                    raise DashboardCaptureError("DASHBOARD_SCAN_LIMIT", "결과 파일 조사 범위를 초과했습니다.")
                child = fs.join(directory, entry.name)
                if entry.name.startswith("."):
                    continue
                if entry.name.casefold() in excluded:
                    if issues is not None:
                        issues.append(_unprocessed_issue(root, fs.path(child), "EXCLUDED_DIRECTORY"))
                    continue
                fs.assert_safe(child)
                if entry.kind == "dir":
                    stack.append((child, depth + 1))
                elif entry.kind == "file":
                    relative_path = child
                    if PurePosixPath(entry.name).suffix.casefold() in allowed and (include_path is None or include_path(relative_path)):
                        items.append(child)
                        if len(items) > 10000:
                            raise DashboardCaptureError("DASHBOARD_CAPTURE_TOO_LARGE", "결과 파일 수 제한을 초과했습니다.")
                    elif excluded_files is not None:
                        excluded_files.append({"relative_path": _safe_issue_relative(relative_path), "reason": "FORMAT_OR_PATTERN_EXCLUDED"})
                    elif issues is not None:
                        issues.append(_unprocessed_issue(root, fs.path(child), "UNSUPPORTED_EXTENSION"))
        result, total, signatures = [], 0, []
        for item in sorted(items, key=lambda path: fs.path(path).as_posix()):
            fs.assert_safe(item)
            if signature_of(item)[0] > MAX_ASSET_BYTES:
                raise DashboardCaptureError("DASHBOARD_ASSET_TOO_LARGE", "원본 자산은 32 MiB 이하여야 합니다.")
            data = fs.read_stable(item, max_bytes=min(MAX_ASSET_BYTES, MAX_TOTAL_BYTES - total))
            fs.assert_safe(item)
            signature = signature_of(item)
            signatures.append((item, *signature))
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise DashboardCaptureError("DASHBOARD_CAPTURE_TOO_LARGE", "수집 버전은 256 MiB 이하여야 합니다.")
            path = item
            media_type = mimetypes.guess_type(PurePosixPath(item).name)[0] or "application/octet-stream"
            result.append((path, data, media_type))
        for item, size, modified, inode in signatures:
            if signature_of(item) != (size, modified, inode):
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


def _schema_scoped(payload: dict[str, Any]) -> bool:
    return bool(payload.get("folder_schema_scoped") or payload.get("folder_schema_snapshot_id"))


def _schema_blocks_file(payload: dict[str, Any], file_relative_path: str) -> bool:
    blocked_paths = payload.get("folder_schema_blocked_paths")
    if not isinstance(blocked_paths, list):
        return False
    file_key = _relative(file_relative_path).casefold()
    return any(
        file_key.startswith(_relative(str(path)).casefold().rstrip("/") + "/")
        for path in blocked_paths if isinstance(path, str) and path
    )


def _schema_fingerprint_locations(locations: Any) -> list[dict[str, Any]]:
    """Keep capture identity tied to location semantics, not review provenance."""
    if not isinstance(locations, list):
        return []
    return sorted(({
        key: item.get(key)
        for key in ("id", "location_id", "target_id", "role_kind", "relative_path", "status")
    } for item in locations if isinstance(item, dict)),
        key=lambda item: (str(item.get("relative_path") or "").casefold(),
                          str(item.get("role_kind") or "")))


def _schema_allows_file(payload: dict[str, Any], case_relative_path: str, file_relative_path: str) -> bool:
    locations = payload.get("folder_schema_locations")
    if _schema_blocks_file(payload, file_relative_path):
        return False
    if not isinstance(locations, list) or not locations:
        return not _schema_scoped(payload)
    environment = str(payload.get("environment") or "").upper()
    # D8: DEPTH_V1 Usage Scenes replace EVALUATION; legacy captures keep EVALUATION.
    allowed_roles = {"EVALUATION", "SCENE", "RESULTS"} if environment == "USAGE" else {"SCENE", "RESULTS"}
    case_key = _relative(case_relative_path).casefold().rstrip("/") + "/"
    file_key = _relative(file_relative_path).casefold()
    for location in locations:
        if not isinstance(location, dict) or location.get("status") not in {"CONFIRMED", "LINKED"}:
            continue
        if str(location.get("role_kind") or "") not in allowed_roles:
            continue
        path = str(location.get("relative_path") or "").casefold().rstrip("/")
        if path.startswith(case_key) and file_key.startswith(path + "/"):
            return True
    return False


def _assert_schema_allows_approved_files(payload: dict[str, Any], approved_files: list[tuple[str, bytes, str]]) -> None:
    locations = payload.get("folder_schema_locations")
    if not isinstance(locations, list) or not locations:
        if _schema_scoped(payload):
            raise DashboardCaptureError("DASHBOARD_SCHEMA_LOCATION_INVALID", "승인 파일을 제한할 확정된 Folder Schema 위치가 없습니다.")
        return
    allowed = [item for item in locations if isinstance(item, dict)
               and item.get("status") in {"CONFIRMED", "LINKED"}
               and item.get("role_kind") in {"RESULTS", "SCENE"}]
    if not allowed:
        raise DashboardCaptureError("DASHBOARD_SCHEMA_LOCATION_INVALID", "승인 파일에 연결된 Folder Schema Results 위치가 없습니다.")
    allowed_results = [str(item.get("relative_path") or "").casefold().rstrip("/") + "/"
                       for item in allowed if item.get("role_kind") == "RESULTS"]
    allowed_scenes = {str(item.get("relative_path") or "").casefold().rstrip("/")
                      for item in allowed if item.get("role_kind") == "SCENE"}
    for path, _content, _media_type in approved_files:
        normalized = _relative(path).casefold()
        parent = PurePosixPath(normalized).parent.as_posix()
        if (_schema_blocks_file(payload, path)
                or not any(normalized.startswith(prefix) for prefix in allowed_results)
                and parent not in allowed_scenes):
            raise DashboardCaptureError("DASHBOARD_SCHEMA_LOCATION_INVALID", "승인 파일이 확인된 Folder Schema Results 위치 밖에 있습니다.")


def _verify_context(conn: ConnectionLike, project_id: str, request_id: str) -> None:
    row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    if not row or str(row[0]) != project_id:
        raise DashboardCaptureError("DASHBOARD_CONTEXT_INVALID", "의뢰와 프로젝트 문맥이 일치하지 않습니다.")


def create_capture(
    conn: ConnectionLike,
    payload: dict[str, Any],
    *,
    actor: str,
    approved_files: list[tuple[str, bytes, str]] | None = None,
    approved_manifest: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _verify_context(conn, str(payload["project_id"]), str(payload["request_id"]))
    relative = _relative(str(payload["root_relative_path"]))
    fs = _provider(conn)
    root = fs.root
    if storage_root_id := str(payload.get("storage_root_id") or ""):
        if storage_root_id != _root_id(root):
            raise DashboardCaptureError("DASHBOARD_ROOT_ID_INVALID", "설정된 저장소와 storage_root_id가 일치하지 않습니다.")
    if approved_files is None:
        _validate_case_root(root, relative, str(payload["environment"]))
    else:
        # Result-registration publication uses the exact review-approved bytes.
        # It must not scan the Case tree, where unreviewed or concurrent files
        # may have appeared after inspection.
        case_root = _safe_target(root, relative)
        if not fs.is_dir(case_root):
            raise DashboardCaptureError("DASHBOARD_CASE_INVALID", "승인된 Case 폴더를 찾을 수 없습니다.")
        expected = {str(item.get("relative_path")): item for item in (approved_manifest or [])}
        if len(expected) != len(approved_files):
            raise DashboardCaptureError("DASHBOARD_APPROVED_SOURCE_INVALID", "승인된 파일 목록이 일치하지 않습니다.")
        _assert_schema_allows_approved_files(payload, approved_files)
        actual: set[str] = set()
        case_prefix = relative.rstrip("/") + "/"
        total = 0
        for path, content, _media_type in approved_files:
            normalized = _relative(path)
            if not normalized.startswith(case_prefix) or normalized.casefold() in {value.casefold() for value in actual}:
                raise DashboardCaptureError("DASHBOARD_APPROVED_SOURCE_INVALID", "승인 파일이 Case 경로 밖에 있거나 중복되었습니다.")
            actual.add(normalized)
            item = expected.get(normalized)
            digest = hashlib.sha256(content).hexdigest()
            if (item is None or int(item.get("size", -1)) != len(content) or
                    str(item.get("sha256") or "").casefold() != digest):
                raise DashboardCaptureError("DASHBOARD_APPROVED_SOURCE_STALE", "승인된 원본이 검수 시점과 달라졌습니다.")
            if len(content) > MAX_ASSET_BYTES:
                raise DashboardCaptureError("DASHBOARD_ASSET_TOO_LARGE", "원본 자산은 32 MiB 이하여야 합니다.")
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise DashboardCaptureError("DASHBOARD_CAPTURE_TOO_LARGE", "수집 버전은 256 MiB 이하여야 합니다.")
    walk_issues: list[str] = []
    review_contract = payload.get("usage_source_review") if payload.get("environment") == "USAGE" else None
    review_selection = usage_source_review.selection(review_contract.get("selection") if isinstance(review_contract, dict) else None)
    if approved_files is None:
        schema_scoped = _schema_scoped(payload)

        def include_capture_path(path: str) -> bool:
            if review_contract and not usage_source_review.include_path(path, review_selection):
                return False
            return not schema_scoped or _schema_allows_file(payload, relative, path)

        files = _walk(root, relative, issues=walk_issues,
                      include_path=include_capture_path if review_contract or schema_scoped else None,
                      excluded_files=[] if review_contract or schema_scoped else None)
        if review_contract and schema_scoped:
            review_files = [(path, data) for path, data, _ in files]
            # Re-run source review against the exact canonical locations;
            # do not let an unconfirmed sibling evaluation satisfy review.
            inspected = usage_source_review.review(
                review_files,
                selected=review_contract.get("selection"),
                selected_sources=review_contract.get("selected_sources"),
                metric_paths=review_contract.get("metric_paths"),
                excludes=review_contract.get("excludes"),
                profile_id=review_contract.get("profile_id"),
                profile_revision=review_contract.get("profile_revision"),
            )
            expected = sorted(review_contract.get("sources") or [], key=lambda item: str(item.get("source", "")).casefold())
            if expected != inspected["contract"]["sources"] or inspected["blocking_count"]:
                raise DashboardCaptureError("USAGE_SOURCE_REVIEW_STALE", "확인된 Folder Schema 위치와 파일 검수가 일치하지 않습니다.")
    else:
        files = sorted(approved_files, key=lambda item: item[0].casefold())
    manifest = [{"relative_path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "media_type": media_type} for path, data, media_type in files]
    recipe_version = "dashboard-v3"
    fingerprint_context = {key: payload.get(key) for key in ("project_id", "request_id", "simulation_case_id", "load_case_id", "execution_run_id", "run_option_id", "option_label", "option_status", "run_option_labels", "hierarchy_assignments", "rule_profile_id", "rule_profile_version", "mode", "component_id")}
    if payload.get("folder_schema_locations"):
        fingerprint_context["folder_schema_locations"] = _schema_fingerprint_locations(
            payload.get("folder_schema_locations"),
        )
    if review_contract:
        fingerprint_context["usage_source_review"] = review_contract.get("fingerprint")
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
    context = {key: payload.get(key) for key in ("project_id", "request_id", "simulation_case_id", "load_case_id", "execution_run_id", "run_display_name", "run_option_id", "option_label", "option_status", "run_option_labels", "hierarchy_assignments", "rule_profile_id", "rule_profile_version", "mode", "capture_id", "component_id", "folder_schema_locations", "folder_schema_snapshot_id")}
    context["capture_id"] = capture_id
    if payload["environment"] == "USAGE":
        grouped: dict[str, list[tuple[str, bytes]]] = {}
        for path, data, _ in files:
            grouped.setdefault(PurePosixPath(path).parts[0] if PurePosixPath(path).parts else "", []).append((path, data))
        parsed = {"environment": "USAGE", "context": context, **_usage_payload(grouped, review_contract)}
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


def _usage_payload(grouped: dict[str, list[tuple[str, bytes]]], review_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    files = [(path, data) for entries in grouped.values() for path, data in entries]
    if review_contract:
        inspected = usage_source_review.review(
            files,
            selected=review_contract.get("selection"),
            selected_sources=review_contract.get("selected_sources"),
            metric_paths=review_contract.get("metric_paths"),
            excludes=review_contract.get("excludes"),
            profile_id=review_contract.get("profile_id"),
            profile_revision=review_contract.get("profile_revision"),
        )
        expected_sources = sorted(review_contract.get("sources") or [], key=lambda item: str(item.get("source", "")).casefold())
        if expected_sources != inspected["contract"]["sources"]:
            raise DashboardCaptureError("USAGE_SOURCE_REVIEW_STALE", "검수 후 선택 원본이 변경되었습니다. 다시 검수하세요.")
        if inspected["blocking_count"] or ((inspected["missing_count"] or review_contract.get("excludes")) and not review_contract.get("acknowledge_partial")):
            raise DashboardCaptureError("USAGE_SOURCE_REVIEW_REQUIRED", "파일·값 검수의 오류 또는 부분 게시 확인을 완료하세요.")
        result = []
        for entry in inspected["entries"]:
            paths = {metric["key"]: metric["path"] for metric in entry["metrics"]}
            statuses = {metric["key"]: metric["status"] for metric in entry["metrics"] if metric["status"] != "READY"}
            result.append({key: entry[key] for key in ("evaluation", "direction", "condition", "source", "status", "values")}
                          | {"metric_paths": paths, "metric_statuses": statuses, "media": []})
        # Match selected media only to the reviewed source stem; unmatched media
        # remain media-only entries just as the legacy collector did.
        used_media = set()
        for item in result:
            source = item.get("source")
            if not source:
                continue
            source_path = PurePosixPath(source)
            for path, _ in files:
                media_path = PurePosixPath(path)
                if media_path.suffix.casefold() in usage_source_review.MEDIA_SUFFIXES and media_path.parent == source_path.parent and media_path.stem == source_path.stem.removesuffix("_result"):
                    used_media.add(path)
                    item["media"].append({"relative_path": path, "title": item["evaluation"] + " · " + item["direction"], "kind": "VIDEO" if media_path.suffix.casefold() in {".mp4", ".webm"} else "IMAGE", "status": "READY", "frame_role": "UNKNOWN"})
        for path, _ in files:
            media_path = PurePosixPath(path)
            evaluation = usage_source_review.evaluation_for_path(path)
            # Media uses the historical stem convention (without ``_result``),
            # while numeric sources intentionally require the strict result
            # suffix in the review selector.
            identity = _usage_identity(path, evaluation) if evaluation else None
            if path not in used_media and media_path.suffix.casefold() in usage_source_review.MEDIA_SUFFIXES:
                media = {"relative_path": path,
                         "title": (evaluation + " · " if evaluation else "") + media_path.stem,
                         "kind": "VIDEO" if media_path.suffix.casefold() in {".mp4", ".webm"} else "IMAGE",
                         "status": "READY", "frame_role": "UNKNOWN"}
                if identity:
                    target = next((entry for entry in result if entry["evaluation"] == evaluation and entry["direction"] == identity[0] and entry.get("condition") == identity[1]), None)
                    if target is not None:
                        target["condition"] = identity[1]
                        target["media"].append(media)
                    else:
                        result.append({"evaluation": evaluation, "direction": identity[0], "condition": identity[1], "status": "MISSING", "values": {}, "metric_paths": {}, "metric_statuses": {}, "media_only": True, "media": [media]})
                else:
                    # Keep otherwise-unmatched media visible in the Capture.
                    # Result Registration deliberately permits a video-only
                    # partial submission after the user acknowledges missing
                    # numeric sources during approval.
                    result.append({"evaluation": evaluation or EVALUATIONS[0], "direction": "common",
                                   "condition": media_path.stem, "status": "MISSING", "values": {},
                                   "metric_paths": {}, "metric_statuses": {}, "media_only": True,
                                   "media": [media]})
        return {"evaluation_names": list(EVALUATIONS), "evaluations": result, "usage_source_review": inspected["contract"]}
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
    assigned_scene_ids: dict[tuple[str, str, str], str] = {}
    run_meta: dict[tuple[str, str], dict[str, Any]] = {}
    quality_issues = set(scan_issues or ())
    assignments = list(context.get("hierarchy_assignments") or [])
    for path, data, _ in files:
        try:
            tail = PurePosixPath(path).relative_to(base).parts
        except ValueError:
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:OUTSIDE_CAPTURE_ROOT")
            continue
        directories = list(tail[:-1])
        assigned = []
        if assignments:
            source_parent = PurePosixPath(path).parent
            assigned = [row for row in assignments if PurePosixPath(str(row.get("relative_path") or "")) == source_parent or PurePosixPath(str(row.get("relative_path") or "")) in source_parent.parents]
            assigned.sort(key=lambda row: len(PurePosixPath(str(row["relative_path"])).parts))
        assigned_by_role = {row.get("role_kind"): row for row in assigned}
        if assignments and all(role in assigned_by_role for role in ("LOAD_CASE", "EXECUTION_RUN", "SCENE")):
            load_row, run_row, scene_row = (assigned_by_role[role] for role in ("LOAD_CASE", "EXECUTION_RUN", "SCENE"))
            option_row = assigned_by_role.get("RUN_OPTION")
            load_case, run_name, scene = str(load_row["raw_name"]), str(run_row["raw_name"]), str(scene_row["raw_name"])
            load_id, run_id = str(load_row["target_id"]), str(run_row["target_id"])
            option_label = str(option_row["raw_name"]) if option_row else None
            option_status = str(option_row.get("option_status") or "PRESENT") if option_row else "ABSENT"
            option_target = option_row.get("target_id") if option_row else None
            option_identity = str(option_row.get("relative_path") or option_label or "") if option_row else ""
            option_id = str(option_target) if option_target else "option-" + hashlib.sha256(
                f"{run_id}:{option_status}:{option_identity}".encode()
            ).hexdigest()[:24]
            mode = option_label.upper() if option_label and option_label.casefold() in {"individual", "cumulative"} else (option_label or "UNKNOWN")
            suffix = PurePosixPath(path).suffix.casefold()
            if suffix == ".json": quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:UNSUPPORTED_DISTRIBUTION_FORMAT")
            run_meta.setdefault((run_id, option_id), {"id": run_id, "source_name": run_name, "load_case_id": load_id,
                "load_case_name": load_case, "mode": mode, "modes": [mode], "run_option_id": option_id,
                "option_label": option_label, "option_status": option_status})
            scene_files.setdefault((run_id, option_id, scene), []).append((path, data))
            # Discovery previews deliberately do not persist SCENE registry rows,
            # so a confirmed hierarchy can validly carry no target_id here.  Do
            # not turn that absence into the shared literal ID "None"; the
            # path/run-option fallback below is stable and collision resistant.
            if scene_row.get("target_id"):
                assigned_scene_ids[(run_id, option_id, scene)] = str(scene_row["target_id"])
            continue
        if assignments:
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:INCOMPLETE_HIERARCHY_ASSIGNMENT")
            continue
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
        option_label: str | None = None
        option_status = "ABSENT"
        if len(remaining) == 2:
            # Run Option is an open label, not an enum. Preserve its exact source
            # spelling while retaining `mode` as a compatibility projection.
            option_label = remaining[0]
            confirmed_labels = {str(item).casefold() for item in context.get("run_option_labels") or []}
            option_status = "PRESENT" if remaining[0].casefold() in {"individual", "cumulative"} | confirmed_labels else "UNRESOLVED"
            remaining = remaining[1:]
        elif len(remaining) != 1:
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:UNEXPECTED_RESULT_PATH_DEPTH")
            continue
        mode = option_label.upper() if option_label and option_label.casefold() in {"individual", "cumulative"} else (option_label or "UNKNOWN")
        scene = remaining[0]
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix == ".json":
            quality_issues.add(f"UNPROCESSED_FILE:{_safe_issue_relative(path)}:UNSUPPORTED_DISTRIBUTION_FORMAT")
        run_identity = f"{storage_root_id}:{relative}:{load_case}/{run_name}"
        run_id = "run-" + hashlib.sha256(run_identity.encode()).hexdigest()[:24]
        load_id = "load-" + hashlib.sha256(f"{storage_root_id}:{relative}:{load_case}".encode()).hexdigest()[:24]
        option_identity = f"{run_id}:{option_status}:{option_label or ''}"
        option_id = "option-" + hashlib.sha256(option_identity.encode()).hexdigest()[:24]
        run_meta.setdefault((run_id, option_id), {"id": run_id, "source_name": run_name, "load_case_id": load_id,
            "load_case_name": load_case, "mode": mode, "modes": [mode], "run_option_id": option_id,
            "option_label": option_label, "option_status": option_status})
        scene_files.setdefault((run_id, option_id, scene), []).append((path, data))
    scenes = []
    runs: dict[tuple[str, str], list[dict[str, Any]]] = {key: [] for key in run_meta}
    for (run_id, option_id, scene), grouped in sorted(scene_files.items(), key=lambda item: (parse_scene_name(item[0][2]).get("scene_sequence_number") is None, parse_scene_name(item[0][2]).get("scene_sequence_number") or 0, item[0][2].casefold())):
        ignored_sources: list[str] = []
        item = build_distribution_scene(scene, grouped, ignored_sources)
        quality_issues.update(
            f"UNPROCESSED_FILE:{_safe_issue_relative(source)}:UNRECOGNIZED_RESULT_FILE"
            for source in ignored_sources
        )
        meta = run_meta[(run_id, option_id)]
        mode = meta["mode"]
        item["id"] = assigned_scene_ids.get((run_id, option_id, scene)) or "scene-" + hashlib.sha256(f"{run_id}|{option_id}|{scene}".encode()).hexdigest()[:24]
        item["label"] = scene
        item["run_id"] = run_id
        item["mode"] = mode
        item["run_option_id"] = option_id
        scenes.append(item)
        runs[(run_id, option_id)].append(item)
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


LATEST_PREFIX = "latest:"


def latest_capture_id(case_id: str) -> str:
    return LATEST_PREFIX + str(case_id)


def merge_latest_payload(entries: list[tuple[str, Any]]) -> dict[str, Any]:
    """Merge a Case's captures, oldest first, into one "latest result" payload.

    Every Scene of every Run/Run option keeps its newest captured result, so a
    Run option shows all of its Scene folders together even when they were
    registered or copied at different times (user decision 2026-10-02).
    Distribution scenes keep the capture they came from in
    ``source_capture_id``; media assets still belong to that capture.
    Usage payloads (no runs) use the newest capture as is.
    """
    if not entries:
        return {"runs": [], "scenes": [], "quality_issues": []}
    newest_id, newest = entries[-1]
    newest = _decode(newest)
    if not any((_decode(payload) or {}).get("runs") for _, payload in entries):
        return {**newest, "merged_capture_ids": [newest_id]}
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    scenes: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    sources: list[str] = []
    for capture_id, raw in entries:
        payload = _decode(raw) or {}
        contributed = False
        for run in payload.get("runs", []):
            key = (str(run.get("id")), str(run.get("run_option_id") or run.get("mode") or ""))
            runs[key] = {k: v for k, v in run.items() if k != "scenes"}
            bucket = scenes.setdefault(key, {})
            for scene in run.get("scenes", []):
                # A Scene folder name is unique inside one Run option.
                scene_key = str(scene.get("label") or scene.get("id") or "").casefold()
                bucket[scene_key] = {**scene, "source_capture_id": scene.get("source_capture_id") or capture_id}
                contributed = True
        if contributed:
            sources.append(capture_id)
    merged_runs = []
    merged_scenes = []
    for key, meta in runs.items():
        ordered = sorted(scenes.get(key, {}).values(), key=lambda item: (
            parse_scene_name(str(item.get("label") or "")).get("scene_sequence_number") is None,
            parse_scene_name(str(item.get("label") or "")).get("scene_sequence_number") or 0,
            str(item.get("label") or "").casefold()))
        merged_runs.append({**meta, "scenes": ordered})
        merged_scenes.extend(ordered)
    return {**{k: v for k, v in newest.items() if k not in {"runs", "scenes", "run"}},
            "runs": merged_runs, "scenes": merged_scenes,
            "run": merged_runs[0] if len(merged_runs) == 1 else {"source_name": newest.get("run", {}).get("source_name")},
            "quality_issues": sorted(set(newest.get("quality_issues", []))),
            "merged_capture_ids": sources}


def get_latest_capture(conn: ConnectionLike, case_id: str) -> dict[str, Any] | None:
    case = conn.execute("SELECT id,project_id,request_id,environment,source_name FROM dashboard_cases WHERE id=?",
                        [case_id]).fetchone()
    if not case:
        return None
    entries = [(str(row[0]), row[1]) for row in conn.execute(
        "SELECT id,payload_json FROM dashboard_captures WHERE case_id=? ORDER BY created_at,id", [case_id]).fetchall()]
    if not entries:
        return None
    payload = merge_latest_payload(entries)
    capture_id = latest_capture_id(case_id)
    payload.setdefault("context", {}).update({"project_id": case[1], "request_id": case[2], "capture_id": capture_id})
    fingerprint = hashlib.sha256(json.dumps([entry[0] for entry in entries]).encode()).hexdigest()
    return {"id": capture_id, "case_id": str(case[0]), "fingerprint": fingerprint, "environment": case[3],
            "source_name": case[4], "payload": payload}


def case_project_id(conn: ConnectionLike, case_id: str) -> str | None:
    row = conn.execute("SELECT project_id FROM dashboard_cases WHERE id=?", [case_id]).fetchone()
    return str(row[0]) if row else None
