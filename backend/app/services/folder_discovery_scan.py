"""Read-only, bounded directory inventory inside a configured server root."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

from . import spdm_storage

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
    return hashlib.sha256(spdm_storage._root_identity(root).encode("utf-8")).hexdigest()


def target(root: Path, relative: str) -> Path:
    path = root.joinpath(*normal(relative).split("/")) if relative else root
    spdm_storage._assert_safe_existing(path, root)
    if not path.is_dir():
        raise ValueError("조사할 폴더를 찾을 수 없습니다.")
    return path


def browse(root: Path, relative: str) -> list[dict[str, Any]]:
    base = target(root, relative)
    entries = []
    started = time.monotonic()
    with os.scandir(base) as iterator:
        for index, entry in enumerate(iterator):
            if index >= MAX_ENTRIES or time.monotonic() - started > MAX_SECONDS:
                raise ValueError("폴더 선택 목록의 조사 한도를 초과했습니다. 더 작은 시작 위치를 지정하세요.")
            path = Path(entry.path)
            if spdm_storage._is_reparse(path):
                continue
            entries.append({"name": entry.name, "relative_path": path.relative_to(root).as_posix(),
                            "is_directory": entry.is_dir(follow_symlinks=False)})
    return sorted(entries, key=lambda item: item["name"].casefold())


def scan(root: Path, relative: str) -> dict[str, Any]:
    nodes, issues = [], []
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
        try:
            spdm_storage._assert_safe_existing(path, root)
            info = path.stat()
            identity = f"{info.st_dev}:{info.st_ino}"
            with os.scandir(path) as iterator:
                for entry in iterator:
                    total_entries += 1
                    if total_entries > MAX_ENTRIES or time.monotonic() - started > MAX_SECONDS:
                        issues.append({"relative_path": rel, "code": "ENTRY_LIMIT", "message": "전체 항목 수 또는 조사 시간 한도를 초과했습니다."})
                        halted = True
                        break
                    child = Path(entry.path)
                    child_rel = child.relative_to(root).as_posix()
                    if spdm_storage._is_reparse(child):
                        issues.append({"relative_path": child_rel, "code": "REPARSE", "message": "연결 폴더·심볼릭 링크는 조사할 수 없습니다."})
                    elif entry.is_dir(follow_symlinks=False):
                        if depth >= MAX_DEPTH or len(child_rel) > 1024:
                            issues.append({"relative_path": child_rel, "code": "DEPTH_LIMIT", "message": "폴더 깊이 또는 경로 길이 한도를 초과했습니다."})
                        else:
                            children.append((child, child_rel, depth + 1, rel))
                    elif entry.is_file(follow_symlinks=False):
                        count += 1
                        files += 1
                        if child.suffix:
                            extensions.add(child.suffix.lower())
        except (OSError, spdm_storage.SpdmStorageError) as error:
            issues.append({"relative_path": rel, "code": "PATH_UNAVAILABLE", "message": str(error)[:200]})
        nodes.append({"relative_path": rel, "parent_path": parent, "name": path.name, "depth": depth,
                      "file_count": count, "extensions": sorted(extensions), "identity": identity})
        stack.extend(sorted(children, key=lambda child: child[1].casefold(), reverse=True))
    return {"status": "INCOMPLETE" if issues else "COMPLETE", "folder_count": len(nodes),
            "file_count": files, "nodes": nodes, "issues": issues}
