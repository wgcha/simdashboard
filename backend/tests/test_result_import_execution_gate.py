from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.result_import_execution_gate as gate_module
from app.services.result_import_execution_gate import (
    RESULT_IMPORT_REFRESH_BUSY,
    RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE,
    ResultImportExecutionGate,
    ResultImportExecutionGateError,
)


pytestmark = pytest.mark.unit


def _hold_local_gate_in_worker(
    root_text: str,
    acquired: object,
    release: object,
    result_queue: object,
) -> None:
    """Keep a real flock lease in a spawned worker for bounded contention QA."""
    try:
        gate = ResultImportExecutionGate("duckdb", Path(root_text))
        with gate.acquire(Path(root_text)):
            acquired.set()
            if not release.wait(timeout=3):
                result_queue.put("release timeout")
                return
        result_queue.put("released")
    except BaseException as exc:  # pragma: no cover - worker diagnostics only
        result_queue.put(f"UNEXPECTED:{type(exc).__name__}")


def test_local_gate_is_nonblocking_and_releases_after_context(tmp_path: Path) -> None:
    first = ResultImportExecutionGate("duckdb", tmp_path)
    second = ResultImportExecutionGate("local", tmp_path)

    with first.acquire(tmp_path):
        with pytest.raises(ResultImportExecutionGateError) as error:
            with second.acquire(tmp_path):
                pass
        assert error.value.code == RESULT_IMPORT_REFRESH_BUSY

    with second.acquire(tmp_path):
        assert (tmp_path / ".simdashboard-result-import.lock").is_file()


@pytest.mark.skipif(os.name != "posix" or gate_module._fcntl is None, reason="local flock requires POSIX fcntl")
def test_local_gate_is_busy_across_workers_then_reacquires_after_release(tmp_path: Path) -> None:
    context = mp.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    result_queue = context.Queue()
    worker = context.Process(
        target=_hold_local_gate_in_worker,
        args=(str(tmp_path), acquired, release, result_queue),
    )
    worker.start()
    try:
        assert acquired.wait(timeout=3), "worker did not acquire the local execution gate"
        with pytest.raises(ResultImportExecutionGateError) as error:
            with ResultImportExecutionGate("duckdb", tmp_path).acquire(tmp_path):
                pass
        assert error.value.code == RESULT_IMPORT_REFRESH_BUSY

        release.set()
        worker.join(timeout=3)
        assert not worker.is_alive(), "worker did not release the local execution gate"
        assert worker.exitcode == 0
        assert result_queue.get(timeout=1) == "released"

        with ResultImportExecutionGate("duckdb", tmp_path).acquire(tmp_path):
            pass
    finally:
        release.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=3)
        result_queue.close()
        result_queue.join_thread()


def test_local_gate_releases_when_refresh_body_raises(tmp_path: Path) -> None:
    gate = ResultImportExecutionGate("duckdb", tmp_path)
    with pytest.raises(RuntimeError, match="body failure"):
        with gate.acquire(tmp_path):
            raise RuntimeError("body failure")
    with gate.acquire(tmp_path):
        pass


