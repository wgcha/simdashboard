"""Dashboard-side scheduling of SCX drive work (issue #51, 2026-10-23).

The adapter worker usually runs with ``SIMDASH_SCX_MAX_CONCURRENCY=1``.  After the administrator's
rescan, the 5-minute result auto-sync, the screens' folder sync, the capture worker, the upload queue
and PhysicsAI's relay all queued drive calls at the same time; the worker's own queue then answered
``OVERLOADED`` ("queue wait timeout") and folder syncs failed as ``FOLDER_SCHEMA_SCAN_UNAVAILABLE`` /
``..._INCOMPLETE``.  Two gates keep that queue short instead:

* :func:`call_slot` — at most ``max_concurrency`` drive calls of this process are handed to the worker
  at once.  Interactive calls (a screen waiting for an answer) go first: a background call does not
  start while an interactive one is waiting.  A caller that cannot get a slot in time gets
  ``DRIVE_BUSY`` (retryable) from the dashboard rather than a worker queue timeout.
* :func:`background_activity` — one background drive *activity* at a time (one folder-sync scope, one
  capture-job session, one rescan step, ...).  Waiters are served first come, first served, so a
  capture worker that keeps finding jobs cannot starve the folder syncs.  The same thread may nest.

Neither gate is held while waiting for the database, and no database connection is opened here.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator

from ..storage.provider import SpdmStorageError

INTERACTIVE = "INTERACTIVE"
BACKGROUND = "BACKGROUND"
INTERACTIVE_SLOT_WAIT_SECONDS = 30.0
BACKGROUND_SLOT_WAIT_SECONDS = 120.0
BACKGROUND_ACTIVITY_WAIT_SECONDS = 60.0
BUSY_MESSAGE = "드라이브 요청이 많아 지금은 처리하지 못했습니다. 잠시 후 다시 확인합니다."


def busy_error(message: str = BUSY_MESSAGE) -> SpdmStorageError:
    return SpdmStorageError("DRIVE_BUSY", message)


def _capacity() -> int:
    try:
        from . import gateway as drive_gateway

        return max(1, int(drive_gateway.current_settings().max_concurrency or 1))
    except Exception:  # noqa: BLE001 - scheduling must never fail a call
        return 1


class _Lanes:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.in_flight = 0
        self.interactive_waiting = 0
        self.activity_owner: int | None = None
        self.activity_depth = 0
        self.activity_queue: deque[int] = deque()
        self.activity_label: str | None = None
        self.max_in_flight = 0          # tests, diagnostics
        self.max_background = 0


_lanes = _Lanes()


def reset_for_tests() -> None:
    global _lanes
    _lanes = _Lanes()


def stats() -> dict[str, int | str | None]:
    lanes = _lanes
    with lanes.cond:
        return {"in_flight": lanes.in_flight, "interactive_waiting": lanes.interactive_waiting,
                "max_in_flight": lanes.max_in_flight, "activity": lanes.activity_label,
                "activity_waiting": len(lanes.activity_queue)}


@contextmanager
def call_slot(priority: str = INTERACTIVE, *, timeout: float | None = None,
              deadline: float | None = None) -> Iterator[None]:
    """Hold one of the ``max_concurrency`` worker call slots for one drive call (interactive first).

    ``deadline`` (``time.monotonic()``) shortens the wait of a bounded operation.  Raises
    ``SpdmStorageError('DRIVE_BUSY')`` when no slot frees up in time.
    """
    lanes = _lanes
    interactive = priority != BACKGROUND
    wait = timeout if timeout is not None else (INTERACTIVE_SLOT_WAIT_SECONDS if interactive
                                                else BACKGROUND_SLOT_WAIT_SECONDS)
    end = time.monotonic() + max(0.0, wait)
    if deadline is not None:
        end = min(end, deadline)
    capacity = _capacity()
    with lanes.cond:
        if interactive:
            lanes.interactive_waiting += 1
        try:
            while not (lanes.in_flight < capacity and (interactive or lanes.interactive_waiting == 0)):
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise busy_error()
                lanes.cond.wait(remaining)
            lanes.in_flight += 1
            lanes.max_in_flight = max(lanes.max_in_flight, lanes.in_flight)
        finally:
            if interactive:
                lanes.interactive_waiting -= 1
                lanes.cond.notify_all()
    try:
        yield
    finally:
        with lanes.cond:
            lanes.in_flight -= 1
            lanes.cond.notify_all()


@contextmanager
def background_activity(label: str = "background", *, timeout: float | None = None,
                        deadline: float | None = None) -> Iterator[None]:
    """One background drive activity of the process at a time (FIFO, re-entrant for the same thread).

    Raises ``SpdmStorageError('DRIVE_BUSY')`` when another activity keeps the drive longer than the wait.
    """
    lanes = _lanes
    me = threading.get_ident()
    with lanes.cond:
        if lanes.activity_owner == me:
            lanes.activity_depth += 1
            nested = True
        else:
            nested = False
    if nested:
        try:
            yield
        finally:
            with lanes.cond:
                lanes.activity_depth -= 1
        return
    wait = BACKGROUND_ACTIVITY_WAIT_SECONDS if timeout is None else timeout
    end = time.monotonic() + max(0.0, wait)
    if deadline is not None:
        end = min(end, deadline)
    with lanes.cond:
        lanes.activity_queue.append(me)
        try:
            while not (lanes.activity_owner is None and lanes.activity_queue[0] == me):
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise busy_error()
                lanes.cond.wait(remaining)
        except BaseException:
            lanes.activity_queue = deque(waiter for waiter in lanes.activity_queue if waiter != me)
            lanes.cond.notify_all()
            raise
        lanes.activity_queue.popleft()
        lanes.activity_owner, lanes.activity_depth, lanes.activity_label = me, 1, label
        lanes.max_background = max(lanes.max_background, 1)
    try:
        yield
    finally:
        with lanes.cond:
            lanes.activity_owner, lanes.activity_depth, lanes.activity_label = None, 0, None
            lanes.cond.notify_all()
