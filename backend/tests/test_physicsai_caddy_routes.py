"""PhysicsAI Caddy routes stay optional and off by default (docs/physicsai-integration.md).

PhysicsAI platform.md §20 D1 / phase2.md §18 D9: ``/physicsai/api/*`` goes to the
PhysicsAI backend (prefix kept) and ``/physicsai/*`` serves its static build, both
before the dashboard catch-all. A server without PhysicsAI must see no change.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy/windows/Caddyfile.intranet.template"
INSTALLER = ROOT / "deploy/windows/offline/install.ps1"
EXAMPLE = ROOT / "deploy/windows/offline/install.example.json"


def _here_string(source: str, variable: str) -> str:
    match = re.search(r"\$" + variable + r' = @"\r?\n(.*?)\r?\n"@', source, re.S)
    assert match, f"installer here-string ${variable} is missing"
    return match.group(1)


def _render_installer_caddyfile(*, enabled: bool, static_root: str = "C:/srv/dash", port: int = 8100,
                                physicsai_root: str = "C:/PhysicsAI/frontend/dist", api_port: int = 18080,
                                site: str = "http://:8080") -> str:
    installer = INSTALLER.read_text(encoding="utf-8-sig")
    routes = ""
    if enabled:
        routes = _here_string(installer, "physicsaiRoutes")
        routes = routes.replace("$physicsaiPort", str(port)).replace("$physicsaiStatic", physicsai_root)
    text = _here_string(installer, "caddyText")
    for name, value in {"siteAddress": site, "tlsLine": "", "staticRoot": static_root, "apiPort": str(api_port), "physicsaiRoutes": routes}.items():
        text = text.replace("$" + name, value)
    assert "$" not in text, "unrendered PowerShell variable in the installer Caddyfile"
    return text


def test_example_config_keeps_physicsai_off_with_default_port() -> None:
    example = json.loads(EXAMPLE.read_text(encoding="utf-8-sig"))
    assert example["physicsaiEnabled"] is False
    assert example["physicsaiPort"] == 8100
    assert example["physicsaiFrontendRoot"] == ""


def test_installer_adds_physicsai_routes_only_when_enabled() -> None:
    installer = INSTALLER.read_text(encoding="utf-8-sig")
    assert "Get-ConfigValue $cfg 'physicsaiEnabled' 'false'" in installer
    assert "Get-ConfigValue $cfg 'physicsaiPort' 8100" in installer
    assert "'SIMDASH_PHYSICSAI_ENABLED'" in installer

    off = _render_installer_caddyfile(enabled=False)
    assert "physicsai" not in off

    on = _render_installer_caddyfile(enabled=True, port=8123)
    assert "handle /physicsai/api/* {\n        reverse_proxy 127.0.0.1:8123\n    }" in on
    assert 'root * "C:/PhysicsAI/frontend/dist"' in on and "uri strip_prefix /physicsai" in on
    assert "redir /physicsai /physicsai/ 308" in on
    # Same site block, and the dashboard backend matcher never claims /physicsai/api.
    assert on.rstrip().endswith("}") and on.index("handle /physicsai/") > on.index("handle_path /home/*")
    assert "@backend path /api /api/* /assets/* /health" in on


def test_template_imports_optional_physicsai_snippet_before_catch_all() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    lines = [line.strip() for line in template.splitlines()]
    assert "import physicsai*.caddy" in lines, "glob import keeps the route off when no snippet file exists"
    assert lines.index("handle @api {") < lines.index("import physicsai*.caddy") < lines.index("handle {")
    assert "@api path /api /api/*" in lines


def _caddy() -> str | None:
    candidate = os.getenv("CADDY_BIN") or shutil.which("caddy")
    return candidate if candidate and Path(candidate).is_file() else None


@pytest.mark.skipif(_caddy() is None, reason="Caddy binary not available (set CADDY_BIN to run)")
def test_rendered_caddyfiles_validate_with_caddy(tmp_path: Path) -> None:
    caddy = _caddy()
    assert caddy
    for enabled in (False, True):
        config = tmp_path / f"installer-{enabled}.Caddyfile"
        config.write_text(_render_installer_caddyfile(enabled=enabled, static_root=tmp_path.as_posix(), physicsai_root=tmp_path.as_posix()), encoding="utf-8")
        result = subprocess.run([caddy, "adapt", "--config", str(config), "--adapter", "caddyfile"], capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert ("/physicsai/api/*" in result.stdout) is enabled

    template = TEMPLATE.read_text(encoding="utf-8")
    for token, value in {"__REPLACE_SERVER_NAME__": "localhost:18443", "__REPLACE_TLS_CERTIFICATE__": "c.pem", "__REPLACE_TLS_CERTIFICATE_KEY__": "k.pem",
                         "__REPLACE_RELEASE_ROOT__": tmp_path.as_posix(), "__REPLACE_API_PORT__": "18080"}.items():
        template = template.replace(token, value)
    site = tmp_path / "site"
    site.mkdir()
    (site / "Caddyfile").write_text(template, encoding="utf-8")
    off = subprocess.run([caddy, "adapt", "--config", str(site / "Caddyfile"), "--adapter", "caddyfile"], capture_output=True, text=True, check=False)
    assert off.returncode == 0, off.stderr
    assert "physicsai" not in off.stdout
    (site / "physicsai.caddy").write_text(
        "redir /physicsai /physicsai/ 308\n"
        "handle /physicsai/api/* {\n\treverse_proxy 127.0.0.1:{$PHYSICSAI_PORT:8100}\n}\n"
        "handle /physicsai/* {\n\troot * " + tmp_path.as_posix() + "\n\turi strip_prefix /physicsai\n\ttry_files {path} /index.html\n\tfile_server\n}\n",
        encoding="utf-8",
    )
    on = subprocess.run([caddy, "adapt", "--config", str(site / "Caddyfile"), "--adapter", "caddyfile"], capture_output=True, text=True, check=False)
    assert on.returncode == 0, on.stderr
    assert "/physicsai/api/*" in on.stdout and "127.0.0.1:8100" in on.stdout
