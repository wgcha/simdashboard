from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import windows_web_access

pytestmark = pytest.mark.unit


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(windows_web_access, 'ROOT', tmp_path)
    for name in ('WINDOWS_WEB_HOST', 'AUTH_MODE', 'AUTH_COOKIE_SECURE', 'DATABASE_URL', 'AUTH_SECRET_KEY'):
        # Track even previously absent keys so dotenv additions are undone.
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)
    (tmp_path / 'backend').mkdir()
    return tmp_path


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(windows_web_access.sys, 'argv', ['windows_web_access.py', *args])
    code = windows_web_access.main()
    output = capsys.readouterr()
    return code, output.out, output.err


def test_unconfigured_installation_stays_local(isolated_config, monkeypatch, capsys):
    code, output, error = invoke(monkeypatch, capsys)
    assert code == 0 and not error
    assert json.loads(output) == {'host': '127.0.0.1', 'mode': 'local'}


def test_lan_configuration_can_be_read_before_initial_admin_setup(isolated_config, monkeypatch, capsys):
    (isolated_config / '.env').write_text('WINDOWS_WEB_HOST=0.0.0.0\nAUTH_MODE=disabled\n')
    code, output, error = invoke(monkeypatch, capsys)
    assert code == 0 and not error
    assert json.loads(output) == {'host': '0.0.0.0', 'mode': 'lan'}


@pytest.mark.parametrize('mode', ['disabled', 'unknown', ''])
def test_lan_requires_supported_authentication(isolated_config, monkeypatch, capsys, mode):
    monkeypatch.setenv('WINDOWS_WEB_HOST', '0.0.0.0')
    monkeypatch.setenv('AUTH_MODE', mode)
    code, output, error = invoke(monkeypatch, capsys, '--check-auth')
    assert code != 0 and not output
    assert 'AUTH_MODE' in error


@pytest.mark.parametrize('mode', ['password', 'oidc'])
def test_lan_accepts_authenticated_http_configuration(isolated_config, monkeypatch, capsys, mode):
    monkeypatch.setenv('WINDOWS_WEB_HOST', '0.0.0.0')
    monkeypatch.setenv('AUTH_MODE', mode)
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    code, output, error = invoke(monkeypatch, capsys, '--check-auth')
    assert code == 0 and not error
    assert json.loads(output)['mode'] == 'lan'


@pytest.mark.parametrize('secure', ['true', '1', 'YES', 'on'])
def test_https_cookie_configuration_cannot_silently_start_as_lan_http(isolated_config, monkeypatch, capsys, secure):
    monkeypatch.setenv('WINDOWS_WEB_HOST', '0.0.0.0')
    monkeypatch.setenv('AUTH_MODE', 'password')
    monkeypatch.setenv('AUTH_COOKIE_SECURE', secure)
    code, output, error = invoke(monkeypatch, capsys, '--check-auth')
    assert code != 0 and not output
    assert 'HTTPS' in error


@pytest.mark.parametrize('host', ['localhost', '*', '192.168.1.2', 'invalid-private-marker'])
def test_invalid_host_is_rejected_without_echoing_input(isolated_config, monkeypatch, capsys, host):
    monkeypatch.setenv('WINDOWS_WEB_HOST', host)
    code, output, error = invoke(monkeypatch, capsys)
    assert code != 0 and not output
    assert host not in error


def test_process_then_root_then_backend_env_precedence_preserves_files(isolated_config, monkeypatch, capsys):
    root_env = isolated_config / '.env'
    backend_env = isolated_config / 'backend' / '.env'
    root_env.write_text('WINDOWS_WEB_HOST=0.0.0.0\nDATABASE_URL=synthetic-private-database\n')
    backend_env.write_text('WINDOWS_WEB_HOST=127.0.0.1\nAUTH_MODE=password\nAUTH_SECRET_KEY=synthetic-private-secret\n')
    originals = (root_env.read_bytes(), backend_env.read_bytes())
    monkeypatch.setenv('WINDOWS_WEB_HOST', '127.0.0.1')
    code, output, _ = invoke(monkeypatch, capsys)
    assert code == 0 and json.loads(output)['mode'] == 'local'
    monkeypatch.delenv('WINDOWS_WEB_HOST')
    code, output, _ = invoke(monkeypatch, capsys)
    assert code == 0 and json.loads(output) == {'host': '0.0.0.0', 'mode': 'lan'}
    assert 'synthetic-private' not in output
    assert originals == (root_env.read_bytes(), backend_env.read_bytes())


def test_backend_only_environment_is_supported(isolated_config, monkeypatch, capsys):
    (isolated_config / 'backend' / '.env').write_text('WINDOWS_WEB_HOST=0.0.0.0\nAUTH_MODE=password\n')
    code, output, _ = invoke(monkeypatch, capsys, '--check-auth')
    assert code == 0 and json.loads(output)['mode'] == 'lan'


def test_windows_utf8_bom_preserves_first_setting(isolated_config, monkeypatch, capsys):
    (isolated_config / '.env').write_text('WINDOWS_WEB_HOST=0.0.0.0\nAUTH_MODE=password\n', encoding='utf-8-sig')
    code, output, _ = invoke(monkeypatch, capsys, '--check-auth')
    assert code == 0 and json.loads(output)['mode'] == 'lan'
