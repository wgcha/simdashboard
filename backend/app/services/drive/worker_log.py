"""Adapter worker stderr → ``<SIMDASH_SCX_WORK_DIR>/worker.log`` (rotating, secrets redacted; issue #51).

The SCX adapter starts its worker process itself (``WorkerDriveGateway``); the dashboard
does not own that ``Popen`` call.  A worker started with its stderr discarded
(``stderr=DEVNULL``) or inherited (``stderr=None``) left no trace of adapter failures in
the dashboard logs.  :func:`install` therefore gives the adapter's modules a
``subprocess.Popen`` that turns such a stderr into a pipe and copies it, line by line and
redacted, into a rotating file (5 MB × 3).  A stderr the adapter reads itself
(``PIPE``/a file) is left alone.  Nothing here touches the drive or the database.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import types
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, IO

from .gateway import mask_secrets

LOG_NAME = "worker.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
MAX_LINE_CHARS = 4000

_SECRET_KV = re.compile(
    r"(?i)((?:access|refresh|id|auth|session)[_-]?token|password|passwd|secret|client[_-]?secret|authorization|cookie)"
    r"([\"']?\s*[:=]\s*[\"']?)([^\s\"',;}&]+)")

_lock = threading.Lock()
_logs: dict[str, "WorkerLog"] = {}
_installed: dict[str, Path] = {}
_REAL_POPEN = subprocess.Popen


def redact(line: str) -> str:
    """Mask bearer tokens, JWT-like strings and ``token=…``/``"password": …`` values in one line."""
    return mask_secrets(_SECRET_KV.sub(r"\1\2***", line))


class WorkerLog:
    """Rotating ``worker.log``; every line goes through :func:`redact` first."""

    def __init__(self, path: Path, *, max_bytes: int = MAX_BYTES, backup_count: int = BACKUP_COUNT) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = RotatingFileHandler(self.path, maxBytes=max_bytes, backupCount=backup_count,
                                            encoding="utf-8", delay=True)
        self._handler.setFormatter(logging.Formatter("%(asctime)s pid=%(worker_pid)s %(message)s"))
        self._write_lock = threading.Lock()

    def write_line(self, line: str, *, pid: int | None = None) -> None:
        text = redact(line.rstrip("\r\n"))[:MAX_LINE_CHARS]
        if not text.strip():
            return
        record = logging.LogRecord("scx.worker", logging.INFO, __file__, 0, text, None, None)
        record.worker_pid = pid if pid is not None else "-"
        with self._write_lock:
            try:
                self._handler.emit(record)
            except Exception:  # noqa: BLE001 - logging must never break the drive path
                pass

    def close(self) -> None:
        with self._write_lock:
            self._handler.close()


def worker_log(work_dir: Path) -> WorkerLog:
    path = Path(work_dir) / LOG_NAME
    with _lock:
        log = _logs.get(str(path))
        if log is None:
            log = _logs[str(path)] = WorkerLog(path)
        return log


def pump(stream: IO[Any], log: WorkerLog, *, pid: int | None = None) -> None:
    """Copy ``stream`` (bytes or text) into ``log`` until EOF; never raises."""
    try:
        while True:
            raw = stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            log.write_line(line, pid=pid)
    except Exception:  # noqa: BLE001 - a closed pipe ends the copy
        pass
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def _capturing_popen(log_dir: Path) -> type:
    class CapturingPopen(_REAL_POPEN):  # type: ignore[misc, valid-type]
        """``subprocess.Popen`` whose discarded or inherited stderr is copied into ``worker.log``."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            stderr = kwargs.get("stderr")
            capture = stderr is None or stderr == subprocess.DEVNULL
            if capture:
                kwargs["stderr"] = subprocess.PIPE
            super().__init__(*args, **kwargs)
            if capture and self.stderr is not None:
                thread = threading.Thread(target=pump, args=(self.stderr, worker_log(log_dir)),
                                          kwargs={"pid": self.pid}, name=f"scx-worker-stderr-{self.pid}", daemon=True)
                thread.start()

    return CapturingPopen


class _SubprocessProxy(types.ModuleType):
    """The ``subprocess`` module as seen by the adapter: same attributes, capturing ``Popen``."""

    def __init__(self, popen: type) -> None:
        super().__init__("subprocess")
        self.Popen = popen

    def __getattr__(self, name: str) -> Any:
        return getattr(subprocess, name)


def install(adapter: Any, work_dir: Path) -> int:
    """Give the adapter package's loaded modules the capturing ``Popen``; returns how many were patched.

    Idempotent; safe to call again after the adapter imported more of its modules.
    """
    root = getattr(adapter, "__name__", None)
    if not root:
        return 0
    popen = _capturing_popen(Path(work_dir))
    patched = 0
    with _lock:
        _installed[root] = Path(work_dir)
        for name, module in list(sys.modules.items()):
            if module is None or not (name == root or name.startswith(root + ".")):
                continue
            current = getattr(module, "subprocess", None)
            if current is subprocess or isinstance(current, _SubprocessProxy):
                setattr(module, "subprocess", _SubprocessProxy(popen))
                patched += 1
            current_popen = getattr(module, "Popen", None)
            if current_popen is _REAL_POPEN or getattr(current_popen, "__name__", "") == "CapturingPopen":
                setattr(module, "Popen", popen)
                patched += 1
    return patched


def reset_for_tests() -> None:
    with _lock:
        for log in _logs.values():
            log.close()
        _logs.clear()
        _installed.clear()


__all__ = ["LOG_NAME", "WorkerLog", "install", "pump", "redact", "worker_log"]