def test_local_gate_accepts_injected_flock_for_deterministic_busy_and_release_tests(tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_flock(_descriptor: int, operation: int) -> None:
        calls.append(operation)

    gate = ResultImportExecutionGate("duckdb", tmp_path, flock=fake_flock)
    with gate.acquire(tmp_path):
        pass

    assert calls == [
        gate_module._fcntl.LOCK_EX | gate_module._fcntl.LOCK_NB,
        gate_module._fcntl.LOCK_UN,
    ]

    def busy_flock(_descriptor: int, operation: int) -> None:
        if operation & gate_module._fcntl.LOCK_EX:
            raise BlockingIOError()

    with pytest.raises(ResultImportExecutionGateError) as error:
        with ResultImportExecutionGate("duckdb", tmp_path, flock=busy_flock).acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_BUSY


def test_local_gate_rejects_unsafe_existing_lock_file(tmp_path: Path) -> None:
    lock = tmp_path / ".simdashboard-result-import.lock"
    lock.write_text("unsafe", encoding="utf-8")
    lock.chmod(0o644)
    with pytest.raises(ResultImportExecutionGateError) as error:
        with ResultImportExecutionGate("duckdb", tmp_path).acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE


def test_local_gate_revalidates_the_opened_workspace_root_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_fstat = gate_module.os.fstat

    def mode_drift(_descriptor: int):
        return SimpleNamespace(
            st_mode=gate_module.stat.S_IFDIR | 0o755,
            st_uid=gate_module.os.geteuid(),
        )

    monkeypatch.setattr(gate_module.os, "fstat", mode_drift)
    with pytest.raises(ResultImportExecutionGateError) as error:
        with ResultImportExecutionGate("duckdb", tmp_path).acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    assert gate_module.os.fstat is not real_fstat


def test_local_gate_maps_root_descriptor_close_failure_without_leaking_other_descriptors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_open = gate_module.os.open
    real_close = gate_module.os.close
    root_descriptor: int | None = None

    def tracking_open(path, flags, *args, **kwargs):
        nonlocal root_descriptor
        descriptor = real_open(path, flags, *args, **kwargs)
        if path == str(tmp_path):
            root_descriptor = descriptor
        return descriptor

    def fail_only_root_close(descriptor: int) -> None:
        if descriptor == root_descriptor:
            raise OSError("injected root close failure")
        real_close(descriptor)

    monkeypatch.setattr(gate_module.os, "open", tracking_open)
    monkeypatch.setattr(gate_module.os, "close", fail_only_root_close)
    try:
        with pytest.raises(ResultImportExecutionGateError) as error:
            with ResultImportExecutionGate("duckdb", tmp_path).acquire(tmp_path):
                pass
        assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    finally:
        if root_descriptor is not None:
            real_close(root_descriptor)


def test_local_gate_maps_unsafe_lock_descriptor_close_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = tmp_path / ".simdashboard-result-import.lock"
    lock.write_text("unsafe", encoding="utf-8")
    lock.chmod(0o644)
    real_open = gate_module.os.open
    real_close = gate_module.os.close
    lock_descriptor: int | None = None

    def tracking_open(path, flags, *args, **kwargs):
        nonlocal lock_descriptor
        descriptor = real_open(path, flags, *args, **kwargs)
        if path == gate_module._LOCK_FILENAME:
            lock_descriptor = descriptor
        return descriptor

    def fail_only_lock_close(descriptor: int) -> None:
        if descriptor == lock_descriptor:
            raise OSError("injected lock close failure")
        real_close(descriptor)

    monkeypatch.setattr(gate_module.os, "open", tracking_open)
    monkeypatch.setattr(gate_module.os, "close", fail_only_lock_close)
    try:
        with pytest.raises(ResultImportExecutionGateError) as error:
            with ResultImportExecutionGate("duckdb", tmp_path).acquire(tmp_path):
                pass
        assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    finally:
        if lock_descriptor is not None:
            real_close(lock_descriptor)


def test_local_gate_reports_unsupported_platform_without_import_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(gate_module, "_fcntl", None)
    with pytest.raises(ResultImportExecutionGateError) as error:
        with ResultImportExecutionGate("duckdb", tmp_path).acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE


class _Cursor:
    def __init__(self, value: bool) -> None:
        self.value = value

    def fetchone(self) -> tuple[bool]:
        return (self.value,)


class _Connection:
    def __init__(
        self,
        acquire: bool = True,
        *,
        fail_unlock: bool = False,
        unlock_value: bool = True,
        fail_close: bool = False,
    ) -> None:
        self.acquire = acquire
        self.fail_unlock = fail_unlock
        self.unlock_value = unlock_value
        self.fail_close = fail_close
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, statement: str, parameters: tuple[object, ...]) -> _Cursor:
        self.calls.append((statement, parameters))
        if "pg_advisory_unlock" in statement and self.fail_unlock:
            raise RuntimeError("database detail must not escape")
        if "pg_advisory_unlock" in statement:
            return _Cursor(self.unlock_value)
        return _Cursor(self.acquire)

    def close(self) -> None:
        self.closed = True
        if self.fail_close:
            raise RuntimeError("database close detail must not escape")


