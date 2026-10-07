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
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator


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


def file_exists(path: Path) -> bool:
    return path.is_file()


def write_stream(path: Path, source, *, chunk: int = 1024 * 1024) -> None:
    """Copy a readable binary stream into a new server staging file (``xb``)."""
    with path.open("xb") as stream:
        while block := source.read(chunk):
            stream.write(block)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def discard_file(path: Path) -> None:
    """Delete one server-local staging file (never a drive item); missing is fine."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_at(path: Path, offset: int, data: bytes) -> int:
    """Append ``data`` at ``offset`` of a staging file (resumable chunk upload).

    The file must be exactly ``offset`` bytes long (new file at 0); returns the new size.
    Raises ``ValueError`` when the stored length differs (another writer or a lost chunk).
    """
    if offset == 0 and not path.exists():
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return len(data)
    with path.open("r+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() != offset:
            raise ValueError("staging file length differs from the chunk offset")
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        return offset + len(data)


def install_file(source: Path, target: Path) -> None:
    """Move a finished staging file into place on the server (replaces an earlier staged copy)."""
    os.replace(source, target)


def file_sha256(path: Path, *, chunk: int = 8 * 1024 * 1024) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(chunk):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def file_sha1(path: Path, *, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha1()  # noqa: S324 - drive content fingerprint (contract §2.2 File.sha1), not security
    with path.open("rb") as stream:
        while block := stream.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def disk_free(path: Path) -> int | None:
    try:
        ensure_dir(path)
        return int(shutil.disk_usage(path).free)
    except OSError:
        return None


def old_dirs(parent: Path, prefix: str, max_age_seconds: float) -> list[Path]:
    cutoff = time.time() - max_age_seconds
    found: list[Path] = []
    try:
        children = list(parent.iterdir())
    except OSError:
        return found
    for child in children:
        try:
            if child.is_dir() and child.name.startswith(prefix) and child.stat().st_mtime < cutoff:
                found.append(child)
        except OSError:
            continue
    return found


_BLOB_NAME = re.compile(r"[0-9a-f]{64}")
_BLOB_FOLDER = re.compile(r"[0-9a-f]{2}")


def _inside(path: Path, base: Path) -> bool:
    try:
        return path == base or path.is_relative_to(base)
    except ValueError:
        return False


def _canonical(path: Path) -> Path:
    try:
        return Path(os.path.realpath(path))
    except OSError:
        return Path(os.path.abspath(path))


def blob_dir_conflict(directory: Path, *, reserved: Iterable[Path] = (), outside: Iterable[Path] = ()) -> str | None:
    """Why ``directory`` cannot hold the blob store, or ``None`` (L2).

    Eviction deletes files there, so it must not be (or contain) another server
    area (``reserved``: work and staging folders) nor lie inside one whose
    contents are removed wholesale (``outside``: the staging folder).
    """
    target = _canonical(directory)
    for area in reserved:
        if _inside(_canonical(area), target):
            return f"{area} 폴더와 같거나 그 상위 폴더입니다"
    for area in outside:
        if _inside(target, _canonical(area)):
            return f"{area} 폴더 안에 있습니다"
    return None


class BlobStore:
    """Content-addressed store ``<dir>/<sha256[:2]>/<sha256>`` with a size cap (least recently used evicted).

    Only files named like a blob (64 lower-case hex digits) in the folder named
    by their first two digits are ever considered for eviction; anything else
    found in the folder is left alone.
    """

    def __init__(self, directory: Path, max_bytes: int, *, reserved: Iterable[Path] = (),
                 outside: Iterable[Path] = ()) -> None:
        conflict = blob_dir_conflict(directory, reserved=reserved, outside=outside)
        if conflict is not None:
            raise ValueError(f"SIMDASH_DRIVE_BLOB_DIR({directory})가 {conflict}.")
        self.directory = directory
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def _path(self, sha256: str) -> Path:
        if not _BLOB_NAME.fullmatch(sha256):
            raise ValueError("invalid sha256")
        return self.directory / sha256[:2] / sha256

    def find(self, sha256: str) -> Path | None:
        """The stored file when present and intact (a corrupt file is discarded); marks it used."""
        try:
            path = self._path(str(sha256).lower())
        except ValueError:
            return None
        if not path.is_file() or path.is_symlink():
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

    def _blobs(self) -> Iterator[Path]:
        try:
            folders = list(self.directory.iterdir())
        except OSError:
            return
        for folder in folders:
            if not _BLOB_FOLDER.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
                continue
            try:
                children = list(folder.iterdir())
            except OSError:
                continue
            for path in children:
                if _BLOB_NAME.fullmatch(path.name) and path.name[:2] == folder.name and not path.is_symlink() \
                        and path.is_file():
                    yield path

    def _evict(self) -> None:
        files: list[tuple[float, int, Path]] = []
        total = 0
        for path in self._blobs():
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


__all__ = ["BlobStore", "blob_dir_conflict", "discard_file", "disk_free", "ensure_dir", "file_exists", "file_sha1", "write_stream", "file_sha256", "file_size",
           "install_file", "is_regular_file_inside", "make_child_dir", "new_dir", "old_dirs", "open_file", "read_file",
           "remove_old_dirs", "remove_tree", "write_at", "write_file"]
