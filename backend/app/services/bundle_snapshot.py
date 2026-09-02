"""Capture a canonical result bundle into a private immutable staging area.

The master result folder may be written by an external producer while a refresh
is running.  This module opens every source component descriptor-relatively
with ``O_NOFOLLOW``, copies and hashes the bytes once, then exposes only the
private snapshot to parsers and persistence.  Fingerprints therefore describe
the exact bytes that ``scan_folder`` and media storage consume.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, Iterator

from ..config import ImportBundleLimits
from ..parsers.manifest_format import ManifestFormat, ManifestFormatError, load_manifest
from .bundle_fingerprint import FingerprintEntry, calculate_bundle_fingerprint
from .canonical_result_bundle import (
    READY_MARKER_MAX_BYTES,
    READY_MARKER_NAME,
    ReadyMarkerError,
    ReadyMarkerV1,
    canonical_bundle_relative,
    parse_ready_marker_bytes,
    verify_ready_marker,
)
from .import_snapshot_workspace import ImportSnapshotWorkspace, ImportSnapshotWorkspaceError


_COPY_CHUNK_BYTES = 1024 * 1024


class BundleSnapshotError(ValueError):
    """A safe, categorized capture failure suitable for a refresh response."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.manifest = manifest
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class CapturedBundle:
    """A private on-disk copy and immutable metadata for one result bundle."""

    bundle_root: Path
    manifest: dict[str, Any]
    manifest_checksum: str
    bundle_fingerprint: str
    entries: tuple[FingerprintEntry, ...]
    _temporary_directory: TemporaryDirectory[str]

    def __enter__(self) -> "CapturedBundle":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        self._temporary_directory.cleanup()


def capture_bundle(
    import_root: Path,
    manifest_relative_path: str,
    limits: ImportBundleLimits,
    *,
    workspace: ImportSnapshotWorkspace | None = None,
) -> CapturedBundle:
    """Securely copy one canonical bundle and return its private snapshot.

    ``manifest_relative_path`` is discovery-relative to ``import_root``.  It
    is never resolved as a live ``Path`` after discovery: source traversal is
    descriptor-relative and every directory/file component rejects symlinks.
    The returned object must be used as a context manager so the temporary
    snapshot is removed after parsing and persistence complete.
    """

    return _capture_bundle(
        import_root,
        manifest_relative_path,
        limits,
        require_ready_marker=False,
        workspace=workspace,
    )


def capture_published_bundle(
    import_root: Path,
    manifest_relative_path: str,
    limits: ImportBundleLimits,
    *,
    workspace: ImportSnapshotWorkspace | None = None,
) -> CapturedBundle:
    """Capture only a completed, canonically published result bundle.

    The readiness marker is copied and parsed from the same already-open
    bundle descriptor as the manifest and mapping bytes.  It is intentionally
    not a fingerprint entry: publishing metadata must not change result-byte
    idempotency.
    """

    return _capture_bundle(
        import_root,
        manifest_relative_path,
        limits,
        require_ready_marker=True,
        workspace=workspace,
    )


