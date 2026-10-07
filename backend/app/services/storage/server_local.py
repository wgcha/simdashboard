"""Server-local file areas of the SCX drive read path (stage D2, integration 05 §2–3, §7).

These folders are on the dashboard server, never on the drive and never below
the SPDM root: the download staging folder (``SIMDASH_SCX_STAGING_DIR``, one
sub-folder per read session, deleted when the session ends) and the
content-addressed blob store (``SIMDASH_DRIVE_BLOB_DIR``) for downloaded files
above 32 MiB.  ``app.services.drive`` uses only these helpers for local files,
so the drive package itself contains no delete/replace calls (static test).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import BinaryIO


def new_dir(parent: Path, prefix: str) -> Path:
    target = parent / f"{prefix}{uuid.uuid4().hex}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def make_child_dir(parent: Path, name: str) -> Path:
    target = parent / name
    target.mkdir(parents=True, exist_ok=False)
    return target


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def remove_old_dirs(parent: Path, prefix: str, max_age_seconds: float) -> None:
    """Remove ``prefix*`` sub-folders older than ``max_age_seconds`` (crash leftovers, 05 §7)."""
    cutoff = time.time() - max_age_seconds
    try:
        children = list(parent.iterdir())
    except OSError:
        return
    for child in children:
        try:
            if child.is_dir() and child.name.startswith(prefix) and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def is_regular_file_inside(path: Path, base: Path) -> bool:
    try:
        return path.resolve().is_relative_to(base.resolve()) and path.is_file() and not path.is_symlink()
    except OSError:
        return False


def write_file(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)


def read_file(path: Path) -> bytes:
    return path.read_bytes()


def open_file(path: Path) -> BinaryIO:
    return path.open("rb")


def file_size(path: Path) -> int:
    return int(path.stat().st_size)


class BlobStore:
    """Content-addressed store ``<dir>/<sha256[:2]>/<sha256>`` with a size cap (least recently used evicted)."""

    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def _path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise ValueError("invalid sha256")
        return self.directory / sha256[:2] / sha256

    def find(self, sha256: str) -> Path | None:
        """The stored file when present and intact (a corrupt file is discarded); marks it used."""
        try:
            path = self._path(str(sha256).lower())
        except ValueError:
            return None
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != path.name:
            path.unlink(missing_ok=True)
            return None
        try:
            os.utime(path)
        except OSError:
            pass
        return path

    def put(self, source: Path, sha256: str) -> Path:
        """Move ``source`` (a staging file whose sha256 is known) into the store."""
        path = self._path(sha256)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                source.unlink(missing_ok=True)
                os.utime(path)
                return path
            try:
                os.replace(source, path)
            except OSError:
                shutil.copyfile(source, path)
                source.unlink(missing_ok=True)
            self._evict()
        return path

    def _evict(self) -> None:
        files: list[tuple[float, int, Path]] = []
        total = 0
        for path in self.directory.glob("*/*"):
            try:
                info = path.stat()
            except OSError:
                continue
            files.append((info.st_mtime, info.st_size, path))
            total += info.st_size
        files.sort()
        while total > self.max_bytes and len(files) > 1:
            _mtime, size, path = files.pop(0)
            try:
                path.unlink()
                total -= size
            except OSError:
                continue


__all__ = ["BlobStore", "file_size", "is_regular_file_inside", "make_child_dir", "new_dir", "open_file", "read_file",
           "remove_old_dirs", "remove_tree", "write_file"]
