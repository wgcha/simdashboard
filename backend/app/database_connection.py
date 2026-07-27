from __future__ import annotations

import re
from threading import Lock
from typing import Any, Protocol

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine

from .config import database_settings


class CursorLike(Protocol):
    def fetchall(self) -> list[Any]: ...
    def fetchone(self) -> Any: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Any | None = None) -> CursorLike: ...
    def __enter__(self) -> "ConnectionLike": ...
    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


_engine: Engine | None = None
_engine_url: str | None = None
_engine_lock = Lock()


def _sqlalchemy_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    raise RuntimeError("DATABASE_URL은 postgresql:// 또는 postgresql+psycopg:// 형식이어야 합니다.")


def _postgres_engine(database_url: str) -> Engine:
    global _engine, _engine_url
    normalized = _sqlalchemy_url(database_url)
    with _engine_lock:
        if _engine is None or _engine_url != normalized:
            if _engine is not None:
                _engine.dispose()
            _engine = create_engine(
                normalized,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                pool_recycle=1800,
            )
            _engine_url = normalized
        return _engine


def _postgres_statement(statement: str) -> str:
    # psycopg uses ``%`` for its parameter protocol even when SQLAlchemy's
    # ``exec_driver_sql`` is used. Escape literal LIKE/modulo percent signs
    # before converting the DuckDB-style positional placeholders.
    translated = statement.replace("%", "%%").replace("?", "%s")
    translated = re.sub(
        r"json_extract_string\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*'\$\.([A-Za-z0-9_-]+)'\s*\)",
        r"(\1 ->> '\2')",
        translated,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", translated, flags=re.IGNORECASE):
        translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", translated, count=1, flags=re.IGNORECASE)
        stripped = translated.rstrip()
        suffix = ";" if stripped.endswith(";") else ""
        body = stripped[:-1].rstrip() if suffix else stripped
        translated = f"{body} ON CONFLICT DO NOTHING{suffix}"
    return translated


class PostgresConnection:
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


def connect() -> duckdb.DuckDBPyConnection | PostgresConnection:
    settings = database_settings()
    if settings.backend == "postgresql":
        if not settings.database_url:
            raise RuntimeError("ANALYSIS_DB_BACKEND=postgresql일 때 DATABASE_URL이 필요합니다.")
        return PostgresConnection(_postgres_engine(settings.database_url))
    settings.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(settings.duckdb_path))


def rows(cursor: Any) -> list[dict[str, Any]]:
    columns = list(cursor.keys()) if hasattr(cursor, "keys") else [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
