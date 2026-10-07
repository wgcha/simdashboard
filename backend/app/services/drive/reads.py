"""SCX drive read path (stage D2, docs/features/scx-drive.md §8, integration 03 §2.1, 05 §2–4).

Every drive read of the dashboard goes through this module (via
``DriveStorageProvider``).  The rule that shapes it: **a thread never waits on
the drive while it holds a database connection** (the drive client delivers
token rotations on its reader thread, which opens its own DB connection; on
DuckDB that needs the process lock, so a request thread waiting on the drive
inside ``with connect()`` would deadlock, and on PostgreSQL it would pin a pool
connection and its locks for the length of a drive call).

How callers keep that rule without restructuring every service:

* :func:`run` opens a **read session** for one operation.  The operation's body
  opens its own DB connection(s) as before.  Inside the body, drive data is
  served from the session (listings, stats, downloaded files in the server
  staging folder).  When the body needs data the session does not have yet and
  its thread holds a connection, the provider raises :class:`DriveReadMiss`
  (a ``BaseException`` so service ``except Exception`` blocks and transaction
  rollbacks pass it through); ``run`` lets the connection close, fetches the
  missing data **without** a connection (plus a bounded speculative batch, so
  a scan or a capture needs few rounds) and runs the body again.  Bodies run
  this way must be safe to repeat (reads before writes, or writes in a
  transaction that the miss rolls back).  ``rounds=1`` turns misses into
  ordinary ``SpdmStorageError("DRIVE_READ_NOT_PREPARED")`` for bodies that
  must not repeat; those prepare their data first (``prepare=``).
* Outside a session, a drive read on a thread that holds a connection raises
  ``SpdmStorageError("DRIVE_READ_NOT_PREPARED")``; without a connection it
  calls the drive directly.
* After a body committed writes (:func:`after_commit`, :func:`committed_phase`)
  a miss is ``DRIVE_READ_NOT_PREPARED`` instead of a repeat of the body.
* Files are read at their **registered** version whenever the live drive
  version differs from the current ``drive_source_versions`` row of a
  classified request folder, also before a sync has classified the change
  (:func:`effective_version`; fail closed).
* One staging byte budget per session and a free-space floor
  (``SESSION_STAGING_MAX_BYTES``, ``STAGING_MIN_FREE_BYTES``).

Downloads go to ``<SIMDASH_SCX_STAGING_DIR>/<session>/`` and are deleted when the
session ends (05 §2 download → register → delete).  Files above
``BLOB_THRESHOLD_BYTES`` are moved into the content-addressed server blob store
(05 §3) instead and reused while their version is unchanged.

Nothing here writes to the drive; the only drive calls are ``list_dir``,
``stat`` and ``download_to`` (static test).
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, TypeVar

from ...database_connection import connect, connection_held
from ..storage import server_local
from ..storage.provider import SpdmStorageError
from ..storage.server_local import BlobStore
from . import gateway as drive_gateway
from .config import validate_drive_rel_path

logger = logging.getLogger("app.services.drive.reads")

T = TypeVar("T")

BLOB_THRESHOLD_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024          # same bound as the local stable reader
MAX_ROUNDS = 8
LIST_PREFETCH_DEPTH = 4                     # folder levels listed below a missed folder
LIST_PREFETCH_MAX = 600                     # listings per miss
CONTENT_PREFETCH_MAX_FILES = 2000
CONTENT_PREFETCH_MAX_BYTES = 512 * 1024 * 1024
CONTENT_PREFETCH_FILE_BYTES = 64 * 1024 * 1024
# What a dashboard capture reads (dashboard_capture._walk): result values and media up to 32 MiB.
CAPTURE_SUFFIXES = frozenset({".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm"})
# L3: bytes one read session may hold in its staging folder, and free space kept on that disk.
SESSION_STAGING_MAX_BYTES = 2 * 1024 * 1024 * 1024
STAGING_MIN_FREE_BYTES = 1024 * 1024 * 1024
REGISTERED_QUERY_CHUNK = 400
DECK_SUFFIXES = frozenset({".inc", ".rad"})
SKIPPED_CAPTURE_DIRS = frozenset({"cad", "report", "reports", "final", "validation", "library"})
ORPHAN_SECONDS = 24 * 3600

NOT_PREPARED = "DRIVE_READ_NOT_PREPARED"
SOURCE_CHANGED = "DRIVE_SOURCE_CHANGED"
STAGING_FULL = "DRIVE_STAGING_FULL"


class DriveReadMiss(BaseException):
    """Control-flow signal inside :func:`run`: ``kind`` data for ``rel`` is not in the session yet."""

    def __init__(self, kind: str, rel: str, token: str | None = None) -> None:
        super().__init__(f"drive read miss {kind}:{rel}")
        self.kind = kind
        self.rel = rel
        self.token = token


# --- versions --------------------------------------------------------------------------------

def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def epoch_us(value: Any) -> int | None:
    stamp = _utc(value)
    if stamp is None:
        return None
    delta = stamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


@dataclass(frozen=True)
class Version:
    """One observed state of a drive file (contract §2.2 ``version_token``)."""

    token: str
    size: int | None
    modified_us: int | None
    sha1: str | None
    item_id: str | None = None

    @property
    def kind(self) -> str:
        return "sha1" if self.sha1 else "size_mtime"

    def same_as(self, other: "Version") -> bool:
        """Same content version.  sha1 when both sides have it (C1/C9: a listing may lack it), else size+time."""
        if self.sha1 and other.sha1:
            return self.sha1 == other.sha1 and (self.size is None or other.size is None or self.size == other.size)
        if self.size is not None and other.size is not None and self.size != other.size:
            return False
        if self.modified_us is not None and other.modified_us is not None:
            return self.modified_us == other.modified_us
        return self.token == other.token


_SHA1 = re.compile(r"[0-9a-f]{40}")


def normal_sha1(value: Any) -> str | None:
    """Lower-case sha1 hex, or ``None`` unless it is exactly 40 hex digits (PG ``VARCHAR(40)`` = DuckDB)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if _SHA1.fullmatch(text) else None


