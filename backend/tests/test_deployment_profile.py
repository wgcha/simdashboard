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
    output = capsys.readouterr().out
    assert "DEPLOYMENT_PROFILE_OK profile=windows-vm-intranet" in output
    assert "media_storage=dual-read" in output


def test_rocky8_profile_requires_postgres_authentication_and_secure_cookie(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "rocky8")
    monkeypatch.setenv("SIMDASH_IMPORT_READINESS_POLICY", "legacy")
    monkeypatch.setattr(preflight, "database_settings", lambda: SimpleNamespace(backend="duckdb"))
    monkeypatch.setattr(preflight, "security_settings", lambda: SimpleNamespace(auth_mode="disabled", cookie_secure=False))
    monkeypatch.setattr(preflight, "directory_settings", lambda: SimpleNamespace(mode="local"))
    with pytest.raises(RuntimeError, match="POSTGRESQL_REQUIRED.*AUTH_REQUIRED.*SECURE_COOKIE_REQUIRED.*READINESS_MARKER_REQUIRED"):
        preflight.main()


@pytest.mark.parametrize("auth_mode", ["password", "oidc"])
def test_rocky8_profile_accepts_supported_production_auth(monkeypatch, capsys, auth_mode):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "rocky8")
    monkeypatch.setenv("SIMDASH_IMPORT_READINESS_POLICY", "required")
    monkeypatch.setattr(preflight, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(preflight, "security_settings", lambda: SimpleNamespace(auth_mode=auth_mode, cookie_secure=True))
    monkeypatch.setattr(preflight, "directory_settings", lambda: SimpleNamespace(mode="local"))
    preflight.main()
    assert "DEPLOYMENT_PROFILE_OK profile=rocky8" in capsys.readouterr().out


def test_rocky8_profile_rejects_legacy_readiness_policy(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "rocky8")
    monkeypatch.setenv("SIMDASH_IMPORT_READINESS_POLICY", "legacy")
    monkeypatch.setattr(preflight, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(preflight, "security_settings", lambda: SimpleNamespace(auth_mode="password", cookie_secure=True))
    monkeypatch.setattr(preflight, "directory_settings", lambda: SimpleNamespace(mode="local"))
    with pytest.raises(RuntimeError, match="READINESS_MARKER_REQUIRED"):
        preflight.main()


def _set_dev_http(monkeypatch, *, backend="postgresql", auth_mode="password", cookie_secure=False, readiness="required"):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "rocky8-dev-http")
    monkeypatch.setenv("SIMDASH_IMPORT_READINESS_POLICY", readiness)
    monkeypatch.setattr(preflight, "database_settings", lambda: SimpleNamespace(backend=backend))
    monkeypatch.setattr(preflight, "security_settings", lambda: SimpleNamespace(auth_mode=auth_mode, cookie_secure=cookie_secure))
    monkeypatch.setattr(preflight, "directory_settings", lambda: SimpleNamespace(mode="local"))


def test_rocky8_dev_http_accepts_postgres_password_insecure_cookie_and_warns(monkeypatch, capsys):
    _set_dev_http(monkeypatch)
    preflight.main()
    captured = capsys.readouterr()
    assert "DEPLOYMENT_PROFILE_OK profile=rocky8-dev-http" in captured.out
    assert "WARN_DEV_HTTP" in captured.err


@pytest.mark.parametrize(
    ("backend", "auth_mode", "cookie_secure", "readiness", "expected"),
    [
        ("duckdb", "password", False, "required", "POSTGRESQL_REQUIRED"),
        ("postgresql", "oidc", False, "required", "PASSWORD_AUTH_REQUIRED"),
        ("postgresql", "disabled", False, "required", "PASSWORD_AUTH_REQUIRED"),
        ("postgresql", "password", True, "required", "INSECURE_COOKIE_REQUIRED_FOR_HTTP"),
        ("postgresql", "password", False, "legacy", "READINESS_MARKER_REQUIRED"),
    ],
)
def test_rocky8_dev_http_rejects_each_incompatible_setting(monkeypatch, backend, auth_mode, cookie_secure, readiness, expected):
    _set_dev_http(monkeypatch, backend=backend, auth_mode=auth_mode, cookie_secure=cookie_secure, readiness=readiness)
    with pytest.raises(RuntimeError, match=expected):
        preflight.main()


def test_rocky8_production_still_requires_secure_cookie(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "rocky8")
    monkeypatch.setenv("SIMDASH_IMPORT_READINESS_POLICY", "required")
    monkeypatch.setattr(preflight, "database_settings", lambda: SimpleNamespace(backend="postgresql"))
    monkeypatch.setattr(preflight, "security_settings", lambda: SimpleNamespace(auth_mode="password", cookie_secure=False))
    monkeypatch.setattr(preflight, "directory_settings", lambda: SimpleNamespace(mode="local"))
    with pytest.raises(RuntimeError, match="SECURE_COOKIE_REQUIRED"):
        preflight.main()
