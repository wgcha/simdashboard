"""Local filesystem SPDM storage provider (stage 1: the only implementation).

Every direct filesystem call on the SPDM root lives in this module.  The
primitives below were moved verbatim from ``spdm_storage`` (reparse/link and
case-collision checks, stable reader, root identity) and ``case_finalization``
(directory pinning, request lock, no-follow metadata reads); their results,
error codes and messages are unchanged.  Module-level primitives remain the
monkeypatch seams used by tests.
"""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Literal, NamedTuple

from .provider import (
    FINAL, LEGACY, NOT_ALLOWED_WRITE, WORKING, Entry, SpdmStorageError, StorageError, check_write,
)

_STORAGE_PACKAGE = __name__.rsplit(".", 1)[0]


# ---------------------------------------------------------------------------
# Primitives (moved from spdm_storage / case_finalization without change).
# ---------------------------------------------------------------------------

def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SpdmStorageError("SPDM_PATH_UNAVAILABLE", "SPDM 저장 경로를 읽을 수 없습니다.") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_safe_existing(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SpdmStorageError("SPDM_PATH_ESCAPE", "SPDM 저장 경로가 root 밖입니다.") from exc
    current = root
    if _is_reparse(current):
        raise SpdmStorageError("SPDM_ROOT_UNSAFE", "SPDM root는 reparse point일 수 없습니다.")
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 경로에 reparse point를 사용할 수 없습니다.")


def _case_collision(parent: Path, name: str) -> Path | None:
    if not parent.exists():
        return None
    for child in parent.iterdir():
        if child.name.casefold() == name.casefold():
            return child
    return None


def _root_identity(path: Path) -> str:
    info = path.stat()
    # st_dev/st_ino comes from the opened filesystem object on Windows too;
    # retaining the normalized spelling makes accidental UNC/drive remaps
    # diagnosable without treating a text path as identity by itself.
    return f"{info.st_dev}:{info.st_ino}:{str(path).casefold()}"


def _assert_raw_root_path(path: Path) -> None:
    if not path.is_absolute():
        raise SpdmStorageError("SPDM_ROOT_INVALID", "SPDM root는 절대 경로여야 합니다.")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise SpdmStorageError("SPDM_ROOT_UNSAFE", "SPDM root 경로에 reparse point를 사용할 수 없습니다.")


def resolve_root(raw: str) -> Path:
    """Resolve the configured root exactly as ``storage_root`` did."""
    path = Path(raw).expanduser()
    _assert_raw_root_path(path)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SpdmStorageError("SPDM_ROOT_UNAVAILABLE", "SPDM root를 확인할 수 없습니다.") from exc
    if not resolved.is_dir() or _is_reparse(resolved):
        raise SpdmStorageError("SPDM_ROOT_UNSAFE", "SPDM root는 일반 디렉터리여야 합니다.")
    return resolved


def prepare_root(raw: str) -> Path:
    """LEGACY zone root creation (``set_storage_root``): create, then resolve."""
    path = Path(raw).expanduser()
    _assert_raw_root_path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SpdmStorageError("SPDM_ROOT_UNAVAILABLE", "SPDM root를 준비할 수 없습니다.") from exc
    if not resolved.is_dir() or _is_reparse(resolved):
        raise SpdmStorageError("SPDM_ROOT_UNSAFE", "SPDM root는 일반 디렉터리여야 합니다.")
    return resolved


def resolve_existing_or_none(raw: str) -> Path | None:
    """Resolve a previously persisted root for comparison; ``None`` when absent."""
    try:
        return Path(raw).expanduser().resolve(strict=True)
    except OSError:
        return None


@contextmanager
def open_stable_reader(path: Path):
    """Open a source only when a Windows writer did not exclude readers.

    Tests may monkeypatch this public seam to model an existing producer handle.
    A sharing violation is deliberately represented as ``SpdmStorageError``;
    refresh records PENDING instead of consuming a paused copy.
    """
    if os.name != "nt":
        try:
            with path.open("rb") as stream:
                yield stream
            return
        except OSError as exc:
            raise SpdmStorageError("SPDM_FILE_BUSY", "원본 파일 작성 완료를 기다립니다.") from exc
    import ctypes
    import msvcrt
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
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.GetFileType.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0x00000001, None, 3, 0x80, None)
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        code = ctypes.get_last_error()
        if code in {32, 33}:
            raise SpdmStorageError("SPDM_FILE_BUSY", "원본 파일 작성 완료를 기다립니다.")
        raise SpdmStorageError("SPDM_FILE_UNAVAILABLE", "원본 파일을 읽을 수 없습니다.")
    information = _FileInformation()
    if kernel32.GetFileType(handle) != 1 or not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)) or information.dwFileAttributes & 0x410:
        kernel32.CloseHandle(handle)
        raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 원본은 일반 reparse 없는 파일이어야 합니다.")
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            yield stream
    except OSError as exc:
        raise SpdmStorageError("SPDM_FILE_UNAVAILABLE", "원본 파일을 읽을 수 없습니다.") from exc


