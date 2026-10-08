"""SPDM storage provider contract (docs/contracts/storage-provider.md §2).

Every listing, lookup, read and write below the configured SPDM root goes
through a ``StorageProvider``.  Stage 1 ships a single local implementation
(:mod:`.local`); paths are root-relative, ``/``-separated strings and ``""``
is the root itself.  Writes name an explicit zone (S3): ``FINAL`` for the
Final designation flow and ``LEGACY`` for the behaviour-preserving legacy
writers, which are additionally restricted to their calling modules.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import BinaryIO, ContextManager, Iterable, Iterator, Literal, Protocol

FINAL = "FINAL"
LEGACY = "LEGACY"
# W8 result drop upload: ``<request>/Working/**`` (folders, staged chunks, published files).
WORKING = "WORKING"
# Result folder structure: the request skeleton above the Case level — an environment request
# folder (``…/<name with exactly one 사용/유통 keyword>``) and its ``Working`` folder. Folders only.
SKELETON = "SKELETON"
WRITE_ZONES = frozenset({FINAL, LEGACY, WORKING, SKELETON})

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

# W8: per-session staging folder directly under ``<request>/Working``. Same volume as the
# destination (publish = rename). Folder scans/auto-sync skip it entirely (it is never a
# Case, never part of a fingerprint); it is also a dot-name, ignored by the depth schema.
UPLOAD_STAGING_DIR = ".simdash-upload"

# S3 ③ WORKING zone: written only by the W8 drag & drop upload (never replaces files).
WORKING_WRITER_MODULES = frozenset({"app.services.result_drop_upload", "app.services.result_folder_structure"})

# S3 ④ SKELETON zone: written only by "폴더 구조 만들기" (result_folder_structure, mkdir_pinned only).
SKELETON_WRITER_MODULES = frozenset({"app.services.result_folder_structure"})

# Per-module operation allowlist (review L3): a (module, zone) pair listed here may only use the named
# provider primitives. "폴더 구조 만들기" creates folders and nothing else, in both of its zones.
WRITER_OPERATIONS: dict[tuple[str, str], frozenset[str]] = {
    ("app.services.result_folder_structure", WORKING): frozenset({"mkdir_pinned"}),
    ("app.services.result_folder_structure", SKELETON): frozenset({"mkdir_pinned"}),
}

# Review L4: the SKELETON zone is depth-aware. The writer states the current depth schema's request
# depth (number of segments of a request folder path, DEPTH_V1 upper levels) for the duration of its
# writes; without it every SKELETON write is refused.
_SKELETON_REQUEST_DEPTH: ContextVar[int | None] = ContextVar("skeleton_request_depth", default=None)


@contextmanager
def skeleton_request_depth(depth: int) -> Iterator[None]:
    """Allow SKELETON writes at exactly this request depth (and its ``Working``) inside the block."""
    token = _SKELETON_REQUEST_DEPTH.set(int(depth))
    try:
        yield
    finally:
        _SKELETON_REQUEST_DEPTH.reset(token)
_ENVIRONMENT_KEYWORDS = ("사용", "유통")

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


# W3: SPDM summary of the current Final, directly in the request's ``Final`` folder.
# Provisional name until the SPDM agreement (docs/features/case-finalization.md "현재 Final 요약 파일");
# ``case_finalization`` uses this one constant. Its temporary file is ``.current.json.<32 hex>.tmp``
# in the same folder (written, then renamed over the summary).
FINAL_SUMMARY_FILE = "current.json"
_FINAL_SUMMARY_TEMP = re.compile(r"^\.current\.json\.[0-9a-f]{32}\.tmp$")


def final_zone_allows(rel_path: str) -> bool:
    """``<request>/Final`` itself and ``<request>/Final/(CAE|Report|.finalizations)/**`` (§15 D21: not ``Reports``),
    plus exactly ``<request>/Final/current.json`` and its temporary file (W3 summary).

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
        if len(rest) == 1 and (rest[0] == FINAL_SUMMARY_FILE or _FINAL_SUMMARY_TEMP.fullmatch(rest[0])):
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


def working_zone_allows(rel_path: str) -> bool:
    """Strictly below ``<project>/<request>/Working`` (W8; re-review N1).

    The first ``Working`` segment must have at least two folders above it (project, request;
    upper CONTAINER levels may add more) and no ``Final`` segment before it; no ``.``/``..``.
    Names deeper inside Working (a Scene's ``final`` or ``Working`` subfolder) are content and
    allowed. The upload service additionally confines every write to the selected request's
    own ``Working`` folder from the registered binding; this is the provider-level guard.
    """
    parts = [part.casefold() for part in _segments(rel_path)]
    if any(part in {".", ".."} for part in parts) or "working" not in parts:
        return False
    index = parts.index("working")
    return index >= 2 and "final" not in parts[:index] and len(parts) > index + 1


def skeleton_zone_allows(rel_path: str, request_depth: int | None = None) -> bool:
    """An environment request folder or exactly its ``Working`` folder (S3 ④).

    Request folder: at least one folder above it (project), its name carries exactly one of the
    environment keywords ``사용``/``유통`` (depth schema D4) and no segment is ``Working``/``Final``.
    ``Working``: the last segment, at least two folders above it, none of them ``Working``/``Final``.
    Never ``.``/``..`` or hidden (``.``/``$``/``~``) segments.

    With ``request_depth`` (segments of a request folder path under the current depth schema) the
    request folder must sit at exactly that depth and ``Working`` directly below such a folder
    (review L4). The provider check always passes the writer's declared depth.
    """
    raw = _segments(rel_path)
    parts = [part.casefold() for part in raw]
    if len(parts) < 2 or any(part in {"", ".", ".."} or part.startswith((".", "$", "~")) for part in parts):
        return False
    if any(part in {"working", "final"} for part in parts[:-1]):
        return False
    if parts[-1] == "working":
        if request_depth is not None:
            return len(parts) == request_depth + 1 and request_depth >= 2
        return len(parts) >= 3
    if parts[-1] == "final":
        return False
    if request_depth is not None and len(parts) != request_depth:
        return False
    return sum(keyword in raw[-1] for keyword in _ENVIRONMENT_KEYWORDS) == 1


def check_write(rel_path: str, zone: str, caller: str, operation: str | None = None) -> None:
    allowed = WRITER_OPERATIONS.get((caller, zone))
    if allowed is not None and operation not in allowed:
        raise StorageError(NOT_ALLOWED_WRITE, "허용된 SPDM 쓰기 구역 밖의 경로입니다.")
    if zone == SKELETON:
        depth = _SKELETON_REQUEST_DEPTH.get()
        if depth is not None and caller in SKELETON_WRITER_MODULES and skeleton_zone_allows(rel_path, depth):
            return
        raise StorageError(NOT_ALLOWED_WRITE, "허용된 SPDM 쓰기 구역 밖의 경로입니다.")
    if zone == FINAL and caller in FINAL_WRITER_MODULES and final_zone_allows(rel_path):
        return
    if zone == WORKING and caller in WORKING_WRITER_MODULES and working_zone_allows(rel_path):
        return
    if zone == LEGACY and caller in LEGACY_WRITER_MODULES and legacy_zone_allows(rel_path, caller):
        return
    raise StorageError(NOT_ALLOWED_WRITE, "허용된 SPDM 쓰기 구역 밖의 경로입니다.")


__all__ = [
    "Entry", "FINAL", "FINAL_SUMMARY_FILE", "SKELETON", "SKELETON_WRITER_MODULES", "skeleton_zone_allows", "skeleton_request_depth", "WRITER_OPERATIONS", "UPLOAD_STAGING_DIR", "WORKING", "WORKING_WRITER_MODULES", "working_zone_allows", "FINAL_WRITER_MODULES", "LEGACY", "LEGACY_WRITER_MODULES", "NOT_ALLOWED_WRITE", "NOT_FOUND", "FORBIDDEN",
    "UNAVAILABLE", "LIMIT", "SpdmStorageError", "StorageError", "StorageProvider", "WRITE_ZONES",
    "check_write", "final_zone_allows", "legacy_zone_allows", "Iterator",
]
