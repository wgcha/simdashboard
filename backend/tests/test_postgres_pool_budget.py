from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import database_connection
from app.config import PostgresPoolSettings, database_settings
from scripts import check_postgres_pool_budget


pytestmark = pytest.mark.unit


def _pool(**overrides: int) -> PostgresPoolSettings:
    defaults = {
        "request_pool_size": 5,
        "request_max_overflow": 10,
        "request_timeout_seconds": 10,
        "media_pool_size": 10,
        "media_max_overflow": 5,
        "media_timeout_seconds": 10,
        "recycle_seconds": 1800,
    }
    defaults.update(overrides)
    return PostgresPoolSettings(**defaults)


def test_postgres_pool_defaults_include_one_import_gate_session_per_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "POSTGRES_REQUEST_POOL_SIZE",
        "POSTGRES_REQUEST_MAX_OVERFLOW",
        "POSTGRES_REQUEST_POOL_TIMEOUT_SECONDS",
        "POSTGRES_MEDIA_POOL_SIZE",
        "POSTGRES_MEDIA_MAX_OVERFLOW",
        "POSTGRES_MEDIA_POOL_TIMEOUT_SECONDS",
        "POSTGRES_POOL_RECYCLE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    pool = database_settings().postgres_pool

    assert pool == _pool()
    assert database_connection.postgres_connection_budget(1, pool) == 31
    assert database_connection.postgres_connection_budget(2, pool) == 62
    assert database_connection.postgres_connection_budget(3, pool) == 93


def test_postgres_pool_is_configurable_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_REQUEST_POOL_SIZE", "3")
    monkeypatch.setenv("POSTGRES_REQUEST_MAX_OVERFLOW", "2")
    monkeypatch.setenv("POSTGRES_MEDIA_POOL_SIZE", "4")
    monkeypatch.setenv("POSTGRES_MEDIA_MAX_OVERFLOW", "1")

    assert database_settings().postgres_pool == _pool(
        request_pool_size=3,
        request_max_overflow=2,
        media_pool_size=4,
        media_max_overflow=1,
    )
    assert database_connection.postgres_connection_budget(2, database_settings().postgres_pool) == 22

    monkeypatch.setenv("POSTGRES_MEDIA_POOL_SIZE", "0")
    with pytest.raises(RuntimeError, match="POSTGRES_MEDIA_POOL_SIZE"):
        database_settings()


def test_postgres_engine_uses_configured_request_and_media_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Engine:
        def dispose(self) -> None:
            return None

    def create_engine(url: str, **kwargs: object) -> Engine:
        calls.append({"url": url, **kwargs})
        return Engine()

    pool = _pool(
        request_pool_size=3,
        request_max_overflow=2,
        request_timeout_seconds=7,
        media_pool_size=4,
        media_max_overflow=1,
        media_timeout_seconds=8,
        recycle_seconds=900,
    )
    monkeypatch.setattr(database_connection, "create_engine", create_engine)
    monkeypatch.setattr(database_connection, "_engine", None)
    monkeypatch.setattr(database_connection, "_engine_url", None)
    monkeypatch.setattr(database_connection, "_engine_pool", None)
    monkeypatch.setattr(database_connection, "_media_engine", None)
    monkeypatch.setattr(database_connection, "_media_engine_url", None)
    monkeypatch.setattr(database_connection, "_media_engine_pool", None)

    database_connection._postgres_engine("postgresql://user:pass@localhost/db", pool)
    database_connection._postgres_engine("postgresql://user:pass@localhost/db", pool, media=True)

    assert calls == [
        {
            "url": "postgresql+psycopg://user:pass@localhost/db",
            "pool_pre_ping": True,
            "pool_size": 3,
            "max_overflow": 2,
            "pool_timeout": 7,
            "pool_recycle": 900,
        },
        {
            "url": "postgresql+psycopg://user:pass@localhost/db",
            "pool_pre_ping": True,
            "pool_size": 4,
            "max_overflow": 1,
            "pool_timeout": 8,
            "pool_recycle": 900,
        },
    ]


@pytest.mark.parametrize(
    ("configured_url", "expected_psycopg_url"),
    [
        ("postgresql://user:pass@localhost/db", "postgresql://user:pass@localhost/db"),
        ("postgres://user:pass@localhost/db", "postgresql://user:pass@localhost/db"),
        ("postgresql+psycopg://user:pass@localhost/db", "postgresql://user:pass@localhost/db"),
    ],
)
def test_import_gate_session_uses_the_existing_postgres_url_contract(
    monkeypatch: pytest.MonkeyPatch,
    configured_url: str,
    expected_psycopg_url: str,
) -> None:
    captured: dict[str, object] = {}

    class Psycopg:
        @staticmethod
        def connect(url: str, *, autocommit: bool) -> object:
            captured.update(url=url, autocommit=autocommit)
            return object()

    monkeypatch.setattr(
        database_connection,
        "database_settings",
        lambda: SimpleNamespace(backend="postgresql", database_url=configured_url),
    )
    import sys

    monkeypatch.setitem(sys.modules, "psycopg", Psycopg)

    assert database_connection.connect_postgres_import_gate_session() is not None
    assert captured == {"url": expected_psycopg_url, "autocommit": True}


def test_import_gate_session_rejects_urls_outside_the_sqlalchemy_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        database_connection,
        "database_settings",
        lambda: SimpleNamespace(backend="postgresql", database_url="mysql://unsafe"),
    )
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        database_connection.connect_postgres_import_gate_session()


def test_budget_check_rejects_pool_larger_than_server_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        check_postgres_pool_budget,
        "database_settings",
        lambda: SimpleNamespace(backend="postgresql", postgres_pool=_pool()),
    )

    assert check_postgres_pool_budget.check_budget(1, 50, 10) == 31
    with pytest.raises(RuntimeError, match="exceeds usable connections"):
        check_postgres_pool_budget.check_budget(2, 50, 10)


def test_budget_check_requires_postgres_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        check_postgres_pool_budget,
        "database_settings",
        lambda: SimpleNamespace(backend="duckdb", postgres_pool=_pool()),
    )

    with pytest.raises(RuntimeError, match="ANALYSIS_DB_BACKEND=postgresql"):
        check_postgres_pool_budget.check_budget(1, 50, 10)


def test_budget_cli_reports_the_two_worker_gate_inclusive_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        check_postgres_pool_budget,
        "database_settings",
        lambda: SimpleNamespace(backend="postgresql", postgres_pool=_pool()),
    )

    assert check_postgres_pool_budget.main([
        "--workers", "2", "--max-connections", "80", "--reserved-connections", "10"
    ]) == 0
    assert capsys.readouterr().out.strip() == (
        "POSTGRES_POOL_BUDGET_OK workers=2 budget=62 max_connections=80 reserved=10"
    )
