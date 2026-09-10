"""Create a filesystem companion archive for a deployment database snapshot.

The caller keeps the database snapshot open.  This module deliberately does
not run migrations or alter database state: it only turns the already-read
media inventory and the legacy assets tree into one verified ZIP file.
"""

from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable
from uuid import uuid4

from app.services.media_integrity import MediaIntegrityError, validate_media_inventory_schema


_REPARSE_POINT = 0x0400
_CHUNK = 1024 * 1024
_WARNING_ORPHAN_BLOBS = "ORPHAN_BLOBS_INCLUDED"
_WARNING_INCOMPLETE_DEMO = "INCOMPLETE_DEMO_CATALOG_INCLUDED"
_WARNING_DEMO_SOURCE_MISSING = "DEMO_SOURCE_MISSING"


class DeploymentMediaBackupError(RuntimeError):
    """A public, path- and identifier-safe failure code for deployment backups."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Source:
    path: Path
    name: str
    before: tuple[int, int, int, int, int]
    sha256: str


def _fail(code: str) -> None:
    raise DeploymentMediaBackupError(code)


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        _fail("ASSETS_UNREADABLE")
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _reject_reparse_ancestors(path: Path, *, code: str) -> None:
    """Reject links/junctions from a trusted absolute root down to ``path``."""
    candidate = path.expanduser().absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            if _is_reparse(current):
                _fail(code)
        except DeploymentMediaBackupError as error:
            if error.code == "ASSETS_UNREADABLE":
                _fail(code)
            raise


def _signature(path: Path) -> tuple[int, int, int, int, int]:
    try:
        info = path.lstat()
    except OSError:
        _fail("ASSETS_UNREADABLE")
    if _is_reparse(path):
        _fail("ASSETS_REPARSE_POINT")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)


def _safe_legacy_name(value: object) -> str:
    if not isinstance(value, str):
        _fail("UNBOUND_MEDIA_PATH_INVALID")
    windows = PureWindowsPath(value)
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        _fail("UNBOUND_MEDIA_PATH_INVALID")
    parts = list(posix.parts)
    if parts and parts[0].casefold() == "assets":
        parts.pop(0)
    relative = PurePosixPath(*parts)
    if not relative.parts or relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        _fail("UNBOUND_MEDIA_PATH_INVALID")
    return relative.as_posix()


def _read_digest(path: Path, expected: tuple[int, int, int, int, int] | None = None) -> tuple[str, tuple[int, int, int, int, int]]:
    before = _signature(path)
    if expected is not None and before != expected:
        _fail("ASSETS_CHANGED")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size, opened.st_mtime_ns) != before:
                _fail("ASSETS_CHANGED")
            for block in iter(lambda: source.read(_CHUNK), b""):
                digest.update(block)
    except DeploymentMediaBackupError:
        raise
    except OSError:
        _fail("ASSETS_UNREADABLE")
    after = _signature(path)
    if after != before:
        _fail("ASSETS_CHANGED")
    return digest.hexdigest(), before


def _catalog(assets_root: Path, namespace: str) -> list[_Source]:
    _reject_reparse_ancestors(assets_root, code="ASSETS_REPARSE_POINT")
    if _is_reparse(assets_root):
        _fail("ASSETS_REPARSE_POINT")
    try:
        root_info = assets_root.lstat()
    except OSError:
        _fail("ASSETS_UNREADABLE")
    if not stat.S_ISDIR(root_info.st_mode):
        _fail("ASSETS_ROOT_INVALID")

    found: list[Path] = []
    def visit(directory: Path) -> None:
        if _is_reparse(directory):
            _fail("ASSETS_REPARSE_POINT")
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            _fail("ASSETS_UNREADABLE")
        for entry in entries:
            signature = _signature(entry)
            if stat.S_ISDIR(signature[2]):
                visit(entry)
            elif stat.S_ISREG(signature[2]):
                found.append(entry)
            else:
                _fail("ASSETS_ENTRY_INVALID")
    visit(assets_root)
    records: list[_Source] = []
    for path in found:
        name = f"{namespace}/{path.relative_to(assets_root).as_posix()}"
        digest, signature = _read_digest(path)
        records.append(_Source(path, name, signature, digest))
    return records


def _unbound_references(connection: Any, assets_root: Path, records: list[_Source]) -> int:
    fold = (lambda value: value.casefold()) if os.name == "nt" else (lambda value: value)
    available = {fold(record.name): record for record in records}
    try:
        cursor = connection.execute("SELECT file_path FROM media_assets WHERE blob_id IS NULL")
    except Exception:
        _fail("UNBOUND_MEDIA_QUERY_FAILED")
    count = 0
    while True:
        try:
            row = cursor.fetchone()
        except Exception:
            _fail("UNBOUND_MEDIA_QUERY_FAILED")
        if row is None:
            break
        name = _safe_legacy_name(row[0] if row else None)
        candidate = assets_root.joinpath(*PurePosixPath(name).parts)
        # Membership in the catalogue also proves this is a regular, non-link file.
        archive_name = f"assets/{name}"
        record = available.get(fold(archive_name))
        if record is None or _signature(candidate) != record.before:
            _fail("UNBOUND_MEDIA_FILE_MISSING")
        count += 1
    return count


def _archive(records: list[_Source], archive_path: Path, verify_catalog: Callable[[], None]) -> None:
    if archive_path.exists() or archive_path.is_symlink():
        _fail("ARCHIVE_EXISTS")
    parent = archive_path.parent
    if not parent.is_dir() or _is_reparse(parent):
        _fail("ARCHIVE_DIRECTORY_INVALID")
    _reject_reparse_ancestors(parent, code="ARCHIVE_DIRECTORY_INVALID")
    partial = archive_path.with_name(f".{archive_path.name}.{uuid4().hex}.partial")
    try:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        _fail("ARCHIVE_CREATE_FAILED")
    try:
        with os.fdopen(descriptor, "wb") as raw, zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for record in records:
                try:
                    with record.path.open("rb") as source, bundle.open(record.name, "w", force_zip64=True) as destination:
                        start = os.fstat(source.fileno())
                        if (start.st_dev, start.st_ino, start.st_mode, start.st_size, start.st_mtime_ns) != record.before:
                            _fail("ASSETS_CHANGED")
                        digest = hashlib.sha256()
                        for block in iter(lambda: source.read(_CHUNK), b""):
                            digest.update(block)
                            destination.write(block)
                except DeploymentMediaBackupError:
                    raise
                except OSError:
                    _fail("ASSETS_UNREADABLE")
                if _signature(record.path) != record.before or digest.hexdigest() != record.sha256:
                    _fail("ASSETS_CHANGED")
        os.chmod(partial, 0o600)
        with zipfile.ZipFile(partial, "r") as bundle:
            if [item.filename for item in bundle.infolist()] != [record.name for record in records]:
                _fail("ARCHIVE_VERIFY_FAILED")
            for record in records:
                digest = hashlib.sha256()
                with bundle.open(record.name, "r") as member:
                    for block in iter(lambda: member.read(_CHUNK), b""):
                        digest.update(block)
                if digest.hexdigest() != record.sha256:
                    _fail("ARCHIVE_VERIFY_FAILED")
        for record in records:
            if _signature(record.path) != record.before:
                _fail("ASSETS_CHANGED")
        verify_catalog()
        try:
            os.link(partial, archive_path)
        except FileExistsError:
            _fail("ARCHIVE_EXISTS")
        except OSError:
            _fail("ARCHIVE_PUBLISH_FAILED")
        partial.unlink()
    except BaseException:
        # Preserve a unique partial for forensic review, never overwrite it.
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _catalog_equal(left: list[_Source], right: list[_Source]) -> bool:
    return [(item.name, item.before, item.sha256) for item in left] == [
        (item.name, item.before, item.sha256) for item in right
    ]


def create_deployment_media_bundle(
    connection: Any, inventory: object, assets_root: Path, archive_path: Path, demo_root: Path | None = None,
) -> dict[str, object]:
    """Write a verified ZIP of all legacy assets for one database snapshot."""
    try:
        report = validate_media_inventory_schema(inventory)
    except MediaIntegrityError as error:
        raise DeploymentMediaBackupError("MEDIA_INVENTORY_SCHEMA_INVALID") from error
    if int(report["corrupt_blob_count"]) or not bool(report["content_integrity_verified"]):
        _fail("MEDIA_BLOB_CORRUPT")
    if int(report["missing_reference_count"]) or int(report["orphan_chunk_count"]) or report["unexpected_demo_ids"]:
        _fail("MEDIA_DATABASE_REFERENCES_INVALID")

    references = int(report["unbound_media_asset_count"])
    root = Path(assets_root)
    assets_present = root.exists() or root.is_symlink()
    if not assets_present:
        if references:
            _fail("UNBOUND_MEDIA_FILE_MISSING")
        records: list[_Source] = []
        referenced_file_count = 0
    else:
        records = _catalog(root, "assets")
        referenced_file_count = _unbound_references(connection, root, records)
        if referenced_file_count != references:
            _fail("UNBOUND_MEDIA_REFERENCE_MISMATCH")

    selected_demo_root = Path(demo_root) if demo_root is not None else root.parents[1] / "video_example"
    demo_records: list[_Source] = []
    demo_missing = not (selected_demo_root.exists() or selected_demo_root.is_symlink())
    if not demo_missing:
        demo_records = _catalog(selected_demo_root, "video_example")
    records.extend(demo_records)
    warnings: list[str] = []
    if int(report["orphan_blob_count"]):
        warnings.append(_WARNING_ORPHAN_BLOBS)
    if not bool(report["demo_exact"]):
        warnings.append(_WARNING_INCOMPLETE_DEMO)
    if demo_missing and not bool(report["demo_exact"]):
        warnings.append(_WARNING_DEMO_SOURCE_MISSING)
    target = Path(archive_path)
    initial_records = list(records)

    def verify_catalog() -> None:
        current_assets_present = root.exists() or root.is_symlink()
        current_demo_present = selected_demo_root.exists() or selected_demo_root.is_symlink()
        if current_assets_present != assets_present or current_demo_present == demo_missing:
            _fail("ASSETS_CHANGED")
        current: list[_Source] = []
        if current_assets_present:
            current.extend(_catalog(root, "assets"))
        if current_demo_present:
            current.extend(_catalog(selected_demo_root, "video_example"))
        if not _catalog_equal(initial_records, current):
            _fail("ASSETS_CHANGED")

    _archive(records, target, verify_catalog)
    try:
        return {
            "format": "analysis-canvas-deployment-media-bundle",
            "format_version": 1,
            "filename": target.name,
            "bytes": target.stat().st_size,
            "sha256": _sha256(target),
            "file_count": len(records),
            "demo_file_count": len(demo_records),
            "referenced_file_count": referenced_file_count,
            "warnings": warnings,
        }
    except OSError:
        _fail("ARCHIVE_VERIFY_FAILED")