def read_stable_bytes(path: Path, *, max_bytes: int = 512 * 1024 * 1024) -> tuple[bytes, str]:
    """Capture one bounded byte snapshot through the stable-reader handle."""
    try:
        before = path.stat()
    except OSError as exc:
        raise SpdmStorageError("SPDM_FILE_UNAVAILABLE", "원본 파일을 읽을 수 없습니다.") from exc
    digest = hashlib.sha256()
    payload = bytearray()
    with open_stable_reader(path) as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise SpdmStorageError("SPDM_FILE_TOO_LARGE", "원본 파일은 512 MiB 이하여야 합니다.")
    try:
        after = path.stat()
    except OSError as exc:
        raise SpdmStorageError("SPDM_FILE_BUSY", "원본 파일 작성 완료를 기다립니다.") from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise SpdmStorageError("SPDM_FILE_BUSY", "원본 파일 작성 완료를 기다립니다.")
    return bytes(payload), digest.hexdigest()


@contextmanager
def pin_directory_chain(root: Path, directory: Path) -> Iterator[None]:
    """Pin every existing directory ancestor without allowing Windows rename/reparse swaps.

    Moved from ``case_finalization``; errors keep their ``FINALIZATION_*``
    codes and messages (the caller re-raises them as ``CaseFinalizationError``).
    """
    try:
        root_path = Path(os.path.abspath(root))
        directory_path = Path(os.path.abspath(directory))
        directory_path.relative_to(root_path)
    except (OSError, ValueError) as exc:
        raise SpdmStorageError("FINALIZATION_PATH_UNSAFE", "최종확정 대상 폴더가 SPDM root 밖에 있습니다.") from exc
    if os.name != "nt":
        _assert_safe_existing(directory_path, root_path)
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
                raise SpdmStorageError("FINALIZATION_PATH_BUSY", "최종확정 경로를 안전하게 고정할 수 없습니다.")
            handles.append(handle)
            info = _FileInformation()
            if (not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info))
                    or not info.dwFileAttributes & 0x10 or info.dwFileAttributes & 0x400):
                raise SpdmStorageError("FINALIZATION_PATH_UNSAFE", "최종확정 경로에 reparse point 또는 일반 폴더가 아닌 항목이 있습니다.")
        yield
    finally:
        for handle in reversed(handles):
            kernel32.CloseHandle(handle)


@contextmanager
def request_lock(path: Path, root: Path) -> Iterator[None]:
    """Process-wide, OS-released lock; the persistent file is never deleted (moved from case_finalization)."""
    with _request_lock(path, root, blocking=True):
        yield


@contextmanager
def try_request_lock(path: Path, root: Path) -> Iterator[None]:
    """:func:`request_lock` without waiting: ``FINALIZATION_LOCK_BUSY`` when another holder has it (W2)."""
    with _request_lock(path, root, blocking=False):
        yield


@contextmanager
def _request_lock(path: Path, root: Path, *, blocking: bool) -> Iterator[None]:
    with pin_directory_chain(root, path.parent):
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
                raise SpdmStorageError("FINALIZATION_LOCK_UNAVAILABLE", "최종확정 잠금 파일을 안전하게 열 수 없습니다.")
            info = _LockFileInformation()
            if (not kernel32.GetFileInformationByHandle(file_handle, ctypes.byref(info))
                    or info.dwFileAttributes & (0x10 | 0x400) or info.nNumberOfLinks != 1):
                kernel32.CloseHandle(file_handle)
                raise SpdmStorageError("FINALIZATION_LOCK_UNSAFE", "최종확정 잠금 파일이 일반 단일 연결 파일이 아닙니다.")
            descriptor = msvcrt.open_osfhandle(file_handle, os.O_RDWR)
        else:
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(descriptor)
                raise SpdmStorageError("FINALIZATION_LOCK_UNSAFE", "최종확정 잠금 파일이 일반 단일 연결 파일이 아닙니다.")
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
                    except OSError as exc:
                        if not blocking:
                            raise SpdmStorageError("FINALIZATION_LOCK_BUSY", "다른 작업이 최종확정 잠금을 사용 중입니다.") from exc
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise SpdmStorageError("FINALIZATION_LOCK_BUSY", "다른 작업이 최종확정 잠금을 사용 중입니다.") from exc
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_regular_nofollow(path: Path, *, max_bytes: int,
                          before_read: Callable[[int], None] | None = None) -> bytes | None:
    """Read one small regular file without following links (moved from ``case_finalization._read_json``).

    Returns ``None`` for a link/reparse/non-regular/oversize/changed file or a
    storage error; an exception from ``before_read`` propagates.
    """
    try:
        before = path.lstat()
        attributes = getattr(before, "st_file_attributes", 0)
        if (not stat.S_ISREG(before.st_mode)
                or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            return None

        if os.name == "nt":
            reader = open_stable_reader(path)
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
                    or opened_before.st_size > max_bytes):
                return None
            if before_read is not None:
                before_read(opened_before.st_size)
            payload = stream.read(max_bytes + 1)
            if len(payload) > max_bytes:
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
        return payload
    except (OSError, SpdmStorageError):
        return None


