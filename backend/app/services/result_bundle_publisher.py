"""Publish a captured canonical result bundle with a no-replace commit.

The publisher has deliberately small responsibilities.  ``capture_bundle``
owns traversal of the producer's directory and returns immutable byte
metadata; this module copies those bytes into a private sibling staging
directory and makes that directory visible with one Linux ``renameat2`` call.
There is no copy/replace fallback: a publication is either committed or it is
not visible at all.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from ..config import import_bundle_limits
from ..folder_import import FolderImportError, scan_folder
from . import bundle_snapshot
from .bundle_fingerprint import FingerprintEntry
from .canonical_result_bundle import (
    READY_MARKER_NAME,
    STAGING_DIRECTORY_PREFIX,
    ReadyMarkerError,
    build_ready_marker,
    canonical_bundle_relative,
    serialize_ready_marker,
)


_COPY_CHUNK_BYTES = 1024 * 1024
_RENAME_NOREPLACE = 1


class ResultBundlePublishError(ValueError):
    """A stable, safe error returned by the service and CLI.

    Messages intentionally contain no paths or operating-system exception
    text.  Paths are supplied by callers and therefore must never be echoed
    back in a command-line error response.
    """

    def __init__(self, code: str, message: str = "결과 bundle publication을 완료할 수 없습니다.") -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PublishedResultBundle:
    """The only data exposed after publication (all paths are relative)."""

    bundle_path: str
    manifest_checksum: str
    bundle_fingerprint: str
    entry_count: int

    def __post_init__(self) -> None:
        if PurePosixPath(self.bundle_path).is_absolute() or "\\" in self.bundle_path:
            raise ValueError("bundle_path는 상대 POSIX 경로여야 합니다.")

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle_path": self.bundle_path,
            "manifest_checksum": self.manifest_checksum,
            "bundle_fingerprint": self.bundle_fingerprint,
            "entry_count": self.entry_count,
        }


def _error_from_snapshot(error: bundle_snapshot.BundleSnapshotError) -> ResultBundlePublishError:
    return ResultBundlePublishError(error.code)


def _require_directory(path: Path, code: str) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ResultBundlePublishError(code) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ResultBundlePublishError(code)


def _reject_overlapping_roots(source: Path, import_root: Path) -> None:
    try:
        source_physical = source.resolve(strict=True)
        root_physical = import_root.resolve(strict=True)
    except OSError as exc:
        raise ResultBundlePublishError("RESULT_BUNDLE_ROOTS_UNSAFE") from exc
    if (
        source_physical == root_physical
        or source_physical in root_physical.parents
        or root_physical in source_physical.parents
    ):
        raise ResultBundlePublishError("RESULT_BUNDLE_ROOTS_OVERLAP")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_directory(path: Path, code: str) -> int:
    try:
        return os.open(str(path), _directory_flags())
    except OSError as exc:
        raise ResultBundlePublishError(code) from exc


def _open_child_directory(parent_fd: int, name: str, *, code: str) -> int:
    try:
        return os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise ResultBundlePublishError(code) from exc


def _mkdir_and_open(parent_fd: int, name: str, *, code: str) -> int:
    created = False
    try:
        os.mkdir(name, 0o755, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise ResultBundlePublishError(code) from exc
    if created:
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            raise ResultBundlePublishError("RESULT_BUNDLE_DIRECTORY_FSYNC_FAILED") from exc
    descriptor = _open_child_directory(parent_fd, name, code=code)
    try:
        # A newly-created directory must reach stable storage before the
        # publication can be made visible.
        os.fsync(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise ResultBundlePublishError("RESULT_BUNDLE_DIRECTORY_FSYNC_FAILED") from exc
    return descriptor


def _open_publication_parent(
    import_root: Path,
    relative: str,
    *,
    create: bool,
) -> tuple[int | None, str, tuple[int, ...]]:
    """Open the load-case parent and return ``(fd, final_name, new_fds)``."""

    parts = tuple(PurePosixPath(relative).parts)
    if len(parts) != 4 or any(part in {"", ".", ".."} for part in parts):
        raise ResultBundlePublishError("RESULT_BUNDLE_PATH_INVALID")
    root_fd = _open_directory(import_root, "RESULT_BUNDLE_IMPORT_ROOT_UNSAFE")
    opened: list[int] = [root_fd]
    try:
        current = root_fd
        for part in parts[:-1]:
            try:
                child = os.open(part, _directory_flags(), dir_fd=current)
            except FileNotFoundError:
                if not create:
                    # A check-only publication may target a not-yet-created
                    # parent.  No writes have happened, so report no existing
                    # final and let the caller validate the remaining path.
                    return None, parts[-1], tuple(opened)
                child = _mkdir_and_open(current, part, code="RESULT_BUNDLE_IMPORT_ROOT_UNSAFE")
            except OSError as exc:
                raise ResultBundlePublishError("RESULT_BUNDLE_IMPORT_ROOT_UNSAFE") from exc
            opened.append(child)
            current = child
        return current, parts[-1], tuple(opened)
    except BaseException:
        for descriptor in reversed(opened):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _close_descriptors(descriptors: tuple[int, ...]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _ensure_no_final(parent_fd: int, final_name: str) -> None:
    try:
        os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ResultBundlePublishError("RESULT_BUNDLE_FINAL_UNSAFE") from exc
    raise ResultBundlePublishError("RESULT_BUNDLE_FINAL_EXISTS")


def _clock_text(clock: Callable[[], object]) -> str:
    try:
        value = clock()
    except Exception as exc:
        raise ResultBundlePublishError("RESULT_BUNDLE_CLOCK_INVALID") from exc
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ResultBundlePublishError("RESULT_BUNDLE_CLOCK_INVALID")
        value = value.astimezone(UTC)
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")
    if isinstance(value, str):
        return value
    raise ResultBundlePublishError("RESULT_BUNDLE_CLOCK_INVALID")


def _new_stage(parent_fd: int, publication_id: str) -> str:
    for _attempt in range(5):
        name = f"{STAGING_DIRECTORY_PREFIX}{publication_id}-{uuid.uuid4().hex}"
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
            return name
        except FileExistsError:
            continue
        except OSError as exc:
            raise ResultBundlePublishError("RESULT_BUNDLE_STAGE_CREATE_FAILED") from exc
    raise ResultBundlePublishError("RESULT_BUNDLE_STAGE_CREATE_FAILED")


def _stage_directory(parent_fd: int, stage_name: str) -> int:
    return _open_child_directory(parent_fd, stage_name, code="RESULT_BUNDLE_STAGE_CREATE_FAILED")


def _copy_snapshot_file(snapshot_root: Path, stage_fd: int, entry: FingerprintEntry) -> None:
    parts = tuple(PurePosixPath(entry.relative_path).parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ResultBundlePublishError("RESULT_BUNDLE_SNAPSHOT_UNSAFE")
    current = os.dup(stage_fd)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, _directory_flags(), dir_fd=current)
            except FileNotFoundError:
                child = _mkdir_and_open(current, part, code="RESULT_BUNDLE_STAGE_CREATE_FAILED")
            except OSError as exc:
                raise ResultBundlePublishError("RESULT_BUNDLE_STAGE_CREATE_FAILED") from exc
            os.close(current)
            current = child
        source = snapshot_root.joinpath(*parts)
        source_fd: int | None = None
        target_fd: int | None = None
        try:
            source_fd = os.open(str(source), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            target_fd = os.open(
                parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o644,
                dir_fd=current,
            )
        except OSError as exc:
            if source_fd is not None:
                os.close(source_fd)
            raise ResultBundlePublishError("RESULT_BUNDLE_SNAPSHOT_UNSAFE") from exc
        try:
            copied = 0
            digest = hashlib.sha256()
            while True:
                chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
                if not chunk:
                    break
                _write_all(target_fd, chunk, "RESULT_BUNDLE_PAYLOAD_WRITE_FAILED")
                copied += len(chunk)
                digest.update(chunk)
            if copied != entry.size or digest.hexdigest() != entry.sha256:
                raise ResultBundlePublishError("RESULT_BUNDLE_SNAPSHOT_CHANGED")
            os.fsync(target_fd)
            # Persist the directory entry after the payload itself is durable.
            # This matters for nested mappings where ``current`` is not stage_fd.
            os.fsync(current)
        except OSError as exc:
            raise ResultBundlePublishError("RESULT_BUNDLE_PAYLOAD_WRITE_FAILED") from exc
        finally:
            assert source_fd is not None and target_fd is not None
            os.close(source_fd)
            os.close(target_fd)
    finally:
        os.close(current)


def _write_marker(stage_fd: int, marker_bytes: bytes) -> None:
    try:
        marker_fd = os.open(
            READY_MARKER_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o644,
            dir_fd=stage_fd,
        )
    except OSError as exc:
        raise ResultBundlePublishError("RESULT_BUNDLE_MARKER_WRITE_FAILED") from exc
    try:
        _write_all(marker_fd, marker_bytes, "RESULT_BUNDLE_MARKER_WRITE_FAILED")
        os.fsync(marker_fd)
    except OSError as exc:
        raise ResultBundlePublishError("RESULT_BUNDLE_MARKER_WRITE_FAILED") from exc
    finally:
        os.close(marker_fd)
    try:
        os.fsync(stage_fd)
    except OSError as exc:
        raise ResultBundlePublishError("RESULT_BUNDLE_STAGE_FSYNC_FAILED") from exc


def _write_all(fd: int, payload: bytes, code: str) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(fd, payload[offset:])
        except OSError as exc:
            raise ResultBundlePublishError(code) from exc
        if written <= 0:
            raise ResultBundlePublishError(code)
        offset += written


def _rename_noreplace(parent_fd: int, stage_name: str, final_name: str) -> None:
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise ResultBundlePublishError("RESULT_BUNDLE_RENAME_UNSUPPORTED")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        raise ResultBundlePublishError("RESULT_BUNDLE_RENAME_UNSUPPORTED") from exc
    result = renameat2(
        parent_fd,
        os.fsencode(stage_name),
        parent_fd,
        os.fsencode(final_name),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        raise ResultBundlePublishError("RESULT_BUNDLE_FINAL_EXISTS")
    if code == errno.EXDEV:
        raise ResultBundlePublishError("RESULT_BUNDLE_RENAME_CROSS_DEVICE")
    if code in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
        raise ResultBundlePublishError("RESULT_BUNDLE_RENAME_UNSUPPORTED")
    raise ResultBundlePublishError("RESULT_BUNDLE_RENAME_FAILED")


def _map_rename_oserror(error: OSError) -> ResultBundlePublishError:
    if error.errno == errno.EEXIST:
        return ResultBundlePublishError("RESULT_BUNDLE_FINAL_EXISTS")
    if error.errno == errno.EXDEV:
        return ResultBundlePublishError("RESULT_BUNDLE_RENAME_CROSS_DEVICE")
    if error.errno in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
        return ResultBundlePublishError("RESULT_BUNDLE_RENAME_UNSUPPORTED")
    return ResultBundlePublishError("RESULT_BUNDLE_RENAME_FAILED")


def _cleanup_stage(parent_fd: int, stage_name: str) -> None:
    # The name was generated by this call and is never a caller-supplied path.
    try:
        stage_path = Path(f"/proc/self/fd/{parent_fd}") / stage_name
        shutil.rmtree(stage_path)
    except OSError:
        try:
            os.unlink(stage_name, dir_fd=parent_fd)
        except OSError:
            pass


def _result(bundle_path: str, bundle: Any) -> PublishedResultBundle:
    return PublishedResultBundle(
        bundle_path=bundle_path,
        manifest_checksum=bundle.manifest_checksum,
        bundle_fingerprint=bundle.bundle_fingerprint,
        entry_count=len(bundle.entries),
    )


def publish_result_bundle(
    source_bundle: Path,
    import_root: Path,
    publication_id: str,
    *,
    check_only: bool = False,
    clock: Callable[[], object] = lambda: datetime.now(UTC),
) -> PublishedResultBundle:
    """Validate and atomically publish one canonical result bundle."""

    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise ResultBundlePublishError("RESULT_BUNDLE_PLATFORM_UNSUPPORTED")
    source = Path(source_bundle)
    root = Path(import_root)
    _require_directory(source, "RESULT_BUNDLE_SOURCE_UNSAFE")
    _require_directory(root, "RESULT_BUNDLE_IMPORT_ROOT_UNSAFE")
    _reject_overlapping_roots(source, root)
    limits = import_bundle_limits()
    captured = None
    try:
        # Both modes use the same descriptor-relative private snapshot and the
        # same semantic importer boundary.  ``check_only`` only suppresses
        # writes below import_root; TemporaryDirectory is private state.
        captured = bundle_snapshot.capture_bundle(source, "manifest.json", limits)
        scan_folder(captured.bundle_root, limits=limits)
        relative = canonical_bundle_relative(captured.manifest, publication_id)
    except ResultBundlePublishError:
        if captured is not None:
            captured.cleanup()
        raise
    except bundle_snapshot.BundleSnapshotError as exc:
        if captured is not None:
            captured.cleanup()
        raise _error_from_snapshot(exc) from exc
    except ReadyMarkerError as exc:
        if captured is not None:
            captured.cleanup()
        raise ResultBundlePublishError(exc.code) from exc
    except FolderImportError as exc:
        if captured is not None:
            captured.cleanup()
        raise ResultBundlePublishError(exc.code or "RESULT_BUNDLE_SOURCE_INVALID") from exc
    except ValueError as exc:
        if captured is not None:
            captured.cleanup()
        raise ResultBundlePublishError("RESULT_BUNDLE_METADATA_INVALID") from exc
    except Exception as exc:
        if captured is not None:
            captured.cleanup()
        raise ResultBundlePublishError("RESULT_BUNDLE_SOURCE_INVALID") from exc

    marker_bytes: bytes | None = None
    if check_only:
        try:
            marker = build_ready_marker(
                bundle_path=relative,
                manifest_checksum=captured.manifest_checksum,
                bundle_fingerprint=captured.bundle_fingerprint,
                entry_count=len(captured.entries),
                published_at=_clock_text(clock),
            )
            marker_bytes = serialize_ready_marker(marker)
        except Exception as exc:
            captured.cleanup()
            raise ResultBundlePublishError("RESULT_BUNDLE_MARKER_INVALID") from exc
        del marker_bytes
        try:
            parent_fd, final_name, descriptors = _open_publication_parent(root, relative, create=False)
            try:
                if parent_fd is not None:
                    _ensure_no_final(parent_fd, final_name)
            finally:
                _close_descriptors(descriptors)
            result = _result(relative, captured)
        finally:
            captured.cleanup()
        return result

    captured_bundle = captured
    try:
        parent_fd, final_name, descriptors = _open_publication_parent(root, relative, create=True)
    except BaseException:
        captured_bundle.cleanup()
        raise
    stage_name: str | None = None
    renamed = False
    try:
        _ensure_no_final(parent_fd, final_name)
        stage_name = _new_stage(parent_fd, publication_id)
        stage_fd = _stage_directory(parent_fd, stage_name)
        try:
            for entry in captured_bundle.entries:
                _copy_snapshot_file(captured_bundle.bundle_root, stage_fd, entry)
            marker = build_ready_marker(
                bundle_path=relative,
                manifest_checksum=captured_bundle.manifest_checksum,
                bundle_fingerprint=captured_bundle.bundle_fingerprint,
                entry_count=len(captured_bundle.entries),
                published_at=_clock_text(clock),
            )
            _write_marker(stage_fd, serialize_ready_marker(marker))
        finally:
            os.close(stage_fd)
        try:
            _rename_noreplace(parent_fd, stage_name, final_name)
        except OSError as exc:
            raise _map_rename_oserror(exc) from exc
        renamed = True
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            # The directory is already visible; report the durability failure
            # without deleting or replacing the committed final.
            raise ResultBundlePublishError("RESULT_BUNDLE_PARENT_FSYNC_FAILED") from exc
        return _result(relative, captured_bundle)
    except ResultBundlePublishError:
        if stage_name is not None and not renamed:
            _cleanup_stage(parent_fd, stage_name)
        raise
    except OSError as exc:
        if stage_name is not None and not renamed:
            _cleanup_stage(parent_fd, stage_name)
        raise ResultBundlePublishError("RESULT_BUNDLE_PUBLISH_FAILED") from exc
    except Exception as exc:
        if stage_name is not None and not renamed:
            _cleanup_stage(parent_fd, stage_name)
        raise ResultBundlePublishError("RESULT_BUNDLE_PUBLISH_FAILED") from exc
    finally:
        _close_descriptors(descriptors)
        captured_bundle.cleanup()
