"""Cross-platform advisory lock for one helper per local data directory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


class InstanceBusyError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, handle: IO[str]) -> None:
        self._handle = handle

    def release(self) -> None:
        if self._handle.closed:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


def acquire_instance_lock(data_dir: Path) -> InstanceLock:
    """Take an OS-released lock before database recovery is allowed to run."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = (data_dir / "local-runner.lock").open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise InstanceBusyError("A local runner is already using this data directory.") from exc
    return InstanceLock(handle)
