from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.access_policy import DEFAULT_MENU_VISIBILITY, MENU_DEFINITIONS
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
    events: list[str] = []
    statements: list[tuple[str, list[object] | None]] = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement: str, parameters: list[object] | None = None) -> None:
            statements.append((statement, parameters))
            events.append("catalog")

    monkeypatch.setattr(database, "connect", lambda: Connection())
    monkeypatch.setattr(database, "ensure_default_content", lambda _connection: events.append("reference"))

    database.seed_reference_database()

    # The explicit seed shares DuckDB's application-owned menu catalog
    # reconciliation.  Keep verifying the actual helper rather than mocking it
    # away, while the reference/demo content itself remains isolated.
    assert events[-1] == "reference"
    menu_rows = [
        parameters
        for statement, parameters in statements
        if "INSERT OR IGNORE INTO menu_definitions" in statement
    ]
    assert [
        (parameters[0], parameters[4])
        for parameters in menu_rows
        if parameters is not None
    ] == [(menu.id, menu.sequence_no) for menu in MENU_DEFINITIONS]
    policy_rows = [
        parameters
        for statement, parameters in statements
        if "INSERT OR IGNORE INTO role_menu_policies" in statement
    ]
    assert {
        (parameters[0], parameters[1], parameters[2])
        for parameters in policy_rows
        if parameters is not None
    } == {
        (role, menu_id, is_visible)
        for role, visibility in DEFAULT_MENU_VISIBILITY.items()
        for menu_id, is_visible in visibility.items()
    }
    version_row = next(
        parameters
        for statement, parameters in statements
        if "INSERT OR IGNORE INTO menu_policy_versions" in statement
    )
    assert version_row is not None
    assert json.loads(str(version_row[0])) == DEFAULT_MENU_VISIBILITY
