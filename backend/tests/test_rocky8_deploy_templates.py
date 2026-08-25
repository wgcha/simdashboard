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


def test_rocky8_installer_is_fail_closed_and_keeps_owner_secret_out_of_service_env() -> None:
    root = Path(__file__).resolve().parents[2]
    deploy = root / "deploy" / "rocky8"
    installer = (deploy / "install.sh").read_text(encoding="utf-8")
    environment_block = installer.split("environment_names=(", 1)[1].split(")", 1)[0]

    assert "Rocky Linux 8.10 is required" in installer
    assert "PostgreSQL 18.x is required" in installer
    assert "scripts/check_postgres_connection.py" in installer
    assert "scripts/check_postgres_pool_budget.py" in installer
    assert "POSTGRES_OWNER_URL" not in environment_block
    assert "POSTGRES_ADMIN_URL" not in environment_block
    assert "SIM_DASH_OWNER_PASSWORD" not in environment_block
    assert "SIMDASH_IMPORT_READINESS_POLICY" in environment_block
    assert 'SIMDASH_IMPORT_READINESS_POLICY="${SIMDASH_IMPORT_READINESS_POLICY}"' in installer
    assert '[[ "${SIMDASH_IMPORT_READINESS_POLICY}" == required ]]' in installer
    assert "SIMDASH_IMPORT_READINESS_POLICY=required" in (deploy / "install.env.example").read_text(encoding="utf-8")


def test_rocky8_bundle_builder_packages_built_frontend_and_checksums() -> None:
    root = Path(__file__).resolve().parents[2]
    builder = (root / "deploy" / "rocky8" / "build-release.sh").read_text(encoding="utf-8")

    assert "frontend/dist/index.html" in builder
    assert '"${project_root}/examples"' in builder
    assert "requirements.lock" in builder
    assert "SHA256SUMS" in builder
    assert '"${output}.sha256"' in builder
    assert "--with-wheels" in builder
    assert '== imports' in builder
