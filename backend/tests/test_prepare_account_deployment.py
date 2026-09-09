from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from scripts import prepare_account_deployment as deployment


def _project(tmp_path: Path, *, backend: str = "duckdb") -> Path:
    (tmp_path / ".env").write_text(
        f"ANALYSIS_DB_BACKEND={backend}\nANALYSIS_DUCKDB_PATH={(tmp_path / 'data.duckdb').as_posix()}\nDATABASE_URL=postgresql://app:app@localhost:5432/testdb\n",
        encoding="utf-8",
    )
    return tmp_path


def test_fresh_duckdb_does_not_create_database_or_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANALYSIS_DB_BACKEND", raising=False)
    monkeypatch.delenv("ANALYSIS_DUCKDB_PATH", raising=False)
    root = _project(tmp_path)
    deployment.prepare(root)
    assert not (tmp_path / "data.duckdb").exists()
    manifests = list((tmp_path / "backups" / "accounts").glob("*/manifest.json"))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding="utf-8"))["classification"] == "fresh"


def test_missing_backend_defaults_to_postgresql_and_requires_database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANALYSIS_DB_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ANALYSIS_DUCKDB_PATH", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        deployment.prepare(tmp_path)

    assert not (tmp_path / "backend" / "data" / "analysis_dashboard.duckdb").exists()
    manifests = list((tmp_path / "backups" / "accounts").glob("*/manifest.json"))
    assert manifests == []


def test_existing_duckdb_backup_preserves_account_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANALYSIS_DB_BACKEND", raising=False)
    monkeypatch.delenv("ANALYSIS_DUCKDB_PATH", raising=False)
    root = _project(tmp_path)
    database = tmp_path / "data.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE users(id VARCHAR, username VARCHAR, password_hash VARCHAR)")
        connection.execute("INSERT INTO users VALUES ('u1', 'alice', 'argon2$secret-hash')")
    directory = deployment.prepare(root)
    copied = directory / "database.duckdb"
    assert copied.exists()
    with duckdb.connect(str(copied), read_only=True) as connection:
        assert connection.execute("SELECT password_hash FROM users WHERE username='alice'").fetchone()[0] == "argon2$secret-hash"


def test_failed_backup_marks_directory_and_never_reports_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(deployment, "_duckdb_backup", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        deployment.prepare(root)
    failed = list((tmp_path / "backups" / "accounts").glob("*/BACKUP_FAILED"))
    assert len(failed) == 1


def test_process_environment_wins_over_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    (tmp_path / ".env").write_text("ANALYSIS_DB_BACKEND=duckdb\n", encoding="utf-8")
    monkeypatch.setenv("ANALYSIS_DB_BACKEND", "postgresql")
    assert deployment._effective_env(root)["ANALYSIS_DB_BACKEND"] == "postgresql"


def test_legacy_postgres_schema_is_backed_up_without_account_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Result:
        def __init__(self, rows):
            self.rows = rows
        def fetchone(self):
            return self.rows[0]
        def fetchall(self):
            return self.rows

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql):
            if "count(*)" in sql: return Result([(1,)])
            return Result([("legacy_table",)])

    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    dump = tmp_path / "backup.dump"
    observed = {}
    def run(command, **kwargs):
        observed.update(kwargs.get("env", {}))
        if "pg_dump" in command[0]:
            Path(command[command.index("--file") + 1]).write_bytes(b"legacy dump")
    monkeypatch.setenv("PGPASSWORD", "ambient-secret")
    monkeypatch.setattr(deployment.subprocess, "run", run)
    result = deployment._postgres_backup("postgresql://app:app@localhost:5432/testdb", tmp_path, {"POSTGRES_BIN": ""})
    assert result["classification"] == "existing"
    assert result["database_backup"]["legacy_inventory_status"] == "unavailable_legacy_schema"
    assert observed["PGPASSWORD"] == "app"


def test_relative_database_path_uses_backend_env_and_directory_is_not_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    (root / ".env").write_text("ANALYSIS_DB_BACKEND=duckdb\n", encoding="utf-8")
    (root / "backend").mkdir()
    (root / "backend" / ".env").write_text("ANALYSIS_DUCKDB_PATH=data/managed.duckdb\n", encoding="utf-8")
    monkeypatch.delenv("ANALYSIS_DUCKDB_PATH", raising=False)
    assert deployment._effective_env(root)["ANALYSIS_DUCKDB_PATH"] == "data/managed.duckdb"
    with pytest.raises(RuntimeError, match="일반 파일"):
        deployment._duckdb_backup(root / "backend", tmp_path)


def test_postgres_database_name_case_mismatch_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="같은 PostgreSQL 대상"):
        deployment._postgres_backup(
            "postgresql://app:app@localhost:5432/CaseDb",
            tmp_path,
            {"POSTGRES_OWNER_URL": "postgresql://owner:owner@LOCALHOST:5432/casedb"},
        )


def test_nonpublic_relation_is_existing_and_not_fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Result:
        def fetchone(self): return (1,)
        def fetchall(self): return [("old_table",)]
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql): return Result()
    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    observed = []
    def run(command, **kwargs):
        observed.append(command)
        if "pg_dump" in command[0]: Path(command[command.index("--file") + 1]).write_bytes(b"dump")
    monkeypatch.setattr(deployment.subprocess, "run", run)
    result = deployment._postgres_backup("postgresql://app:app@localhost:5432/testdb", tmp_path, {"POSTGRES_BIN": ""})
    assert result["classification"] == "existing"
    assert any("pg_dump" in command[0] for command in observed)


def test_current_schema_backup_failure_does_not_fallback_to_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0]
        def fetchall(self): return self.rows
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql):
            if "count(*)" in sql: return Result([(2,)])
            return Result([("users",), ("project_memberships",)])
    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: Connection())
    def fail(*args, **kwargs): raise deployment.subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(deployment.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="PostgreSQL 계정 백업"):
        deployment._postgres_backup("postgresql://app:app@localhost:5432/testdb", tmp_path, {"POSTGRES_BIN": ""})
