#!/usr/bin/env bash
# CI-only verification in a disposable Rocky 8.6 container with --network none.
# This installs RPMs in that container; do not use it as the production installer.
set -Eeuo pipefail
umask 077
[[ "${EUID}" == 0 ]] || { printf '%s\n' 'Run in the root-owned disposable CI container.' >&2; exit 1; }
[[ $# == 1 ]] || { printf '%s\n' 'Usage: verify-offline-bundle.sh ARCHIVE' >&2; exit 1; }
archive="$(readlink -f "$1")"
source /etc/os-release
[[ "${ID:-}:${VERSION_ID:-}:$(uname -m)" == rocky:8.6:x86_64 ]] || exit 1
(cd "$(dirname "$archive")" && sha256sum -c "${archive}.sha256")
verify_root="$(mktemp -d /tmp/simdashboard-offline-verify.XXXXXX)"
trap 'rm -rf -- "${verify_root}"' EXIT
tar -xzf "$archive" -C "$verify_root"
# The minimal official image has no findutils until the local RPM transaction.
shopt -s nullglob
bundles=("${verify_root}"/*/)
[[ "${#bundles[@]}" == 1 ]] || exit 1
bundle="${bundles[0]%/}"
(cd "$bundle" && sha256sum --check --quiet SHA256SUMS)

# This is a genuinely fresh OS instance: the builder's installed RPMs cannot
# accidentally satisfy omitted dependencies. All network interfaces are disabled
# by the workflow's docker invocation.
dnf -y --disablerepo='*' \
  --repofrompath="simdashboard-offline,file://${bundle}/rpm-repo" \
  --setopt=simdashboard-offline.gpgcheck=1 \
  --setopt="simdashboard-offline.gpgkey=file://${bundle}/rpm-repo/RPM-GPG-KEY-Rocky-8" \
  --setopt=simdashboard-offline.module_hotfixes=1 \
  --setopt=install_weak_deps=False \
  install nginx curl ca-certificates tar findutils shadow-utils policycoreutils-python-utils firewalld

# Fixture certificates are only used for install.sh --check's readable-path
# checks, never to start a TLS service. No real credentials are placed in artifacts.
printf '%s\n' 'preflight fixture, not a TLS certificate' >"${verify_root}/cert.pem"
printf '%s\n' 'preflight fixture, not a TLS key' >"${verify_root}/key.pem"
config="${verify_root}/install.env"
printf '%s\n' \
  'SERVER_NAME=offline-check.example.invalid' \
  'CORS_ALLOWED_ORIGINS=https://offline-check.example.invalid' \
  "TLS_CERTIFICATE=${verify_root}/cert.pem" \
  "TLS_CERTIFICATE_KEY=${verify_root}/key.pem" \
  'DATABASE_URL=postgresql://offline:fixture@127.0.0.1/offline' \
  'MIGRATE_DATABASE=0' \
  'AUTH_SECRET_KEY=offline-fixture-secret-at-least-32-characters' \
  >"${config}"
chmod 0600 "$config"
[[ ! -e /opt/simdashboard/runtimes ]] || exit 1
bash "${bundle}/install-offline.sh" --config "$config" --check
# Prove development mode does not merely accept a placeholder certificate:
# the configured paths are now absent, while default HTTPS remains checked above.
printf '\nTLS_CERTIFICATE=/missing/dev-cert.pem\nTLS_CERTIFICATE_KEY=/missing/dev-key.key\n' >>"${config}"
bash "${bundle}/install-offline.sh" --config "$config" --dev-http --check
[[ ! -e /opt/simdashboard/runtimes ]] || {
  printf '%s\n' '--check unexpectedly installed a Python runtime.' >&2
  exit 1
}
printf '%s\n' 'OFFLINE_ROCKY86_DEPENDENCIES_AND_PREFLIGHT_OK'
printf '%s\n' 'No external network was available. Real PostgreSQL, TLS, systemd and site mounts still require site validation.'
