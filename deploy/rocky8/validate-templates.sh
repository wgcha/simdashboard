#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash -n "${root}/healthcheck.sh"
bash -n "${root}/validate-templates.sh"
bash -n "${root}/build-release.sh"
bash -n "${root}/install.sh"

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
  'PostgreSQL 18.x is required' \
  'DEPLOYMENT_PROFILE=rocky8' \
  'scripts/check_postgres_connection.py' \
  'scripts/check_postgres_pool_budget.py' \
  'httpd_can_network_connect' \
  'SHA256SUMS'; do
  grep -Fq "${required_installer_contract}" "${root}/install.sh" || {
    printf 'Rocky 8 installer contract is missing: %s\n' "${required_installer_contract}" >&2
    exit 1
  }
done

grep -Fq 'wheelhouse' "${root}/build-release.sh"
grep -Fq 'AUTH_COOKIE_SECURE=true' "${root}/install.env.example"
printf '%s\n' 'ROCKY8_DEPLOY_TEMPLATES_OK'
