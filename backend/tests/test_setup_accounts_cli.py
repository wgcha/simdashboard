from __future__ import annotations

import errno
import json
import ctypes
from ctypes import wintypes
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
    class WindowsSharingViolation(OSError):
        """Portable fixture for the Win32 ERROR_LOCK_VIOLATION attribute."""

        def __init__(self) -> None:
            super().__init__(0, "locked")
            self.winerror = 33

    sharing_violation = WindowsSharingViolation()
    assert setup_accounts._is_lock_contention(BlockingIOError(errno.EAGAIN, "locked"))
    assert sharing_violation.winerror == 33
    assert setup_accounts._is_lock_contention(sharing_violation)
    assert not setup_accounts._is_lock_contention(PermissionError("ACL denied"))


def _mock_windows_descriptor_copy(monkeypatch: pytest.MonkeyPatch, owner_matches: bool, group_matches: bool, dacl_information: int, set_result: bool = True):
    source = Path("source.env")
    target = Path("target.env")
    source_descriptor = object()
    target_descriptor = object()
    owner_accessor = object()
    group_accessor = object()
    calls: list[tuple[str, object]] = []
    advapi = SimpleNamespace(
        GetSecurityDescriptorOwner=owner_accessor,
        GetSecurityDescriptorGroup=group_accessor,
        EqualSid=lambda source_sid, target_sid: source_sid == target_sid,
        SetFileSecurityW=lambda path, information, descriptor: calls.append((path, information, descriptor)) or set_result,
    )
    monkeypatch.setattr(setup_accounts, "_windows_advapi32", lambda: advapi)
    monkeypatch.setattr(setup_accounts, "_configure_windows_security_apis", lambda _advapi: None)
    monkeypatch.setattr(
        setup_accounts,
        "_read_windows_security_descriptor",
        lambda _advapi, path, _information: source_descriptor if path == source else target_descriptor,
    )

    def descriptor_sid(_advapi, descriptor, accessor):
        if accessor is owner_accessor:
            return "owner" if descriptor is source_descriptor or owner_matches else "different-owner"
        return "group" if descriptor is source_descriptor or group_matches else "different-group"

    monkeypatch.setattr(setup_accounts, "_windows_descriptor_sid", descriptor_sid)
    monkeypatch.setattr(setup_accounts, "_windows_dacl_security_information", lambda _advapi, _descriptor: dacl_information)
    monkeypatch.setattr(setup_accounts, "_windows_security_error", lambda operation: RuntimeError(operation))
    return source, target, calls


def test_windows_descriptor_copy_skips_write_owner_and_group_for_matching_sids(monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, calls = _mock_windows_descriptor_copy(
        monkeypatch,
        owner_matches=True,
        group_matches=True,
        dacl_information=setup_accounts._DACL_SECURITY_INFORMATION | setup_accounts._PROTECTED_DACL_SECURITY_INFORMATION,
    )

    setup_accounts._copy_windows_security_descriptor(source, target)

    assert len(calls) == 1
    assert calls[0][0] == str(target)
    assert calls[0][1] == setup_accounts._DACL_SECURITY_INFORMATION | setup_accounts._PROTECTED_DACL_SECURITY_INFORMATION


def test_windows_descriptor_copy_keeps_owner_group_restore_required_when_sids_differ(monkeypatch: pytest.MonkeyPatch) -> None:
    source, target, calls = _mock_windows_descriptor_copy(
        monkeypatch,
        owner_matches=False,
        group_matches=False,
        dacl_information=setup_accounts._DACL_SECURITY_INFORMATION | setup_accounts._UNPROTECTED_DACL_SECURITY_INFORMATION,
        set_result=False,
    )

    with pytest.raises(RuntimeError, match="Could not preserve .env access controls"):
        setup_accounts._copy_windows_security_descriptor(source, target)

    assert calls[0][1] == (
        setup_accounts._DACL_SECURITY_INFORMATION
        | setup_accounts._UNPROTECTED_DACL_SECURITY_INFORMATION
        | setup_accounts._OWNER_SECURITY_INFORMATION
        | setup_accounts._GROUP_SECURITY_INFORMATION
    )


@pytest.mark.parametrize(
    ("source_control", "expected_inheritance"),
    [
        (setup_accounts._SE_DACL_PROTECTED, setup_accounts._PROTECTED_DACL_SECURITY_INFORMATION),
        (0, setup_accounts._UNPROTECTED_DACL_SECURITY_INFORMATION),
    ],
)
def test_windows_descriptor_dacl_inheritance_state_comes_from_source_control(
    source_control: int, expected_inheritance: int
) -> None:
    def get_security_descriptor_control(_descriptor, control_pointer, revision_pointer) -> bool:
        ctypes.cast(control_pointer, ctypes.POINTER(wintypes.WORD)).contents.value = source_control
        ctypes.cast(revision_pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = 1
        return True

    advapi = SimpleNamespace(GetSecurityDescriptorControl=get_security_descriptor_control)

    assert setup_accounts._windows_dacl_security_information(advapi, object()) == (
        setup_accounts._DACL_SECURITY_INFORMATION | expected_inheritance
    )
