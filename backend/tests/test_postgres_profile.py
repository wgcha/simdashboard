from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from scripts import check_postgres_connection as preflight


pytestmark = pytest.mark.postgres_integration


@pytest.fixture(autouse=True)
def postgres_profile_enabled() -> None:
    enabled = (
        os.getenv("ANALYSIS_TEST_POSTGRES") == "1"
        and os.getenv("ANALYSIS_DB_BACKEND", "").strip().lower() == "postgresql"
    )
    if not enabled:
        pytest.skip("requires ANALYSIS_TEST_POSTGRES=1 and ANALYSIS_DB_BACKEND=postgresql")


def test_app_role_postgres_preflight_contract() -> None:
    """Verify app identity, migration head, CRUD rollback, audit append, and DDL denial."""
    assert preflight.main() == 0


@pytest.mark.parametrize("revisions", [[], [("head",), ("older",)]])
def test_preflight_rejects_missing_or_multiple_alembic_version_rows(
    monkeypatch: pytest.MonkeyPatch, revisions: list[tuple[str, ...]]
) -> None:
    class Connection:
        def execute(self, statement: str, _parameters=None):
            if "current_database" in statement:
                return SimpleNamespace(fetchone=lambda: ("simulation_dashboard", "simdashboard_app"))
            if "FROM alembic_version" in statement:
                return SimpleNamespace(fetchall=lambda: revisions)
            raise AssertionError(f"unexpected query: {statement}")

    monkeypatch.setenv("SIM_DASH_APP_ROLE", "simdashboard_app")
    monkeypatch.setattr(preflight, "REQUIRED_TABLES", ())
    with pytest.raises(RuntimeError, match="정확히 하나"):
        preflight._verify_runtime_contract(Connection())
