from __future__ import annotations

from pathlib import Path

from scripts import check_database_startup_preflight as preflight


def test_default_postgresql_blocks_readiness_without_database_url(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.delenv("ANALYSIS_DB_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(tmp_path / "missing.duckdb"))

    assert preflight.main() == 2
    message = capsys.readouterr().err
    assert "POSTGRESQL_DATABASE_URL_REQUIRED" in message
    assert "DuckDB" in message


def test_whitespace_postgresql_url_is_not_accepted(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.delenv("ANALYSIS_DB_BACKEND", raising=False)
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(tmp_path / "missing.duckdb"))

    assert preflight.main() == 2
    assert "POSTGRESQL_DATABASE_URL_REQUIRED" in capsys.readouterr().err


def test_explicit_duckdb_remains_a_valid_legacy_selection(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert preflight.main() == 0
    assert "backend=duckdb" in capsys.readouterr().out


def test_untyped_legacy_duckdb_is_blocked_before_default_postgresql(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.delenv("ANALYSIS_DB_BACKEND", raising=False)
    legacy_database = tmp_path / "analysis_dashboard.duckdb"
    legacy_database.touch()
    monkeypatch.setattr(preflight, "database_settings", lambda: type("Settings", (), {"backend": "postgresql", "database_url": "postgresql://configured", "duckdb_path": legacy_database})())

    assert preflight.main() == 3
    assert "LEGACY_DUCKDB_BACKEND_SELECTION_REQUIRED" in capsys.readouterr().err


def test_explicit_postgresql_allows_a_stale_duckdb_file(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "postgresql")
    legacy_database = tmp_path / "analysis_dashboard.duckdb"
    legacy_database.touch()
    monkeypatch.setattr(preflight, "database_settings", lambda: type("Settings", (), {"backend": "postgresql", "database_url": "postgresql://configured", "duckdb_path": legacy_database})())

    assert preflight.main() == 0
    assert "backend=postgresql" in capsys.readouterr().out


def test_explicit_duckdb_allows_an_existing_duckdb_file(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    legacy_database = tmp_path / "analysis_dashboard.duckdb"
    legacy_database.touch()
    monkeypatch.setattr(preflight, "database_settings", lambda: type("Settings", (), {"backend": "duckdb", "database_url": None, "duckdb_path": legacy_database})())

    assert preflight.main() == 0
    assert "backend=duckdb" in capsys.readouterr().out
