from __future__ import annotations

import re
from pathlib import Path
from threading import Lock, RLock, local
from typing import Any, Protocol

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine

from .config import PostgresPoolSettings, database_settings


class CursorLike(Protocol):
    def fetchall(self) -> list[Any]: ...
    def fetchone(self) -> Any: ...


class ConnectionLike(Protocol):
    @property
    def backend(self) -> str: ...

    def execute(self, statement: str, parameters: Any | None = None) -> CursorLike: ...
    def __enter__(self) -> "ConnectionLike": ...
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


_engine: Engine | None = None
_engine_url: str | None = None
_engine_pool: PostgresPoolSettings | None = None
_media_engine: Engine | None = None
_media_engine_url: str | None = None
_media_engine_pool: PostgresPoolSettings | None = None
_engine_lock = Lock()


class _SerializedDuckDBManager:
    """Bound DuckDB to one open file handle per process at a time.

    DuckDB is the local development adapter, not the concurrent production
    database. FastAPI sync handlers can run in different worker threads, so a
    process-wide lock owns the complete connection lifetime. Re-entrant calls
    on one thread reuse the active connection instead of opening a conflicting
    handle.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._state = local()

    def acquire(self, path: Path) -> duckdb.DuckDBPyConnection:
        self._lock.acquire()
        active = getattr(self._state, "connection", None)
        if active is not None:
            if self._state.path != path:
                self._lock.release()
                raise RuntimeError("중첩 DuckDB 연결은 동일한 데이터베이스 경로를 사용해야 합니다.")
            self._state.depth += 1
            return active
        try:
            connection = duckdb.connect(str(path))
        except BaseException:
            self._lock.release()
            raise
        self._state.connection = connection
        self._state.path = path
        self._state.depth = 1
        return connection

    def release(self) -> None:
        connection = getattr(self._state, "connection", None)
        if connection is None:
            raise RuntimeError("활성 DuckDB 연결이 없습니다.")
        self._state.depth -= 1
        try:
            if self._state.depth == 0:
                # A failed close must not leave a closed handle in thread-local
                # state. The next context must be able to acquire normally.
                try:
                    connection.close()
                finally:
                    del self._state.connection
                    del self._state.path
                    del self._state.depth
        finally:
            self._lock.release()


_duckdb_manager = _SerializedDuckDBManager()


class SerializedDuckDBConnection:
    backend = "duckdb"

    def __init__(self, path: Path):
        self._path = path
        self._connection: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> "SerializedDuckDBConnection":
        if self._connection is not None:
            raise RuntimeError("DuckDB 연결 context는 재사용할 수 없습니다.")
        self._connection = _duckdb_manager.acquire(self._path)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._connection is None:
            return
        try:
            _duckdb_manager.release()
        finally:
            self._connection = None

    def _active(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            raise RuntimeError("DuckDB 연결은 with connect() 문 안에서 사용해야 합니다.")
        return self._connection

    def execute(self, statement: str, parameters: Any | None = None):
        connection = self._active()
        if parameters is None:
            return connection.execute(statement)
        return connection.execute(statement, parameters)

    def executemany(self, statement: str, parameters: Any):
        return self._active().executemany(statement, parameters)


def _sqlalchemy_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    raise RuntimeError("DATABASE_URL은 postgresql:// 또는 postgresql+psycopg:// 형식이어야 합니다.")


def postgres_connection_budget(workers: int, pool: PostgresPoolSettings) -> int:
    """Worst-case checked-out PostgreSQL connections for this application."""
    if workers < 1:
        raise ValueError("workers must be at least one")
    per_worker = (
        pool.request_pool_size
        + pool.request_max_overflow
        + pool.media_pool_size
        + pool.media_max_overflow
    )
    return workers * per_worker


def _postgres_engine(database_url: str, pool: PostgresPoolSettings, *, media: bool = False) -> Engine:
    global _engine, _engine_url, _engine_pool, _media_engine, _media_engine_url, _media_engine_pool
    normalized = _sqlalchemy_url(database_url)
    with _engine_lock:
        if media:
            if _media_engine is None or _media_engine_url != normalized or _media_engine_pool != pool:
                if _media_engine is not None:
                    _media_engine.dispose()
                _media_engine = create_engine(
                    normalized,
                    pool_pre_ping=True,
                    pool_size=pool.media_pool_size,
                    max_overflow=pool.media_max_overflow,
                    pool_timeout=pool.media_timeout_seconds,
                    pool_recycle=pool.recycle_seconds,
                )
                _media_engine_url = normalized
                _media_engine_pool = pool
            return _media_engine
        if _engine is None or _engine_url != normalized or _engine_pool != pool:
            if _engine is not None:
                _engine.dispose()
            _engine = create_engine(
                normalized,
                pool_pre_ping=True,
                pool_size=pool.request_pool_size,
                max_overflow=pool.request_max_overflow,
                pool_timeout=pool.request_timeout_seconds,
                pool_recycle=pool.recycle_seconds,
            )
            _engine_url = normalized
            _engine_pool = pool
        return _engine


def _postgres_statement(statement: str) -> str:
    # 1. DuckDB 형태의 ? IS NULL 구문을 PostgreSQL이 이해하도록 CAST 구문으로 먼저 변경
    translated = re.sub(
        r"\?\s+IS\s+NULL",
        "CAST(? AS text) IS NULL",
        statement,
        flags=re.IGNORECASE,
    )
    
    # 2. % 이스케이프 및 ? -> %s 변환
    translated = translated.replace("%", "%%").replace("?", "%s")

    # 3. json_extract_string 변환
    translated = re.sub(
        r"json_extract_string\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*'\$\.([A-Za-z0-9_-]+)'\s*\)",
        r"(\1 ->> '\2')",
        translated,
        flags=re.IGNORECASE,
    )

    # 4. INSERT OR IGNORE 변환
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", translated, flags=re.IGNORECASE):
        translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", translated, count=1, flags=re.IGNORECASE)
        stripped = translated.rstrip()
        suffix = ";" if stripped.endswith(";") else ""
        body = stripped[:-1].rstrip() if suffix else stripped
        translated = f"{body} ON CONFLICT DO NOTHING{suffix}"
        
    return translated


class PostgresConnection:
    backend = "postgresql"

    def __init__(self, engine: Engine):
        self._engine = engine
        self._connection: Connection | None = None

    def __enter__(self) -> "PostgresConnection":
        self._connection = self._engine.connect()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._connection is None:
            return
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()
            self._connection = None

    def execute(self, statement: str, parameters: Any | None = None):
        if self._connection is None:
            raise RuntimeError("PostgreSQL 연결은 with connect() 문 안에서 사용해야 합니다.")
        normalized = statement.strip().rstrip(";").upper()
        if normalized in {"BEGIN", "BEGIN TRANSACTION"}:
            return self._connection.exec_driver_sql("SELECT 1 WHERE false")
        if normalized == "COMMIT":
            self._connection.commit()
            return self._connection.exec_driver_sql("SELECT 1 WHERE false")
        if normalized == "ROLLBACK":
            self._connection.rollback()
            return self._connection.exec_driver_sql("SELECT 1 WHERE false")
        values = None if parameters is None else tuple(parameters)
        return self._connection.exec_driver_sql(_postgres_statement(statement), values)


def connect() -> SerializedDuckDBConnection | PostgresConnection:
    settings = database_settings()
    if settings.backend == "postgresql":
        if not settings.database_url:
            raise RuntimeError("ANALYSIS_DB_BACKEND=postgresql일 때 DATABASE_URL이 필요합니다.")
        return PostgresConnection(_postgres_engine(settings.database_url, settings.postgres_pool))
    settings.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    return SerializedDuckDBConnection(settings.duckdb_path)


def media_connect() -> SerializedDuckDBConnection | PostgresConnection:
    """Open the bounded read/write pool reserved for media work.

    DuckDB has one embedded connection type, so it intentionally shares the
    normal helper there. PostgreSQL uses a separate SQLAlchemy pool so a slow
    video response cannot exhaust the ordinary request pool.
    """
    settings = database_settings()
    if settings.backend == "postgresql":
        if not settings.database_url:
            raise RuntimeError("ANALYSIS_DB_BACKEND=postgresql일 때 DATABASE_URL이 필요합니다.")
        return PostgresConnection(_postgres_engine(settings.database_url, settings.postgres_pool, media=True))
    settings.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    return SerializedDuckDBConnection(settings.duckdb_path)


def rows(cursor: Any) -> list[dict[str, Any]]:
    columns = list(cursor.keys()) if hasattr(cursor, "keys") else [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