def test_postgres_gate_uses_dedicated_session_advisory_lock_and_closes_last(tmp_path: Path) -> None:
    connection = _Connection()
    gate = ResultImportExecutionGate("postgresql", tmp_path, connection_factory=lambda: connection)

    with gate.acquire(tmp_path):
        assert not connection.closed

    assert connection.closed
    assert [statement for statement, _params in connection.calls] == [
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
    ]
    expected_key = f"simdashboard:result-import-refresh:v1:{tmp_path.resolve()}"
    assert connection.calls[0][1] == connection.calls[1][1] == (expected_key,)


def test_postgres_gate_busy_and_connection_failure_are_safe_and_close(tmp_path: Path) -> None:
    busy_connection = _Connection(acquire=False)
    busy_gate = ResultImportExecutionGate("postgresql", tmp_path, connection_factory=lambda: busy_connection)
    with pytest.raises(ResultImportExecutionGateError) as error:
        with busy_gate.acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_BUSY
    assert busy_connection.closed

    busy_close_failure = _Connection(acquire=False, fail_close=True)
    with pytest.raises(ResultImportExecutionGateError) as error:
        with ResultImportExecutionGate(
            "postgresql", tmp_path, connection_factory=lambda: busy_close_failure
        ).acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    assert busy_close_failure.closed

    unavailable_gate = ResultImportExecutionGate(
        "postgresql",
        tmp_path,
        connection_factory=lambda: (_ for _ in ()).throw(RuntimeError("secret connection string")),
    )
    with pytest.raises(ResultImportExecutionGateError) as error:
        with unavailable_gate.acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    assert "secret" not in str(error.value)


def test_postgres_gate_closes_session_when_refresh_body_or_unlock_fails(tmp_path: Path) -> None:
    body_connection = _Connection()
    body_gate = ResultImportExecutionGate("postgresql", tmp_path, connection_factory=lambda: body_connection)
    with pytest.raises(RuntimeError, match="body failure"):
        with body_gate.acquire(tmp_path):
            raise RuntimeError("body failure")
    assert body_connection.closed

    release_connection = _Connection(fail_unlock=True)
    release_gate = ResultImportExecutionGate("postgresql", tmp_path, connection_factory=lambda: release_connection)
    with pytest.raises(ResultImportExecutionGateError) as error:
        with release_gate.acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    assert release_connection.closed

    false_unlock_connection = _Connection(unlock_value=False)
    false_unlock_gate = ResultImportExecutionGate(
        "postgresql", tmp_path, connection_factory=lambda: false_unlock_connection
    )
    with pytest.raises(ResultImportExecutionGateError) as error:
        with false_unlock_gate.acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    assert false_unlock_connection.closed


def test_postgres_cleanup_failure_does_not_mask_refresh_body_exception(tmp_path: Path) -> None:
    connection = _Connection(fail_unlock=True, fail_close=True)
    gate = ResultImportExecutionGate("postgresql", tmp_path, connection_factory=lambda: connection)

    with pytest.raises(RuntimeError, match="body failure"):
        with gate.acquire(tmp_path):
            raise RuntimeError("body failure")
    assert connection.closed


def test_unknown_backend_is_unavailable_without_path_leakage(tmp_path: Path) -> None:
    with pytest.raises(ResultImportExecutionGateError) as error:
        with ResultImportExecutionGate("unsupported", tmp_path).acquire(tmp_path):
            pass
    assert error.value.code == RESULT_IMPORT_REFRESH_LOCK_UNAVAILABLE
    assert str(tmp_path) not in str(error.value)
