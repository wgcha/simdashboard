from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

from app.database import initialize_database
from app.database_connection import connect


# WSL2 kernel 6.18 currently fails to wake Python's selector loop through
# call_soon_threadsafe(), which is the primitive Starlette TestClient uses.
# Uvicorn's standard runtime already selects uvloop on Linux, so tests use the
# same backend and retain a standalone compatibility smoke in backend/scripts.
_previous_event_loop_policy = asyncio.get_event_loop_policy()
if sys.platform != "win32":
    try:
        import uvloop
    except ImportError:
        pass
    else:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


def pytest_configure(config: pytest.Config) -> None:
    _verify_postgres_test_database()


def _verify_postgres_test_database() -> None:
    """Fail closed before collection if a PostgreSQL test target is ambiguous."""
    if os.getenv("ANALYSIS_TEST_POSTGRES", "").strip() != "1":
        return

    expected_database = os.getenv("ANALYSIS_TEST_POSTGRES_DATABASE", "").strip()
    if not expected_database:
        raise pytest.UsageError(
            "ANALYSIS_TEST_POSTGRES=1 requires ANALYSIS_TEST_POSTGRES_DATABASE; refusing an ambiguous database target."
        )
    if os.getenv("ANALYSIS_DB_BACKEND", "").strip().lower() != "postgresql":
        raise pytest.UsageError(
            "ANALYSIS_TEST_POSTGRES=1 requires ANALYSIS_DB_BACKEND=postgresql."
        )

    try:
        with connect() as connection:
            row = connection.execute("SELECT current_database()").fetchone()
    except Exception as exc:
        raise pytest.UsageError("PostgreSQL test database target could not be verified.") from exc
    actual_database = row[0] if row else None
    if actual_database != expected_database:
        raise pytest.UsageError(
            "PostgreSQL test database mismatch: "
            f"expected {expected_database!r}, got {actual_database!r}."
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Classify runtime tests while retaining profile-agnostic API contracts."""
    del config
    runtime_markers = ("unit", "postgres_integration", "duckdb_integration")
    for item in items:
        if item.path.name == "test_api.py" and item.get_closest_marker("contract") is None:
            item.add_marker(pytest.mark.contract)
        if not any(item.get_closest_marker(name) is not None for name in runtime_markers):
            if item.get_closest_marker("contract") is None:
                item.add_marker(pytest.mark.duckdb_integration)
        present = [name for name in runtime_markers if item.get_closest_marker(name) is not None]
        if not present and item.get_closest_marker("contract") is not None:
            continue
        if len(present) != 1:
            raise pytest.UsageError(f"{item.nodeid} must have exactly one runtime marker; found {present}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Restore global state before pytest emits its terminal reports."""
    del session, exitstatus
    asyncio.set_event_loop_policy(_previous_event_loop_policy)


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


def _cleanup_disposable_duckdb(database: Path) -> None:
    """Remove only the per-test DuckDB files, leaving test diagnostics intact."""
    for artifact in (database, database.with_name(f"{database.name}.wal")):
        artifact.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def isolated_database(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use a disposable DB unless PostgreSQL testing was explicitly enabled.

    A developer may keep PostgreSQL selected in the shell while running the
    ordinary unit suite. Requiring a second, test-specific opt-in prevents
    those tests from mutating the configured application database by accident.
    """
    if request.node.get_closest_marker("unit") is not None:
        yield
        return

    postgres_test_enabled = (
        os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower() == "postgresql"
        and os.getenv("ANALYSIS_TEST_POSTGRES", "").strip() == "1"
    )
    if postgres_test_enabled:
        yield
        return
    seeded_duckdb = request.getfixturevalue("seeded_duckdb")
    database = tmp_path / "test.duckdb"
    try:
        shutil.copy2(seeded_duckdb, database)
        monkeypatch.setenv("ANALYSIS_DB_BACKEND", "duckdb")
        monkeypatch.setenv("ANALYSIS_DUCKDB_PATH", str(database))
        yield
    finally:
        _cleanup_disposable_duckdb(database)
