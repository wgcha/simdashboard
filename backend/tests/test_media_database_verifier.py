from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts import verify_media_database_only as verifier


pytestmark = pytest.mark.unit


def test_database_only_verifier_reads_the_alembic_head_from_the_migration_graph() -> None:
    """The database gate must follow the migration graph, not a copied literal."""
    config = Config(str(Path(verifier.BACKEND) / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1
    assert verifier._expected_revision() == heads[0]


def test_database_only_verifier_does_not_retain_the_removed_stale_revision_constant() -> None:
    source = Path(verifier.__file__).read_text(encoding="utf-8")

    assert "EXPECTED_REVISION" not in source
    assert '"0009_menu_workflow_order"' not in source


@pytest.mark.parametrize(
    ("backend", "current", "expected", "matches"),
    [
        ("postgresql", None, "head", False),
        ("postgresql", "older", "head", False),
        ("postgresql", "head", "head", True),
        ("duckdb", None, None, True),
    ],
)
def test_database_only_verifier_fails_closed_when_postgres_revision_is_missing_or_stale(
    backend: str, current: str | None, expected: str | None, matches: bool
) -> None:
    assert verifier._revision_matches(backend=backend, current=current, expected=expected) is matches
