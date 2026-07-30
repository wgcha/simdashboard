from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.database import initialize_database


@pytest.fixture(scope="session")
def seeded_duckdb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build one canonical test database without touching developer data."""
    database = tmp_path_factory.mktemp("analysis-canvas-seed") / "seed.duckdb"
    previous_backend = os.environ.get("ANALYSIS_DB_BACKEND")
    previous_path = os.environ.get("ANALYSIS_DUCKDB_PATH")
    os.environ["ANALYSIS_DB_BACKEND"] = "duckdb"
    os.environ["ANALYSIS_DUCKDB_PATH"] = str(database)
    try:
        initialize_database()
    finally:
        if previous_backend is None:
            os.environ.pop("ANALYSIS_DB_BACKEND", None)
        else:
            os.environ["ANALYSIS_DB_BACKEND"] = previous_backend
        if previous_path is None:
            os.environ.pop("ANALYSIS_DUCKDB_PATH", None)
        else:
            os.environ["ANALYSIS_DUCKDB_PATH"] = previous_path
    return database


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, seeded_duckdb: Path, monkeypatch: pytest.MonkeyPatch):
    """Use a disposable DB unless PostgreSQL testing was explicitly enabled.

    A developer may keep PostgreSQL selected in the shell while running the
    ordinary unit suite. Requiring a second, test-specific opt-in prevents
    those tests from mutating the configured application database by accident.
    """
    postgres_test_enabled = (
        os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower() == "postgresql"
        and os.getenv("ANALYSIS_TEST_POSTGRES", "").strip() == "1"
    )
    if postgres_test_enabled:
        yield
        return
    database = tmp_path / "test.duckdb"
    shutil.copy2(seeded_duckdb, database)
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
    monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(database))
    yield
