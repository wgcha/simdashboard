from __future__ import annotations

import errno
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import setup_accounts


pytestmark = pytest.mark.unit


def test_password_accepts_the_confirmed_eight_character_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = iter(("12345678", "12345678"))
    monkeypatch.setattr(setup_accounts.getpass, "getpass", lambda _prompt: next(entries))

    assert setup_accounts._prompt_password() == "12345678"


def test_password_mismatch_is_bounded_and_has_a_distinct_code(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = iter(("first-secret", "second-secret") * setup_accounts.PROMPT_ATTEMPTS)
    monkeypatch.setattr(setup_accounts.getpass, "getpass", lambda _prompt: next(entries))

    with pytest.raises(setup_accounts.SetupInputError) as raised:
        setup_accounts._prompt_password()

    assert raised.value.code == "ACCOUNT_SETUP_FAILED_INPUT_PASSWORD_MISMATCH"


def test_retry_prompts_explain_the_allowed_format_without_echoing_password(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    entries = iter(("short", "short") * setup_accounts.PROMPT_ATTEMPTS)
    monkeypatch.setattr(setup_accounts.getpass, "getpass", lambda _prompt: next(entries))

    with pytest.raises(setup_accounts.SetupInputError):
        setup_accounts._prompt_password()

    output = capsys.readouterr()
    assert "8-256 characters" in output.out + output.err
    assert "short" not in output.out + output.err


def test_duplicate_username_never_promotes_or_resets_an_existing_account(monkeypatch: pytest.MonkeyPatch) -> None:
    names = iter(("existing-admin",) * setup_accounts.PROMPT_ATTEMPTS)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(names))
    monkeypatch.setattr(setup_accounts, "_username_is_available", lambda _username: False)

    with pytest.raises(setup_accounts.SetupInputError) as raised:
        setup_accounts._prompt_admin()

    assert raised.value.code == "ACCOUNT_SETUP_FAILED_INPUT_USERNAME_DUPLICATE"


def test_cli_diagnostic_is_allowlisted_and_never_echoes_a_secret(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    secret = "synthetic-setup-password"
    monkeypatch.setattr(setup_accounts, "main", lambda: (_ for _ in ()).throw(RuntimeError(f"postgresql://owner:{secret}@private-db")))

    assert setup_accounts.run_cli() == 1

    output = capsys.readouterr().err
    assert "ACCOUNT_SETUP_FAILED code=ACCOUNT_SETUP_FAILED_UNKNOWN" in output
    detail = next(line for line in output.splitlines() if line.startswith("ACCOUNT_SETUP_DETAIL "))
    assert json.loads(detail.removeprefix("ACCOUNT_SETUP_DETAIL ")) == {"exception_type": "RuntimeError"}
    assert secret not in output
    assert "private-db" not in output


def test_external_value_identical_to_requested_update_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("AUTH_MODE=password\n", encoding="utf-8")
    monkeypatch.setattr(setup_accounts, "ENV_FILE", env_file)
    monkeypatch.setattr(setup_accounts, "INITIAL_ENVIRONMENT", {"AUTH_MODE": "password"})

    setup_accounts._preflight_updates({"AUTH_MODE": "password"}, {})
    setup_accounts._write_env({"AUTH_MODE": "password"}, {})

    assert env_file.read_text(encoding="utf-8") == "AUTH_MODE=password\n"


def test_conflicting_external_value_is_rejected_before_account_write(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_accounts, "INITIAL_ENVIRONMENT", {"AUTH_MODE": "oidc"})

    with pytest.raises(RuntimeError):
        setup_accounts._preflight_updates({"AUTH_MODE": "password"}, {})

    assert setup_accounts._safe_failure_code(RuntimeError("AUTH_MODE is supplied by the process environment")) == "ACCOUNT_SETUP_FAILED_ENVIRONMENT_OVERRIDE"


def test_postgres_lock_contention_aborts_without_writing_account_or_env(tmp_path, monkeypatch, capsys) -> None:
    from app import config, database_connection

    env_file = tmp_path / '.env'
    env_file.write_text('AUTH_MODE=password\nAUTH_SECRET_KEY=' + 'x' * 40 + '\n', encoding='utf-8')
    original = env_file.read_bytes()
    monkeypatch.setattr(setup_accounts, 'ENV_FILE', env_file)
    monkeypatch.setenv('AUTH_MODE', 'password')
    monkeypatch.setenv('AUTH_SECRET_KEY', 'x' * 40)
    monkeypatch.setattr(setup_accounts, 'INITIAL_ENVIRONMENT', {})
    monkeypatch.setattr(setup_accounts.sys, 'argv', ['setup', '--env-file', str(env_file)])
    monkeypatch.setattr(setup_accounts.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(setup_accounts, '_acquire_setup_lock', lambda: None)
    monkeypatch.setattr(setup_accounts, '_has_active_password_admin', lambda: False)
    monkeypatch.setattr(setup_accounts, '_prompt_admin', lambda: ('new-admin', 'Admin', 'test1234'))
    monkeypatch.setattr(config, 'database_settings', lambda: SimpleNamespace(backend='postgresql'))
    statements = []
    exits = []

    class Connection:
        backend = 'postgresql'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            exits.append(exc_type)

        def execute(self, statement, parameters=None):
            statements.append(statement)
            if statement.startswith('SELECT pg_try_advisory_xact_lock'):
                return SimpleNamespace(fetchone=lambda: (False,))
            assert statement.startswith('SELECT 1 FROM users WHERE username=')
            return SimpleNamespace(fetchone=lambda: None)

    monkeypatch.setattr(database_connection, 'connect', Connection)
    assert setup_accounts.run_cli() == 1
    assert 'ACCOUNT_SETUP_FAILED_DATABASE_IN_PROGRESS' in capsys.readouterr().err
    assert exits[-1] is setup_accounts.SetupInProgressError
    assert len(statements) == 2
    assert env_file.read_bytes() == original


def test_only_an_actual_nonblocking_lock_collision_maps_to_setup_in_progress() -> None:
    assert setup_accounts._is_lock_contention(BlockingIOError(errno.EAGAIN, "locked"))
    assert setup_accounts._is_lock_contention(OSError(0, "locked", None, 33))
    assert not setup_accounts._is_lock_contention(PermissionError("ACL denied"))
