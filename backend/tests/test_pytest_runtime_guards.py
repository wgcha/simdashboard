from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest


@pytest.mark.unit
def test_postgres_test_database_guard_requires_exact_verified_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALYSIS_TEST_POSTGRES", "1")
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "postgresql")
    monkeypatch.delenv("ANALYSIS_TEST_POSTGRES_DATABASE", raising=False)
    with pytest.raises(pytest.UsageError, match="ANALYSIS_TEST_POSTGRES_DATABASE"):
        conftest._verify_postgres_test_database()

    monkeypatch.setenv("ANALYSIS_TEST_POSTGRES_DATABASE", "safe_test_database")

    class Connection:
        def execute(self, statement: str) -> SimpleNamespace:
            assert statement == "SELECT current_database()"
            return SimpleNamespace(fetchone=lambda: ("wrong_database",))

        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

    class ExactConnection(Connection):
        def execute(self, statement: str) -> SimpleNamespace:
            assert statement == "SELECT current_database()"
            return SimpleNamespace(fetchone=lambda: ("safe_test_database",))

    monkeypatch.setattr(conftest, "connect", lambda: Connection())
    with pytest.raises(pytest.UsageError, match="database mismatch"):
        conftest._verify_postgres_test_database()

    monkeypatch.setattr(conftest, "connect", lambda: ExactConnection())
    conftest._verify_postgres_test_database()


@pytest.mark.unit
def test_pytest_owns_tmp_path_cleanup_lifecycle() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = (root / "pytest.ini").read_text(encoding="utf-8")
    conftest_source = (root / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert "tmp_path_retention_policy = none" in settings
    assert "tmp_path_retention_count = 0" in settings
    assert "config.option.basetemp" not in conftest_source
    assert "shutil.rmtree(run_directory" not in conftest_source


@pytest.mark.unit
def test_disposable_duckdb_cleanup_removes_database_and_wal_only(tmp_path: Path) -> None:
    database = tmp_path / "test.duckdb"
    wal = tmp_path / "test.duckdb.wal"
    diagnostics = tmp_path / "failure-output.txt"
    seed = tmp_path / "seed.duckdb"
    for path in (database, wal, diagnostics, seed):
        path.write_text(path.name, encoding="utf-8")

    conftest._cleanup_disposable_duckdb(database)

    assert not database.exists()
    assert not wal.exists()
    assert diagnostics.read_text(encoding="utf-8") == diagnostics.name
    assert seed.read_text(encoding="utf-8") == seed.name
