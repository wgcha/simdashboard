"""Read-only, bounded directory inventory inside a configured server root."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from . import spdm_storage
from .storage.local import LocalFsProvider
from .storage.provider import UPLOAD_STAGING_DIR

_STAGING_FOLDED = UPLOAD_STAGING_DIR.casefold()


def _is_upload_staging(entry) -> bool:
    """W8 drop-upload staging folder: never listed, scanned or fingerprinted."""
    return entry.kind == "dir" and entry.name.casefold() == _STAGING_FOLDED

MAX_FOLDERS = 5000
MAX_ENTRIES = 50000
MAX_DEPTH = 64
MAX_SECONDS = 15


def normal(relative: str) -> str:
    if not relative:
        return ""
    if len(relative) > 1024:
        raise ValueError("폴더 상대 경로는 1024자 이하여야 합니다.")
    return spdm_storage._normalise_relative(relative)


def root_identity(root: Path) -> str:
    """``root_key``: sha256 of the provider's root identity."""
    return LocalFsProvider(root).root_key()


def target(root: Path, relative: str) -> str:
    """Root-relative folder to inspect (checked: no reparse ancestor, a directory)."""
    fs = LocalFsProvider(root)
    path = "/".join(normal(relative).split("/")) if relative else ""
    fs.assert_safe(path)
    if not fs.is_dir(path):
        raise ValueError("조사할 폴더를 찾을 수 없습니다.")
    return path


def browse(root: Path, relative: str) -> list[dict[str, Any]]:
    fs = LocalFsProvider(root)
    base = target(root, relative)
    entries = []
    started = time.monotonic()
    for index, entry in enumerate(fs.list(base)):
        if index >= MAX_ENTRIES or time.monotonic() - started > MAX_SECONDS:
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
    fs = LocalFsProvider(root)
    nodes, issues = [], []
    file_state: list[dict[str, Any]] = []
    files = total_entries = 0
    started = time.monotonic()
    stack = [(target(root, relative), normal(relative), 0, None)]
    halted = False
    while stack and not halted:
        path, rel, depth, parent = stack.pop()
        if len(nodes) >= MAX_FOLDERS or time.monotonic() - started > MAX_SECONDS:
            issues.append({"relative_path": rel, "code": "SCAN_LIMIT", "message": "폴더 수 또는 조사 시간 한도를 초과했습니다."})
            break
        children, extensions = [], set()
        count = 0
        identity = ""
        skipped = bool(skip_descendants and skip_descendants(rel, parent))
        try:
            fs.assert_safe(path)
            info = fs.stat(path, follow_links=True, missing_ok=False)
            identity = info.item_id
            if not skipped:
                for entry in fs.list(path, stat="files"):
                    if _is_upload_staging(entry):
                        continue
                    total_entries += 1
                    if total_entries > MAX_ENTRIES or time.monotonic() - started > MAX_SECONDS:
                        issues.append({"relative_path": rel, "code": "ENTRY_LIMIT", "message": "전체 항목 수 또는 조사 시간 한도를 초과했습니다."})
                        halted = True
                        break
                    child = fs.join(path, entry.name)
                    child_rel = child
                    if fs.is_link(child):
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
            issues.append({"relative_path": rel, "code": "PATH_UNAVAILABLE", "message": str(error)[:200]})
        node = {"relative_path": rel, "parent_path": parent, "name": fs.path(path).name, "depth": depth,
                "file_count": count, "extensions": sorted(extensions), "identity": identity}
        if skipped:
            node["children_skipped"] = True
        nodes.append(node)
        stack.extend(sorted(children, key=lambda child: child[1].casefold(), reverse=True))
    file_state.sort(key=lambda item: str(item["relative_path"]).casefold())
    return {"status": "INCOMPLETE" if issues else "COMPLETE", "folder_count": len(nodes),
            "file_count": files, "nodes": nodes, "file_state": file_state, "issues": issues}


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
