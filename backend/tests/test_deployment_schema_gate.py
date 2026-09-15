from types import SimpleNamespace

import pytest

from scripts import check_postgres_schema as gate


@pytest.mark.parametrize("pending,expected,checks", [(False, 0, [False, True]), (True, 3, [False])])
def test_service_start_checks_only_and_requires_deployment_for_pending_schema(monkeypatch, pending, expected, checks):
    monkeypatch.setattr(gate, "database_settings", lambda: SimpleNamespace(backend="postgresql", database_url="secret"))
    actual = []

    def inspect(url, *, verify_catalog):
        assert url == "secret"
        actual.append(verify_catalog)
        return SimpleNamespace(pending=pending)

    monkeypatch.setattr(gate, "inspect_app_revision", inspect)
    assert gate.main() == expected
    assert actual == checks


@pytest.mark.parametrize("code", ["APP_CONNECTION_FAILED", "DATABASE_SETUP_REQUIRED", "DATABASE_REVISION_DIVERGED"])
def test_connection_or_unknown_database_does_not_trigger_bootstrap(monkeypatch, capsys, code):
    monkeypatch.setattr(gate, "database_settings", lambda: SimpleNamespace(backend="postgresql", database_url="secret"))

    def inspect(*args, **kwargs):
        raise gate.StartupMigrationError(code)

    monkeypatch.setattr(gate, "inspect_app_revision", inspect)
    assert gate.main() == 1
    output = capsys.readouterr()
    assert code in output.err
    assert "secret" not in output.err


def test_missing_connection_stops_before_database_access(monkeypatch):
    monkeypatch.setattr(gate, "database_settings", lambda: SimpleNamespace(backend="postgresql", database_url=None))
    monkeypatch.setattr(gate, "inspect_app_revision", lambda *a, **kw: pytest.fail("must not connect"))
    assert gate.main() == 2


def test_explicit_legacy_database_skips_postgres(monkeypatch):
    monkeypatch.setattr(gate, "database_settings", lambda: SimpleNamespace(backend="duckdb", database_url=None))
    monkeypatch.setattr(gate, "inspect_app_revision", lambda *a, **kw: pytest.fail("must not connect"))
    assert gate.main() == 0
