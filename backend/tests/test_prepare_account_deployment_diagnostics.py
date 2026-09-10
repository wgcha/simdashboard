from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import prepare_account_deployment as deployment
from scripts.postgres_cli import PostgresToolNotFound


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(
        "ANALYSIS_DB_BACKEND=duckdb\n"
        f"ANALYSIS_DUCKDB_PATH={(tmp_path / 'data.duckdb').as_posix()}\n"
        "DATABASE_URL=postgresql://owner:super-secret@db.example.invalid:5432/accounts\n",
        encoding="utf-8",
    )
    return tmp_path


def test_failure_report_is_structured_and_never_contains_synthetic_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    secret = "synthetic-password-must-not-leak"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"postgresql://owner:{secret}@private.example.invalid/hidden")

    monkeypatch.setattr(deployment, "_duckdb_backup", fail)
    with pytest.raises(deployment.AccountBackupFailure) as raised:
        deployment.prepare(root)

    failure = raised.value
    assert failure.code == "ACCOUNT_BACKUP_FAILED_UNKNOWN"
    assert failure.report_path is not None
    contents = failure.report_path.read_text(encoding="utf-8")
    assert secret not in contents
    assert "private.example.invalid" not in contents
    assert json.loads(contents) == {
        "format": "analysis-canvas-account-deployment-backup-failure",
        "format_version": 1,
        "created_at": json.loads(contents)["created_at"],
        "code": "ACCOUNT_BACKUP_FAILED_UNKNOWN",
        "stage": "duckdb_backup",
        "remediation": deployment._REMEDIATION["ACCOUNT_BACKUP_FAILED_UNKNOWN"],
    }


def test_missing_postgres_tool_has_stable_code() -> None:
    error = PostgresToolNotFound("pg_dump executable / private location")
    assert deployment._safe_failure_code(error) == "ACCOUNT_BACKUP_FAILED_MISSING_POSTGRES_TOOLS"


@pytest.mark.parametrize(("stderr", "code"), [
    ("scripts.postgres_cli.PostgresToolNotFound: pg_dump 실행 파일을 찾지 못했습니다.", "MISSING_POSTGRES_TOOLS"),
    ("psycopg.errors.InsufficientPrivilege: permission denied for table users", "DATABASE_PRIVILEGE"),
    ("PermissionError: [Errno 13] Permission denied: 'backup.dump'", "FILESYSTEM_ACCESS_OR_DISK"),
    ("MediaIntegrityError: MEDIA_INTEGRITY_FAILED", "MEDIA_INTEGRITY"),
    ("pg_dump: error: aborting because of server version mismatch", "POSTGRES_VERSION_MISMATCH"),
])
def test_canonical_child_failure_classification(stderr: str, code: str) -> None:
    child = subprocess.CalledProcessError(1, ["python", "backup_postgres.py"], stderr=stderr)
    error = deployment.AccountBackupChildError("postgres_current_schema_backup", child)
    assert deployment._safe_failure_code(error) == f"ACCOUNT_BACKUP_FAILED_{code}"


def test_cli_failure_never_prints_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path)
    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("postgresql://owner:synthetic-cli-secret@private.example.invalid/accounts")
    monkeypatch.setattr(deployment, "_duckdb_backup", fail)
    monkeypatch.setattr(deployment.sys, "argv", ["prepare_account_deployment.py", "--project-root", str(root)])
    assert deployment.main() == 1
    output = capsys.readouterr()
    assert "ACCOUNT_BACKUP_FAILED_UNKNOWN" in output.err
    assert "ACCOUNT_BACKUP_ACTION" in output.err
    assert "synthetic-cli-secret" not in output.err + output.out
    assert "private.example.invalid" not in output.err + output.out


def test_schema_child_failure_has_stable_code() -> None:
    child = subprocess.CalledProcessError(
        1,
        ["pg_dump"],
        stderr="ERROR: relation users does not exist\nSQLSTATE 42P01",
    )
    error = deployment.AccountBackupChildError("postgres_current_schema_backup", child)
    assert deployment._safe_failure_code(error) == "ACCOUNT_BACKUP_FAILED_DATABASE_SCHEMA_OBJECT_MISSING"


def test_known_pre_media_revision_uses_verified_legacy_dump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queries: list[str] = []

    class Result:
        def __init__(self, rows: list[tuple[object, ...]]):
            self.rows = rows

        def fetchone(self) -> tuple[object, ...]:
            return self.rows[0]

        def fetchall(self) -> list[tuple[object, ...]]:
            return self.rows

    class Connection:
        def __enter__(self) -> "Connection":
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def execute(self, query: str) -> Result:
            queries.append(query)
            if "count(*)" in query:
                return Result([(2,)])
            if "to_regclass" in query:
                return Result([("alembic_version",)])
            if "version_num" in query:
                return Result([("0007_access_control_menu_policy",)])
            return Result([("users",), ("project_memberships",)])

    def child(command: list[str], **_kwargs: object) -> None:
        if "pg_dump" in command[0]:
            Path(command[command.index("--file") + 1]).write_bytes(b"verified legacy dump")

    monkeypatch.setattr("psycopg.connect", lambda *_args, **_kwargs: Connection())
    monkeypatch.setattr(deployment, "_run_backup_child", child)
    result = deployment._postgres_backup(
        "postgresql://app:app@localhost:5432/testdb",
        tmp_path,
        {"POSTGRES_BIN": str(tmp_path)},
    )

    assert result["database_backup"]["legacy_inventory_status"] == "unavailable_legacy_schema"
    assert all("media" not in query.casefold() for query in queries)


def test_backup_children_use_utf8_replace_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def run(_command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed.update(kwargs)
        return subprocess.CompletedProcess(_command, 0, stdout="완료", stderr="")

    monkeypatch.setattr(deployment.subprocess, "run", run)
    deployment._run_backup_child(["backup-child"], cwd=tmp_path, env={}, stage="test")
    assert observed["text"] is True
    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"
    assert observed["env"] and observed["env"]["PYTHONIOENCODING"] == "utf-8"