def _capture_bundle(
    import_root: Path,
    manifest_relative_path: str,
    limits: ImportBundleLimits,
    *,
    require_ready_marker: bool,
    workspace: ImportSnapshotWorkspace | None,
) -> CapturedBundle:
    _require_secure_traversal()
    manifest_parts = _relative_parts(manifest_relative_path, "BUNDLE_MANIFEST_PATH_INVALID")
    if manifest_parts[-1] != "manifest.json":
        raise BundleSnapshotError(
            "BUNDLE_MANIFEST_PATH_INVALID",
            "발견된 결과 manifest 경로가 올바르지 않습니다.",
        )

    try:
        temporary_directory = (
            workspace.create_temporary_directory()
            if workspace is not None
            else TemporaryDirectory(prefix="simdashboard-result-bundle-")
        )
    except ImportSnapshotWorkspaceError as exc:
        raise BundleSnapshotError(exc.code, str(exc), manifest=None) from exc
    try:
        snapshot_root = Path(temporary_directory.name)
        with _open_import_root(import_root) as root_fd:
            with _open_directory_at(root_fd, manifest_parts[:-1]) as bundle_fd:
                ready_marker: ReadyMarkerV1 | None = None
                if require_ready_marker:
                    _copy_relative_file(
                        bundle_fd,
                        (READY_MARKER_NAME,),
                        snapshot_root,
                        READY_MARKER_MAX_BYTES,
                        READY_MARKER_MAX_BYTES,
                        0,
                        code_prefix="READY_MARKER",
                        missing_code="BUNDLE_READY_MARKER_MISSING",
                    )
                    try:
                        ready_marker = parse_ready_marker_bytes(
                            (snapshot_root / READY_MARKER_NAME).read_bytes()
                        )
                    except ReadyMarkerError as exc:
                        raise BundleSnapshotError(exc.code, str(exc)) from exc
                manifest_entry = _copy_relative_file(
                    bundle_fd,
                    (manifest_parts[-1],),
                    snapshot_root,
                    limits.max_manifest_bytes,
                    limits.max_total_bytes,
                    0,
                    code_prefix="MANIFEST",
                )

                snapshot_manifest_path = snapshot_root / "manifest.json"
                try:
                    manifest = load_manifest(
                        snapshot_manifest_path,
                        expected_format=ManifestFormat.CANONICAL_MAPPINGS,
                        max_bytes=limits.max_manifest_bytes,
                        max_mapping_count=limits.max_mapping_count,
                    ).data
                except ManifestFormatError as exc:
                    code = {
                        "MANIFEST_MAPPING_COUNT_LIMIT": "BUNDLE_MAPPING_COUNT_LIMIT",
                        "MANIFEST_SIZE_LIMIT": "BUNDLE_MANIFEST_FILE_LIMIT",
                        "MANIFEST_COMPLEXITY_LIMIT": "BUNDLE_MANIFEST_COMPLEXITY_LIMIT",
                    }.get(exc.code, exc.code)
                    raise BundleSnapshotError(
                        code,
                        "canonical manifest를 읽을 수 없습니다.",
                        manifest=exc.data,
                    ) from exc

                mappings = manifest.get("mappings")
                if not isinstance(mappings, list):
                    raise BundleSnapshotError(
                        "BUNDLE_MAPPINGS_INVALID",
                        "manifest.mappings는 배열이어야 합니다.",
                        manifest=manifest,
                    )
                if workspace is not None:
                    try:
                        workspace.ensure_capacity(manifest)
                    except ImportSnapshotWorkspaceError as exc:
                        raise BundleSnapshotError(
                            exc.code,
                            str(exc),
                            manifest=exc.manifest or manifest,
                        ) from exc
                total_bytes = manifest_entry.size
                entries: list[FingerprintEntry] = [manifest_entry]
                captured_by_path: dict[str, FingerprintEntry] = {}
                for mapping in mappings:
                    if not isinstance(mapping, dict) or not isinstance(mapping.get("path"), str):
                        raise BundleSnapshotError(
                            "BUNDLE_MAPPING_PATH_INVALID",
                            "manifest mapping에 안전한 path가 필요합니다.",
                            manifest=manifest,
                        )
                    relative_path = str(mapping["path"])
                    mapping_parts = _relative_parts(relative_path, "BUNDLE_MAPPING_PATH_INVALID", manifest=manifest)
                    normalized_relative = "/".join(mapping_parts)
                    if normalized_relative in {"manifest.json", READY_MARKER_NAME}:
                        raise BundleSnapshotError(
                            "BUNDLE_MAPPING_PATH_RESERVED",
                            "manifest.json과 readiness marker는 결과 mapping으로 사용할 수 없습니다.",
                            manifest=manifest,
                        )
                    if normalized_relative in captured_by_path:
                        raise BundleSnapshotError(
                            "BUNDLE_MAPPING_PATH_DUPLICATE",
                            "같은 결과 파일을 두 개의 mapping으로 사용할 수 없습니다.",
                            manifest=manifest,
                        )
                    kind = mapping.get("kind")
                    if not isinstance(kind, str):
                        raise BundleSnapshotError(
                            "BUNDLE_MAPPING_KIND_UNSUPPORTED",
                            "지원하지 않는 결과 mapping 유형입니다.",
                            manifest=manifest,
                        )
                    if kind in {"typed_scalars", "curve_csv"}:
                        mapping_file_limit = limits.max_structured_bytes
                    elif kind == "media":
                        mapping_file_limit = limits.max_file_bytes
                    else:
                        raise BundleSnapshotError(
                            "BUNDLE_MAPPING_KIND_UNSUPPORTED",
                            "지원하지 않는 결과 mapping 유형입니다.",
                            manifest=manifest,
                        )
                    entry = _copy_relative_file(
                        bundle_fd,
                        mapping_parts,
                        snapshot_root,
                        mapping_file_limit,
                        limits.max_total_bytes,
                        total_bytes,
                        code_prefix="MAPPING",
                        manifest=manifest,
                    )
                    captured_by_path[normalized_relative] = entry
                    total_bytes += entry.size
                    entries.append(entry)

        bundle_fingerprint = calculate_bundle_fingerprint(entries)
        if ready_marker is not None:
            try:
                physical_bundle_path = "/".join(manifest_parts[:-1])
                canonical_bundle_path = canonical_bundle_relative(manifest, manifest_parts[-2])
                if physical_bundle_path != canonical_bundle_path:
                    raise ReadyMarkerError(
                        "BUNDLE_READY_MARKER_PATH_MISMATCH",
                        "발견된 bundle 경로가 manifest 대상과 canonical 경로로 일치하지 않습니다.",
                    )
                verify_ready_marker(
                    ready_marker,
                    bundle_path=physical_bundle_path,
                    manifest_checksum=manifest_entry.sha256,
                    bundle_fingerprint=bundle_fingerprint,
                    entry_count=len(entries),
                )
            except ReadyMarkerError as exc:
                raise BundleSnapshotError(exc.code, str(exc), manifest=manifest) from exc

        return CapturedBundle(
            bundle_root=snapshot_root,
            manifest=manifest,
            manifest_checksum=manifest_entry.sha256,
            bundle_fingerprint=bundle_fingerprint,
            entries=tuple(entries),
            _temporary_directory=temporary_directory,
        )
    except BaseException:
        temporary_directory.cleanup()
        raise


