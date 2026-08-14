from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import database


pytestmark = pytest.mark.unit


def test_duckdb_initialize_delegates_through_dev_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(database, "database_settings", lambda: SimpleNamespace(backend="duckdb"))
    monkeypatch.setattr(
        "app.adapters.persistence.duckdb.bootstrap.initialize_duckdb_development_database",
        lambda legacy: calls.append("adapter"),
    )

    database.initialize_database()

    assert calls == ["adapter"]


def test_reference_seed_is_explicit_not_postgres_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(database, "connect", lambda: Connection())
    monkeypatch.setattr(database, "ensure_default_content", lambda _connection: calls.append("reference"))

    database.seed_reference_database()

    assert calls == ["reference"]
