"""In-process worker pool for Final copy jobs (W2).

No new service or dependency: daemon threads inside the backend process. At most
``MAX_CONCURRENT`` jobs run per process, and jobs of the same request (same
``request_key``) run one at a time. The durable state of a job lives in its
``Final/.finalizations/<Final ID>/`` records (``case_finalization``); this module
only schedules callables, so a restart loses nothing but the queue, which the
startup scan and status polling rebuild.

Writes to SPDM never happen here: the scheduled callable is
``case_finalization.run_job`` (the only FINAL zone writer module).
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable

MAX_CONCURRENT = 2

_LOG = logging.getLogger(__name__)
_lock = threading.Condition()
_queue: deque[tuple[str, str, Callable[[], object]]] = deque()
_queued: set[str] = set()
_running: dict[str, str] = {}


def submit(key: str, request_key: str, run: Callable[[], object]) -> str:
    """Queue ``run`` once per ``key``; returns ``RUNNING`` or ``QUEUED`` (also for an existing entry)."""
    with _lock:
        if key in _running:
            return "RUNNING"
        if key not in _queued:
            _queue.append((key, request_key, run))
            _queued.add(key)
        _dispatch_locked()
        return "RUNNING" if key in _running else "QUEUED"


def is_active(key: str) -> bool:
    with _lock:
        return key in _running or key in _queued


def _dispatch_locked() -> None:
    while len(_running) < MAX_CONCURRENT:
        busy_requests = set(_running.values())
        index = next((position for position, item in enumerate(_queue) if item[1] not in busy_requests), None)
        if index is None:
            return
        key, request_key, run = _queue[index]
        del _queue[index]
        _queued.discard(key)
        _running[key] = request_key
        threading.Thread(target=_work, args=(key, run), name=f"final-copy-{key[-8:]}", daemon=True).start()


def _work(key: str, run: Callable[[], object]) -> None:
    try:
        run()
    except BaseException:  # noqa: BLE001 - a dying job must still free its slot
        _LOG.exception("Final copy worker stopped unexpectedly")
    finally:
        with _lock:
            _running.pop(key, None)
            _dispatch_locked()
            _lock.notify_all()


def wait_idle(timeout: float = 60.0) -> bool:
    """Block until no job is queued or running (tests and orderly shutdown)."""
    with _lock:
        return _lock.wait_for(lambda: not _running and not _queue, timeout=timeout)


def snapshot() -> dict[str, list[str]]:
    with _lock:
        return {"running": sorted(_running), "queued": [item[0] for item in _queue]}