def _publish_no_replace(staged: Path, destination: Path) -> None:
    """Atomically publish a same-directory temporary file without replacement.

    On POSIX the temporary name stays linked; each caller removes it with its
    own (strict or best-effort) cleanup, exactly as before the provider.
    """
    if sys.platform == "win32":
        # SMB deployments need not support hardlinks.  On Windows a same-volume
        # rename fails for an existing destination, unlike POSIX replace().
        if destination.exists():
            raise FileExistsError(destination)
        os.rename(staged, destination)
        return
    os.link(staged, destination)


# ---------------------------------------------------------------------------
# Streaming copy (W2 Final copy rework). Additive: no existing caller changes.
# ---------------------------------------------------------------------------

COPY_CHUNK_BYTES = 8 * 1024 * 1024
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4


class CopyResult(NamedTuple):
    size: int
    sha256: str
    modified_ns: int      # source mtime (ns) seen before and after the copy
    method: str           # "STREAM" (chunked) or "OS_COPY" (server_side_copy seam)


# Seam for a future OS copy (Windows ``CopyFile2``; on one SMB share it lets the
# server copy without the bytes crossing the app server). When set, it is tried
# first: ``server_side_copy(source, destination) -> bool`` must create
# ``destination`` exclusively and return True, or return False (unsupported) so
# the chunked copy runs. The destination is then hashed by reading it back and
# the source must be unchanged (same identity/size/mtime) before and after.
# Not implemented yet (W2); measured and decided in W9.
server_side_copy: Callable[[Path, Path], bool] | None = None


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _regular_no_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISREG(info.st_mode) and not attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _changed() -> SpdmStorageError:
    return SpdmStorageError("SPDM_FILE_CHANGED", "복사하는 동안 원본 파일이 바뀌었습니다.")


@contextmanager
def _open_source_nofollow(path: Path) -> Iterator[Any]:
    """Stable reader that never follows a final-component link (POSIX ``O_NOFOLLOW``)."""
    if os.name == "nt":
        with open_stable_reader(path) as stream:
            yield stream
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError as exc:
        raise SpdmStorageError("SPDM_FILE_MISSING", "원본 파일을 찾을 수 없습니다.") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 경로에 reparse point를 사용할 수 없습니다.") from exc
        raise SpdmStorageError("SPDM_FILE_BUSY", "원본 파일 작성 완료를 기다립니다.") from exc
    with os.fdopen(descriptor, "rb", buffering=0, closefd=True) as stream:
        yield stream


def _lstat_regular(path: Path) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise SpdmStorageError("SPDM_FILE_MISSING", "원본 파일을 찾을 수 없습니다.") from exc
    except OSError as exc:
        raise SpdmStorageError("SPDM_FILE_UNAVAILABLE", "원본 파일을 읽을 수 없습니다.") from exc
    if not _regular_no_reparse(info):
        raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 원본은 일반 reparse 없는 파일이어야 합니다.")
    return info


def _read_through(reader: Any, chunk_size: int, sink: Callable[[memoryview], None]) -> None:
    buffer = bytearray(chunk_size)
    view = memoryview(buffer)
    while True:
        count = reader.readinto(view)
        if not count:
            return
        sink(view[:count])


def _hash_stable(path: Path, *, chunk_size: int, on_progress: Callable[[int], None] | None) -> tuple[int, str, int]:
    before = _lstat_regular(path)
    digest = hashlib.sha256()
    size = 0

    def sink(chunk: memoryview) -> None:
        nonlocal size
        size += len(chunk)
        digest.update(chunk)
        if on_progress is not None:
            on_progress(len(chunk))

    with _open_source_nofollow(path) as reader:
        if _signature(os.fstat(reader.fileno())) != _signature(before):
            raise _changed()
        _read_through(reader, chunk_size, sink)
        opened_after = os.fstat(reader.fileno())
    after = _lstat_regular(path)
    if _signature(opened_after) != _signature(before) or _signature(after) != _signature(before) or size != before.st_size:
        raise _changed()
    return size, digest.hexdigest(), before.st_mtime_ns


def _create_new_file(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, 0o666)


