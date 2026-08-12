from pathlib import Path

from app.database_connection import _postgres_statement, _sqlalchemy_url
from scripts.export_postgres_schema import extract_schema
from scripts.migrate_duckdb_to_postgres import compare, normalize, table_order
from scripts.postgres_cli import parse_target


BACKEND = Path(__file__).resolve().parents[1]


def test_postgres_schema_export_is_current_and_portable():
    generated = extract_schema(BACKEND / "app" / "database.py")
    stored = (BACKEND / "migrations" / "schema.sql").read_text(encoding="utf-8")
    assert generated == stored
    assert " JSONB" in stored
    assert " DOUBLE PRECISION" in stored
    assert "INSERT OR IGNORE" not in stored
    assert "CREATE TABLE IF NOT EXISTS users" in stored
    assert "CREATE TABLE IF NOT EXISTS audit_events" in stored
    assert "account_status IN ('PENDING', 'ACTIVE', 'SUSPENDED')" in stored
    assert "CREATE TABLE IF NOT EXISTS project_memberships" in stored
    assert "CREATE TABLE IF NOT EXISTS project_workspace_layouts" in stored
    assert "uq_users_oidc_identity" in stored
    assert len(table_order()) >= 50


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
