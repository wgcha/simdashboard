"""``DriveStorageProvider``: the SPDM root on the SCX drive (stage D2, integration 03 §2).

Returned by :func:`provider_for_root` for a :class:`DriveRoot` (scx mode with
``SIMDASH_SCX_DRIVE_ROOT``).  Paths are the same root-relative ``/`` strings as
``LocalFsProvider``; the drive path is ``<SIMDASH_SCX_DRIVE_ROOT>/<rel>`` below
the shared account home.  Reads go through :mod:`app.services.drive.reads`
(read sessions, no drive wait while a DB connection is held).  Every write
primitive raises ``SpdmStorageError("DRIVE_WRITE_DISABLED")`` in D2; the drive
upload queue (D3) replaces them.

What callers see differs from the local provider only where the drive has no
equivalent: no links or reparse points (``is_link`` is ``False``), ``pin`` is a
no-op, ``item_id`` is the drive item id and ``etag`` the effective
``version_token``.  Files whose live drive state is not applied yet (a change
waiting for user confirmation, a dismissed change, a deleted source; 05 §4)
are shown at their registered version: same size, time and token, and their
content comes from the blob store or the registered DB content.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable, Iterator, Literal

from .provider import Entry, SpdmStorageError

WRITE_DISABLED = "DRIVE_WRITE_DISABLED"
WRITE_DISABLED_MESSAGE = ("드라이브 모드에서는 아직 드라이브에 쓸 수 없습니다(읽기 전용). "
                          "업로드·Final 지정은 드라이브 쓰기 단계(D3) 이후 사용할 수 있습니다.")


def write_disabled() -> SpdmStorageError:
    return SpdmStorageError(WRITE_DISABLED, WRITE_DISABLED_MESSAGE)


class DriveRoot:
    """The configured SPDM root on the drive (opaque; never a local path).

    ``root_identity`` is ``scx:<server host>:/<SIMDASH_SCX_DRIVE_ROOT>``.  The
    root is fixed by the environment variable, so the path is the identity;
    computing it needs no drive call (callers hold DB connections while they
    resolve the root).
    """

    __slots__ = ("spdm_root", "server_host")

    def __init__(self, spdm_root: str, server_host: str | None) -> None:
        self.spdm_root = spdm_root.strip("/")
        self.server_host = (server_host or "").lower()

    @property
    def identity(self) -> str:
        return f"scx:{self.server_host}:/{self.spdm_root}"

    def root_key(self) -> str:
        return hashlib.sha256(self.identity.encode("utf-8")).hexdigest()

    @property
    def virtual(self) -> PurePosixPath:
        """Display-only path (issue texts, folder names); never opened locally."""
        return PurePosixPath("/") / self.spdm_root

    def __str__(self) -> str:
        return f"scx://{self.server_host}/{self.spdm_root}"

    def __repr__(self) -> str:
        return f"DriveRoot({self.spdm_root!r}, {self.server_host!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DriveRoot) and (other.spdm_root, other.server_host) == (self.spdm_root, self.server_host)

    def __hash__(self) -> int:
        return hash(("DriveRoot", self.spdm_root, self.server_host))


def drive_root_from_settings(settings: Any) -> DriveRoot:
    return DriveRoot(str(settings.spdm_root or ""), settings.server_host)


def _modified_ns(us: int | None) -> int:
    return int(us) * 1000 if us is not None else 0


class DriveStorageProvider:
    """Read-only ``StorageProvider`` over the drive SPDM root (module docstring)."""

    def __init__(self, root: DriveRoot) -> None:
        self.root = root

    # -- paths -------------------------------------------------------------------------------
    def _rel(self, rel_path: str) -> str:
        if not rel_path or rel_path == ".":
            return ""
        if ("\x00" in rel_path or "\\" in rel_path or ":" in rel_path or rel_path.startswith("/")
                or PurePosixPath(rel_path).is_absolute()):
            raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 상대 경로가 올바르지 않습니다.")
        parts = [part for part in rel_path.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 상대 경로가 올바르지 않습니다.")
        from ..drive import reads

        return reads.check_rel("/".join(parts))

    def path(self, rel_path: str) -> PurePosixPath:
        """Display path of ``rel_path`` (``PurePosixPath`` below ``/<drive root>``; no local file)."""
        rel = self._rel(rel_path)
        return self.root.virtual.joinpath(*rel.split("/")) if rel else self.root.virtual

    def rel(self, path: Any) -> str:
        value = PurePosixPath(str(path)).relative_to(self.root.virtual).as_posix()
        return "" if value == "." else value

    @staticmethod
    def join(rel_path: str, *names: str) -> str:
        return "/".join(part for part in (rel_path, *names) if part)

    # -- identity ----------------------------------------------------------------------------
    def root_identity(self) -> str:
        return self.root.identity

    def root_key(self) -> str:
        return self.root.root_key()

    # -- effective view ----------------------------------------------------------------------
    def _entry(self, rel: str, raw: Any) -> Entry:
        from ..drive import reads

        kind = raw.kind if raw.kind in {"file", "dir"} else "other"
        if kind != "file":
            return Entry(name=str(raw.name), kind=kind, size=None, modified_ns=_modified_ns(reads.epoch_us(raw.modified_at)),
                         item_id=str(raw.item_id or ""), etag=None, is_link=False)
        version, _masked = reads.effective_version(rel, raw)
        return Entry(name=str(raw.name), kind="file", size=int(version.size or 0), modified_ns=_modified_ns(version.modified_us),
                     item_id=str(version.item_id or raw.item_id or ""), etag=version.token, is_link=False)

    @staticmethod
    def _synthetic(name: str, kind: Literal["file", "dir"], accepted: Any | None = None) -> Entry:
        if kind == "dir" or accepted is None:
            return Entry(name=name, kind="dir", size=None, modified_ns=0, item_id="", etag=None, is_link=False)
        version = accepted.version
        return Entry(name=name, kind="file", size=int(version.size or 0), modified_ns=_modified_ns(version.modified_us),
                     item_id=str(version.item_id or ""), etag=version.token, is_link=False)

    def _children(self, rel: str) -> list[Entry]:
        """Effective direct children; registered files missing on the drive stay visible (05 §4 MISSING)."""
        from ..drive import reads

        missing_folder = False
        try:
            raw = reads.listing(self.root.spdm_root, rel)
        except SpdmStorageError as error:
            if error.code != "SPDM_NOT_FOUND" or not reads.overlay_below(rel):
                raise _os_error(error, rel) from None
            raw, missing_folder = [], True
        entries = [self._entry(self.join(rel, str(item.name)), item) for item in raw]
        present = {entry.name for entry in entries}
        prefix = f"{rel}/" if rel else ""
        for item in reads.overlay_below(rel):
            if item.source_state != "MISSING" and not missing_folder:
                continue
            rest = item.rel_path[len(prefix):]
            name, _, deeper = rest.partition("/")
            if not name or name in present:
                continue
            present.add(name)
            entries.append(self._synthetic(name, "dir" if deeper else "file", None if deeper else item))
        return sorted(entries, key=lambda entry: entry.name)

    def _stat(self, rel: str) -> Entry | None:
        from ..drive import reads

        if not rel:
            raw = reads.raw_entry(self.root.spdm_root, "")
            if raw is None:
                return None
            return Entry(name=self.root.virtual.name, kind="dir", size=None, modified_ns=0, item_id=str(raw.item_id or ""),
                         etag=None, is_link=False)
        raw = reads.raw_entry(self.root.spdm_root, rel)
        if raw is not None:
            return self._entry(rel, raw)
        item = reads.accepted(rel)
        name = rel.rsplit("/", 1)[-1]
        if item is not None and item.source_state == "MISSING":
            return self._synthetic(name, "file", item)
        if reads.overlay_below(rel):
            return self._synthetic(name, "dir")
        return None

    # -- reads -------------------------------------------------------------------------------
    def list(self, rel_path: str, *, stat: bool | Literal["files"] = False) -> list[Entry]:
        return self._children(self._rel(rel_path))

    def stat(self, rel_path: str, *, follow_links: bool = False, missing_ok: bool = True) -> Entry | None:
        rel = self._rel(rel_path)
        entry = self._stat(rel)
        if entry is None and not missing_ok:
            raise FileNotFoundError(2, "drive item not found", rel)
        return entry

    def exists(self, rel_path: str, *, follow_links: bool = True) -> bool:
        return self.stat(rel_path) is not None

    def is_dir(self, rel_path: str) -> bool:
        entry = self.stat(rel_path)
        return entry is not None and entry.kind == "dir"

    def is_file(self, rel_path: str) -> bool:
        entry = self.stat(rel_path)
        return entry is not None and entry.kind == "file"

    def is_link(self, rel_path: str) -> bool:
        self._rel(rel_path)
        return False

    def assert_safe(self, rel_path: str) -> None:
        """No links on the drive; only the path rules apply (contract §1)."""
        self._rel(rel_path)

    def case_collision(self, parent_rel: str, name: str) -> str | None:
        parent = self._rel(parent_rel)
        try:
            children = self._children(parent)
        except FileNotFoundError:
            return None
        found = next((entry for entry in children if entry.name.casefold() == name.casefold()), None)
        return None if found is None else self.join(parent, found.name)

    def resolve(self, rel_path: str) -> str:
        rel = self._rel(rel_path)
        if self._stat(rel) is None:
            raise FileNotFoundError(2, "drive item not found", rel)
        return rel

    def walk(self, rel_path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        """Top-down like ``os.walk``; pruning ``dirnames`` in place prunes the walk."""
        start = self._rel(rel_path)
        stack = [start]
        while stack:
            current = stack.pop()
            try:
                children = self._children(current)
            except (FileNotFoundError, NotADirectoryError):
                continue
            dirnames = [entry.name for entry in children if entry.kind == "dir"]
            filenames = [entry.name for entry in children if entry.kind == "file"]
            yield current, dirnames, filenames
            stack.extend(self.join(current, name) for name in reversed(dirnames))

    def rglob(self, rel_path: str) -> Iterator[str]:
        for directory, dirnames, filenames in self.walk(rel_path):
            for name in [*dirnames, *filenames]:
                yield self.join(directory, name)

    def _file_version(self, rel: str):
        from ..drive import reads

        raw = reads.raw_entry(self.root.spdm_root, rel)
        if raw is None:
            item = reads.accepted(rel)
            if item is not None and item.source_state == "MISSING":
                return item.version, True
            raise SpdmStorageError("SPDM_FILE_UNAVAILABLE", "원본 파일을 읽을 수 없습니다.")
        if raw.kind != "file":
            raise SpdmStorageError("SPDM_FILE_UNAVAILABLE", "원본 파일을 읽을 수 없습니다.")
        if getattr(raw, "local_safe", True) is False:
            raise SpdmStorageError("SPDM_INVALID_PATH", "이 서버에서 쓸 수 없는 이름입니다(Windows 금지 문자·대소문자 중복).")
        return reads.effective_version(rel, raw)

    def read_stable(self, rel_path: str, *, max_bytes: int = 512 * 1024 * 1024) -> bytes:
        return self.read_stable_digest(rel_path, max_bytes=max_bytes)[0]

    def read_stable_digest(self, rel_path: str, *, max_bytes: int = 512 * 1024 * 1024) -> tuple[bytes, str]:
        from ..drive import reads

        rel = self._rel(rel_path)
        version, masked = self._file_version(rel)
        return reads.read_bytes(self.root.spdm_root, rel, version, masked=masked, max_bytes=max_bytes)

    @contextmanager
    def open_read(self, rel_path: str):
        from ..drive import reads

        rel = self._rel(rel_path)
        version, masked = self._file_version(rel)
        with reads.open_stream(self.root.spdm_root, rel, version, masked=masked, max_bytes=512 * 1024 * 1024) as stream:
            yield stream

    def read_small_nofollow(self, rel_path: str, *, max_bytes: int, before_read=None) -> bytes | None:
        try:
            rel = self._rel(rel_path)
            entry = self._stat(rel)
            if entry is None or entry.kind != "file" or (entry.size or 0) > max_bytes:
                return None
            if before_read is not None:
                before_read(int(entry.size or 0))
            return self.read_stable(rel, max_bytes=max_bytes)
        except (OSError, SpdmStorageError):
            return None

    def content_token(self, rel_path: str) -> str | None:
        """Effective ``version_token`` (content fingerprints use it instead of reading the file)."""
        rel = self._rel(rel_path)
        version, _masked = self._file_version(rel)
        return version.token

    def hash_stable(self, rel_path: str, *, chunk_size: int | None = None, on_progress=None) -> tuple[int, str]:
        from ..drive import reads

        rel = self._rel(rel_path)
        version, masked = self._file_version(rel)
        size, digest = reads.local_copy_sha256(self.root.spdm_root, rel, version, masked=masked, max_bytes=512 * 1024 * 1024)
        if on_progress is not None:
            on_progress(size)
        return size, digest

    def pin(self, rel_path: str):
        self._rel(rel_path)
        return nullcontext()

    def changes(self, rel_path: str, since: str | None) -> None:
        return None

    def list_detailed(self, rel_path: str) -> list[tuple[Entry, int]]:
        return [(entry, 0) for entry in self.list(rel_path)]

    # -- writes: none in D2 ------------------------------------------------------------------
    def free_bytes(self, rel_path: str) -> int:
        raise write_disabled()

    def copy_stream(self, src_rel: str, dst_rel: str, *, zone: str, chunk_size: int | None = None, on_progress=None):
        raise write_disabled()

    def rename_no_replace(self, src_rel: str, dst_rel: str, *, zone: str) -> None:
        raise write_disabled()

    def append_bytes(self, rel_path: str, data: bytes, *, zone: str, fsync: bool = False) -> None:
        raise write_disabled()

    def try_lock(self, rel_path: str, *, zone: str):
        raise write_disabled()

    def mkdir_pinned(self, rel_path: str, *, zone: str) -> bool:
        raise write_disabled()

    def set_hidden(self, rel_path: str, *, zone: str) -> None:
        raise write_disabled()

    def write_chunk(self, rel_path: str, offset: int, data: bytes, *, zone: str, fsync: bool = True) -> int:
        raise write_disabled()

    def mkdirs(self, rel_path: str, *, zone: str, parents: bool = True, exist_ok: bool = True) -> None:
        raise write_disabled()

    def create_exclusive(self, rel_path: str, data: bytes | Iterable[bytes], *, zone: str, fsync: bool = True,
                         private: bool = False) -> None:
        raise write_disabled()

    def move_no_overwrite(self, src_rel: str, dst_rel: str, *, zone: str) -> None:
        raise write_disabled()

    def replace(self, src_rel: str, dst_rel: str, *, zone: str) -> None:
        raise write_disabled()

    def remove(self, rel_path: str, *, zone: str, directory: bool = False, missing_ok: bool = False) -> None:
        raise write_disabled()

    def lock(self, rel_path: str, *, zone: str):
        raise write_disabled()


def _os_error(error: SpdmStorageError, rel: str) -> BaseException:
    """Local parity: a missing folder is ``FileNotFoundError``, a file listed as folder ``NotADirectoryError``."""
    if error.code == "SPDM_NOT_FOUND":
        return FileNotFoundError(2, "drive folder not found", rel)
    if error.code == "SPDM_INVALID_PATH":
        return NotADirectoryError(20, "not a drive folder", rel)
    return error


__all__ = ["DriveRoot", "DriveStorageProvider", "WRITE_DISABLED", "drive_root_from_settings", "write_disabled"]
