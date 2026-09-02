from __future__ import annotations

import sys
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


def test_database_only_verifier_rejects_the_legacy_initialize_option(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operational verifier is read-only and must not initialize a database."""
    monkeypatch.setattr(sys, "argv", ["verify_media_database_only.py", "--initialize"])

    with pytest.raises(SystemExit) as error:
        verifier.main()

    assert error.value.code == 2
    assert "unrecognized arguments: --initialize" in capsys.readouterr().err


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


@pytest.mark.parametrize(
    ("rows", "revision"),
    [([], None), ([(None,)], None), ([("0017_run_identity_v2",)], "0017_run_identity_v2"), ([("0017_run_identity_v2",), ("older",)], None)],
)
def test_database_only_verifier_requires_exactly_one_alembic_version_row(
    rows: list[object], revision: str | None
) -> None:
    assert verifier._single_revision(rows) == revision
