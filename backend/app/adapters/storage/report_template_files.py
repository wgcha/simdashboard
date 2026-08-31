from __future__ import annotations

import logging
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ...domains.reports.template_models import (
    ReportTemplateFileCollisionError,
    UnsafeReportTemplateFileError,
)


logger = logging.getLogger(__name__)
REPORT_TEMPLATE_STORAGE_ROOT = (
    Path(__file__).resolve().parents[3] / "assets" / "report-templates"
)


@dataclass(frozen=True)
class _QuarantinedTemplate:
    source: Path
    quarantine: Path


class ReportTemplateFileStore:
    """File-system boundary for assets/report-templates.

    Database paths are intentionally not general paths: they must be exactly
    ``report-templates/<one direct .pptx child>``.  The configured root may be
    a deployment-managed symlink (as on Rocky), but files below it may never be
    symlinks.
    """

    _prefix = "report-templates"
    _filename_pattern = re.compile(r"report-template-[0-9a-f]{12}\.pptx\Z")

    def __init__(self, storage_root: Path | str | None = None) -> None:
        self._configured_root = (
            Path(storage_root) if storage_root is not None else REPORT_TEMPLATE_STORAGE_ROOT
        )

    def relative_path(self, template_id: str) -> str:
        filename = f"{template_id}.pptx"
        self._filename_from_relative(f"{self._prefix}/{filename}")
        return f"{self._prefix}/{filename}"

    def promote_upload(self, relative_path: str, data: bytes) -> None:
        root, target = self._managed_target(relative_path)
        if target.exists() or target.is_symlink():
            raise ReportTemplateFileCollisionError()
        staging = root / f".{target.name}.{uuid4().hex}.stage"
        descriptor: int | None = None
        try:
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # link(2) creates the final name exclusively; unlike replace,
                # it cannot overwrite an id collision that appeared mid-upload.
                os.link(staging, target, follow_symlinks=False)
            except FileExistsError as exc:
                raise ReportTemplateFileCollisionError() from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            self._unlink_if_regular(staging)

    def remove_upload(self, relative_path: str) -> None:
        try:
            _, target = self._managed_target(relative_path)
        except UnsafeReportTemplateFileError:
            return
        self._unlink_if_regular(target)

    def read_for_render(self, relative_path: str) -> bytes:
        _, target = self._managed_target(relative_path)
        if target.is_symlink() or not target.is_file():
            raise FileNotFoundError(target)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags)
        try:
            mode = os.fstat(descriptor).st_mode
            if not stat.S_ISREG(mode):
                raise FileNotFoundError(target)
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                return handle.read()
        finally:
            if descriptor is not None and descriptor >= 0:
                os.close(descriptor)

    def quarantine_for_delete(self, relative_path: str) -> object | None:
        root, source = self._managed_target(relative_path)
        if source.is_symlink():
            raise UnsafeReportTemplateFileError()
        if not source.is_file():
            return None
        quarantine = root / f".{source.name}.{uuid4().hex}.quarantine"
        try:
            # Same-directory rename is atomic and leaves no window where an
            # attacker-controlled destination can replace the source.
            os.replace(source, quarantine)
        except FileNotFoundError:
            return None
        return _QuarantinedTemplate(source=source, quarantine=quarantine)

    def restore_quarantine(self, quarantine: object | None) -> None:
        if quarantine is None:
            return
        if not isinstance(quarantine, _QuarantinedTemplate):
            raise UnsafeReportTemplateFileError()
        if quarantine.quarantine.is_symlink() or quarantine.source.exists():
            raise UnsafeReportTemplateFileError()
        if quarantine.quarantine.is_file():
            os.replace(quarantine.quarantine, quarantine.source)

    def purge_quarantine(self, quarantine: object | None) -> None:
        if quarantine is None:
            return
        if not isinstance(quarantine, _QuarantinedTemplate):
            raise UnsafeReportTemplateFileError()
        try:
            self._unlink_if_regular(quarantine.quarantine)
        except OSError:
            # The database state is already committed. Retain the private,
            # managed quarantine file for an operator/reconciliation pass
            # instead of turning a successful deactivation into a 500.
            logger.exception("report template quarantine purge deferred: %s", quarantine.quarantine)

    def _managed_target(self, relative_path: str) -> tuple[Path, Path]:
        filename = self._filename_from_relative(relative_path)
        root = self._root()
        target = root / filename
        # root.resolve() deliberately follows only the configured root symlink.
        # Target children are checked separately and never resolved through.
        if target.parent.resolve() != root:
            raise UnsafeReportTemplateFileError()
        return root, target

    def _root(self) -> Path:
        try:
            self._configured_root.mkdir(parents=True, exist_ok=True)
            root = self._configured_root.resolve()
        except OSError as exc:
            raise UnsafeReportTemplateFileError() from exc
        if not root.is_dir():
            raise UnsafeReportTemplateFileError()
        return root

    def _filename_from_relative(self, relative_path: str) -> str:
        if "\\" in relative_path:
            raise UnsafeReportTemplateFileError()
        candidate = PurePosixPath(relative_path)
        if candidate.is_absolute() or candidate.parts[:1] == ("..",):
            raise UnsafeReportTemplateFileError()
        if len(candidate.parts) != 2 or candidate.parts[0] != self._prefix:
            raise UnsafeReportTemplateFileError()
        filename = candidate.parts[1]
        if candidate.name != filename or not self._filename_pattern.fullmatch(filename):
            raise UnsafeReportTemplateFileError()
        return filename

    @staticmethod
    def _unlink_if_regular(path: Path) -> None:
        try:
            if path.is_symlink():
                return
            if path.is_file():
                path.unlink()
        except FileNotFoundError:
            return
