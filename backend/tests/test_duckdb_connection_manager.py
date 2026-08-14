from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
import duckdb

from app import database_connection
from app.database_connection import connect
from app.main import app


def test_duckdb_serializes_parallel_reads(monkeypatch, tmp_path) -> None:
    database = tmp_path / "parallel-read.duckdb"
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(database))
    with connect() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.executemany("INSERT INTO sample VALUES (?)", [(value,) for value in range(20)])

    def read_count(_: int) -> int:
        with connect() as connection:
            return connection.execute("SELECT count(*) FROM sample").fetchone()[0]

    with ThreadPoolExecutor(max_workers=20) as executor:
        assert list(executor.map(read_count, range(20))) == [20] * 20


def test_duckdb_reuses_a_connection_for_nested_calls(monkeypatch, tmp_path) -> None:
    database = tmp_path / "nested.duckdb"
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(database))

    with connect() as outer:
        outer.execute("CREATE TABLE sample (value INTEGER)")
        with connect() as inner:
            inner.execute("INSERT INTO sample VALUES (1)")
        assert outer.execute("SELECT value FROM sample").fetchone() == (1,)


def test_duckdb_rejects_nested_connections_for_different_paths(monkeypatch, tmp_path) -> None:
    first = tmp_path / "first.duckdb"
    second = tmp_path / "second.duckdb"
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(first))

    with connect() as outer:
        outer.execute("CREATE TABLE sample (value INTEGER)")
        monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(second))
        with pytest.raises(RuntimeError, match="동일한 데이터베이스 경로"):
            with connect():
                pass
        assert outer.execute("SELECT count(*) FROM sample").fetchone() == (0,)


def test_duckdb_recovers_after_an_exception(monkeypatch, tmp_path) -> None:
    database = tmp_path / "exception.duckdb"
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(database))

    with pytest.raises(ValueError, match="expected"):
        with connect() as connection:
            connection.execute("CREATE TABLE sample (value INTEGER)")
            raise ValueError("expected")
    with connect() as connection:
        connection.execute("INSERT INTO sample VALUES (1)")
        assert connection.execute("SELECT count(*) FROM sample").fetchone() == (1,)


def test_duckdb_close_failure_clears_manager_state(monkeypatch, tmp_path) -> None:
    class FailingClose:
        def close(self) -> None:
            raise OSError("close failed")

    manager = database_connection._SerializedDuckDBManager()
    monkeypatch.setattr(database_connection.duckdb, "connect", lambda _: FailingClose())
    manager.acquire(tmp_path / "close-failure.duckdb")
    with pytest.raises(OSError, match="close failed"):
        manager.release()
    assert getattr(manager._state, "connection", None) is None


def test_duckdb_serializes_parallel_reads_and_writes(monkeypatch, tmp_path) -> None:
    database = tmp_path / "parallel-write.duckdb"
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(database))
    with connect() as connection:
        connection.execute("CREATE TABLE sample (value INTEGER PRIMARY KEY)")

    def write_and_read(value: int) -> int:
        with connect() as connection:
            connection.execute("INSERT INTO sample VALUES (?)", [value])
            return connection.execute("SELECT count(*) FROM sample").fetchone()[0]

    with ThreadPoolExecutor(max_workers=20) as executor:
        counts = list(executor.map(write_and_read, range(20)))
    assert sorted(counts) == list(range(1, 21))
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM sample").fetchone() == (20,)


def test_duckdb_handles_concurrent_http_reads_without_internal_errors() -> None:
    with TestClient(app) as client:
        def list_projects(_: int) -> int:
            return client.get("/api/projects").status_code

        with ThreadPoolExecutor(max_workers=20) as executor:
            assert list(executor.map(list_projects, range(20))) == [200] * 20


def test_duckdb_rejects_a_second_process_while_file_handle_is_open(tmp_path) -> None:
    """DuckDB 1.5 local-file adapter is deliberately single-process."""
    database = tmp_path / "process-lock.duckdb"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb, sys, time; connection = duckdb.connect(sys.argv[1]); "
            "print('ready', flush=True); time.sleep(30); connection.close()",
            str(database),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(duckdb.IOException, match="file|lock|handle|Conflict"):
            duckdb.connect(str(database))
    finally:
        holder.terminate()
        holder.communicate(timeout=5)