def make_token(sha1: str | None, size: int | None, modified_us: int | None) -> str:
    """``sha1:<hex>:<size>`` when the drive gives sha1, else ``t:<size>:<mtime µs>`` ('?' when unknown)."""
    if sha1:
        return f"sha1:{sha1.lower()}:{size if size is not None else '?'}"
    return f"t:{size if size is not None else '?'}:{modified_us if modified_us is not None else '?'}"


def version_of(entry: Any) -> Version:
    sha1 = normal_sha1(getattr(entry, "sha1", None))
    size = getattr(entry, "size", None)
    size = int(size) if size is not None else None
    modified_us = epoch_us(getattr(entry, "modified_at", None))
    item_id = getattr(entry, "item_id", None)
    return Version(token=make_token(sha1, size, modified_us), size=size, modified_us=modified_us, sha1=sha1,
                   item_id=str(item_id) if item_id else None)


# --- token-kind observation (C1/C9: does the listing carry sha1?) ------------------------------

_observed_lock = threading.Lock()
_observed: dict[str, Any] = {"sha1": 0, "size_mtime": 0, "last_kind": None, "last_at": None}


def _observe(entries: list[Any]) -> None:
    files = [entry for entry in entries if getattr(entry, "kind", None) == "file"]
    if not files:
        return
    with_sha1 = sum(1 for entry in files if getattr(entry, "sha1", None))
    kind = "sha1" if with_sha1 == len(files) else ("size_mtime" if with_sha1 == 0 else "mixed")
    with _observed_lock:
        previous = _observed["last_kind"]
        _observed["sha1"] += with_sha1
        _observed["size_mtime"] += len(files) - with_sha1
        _observed["last_kind"] = kind
        _observed["last_at"] = datetime.now(timezone.utc).isoformat()
    if previous != kind:
        logger.info("SCX drive listing version_token: %s (sha1 in %d of %d listed files)", kind, with_sha1, len(files))


def token_observation() -> dict[str, Any]:
    """Which version_token form the drive listings produced so far (admin status, C1/C9 record)."""
    with _observed_lock:
        return dict(_observed)


def reset_for_tests() -> None:
    with _observed_lock:
        _observed.update(sha1=0, size_mtime=0, last_kind=None, last_at=None)


# --- session ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Accepted:
    """Registered (accepted) version of a file whose live state is not applied (pending, ignored, missing)."""

    rel_path: str
    version: Version
    sha256: str | None
    stored_path: str | None
    source_state: str
    review_state: str
    scoped: bool = True   # the row belongs to a request folder that syncs classify (project/request ids set)


@dataclass
class LocalCopy:
    path: Path
    sha256: str
    size: int
    blob: bool = False


@dataclass(frozen=True)
class DownloadRecord:
    rel_path: str
    version: Version
    sha256: str
    size: int
    stored_path: str | None


