"""Test-only fake of the ``scx_drive_adapter`` package (contract v0.2 §2–4, §10.3).

Never imported by ``app/``: tests install it as ``sys.modules["scx_drive_adapter"]``
so the production lazy import in ``app.services.drive.gateway`` exercises the
same code path as with the real adapter.  The memory drive simulates
pagination, CONFLICT, NOT_FOUND, BUSY, AUTH_REQUIRED, refresh-token
rotation (``TokenStore.save`` on every session creation and refresh) and the
worker's in-memory token cache (a deleted row does not end a live session).

Stage D2 additions: sha1 present/absent per call kind (``list_has_sha1`` /
``stat_has_sha1``; the real drive's C1/C9 behaviour is unknown), file
modification (``modify_file``: new bytes and time; ``touch``: time only),
deletion (``delete``), persistent error injection (``fail_always`` per op, e.g.
BUSY/TIMEOUT/AUTH_REQUIRED) and an ``on_call`` hook so tests can assert the
calling thread holds no database connection.
"""
from __future__ import annotations

import hashlib
import itertools
import threading
import types
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Literal


class Priority(IntEnum):
    INTERACTIVE = 0
    BACKGROUND = 1


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    LOCKED = "LOCKED"
    BUSY = "BUSY"
    LIMIT = "LIMIT"
    INVALID_PATH = "INVALID_PATH"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    OVERLOADED = "OVERLOADED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"


RETRYABLE = {ErrorCode.LOCKED, ErrorCode.BUSY, ErrorCode.OVERLOADED, ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE}


