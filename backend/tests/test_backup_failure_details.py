from __future__ import annotations

import json
import subprocess

import pytest

from scripts.backup_failure_details import (
    DETAIL_PREFIX,
    backup_stage,
    child_failure_details,
    exception_details,
)


def test_exception_details_allowlists_fields_and_never_serializes_secret() -> None:
    error = subprocess.CalledProcessError(7, ["pg_dump", "PASSWORD=secret", "C:/private.db"])
    error.filename = "C:/secret.txt"
    error.stderr = "secret stderr"
    error.sqlstate = "42501"

    result = exception_details(error)

    assert result == {"exception_type": "CalledProcessError", "returncode": 7, "sqlstate": "42501"}
    assert "secret" not in json.dumps(result).lower()


def test_unknown_exception_and_invalid_attributes_are_safe() -> None:
    class SecretError(Exception):
        errno = "password"
        sqlstate = ["secret"]

    assert exception_details(SecretError("do not read me")) == {"exception_type": "Exception"}


def test_backup_stage_prints_safe_detail_and_reraises_original(capsys: pytest.CaptureFixture[str]) -> None:
    error = RuntimeError("secret path")
    with pytest.raises(RuntimeError) as caught:
        with backup_stage("pg_dump"):
            raise error
    assert caught.value is error
    line = capsys.readouterr().err.strip()
    assert line.startswith(DETAIL_PREFIX)
    assert child_failure_details(line) == {"stage": "pg_dump", "exception_type": "RuntimeError"}
    assert "secret" not in line


def test_child_parser_ignores_untrusted_lines_and_returns_last_valid() -> None:
    valid1 = DETAIL_PREFIX + '{"stage":"config","exception_type":"ValueError","errno":2}'
    malformed = DETAIL_PREFIX + '{"stage":"config","exception_type":"ValueError"} trailing'
    untrusted = DETAIL_PREFIX + '{"stage":"config","exception_type":"SecretError","message":"password"}'
    valid2 = DETAIL_PREFIX + '{"stage":"pg_dump","exception_type":"CalledProcessError","returncode":-2}'
    assert child_failure_details("stderr first\n" + valid1 + "\n" + malformed + "\n" + untrusted + "\n" + valid2) == {
        "stage": "pg_dump",
        "exception_type": "CalledProcessError",
        "returncode": -2,
    }


def test_child_parser_enforces_line_and_total_bounds() -> None:
    too_long = DETAIL_PREFIX + (" " * (16 * 1024))
    valid = DETAIL_PREFIX + '{"stage":"report_success","exception_type":"OSError","winerror":5}'
    assert child_failure_details(too_long + "\n" + valid) == {
        "stage": "report_success",
        "exception_type": "OSError",
        "winerror": 5,
    }
    assert child_failure_details("x" * (1024 * 1024) + valid) == {}
