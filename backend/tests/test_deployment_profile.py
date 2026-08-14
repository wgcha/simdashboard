from types import SimpleNamespace

import pytest

from scripts import check_deployment_profile as preflight


pytestmark = pytest.mark.unit


def test_windows_vm_profile_requires_postgres_oidc_secure_cookie_and_http_directory(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "windows-vm-intranet")
    monkeypatch.setattr(preflight, "database_settings", lambda: SimpleNamespace(backend="duckdb"))
    monkeypatch.setattr(preflight, "security_settings", lambda: SimpleNamespace(auth_mode="password", cookie_secure=False))
    monkeypatch.setattr(preflight, "directory_settings", lambda: SimpleNamespace(mode="local"))
    with pytest.raises(RuntimeError, match="POSTGRESQL_REQUIRED.*OIDC_REQUIRED.*SECURE_COOKIE_REQUIRED.*HTTP_DIRECTORY_REQUIRED"):
        preflight.main()


def test_windows_vm_profile_accepts_only_complete_intranet_contract(monkeypatch, capsys):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "windows-vm-intranet")
    monkeypatch.setattr(preflight, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(preflight, "security_settings", lambda: SimpleNamespace(auth_mode="oidc", cookie_secure=True))
    monkeypatch.setattr(preflight, "directory_settings", lambda: SimpleNamespace(mode="http"))
    preflight.main()
    assert "DEPLOYMENT_PROFILE_OK profile=windows-vm-intranet" in capsys.readouterr().out
