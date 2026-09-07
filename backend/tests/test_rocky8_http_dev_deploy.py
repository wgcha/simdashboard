from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(os.name != "posix", reason="Rocky 8 HTTP deployment tests require POSIX shell tooling"),
]
DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "rocky8"


def _check(tmp_path: Path, *, dev_http: bool, extra: str = "") -> subprocess.CompletedProcess[str]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for directory in ("backend/app", "backend/migrations", "frontend/dist"):
        (bundle / "payload" / directory).mkdir(parents=True)
    (bundle / "payload/backend/requirements.lock").write_text("", encoding="utf-8")
    (bundle / "payload/frontend/dist/index.html").write_text("fixture", encoding="utf-8")
    (bundle / "payload/.python-version").write_text("3.12.13\n", encoding="utf-8")
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=rocky\nVERSION_ID=8.6\n", encoding="utf-8")
    source = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    source = source.replace("/etc/os-release", str(os_release)).replace('"${EUID}"', '"0"')
    # Inspect effective values at the real --check success boundary. Nothing
    # after that boundary (OS packages/DB/systemd) may run in these tests.
    source = source.replace(
        'log "Validation passed for release',
        'printf "EFFECTIVE profile=%s cookie=%s cors=%s\\n" "${deployment_profile}" "${AUTH_COOKIE_SECURE}" "${CORS_ALLOWED_ORIGINS}"\n  log "Validation passed for release',
    )
    installer = bundle / "install.sh"
    installer.write_text(source, encoding="utf-8")
    config = tmp_path / "install.env"
    config.write_text(
        "SERVER_NAME=192.0.2.10\n"
        "DATABASE_URL=postgresql://fixture:fixture@127.0.0.1/fixture\n"
        "MIGRATE_DATABASE=0\n"
        "AUTH_SECRET_KEY=fixture-secret-with-at-least-32-characters\n"
        "AUTH_MODE=password\n"
        "AUTH_COOKIE_SECURE=true\n"
        "TLS_CERTIFICATE=/missing/dashboard.example.com.pem\n"
        "TLS_CERTIFICATE_KEY=/missing/dashboard.example.com.key\n"
        f"PYTHON_BIN={sys.executable}\n"
        + extra,
        encoding="utf-8",
    )
    config.chmod(0o600)
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    root_stat = mock_bin / "stat"
    root_stat.write_text(
        '#!/usr/bin/env bash\nif [[ "$1" == -c && "$2" == %u ]]; then echo 0; else exec /usr/bin/stat "$@"; fi\n',
        encoding="utf-8",
    )
    root_stat.chmod(0o755)
    args = ["bash", str(installer), "--config", str(config), "--check"]
    if dev_http:
        args.append("--dev-http")
    return subprocess.run(args, env=os.environ | {"PATH": f"{mock_bin}:{os.environ['PATH']}"}, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("cors", ["", "CORS_ALLOWED_ORIGINS=https://192.0.2.10\n", "CORS_ALLOWED_ORIGINS=http://192.0.2.10\n"])
def test_explicit_dev_http_accepts_missing_certificates_and_sets_matching_cookie_and_cors(tmp_path: Path, cors: str) -> None:
    result = _check(tmp_path, dev_http=True, extra=cors)
    assert result.returncode == 0, result.stderr
    assert "profile=rocky8-dev-http cookie=false cors=http://192.0.2.10" in result.stdout


def test_default_https_still_rejects_missing_certificate(tmp_path: Path) -> None:
    result = _check(tmp_path, dev_http=False)
    assert result.returncode != 0
    assert "TLS certificate is not readable" in result.stderr


@pytest.mark.parametrize("secure", [True, False])
def test_default_https_cookie_guard_with_readable_certificate(tmp_path: Path, secure: bool) -> None:
    cert = tmp_path / "test-cert.pem"
    key = tmp_path / "test-key.key"
    cert.write_text("readability fixture only", encoding="utf-8")
    key.write_text("readability fixture only", encoding="utf-8")
    result = _check(tmp_path, dev_http=False, extra=f"TLS_CERTIFICATE={cert}\nTLS_CERTIFICATE_KEY={key}\nAUTH_COOKIE_SECURE={str(secure).lower()}\n")
    if secure:
        assert result.returncode == 0, result.stderr
        assert "profile=rocky8 cookie=true cors=https://192.0.2.10" in result.stdout
    else:
        assert result.returncode != 0
        assert "Rocky production requires AUTH_COOKIE_SECURE=true" in result.stderr


def test_dev_http_does_not_need_even_placeholder_certificate_settings(tmp_path: Path) -> None:
    result = _check(tmp_path, dev_http=True, extra="TLS_CERTIFICATE=\nTLS_CERTIFICATE_KEY=\n")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("extra", ["AUTH_MODE=oidc\n", "AUTH_MODE=disabled\n", "CORS_ALLOWED_ORIGINS=https://another.example\n", "SIMDASH_IMPORT_READINESS_POLICY=legacy\n", "DATABASE_URL=duckdb://fixture\n"])
def test_dev_http_does_not_silently_downgrade_auth_or_skip_data_safety(tmp_path: Path, extra: str) -> None:
    result = _check(tmp_path, dev_http=True, extra=extra)
    assert result.returncode != 0


def test_http_nginx_preserves_proxy_routes_without_ssl() -> None:
    source = (DEPLOY / "nginx/simdashboard-http-dev.conf.template").read_text(encoding="utf-8")
    assert "listen 80;" in source
    assert "ssl_certificate" not in source
    assert "listen 443" not in source
    for required in ("location /api/", "try_files $uri @backend_assets;", "proxy_set_header X-Forwarded-Proto $scheme;", "http://127.0.0.1:__REPLACE_API_PORT__", "try_files $uri $uri/ /index.html;"):
        assert required in source
