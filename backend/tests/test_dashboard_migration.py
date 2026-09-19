from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import duckdb
import pytest

from app.adapters.persistence.dashboard_schema import ensure_dashboard_schema
from app.database import ensure_folder_environment_schema


pytestmark = pytest.mark.unit


def test_additive_schema_preserves_existing_rows_and_captured_bytes():
    with duckdb.connect(":memory:") as conn:
        conn.execute("CREATE TABLE projects(id VARCHAR PRIMARY KEY, name VARCHAR)")
        conn.execute("CREATE TABLE analysis_requests(id VARCHAR PRIMARY KEY)")
        conn.execute("INSERT INTO projects VALUES ('project', 'Existing project')")
        conn.execute("INSERT INTO analysis_requests VALUES ('request')")
        ensure_dashboard_schema(conn)
        conn.execute("""INSERT INTO dashboard_cases VALUES
            ('case','project','request','root','Project/WR/Case','USAGE','Case','{}',CURRENT_TIMESTAMP)""")
        conn.execute("""INSERT INTO dashboard_captures VALUES
            ('capture','case','hash','v1','[]','{}','tester',CURRENT_TIMESTAMP)""")
        conn.execute("""INSERT INTO dashboard_assets VALUES
            ('asset','capture','Wobble/original.mp4','sha','video/mp4',?,'{}')""", [b"original bytes"])
        ensure_dashboard_schema(conn)
        assert conn.execute("SELECT * FROM projects").fetchall() == [('project', 'Existing project')]
        assert conn.execute("SELECT content FROM dashboard_assets").fetchone()[0] == b"original bytes"
        with pytest.raises(duckdb.ConstraintException):
            conn.execute("""INSERT INTO dashboard_captures VALUES
                ('duplicate','case','hash','v1','[]','{}','tester',CURRENT_TIMESTAMP)""")


def test_migration_is_additive_and_denies_destructive_downgrade(monkeypatch):
    path = Path(__file__).parents[1] / 'migrations/versions/0029_dashboard_captures.py'
    spec = spec_from_file_location('dashboard_migration', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    class Operations:
        def execute(self, statement):
            statements.append(str(statement))
    monkeypatch.setattr(module, 'op', Operations())
    monkeypatch.setenv('SIM_DASH_APP_ROLE', 'dashboard_test_app')
    module.upgrade()
    sql = '\n'.join(statements)
    assert module.down_revision == '0028_folder_catalog'
    assert 'content BYTEA NOT NULL' in sql
    assert 'UNIQUE(case_id, fingerprint)' in sql
    assert 'GRANT SELECT, INSERT ON' in sql
    assert all(word not in sql.upper() for word in ('DROP TABLE', 'DELETE FROM', 'TRUNCATE', 'UPDATE '))
    with pytest.raises(RuntimeError, match='user history'):
        module.downgrade()
    monkeypatch.setenv('SIM_DASH_APP_ROLE', 'unsafe; role')
    with pytest.raises(ValueError, match='safe PostgreSQL identifier'):
        module.upgrade()


def test_environment_schema_keeps_profile_edits_and_existing_capture_bytes():
    with duckdb.connect(':memory:') as conn:
        conn.execute("CREATE TABLE projects(id VARCHAR PRIMARY KEY)")
        conn.execute("CREATE TABLE analysis_requests(id VARCHAR PRIMARY KEY)")
        conn.execute("INSERT INTO projects VALUES ('project')")
        conn.execute("INSERT INTO analysis_requests VALUES ('request')")
        ensure_dashboard_schema(conn)
        conn.execute("INSERT INTO dashboard_cases VALUES ('case','project','request','root','Case','USAGE','Case','{}',CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO dashboard_captures VALUES ('capture','case','hash','v1','[]','{}','tester',CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO dashboard_assets VALUES ('asset','capture','result.png','sha','image/png',?,'{}')", [b'preserved'])
        ensure_folder_environment_schema(conn)
        conn.execute("UPDATE folder_environment_profiles SET revision=7,rules_json=? WHERE environment='USAGE'", ['{"custom":"preserved"}'])
        ensure_folder_environment_schema(conn)
        assert conn.execute("SELECT revision,rules_json FROM folder_environment_profiles WHERE environment='USAGE'").fetchone() == (7, '{"custom":"preserved"}')
        assert conn.execute('SELECT content FROM dashboard_assets').fetchone()[0] == b'preserved'
        assert conn.execute('SELECT count(*) FROM folder_environment_profiles').fetchone()[0] == 2


def test_environment_migration_does_not_rewrite_old_rules_or_captures(monkeypatch):
    path = Path(__file__).parents[1] / 'migrations/versions/0030_folder_environment_profiles.py'
    spec = spec_from_file_location('environment_migration', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    statements = []
    class Operations:
        def execute(self, statement):
            statements.append(str(statement))
    monkeypatch.setattr(module, 'op', Operations())
    monkeypatch.setenv('SIM_DASH_APP_ROLE', 'environment_test_app')
    module.upgrade()
    sql = '\n'.join(statements).upper()
    assert module.down_revision == '0029_dashboard_captures'
    assert all(word not in sql for word in ('DROP TABLE', 'DELETE FROM', 'TRUNCATE', 'UPDATE DASHBOARD_', 'UPDATE FOLDER_DISCOVERY_'))
    assert 'IDEMPOTENCY_KEY VARCHAR NOT NULL UNIQUE' in sql
    assert 'GRANT SELECT, INSERT, UPDATE ON' in sql
    with pytest.raises(RuntimeError, match='durable user history'):
        module.downgrade()
