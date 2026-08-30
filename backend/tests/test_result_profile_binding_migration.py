from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import sqlalchemy as sa


BACKEND = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "migrations" / "versions"


def _migration(filename: str):
    spec = spec_from_file_location(filename.removesuffix(".py"), VERSIONS / filename)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Statements:
    def __init__(self) -> None:
        self.values: list[object] = []

    def execute(self, statement: object) -> None:
        self.values.append(statement)


def test_result_profile_migration_revisions_fit_alembic_version_num() -> None:
    revision_14 = _migration("0014_project_result_profile_binding_revisions.py")
    revision_15 = _migration("0015_seed_legacy_drop_result_layout.py")
    assert len(revision_14.revision) <= 32
    assert len(revision_15.revision) <= 32
    assert revision_14.revision == "0014_result_profile_revs"
    assert revision_15.down_revision == revision_14.revision


def test_result_profile_migration_chain_and_roundtrip_sql(monkeypatch) -> None:
    config = Config(str(BACKEND / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    revision_14 = script.get_revision("0014_result_profile_revs")
    revision_15 = script.get_revision("0015_legacy_drop_layout")
    assert revision_14 and revision_14.down_revision == "0013_project_result_profile_menu"
    assert revision_15 and revision_15.down_revision == "0014_result_profile_revs"
    assert tuple(script.get_heads()) == ("0018_batch_attempt_run_identity",)

    module = _migration("0014_project_result_profile_binding_revisions.py")
    statements = _Statements()
    monkeypatch.setattr(module, "op", statements)
    module.downgrade()
    sql = "\n".join(statements.values)
    assert "DELETE FROM project_request_type_result_profiles AS older" in sql
    assert "older.binding_version < newer.binding_version" in sql
    assert statements.values.index("ALTER TABLE project_request_type_result_profiles ADD PRIMARY KEY (project_id, request_type_id, request_type_version)") > 0
    assert statements.values[-1] == "ALTER TABLE project_request_type_result_profiles DROP COLUMN IF EXISTS binding_version"

    statements = _Statements()
    monkeypatch.setattr(module, "op", statements)
    module.upgrade()
    assert "ADD COLUMN IF NOT EXISTS binding_version" in "\n".join(statements.values)
    assert "PRIMARY KEY (project_id, request_type_id, request_type_version, binding_version)" in "\n".join(statements.values)


def test_legacy_drop_seed_sql_uses_bind_safe_jsonb_cast(monkeypatch) -> None:
    module = _migration("0015_seed_legacy_drop_result_layout.py")
    statements = _Statements()
    monkeypatch.setattr(module, "op", statements)
    module.upgrade()
    assert len(statements.values) == 1
    statement = statements.values[0]
    assert isinstance(statement, sa.sql.elements.TextClause)
    assert not statement._bindparams
    sql = str(statement)
    assert "::jsonb" not in sql.casefold()
    assert "CAST(" in sql and " AS JSONB)" in sql
    assert '"template_version":1' in sql and r"\:" not in sql


def test_legacy_drop_seed_compiles_in_postgres_offline_context(monkeypatch) -> None:
    module = _migration("0015_seed_legacy_drop_result_layout.py")
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(module, "op", Operations(context))
    module.upgrade()
    sql = output.getvalue()
    assert "INSERT INTO request_result_layout_snapshots" in sql
    assert "CAST(" in sql and " AS JSONB)" in sql
    assert '"template_version":1' in sql and r"\:" not in sql
