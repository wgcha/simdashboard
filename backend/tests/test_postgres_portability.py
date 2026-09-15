import re
from pathlib import Path

from app.database_connection import _postgres_statement, _sqlalchemy_url
from scripts.export_postgres_schema import extract_schema
from scripts.migrate_duckdb_to_postgres import compare, normalize, table_order
from scripts.postgres_cli import parse_target
from scripts.postgres_semantic_schema import SEMANTIC_DDL


BACKEND = Path(__file__).resolve().parents[1]


def test_postgres_schema_export_is_current_and_portable():
    generated = extract_schema(BACKEND / "app" / "database.py")
    stored = (BACKEND / "migrations" / "schema.sql").read_text(encoding="utf-8")
    assert generated == stored
    assert " JSONB" in stored
    assert " DOUBLE PRECISION" in stored
    assert "regexp_matches" not in stored
    assert "sha256 ~ '^[0-9a-f]{64}$'" in stored
    assert "INSERT OR IGNORE" not in stored
    assert "CREATE TABLE IF NOT EXISTS users" in stored
    assert "CREATE TABLE IF NOT EXISTS audit_events" in stored
    assert "account_status IN ('PENDING', 'ACTIVE', 'SUSPENDED')" in stored
    assert "CREATE TABLE IF NOT EXISTS project_memberships" in stored
    assert "CREATE TABLE IF NOT EXISTS project_workspace_layouts" in stored
    assert "uq_users_oidc_identity" in stored
    assert len(table_order()) >= 50


def test_postgres_semantic_bootstrap_ddl_covers_migration_tables_and_constraints():
    migrations = {
        "0024_semantic_mapping.py": {
            "semantic_result_items",
            "semantic_result_item_versions",
            "semantic_recipes",
            "semantic_recipe_versions",
            "semantic_templates",
            "semantic_template_versions",
            "semantic_folder_bindings",
            "semantic_import_provenance",
        },
        "0025_semantic_vocabulary.py": {
            "semantic_vocabulary_entries",
            "semantic_vocabulary_terms",
        },
        "0026_semantic_import_review.py": {
            "semantic_import_review_items",
            "semantic_import_review_events",
        },
    }
    semantic_tables = set(re.findall(r"^CREATE TABLE IF NOT EXISTS (\w+)", SEMANTIC_DDL, re.MULTILINE))
    assert semantic_tables == set().union(*migrations.values())

    migration_source = "\n".join(
        (BACKEND / "migrations" / "versions" / filename).read_text(encoding="utf-8")
        for filename in migrations
    )
    for clause in (
        "sample_sha256 CHAR(64)",
        "sample_bytes BYTEA",
        "key ~ '^[a-z][a-z0-9_]{1,127}$'",
        "ON DELETE CASCADE",
        "binding_id VARCHAR NOT NULL REFERENCES semantic_folder_bindings(id)",
        "CHECK((selected_recipe_id IS NULL) = (selected_recipe_version IS NULL))",
    ):
        assert clause in migration_source
        assert clause in SEMANTIC_DDL


def test_postgres_sql_adapter_translates_parameters_and_conflict_policy():
    assert _postgres_statement("SELECT * FROM projects WHERE id=?") == "SELECT * FROM projects WHERE id=%s"
    translated = _postgres_statement("INSERT OR IGNORE INTO projects VALUES (?, ?);")
    assert translated == "INSERT INTO projects VALUES (%s, %s) ON CONFLICT DO NOTHING;"
    selected = _postgres_statement("INSERT OR IGNORE INTO dashboard_versions SELECT id, version FROM dashboards")
    assert selected.endswith("ON CONFLICT DO NOTHING")
    assert _postgres_statement("name LIKE 'chassis_%_rear' AND id=?") == "name LIKE 'chassis_%%_rear' AND id=%s"
    assert _postgres_statement("json_extract_string(result.metadata_json, '$.variable_key') = ?") == (
        "(result.metadata_json ->> 'variable_key') = %s"
    )


def test_postgres_url_and_cross_database_manifest_normalization():
    assert _sqlalchemy_url("postgresql://user:pass@db/app") == "postgresql+psycopg://user:pass@db/app"
    assert _sqlalchemy_url("postgres://user:pass@db/app") == "postgresql+psycopg://user:pass@db/app"
    assert normalize('{"b": 2, "a": 1}', json_value=True) == {"a": 1, "b": 2}
    assert compare({"projects": {"count": 1, "checksum": "a"}}, {"projects": {"count": 1, "checksum": "a"}}) == []
    target = parse_target("postgresql+psycopg://app:p%40ss@db.example:5544/simulation")
    assert (target.host, target.port, target.username, target.password, target.database) == (
        "db.example", 5544, "app", "p@ss", "simulation",
    )
