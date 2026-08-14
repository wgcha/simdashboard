#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash -n "${root}/healthcheck.sh"
bash -n "${root}/validate-templates.sh"

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
printf '%s\n' 'ROCKY8_DEPLOY_TEMPLATES_OK'
