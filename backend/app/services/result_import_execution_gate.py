"""Cross-worker, nonblocking execution gates for result-import refreshes.

The existing in-process refresh lock protects one worker only. This boundary
adds a short-lived lease around the full refresh execution without consuming
the ordinary PostgreSQL request or media pools.
"""

from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, Protocol

from ..database_connection import connect_postgres_import_gate_session

try:  # ``fcntl`` is unavailable on Windows, where local acquisition fails safely.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by injected unsupported platform test
    _fcntl = None


RESULT_IMPORT_REFRESH_BUSY = "RESULT_IMPORT_REFRESH_BUSY"
RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE = "RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE"
_LOCK_FILENAME = ".simdashboard-result-import.lock"
_ADVISORY_KEY_PREFIX = "simdashboard:result-import-refresh:v1:"


class ResultImportExecutionGateError(RuntimeError):
    """Safe gate-acquisition failure with a stable API-visible code."""

    def __init__(self, code: str) -> None:
        self.code = code
        message = (
            "다른 결과 가져오기 새로고침이 진행 중입니다."
            if code == RESULT_IMPORT_REFRESH_BUSY
            else "결과 가져오기 실행 잠금을 사용할 수 없습니다."
        )
        super().__init__(message)


class ResultImportExecutionGateProtocol(Protocol):
    """A nonblocking, exception-safe import execution lease."""

    def acquire(self, prepared_snapshot_root: Path) -> ContextManager[None]: ...


class ResultImportExecutionGate:
    """Choose a local flock or PostgreSQL advisory-lock lease by backend."""

    def __init__(
        self,
        backend: str,
        import_root: Path,
        *,
        connection_factory: Callable[[], Any] = connect_postgres_import_gate_session,
        flock: Callable[[int, int], None] | None = None,
    ) -> None:
        self._backend = backend.strip().lower() if isinstance(backend, str) else ""
        self._import_root = Path(import_root)
        self._connection_factory = connection_factory
        self._flock = flock

    def acquire(self, prepared_snapshot_root: Path) -> ContextManager[None]:
        if self._backend in {"duckdb", "local"}:
            return self._acquire_local(Path(prepared_snapshot_root))
        if self._backend == "postgresql":
            return self._acquire_postgres()
        return _unavailable_context()

    @contextmanager
    def _acquire_local(self, prepared_snapshot_root: Path) -> Iterator[None]:
        flock = self._flock or (_fcntl.flock if _fcntl is not None else None)
        if os.name != "posix" or _fcntl is None or flock is None:
            raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)
        descriptor = _open_secure_lock_file(prepared_snapshot_root)
        locked = False
        body_failed = False
        try:
            try:
                flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                locked = True
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN} or isinstance(exc, BlockingIOError):
                    raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_BUSY) from None
                raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE) from None
            try:
                yield
            except BaseException:
                body_failed = True
                raise
        finally:
            release_failed = False
            if locked:
                try:
                    flock(descriptor, _fcntl.LOCK_UN)
                except OSError:
                    release_failed = True
            try:
                os.close(descriptor)
            except OSError:
                release_failed = True
            if release_failed and not body_failed:
                raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)

    @contextmanager
    def _acquire_postgres(self) -> Iterator[None]:
        key = _normalized_import_root_key(self._import_root)
        connection = _open_postgres_connection(self._connection_factory)
        try:
            try:
                acquired = _advisory_lock(connection, key)
            except Exception:
                _close_safely(connection)
                raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE) from None
            if not acquired:
                if not _close_safely(connection):
                    raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)
                raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_BUSY)

            body_failed = False
            try:
                yield
            except BaseException:
                body_failed = True
                raise
            finally:
                release_failed = not _advisory_unlock(connection, key)
                # Closing the dedicated session is deliberately last: it is
                # PostgreSQL's final session-scoped advisory-lock release.
                release_failed = not _close_safely(connection) or release_failed
                if release_failed and not body_failed:
                    raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)
        except ResultImportExecutionGateError:
            raise


@contextmanager
def _unavailable_context() -> Iterator[None]:
    raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)
    yield  # pragma: no cover - keeps this a generator context manager


def _normalized_import_root_key(import_root: Path) -> str:
    try:
        normalized = import_root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE) from None
    if not normalized.is_dir():
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)
    return _ADVISORY_KEY_PREFIX + str(normalized)


def _open_secure_lock_file(prepared_snapshot_root: Path) -> int:
    if os.name != "posix" or _fcntl is None:
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)
    root_fd: int | None = None
    try:
        root_stat = os.lstat(prepared_snapshot_root)
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise OSError(errno.EINVAL, "unsafe lock directory")
        root_fd = os.open(
            str(prepared_snapshot_root),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        # Revalidate the directory through the descriptor we will actually
        # use. A prepare-time lstat alone cannot protect against an ownership
        # or mode swap before the relative lock-file open.
        opened_stat = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(opened_stat.st_mode)
            or opened_stat.st_uid != os.geteuid()
            or stat.S_IMODE(opened_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "unsafe lock directory")
    except OSError:
        if root_fd is not None:
            _close_fd_safely(root_fd)
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE) from None
    try:
        descriptor = os.open(
            _LOCK_FILENAME,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
    except OSError:
        _close_fd_safely(root_fd)
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE) from None
    if not _close_fd_safely(root_fd):
        _close_fd_safely(descriptor)
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            raise OSError(errno.EPERM, "unsafe lock file")
        return descriptor
    except OSError:
        _close_fd_safely(descriptor)
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE) from None


def _close_fd_safely(descriptor: int) -> bool:
    try:
        os.close(descriptor)
    except OSError:
        return False
    return True


def _open_postgres_connection(factory: Callable[[], Any]) -> Any:
    try:
        return factory()
    except Exception:
        raise ResultImportExecutionGateError(RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE) from None


def _advisory_lock(connection: Any, key: str) -> bool:
    row = connection.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
        (key,),
    ).fetchone()
    return bool(row and row[0] is True)


def _advisory_unlock(connection: Any, key: str) -> bool:
    try:
        row = connection.execute(
            "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
            (key,),
        ).fetchone()
        return bool(row and row[0] is True)
    except Exception:
        return False


def _close_safely(connection: Any) -> bool:
    try:
        connection.close()
    except Exception:
        return False
    return True