class DriveError(Exception):
    def __init__(self, code: ErrorCode, message: str, rel_path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = code in RETRYABLE
        self.rel_path = rel_path


@dataclass(frozen=True)
class DriveEntry:
    name: str
    rel_path: str
    kind: Literal["file", "dir", "other"]
    size: int | None
    modified_at: datetime | None
    created_at: datetime | None
    item_id: str | None
    sha1: str | None
    version_token: str
    local_safe: bool


@dataclass(frozen=True)
class DownloadResult:
    local_path: Path
    entry: DriveEntry
    size: int
    sha256: str


@dataclass(frozen=True)
class HealthStatus:
    state: Literal["OK", "DEGRADED", "AUTH_REQUIRED", "UNAVAILABLE"]
    last_success_at: datetime | None
    last_error_code: str | None
    last_error_at: datetime | None
    token_refreshed_at: datetime | None
    queue_depth: int
    in_flight: int


@dataclass(frozen=True)
class TokenBundle:
    server_url: str
    access_token: str
    refresh_token: str | None
    obtained_at: datetime
    account_hint: str | None


@dataclass(frozen=True)
class AdapterConfig:
    server_url: str
    drive_root: str
    client_name: str = "drive-desktop"
    ca_bundle_path: Path | None = None
    temp_dir: Path | None = None
    max_concurrency: int = 1
    queue_size: int = 64
    queue_wait_timeout_s: float = 10.0
    call_timeout_s: float = 60.0
    transfer_timeout_s: float = 600.0
    cache_ttl_s: float = 30.0
    refresh_interval_s: float = 1800.0
    page_size: int = 1000


@dataclass(frozen=True)
class WorkerConfig:
    python_exe: Path
    adapter: AdapterConfig
    work_dir: Path
    start_timeout_s: float = 30.0
    ipc_margin_s: float = 15.0
    restart_backoff_s: tuple[float, ...] = (1.0, 5.0, 30.0)
    token_poll_interval_s: float = 10.0


@dataclass
class _Node:
    kind: Literal["file", "dir"]
    item_id: str
    created_at: datetime
    modified_at: datetime
    content: bytes = b""


@dataclass
class MemoryDrive:
    """Shared-account home ``~`` as a flat path → node map."""

    page_size: int = 3
    list_has_sha1: bool = True
    stat_has_sha1: bool = True
    nodes: dict[str, _Node] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    page_calls: int = 0
    busy_paths: set[str] = field(default_factory=set)
    fail_next: dict[str, ErrorCode] = field(default_factory=dict)
    fail_always: dict[str, ErrorCode] = field(default_factory=dict)
    on_call: Any = None
    sessions: int = 0
    _ids: Any = field(default_factory=lambda: itertools.count(1))
    _clock: Any = field(default_factory=lambda: itertools.count(1))

    def __post_init__(self) -> None:
        self.nodes.setdefault("", self._node("dir"))

    def _now(self) -> datetime:
        return datetime(2026, 10, 7, tzinfo=timezone.utc) + timedelta(seconds=next(self._clock), microseconds=123)

    def _node(self, kind: Literal["file", "dir"], content: bytes = b"") -> _Node:
        now = self._now()
        return _Node(kind=kind, item_id=f"item-{next(self._ids)}", created_at=now, modified_at=now, content=content)

    def modify_file(self, rel: str, content: bytes) -> None:
        """Overwrite an existing file in place (another SPDM user saving a new version)."""
        node = self.nodes[rel]
        node.content = content
        node.modified_at = self._now()

    def touch(self, rel: str) -> None:
        """New modification time, same bytes (a copy tool re-writing an identical file)."""
        self.nodes[rel].modified_at = self._now()

    def delete(self, rel: str) -> None:
        """Remove a file or a whole folder (done on the drive by someone else)."""
        for path in [path for path in self.nodes if path == rel or path.startswith(rel + "/")]:
            del self.nodes[path]

    def downloads(self) -> list[str]:
        return [rel for op, rel in self.calls if op == "download_to"]

    def add_dir(self, rel: str) -> None:
        parts = rel.split("/")
        for index in range(1, len(parts) + 1):
            self.nodes.setdefault("/".join(parts[:index]), self._node("dir"))

    def add_file(self, rel: str, content: bytes) -> None:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if parent:
            self.add_dir(parent)
        self.nodes[rel] = self._node("file", content)

    def entry(self, rel: str, *, listing: bool = False) -> DriveEntry:
        node = self.nodes[rel]
        sha1 = hashlib.sha1(node.content).hexdigest() if node.kind == "file" else None
        if (listing and not self.list_has_sha1) or (not listing and not self.stat_has_sha1):
            sha1 = None
        size = len(node.content) if node.kind == "file" else None
        token = f"sha1:{sha1}:{size}" if sha1 else f"t:{size if size is not None else '?'}:{int(node.modified_at.timestamp() * 1e6)}"
        return DriveEntry(name=rel.rsplit("/", 1)[-1] if rel else "~", rel_path=rel, kind=node.kind, size=size,
                          modified_at=node.modified_at, created_at=node.created_at, item_id=node.item_id, sha1=sha1,
                          version_token=token, local_safe=True)

    def children(self, rel: str) -> list[str]:
        prefix = f"{rel}/" if rel else ""
        return sorted(path for path in self.nodes if path and path.startswith(prefix) and "/" not in path[len(prefix):])


def _check_path(rel: str) -> str:
    if rel == "":
        return rel
    if rel.startswith(("/", "~")) or "\\" in rel or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise DriveError(ErrorCode.INVALID_PATH, "invalid path", rel)
    return rel


class FakeWorkerDriveGateway:
    """``WorkerDriveGateway`` stand-in over a :class:`MemoryDrive`; same public API only."""

    drive: MemoryDrive  # bound by make_fake_adapter_module
    worker_version = "0.2.0-fake"
    instances: list["FakeWorkerDriveGateway"] = []

    def __init__(self, config: WorkerConfig, token_store: Any) -> None:
        self.config = config
        self.token_store = token_store
        self.closed = False
        self._bundle: TokenBundle | None = None
        self._sent_obtained_at: datetime | None = None
        self._lock = threading.Lock()
        self.last_success_at: datetime | None = None
        self.last_error: tuple[str, datetime] | None = None
        self.token_refreshed_at: datetime | None = None
        type(self).instances.append(self)

    # -- session / tokens (contract §4.2: every session creation rotates the refresh token) --

    def _session(self, op: str, rel: str) -> None:
        """Like the real worker: tokens live in memory once a session exists.

        ``load()`` is consulted on every call (the real client polls every
        ``token_poll_interval_s``), but only a *different registered* bundle is
        adopted.  A deleted row does not stop an existing in-memory session;
        only closing the gateway does, which is what the dashboard relies on.
        """
        if self.closed:
            raise DriveError(ErrorCode.UNAVAILABLE, "gateway closed")
        self.drive.calls.append((op, rel))
        if self.drive.on_call is not None:
            self.drive.on_call(op, rel)
        loaded = self.token_store.load()
        if loaded is not None and not isinstance(loaded, TokenBundle):
            raise AssertionError("TokenStore.load() must return the adapter TokenBundle type")
        if self._bundle is None:
            if loaded is None:
                self._fail(ErrorCode.AUTH_REQUIRED)
                raise DriveError(ErrorCode.AUTH_REQUIRED, "no token registered")
            self._start_session(loaded)
        elif loaded is not None and loaded.obtained_at not in (self._sent_obtained_at, self._bundle.obtained_at):
            self._start_session(loaded)
        injected = self.drive.fail_next.pop(op, None) or self.drive.fail_always.get(op)
        if injected is not None:
            self._fail(injected)
            raise DriveError(injected, f"injected {injected.value}", rel)

    def _start_session(self, loaded: TokenBundle) -> None:
        # contract §4.2: every session creation rotates the refresh token
        self._sent_obtained_at = loaded.obtained_at
        self._bundle = loaded
        self.rotate()

    def rotate(self) -> TokenBundle:
        """Refresh the in-memory session and notify ``TokenStore.save`` synchronously.

        Tests also call this on a *closed* gateway to simulate a late ``tokens``
        notification from the old worker's chain.
        """
        assert self._bundle is not None
        self.drive.sessions += 1
        current = self._bundle
        rotated = TokenBundle(server_url=current.server_url, access_token=f"{current.access_token}-a{self.drive.sessions}",
                              refresh_token=f"{current.refresh_token}-r{self.drive.sessions}",
                              obtained_at=datetime.now(timezone.utc), account_hint=current.account_hint)
        self.token_store.save(rotated)   # synchronous, as the worker notification would
        self._bundle = rotated
        self.token_refreshed_at = rotated.obtained_at
        return rotated

    def _ok(self) -> None:
        self.last_success_at = datetime.now(timezone.utc)

    def _fail(self, code: ErrorCode) -> None:
        self.last_error = (code.value, datetime.now(timezone.utc))

    def _require(self, rel: str) -> _Node:
        node = self.drive.nodes.get(rel)
        if node is None:
            raise DriveError(ErrorCode.NOT_FOUND, "not found", rel)
        return node

    # -- DriveGateway --------------------------------------------------------------------------

    def root_identity(self) -> str:
        self._session("root_identity", "")
        self._ok()
        return f"scx:{self.config.adapter.server_url}:{self.drive.nodes[''].item_id}"

    def list_dir(self, rel_path: str, *, max_entries: int = 50_000, priority: Priority = Priority.INTERACTIVE) -> list[DriveEntry]:
        self._session("list_dir", _check_path(rel_path))
        if self._require(rel_path).kind != "dir":
            raise DriveError(ErrorCode.INVALID_PATH, "not a folder", rel_path)
        names = self.drive.children(rel_path)
        collected: list[DriveEntry] = []
        for offset in range(0, max(len(names), 1), self.drive.page_size):   # every page, like LSOptions().offset(n)
            self.drive.page_calls += 1
            collected += [self.drive.entry(path, listing=True) for path in names[offset:offset + self.drive.page_size]]
            if len(collected) > max_entries:
                raise DriveError(ErrorCode.LIMIT, "too many entries", rel_path)
        self._ok()
        return sorted(collected, key=lambda entry: entry.name)

    def stat(self, rel_path: str, *, priority: Priority = Priority.INTERACTIVE) -> DriveEntry | None:
        self._session("stat", _check_path(rel_path))
        self._ok()
        return self.drive.entry(rel_path) if rel_path in self.drive.nodes else None

    def download_to(self, rel_path: str, local_dir: Path, *, max_bytes: int, priority: Priority = Priority.BACKGROUND) -> DownloadResult:
        self._session("download_to", _check_path(rel_path))
        node = self._require(rel_path)
        if node.kind != "file":
            raise DriveError(ErrorCode.INVALID_PATH, "not a file", rel_path)
        if len(node.content) > max_bytes:
            raise DriveError(ErrorCode.LIMIT, "too large", rel_path)
        if rel_path in self.drive.busy_paths:
            raise DriveError(ErrorCode.BUSY, "changed while reading", rel_path)
        target = Path(local_dir) / rel_path.rsplit("/", 1)[-1]
        if target.exists():
            raise DriveError(ErrorCode.CONFLICT, "local file exists", rel_path)
        target.write_bytes(node.content)
        self._ok()
        return DownloadResult(local_path=target, entry=self.drive.entry(rel_path), size=len(node.content),
                              sha256=hashlib.sha256(node.content).hexdigest())

    def upload_new(self, local_file: Path, rel_dir: str, *, name: str | None = None) -> DriveEntry:
        self._session("upload_new", _check_path(rel_dir))
        if self._require(rel_dir).kind != "dir":
            raise DriveError(ErrorCode.INVALID_PATH, "not a folder", rel_dir)
        target = f"{rel_dir}/{name or Path(local_file).name}" if rel_dir else (name or Path(local_file).name)
        if target in self.drive.nodes:
            raise DriveError(ErrorCode.CONFLICT, "target exists", target)
        self.drive.add_file(target, Path(local_file).read_bytes())
        self._ok()
        return self.drive.entry(target)

    def copy_within(self, src_rel: str, dst_dir_rel: str, *, new_name: str | None = None) -> DriveEntry:
        self._session("copy_within", _check_path(src_rel))
        node = self._require(src_rel)
        if self._require(dst_dir_rel).kind != "dir":
            raise DriveError(ErrorCode.INVALID_PATH, "not a folder", dst_dir_rel)
        target = f"{dst_dir_rel}/{new_name or src_rel.rsplit('/', 1)[-1]}"
        if target in self.drive.nodes:
            raise DriveError(ErrorCode.CONFLICT, "target exists", target)
        self.drive.nodes[target] = replace(self.drive._node(node.kind, node.content))
        self._ok()
        return self.drive.entry(target)

    def mkdirs(self, rel_path: str) -> DriveEntry:
        self._session("mkdirs", _check_path(rel_path))
        existing = self.drive.nodes.get(rel_path)
        if existing is not None and existing.kind != "dir":
            raise DriveError(ErrorCode.CONFLICT, "file exists", rel_path)
        self.drive.add_dir(rel_path)
        self._ok()
        return self.drive.entry(rel_path)

    def health(self) -> HealthStatus:
        if self.closed:
            return HealthStatus("UNAVAILABLE", self.last_success_at, None, None, self.token_refreshed_at, 0, 0)
        if self.last_error and self.last_error[0] == "AUTH_REQUIRED" and (
                self.last_success_at is None or self.last_error[1] > self.last_success_at):
            state = "AUTH_REQUIRED"
        elif self.last_success_at is None and self.last_error is None:
            state = "UNAVAILABLE"   # worker not started yet
        elif self.last_error and (self.last_success_at is None or self.last_error[1] > self.last_success_at):
            state = "DEGRADED"
        else:
            state = "OK"
        return HealthStatus(state, self.last_success_at, self.last_error[0] if self.last_error else None,
                            self.last_error[1] if self.last_error else None, self.token_refreshed_at, 0, 0)

    def close(self) -> None:
        self.closed = True


def make_fake_adapter_module(drive: MemoryDrive) -> types.ModuleType:
    module = types.ModuleType("scx_drive_adapter")
    gateway_cls = type("WorkerDriveGateway", (FakeWorkerDriveGateway,), {"drive": drive, "instances": []})
    for name, value in {
        "Priority": Priority, "ErrorCode": ErrorCode, "DriveError": DriveError, "DriveEntry": DriveEntry,
        "DownloadResult": DownloadResult, "HealthStatus": HealthStatus, "TokenBundle": TokenBundle,
        "AdapterConfig": AdapterConfig, "WorkerConfig": WorkerConfig, "WorkerDriveGateway": gateway_cls,
        "__version__": "0.2.0-fake",
    }.items():
        setattr(module, name, value)
    return module
