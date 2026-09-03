#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash -n "${root}/healthcheck.sh"
bash -n "${root}/validate-templates.sh"
bash -n "${root}/build-release.sh"
bash -n "${root}/install.sh"
bash -n "${root}/bootstrap-python-runtime.sh"

for template in \
  "${root}/systemd/simdashboard.service.template" \
  "${root}/nginx/simdashboard.conf.template"; do
  grep -q '__REPLACE_' "${template}"
done

nginx_template="${root}/nginx/simdashboard.conf.template"
assets_line="$(grep -n '^    location /assets/' "${nginx_template}" | cut -d: -f1)"
fallback_line="$(grep -n '^    location @backend_assets' "${nginx_template}" | cut -d: -f1)"
try_files_line="$(grep -n 'try_files \$uri @backend_assets;' "${nginx_template}" | cut -d: -f1)"
if [[ -z "${assets_line}" || -z "${fallback_line}" || -z "${try_files_line}" \
  || "${try_files_line}" -le "${assets_line}" || "${fallback_line}" -le "${try_files_line}" ]]; then
  printf '%s\n' 'nginx /assets/ must serve release files before the backend fallback.' >&2
  exit 1
fi

if grep --line-number --extended-regexp '\b(systemctl|dnf|yum|useradd|install)\b' "${root}/healthcheck.sh"; then
  printf '%s\n' 'Deployment template scripts must not execute privileged deployment actions.' >&2
  exit 1
fi

for required_installer_contract in \
  'rocky8_version_supported()' \
  'Rocky Linux 8.6 or later (8.x) is required' \
  'VERSION_ID:-' \
  'PYTHON_BIN must be an absolute path when configured.' \
  '/opt/simdashboard/runtime/python/bin/python3.12' \
  'python_bin_configured' \
  'runtime_packages+=(python3.12 python3.12-pip)' \
  'PYTHON_BIN}" -m venv' \
  'PostgreSQL 18.x is required' \
  'DEPLOYMENT_PROFILE=rocky8' \
  'scripts/check_postgres_connection.py' \
  'scripts/check_postgres_pool_budget.py' \
  'scripts/check_media_storage_preflight.py' \
  'httpd_can_network_connect' \
  'SIMDASH_IMPORT_ROOT must be outside INSTALL_ROOT' \
  'SIMDASH_IMPORT_ROOT cannot contain whitespace' \
  'SIMDASH_IMPORT_ROOT is not readable/traversable by service user' \
  'Non-default SIMDASH_IMPORT_ROOT is missing' \
  'Non-default SIMDASH_IMPORT_ROOT must be a mounted non-symlink directory' \
  'SIMDASH_IMPORT_ROOT="${SIMDASH_IMPORT_ROOT}"' \
  'SIMDASH_IMPORT_READINESS_POLICY="${SIMDASH_IMPORT_READINESS_POLICY}"' \
  'SIMDASH_IMPORT_SNAPSHOT_ROOT="${SIMDASH_IMPORT_SNAPSHOT_ROOT}"' \
  'SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES="${SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES}"' \
  'SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES="${SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES}"' \
  'SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS="${SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS}"' \
  'SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT="${SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT}"' \
  'Rocky production requires SIMDASH_IMPORT_READINESS_POLICY=required.' \
  'SIMDASH_IMPORT_SNAPSHOT_ROOT cannot contain whitespace' \
  'SIMDASH_IMPORT_SNAPSHOT_ROOT must be outside INSTALL_ROOT' \
  'SIMDASH_IMPORT_SNAPSHOT_ROOT must be inside RUNTIME_DIRECTORY' \
  'SIMDASH_IMPORT_SNAPSHOT_ROOT must not overlap SIMDASH_IMPORT_ROOT.' \
  'SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES must be 1073741824-17179869184 bytes.' \
  'SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES must be 0-17179869184 bytes.' \
  'SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS must be 60-7776000 seconds.' \
  'Rocky production requires SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1.' \
  'SIMDASH_IMPORT_READINESS_POLICY' \
  'SHA256SUMS'; do
  grep -Fq "${required_installer_contract}" "${root}/install.sh" || {
    printf 'Rocky 8 installer contract is missing: %s\n' "${required_installer_contract}" >&2
    exit 1
  }
done

grep -Fq 'wheelhouse' "${root}/build-release.sh"
grep -Fq 'AUTH_COOKIE_SECURE=true' "${root}/install.env.example"
grep -Fq 'PYTHON_BIN=' "${root}/install.env.example"
grep -Fq 'UV_VERSION=0.11.8' "${root}/bootstrap-python-runtime.sh"
grep -Fq 'uv_asset="uv-${uv_target}.tar.gz"' "${root}/bootstrap-python-runtime.sh"
grep -Fq 'UV_PYTHON_INSTALL_DIR="${RUNTIME_ROOT}"' "${root}/bootstrap-python-runtime.sh"
grep -Fq 'SIMDASH_IMPORT_ROOT=/var/lib/simdashboard/import' "${root}/install.env.example"
grep -Fq 'SIMDASH_IMPORT_READINESS_POLICY=required' "${root}/install.env.example"
grep -Fq 'SIMDASH_MEDIA_STORAGE_MODE=database-only' "${root}/install.env.example"
grep -Fq 'SIMDASH_IMPORT_SNAPSHOT_ROOT=/var/lib/simdashboard/snapshots' "${root}/install.env.example"
grep -Fq 'SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES=1073741824' "${root}/install.env.example"
grep -Fq 'SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES=536870912' "${root}/install.env.example"
grep -Fq 'SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS=86400' "${root}/install.env.example"
grep -Fq 'SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1' "${root}/install.env.example"
grep -Fq 'RequiresMountsFor=__REPLACE_IMPORT_ROOT__' "${root}/systemd/simdashboard.service.template"
grep -Fq 'ReadOnlyPaths=__REPLACE_IMPORT_ROOT__' "${root}/systemd/simdashboard.service.template"
grep -Fq 'check_media_storage_preflight.py' "${root}/systemd/simdashboard.service.template"
grep -Fq 'TimeoutStartSec=300' "${root}/systemd/simdashboard.service.template"
printf '%s\n' 'ROCKY8_DEPLOY_TEMPLATES_OK'
