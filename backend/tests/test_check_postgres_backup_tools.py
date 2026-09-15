from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import check_postgres_backup_tools as check


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql://owner:synthetic-secret@private.example.invalid:5432/accounts\n"
        "POSTGRES_BIN=C:/configured/postgres/bin\n",
        encoding="utf-8",
    )
    return tmp_path


def test_check_spawns_only_the_read_only_child_with_effective_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    observed: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://owner:environment-secret@db.example.invalid:5432/accounts")

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="child secret", stderr="")

    monkeypatch.setattr(check.subprocess, "run", run)
    check._run_check(root)

    assert observed["command"] == [check.sys.executable, str(check.BACKEND / "scripts" / "backup_postgres.py"), "--check-tools"]
    assert observed["cwd"] == check.BACKEND
    assert observed["text"] is True
    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"
    assert observed["env"] and observed["env"]["DATABASE_URL"] == "postgresql://owner:environment-secret@db.example.invalid:5432/accounts"
    assert observed["env"]["POSTGRES_BIN"] == "C:/configured/postgres/bin"


def test_success_prints_only_fixed_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(check, "_run_check", lambda _root: None)
    monkeypatch.setattr(check.sys, "argv", ["check_postgres_backup_tools.py", "--project-root", str(root)])

    assert check.main() == 0
    captured = capsys.readouterr()
    assert captured.out == "POSTGRES_BACKUP_TOOLS_READY\n"
    assert captured.err == ""


def test_launch_permission_error_preserves_safe_windows_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> None:
        error = PermissionError(13, "Permission denied", "C:/private/tool.exe")
        error.winerror = 19  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(check.subprocess, "run", fail)
    monkeypatch.setattr(check.sys, "argv", ["check_postgres_backup_tools.py", "--project-root", str(root)])

    assert check.main() == 1
    output = capsys.readouterr().err
    assert "ACCOUNT_BACKUP_FAILED code=ACCOUNT_BACKUP_FAILED_WINDOWS_WRITE_PROTECTED stage=postgres_tools_check" in output
    assert '"errno": 13' in output
    assert '"winerror": 19' in output
    assert "private/tool.exe" not in output
    assert "synthetic-secret" not in output


def test_child_failure_uses_only_canonical_safe_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _project(tmp_path)
    secret = "synthetic-child-secret"
    child = subprocess.CalledProcessError(
        1,
        ["private-command"],
        stderr='POSTGRES_BACKUP_DETAIL {"stage":"pg_dump_version","exception_type":"PermissionError","errno":13,"winerror":19}\n' + secret,
    )
    monkeypatch.setattr(check, "_run_check", lambda _root: (_ for _ in ()).throw(check.deployment.AccountBackupChildError("postgres_tools_check", child)))
    monkeypatch.setattr(check.sys, "argv", ["check_postgres_backup_tools.py", "--project-root", str(root)])

    assert check.main() == 1
    output = capsys.readouterr().err
    assert '"stage": "pg_dump_version"' in output
    assert '"winerror": 19' in output
    assert secret not in output
    assert "private-command" not in output
