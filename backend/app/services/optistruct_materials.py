"""Usage-environment materials: background parse of one OptiStruct input file, cached in the DB.

Rule (docs/features/materials-optistruct.md): the deck of a usage Scene is the
``.fem`` file directly inside the Scene folder, else directly inside its Case
folder (``materials_catalog._usage_candidates``).  The file is 500–1000 MB, so:

* a request never parses: it looks up ``materials_deck_cache`` by (root_key,
  rel_path, solver) and serves the row when its fingerprint (local size+mtime,
  drive ``version_token``), parser version and INCLUDE fingerprints still match;
* otherwise it queues one background parse (at most ``MAX_CONCURRENT`` per
  process) and answers ``QUEUED``/``RUNNING`` with progress; the screen polls;
* the job runs **without** a DB connection while it reads (drive mode: one
  ``drive_reads.run`` session, the file is downloaded once into the blob store
  and reused by sha256 while its version is unchanged), then writes the row with
  one short connection.

Nothing here writes to SPDM or the drive.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Iterator

from ..database_connection import ConnectionLike, connect, rows
from ..parsers.optistruct_deck_parser import PARSER_VERSION, OptiStructDeckParser, OptiStructParseError
from .drive import reads as drive_reads
from .storage import provider_for_root
from .storage import server_local
from .storage.drive import DriveRoot
from .storage.provider import SpdmStorageError

_LOG = logging.getLogger(__name__)

SOLVER = "OPTISTRUCT"
INPUT_SUFFIXES = frozenset({".fem"})
DEFAULT_MAX_INPUT_BYTES = 2 * 1024 ** 3
DEFAULT_MAX_PARSE_SECONDS = 1800
MAX_CONCURRENT = 1
_ERROR_MESSAGE_LIMIT = 400
RETRY_INTERVAL_SECONDS = 60.0          # explicit [다시 분석] at most once per file per minute (process-wide)
TRANSIENT_BACKOFF_SECONDS = 60.0       # a storage/drive hiccup is shown, not cached, and re-queued after this
# Storage errors that are a property of the file version (cached as FAILED like a parse error); every other
# SpdmStorageError (staging full, drive timeout/unavailable, busy file …) and OSError is transient.
_PERMANENT_STORAGE_CODES = frozenset({"SPDM_FILE_TOO_LARGE", "SPDM_PATH_INVALID"})


def max_input_bytes() -> int:
    """``SIMDASH_OPTISTRUCT_MAX_BYTES`` (default 2 GiB, 1 MiB–16 GiB): one input file plus its INCLUDE files."""
    return _env_int("SIMDASH_OPTISTRUCT_MAX_BYTES", DEFAULT_MAX_INPUT_BYTES, 1024 ** 2, 16 * 1024 ** 3)


def max_parse_seconds() -> int:
    """``SIMDASH_OPTISTRUCT_MAX_SECONDS`` (default 1800 s, 10 s–4 h)."""
    return _env_int("SIMDASH_OPTISTRUCT_MAX_SECONDS", DEFAULT_MAX_PARSE_SECONDS, 10, 4 * 3600)


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _LOG.warning("%s=%r is not an integer; using %s", name, raw, default)
        return default
    return min(max(value, low), high)


def fingerprint(entry: Any) -> str:
    """Drive: ``drive:<version_token>``; local: size and modification time (ns)."""
    etag = getattr(entry, "etag", None)
    if etag:
        return f"drive:{etag}"
    return f"local:{int(getattr(entry, 'size', 0) or 0)}:{int(getattr(entry, 'modified_ns', 0) or 0)}"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- cache rows ------------------------------------------------------------------------------

_COLUMNS = ("id,root_key,rel_path,solver,parser_version,fingerprint,size_bytes,status,deck_json,dependencies_json,"
            "blob_sha256,error_code,error_message,parse_seconds,created_at,updated_at")


def load_row(conn: ConnectionLike, root_key: str, rel_path: str) -> dict[str, Any] | None:
    found = rows(conn.execute(f"SELECT {_COLUMNS} FROM materials_deck_cache WHERE root_key=? AND rel_path=? AND solver=?",
                              [root_key, rel_path, SOLVER]))
    return found[0] if found else None


def _store(*, root_key: str, rel_path: str, fp: str, size: int | None, status: str, deck: dict[str, Any] | None,
           dependencies: list[dict[str, Any]], blob_sha256: str | None, error_code: str | None,
           error_message: str | None, seconds: float | None) -> None:
    now = _now()
    # allow_nan=False: NaN/Infinity is not JSON and would make every later response fail (ValueError here
    # instead; run_job then stores FAILED).
    payload = json.dumps(deck, ensure_ascii=False, separators=(",", ":"), allow_nan=False) if deck is not None else None
    deps = json.dumps(dependencies, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    message = (error_message or "")[:_ERROR_MESSAGE_LIMIT] or None
    with connect() as conn:
        # One statement (PostgreSQL and DuckDB): two workers or processes finishing the same file never race
        # a SELECT-then-INSERT into the unique (root_key, rel_path, solver) constraint.
        conn.execute(
            f"INSERT INTO materials_deck_cache({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (root_key, rel_path, solver) DO UPDATE SET parser_version=excluded.parser_version,"
            "fingerprint=excluded.fingerprint,size_bytes=excluded.size_bytes,status=excluded.status,"
            "deck_json=excluded.deck_json,dependencies_json=excluded.dependencies_json,"
            "blob_sha256=excluded.blob_sha256,error_code=excluded.error_code,error_message=excluded.error_message,"
            "parse_seconds=excluded.parse_seconds,updated_at=excluded.updated_at",
            [f"mdc-{uuid.uuid4().hex}", root_key, rel_path, SOLVER, PARSER_VERSION, fp, size, status, payload, deps,
             blob_sha256, error_code, message, seconds, now, now])


# --- background jobs (in process, like case_finalization_jobs) ------------------------------

@dataclass
class JobSpec:
    key: str
    root: Any                      # Path (local) or DriveRoot
    root_key: str
    rel_path: str
    fingerprint: str
    size: int
    request_relative_path: str
    blob_sha256: str | None = None
    resolve_include: Callable[[str, str, str], str] | None = None


@dataclass
class JobState:
    spec: JobSpec
    status: str = "QUEUED"
    bytes_done: int = 0
    bytes_total: int = 0
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None


_lock = threading.Condition()
_queue: deque[str] = deque()
_jobs: dict[str, JobState] = {}
_running: set[str] = set()
_transient: dict[str, dict[str, Any]] = {}     # file key → last transient failure (not cached in the DB)
_last_retry: dict[str, float] = {}             # file key → monotonic time of the last accepted retry


def file_key(root_key: str, rel_path: str) -> str:
    return f"{root_key}:{rel_path.casefold()}"


def job_key(root_key: str, rel_path: str, fp: str) -> str:
    return f"{root_key}:{rel_path.casefold()}:{fp}"


def job_state(key: str) -> dict[str, Any] | None:
    with _lock:
        state = _jobs.get(key)
        if state is None:
            return None
        return {"status": state.status, "bytes_done": state.bytes_done, "bytes_total": state.bytes_total or state.spec.size,
                "queued_at": state.queued_at, "started_at": state.started_at,
                "queue_position": (list(_queue).index(key) + 1) if key in _queue else 0}


def submit(spec: JobSpec) -> dict[str, Any]:
    with _lock:
        if spec.key not in _jobs:
            _jobs[spec.key] = JobState(spec=spec, bytes_total=spec.size)
            _queue.append(spec.key)
            _dispatch_locked()
    state = job_state(spec.key)
    assert state is not None
    return state


def _dispatch_locked() -> None:
    while _queue and len(_running) < MAX_CONCURRENT:
        key = _queue.popleft()
        state = _jobs.get(key)
        if state is None:
            continue
        state.status = "RUNNING"
        state.started_at = time.time()
        _running.add(key)
        threading.Thread(target=_work, args=(key,), name=f"optistruct-parse-{key[-8:]}", daemon=True).start()


def _work(key: str) -> None:
    try:
        state = _jobs[key]
        run_job(state)
    except BaseException:  # noqa: BLE001 - a dying job must still free its slot
        _LOG.exception("OptiStruct parse worker stopped unexpectedly")
    finally:
        with _lock:
            _running.discard(key)
            _jobs.pop(key, None)
            _dispatch_locked()
            _lock.notify_all()


def wait_idle(timeout: float = 120.0) -> bool:
    """Block until no parse is queued or running (tests and orderly shutdown)."""
    with _lock:
        return _lock.wait_for(lambda: not _running and not _queue, timeout=timeout)


def reset_for_tests() -> None:
    wait_idle(30.0)
    with _lock:
        _queue.clear()
        _jobs.clear()
        _transient.clear()
        _last_retry.clear()


# --- the parse ------------------------------------------------------------------------------

class _Reader:
    """Opens files of one root for one job (local stable reader, or drive content through the read session)."""

    def __init__(self, spec: JobSpec, limit: int, check: Callable[[], None] | None = None) -> None:
        self.spec = spec
        self.limit = limit
        self.check = check or (lambda: None)
        self.drive = isinstance(spec.root, DriveRoot)
        self.fs = provider_for_root(spec.root)
        self.blob_sha256: str | None = None

    def describe(self, rel: str) -> tuple[int, str]:
        entry = self.fs.stat(rel, follow_links=False, missing_ok=True)
        if entry is None or entry.kind != "file":
            raise OptiStructParseError("MATERIALS_INCLUDE_NOT_FOUND", f"파일을 찾을 수 없습니다: {rel}", 404)
        if getattr(entry, "is_link", False):
            raise OptiStructParseError("MATERIALS_PATH_UNSAFE", "입력 파일이 reparse 또는 symbolic link입니다.")
        return int(entry.size or 0), fingerprint(entry)

    @contextmanager
    def reading(self, rel: str, *, main: bool = False) -> Iterator[tuple[Any, int, str]]:
        size, fp = self.describe(rel)
        if main and fp != self.spec.fingerprint:
            # A queued job whose file changed meanwhile: stop before reading (drive: before downloading).
            raise OptiStructParseError("MATERIALS_FILE_CHANGED", "분석 대기 중 입력 파일이 바뀌었습니다. 다시 분석합니다.", 409)
        if size > self.limit:
            raise OptiStructParseError("MATERIALS_FILE_SIZE_LIMIT", _size_message(self.limit), 413)
        if not self.drive:
            from . import result_registration_paths

            checked = result_registration_paths._safe_existing(self.spec.root, rel)
            self.fs.assert_safe(checked)
            with self.fs.open_read(checked) as stream:
                yield stream, size, fp
            after_size, after_fp = self.describe(rel)
            if (after_size, after_fp) != (size, fp):
                raise OptiStructParseError("MATERIALS_FILE_CHANGED", "분석 중 입력 파일이 바뀌었습니다. 다시 분석합니다.", 409)
            return
        spdm_root = self.spec.root.spdm_root
        raw = drive_reads.raw_entry(spdm_root, rel)
        if raw is None or getattr(raw, "kind", None) != "file":
            raise OptiStructParseError("MATERIALS_INCLUDE_NOT_FOUND", f"파일을 찾을 수 없습니다: {rel}", 404)
        version, masked = drive_reads.effective_version(rel, raw)
        path = None
        if main and self.spec.blob_sha256 and fp == self.spec.fingerprint:
            path = drive_reads.blob_store().find(self.spec.blob_sha256)
            if path is not None:
                self.blob_sha256 = self.spec.blob_sha256
        if path is None:
            # The download itself is bounded by the drive gateway's own timeouts, not MAX_SECONDS (content() takes
            # no deadline); the deadline is checked right before and right after it.
            self.check()
            copy = drive_reads.content(spdm_root, rel, version, masked=masked, max_bytes=self.limit)
            self.check()
            path = copy.path
            if main and copy.blob:
                self.blob_sha256 = copy.sha256
        with server_local.open_file(path) as stream:   # session staging copy or server blob (not SPDM)
            yield stream, size, fp


def _size_message(limit: int) -> str:
    return f"OptiStruct 입력 파일(INCLUDE 포함)은 {limit / 1024 ** 3:.1f} GiB 이하여야 합니다."


def _parse(state: JobState, deadline: float, limit: int,
           dependencies: list[dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
    """Parse ``state.spec``; ``dependencies`` (INCLUDE files read or missing) is filled even when this raises."""
    spec = state.spec

    def check() -> None:
        if time.monotonic() > deadline:
            raise OptiStructParseError("MATERIALS_PARSE_TIME_LIMIT", "입력 파일 분석 시간이 한도를 초과했습니다.", 413)

    reader = _Reader(spec, limit, check)

    def progress(done: int, total: int) -> None:
        if done > limit:
            raise OptiStructParseError("MATERIALS_TOTAL_SIZE_LIMIT", _size_message(limit), 413)
        with _lock:
            state.bytes_done = done
            state.bytes_total = max(total, spec.size)

    @contextmanager
    def open_include(including: str, value: str) -> Iterator[tuple[Any, str, int]]:
        if spec.resolve_include is None:
            raise OptiStructParseError("MATERIALS_INCLUDE_UNAVAILABLE", "INCLUDE를 읽을 수 없습니다.")
        target = spec.resolve_include(spec.request_relative_path, including, value)
        if len(dependencies) >= 500:
            raise OptiStructParseError("MATERIALS_INCLUDE_FILE_LIMIT", "INCLUDE 파일 수가 허용 한도를 초과했습니다.", 413)
        stack = ExitStack()
        try:
            stream, size, fp = stack.enter_context(reader.reading(target))
        except OptiStructParseError as error:
            if error.code == "MATERIALS_INCLUDE_NOT_FOUND":
                # Recorded so that creating the file later invalidates the cached result.
                dependencies.append({"rel_path": target, "fingerprint": None, "size_bytes": None, "missing": True})
            raise
        dependencies.append({"rel_path": target, "fingerprint": fp, "size_bytes": size})
        with stack:
            yield stream, target, size

    with reader.reading(spec.rel_path, main=True) as (stream, size, _fp):
        parser = OptiStructDeckParser(open_include=open_include, on_progress=progress, check=check)
        result = parser.parse(stream, spec.rel_path, size)
    return result, reader.blob_sha256


def _is_transient(error: BaseException) -> bool:
    if isinstance(error, OptiStructParseError):
        return False
    if isinstance(error, SpdmStorageError):
        return getattr(error, "code", None) not in _PERMANENT_STORAGE_CODES
    return isinstance(error, OSError)


def run_job(state: JobState) -> None:
    """Parse ``state.spec`` without a DB connection, then store READY or FAILED (one short connection).

    Transient storage/drive failures are not stored: they are kept in memory for
    ``TRANSIENT_BACKOFF_SECONDS`` (shown as FAILED, then re-queued by the next view).
    """
    spec = state.spec
    started = time.monotonic()
    deadline = started + max_parse_seconds()
    limit = max_input_bytes()
    dependencies: list[dict[str, Any]] = []

    def failed(code: str, message: str) -> None:
        _store(root_key=spec.root_key, rel_path=spec.rel_path, fp=spec.fingerprint, size=spec.size, status="FAILED",
               deck=None, dependencies=dependencies, blob_sha256=None, error_code=code, error_message=message,
               seconds=round(time.monotonic() - started, 3))

    try:
        result, blob = drive_reads.run(lambda: _parse(state, deadline, limit, dependencies), rounds=1,
                                       priority="BACKGROUND")
    except (OptiStructParseError, SpdmStorageError, OSError) as error:
        code = getattr(error, "code", None) or "MATERIALS_SCAN_INCOMPLETE"
        if code == "MATERIALS_FILE_CHANGED":
            return   # the next view sees the new fingerprint and queues a fresh parse
        if _is_transient(error):
            message = (str(error) if isinstance(error, SpdmStorageError)
                       else f"입력 파일을 읽을 수 없습니다: {error.__class__.__name__}")
            _LOG.warning("OptiStruct parse interrupted (not cached): %s %s", spec.rel_path, code)
            with _lock:
                _transient[file_key(spec.root_key, spec.rel_path)] = {
                    "fingerprint": spec.fingerprint, "error_code": code, "error_message": message[:_ERROR_MESSAGE_LIMIT],
                    "until": time.monotonic() + TRANSIENT_BACKOFF_SECONDS}
            return
        failed(code, str(error))
        return
    except Exception as error:  # noqa: BLE001 - recorded so the screen does not poll forever
        _LOG.exception("OptiStruct parse failed: %s", spec.rel_path)
        failed("MATERIALS_PARSE_FAILED", f"입력 파일 분석 중 오류가 발생했습니다: {error.__class__.__name__}")
        return
    seconds = round(time.monotonic() - started, 3)
    try:
        _store(root_key=spec.root_key, rel_path=spec.rel_path, fp=spec.fingerprint, size=spec.size, status="READY",
               deck=result, dependencies=dependencies, blob_sha256=blob, error_code=None, error_message=None,
               seconds=seconds)
    except ValueError:   # not JSON-serialisable (e.g. a non-finite number that slipped through)
        _LOG.exception("OptiStruct parse result not storable: %s", spec.rel_path)
        failed("MATERIALS_PARSE_FAILED", "분석 결과를 저장할 수 없습니다(잘못된 수치).")
        return
    with _lock:
        _transient.pop(file_key(spec.root_key, spec.rel_path), None)
    _LOG.info("OptiStruct materials parsed: %s (%d bytes, %.1f s)", spec.rel_path, spec.size, seconds)


# --- request side ---------------------------------------------------------------------------

def _dependencies_fresh(fs: Any, dependencies: list[dict[str, Any]]) -> bool:
    for item in dependencies:
        rel = str(item.get("rel_path") or "")
        if not rel:
            return False
        try:
            entry = fs.stat(rel, follow_links=False, missing_ok=True)
        except (OSError, SpdmStorageError):
            return False
        if item.get("missing"):
            if entry is not None and entry.kind == "file":   # the missing INCLUDE file appeared
                return False
            continue
        if entry is None or entry.kind != "file" or fingerprint(entry) != item.get("fingerprint"):
            return False
    return True


def _analysis(status: str, *, rel_path: str, size: int, **extra: Any) -> dict[str, Any]:
    payload = {"solver": SOLVER, "status": status, "relative_path": rel_path, "size_bytes": size,
               "max_bytes": max_input_bytes(), "bytes_done": 0, "bytes_total": size, "progress": 0.0,
               "queue_position": 0, "parse_seconds": None, "parsed_at": None, "error_code": None,
               "error_message": None, "cached": False, "transient": False, "retry_after_seconds": None}
    payload.update(extra)
    total = payload.get("bytes_total") or 0
    if status == "READY":
        payload["progress"] = 1.0
    elif total:
        payload["progress"] = round(min(1.0, float(payload.get("bytes_done") or 0) / float(total)), 4)
    return payload


def _accept_retry(key: str) -> tuple[bool, int | None]:
    """(accepted, seconds to wait): one explicit retry per file per ``RETRY_INTERVAL_SECONDS``."""
    now = time.monotonic()
    with _lock:
        last = _last_retry.get(key)
        if last is not None and now - last < RETRY_INTERVAL_SECONDS:
            return False, max(1, int(RETRY_INTERVAL_SECONDS - (now - last) + 0.999))
        _last_retry[key] = now
        if len(_last_retry) > 4096:   # bounded: drop entries older than the interval
            for stale in [item for item, at in _last_retry.items() if now - at >= RETRY_INTERVAL_SECONDS]:
                _last_retry.pop(stale, None)
        return True, None


def deck_status(conn: ConnectionLike, *, root: Any, root_key: str, rel_path: str, entry: Any,
                request_relative_path: str, resolve_include: Callable[[str, str, str], str],
                retry: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """(analysis, deck or None, files) for one input file; queues a parse when the cache is stale."""
    size = int(getattr(entry, "size", 0) or 0)
    limit = max_input_bytes()
    if size > limit:
        raise OptiStructParseError("MATERIALS_FILE_SIZE_LIMIT", _size_message(limit), 413)
    fp = fingerprint(entry)
    row = load_row(conn, root_key, rel_path)
    files = [{"relative_path": rel_path, "size_bytes": size}]
    key = job_key(root_key, rel_path, fp)
    fkey = file_key(root_key, rel_path)
    running = job_state(key)
    retry_after: int | None = None

    def wants_retry() -> bool:
        nonlocal retry_after
        if not retry:
            return False
        accepted, retry_after = _accept_retry(fkey)
        return accepted

    if row and row.get("fingerprint") == fp and row.get("parser_version") == PARSER_VERSION and running is None:
        try:
            dependencies = json.loads(row.get("dependencies_json") or "[]")
        except ValueError:
            dependencies = None
        if isinstance(dependencies, list) and _dependencies_fresh(provider_for_root(root), dependencies):
            files += [{"relative_path": str(item.get("rel_path")), "size_bytes": item.get("size_bytes")}
                      for item in dependencies if not item.get("missing")]
            parsed_at = row.get("updated_at")
            common = {"parse_seconds": row.get("parse_seconds"), "cached": True,
                      "parsed_at": parsed_at.isoformat() if hasattr(parsed_at, "isoformat") else parsed_at}
            if row.get("status") == "READY" and row.get("deck_json"):
                deck = json.loads(row["deck_json"])
                return _analysis("READY", rel_path=rel_path, size=size, bytes_done=size, **common), deck, files
            if row.get("status") == "FAILED" and not wants_retry():
                return _analysis("FAILED", rel_path=rel_path, size=size, error_code=row.get("error_code"),
                                 error_message=row.get("error_message"), retry_after_seconds=retry_after,
                                 **common), None, files
    if running is None:
        with _lock:
            transient = _transient.get(fkey)
            if transient is not None and (transient["fingerprint"] != fp or transient["until"] <= time.monotonic()):
                _transient.pop(fkey, None)
                transient = None
        if transient is not None and not wants_retry():
            return _analysis("FAILED", rel_path=rel_path, size=size, error_code=transient["error_code"],
                             error_message=transient["error_message"], transient=True,
                             retry_after_seconds=retry_after), None, files
        with _lock:
            _transient.pop(fkey, None)
        blob = row.get("blob_sha256") if row and row.get("fingerprint") == fp else None
        running = submit(JobSpec(key=key, root=root, root_key=root_key, rel_path=rel_path, fingerprint=fp, size=size,
                                 request_relative_path=request_relative_path, blob_sha256=blob,
                                 resolve_include=resolve_include))
    return _analysis(running["status"], rel_path=rel_path, size=size, bytes_done=running["bytes_done"],
                     bytes_total=running["bytes_total"], queue_position=running["queue_position"]), None, files


def is_input_name(name: str) -> bool:
    return PurePosixPath(name).suffix.casefold() in INPUT_SUFFIXES


__all__ = ["DEFAULT_MAX_INPUT_BYTES", "INPUT_SUFFIXES", "JobSpec", "SOLVER", "deck_status", "file_key", "fingerprint",
           "is_input_name", "job_state", "load_row", "max_input_bytes", "max_parse_seconds", "reset_for_tests",
           "run_job", "submit", "wait_idle"]