@dataclass(eq=False)
class DriveReadSession:
    spdm_root: str
    root_key: str
    staging: Path
    priority: str = "BACKGROUND"
    scope: str | None = None
    scope_ids: tuple[str, str] | None = None
    listings: dict[str, list[Any]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    errors: dict[tuple[str, str], SpdmStorageError] = field(default_factory=dict)
    files: dict[tuple[str, str], LocalCopy] = field(default_factory=dict)
    overlay: dict[str, Accepted] = field(default_factory=dict)
    downloads: list[DownloadRecord] = field(default_factory=list)
    prepared_scopes: set[str] = field(default_factory=set)
    registered: dict[str, Accepted] = field(default_factory=dict)       # current rows of checked files (M2)
    registered_checked: set[str] = field(default_factory=set)
    staged_bytes: int = 0
    committed: bool = False
    final_round: bool = False
    rounds: int = 0
    ephemeral: bool = False
    _counter: int = 0

    def next_dir(self) -> Path:
        self._counter += 1
        return server_local.make_child_dir(self.staging, f"d{self._counter:05d}")


_session_var: contextvars.ContextVar[DriveReadSession | None] = contextvars.ContextVar("drive_read_session", default=None)


def current_session() -> DriveReadSession | None:
    return _session_var.get()


def active() -> bool:
    """scx mode with an SPDM root on the drive (reads go through this module)."""
    try:
        settings = drive_gateway.current_settings()
    except Exception:  # noqa: BLE001 - a startup error surfaces elsewhere; reads are not active
        return False
    return bool(settings.enabled and settings.spdm_root)


def _settings():
    return drive_gateway.current_settings()


def _root_key() -> str:
    from ..storage.drive import drive_root_from_settings

    root = drive_root_from_settings(_settings())
    return root.root_key()


def _new_session(*, priority: str, scope: str | None, ephemeral: bool = False) -> DriveReadSession:
    settings = _settings()
    staging = server_local.new_dir(settings.staging_dir, "read-")
    return DriveReadSession(spdm_root=str(settings.spdm_root or ""), root_key=_root_key(), staging=staging,
                            priority=priority, scope=scope, ephemeral=ephemeral)


def _close_session(session: DriveReadSession) -> None:
    server_local.remove_tree(session.staging)


_orphan_lock = threading.Lock()
_orphan_checked = 0.0


def _cleanup_orphans() -> None:
    """05 §7: once an hour, remove read-session staging folders older than a day (crash leftovers)."""
    global _orphan_checked
    with _orphan_lock:
        if _orphan_checked and time.monotonic() - _orphan_checked < 3600:
            return
        _orphan_checked = time.monotonic()
    server_local.remove_old_dirs(_settings().staging_dir, "read-", ORPHAN_SECONDS)


def _need(session: DriveReadSession | None, kind: str, rel: str, token: str | None = None):
    if session is None or session.ephemeral:
        raise SpdmStorageError(NOT_PREPARED, "드라이브 자료를 아직 준비하지 못했습니다. 잠시 후 다시 시도하세요.")
    if session.final_round:
        raise SpdmStorageError(NOT_PREPARED, "드라이브 자료를 준비하지 못했습니다(조회 횟수 한도). 잠시 후 다시 시도하세요.")
    if session.committed:
        # L1: the body already committed writes; repeating it is not safe.  The caller records a
        # retryable failure (e.g. a capture job FAILED with this code) instead.
        raise SpdmStorageError(NOT_PREPARED, "드라이브 자료를 미리 준비하지 못했습니다. 다시 시도하세요.")
    raise DriveReadMiss(kind, rel, token)


@contextmanager
def committed_phase() -> Iterator[None]:
    """:func:`after_commit` for one block (a body that registers several requests resets it after each)."""
    session = current_session()
    if session is None:
        yield
        return
    previous = session.committed
    session.committed = True
    try:
        yield
    finally:
        session.committed = previous


def after_commit() -> None:
    """Mark the current read session: the body has committed writes (L1).

    From here on a read the session lacks raises ``DRIVE_READ_NOT_PREPARED``
    instead of :class:`DriveReadMiss`, so ``run`` never repeats a body whose
    first part is already durable.  No-op outside a read session (local mode).
    """
    session = current_session()
    if session is not None:
        session.committed = True


def run(body: Callable[[], T], *, prepare: Callable[[DriveReadSession], None] | None = None,
        rounds: int = MAX_ROUNDS, scope: str | None = None, priority: str = "INTERACTIVE") -> T:
    """Run ``body`` (which opens its own DB connections) with a drive read session (module docstring).

    Outside scx mode, or inside an already open session, this is just ``body()``.
    """
    if not active() or current_session() is not None:
        return body()
    if connection_held():
        raise RuntimeError("drive read session must start without an open database connection")
    _cleanup_orphans()
    session = _new_session(priority=priority, scope=scope)
    token = _session_var.set(session)
    try:
        load_overlay(session)
        if prepare is not None:
            prepare(session)
        _load_listed_registered(session)
        attempts = max(1, int(rounds))
        for attempt in range(attempts):
            session.rounds = attempt + 1
            session.final_round = attempt == attempts - 1
            try:
                result = body()
            except DriveReadMiss as miss:
                if connection_held():
                    raise RuntimeError("drive read miss escaped while a database connection was still open") from None
                _fulfil(session, miss)
                _load_listed_registered(session)
                continue
            _record_downloads(session)
            return result
        raise RuntimeError("unreachable: the final round never raises DriveReadMiss")
    finally:
        _session_var.reset(token)
        _close_session(session)


def read_session(*, rounds: int = MAX_ROUNDS, priority: str = "INTERACTIVE",
                 prepare: Callable[..., None] | None = None):
    """Route decorator: run a sync endpoint (which opens its own connections) in :func:`run`.

    ``prepare(session, *args, **kwargs)`` gets the endpoint arguments.  Local mode: the endpoint unchanged.
    """
    def decorate(endpoint: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(endpoint)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            step = (lambda session: prepare(session, *args, **kwargs)) if prepare is not None else None
            return run(lambda: endpoint(*args, **kwargs), prepare=step, rounds=rounds, priority=priority)
        # FastAPI resolves string annotations in the wrapper's module; hand it the endpoint's resolved ones.
        wrapper.__signature__ = inspect.signature(endpoint, eval_str=True)  # type: ignore[attr-defined]
        return wrapper
    return decorate


@contextmanager
def _ephemeral_session() -> Iterator[DriveReadSession]:
    """The current session, or a temporary one (no DB connection held) cleaned up on exit."""
    session = current_session()
    if session is not None:
        yield session
        return
    if connection_held():
        raise SpdmStorageError(NOT_PREPARED, "드라이브 자료를 아직 준비하지 못했습니다. 잠시 후 다시 시도하세요.")
    temporary = _new_session(priority="INTERACTIVE", scope=None, ephemeral=True)
    token = _session_var.set(temporary)
    try:
        load_overlay(temporary)
        yield temporary
    finally:
        _session_var.reset(token)
        _close_session(temporary)


# --- live drive calls (never with a DB connection) ---------------------------------------------

def drive_path(spdm_root: str, rel: str) -> str:
    return f"{spdm_root}/{rel}" if rel else spdm_root


def check_rel(rel: str) -> str:
    try:
        return validate_drive_rel_path(rel, allow_empty=True)
    except ValueError as exc:
        raise SpdmStorageError("SPDM_PATH_INVALID", "SPDM 상대 경로가 올바르지 않습니다.") from exc


def _priority(session: DriveReadSession | None) -> Any:
    return drive_gateway.priority(session.priority if session is not None else "INTERACTIVE")


def _call(fn: Callable[[Any], T]) -> T:
    if connection_held():
        raise RuntimeError("drive call while the thread holds a database connection")
    try:
        gateway = drive_gateway.get_drive_gateway()
    except drive_gateway.DriveStartupError as exc:
        raise SpdmStorageError("DRIVE_UNAVAILABLE", str(exc)[:200]) from None
    if gateway is None:
        raise SpdmStorageError("DRIVE_MODE_DISABLED", "SCX 드라이브 모드가 아닙니다.")
    try:
        return fn(gateway)
    except SpdmStorageError:
        raise
    except Exception as error:  # adapter DriveError carries a code; anything else is a bug
        if drive_gateway.drive_error_code(error) is None:
            raise
        raise drive_gateway.to_storage_error(error) from None


def _live_list(session: DriveReadSession | None, spdm_root: str, rel: str) -> list[Any]:
    entries = _call(lambda gateway: gateway.list_dir(drive_path(spdm_root, rel), priority=_priority(session)))
    entries = list(entries)
    _observe(entries)
    return entries


def _live_stat(session: DriveReadSession | None, spdm_root: str, rel: str) -> Any | None:
    return _call(lambda gateway: gateway.stat(drive_path(spdm_root, rel), priority=_priority(session)))


# --- raw (live drive) view, session cached ---------------------------------------------------

def _split(rel: str) -> tuple[str | None, str]:
    if not rel:
        return None, ""
    parent, _, name = rel.rpartition("/")
    return parent, name


def listing(spdm_root: str, rel: str) -> list[Any]:
    """Raw drive entries of folder ``rel`` (``SPDM_NOT_FOUND`` / ``SPDM_INVALID_PATH`` storage errors)."""
    session = current_session()
    if session is not None:
        if rel in session.listings:
            return session.listings[rel]
        error = session.errors.get(("list", rel))
        if error is not None:
            raise error
    if connection_held():
        _need(session, "list", rel)
    entries = _live_list(session, spdm_root, rel)
    if session is not None:
        session.listings[rel] = entries
    return entries


def raw_entry(spdm_root: str, rel: str) -> Any | None:
    """Raw drive entry of ``rel`` or ``None`` (from the parent listing when the session has it)."""
    session = current_session()
    parent, name = _split(rel)
    if session is not None:
        if rel in session.stats:
            return session.stats[rel]
        if parent is not None and parent in session.listings:
            return next((entry for entry in session.listings[parent] if entry.name == name), None)
        parent_error = session.errors.get(("list", parent)) if parent is not None else None
        if parent_error is not None and parent_error.code == "SPDM_NOT_FOUND":
            return None
        error = session.errors.get(("stat", rel))
        if error is not None:
            raise error
    if connection_held():
        _need(session, "stat", rel)
    entry = _live_stat(session, spdm_root, rel)
    if session is not None:
        session.stats[rel] = entry
    return entry


# --- accepted versions overlay (pending / ignored / missing) -----------------------------------

def load_overlay(session: DriveReadSession) -> None:
    """Rows whose live state must not be applied: review PENDING/IGNORED or source MISSING (05 §4)."""
    from . import sources

    if connection_held():
        raise RuntimeError("overlay must be loaded without an open database connection")
    with connect() as conn:
        session.overlay = sources.load_overlay(conn, session.root_key)
    session.registered.clear()
    session.registered_checked.clear()


def accepted(rel: str) -> Accepted | None:
    session = current_session()
    return session.overlay.get(rel) if session is not None else None


def _registrable(rel: str) -> bool:
    from .sources import RELEVANT_SUFFIXES

    return PurePosixPath(rel).suffix.casefold() in RELEVANT_SUFFIXES


def _load_registered(session: DriveReadSession, rels: list[str]) -> None:
    """Current registered rows of ``rels`` into the session (short DB connection; never with one held)."""
    from . import sources

    wanted = sorted({rel for rel in rels if rel not in session.registered_checked})
    if not wanted:
        return
    if connection_held():
        raise RuntimeError("registered versions must be loaded without an open database connection")
    with connect() as conn:
        for start in range(0, len(wanted), REGISTERED_QUERY_CHUNK):
            chunk = wanted[start:start + REGISTERED_QUERY_CHUNK]
            session.registered.update(sources.load_registered(conn, session.root_key, chunk))
    session.registered_checked.update(wanted)


def _listed_files(session: DriveReadSession) -> list[str]:
    found = []
    for folder, entries in session.listings.items():
        for entry in entries:
            if getattr(entry, "kind", None) == "file":
                rel = f"{folder}/{entry.name}" if folder else str(entry.name)
                if rel not in session.registered_checked and _registrable(rel):
                    found.append(rel)
    for rel, entry in session.stats.items():
        if entry is not None and getattr(entry, "kind", None) == "file" and rel not in session.registered_checked \
                and _registrable(rel):
            found.append(rel)
    return found


def _load_listed_registered(session: DriveReadSession) -> None:
    """Registered rows of every file the session has listed so far (one batch, no extra round)."""
    if not connection_held():
        _load_registered(session, _listed_files(session))


def registered(rel: str) -> Accepted | None:
    """Registered current version of ``rel`` whose live drive state must not be applied unseen (M2).

    Only rows of classified request folders count (``scoped``); files nobody
    classifies (no request scope) are read live as before.  Fail closed: with
    a DB connection held and the row not loaded yet this is a read miss.
    """
    session = current_session()
    if session is not None:
        item = session.overlay.get(rel)
        if item is not None:
            return item
    if not _registrable(rel):
        return None
    if session is None:
        if connection_held():
            raise SpdmStorageError(NOT_PREPARED, "드라이브 자료를 아직 준비하지 못했습니다. 잠시 후 다시 시도하세요.")
        from . import sources

        with connect() as conn:
            found = sources.load_registered(conn, _root_key(), [rel])
        return found.get(rel)
    if rel not in session.registered_checked:
        if connection_held():
            _need(session, "registered", rel)
        _load_registered(session, [rel, *_listed_files(session)])
    return session.registered.get(rel)


def overlay_below(rel: str) -> list[Accepted]:
    session = current_session()
    if session is None or not session.overlay:
        return []
    prefix = f"{rel}/" if rel else ""
    return [item for path, item in session.overlay.items() if path.startswith(prefix) and path != rel]


# --- content ----------------------------------------------------------------------------------

def content(spdm_root: str, rel: str, version: Version, *, masked: bool, max_bytes: int) -> LocalCopy:
    """Local copy of ``rel`` at ``version`` inside the current session (see :func:`read_bytes`)."""
    if version.size is not None and version.size > max_bytes:
        raise SpdmStorageError("SPDM_FILE_TOO_LARGE", "원본 파일이 허용 크기를 초과했습니다.")
    session = current_session()
    if session is None:
        raise RuntimeError("drive content needs a read session (use read_bytes/open_stream)")
    key = (rel, version.token)
    found = session.files.get(key)
    if found is not None:
        return found
    error = session.errors.get(("content", rel))
    if error is not None:
        raise error
    if connection_held():
        _need(session, "old_content" if masked else "content", rel, version.token)
    return _old_content(session, rel) if masked else _download(session, spdm_root, rel, version, max_bytes)


def read_bytes(spdm_root: str, rel: str, version: Version, *, masked: bool, max_bytes: int) -> tuple[bytes, str]:
    """(bytes, sha256) of ``rel`` at ``version``; outside a session a temporary one is used and removed."""
    with _ephemeral_session():
        copy = content(spdm_root, rel, version, masked=masked, max_bytes=max_bytes)
        data = server_local.read_file(copy.path)
    if len(data) > max_bytes:
        raise SpdmStorageError("SPDM_FILE_TOO_LARGE", "원본 파일이 허용 크기를 초과했습니다.")
    return data, copy.sha256


@contextmanager
def open_stream(spdm_root: str, rel: str, version: Version, *, masked: bool, max_bytes: int):
    with _ephemeral_session():
        copy = content(spdm_root, rel, version, masked=masked, max_bytes=max_bytes)
        with server_local.open_file(copy.path) as stream:
            yield stream


def local_copy_sha256(spdm_root: str, rel: str, version: Version, *, masked: bool, max_bytes: int) -> tuple[int, str]:
    with _ephemeral_session():
        copy = content(spdm_root, rel, version, masked=masked, max_bytes=max_bytes)
        return copy.size, copy.sha256


def _recorded_blob(session: DriveReadSession, rel: str, version: Version) -> LocalCopy | None:
    from . import sources

    with connect() as conn:
        row = sources.current_row(conn, session.root_key, rel)
    if not row or not row.get("stored_path") or not row.get("sha256"):
        return None
    recorded = Version(token=str(row["version_token"]), size=row.get("size_bytes"),
                       modified_us=epoch_us(row.get("modified_at")), sha1=normal_sha1(row.get("sha1")))
    if not recorded.same_as(version):
        return None
    path = blob_store().find(str(row["sha256"]))
    if path is None:
        return None
    return LocalCopy(path=path, sha256=str(row["sha256"]), size=int(row.get("size_bytes") or server_local.file_size(path)), blob=True)


def _download(session: DriveReadSession, spdm_root: str, rel: str, version: Version, max_bytes: int) -> LocalCopy:
    key = (rel, version.token)
    if version.size is not None and version.size > BLOB_THRESHOLD_BYTES:
        reused = _recorded_blob(session, rel, version)
        if reused is not None:
            session.files[key] = reused
            return reused
    _reserve_staging(session, int(version.size or 0))
    target = session.next_dir()
    result = _call(lambda gateway: gateway.download_to(drive_path(spdm_root, rel), target,
                                                       max_bytes=max_bytes, priority=_priority(session)))
    local = Path(result.local_path)
    if not server_local.is_regular_file_inside(local, target):
        raise SpdmStorageError("DRIVE_INTERNAL", "드라이브 다운로드 위치가 올바르지 않습니다.")
    got = version_of(result.entry)
    if not got.same_as(version):
        server_local.remove_tree(target)
        raise SpdmStorageError("SPDM_FILE_BUSY", "드라이브 원본이 조회 이후 바뀌었습니다. 다음 확인에서 다시 읽습니다.")
    size = int(result.size)
    sha256 = str(result.sha256).lower()
    stored: str | None = None
    copy = LocalCopy(path=local, sha256=sha256, size=size)
    session.staged_bytes += size
    if size > BLOB_THRESHOLD_BYTES:
        blob = blob_store().put(local, sha256)
        session.staged_bytes -= size   # moved out of the session folder into the blob store
        copy = LocalCopy(path=blob, sha256=sha256, size=size, blob=True)
        stored = str(blob)
    session.files[key] = copy
    session.downloads.append(DownloadRecord(rel_path=rel, version=version, sha256=sha256, size=size, stored_path=stored))
    return copy


def _staging_room(session: DriveReadSession, size: int) -> bool:
    if session.staged_bytes + size > SESSION_STAGING_MAX_BYTES:
        return False
    free = server_local.disk_free(session.staging)
    return free is None or free - size >= STAGING_MIN_FREE_BYTES


def _reserve_staging(session: DriveReadSession, size: int) -> None:
    """L3: one staging byte budget per read session and a free-space floor on the staging disk."""
    if not _staging_room(session, size):
        raise SpdmStorageError(STAGING_FULL, "서버 임시 저장 공간이 부족해 드라이브 파일을 받을 수 없습니다. 잠시 후 다시 시도하세요.")


def fetch_version(rel: str, version: Version, *, max_bytes: int = MAX_FILE_BYTES) -> DownloadRecord:
    """Download ``rel`` at exactly ``version`` (no DB connection held) and return its sha256 (accept, L4).

    Raises ``SPDM_FILE_BUSY`` when the drive no longer holds that version.
    Files above the blob threshold stay in the blob store (``stored_path``).
    """
    if connection_held():
        raise RuntimeError("drive download while the thread holds a database connection")
    rel = check_rel(rel)
    with _ephemeral_session() as session:
        copy = _download(session, session.spdm_root, rel, version, max_bytes)
        return DownloadRecord(rel_path=rel, version=version, sha256=copy.sha256, size=copy.size,
                              stored_path=str(copy.path) if copy.blob else None)


def _old_content(session: DriveReadSession, rel: str) -> LocalCopy:
    """Content of the registered version of a file whose drive copy changed or disappeared."""
    item = session.overlay.get(rel) or session.registered.get(rel)
    changed = SpdmStorageError(SOURCE_CHANGED, "드라이브 원본이 바뀌어 등록된 버전을 다시 읽을 수 없습니다. 원본 변경을 확인하세요.")
    if item is None or not item.sha256:
        raise changed
    name = PurePosixPath(rel).name
    if not name or name in {".", ".."} or "\\" in rel or ":" in rel or ".." in rel.split("/"):
        raise changed   # the local file name comes from a DB value: never a path outside the session folder
    key = (rel, item.version.token)
    path = blob_store().find(item.sha256)
    if path is not None:
        copy = LocalCopy(path=path, sha256=item.sha256, size=server_local.file_size(path), blob=True)
        session.files[key] = copy
        return copy
    from . import sources

    with connect() as conn:
        data = sources.registered_content(conn, item.sha256)
    if data is None or hashlib.sha256(data).hexdigest() != item.sha256:
        raise changed
    _reserve_staging(session, len(data))
    target = session.next_dir() / name
    server_local.write_file(target, data)
    session.staged_bytes += len(data)
    copy = LocalCopy(path=target, sha256=item.sha256, size=len(data))
    session.files[key] = copy
    return copy


# --- miss fulfilment (no DB connection) --------------------------------------------------------

def _fulfil(session: DriveReadSession, miss: DriveReadMiss) -> None:
    if miss.kind == "list":
        _prefetch_tree(session, miss.rel)
    elif miss.kind == "stat":
        try:
            session.stats[miss.rel] = _live_stat(session, session.spdm_root, miss.rel)
        except SpdmStorageError as error:
            session.errors[("stat", miss.rel)] = error
    elif miss.kind == "old_content":
        try:
            _old_content(session, miss.rel)
        except SpdmStorageError as error:
            session.errors[("content", miss.rel)] = error
    elif miss.kind == "content":
        _prefetch_content(session, miss.rel)
    elif miss.kind == "registered":
        _load_registered(session, [miss.rel, *_listed_files(session)])
    elif miss.kind == "scopes":
        for scope in miss.rel.split("\n"):
            prefetch_content(session, scope)
            session.prepared_scopes.add(scope)
    else:  # pragma: no cover - programming error
        raise RuntimeError(f"unknown drive read miss {miss.kind}")


def _prefetch_tree(session: DriveReadSession, rel: str) -> None:
    """List ``rel``, its siblings' subtrees and up to ``LIST_PREFETCH_DEPTH`` folder levels below (bounded).

    Starting at the parent covers the usual walk (a scan or progress check visits
    sibling Scenes next) in one round; the missed folder is listed first.
    """
    parent, _ = _split(rel)
    queue: list[tuple[str, int]] = [(rel, 1)]
    if parent is not None:
        queue.append((parent, 0))
    listed = 0
    while queue and listed < LIST_PREFETCH_MAX:
        current, depth = queue.pop(0)
        if current in session.listings:
            entries = session.listings[current]
        else:
            try:
                entries = _live_list(session, session.spdm_root, current)
            except SpdmStorageError as error:
                if current == rel:
                    session.errors[("list", current)] = error
                continue
            session.listings[current] = entries
            listed += 1
        if depth >= LIST_PREFETCH_DEPTH:
            continue
        for entry in entries:
            if getattr(entry, "kind", None) == "dir" and not str(entry.name).startswith("."):
                child = f"{current}/{entry.name}" if current else str(entry.name)
                if child not in session.listings:
                    queue.append((child, depth + 1))


def effective_version(rel: str, entry: Any) -> tuple[Version, bool]:
    """(version the dashboard uses, masked).  Masked = registered version differs from the live one."""
    live = version_of(entry)
    item = registered(rel)
    if item is None or not item.scoped or item.version.same_as(live):
        return live, False
    return item.version, True


def unapplied(rel: str) -> bool:
    """True when the drive bytes of ``rel`` are not the registered version the dashboard shows."""
    if accepted(rel) is not None:
        return True
    session = current_session()
    raw = raw_entry(session.spdm_root if session is not None else str(_settings().spdm_root or ""), rel)
    if raw is None or getattr(raw, "kind", None) != "file":
        return False
    return effective_version(rel, raw)[1]


def _cached_files_below(session: DriveReadSession, base: str) -> Iterator[tuple[str, Any]]:
    stack = [base]
    while stack:
        folder = stack.pop()
        for entry in session.listings.get(folder, []):
            name = str(entry.name)
            if name.startswith("."):
                continue
            child = f"{folder}/{name}" if folder else name
            if getattr(entry, "kind", None) == "dir":
                stack.append(child)
            elif getattr(entry, "kind", None) == "file":
                yield child, entry


def _prefetch_content(session: DriveReadSession, rel: str) -> None:
    """Download ``rel`` and, speculatively, the other readable files of the session scope (bounded)."""
    parent, _ = _split(rel)
    entry = raw_entry_cached(session, rel)
    if entry is None:
        try:
            entry = _live_stat(session, session.spdm_root, rel)
        except SpdmStorageError as error:
            session.errors[("content", rel)] = error
            return
        if entry is None:
            session.errors[("content", rel)] = SpdmStorageError("SPDM_NOT_FOUND", "드라이브에서 항목을 찾을 수 없습니다.")
            return
    version, masked = effective_version(rel, entry)
    try:
        if masked:
            _old_content(session, rel)
        else:
            _download(session, session.spdm_root, rel, version, MAX_FILE_BYTES)
    except SpdmStorageError as error:
        session.errors[("content", rel)] = error
        return
    suffix = PurePosixPath(rel).suffix.casefold()
    if suffix in DECK_SUFFIXES:
        # decks (materials): the other decks of the same folder (INCLUDE neighbours)
        base, wanted, file_limit = parent or "", DECK_SUFFIXES, CONTENT_PREFETCH_FILE_BYTES
    elif suffix in CAPTURE_SUFFIXES or suffix in VALUE_SUFFIXES:
        # captures: the readable result files of the session scope (one round instead of one per file)
        base = session.scope if session.scope is not None and _is_below(rel, session.scope) else (parent or "")
        wanted, file_limit = CAPTURE_SUFFIXES | VALUE_SUFFIXES, BLOB_THRESHOLD_BYTES
    else:
        return
    budget_bytes = CONTENT_PREFETCH_MAX_BYTES
    budget_files = CONTENT_PREFETCH_MAX_FILES
    for path, candidate in _cached_files_below(session, base):
        if budget_files <= 0 or budget_bytes <= 0:
            break
        if PurePosixPath(path).suffix.casefold() not in wanted:
            continue
        if any(part.casefold() in SKIPPED_CAPTURE_DIRS for part in path.split("/")[len(base.split("/")) if base else 0:-1]):
            continue
        candidate_version, masked = effective_version(path, candidate)
        size = candidate_version.size or 0
        if masked or size > file_limit or (path, candidate_version.token) in session.files:
            continue
        if ("content", path) in session.errors:
            continue
        if not _staging_room(session, size):
            break
        budget_files -= 1
        budget_bytes -= size
        try:
            _download(session, session.spdm_root, path, candidate_version, MAX_FILE_BYTES)
        except SpdmStorageError:
            continue   # its own read reports the error


def raw_entry_cached(session: DriveReadSession, rel: str) -> Any | None:
    if rel in session.stats:
        return session.stats[rel]
    parent, name = _split(rel)
    if parent is not None and parent in session.listings:
        return next((entry for entry in session.listings[parent] if entry.name == name), None)
    return None


def _is_below(rel: str, scope: str) -> bool:
    return not scope or rel == scope or rel.startswith(scope.rstrip("/") + "/")


def prefetch_content(session: DriveReadSession, scope: str, *, max_bytes: int = CONTENT_PREFETCH_MAX_BYTES) -> None:
    """List ``scope`` (capture rules: no dot or excluded folders) and download its capture files (no DB connection)."""
    stack = [scope]
    listed = 0
    while stack and listed < 5000:
        folder = stack.pop()
        try:
            entries = listing(session.spdm_root, folder)
        except SpdmStorageError as error:
            session.errors[("list", folder)] = error
            continue
        listed += 1
        for entry in entries:
            name = str(entry.name)
            if getattr(entry, "kind", None) == "dir" and not name.startswith(".") and name.casefold() not in SKIPPED_CAPTURE_DIRS:
                stack.append(f"{folder}/{name}" if folder else name)
    budget = max_bytes
    for path, entry in _cached_files_below(session, scope):
        if PurePosixPath(path).suffix.casefold() not in CAPTURE_SUFFIXES:
            continue
        if any(part.casefold() in SKIPPED_CAPTURE_DIRS for part in path[len(scope):].split("/")[:-1]):
            continue
        version, masked = effective_version(path, entry)
        if (version.size or 0) > BLOB_THRESHOLD_BYTES or (path, version.token) in session.files:
            continue
        budget -= version.size or 0
        if budget < 0 or not _staging_room(session, version.size or 0):
            break
        try:
            if masked:
                _old_content(session, path)
            else:
                _download(session, session.spdm_root, path, version, MAX_FILE_BYTES)
        except SpdmStorageError as error:
            session.errors[("content", path)] = error


VALUE_SUFFIXES = frozenset({".csv", ".json", ".tsv", ".txt"})


def prefetch_values(session: DriveReadSession, folder: str, *, max_file_bytes: int = BLOB_THRESHOLD_BYTES,
                    max_total_bytes: int = 128 * 1024 * 1024) -> None:
    """Prepare step: list one folder and download its value files (semantic mapping refresh)."""
    try:
        entries = listing(session.spdm_root, folder)
    except SpdmStorageError as error:
        session.errors[("list", folder)] = error
        return
    budget = max_total_bytes
    for entry in entries:
        name = str(entry.name)
        if getattr(entry, "kind", None) != "file" or PurePosixPath(name).suffix.casefold() not in VALUE_SUFFIXES:
            continue
        path = f"{folder}/{name}" if folder else name
        version, masked = effective_version(path, entry)
        if (version.size or 0) > max_file_bytes or (path, version.token) in session.files:
            continue
        budget -= version.size or 0
        if budget < 0 or not _staging_room(session, version.size or 0):
            break
        try:
            _old_content(session, path) if masked else _download(session, session.spdm_root, path, version, MAX_FILE_BYTES)
        except SpdmStorageError as error:
            session.errors[("content", path)] = error


def require_content(scopes: Any) -> None:
    """Make sure the capture files below ``scopes`` are in the session before a body writes anything.

    Call it where a body is about to start writes that are followed by file
    reads (registration, capture retry, refresh capture): with a DB connection
    held it raises :class:`DriveReadMiss` (``run`` downloads, then repeats the
    body), so the later reads never miss after a commit.  No-op outside a
    drive read session (local mode).
    """
    session = current_session()
    if session is None or session.ephemeral:
        return
    wanted = sorted({str(scope) for scope in scopes} - session.prepared_scopes)
    if not wanted:
        return
    if not connection_held():
        for scope in wanted:
            prefetch_content(session, scope)
            session.prepared_scopes.add(scope)
        return
    if session.final_round or session.committed:
        return   # the reads themselves report what is missing
    raise DriveReadMiss("scopes", "\n".join(wanted))


def _record_downloads(session: DriveReadSession) -> None:
    if not session.downloads:
        return
    from . import sources

    try:
        with connect() as conn:
            sources.record_downloads(conn, session.root_key, session.downloads, session.scope_ids)
    except Exception:  # noqa: BLE001 - bookkeeping only; the next sync records again
        logger.warning("SCX drive download bookkeeping failed", exc_info=True)
    session.downloads.clear()


# --- server blob store (05 §3) ----------------------------------------------------------------

_blob_lock = threading.Lock()
_blob: BlobStore | None = None


def blob_store() -> BlobStore:
    global _blob
    settings = _settings()
    with _blob_lock:
        if _blob is None or _blob.directory != settings.blob_dir or _blob.max_bytes != settings.blob_max_bytes:
            _blob = BlobStore(settings.blob_dir, settings.blob_max_bytes,
                              reserved=(settings.work_dir, settings.staging_dir), outside=(settings.staging_dir,))
        return _blob


__all__ = [
    "Accepted", "BLOB_THRESHOLD_BYTES", "DriveReadMiss", "after_commit", "committed_phase", "fetch_version", "normal_sha1", "registered",
    "unapplied", "DriveReadSession", "LocalCopy", "NOT_PREPARED",
    "SOURCE_CHANGED", "Version", "accepted", "active", "blob_store", "check_rel", "content", "current_session",
    "drive_path", "effective_version", "epoch_us", "listing", "load_overlay", "local_copy_sha256", "make_token",
    "open_stream", "overlay_below", "prefetch_content", "prefetch_values", "raw_entry", "read_bytes", "read_session", "require_content",
    "run",
    "token_observation",
    "version_of",
]
