from __future__ import annotations

import json

import pytest

from scripts import check_media_storage_preflight as preflight


pytestmark = pytest.mark.unit


def test_dual_read_explicitly_skips_without_database_access(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "media_storage_mode", lambda: "dual-read")
    monkeypatch.setattr(preflight, "_run_database_only_verifier", lambda: pytest.fail("verifier must be skipped"))

    assert preflight.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "skipped",
        "mode": "dual-read",
        "database_access": False,
        "reason": "database-only media verifier is required only for database-only mode",
    }


def test_database_only_runs_verifier_and_returns_report(monkeypatch, capsys):
    monkeypatch.setattr(preflight, "media_storage_mode", lambda: "database-only")
    monkeypatch.setattr(preflight, "_run_database_only_verifier", lambda: {"status": "database_only", "blob_count": 2})

    assert preflight.main() == 0
    assert json.loads(capsys.readouterr().out) == {"status": "database_only", "blob_count": 2}


def test_invalid_mode_fails_closed_without_running_verifier(monkeypatch):
    monkeypatch.setattr(preflight, "media_storage_mode", lambda: "invalid")
    monkeypatch.setattr(preflight, "_run_database_only_verifier", lambda: pytest.fail("invalid mode must not verify"))

    with pytest.raises(RuntimeError, match="MEDIA_STORAGE_MODE_INVALID"):
        preflight.main()
