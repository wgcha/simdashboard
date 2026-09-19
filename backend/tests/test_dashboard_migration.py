from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import duckdb
import pytest

from app.adapters.persistence.dashboard_schema import ensure_dashboard_schema


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
