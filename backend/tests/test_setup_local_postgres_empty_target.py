from __future__ import annotations

import sys
from pathlib import Path

import pytest

from scripts import setup_local_postgres as setup


class _StopAfterBootstrap(RuntimeError):
    pass


class _Cursor:
    def execute(self, statement: str, _parameters=None):
        if "current_user, current_database" in statement:
            return self
        if "rolcreatedb, rolcreaterole, rolsuper" in statement:
            return self
        if "FROM pg_database" in statement:
            return self
        if "SELECT rolname FROM pg_roles" in statement:
            return self
        raise AssertionError(f"unexpected query: {statement}")

    def fetchone(self):
        # The test runs each query once, in main()'s documented order.
        return self._responses.pop(0)

    def fetchall(self):
        return []

    def __init__(self):
        self._responses = [
            ("postgres", "postgres"),
            (True, True, True),
            (1,),  # simulation_dashboard already exists
        ]


class _Connection(_Cursor):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_empty_existing_target_continues_without_replace_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An empty pre-created target is safe; populated targets take the fail-closed branch."""

    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "BACKEND", tmp_path / "backend")
    monkeypatch.setattr(setup, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(setup, "OWNER_ENV_FILE", tmp_path / ".postgres-owner.env")
    monkeypatch.setattr(setup, "dotenv_values", lambda _path: {})
    monkeypatch.setattr(setup, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(setup.psycopg, "connect", lambda *_args, **_kwargs: _Connection())
    monkeypatch.setattr(setup, "target_object_count", lambda _parts: 0)
    monkeypatch.setattr(setup, "write_recovery_marker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(setup, "database_identity", lambda *_args, **_kwargs: "fixture-oid")
    monkeypatch.setattr(setup, "verify_app_privileges", lambda _url: None)

    commands: list[list[str]] = []

    def stop_after_first_setup(command: list[str], _environment: dict[str, str]) -> None:
        commands.append(command)
        raise _StopAfterBootstrap()

    monkeypatch.setattr(setup, "run", stop_after_first_setup)
    monkeypatch.setattr(
        sys,
        "argv",
        ["setup_local_postgres.py", "--seed-mode", "empty"],
    )
    monkeypatch.setenv("POSTGRES_ADMIN_URL", "postgresql://admin:secret@127.0.0.1:5432/postgres")

    with pytest.raises(_StopAfterBootstrap):
        setup.main()

    assert commands and commands[0][-1].endswith("bootstrap_postgres.py")
