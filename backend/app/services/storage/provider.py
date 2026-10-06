"""SPDM storage provider contract (docs/contracts/storage-provider.md §2).

Every listing, lookup, read and write below the configured SPDM root goes
through a ``StorageProvider``.  Stage 1 ships a single local implementation
(:mod:`.local`); paths are root-relative, ``/``-separated strings and ``""``
is the root itself.  Writes name an explicit zone (S3): ``FINAL`` for the
Final designation flow and ``LEGACY`` for the behaviour-preserving legacy
writers, which are additionally restricted to their calling modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, ContextManager, Iterable, Iterator, Literal, Protocol

FINAL = "FINAL"
LEGACY = "LEGACY"
WRITE_ZONES = frozenset({FINAL, LEGACY})

# Contract error codes (§2).  Legacy callers keep receiving ``SpdmStorageError``
# with their existing ``SPDM_*``/``FINALIZATION_*`` codes; these codes are used
# for conditions that did not exist before the provider (zone violations).
NOT_FOUND = "NOT_FOUND"
FORBIDDEN = "FORBIDDEN"
NOT_ALLOWED_WRITE = "NOT_ALLOWED_WRITE"
UNAVAILABLE = "UNAVAILABLE"
LIMIT = "LIMIT"

# S3 ① FINAL zone: written only by the Final designation flow.
FINAL_WRITER_MODULES = frozenset({"app.services.case_finalization"})

# S3 ② LEGACY zone: behaviour-preserving writers only, never new callers.
LEGACY_WRITER_MODULES = frozenset({
    "app.services.spdm_storage",               # legacy SPDM upload / bind / completion marker
    "app.services.result_registration_paths",  # prepare_folders + rmdir compensation
    "app.services.result_registration",        # approved result file copy
})


class StorageError(ValueError):
    """Provider failure with a stable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SpdmStorageError(StorageError):
    """Existing SPDM error type; callers keep catching ``spdm_storage.SpdmStorageError``."""


@dataclass(frozen=True)
class Entry:
    name: str
    kind: Literal["file", "dir", "other"]
    size: int | None
    modified_ns: int | None
    item_id: str               # local: st_dev:st_ino ("" when the listing was not stat'ed)
    etag: str | None           # local: None
    is_link: bool              # reparse point / symlink


class StorageProvider(Protocol):
    def root_identity(self) -> str: ...
    def list(self, rel_path: str) -> list[Entry]: ...
    def stat(self, rel_path: str, *, follow_links: bool = False) -> Entry | None: ...
    def read_stable(self, rel_path: str, *, max_bytes: int) -> bytes: ...
    def open_read(self, rel_path: str) -> ContextManager[BinaryIO]: ...
    def mkdirs(self, rel_path: str, *, zone: str, parents: bool = True, exist_ok: bool = True) -> None: ...
    def create_exclusive(self, rel_path: str, data: bytes | Iterable[bytes], *, zone: str, fsync: bool = True) -> Entry | None: ...
    def move_no_overwrite(self, src_rel: str, dst_rel: str, *, zone: str) -> None: ...
    def replace(self, src_rel: str, dst_rel: str, *, zone: str) -> None: ...
    def remove(self, rel_path: str, *, zone: str, directory: bool = False, missing_ok: bool = False) -> None: ...
    def lock(self, rel_path: str, *, zone: str) -> ContextManager[None]: ...
    def changes(self, rel_path: str, since: str | None) -> None: ...


def _segments(rel_path: str) -> list[str]:
    return [part for part in rel_path.split("/") if part]


def final_zone_allows(rel_path: str) -> bool:
    """``<request>/Final`` itself and ``<request>/Final/(CAE|Report|.finalizations)/**`` (§15 D21: not ``Reports``).

    The ``Final`` component needs a non-empty request prefix and must not sit
    under a ``Working`` folder; names compare case-insensitively because the
    Final writer reuses an existing differently-cased folder.
    """
    parts = [part.casefold() for part in _segments(rel_path)]
    for index, part in enumerate(parts):
        if part != "final" or index == 0:
            continue
        if "working" in parts[:index]:
            return False
        rest = parts[index + 1:]
        if not rest or rest[0] in {"cae", "report", ".finalizations"}:
            return True
    return False


def legacy_zone_allows(rel_path: str, caller: str) -> bool:
    """LEGACY zone writes, per calling module (S3 ②)."""
    parts = _segments(rel_path)
    if caller == "app.services.spdm_storage":
        # Issue #13 leaf creation may create the exact Project_*/WR_* parents in an
        # empty root; uploads go to ``<leaf>/<kind>`` and ``<P>/<WR>/보고서/**``.
        if not parts or not parts[0].startswith("Project_"):
            return False
        if len(parts) == 1:
            return True
        if not parts[1].startswith("WR_"):
            return False
        return len(parts) == 2 or parts[2] in {"CAE", "보고서"}
    if caller in {"app.services.result_registration_paths", "app.services.result_registration"}:
        return bool(parts)
    return False


def check_write(rel_path: str, zone: str, caller: str) -> None:
    if zone == FINAL and caller in FINAL_WRITER_MODULES and final_zone_allows(rel_path):
        return
    if zone == LEGACY and caller in LEGACY_WRITER_MODULES and legacy_zone_allows(rel_path, caller):
        return
    raise StorageError(NOT_ALLOWED_WRITE, "허용된 SPDM 쓰기 구역 밖의 경로입니다.")


__all__ = [
    "Entry", "FINAL", "FINAL_WRITER_MODULES", "LEGACY", "LEGACY_WRITER_MODULES", "NOT_ALLOWED_WRITE", "NOT_FOUND", "FORBIDDEN",
    "UNAVAILABLE", "LIMIT", "SpdmStorageError", "StorageError", "StorageProvider", "WRITE_ZONES",
    "check_write", "final_zone_allows", "legacy_zone_allows", "Iterator",
]
