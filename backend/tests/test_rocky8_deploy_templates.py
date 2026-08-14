from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.unit


def test_rocky8_templates_are_non_privileged_and_parseable() -> None:
    root = Path(__file__).resolve().parents[2]
    deploy = root / "deploy" / "rocky8"
    result = subprocess.run(
        ["bash", str(deploy / "validate-templates.sh")],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "ROCKY8_DEPLOY_TEMPLATES_OK" in result.stdout
    assert "ANALYSIS_DB_BACKEND=postgresql" in (deploy / "systemd" / "simdashboard.service.template").read_text(encoding="utf-8")
    nginx = (deploy / "nginx" / "simdashboard.conf.template").read_text(encoding="utf-8")
    assert "location /api/" in nginx
    assert "location /assets/" in nginx
    assert "try_files $uri @backend_assets;" in nginx
    assert nginx.index("try_files $uri @backend_assets;") < nginx.index("location @backend_assets")
