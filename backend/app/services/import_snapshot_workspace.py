"""Private workspaces and disk-reservation checks for import snapshots."""

from __future__ import annotations

import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from ..config import ImportBundleLimits, ImportSnapshotSettings, import_bundle_limits, import_snapshot_settings


_SNAPSHOT_DIRECTORY_PREFIX = "simdashboard-result-bundle-"
_SNAPSHOT_DIRECTORY_PATTERN = re.compile(r"^simdashboard-result-bundle-[a-z0-9_]{8}$")


class ImportSnapshotWorkspaceError(ValueError):
    """Safe, stable workspace or capacity failure."""

    def __init__(self, code: str, message: str = "import snapshot workspace를 사용할 수 없습니다.", *, manifest: dict[str, Any] | None = None) -> None:
        self.code = code
        self.manifest = manifest
        super().__init__(message)


def _physical(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE") from exc


def _reject_overlap(workspace_root: Path, import_root: Path) -> None:
    workspace_physical = _physical(workspace_root)
    import_physical = _physical(import_root)
    if (
        workspace_physical == import_physical
        or workspace_physical in import_physical.parents
        or import_physical in workspace_physical.parents
    ):
        raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_OVERLAP")


def _ensure_secure_root(root: Path) -> None:
    try:
        try:
            info = os.lstat(root)
        except FileNotFoundError:
            # ``exist_ok`` makes first-use initialization safe when multiple
            # workers prepare the same service-owned root concurrently.  The
            # lstat and ownership/mode checks below still fail closed if the
            # winner created an unsafe object.
            root.mkdir(parents=True, mode=0o700, exist_ok=True)
            info = os.lstat(root)
    except OSError as exc:
        raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE") from exc
    _validate_secure_root_info(root, info)


def _validate_secure_root_info(root: Path, info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE")


def _clock_seconds(clock: Callable[[], object]) -> float:
    value = clock()
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_CLOCK_INVALID")


@dataclass
class ImportSnapshotWorkspace:
    """A service-owned directory which contains all temporary snapshots."""

    root: Path
    import_root: Path
    settings: ImportSnapshotSettings
    clock: Callable[[], object] = time.time
    disk_usage: Callable[[Path], object] = shutil.disk_usage

    @classmethod
    def prepare(
        cls,
        import_root: Path,
        *,
        settings: ImportSnapshotSettings | None = None,
        limits: ImportBundleLimits | None = None,
        clock: Callable[[], object] = time.time,
        disk_usage: Callable[[Path], object] = shutil.disk_usage,
    ) -> "ImportSnapshotWorkspace":
        effective_limits = limits or import_bundle_limits()
        policy = settings or import_snapshot_settings(effective_limits)
        if policy.reserve_bytes < effective_limits.max_total_bytes:
            raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_RESERVE_INVALID")
        root = Path(policy.root)
        import_path = Path(import_root)
        _reject_overlap(root, import_path)
        _ensure_secure_root(root)
        return cls(root=root, import_root=import_path, settings=policy, clock=clock, disk_usage=disk_usage)

    def create_temporary_directory(self) -> TemporaryDirectory[str]:
        try:
            temporary = TemporaryDirectory(dir=str(self.root), prefix=_SNAPSHOT_DIRECTORY_PREFIX)
            candidate = Path(temporary.name)
            if candidate.parent != self.root or candidate.is_symlink() or not candidate.is_dir():
                temporary.cleanup()
                raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE")
            os.chmod(candidate, 0o700)
            return temporary
        except ImportSnapshotWorkspaceError:
            raise
        except OSError as exc:
            raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_CREATE_FAILED") from exc

    def prune_stale(self) -> None:
        try:
            _validate_secure_root_info(self.root, os.lstat(self.root))
        except OSError as exc:
            raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE") from exc
        now = _clock_seconds(self.clock)
        try:
            children = tuple(self.root.iterdir())
        except OSError as exc:
            raise ImportSnapshotWorkspaceError("BUNDLE_SNAPSHOT_WORKSPACE_UNSAFE") from exc
        for child in children:
            if not _SNAPSHOT_DIRECTORY_PATTERN.fullmatch(child.name):
                continue
            try:
                info = os.lstat(child)
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                    or now - info.st_mtime <= self.settings.stale_seconds
                ):
                    continue
                shutil.rmtree(child)
            except OSError:
                # A stale workspace is advisory cleanup; never turn an
                # unknown or concurrently changing entry into a destructive
                # action.
                continue

    def ensure_capacity(self, manifest: dict[str, Any]) -> None:
        try:
            usage = self.disk_usage(self.root)
            free = usage.free if hasattr(usage, "free") else int(usage)
        except (OSError, TypeError, ValueError) as exc:
            raise ImportSnapshotWorkspaceError(
                "BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE", manifest=manifest
            ) from exc
        if free < self.settings.reserve_bytes + self.settings.min_free_bytes:
            raise ImportSnapshotWorkspaceError(
                "BUNDLE_SNAPSHOT_RESERVE_UNAVAILABLE", manifest=manifest
            )


def prepare_import_snapshot_workspace(
    import_root: Path,
    *,
    settings: ImportSnapshotSettings | None = None,
    limits: ImportBundleLimits | None = None,
    clock: Callable[[], object] = time.time,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
) -> ImportSnapshotWorkspace:
    return ImportSnapshotWorkspace.prepare(
        import_root,
        settings=settings,
        limits=limits,
        clock=clock,
        disk_usage=disk_usage,
    )