def _require_secure_traversal() -> None:
    required_flags = ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")
    if (
        os.name != "posix"
        or os.open not in os.supports_dir_fd
        or any(not hasattr(os, flag) for flag in required_flags)
    ):
        raise BundleSnapshotError(
            "BUNDLE_SECURE_TRAVERSAL_UNSUPPORTED",
            "이 운영체제는 안전한 descriptor-relative 결과 파일 탐색을 지원하지 않습니다.",
        )


def _relative_parts(
    relative_path: str,
    code: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    if not relative_path or "\\" in relative_path or "\x00" in relative_path:
        raise BundleSnapshotError(code, "결과 파일 경로는 POSIX 상대 경로여야 합니다.", manifest=manifest)
    raw_parts = relative_path.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise BundleSnapshotError(code, "결과 파일 경로는 정규화된 bundle 내부 상대 경로여야 합니다.", manifest=manifest)
    path = PurePosixPath(relative_path)
    if path.is_absolute() or not path.parts:
        raise BundleSnapshotError(code, "결과 파일 경로는 bundle 내부 상대 경로여야 합니다.", manifest=manifest)
    return tuple(path.parts)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    # A FIFO opened read-only blocks until a writer appears unless
    # O_NONBLOCK is present. The regular-file path is unaffected by this flag,
    # while special files return immediately and are rejected by fstat below.
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NONBLOCK
    )


