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
    assert "scripts/check_media_storage_preflight.py" in installer
    assert "POSTGRES_OWNER_URL" not in environment_block
    assert "POSTGRES_ADMIN_URL" not in environment_block
    assert "SIM_DASH_OWNER_PASSWORD" not in environment_block
    assert "SIMDASH_IMPORT_READINESS_POLICY" in environment_block
    assert "SIMDASH_MEDIA_STORAGE_MODE" in environment_block
    assert 'SIMDASH_IMPORT_READINESS_POLICY="${SIMDASH_IMPORT_READINESS_POLICY}"' in installer
    assert 'SIMDASH_MEDIA_STORAGE_MODE="${SIMDASH_MEDIA_STORAGE_MODE}"' in installer
    assert '[[ "${SIMDASH_IMPORT_READINESS_POLICY}" == required ]]' in installer
    assert "SIMDASH_IMPORT_READINESS_POLICY=required" in (deploy / "install.env.example").read_text(encoding="utf-8")
    assert "SIMDASH_MEDIA_STORAGE_MODE=database-only" in (deploy / "install.env.example").read_text(encoding="utf-8")
    for name in (
        "SIMDASH_IMPORT_SNAPSHOT_ROOT",
        "SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES",
        "SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES",
        "SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS",
        "SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT",
    ):
        assert name in environment_block
        assert f'{name}="${{{name}}}"' in installer

    install_example = (deploy / "install.env.example").read_text(encoding="utf-8")
    assert "SIMDASH_IMPORT_SNAPSHOT_ROOT=/var/lib/simdashboard/snapshots" in install_example
    assert "SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES=1073741824" in install_example
    assert "SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES=536870912" in install_example
    assert "SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS=86400" in install_example
    assert "SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1" in install_example
    assert '[[ "${SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT}" == 1 ]]' in installer
    assert "Rocky production requires SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1." in installer
    assert "SIMDASH_IMPORT_SNAPSHOT_ROOT must be outside INSTALL_ROOT" in installer
    assert "SIMDASH_IMPORT_SNAPSHOT_ROOT must be inside RUNTIME_DIRECTORY" in installer
    assert "SIMDASH_IMPORT_SNAPSHOT_ROOT must not overlap SIMDASH_IMPORT_ROOT." in installer
    assert 'install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0700 "${SIMDASH_IMPORT_SNAPSHOT_ROOT}"' in installer
    assert '(( SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES >= 1073741824 ))' in installer
    assert '(( SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES <= 17179869184 ))' in installer
    assert '(( SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES <= 17179869184 ))' in installer
    assert '(( SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS >= 60 ))' in installer
    assert '(( SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS <= 7776000 ))' in installer
    assert "SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES must be 1073741824-17179869184 bytes." in installer
    assert "SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES must be 0-17179869184 bytes." in installer
    assert "SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS must be 60-7776000 seconds." in installer
    service = (deploy / "systemd" / "simdashboard.service.template").read_text(encoding="utf-8")
    assert "check_media_storage_preflight.py" in service
    assert "TimeoutStartSec=300" in service


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


def test_postgres_restore_wrappers_forward_explicit_app_role_verify_url() -> None:
    root = Path(__file__).resolve().parents[2]
    shell = (root / "scripts" / "postgres" / "restore-postgres.sh").read_text(encoding="utf-8")
    powershell = (root / "scripts" / "postgres" / "restore-postgres.ps1").read_text(encoding="utf-8")
    assert "--verify-database-url" in shell
    assert "SIMDASH_APP_DATABASE_URL" in shell
    assert "--verify-database-url" in powershell
    assert "$AppRoleVerifyUrl" in powershell