def _copy_chunked(source: Path, destination: Path, before: os.stat_result, *, chunk_size: int,
                  on_progress: Callable[[int], None] | None) -> CopyResult:
    digest = hashlib.sha256()
    size = 0
    created = False
    try:
        with _open_source_nofollow(source) as reader:
            if _signature(os.fstat(reader.fileno())) != _signature(before):
                raise _changed()
            descriptor = _create_new_file(destination)
            created = True
            with os.fdopen(descriptor, "wb", buffering=0, closefd=True) as writer:
                def sink(chunk: memoryview) -> None:
                    nonlocal size
                    size += len(chunk)
                    if size > before.st_size:
                        raise _changed()  # the source grew while it was copied
                    digest.update(chunk)
                    written = 0
                    while written < len(chunk):
                        written += writer.write(chunk[written:]) or 0
                    if on_progress is not None:
                        on_progress(len(chunk))

                _read_through(reader, chunk_size, sink)
                writer.flush()
                os.fsync(writer.fileno())
            opened_after = os.fstat(reader.fileno())
        after = _lstat_regular(source)
        if _signature(opened_after) != _signature(before) or _signature(after) != _signature(before) or size != before.st_size:
            raise _changed()
        return CopyResult(size, digest.hexdigest(), before.st_mtime_ns, "STREAM")
    except BaseException:
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        raise


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Rename a file or directory without ever replacing ``destination`` (``FileExistsError``)."""
    if sys.platform == "win32":
        # MoveFileExW without MOVEFILE_REPLACE_EXISTING refuses an existing destination.
        os.rename(source, destination)
        return
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
    except (OSError, AttributeError):
        renameat2 = None
    if renameat2 is not None:
        at_fdcwd, rename_noreplace = -100, 1
        result = renameat2(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(destination), rename_noreplace)
        if result == 0:
            return
        code = ctypes.get_errno()
        if code not in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise OSError(code, os.strerror(code), str(source), None, str(destination))
    # Filesystems without RENAME_NOREPLACE: POSIX rename() would replace an empty
    # directory, so refuse any existing entry first (callers hold the operation lock).
    if os.path.lexists(destination):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
    os.rename(source, destination)


def _entry_from_stat(name: str, info: os.stat_result, *, followed: bool) -> Entry:
    if stat.S_ISDIR(info.st_mode):
        kind = "dir"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
    else:
        kind = "other"
    attributes = getattr(info, "st_file_attributes", 0)
    is_link = (not followed) and (stat.S_ISLNK(info.st_mode)
                                  or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))
    return Entry(name=name, kind=kind, size=int(info.st_size),
                 modified_ns=int(getattr(info, "st_mtime_ns", info.st_mtime * 1_000_000_000)),
                 item_id=f"{info.st_dev}:{info.st_ino}", etag=None, is_link=is_link)


def _caller_module() -> str:
    frame = sys._getframe(1)
    while frame is not None:
        name = str(frame.f_globals.get("__name__", ""))
        if not (name == _STORAGE_PACKAGE or name.startswith(_STORAGE_PACKAGE + ".") or name == "contextlib"):
            return name
        frame = frame.f_back
    return ""


class LocalFsProvider:
    """``StorageProvider`` over a resolved local/SMB directory.

    Paths are root-relative ``/`` strings (``""`` is the root).  Besides the
    contract methods (§2) it exposes the safety helpers that moved here with
    identical results: ``is_link``, ``assert_safe``, ``case_collision``,
    ``pin``, ``walk``, ``rglob``, ``resolve``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- paths -----------------------------------------------------------
    def path(self, rel_path: str) -> Path:
        """Physical path of a root-relative path (no filesystem access)."""
        if not rel_path or rel_path == ".":
            return self.root
        # Same rule on every platform: no absolute/UNC form, no backslash or drive/stream
        # (``:``) component and no ``..``, so a path can never leave the root.
        if ("\x00" in rel_path or "\\" in rel_path or ":" in rel_path or rel_path.startswith("/")
                or PurePosixPath(rel_path).is_absolute()):
            raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 상대 경로가 올바르지 않습니다.")
        parts = [part for part in rel_path.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 상대 경로가 올바르지 않습니다.")
        return self.root.joinpath(*parts)

    def rel(self, path: Path | str) -> str:
        """Root-relative form of a physical path below the root (no filesystem access)."""
        value = Path(path).relative_to(self.root).as_posix()
        return "" if value == "." else value

    @staticmethod
    def join(rel_path: str, *names: str) -> str:
        parts = [part for part in (rel_path, *names) if part]
        return "/".join(parts)

    # -- identity --------------------------------------------------------
    def root_identity(self) -> str:
        """``st_dev:st_ino:<casefold path>`` — the value stored as ``spdm_storage_settings.root_identity``."""
        return _root_identity(self.root)

    def root_key(self) -> str:
        """sha256 of :meth:`root_identity` (``folder_discovery_*.root_key``)."""
        return hashlib.sha256(self.root_identity().encode("utf-8")).hexdigest()

    # -- reads -----------------------------------------------------------
    def list(self, rel_path: str, *, stat: bool | Literal["files"] = False) -> list[Entry]:
        """All direct entries in ``os.scandir`` order (callers keep their own ordering and limits).

        ``stat=True`` fills size/mtime/id for every entry; ``stat="files"`` only for regular
        files, from the scandir entry (its cached ``lstat``), as the folder scan always did.
        """
        entries: list[Entry] = []
        with os.scandir(self.path(rel_path)) as iterator:
            for item in iterator:
                if stat is True:
                    entries.append(_entry_from_stat(item.name, item.stat(follow_symlinks=False), followed=False))
                    continue
                if item.is_dir(follow_symlinks=False):
                    kind = "dir"
                elif item.is_file(follow_symlinks=False):
                    kind = "file"
                else:
                    kind = "other"
                is_link = item.is_symlink()
                if not is_link and os.name == "nt":
                    attributes = getattr(item.stat(follow_symlinks=False), "st_file_attributes", 0)
                    is_link = bool(attributes & 0x400)
                if stat == "files" and kind == "file" and not is_link:
                    info = item.stat(follow_symlinks=False)
                    entries.append(Entry(name=item.name, kind=kind, size=int(info.st_size),
                                         modified_ns=int(getattr(info, "st_mtime_ns", info.st_mtime * 1_000_000_000)),
                                         item_id=f"{info.st_dev}:{info.st_ino}", etag=None, is_link=False))
                    continue
                entries.append(Entry(name=item.name, kind=kind, size=None, modified_ns=None,
                                     item_id="", etag=None, is_link=is_link))
        return entries

    def stat(self, rel_path: str, *, follow_links: bool = False, missing_ok: bool = True) -> Entry | None:
        path = self.path(rel_path)
        try:
            info = path.stat() if follow_links else path.lstat()
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        return _entry_from_stat(path.name, info, followed=follow_links)

    def exists(self, rel_path: str, *, follow_links: bool = True) -> bool:
        path = self.path(rel_path)
        return path.exists() if follow_links else os.path.lexists(path)

    def is_dir(self, rel_path: str) -> bool:
        return self.path(rel_path).is_dir()

    def is_file(self, rel_path: str) -> bool:
        return self.path(rel_path).is_file()

    def is_link(self, rel_path: str) -> bool:
        """Symlink/reparse check; ``SPDM_PATH_UNAVAILABLE`` when the entry cannot be read."""
        return _is_reparse(self.path(rel_path))

    def assert_safe(self, rel_path: str) -> None:
        """Reject a reparse point/symlink in the root or any existing ancestor."""
        _assert_safe_existing(self.path(rel_path), self.root)

    def case_collision(self, parent_rel: str, name: str) -> str | None:
        """Existing child of ``parent_rel`` whose name equals ``name`` case-insensitively."""
        found = _case_collision(self.path(parent_rel), name)
        return None if found is None else self.join(parent_rel, found.name)

    def resolve(self, rel_path: str) -> str:
        """Strictly resolve and confine to the resolved root (``ValueError`` on escape)."""
        target = self.path(rel_path).resolve(strict=True)
        relative = target.relative_to(self.root.resolve(strict=True)).as_posix()
        return "" if relative == "." else relative

    def walk(self, rel_path: str) -> Iterator[tuple[str, list[str], list[str]]]:
        """``os.walk(followlinks=False)``; pruning ``dirnames`` in place prunes the walk."""
        base = self.path(rel_path)
        for directory, dirnames, filenames in os.walk(base, followlinks=False):
            yield self.rel(directory), dirnames, filenames

    def rglob(self, rel_path: str) -> Iterator[str]:
        """``Path.rglob('*')`` below ``rel_path`` in the same order."""
        for item in self.path(rel_path).rglob("*"):
            yield self.rel(item)

    def read_stable(self, rel_path: str, *, max_bytes: int = 512 * 1024 * 1024) -> bytes:
        return read_stable_bytes(self.path(rel_path), max_bytes=max_bytes)[0]

    def read_stable_digest(self, rel_path: str, *, max_bytes: int = 512 * 1024 * 1024) -> tuple[bytes, str]:
        """:meth:`read_stable` plus the sha256 computed while reading."""
        return read_stable_bytes(self.path(rel_path), max_bytes=max_bytes)

    def open_read(self, rel_path: str):
        """Context manager yielding a stable binary reader (``SPDM_FILE_BUSY`` when excluded)."""
        return open_stable_reader(self.path(rel_path))

    def read_small_nofollow(self, rel_path: str, *, max_bytes: int,
                            before_read: Callable[[int], None] | None = None) -> bytes | None:
        return read_regular_nofollow(self.path(rel_path), max_bytes=max_bytes, before_read=before_read)

    def pin(self, rel_path: str):
        """Context manager pinning ``rel_path`` and its ancestors (Windows); POSIX checks links."""
        return pin_directory_chain(self.root, self.path(rel_path))

    def changes(self, rel_path: str, since: str | None) -> None:
        return None

    # -- W2 Final copy rework (additive) ----------------------------------
    def list_detailed(self, rel_path: str) -> list[tuple[Entry, int]]:
        """Direct entries with ``lstat`` data and Windows file attributes (0 elsewhere)."""
        result: list[tuple[Entry, int]] = []
        with os.scandir(self.path(rel_path)) as iterator:
            for item in iterator:
                info = item.stat(follow_symlinks=False)
                result.append((_entry_from_stat(item.name, info, followed=False),
                               int(getattr(info, "st_file_attributes", 0))))
        return result

    def free_bytes(self, rel_path: str) -> int:
        """Free bytes on the volume holding ``rel_path`` (``shutil.disk_usage``)."""
        return int(shutil.disk_usage(self.path(rel_path)).free)

    def hash_stable(self, rel_path: str, *, chunk_size: int | None = None,
                    on_progress: Callable[[int], None] | None = None) -> tuple[int, str]:
        """(size, sha256) streamed in chunks; ``SPDM_FILE_CHANGED`` when the file changes while read."""
        path = self.path(rel_path)
        with pin_directory_chain(self.root, path.parent):
            _assert_safe_existing(path, self.root)
            size, digest, _modified = _hash_stable(path, chunk_size=int(chunk_size or COPY_CHUNK_BYTES),
                                                   on_progress=on_progress)
        return size, digest

    def copy_stream(self, src_rel: str, dst_rel: str, *, zone: str, chunk_size: int | None = None,
                    on_progress: Callable[[int], None] | None = None) -> CopyResult:
        """Copy one file into a new ``dst_rel`` in constant memory, hashing while copying.

        Same safety as the stable reads: no link/reparse source (``O_NOFOLLOW`` / handle
        attributes), both parent chains pinned, the source identity/size/mtime must be
        unchanged before, during (no growth) and after the copy (``SPDM_FILE_CHANGED``).
        The destination is created exclusively (``FileExistsError``), fsynced, and removed
        again on any failure. ``server_side_copy`` (module seam) is tried first when set.
        """
        self._refuse_working(zone)
        self._check(zone, dst_rel)
        source = self.path(src_rel)
        destination = self.path(dst_rel)
        chunk = int(chunk_size or COPY_CHUNK_BYTES)
        with pin_directory_chain(self.root, source.parent), pin_directory_chain(self.root, destination.parent):
            _assert_safe_existing(source, self.root)
            _assert_safe_existing(destination.parent, self.root)
            before = _lstat_regular(source)
            if os.path.lexists(destination):
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
            hook = server_side_copy
            if hook is not None:
                copied = False
                try:
                    copied = bool(hook(source, destination))
                except OSError:
                    copied = False
                if copied:
                    try:
                        size, digest, _modified = _hash_stable(destination, chunk_size=chunk, on_progress=on_progress)
                        after = _lstat_regular(source)
                        if _signature(after) != _signature(before) or size != before.st_size:
                            raise _changed()
                        return CopyResult(size, digest, before.st_mtime_ns, "OS_COPY")
                    except BaseException:
                        try:
                            destination.unlink()
                        except OSError:
                            pass
                        raise
                if os.path.lexists(destination):
                    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
            return _copy_chunked(source, destination, before, chunk_size=chunk, on_progress=on_progress)

    def rename_no_replace(self, src_rel: str, dst_rel: str, *, zone: str) -> None:
        """Rename a file or folder (same volume) without replacing ``dst_rel`` (``FileExistsError``).

        Both parent chains are pinned; the moved entry itself is not (Windows refuses to
        rename a folder while a handle without delete sharing is open on it). A Windows
        sharing violation surfaces as ``PermissionError``; callers retry with backoff.
        """
        self._check(zone, src_rel, dst_rel)
        source = self.path(src_rel)
        destination = self.path(dst_rel)
        with pin_directory_chain(self.root, source.parent), pin_directory_chain(self.root, destination.parent):
            _assert_safe_existing(source.parent, self.root)
            _assert_safe_existing(destination.parent, self.root)
            if _is_reparse(source):
                raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 경로에 reparse point를 사용할 수 없습니다.")
            _rename_no_replace(source, destination)

    def append_bytes(self, rel_path: str, data: bytes, *, zone: str, fsync: bool = False) -> None:
        """Append to a regular single-link file (created when absent), never through a link.

        The parent chain is pinned and re-checked; the opened handle must be the same file
        as the path's own ``lstat`` entry (no symlink/reparse, same id), which also covers
        Windows where ``O_NOFOLLOW`` does not exist (re-review N2).
        """
        self._refuse_working(zone)
        self._check(zone, rel_path)
        path = self.path(rel_path)
        unsafe = SpdmStorageError("FINALIZATION_PATH_UNSAFE", "최종확정 기록 파일이 일반 단일 연결 파일이 아닙니다.")
        with pin_directory_chain(self.root, path.parent):
            _assert_safe_existing(path.parent, self.root)
            if os.path.lexists(path) and not _regular_no_reparse(path.lstat()):
                raise unsafe
            flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
            descriptor = os.open(path, flags, 0o666)
            try:
                opened = os.fstat(descriptor)
                entry = path.lstat()
                if (not _regular_no_reparse(opened) or not _regular_no_reparse(entry) or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)):
                    raise unsafe
                view = memoryview(data)
                while view:
                    view = view[os.write(descriptor, view):]
                if fsync:
                    os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def try_lock(self, rel_path: str, *, zone: str):
        """:meth:`lock` without waiting (``FINALIZATION_LOCK_BUSY``)."""
        self._refuse_working(zone)
        self._check(zone, rel_path)
        return try_request_lock(self.path(rel_path), self.root)

    # -- W8 result drop upload (additive) ---------------------------------
    def mkdir_pinned(self, rel_path: str, *, zone: str) -> bool:
        """Create one folder below a pinned, re-checked parent chain; ``True`` when it was created.

        An existing plain folder is accepted (``False``); an existing file, link or reparse
        point is refused (``SPDM_PATH_UNSAFE``). Parents are never created implicitly.
        """
        self._check(zone, rel_path)
        path = self.path(rel_path)
        with pin_directory_chain(self.root, path.parent):
            _assert_safe_existing(path.parent, self.root)
            if _is_reparse(path.parent):
                raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 경로에 reparse point를 사용할 수 없습니다.")
            try:
                path.mkdir()
            except FileExistsError:
                info = path.lstat()
                attributes = getattr(info, "st_file_attributes", 0)
                if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                    raise SpdmStorageError("SPDM_PATH_UNSAFE", "같은 이름의 파일이나 연결 항목이 있어 폴더를 만들 수 없습니다.")
                return False
        return True

    def set_hidden(self, rel_path: str, *, zone: str) -> None:
        """Best-effort Windows hidden attribute (no-op elsewhere); never raises."""
        self._check(zone, rel_path)
        if os.name != "nt":
            return
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(self.path(rel_path)), FILE_ATTRIBUTE_HIDDEN)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    def write_chunk(self, rel_path: str, offset: int, data: bytes, *, zone: str, fsync: bool = True) -> int:
        """Write ``data`` at ``offset`` of a regular single-link file and return the new size.

        ``offset == 0`` on a missing file creates it exclusively; otherwise the file must
        exist and its size must equal ``offset`` (``SPDM_CHUNK_OFFSET``), so a chunk is
        never written twice or with a gap. The parent chain is pinned and re-checked and
        the opened handle must be the path's own ``lstat`` entry (no link/reparse swap).
        A failed write truncates the file back to ``offset``.
        """
        self._check(zone, rel_path)
        if offset < 0:
            raise SpdmStorageError("SPDM_CHUNK_OFFSET", "조각 위치가 올바르지 않습니다.")
        path = self.path(rel_path)
        unsafe = SpdmStorageError("SPDM_PATH_UNSAFE", "임시 업로드 파일이 일반 단일 연결 파일이 아닙니다.")
        base = os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with pin_directory_chain(self.root, path.parent):
            _assert_safe_existing(path.parent, self.root)
            exists = os.path.lexists(path)
            if exists and not _regular_no_reparse(path.lstat()):
                raise unsafe
            if not exists and offset != 0:
                raise SpdmStorageError("SPDM_CHUNK_OFFSET", "임시 업로드 파일이 없어 처음부터 다시 보내야 합니다.")
            descriptor = os.open(path, base | (0 if exists else os.O_CREAT | os.O_EXCL), 0o666)
            try:
                opened = os.fstat(descriptor)
                entry = path.lstat()
                if (not _regular_no_reparse(opened) or not _regular_no_reparse(entry) or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)):
                    raise unsafe
                if opened.st_size != offset:
                    raise SpdmStorageError("SPDM_CHUNK_OFFSET", "임시 업로드 파일 크기가 조각 위치와 다릅니다.")
                os.lseek(descriptor, offset, os.SEEK_SET)
                try:
                    view = memoryview(data)
                    while view:
                        view = view[os.write(descriptor, view):]
                    if fsync:
                        os.fsync(descriptor)
                except BaseException:
                    try:
                        os.ftruncate(descriptor, offset)
                    except OSError:
                        pass
                    raise
                return offset + len(data)
            finally:
                os.close(descriptor)

    # -- writes (zone-checked, S3) ---------------------------------------
    @staticmethod
    def _refuse_working(zone: str) -> None:
        """W8 review L2: the WORKING zone only creates folders (``mkdir_pinned``), writes staged chunks
        (``write_chunk``), publishes without replacing (``rename_no_replace``), removes its own staging
        (``remove``) and sets the hidden attribute; every other write primitive refuses it."""
        if zone == WORKING:
            raise StorageError(NOT_ALLOWED_WRITE, "허용된 SPDM 쓰기 구역 밖의 경로입니다.")

    def _check(self, zone: str, *rel_paths: str) -> None:
        caller = _caller_module()
        for rel_path in rel_paths:
            check_write(rel_path, zone, caller)

    def mkdirs(self, rel_path: str, *, zone: str, parents: bool = True, exist_ok: bool = True) -> None:
        self._refuse_working(zone)
        self._check(zone, rel_path)
        self.path(rel_path).mkdir(parents=parents, exist_ok=exist_ok)

    def create_exclusive(self, rel_path: str, data: bytes | Iterable[bytes], *, zone: str,
                         fsync: bool = True, private: bool = False) -> None:
        """Create a new file (``'xb'``); ``private`` uses ``O_NOFOLLOW`` and mode 0600.

        An exception raised by ``data`` while writing propagates and leaves the
        partial file for the caller's cleanup, as before.
        """
        self._refuse_working(zone)
        self._check(zone, rel_path)
        path = self.path(rel_path)
        if private:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                flags |= os.O_BINARY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            stream_context = os.fdopen(descriptor, "wb")
        else:
            stream_context = path.open("xb")
        with stream_context as stream:
            if isinstance(data, (bytes, bytearray, memoryview)):
                stream.write(data)
            else:
                for chunk in data:
                    stream.write(chunk)
            stream.flush()
            if fsync:
                os.fsync(stream.fileno())
        return None

    def move_no_overwrite(self, src_rel: str, dst_rel: str, *, zone: str) -> None:
        """Publish ``src`` as ``dst`` without replacing; ``FileExistsError`` when ``dst`` exists.

        POSIX hardlinks (``src`` remains; the caller removes it), Windows renames.
        """
        self._refuse_working(zone)
        self._check(zone, src_rel, dst_rel)
        _publish_no_replace(self.path(src_rel), self.path(dst_rel))

    def _guarded_parents(self, *paths: Path):
        """FINAL zone (W2 review H1): pin each existing parent chain and re-check it inside the pin.

        Windows: the pinned handles refuse a rename/junction swap of any ancestor until the
        operation is done. POSIX: the ancestors are re-checked for links (a swap between the
        check and the call remains possible there; Windows is the target platform).
        """
        stack = ExitStack()
        try:
            for parent in dict.fromkeys(path.parent for path in paths):
                if not os.path.lexists(parent):
                    continue
                stack.enter_context(pin_directory_chain(self.root, parent))
                _assert_safe_existing(parent, self.root)
                if _is_reparse(parent):
                    raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 경로에 reparse point를 사용할 수 없습니다.")
        except BaseException:
            stack.close()
            raise
        return stack

    def replace(self, src_rel: str, dst_rel: str, *, zone: str) -> None:
        self._refuse_working(zone)
        self._check(zone, src_rel, dst_rel)
        source, destination = self.path(src_rel), self.path(dst_rel)
        if zone != FINAL:
            os.replace(source, destination)
            return
        with self._guarded_parents(source, destination):
            if os.path.lexists(destination) and (_is_reparse(destination) or os.path.isdir(destination)):
                raise SpdmStorageError("SPDM_PATH_UNSAFE", "교체할 대상이 일반 파일이 아닙니다.")
            os.replace(source, destination)

    def remove(self, rel_path: str, *, zone: str, directory: bool = False, missing_ok: bool = False) -> None:
        self._check(zone, rel_path)
        path = self.path(rel_path)
        if zone not in {FINAL, WORKING}:
            if directory:
                path.rmdir()
            else:
                path.unlink(missing_ok=missing_ok)
            return
        with self._guarded_parents(path):
            if directory:
                if _is_reparse(path):
                    raise SpdmStorageError("SPDM_PATH_UNSAFE", "SPDM 경로에 reparse point를 사용할 수 없습니다.")
                path.rmdir()
            else:
                path.unlink(missing_ok=missing_ok)

    def lock(self, rel_path: str, *, zone: str):
        """Exclusive request lock on a persistent lock file (created when absent)."""
        self._refuse_working(zone)
        self._check(zone, rel_path)
        return request_lock(self.path(rel_path), self.root)


def provider_for_root(root: Path) -> LocalFsProvider:
    return LocalFsProvider(root)


__all__ = [
    "COPY_CHUNK_BYTES", "CopyResult", "FINAL", "LEGACY", "LocalFsProvider", "StorageError", "SpdmStorageError",
    "open_stable_reader", "pin_directory_chain", "prepare_root", "provider_for_root", "read_regular_nofollow",
    "read_stable_bytes", "request_lock", "resolve_existing_or_none", "resolve_root", "server_side_copy",
    "try_request_lock",
]
