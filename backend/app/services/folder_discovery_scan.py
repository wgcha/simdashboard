"""Read-only, bounded directory inventory inside a configured server root."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import spdm_storage
from .storage import provider_for_root
from .storage.provider import UPLOAD_STAGING_DIR

_STAGING_FOLDED = UPLOAD_STAGING_DIR.casefold()


def _is_upload_staging(entry) -> bool:
    """W8 drop-upload staging folder: never listed, scanned or fingerprinted."""
    return entry.kind == "dir" and entry.name.casefold() == _STAGING_FOLDED

MAX_FOLDERS = 5000
MAX_ENTRIES = 50000
MAX_DEPTH = 64
MAX_SECONDS = 15

# SCX drive mode (issue #51): every folder is one remote ``list_dir`` round trip, so the local
# 15 s bound made request scans INCOMPLETE on a real drive. The drive bounds are configurable.
DRIVE_MAX_SECONDS_ENV = "SIMDASH_DRIVE_SCAN_MAX_SECONDS"
DRIVE_MAX_ENTRIES_ENV = "SIMDASH_DRIVE_SCAN_MAX_ENTRIES"
DRIVE_MAX_FOLDERS_ENV = "SIMDASH_DRIVE_SCAN_MAX_FOLDERS"
DRIVE_DEFAULT_MAX_SECONDS = 180
DRIVE_DEFAULT_MAX_ENTRIES = 200_000
DRIVE_DEFAULT_MAX_FOLDERS = 20_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanLimits:
    max_seconds: float
    max_entries: int
    max_folders: int


def _env_bound(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        logger.warning("%s=%s is outside %s..%s; using %s", name, value, minimum, maximum, default)
        return default
    return value


def drive_limits() -> ScanLimits:
    """SCX drive scan bounds (``SIMDASH_DRIVE_SCAN_MAX_*``; an invalid value falls back to the default)."""
    return ScanLimits(
        max_seconds=_env_bound(DRIVE_MAX_SECONDS_ENV, DRIVE_DEFAULT_MAX_SECONDS, 15, 3600),
        max_entries=_env_bound(DRIVE_MAX_ENTRIES_ENV, DRIVE_DEFAULT_MAX_ENTRIES, 1000, 2_000_000),
        max_folders=_env_bound(DRIVE_MAX_FOLDERS_ENV, DRIVE_DEFAULT_MAX_FOLDERS, 100, 200_000),
    )


def limits_for(fs) -> ScanLimits:
    """Scan bounds for one storage provider: local constants, larger configurable ones on the drive."""
    from .storage.drive import DriveStorageProvider

    if isinstance(fs, DriveStorageProvider):
        return drive_limits()
    return ScanLimits(max_seconds=MAX_SECONDS, max_entries=MAX_ENTRIES, max_folders=MAX_FOLDERS)


def limits_for_root(root) -> ScanLimits:
    return limits_for(provider_for_root(root))


def issue_summary(issues, limit: int = 3) -> str:
    """``CODE:path`` of the first scan issues (for logs), e.g. ``SCAN_LIMIT:P/R/Working``."""
    items = [f"{item.get('code')}:{item.get('relative_path') or '.'}" for item in list(issues or [])[:limit]
             if isinstance(item, dict)]
    more = len(issues or []) - len(items)
    return ", ".join(items) + (f" (+{more})" if more > 0 else "")


def issue_detail(issues, limit: int = 1) -> str:
    """Which folder/operation a scan could not read, for a user-facing message (issue #55).

    ``"<path> (list_dir, DRIVE_READ_NOT_PREPARED) 외 2건"``; empty when the scan carries no issue.
    """
    items = [item for item in list(issues or []) if isinstance(item, dict)]
    parts = []
    for item in items[:limit]:
        facts = [str(value) for value in (item.get("operation"), item.get("error_code") or item.get("code")) if value]
        parts.append(f"{item.get('relative_path') or '.'}" + (f" ({', '.join(facts)})" if facts else ""))
    more = len(items) - len(parts)
    return ", ".join(parts) + (f" 외 {more}건" if parts and more > 0 else "")


def incomplete_message(base: str, issues) -> str:
    """``base`` + the first unread path and operation: ``"… 읽지 못한 곳: <path> (list_dir, CODE)"``."""
    detail = issue_detail(issues)
    return f"{base} 읽지 못한 곳: {detail}" if detail else base


def normal(relative: str) -> str:
    if not relative:
        return ""
    if len(relative) > 1024:
        raise ValueError("폴더 상대 경로는 1024자 이하여야 합니다.")
    return spdm_storage._normalise_relative(relative)


def root_identity(root: Path) -> str:
    """``root_key``: sha256 of the provider's root identity."""
    return provider_for_root(root).root_key()


def target(root: Path, relative: str) -> str:
    """Root-relative folder to inspect (checked: no reparse ancestor, a directory)."""
    fs = provider_for_root(root)
    path = "/".join(normal(relative).split("/")) if relative else ""
    fs.assert_safe(path)
    if not fs.is_dir(path):
        raise ValueError("조사할 폴더를 찾을 수 없습니다.")
    return path


def browse(root: Path, relative: str) -> list[dict[str, Any]]:
    fs = provider_for_root(root)
    limits = limits_for(fs)
    base = target(root, relative)
    entries = []
    started = time.monotonic()
    for index, entry in enumerate(fs.list(base)):
        if index >= limits.max_entries or time.monotonic() - started > limits.max_seconds:
            raise ValueError("폴더 선택 목록의 조사 한도를 초과했습니다. 더 작은 시작 위치를 지정하세요.")
        if _is_upload_staging(entry):
            continue
        path = fs.join(base, entry.name)
        if fs.is_link(path):
            continue
        entries.append({"name": entry.name, "relative_path": path,
                        "is_directory": entry.kind == "dir"})
    return sorted(entries, key=lambda item: item["name"].casefold())


def scan(root: Path, relative: str, *, skip_descendants: Callable[[str, str | None], bool] | None = None) -> dict[str, Any]:
    from .storage.drive import DriveStorageProvider

    fs = provider_for_root(root)
    limits = limits_for(fs)
    drive_mode = isinstance(fs, DriveStorageProvider)
    nodes, issues = [], []
    skipped_entries: list[dict[str, Any]] = []   # entries left out without making the scan incomplete
    file_state: list[dict[str, Any]] = []
    files = total_entries = 0
    started = time.monotonic()
    stack = [(target(root, relative), normal(relative), 0, None)]
    halted = False
    while stack and not halted:
        path, rel, depth, parent = stack.pop()
        if len(nodes) >= limits.max_folders or time.monotonic() - started > limits.max_seconds:
            issues.append({"relative_path": rel, "code": "SCAN_LIMIT", "message": "폴더 수 또는 조사 시간 한도를 초과했습니다."})
            break
        children, extensions = [], set()
        count = 0
        identity = ""
        skipped = bool(skip_descendants and skip_descendants(rel, parent))
        operation = "stat"
        try:
            fs.assert_safe(path)
            info = fs.stat(path, follow_links=True, missing_ok=False)
            identity = info.item_id
            if not skipped:
                operation = "list_dir"
                for entry in fs.list(path, stat="files"):
                    if _is_upload_staging(entry):
                        continue
                    total_entries += 1
                    if total_entries > limits.max_entries or time.monotonic() - started > limits.max_seconds:
                        issues.append({"relative_path": rel, "code": "ENTRY_LIMIT", "message": "전체 항목 수 또는 조사 시간 한도를 초과했습니다."})
                        halted = True
                        break
                    child = fs.join(path, entry.name)
                    child_rel = child
                    try:
                        linked = fs.is_link(child)
                    except spdm_storage.SpdmStorageError as error:
                        if not drive_mode:
                            raise
                        # Issue #55 (SCX drive): one child whose name the dashboard cannot address (':' or
                        # '\\') is left out and reported; it never makes the whole folder unavailable.
                        skipped_entries.append({"relative_path": child_rel, "code": "NAME_NOT_SUPPORTED",
                                        "kind": entry.kind, "message": str(error)[:200]})
                        _log_skipped(child_rel, error)
                        continue
                    if linked:
                        issues.append({"relative_path": child_rel, "code": "REPARSE", "message": "연결 폴더·심볼릭 링크는 조사할 수 없습니다."})
                    elif entry.kind == "dir":
                        if depth >= MAX_DEPTH or len(child_rel) > 1024:
                            issues.append({"relative_path": child_rel, "code": "DEPTH_LIMIT", "message": "폴더 깊이 또는 경로 길이 한도를 초과했습니다."})
                        else:
                            children.append((child, child_rel, depth + 1, rel))
                    elif entry.kind == "file":
                        count += 1
                        files += 1
                        file_state.append({
                            "relative_path": child_rel,
                            "size": int(entry.size),
                            "modified_ns": int(entry.modified_ns),
                        })
                        suffix = PurePosixPath(entry.name).suffix
                        if suffix:
                            extensions.add(suffix.lower())
        except (OSError, spdm_storage.SpdmStorageError) as error:
            issue = {"relative_path": rel, "code": "PATH_UNAVAILABLE", "message": str(error)[:200]}
            if isinstance(error, spdm_storage.SpdmStorageError):
                # Which storage call failed and its code (SCX drive: the adapter operation, e.g. list_dir).
                issue.update(operation=operation, error_code=str(error.code))
            issues.append(issue)
        node = {"relative_path": rel, "parent_path": parent, "name": fs.path(path).name, "depth": depth,
                "file_count": count, "extensions": sorted(extensions), "identity": identity}
        if skipped:
            node["children_skipped"] = True
        nodes.append(node)
        stack.extend(sorted(children, key=lambda child: child[1].casefold(), reverse=True))
    file_state.sort(key=lambda item: str(item["relative_path"]).casefold())
    return {"status": "INCOMPLETE" if issues else "COMPLETE", "folder_count": len(nodes),
            "file_count": files, "nodes": nodes, "file_state": file_state, "issues": issues,
            "skipped_entries": skipped_entries}


_SKIPPED_LOG_SECONDS = 3600.0
_skipped_logged: dict[str, float] = {}


def _log_skipped(relative_path: str, error: BaseException) -> None:
    """One WARNING per skipped drive entry per hour (the screens re-scan every minute)."""
    now = time.monotonic()
    last = _skipped_logged.get(relative_path)
    if last is not None and now - last < _SKIPPED_LOG_SECONDS:
        return
    if len(_skipped_logged) > 4096:
        _skipped_logged.clear()
    _skipped_logged[relative_path] = now
    logger.warning("Folder scan skipped a drive entry whose name cannot be used on this server: %s (%s)",
                   relative_path[:512], getattr(error, "code", type(error).__name__))


# Files the Case results, materials and finalization actually read. Other files
# (solver logs, scratch output) may change constantly and must not trigger a
# refresh or new result versions.
RESULT_RELEVANT_EXTENSIONS = frozenset({
    ".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm",  # results and media
    ".inc", ".rad",                                              # input decks
    ".pdf", ".ppt", ".pptx", ".xlsx",                            # reports
})


def stat_fingerprint(scan_result: dict[str, Any]) -> str:
    """Fingerprint the folder tree and relevant files' (path, size, mtime) without reading contents."""
    import json
    from pathlib import PurePosixPath

    structure = sorted((
        str(item.get("relative_path") or "").casefold(),
        str(item.get("parent_path") or "").casefold(),
    ) for item in scan_result.get("nodes", []))
    files = sorted((
        str(item.get("relative_path") or "").casefold(),
        int(item.get("size") or 0),
        int(item.get("modified_ns") or 0),
    ) for item in scan_result.get("file_state", [])
        if PurePosixPath(str(item.get("relative_path") or "")).suffix.casefold() in RESULT_RELEVANT_EXTENSIONS)
    payload = json.dumps({"structure": structure, "files": files}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def relevant_content_fingerprint(content_entries: list) -> str:
    """Fingerprint result-relevant files by content digest (or size when not hashed).

    Modification times are left out: a file rewritten with identical bytes is
    unchanged, while changed bytes are detected even with an identical mtime.
    """
    import json
    from pathlib import PurePosixPath

    relevant = sorted(
        (str(path), int(size), digest if digest is not None else None)
        for path, size, _modified_ns, digest in content_entries
        if PurePosixPath(str(path)).suffix.casefold() in RESULT_RELEVANT_EXTENSIONS
    )
    payload = json.dumps(relevant, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