@contextmanager
def _open_import_root(root: Path) -> Iterator[int]:
    try:
        descriptor = os.open(str(root), _directory_flags())
    except OSError as exc:
        raise BundleSnapshotError("BUNDLE_IMPORT_ROOT_UNSAFE", "결과 import root를 안전하게 열 수 없습니다.") from exc
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _open_directory_at(
    parent_fd: int,
    parts: tuple[str, ...],
    *,
    code: str = "BUNDLE_SOURCE_PATH_UNSAFE",
    manifest: dict[str, Any] | None = None,
) -> Iterator[int]:
    current_fd = os.dup(parent_fd)
    try:
        for part in parts:
            try:
                next_fd = os.open(part, _directory_flags(), dir_fd=current_fd)
            except OSError as exc:
                raise BundleSnapshotError(
                    code,
                    "결과 bundle 경로에 안전하지 않은 디렉터리가 있습니다.",
                    manifest=manifest,
                ) from exc
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


def _copy_relative_file(
    bundle_fd: int,
    parts: tuple[str, ...],
    snapshot_root: Path,
    file_limit: int,
    total_limit: int,
    total_before: int,
    *,
    code_prefix: str,
    missing_code: str | None = None,
    manifest: dict[str, Any] | None = None,
) -> FingerprintEntry:
    with _open_directory_at(
        bundle_fd,
        parts[:-1],
        code=f"BUNDLE_{code_prefix}_FILE_UNSAFE",
        manifest=manifest,
    ) as parent_fd:
        try:
            file_fd = os.open(parts[-1], _file_flags(), dir_fd=parent_fd)
        except OSError as exc:
            raise BundleSnapshotError(
                missing_code if exc.errno == errno.ENOENT and missing_code else f"BUNDLE_{code_prefix}_FILE_UNSAFE",
                "결과 파일 심볼릭 링크는 허용되지 않습니다."
                if exc.errno == errno.ELOOP
                else "결과 파일을 안전하게 열 수 없습니다.",
                manifest=manifest,
            ) from exc

    try:
        source_stat = os.fstat(file_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise BundleSnapshotError(
                f"BUNDLE_{code_prefix}_FILE_UNSAFE",
                "결과 파일은 일반 파일이어야 합니다.",
                manifest=manifest,
            )
        if source_stat.st_size > file_limit:
            raise BundleSnapshotError(
                f"BUNDLE_{code_prefix}_FILE_LIMIT",
                "결과 파일 크기가 허용 한도를 초과했습니다.",
                manifest=manifest,
            )

        try:
            destination = snapshot_root.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            copied_bytes = 0
            with destination.open("xb") as target:
                while chunk := os.read(file_fd, _COPY_CHUNK_BYTES):
                    copied_bytes += len(chunk)
                    if copied_bytes > file_limit:
                        raise BundleSnapshotError(
                            f"BUNDLE_{code_prefix}_FILE_LIMIT",
                            "결과 파일 크기가 허용 한도를 초과했습니다.",
                            manifest=manifest,
                        )
                    if total_before + copied_bytes > total_limit:
                        raise BundleSnapshotError(
                            "BUNDLE_TOTAL_SIZE_LIMIT",
                            "결과 bundle 전체 크기가 허용 한도를 초과했습니다.",
                            manifest=manifest,
                        )
                    target.write(chunk)
                    digest.update(chunk)
        except OSError as exc:
            code = (
                "BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE"
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}
                else "BUNDLE_SNAPSHOT_WRITE_FAILED"
            )
            raise BundleSnapshotError(
                code,
                "결과 bundle 임시 snapshot을 저장할 수 없습니다.",
                manifest=manifest,
            ) from exc
        return FingerprintEntry("/".join(parts), copied_bytes, digest.hexdigest())
    finally:
        os.close(file_fd)
