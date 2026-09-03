#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_file=""
check_only=0

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh --config /root/simdashboard-install.env [--check]

Installs an immutable Analysis Canvas release on Rocky Linux 8.6 or later (8.x). The config is
a trusted root-owned shell file based on install.env.example.

Options:
  --config FILE   Required installation configuration
  --check         Validate OS, bundle, config, certificates, and source only
  -h, --help      Show this help
EOF
}

die() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[simdashboard] %s\n' "$*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config_file="${2:-}"; shift 2 ;;
    --check) check_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || die 'Run this installer as root (sudo).'
[[ -n "${config_file}" ]] || die '--config is required.'
[[ -f "${config_file}" ]] || die "Config file not found: ${config_file}"
config_file="$(readlink -f "${config_file}")"
[[ "$(stat -c '%u' "${config_file}")" == 0 ]] || die 'The config file must be owned by root.'
config_mode="$(stat -c '%a' "${config_file}")"
(( (8#${config_mode} & 077) == 0 )) || die 'The config file must not be accessible by group or others (use chmod 0600).'

INSTALL_ROOT=/opt/simdashboard
RUNTIME_DIRECTORY=/var/lib/simdashboard
# Empty means the local default, derived from RUNTIME_DIRECTORY after the config
# has been read. Keep an explicit value in install.env.example for operators.
SIMDASH_IMPORT_ROOT=
SIMDASH_IMPORT_READINESS_POLICY=required
SIMDASH_MEDIA_STORAGE_MODE=database-only
SIMDASH_IMPORT_SNAPSHOT_ROOT=/var/lib/simdashboard/snapshots
SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES=1073741824
SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES=536870912
SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS=86400
SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1
ENVIRONMENT_FILE=/etc/simdashboard/simdashboard.env
SERVICE_USER=simdashboard
SERVICE_GROUP=simdashboard
API_PORT=8000
UVICORN_WORKERS=2
POSTGRES_MAX_CONNECTIONS=100
POSTGRES_RESERVED_CONNECTIONS=10
POSTGRES_REQUEST_POOL_SIZE=5
POSTGRES_REQUEST_MAX_OVERFLOW=10
POSTGRES_REQUEST_POOL_TIMEOUT_SECONDS=10
POSTGRES_MEDIA_POOL_SIZE=10
POSTGRES_MEDIA_MAX_OVERFLOW=5
POSTGRES_MEDIA_POOL_TIMEOUT_SECONDS=10
POSTGRES_POOL_RECYCLE_SECONDS=1800
BOOTSTRAP_DATABASE=0
SIM_DASH_DATABASE=simulation_dashboard
SIM_DASH_OWNER_ROLE=simdashboard_owner
SIM_DASH_APP_ROLE=simdashboard_app
SIM_DASH_PRESERVE_EXISTING_ROLES=1
MIGRATE_DATABASE=1
SEED_MODE=empty
AUTH_MODE=password
AUTH_TOKEN_TTL_MINUTES=480
AUTH_COOKIE_SECURE=true
DIRECTORY_MODE=local
REQUIRE_EXACT_PYTHON=1
INSTALL_OS_PACKAGES=1
CONFIGURE_SELINUX=1
CONFIGURE_FIREWALL=1
START_SERVICES=1
HEALTHCHECK_URL=
INITIAL_ADMIN_USERNAME=
INITIAL_ADMIN_DISPLAY_NAME='Initial administrator'
INSTALL_SOURCE_ROOT=
# Optional absolute path to a preinstalled Python 3.12.13 executable. Rocky
# 8.6 fixed AppStream repositories may not provide the python3.12 RPM; when
# set, the installer uses this runtime and does not request Python from dnf.
PYTHON_BIN=

# This is intentionally a trusted root-owned shell file: it may contain quoted
# URLs and secrets that cannot be parsed correctly as a simplistic KEY=VALUE file.
# shellcheck disable=SC1090
source "${config_file}"

INSTALL_ROOT="${INSTALL_ROOT%/}"
RUNTIME_DIRECTORY="${RUNTIME_DIRECTORY%/}"
SIMDASH_IMPORT_ROOT="${SIMDASH_IMPORT_ROOT:-${RUNTIME_DIRECTORY}/import}"
SIMDASH_IMPORT_ROOT="${SIMDASH_IMPORT_ROOT%/}"
SIMDASH_IMPORT_SNAPSHOT_ROOT="${SIMDASH_IMPORT_SNAPSHOT_ROOT%/}"

# A Rocky service must never silently fall back to legacy discovery.  The
# producer is responsible for creating the sibling staging directory, marker,
# and no-replace publication; the app service has read-only access only.
[[ "${SIMDASH_IMPORT_READINESS_POLICY}" == required ]] || \
  die 'Rocky production requires SIMDASH_IMPORT_READINESS_POLICY=required.'
[[ "${SIMDASH_MEDIA_STORAGE_MODE}" == dual-read || "${SIMDASH_MEDIA_STORAGE_MODE}" == database-only ]] || \
  die 'SIMDASH_MEDIA_STORAGE_MODE must be dual-read or database-only.'

for boolean_name in BOOTSTRAP_DATABASE SIM_DASH_PRESERVE_EXISTING_ROLES MIGRATE_DATABASE \
  REQUIRE_EXACT_PYTHON INSTALL_OS_PACKAGES CONFIGURE_SELINUX CONFIGURE_FIREWALL START_SERVICES; do
  boolean_value="${!boolean_name}"
  [[ "${boolean_value}" == 0 || "${boolean_value}" == 1 ]] || die "${boolean_name} must be 0 or 1."
done

rocky8_version_supported() {
  local version_id="${1:-}"
  local major minor normalized_minor

  # VERSION_ID is metadata from /etc/os-release, not shell input. Still parse
  # it as a strict dotted decimal version before making any numeric decision.
  [[ "${version_id}" =~ ^([0-9]+)\.([0-9]+)(\.[0-9]+)?$ ]] || return 1
  major="${BASH_REMATCH[1]}"
  minor="${BASH_REMATCH[2]}"
  [[ "${major}" == 8 ]] || return 1

  # Strip leading zeroes without arithmetic expansion. This keeps the check
  # safe for malformed/very large metadata values while accepting 8.06 too.
  normalized_minor="${minor#${minor%%[!0]*}}"
  normalized_minor="${normalized_minor:-0}"
  [[ "${#normalized_minor}" -gt 1 || "${normalized_minor}" =~ ^[6-9]$ ]]
}

[[ -r /etc/os-release ]] || die '/etc/os-release is missing.'
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != rocky ]] || ! rocky8_version_supported "${VERSION_ID:-}"; then
  die "Rocky Linux 8.6 or later (8.x) is required; detected ${PRETTY_NAME:-unknown} (VERSION_ID=${VERSION_ID:-unknown})."
fi

python_bin_configured=0
if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin_configured=1
  [[ "${PYTHON_BIN}" == /* ]] || die 'PYTHON_BIN must be an absolute path when configured.'
  [[ -f "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] || die "PYTHON_BIN is not an executable file: ${PYTHON_BIN}"
elif [[ -f /opt/simdashboard/runtime/python/bin/python3.12 && -x /opt/simdashboard/runtime/python/bin/python3.12 ]]; then
  # bootstrap-python-runtime.sh prepares this stable path on Rocky 8.6 hosts
  # whose fixed AppStream does not carry python3.12 RPMs. Treat it like an
  # operator-provided runtime and do not ask dnf to install Python packages.
  PYTHON_BIN=/opt/simdashboard/runtime/python/bin/python3.12
  python_bin_configured=1
else
  # Keep the existing package-managed default. The concrete path is resolved
  # after dnf so every later check invokes the same executable.
  PYTHON_BIN=python3.12
fi

safe_name='^[A-Za-z_][A-Za-z0-9_-]*$'
[[ "${SERVICE_USER}" =~ ${safe_name} ]] || die 'SERVICE_USER contains unsupported characters.'
[[ "${SERVICE_GROUP}" =~ ${safe_name} ]] || die 'SERVICE_GROUP contains unsupported characters.'
[[ "${SIM_DASH_DATABASE}" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]] || die 'SIM_DASH_DATABASE contains unsupported characters.'
[[ "${SIM_DASH_OWNER_ROLE}" =~ ${safe_name} ]] || die 'SIM_DASH_OWNER_ROLE contains unsupported characters.'
[[ "${SIM_DASH_APP_ROLE}" =~ ${safe_name} ]] || die 'SIM_DASH_APP_ROLE contains unsupported characters.'
[[ "${SERVER_NAME:-}" =~ ^[A-Za-z0-9.-]+$ ]] || die 'SERVER_NAME must be a DNS host name.'

for path_name in INSTALL_ROOT RUNTIME_DIRECTORY SIMDASH_IMPORT_ROOT SIMDASH_IMPORT_SNAPSHOT_ROOT ENVIRONMENT_FILE TLS_CERTIFICATE TLS_CERTIFICATE_KEY; do
  path_value="${!path_name:-}"
  [[ "${path_value}" == /* && "${path_value}" != / ]] || die "${path_name} must be an absolute, non-root path."
  [[ "${path_value}" != *$'\n'* && "${path_value}" != *$'\r'* ]] || die "${path_name} contains a newline."
done
[[ "${SIMDASH_IMPORT_ROOT}" != "${INSTALL_ROOT}" && "${SIMDASH_IMPORT_ROOT}" != "${INSTALL_ROOT}/"* ]] || \
  die 'SIMDASH_IMPORT_ROOT must be outside INSTALL_ROOT so imports survive immutable release changes.'
[[ "${SIMDASH_IMPORT_ROOT}" != *[[:space:]]* ]] || \
  die 'SIMDASH_IMPORT_ROOT cannot contain whitespace because it is rendered into systemd paths.'
[[ "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" != *[[:space:]]* ]] || \
  die 'SIMDASH_IMPORT_SNAPSHOT_ROOT cannot contain whitespace because it is rendered into the service environment.'
[[ "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" != "${INSTALL_ROOT}" && "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" != "${INSTALL_ROOT}/"* ]] || \
  die 'SIMDASH_IMPORT_SNAPSHOT_ROOT must be outside INSTALL_ROOT so private workspaces never enter immutable releases.'
[[ "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" == "${RUNTIME_DIRECTORY}/"* ]] || \
  die 'SIMDASH_IMPORT_SNAPSHOT_ROOT must be inside RUNTIME_DIRECTORY so systemd grants it writable runtime access.'
[[ "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" != "${SIMDASH_IMPORT_ROOT}" && \
  "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" != "${SIMDASH_IMPORT_ROOT}/"* && \
  "${SIMDASH_IMPORT_ROOT}" != "${SIMDASH_IMPORT_SNAPSHOT_ROOT}/"* ]] || \
  die 'SIMDASH_IMPORT_SNAPSHOT_ROOT must not overlap SIMDASH_IMPORT_ROOT.'
[[ -r "${TLS_CERTIFICATE}" ]] || die "TLS certificate is not readable: ${TLS_CERTIFICATE}"
[[ -r "${TLS_CERTIFICATE_KEY}" ]] || die "TLS private key is not readable: ${TLS_CERTIFICATE_KEY}"

verify_import_root_access() {
  local inaccessible_path
  if ! runuser -u "${SERVICE_USER}" -- test -r "${SIMDASH_IMPORT_ROOT}" -a -x "${SIMDASH_IMPORT_ROOT}"; then
    die "SIMDASH_IMPORT_ROOT is not readable/traversable by service user ${SERVICE_USER}: ${SIMDASH_IMPORT_ROOT}"
  fi
  inaccessible_path="$(
    runuser -u "${SERVICE_USER}" -- find "${SIMDASH_IMPORT_ROOT}" \
      \( -type d \( ! -readable -o ! -executable \) -o -type f ! -readable \) -print -quit
  )" || die "SIMDASH_IMPORT_ROOT could not be traversed by service user ${SERVICE_USER}: ${SIMDASH_IMPORT_ROOT}"
  [[ -z "${inaccessible_path}" ]] || \
    die "SIMDASH_IMPORT_ROOT contains a path unreadable by service user ${SERVICE_USER}: ${inaccessible_path}"
}

[[ "${API_PORT}" =~ ^[0-9]+$ ]] && (( API_PORT >= 1024 && API_PORT <= 65535 )) || die 'API_PORT must be 1024-65535.'
[[ "${UVICORN_WORKERS}" =~ ^[1-9][0-9]*$ ]] || die 'UVICORN_WORKERS must be a positive integer.'
[[ "${SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES}" =~ ^[1-9][0-9]*$ ]] && \
  (( SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES >= 1073741824 )) && \
  (( SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES <= 17179869184 )) || \
  die 'SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES must be 1073741824-17179869184 bytes.'
[[ "${SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES}" =~ ^[0-9]+$ ]] && \
  (( SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES <= 17179869184 )) || \
  die 'SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES must be 0-17179869184 bytes.'
[[ "${SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS}" =~ ^[1-9][0-9]*$ ]] && \
  (( SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS >= 60 )) && \
  (( SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS <= 7776000 )) || \
  die 'SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS must be 60-7776000 seconds.'
[[ "${SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT}" =~ ^[1-9][0-9]*$ ]] || \
  die 'SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT must be a positive integer.'
[[ "${SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT}" == 1 ]] || \
  die 'Rocky production requires SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT=1.'
for number_name in POSTGRES_MAX_CONNECTIONS POSTGRES_RESERVED_CONNECTIONS POSTGRES_REQUEST_POOL_SIZE \
  POSTGRES_REQUEST_MAX_OVERFLOW POSTGRES_REQUEST_POOL_TIMEOUT_SECONDS POSTGRES_MEDIA_POOL_SIZE \
  POSTGRES_MEDIA_MAX_OVERFLOW POSTGRES_MEDIA_POOL_TIMEOUT_SECONDS POSTGRES_POOL_RECYCLE_SECONDS; do
  [[ "${!number_name}" =~ ^[0-9]+$ ]] || die "${number_name} must be a non-negative integer."
done

[[ "${DATABASE_URL:-}" == postgresql://* || "${DATABASE_URL:-}" == postgresql+psycopg://* ]] || die 'DATABASE_URL must be a PostgreSQL URL for the application role.'
[[ "${DATABASE_URL}" != *CHANGE_ME* ]] || die 'DATABASE_URL still contains CHANGE_ME.'
if [[ "${MIGRATE_DATABASE}" == 1 ]]; then
  [[ "${POSTGRES_OWNER_URL:-}" == postgresql://* || "${POSTGRES_OWNER_URL:-}" == postgresql+psycopg://* ]] || die 'POSTGRES_OWNER_URL is required when MIGRATE_DATABASE=1.'
  [[ "${POSTGRES_OWNER_URL}" != *CHANGE_ME* ]] || die 'POSTGRES_OWNER_URL still contains CHANGE_ME.'
fi
if [[ "${BOOTSTRAP_DATABASE}" == 1 ]]; then
  [[ "${POSTGRES_ADMIN_URL:-}" == postgresql://* || "${POSTGRES_ADMIN_URL:-}" == postgresql+psycopg://* ]] || die 'POSTGRES_ADMIN_URL is required when BOOTSTRAP_DATABASE=1.'
  [[ "${POSTGRES_ADMIN_URL}" != *CHANGE_ME* ]] || die 'POSTGRES_ADMIN_URL still contains CHANGE_ME.'
  [[ -n "${SIM_DASH_OWNER_PASSWORD:-}" && "${SIM_DASH_OWNER_PASSWORD}" != *CHANGE_ME* ]] || die 'SIM_DASH_OWNER_PASSWORD is required for database bootstrap.'
  [[ -n "${SIM_DASH_APP_PASSWORD:-}" && "${SIM_DASH_APP_PASSWORD}" != *CHANGE_ME* ]] || die 'SIM_DASH_APP_PASSWORD is required for database bootstrap.'
fi
[[ "${SEED_MODE}" == empty || "${SEED_MODE}" == reference ]] || die 'SEED_MODE must be empty or reference.'
[[ "${AUTH_MODE}" == password || "${AUTH_MODE}" == oidc ]] || die 'Rocky production requires AUTH_MODE=password or oidc.'
[[ "${AUTH_COOKIE_SECURE}" == true ]] || die 'Rocky production requires AUTH_COOKIE_SECURE=true.'
[[ -n "${AUTH_SECRET_KEY:-}" && ${#AUTH_SECRET_KEY} -ge 32 && "${AUTH_SECRET_KEY}" != *CHANGE_ME* ]] || die 'AUTH_SECRET_KEY must be a non-placeholder value of at least 32 characters.'
CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-https://${SERVER_NAME}}"

if [[ "${AUTH_MODE}" == oidc ]]; then
  for oidc_name in OIDC_ISSUER_URL OIDC_CLIENT_ID OIDC_CLIENT_SECRET OIDC_REDIRECT_URI; do
    [[ -n "${!oidc_name:-}" && "${!oidc_name}" != *CHANGE_ME* ]] || die "${oidc_name} is required for AUTH_MODE=oidc."
  done
fi
[[ "${DIRECTORY_MODE}" == local || "${DIRECTORY_MODE}" == http ]] || die 'DIRECTORY_MODE must be local or http.'
if [[ "${DIRECTORY_MODE}" == http ]]; then
  [[ "${DIRECTORY_API_BASE_URL:-}" == https://* ]] || die 'DIRECTORY_API_BASE_URL must be HTTPS for DIRECTORY_MODE=http.'
  [[ -n "${DIRECTORY_API_TOKEN:-}" && "${DIRECTORY_API_TOKEN}" != *CHANGE_ME* ]] || die 'DIRECTORY_API_TOKEN is required for DIRECTORY_MODE=http.'
fi
if [[ -n "${INITIAL_ADMIN_USERNAME}" ]]; then
  [[ "${AUTH_MODE}" == password ]] || die 'INITIAL_ADMIN_USERNAME is only valid with AUTH_MODE=password.'
  [[ "${INITIAL_ADMIN_USERNAME}" =~ ^[a-z0-9._-]+$ ]] || die 'INITIAL_ADMIN_USERNAME has invalid characters.'
  [[ -n "${INITIAL_ADMIN_PASSWORD:-}" && ${#INITIAL_ADMIN_PASSWORD} -ge 12 && "${INITIAL_ADMIN_PASSWORD}" != *CHANGE_ME* ]] || die 'INITIAL_ADMIN_PASSWORD must be at least 12 characters.'
fi

if [[ -n "${INSTALL_SOURCE_ROOT}" ]]; then
  source_root="$(readlink -f "${INSTALL_SOURCE_ROOT}")"
elif [[ -d "${script_root}/payload/backend" ]]; then
  source_root="${script_root}/payload"
else
  source_root="$(cd "${script_root}/../.." && pwd)"
fi
[[ -d "${source_root}/backend/app" && -d "${source_root}/backend/migrations" ]] || die "Backend payload is incomplete: ${source_root}"
[[ -f "${source_root}/backend/requirements.lock" ]] || die 'backend/requirements.lock is missing.'
[[ -f "${source_root}/frontend/dist/index.html" ]] || die 'Built frontend payload is missing (frontend/dist/index.html).'
[[ -f "${source_root}/.python-version" ]] || die '.python-version is missing from the payload.'

if [[ -f "${script_root}/SHA256SUMS" ]]; then
  log 'Verifying release bundle checksums'
  (cd "${script_root}" && sha256sum --check --quiet SHA256SUMS) || die 'Release bundle checksum validation failed.'
fi

if [[ -f "${script_root}/RELEASE_ID" ]]; then
  release_id="$(tr -d '[:space:]' <"${script_root}/RELEASE_ID")"
else
  release_id="${RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
fi
[[ "${release_id}" =~ ^[A-Za-z0-9._-]+$ ]] || die 'RELEASE_ID contains unsupported characters.'
release_root="${INSTALL_ROOT}/releases/${release_id}"
[[ ! -e "${release_root}" ]] || die "Release already exists: ${release_root}"

if [[ "${check_only}" == 1 ]]; then
  # A fresh local default is created by a real installation. Any non-default
  # root represents an operator-managed path (typically a mount) and must
  # already be present so a missing share never looks like an empty import root.
  if [[ "${SIMDASH_IMPORT_ROOT}" != "${RUNTIME_DIRECTORY}/import" ]]; then
    [[ -d "${SIMDASH_IMPORT_ROOT}" && ! -L "${SIMDASH_IMPORT_ROOT}" ]] || \
      die "Non-default SIMDASH_IMPORT_ROOT must be a mounted non-symlink directory: ${SIMDASH_IMPORT_ROOT}"
    if getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
      command -v runuser >/dev/null 2>&1 || die 'Import-root preflight requires runuser.'
      verify_import_root_access
    else
      log "Service user ${SERVICE_USER} does not exist yet; import-root access will be checked during installation."
    fi
  fi
  log "Validation passed for release ${release_id}; no system changes were made."
  exit 0
fi

if [[ "${INSTALL_OS_PACKAGES}" == 1 ]]; then
  log 'Installing Rocky Linux runtime packages (PostgreSQL is not installed or modified)'
  runtime_packages=(nginx curl ca-certificates tar findutils shadow-utils policycoreutils-python-utils firewalld)
  if [[ "${python_bin_configured}" == 0 ]]; then
    runtime_packages+=(python3.12 python3.12-pip)
  fi
  dnf -y install "${runtime_packages[@]}"
fi
if [[ "${python_bin_configured}" == 0 ]]; then
  PYTHON_BIN="$(command -v python3.12 || true)"
  [[ -n "${PYTHON_BIN}" ]] || die 'python3.12 is required when PYTHON_BIN is not configured.'
fi
[[ -f "${PYTHON_BIN}" && -x "${PYTHON_BIN}" ]] || die "PYTHON_BIN is not an executable file: ${PYTHON_BIN}"
for command_name in nginx curl systemctl systemd-analyze useradd groupadd getent install find sed grep runuser; do
  command -v "${command_name}" >/dev/null 2>&1 || die "Required command not found: ${command_name}"
done
if [[ "${CONFIGURE_SELINUX}" == 1 ]]; then
  for command_name in semanage restorecon setsebool; do
    command -v "${command_name}" >/dev/null 2>&1 || die "SELinux configuration requires: ${command_name}"
  done
fi
if [[ "${CONFIGURE_FIREWALL}" == 1 ]]; then
  command -v firewall-cmd >/dev/null 2>&1 || die 'Firewall configuration requires firewall-cmd.'
fi

expected_python="$(tr -d '[:space:]' <"${source_root}/.python-version")"
actual_python="$("${PYTHON_BIN}" -c 'import platform; print(platform.python_version())')"
[[ "${actual_python}" == 3.12.* ]] || die "Python 3.12 is required; detected ${actual_python}."
if [[ "${REQUIRE_EXACT_PYTHON}" == 1 && "${actual_python}" != "${expected_python}" ]]; then
  die "Python patch mismatch: release=${expected_python}, server=${actual_python}. Install the pinned runtime or record an approved policy exception."
fi

if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd --system "${SERVICE_GROUP}"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --gid "${SERVICE_GROUP}" --home-dir "${INSTALL_ROOT}" --shell /sbin/nologin "${SERVICE_USER}"
fi
[[ "$(id -gn "${SERVICE_USER}")" == "${SERVICE_GROUP}" ]] || die "Existing service user ${SERVICE_USER} does not use group ${SERVICE_GROUP}."

install -d -o root -g root -m 0755 "${INSTALL_ROOT}" "${INSTALL_ROOT}/releases"
install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 "${RUNTIME_DIRECTORY}" "${RUNTIME_DIRECTORY}/report-templates"
# Private importer snapshots must remain under the service-writable runtime
# directory, separate from both immutable releases and the read-only import
# source. This is an application capacity/reserve gate, not a kernel quota.
if [[ -e "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" || -L "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" ]]; then
  [[ -d "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" && ! -L "${SIMDASH_IMPORT_SNAPSHOT_ROOT}" ]] || \
    die "SIMDASH_IMPORT_SNAPSHOT_ROOT must be a non-symlink directory: ${SIMDASH_IMPORT_SNAPSHOT_ROOT}"
fi
install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0700 "${SIMDASH_IMPORT_SNAPSHOT_ROOT}"
# The import source is intentionally separate from an immutable release.  Create
# the default/local root with no service-user write access; an existing approved
# NAS/SMB/NFS mount keeps its ownership and mode, but must already be readable by
# the service account.
if [[ ! -e "${SIMDASH_IMPORT_ROOT}" ]]; then
  if [[ "${SIMDASH_IMPORT_ROOT}" == "${RUNTIME_DIRECTORY}/import" ]]; then
    install -d -o root -g "${SERVICE_GROUP}" -m 0750 "${SIMDASH_IMPORT_ROOT}"
  else
    die "Non-default SIMDASH_IMPORT_ROOT is missing; mount or create it before installation: ${SIMDASH_IMPORT_ROOT}"
  fi
fi
[[ -d "${SIMDASH_IMPORT_ROOT}" && ! -L "${SIMDASH_IMPORT_ROOT}" ]] || \
  die "SIMDASH_IMPORT_ROOT must be an existing non-symlink directory: ${SIMDASH_IMPORT_ROOT}"
verify_import_root_access
install -d -o root -g root -m 0755 "${release_root}" "${release_root}/backend" "${release_root}/frontend"

log "Copying immutable release ${release_id}"
if [[ -d "${source_root}/backend/assets/report-templates" ]]; then
  cp -an "${source_root}/backend/assets/report-templates/." "${RUNTIME_DIRECTORY}/report-templates/"
  chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${RUNTIME_DIRECTORY}/report-templates"
fi
for backend_directory in app migrations scripts public_assets; do
  cp -a "${source_root}/backend/${backend_directory}" "${release_root}/backend/"
done
install -d -o root -g root -m 0755 "${release_root}/backend/assets"
shopt -s dotglob nullglob
for asset_item in "${source_root}/backend/assets/"*; do
  [[ "$(basename "${asset_item}")" == report-templates ]] || cp -a "${asset_item}" "${release_root}/backend/assets/"
done
shopt -u dotglob nullglob
ln -s "${RUNTIME_DIRECTORY}/report-templates" "${release_root}/backend/assets/report-templates"
cp "${source_root}/backend/alembic.ini" "${source_root}/backend/requirements.txt" \
  "${source_root}/backend/requirements.lock" "${release_root}/backend/"
cp -a "${source_root}/frontend/dist" "${release_root}/frontend/"
if [[ -d "${source_root}/video_example" ]]; then
  cp -a "${source_root}/video_example" "${release_root}/"
fi
if [[ -d "${source_root}/examples" ]]; then
  cp -a "${source_root}/examples" "${release_root}/"
fi

"${PYTHON_BIN}" -m venv "${release_root}/.venv"
pip_environment=(PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1)
[[ -z "${PIP_INDEX_URL:-}" ]] || pip_environment+=(PIP_INDEX_URL="${PIP_INDEX_URL}")
[[ -z "${PIP_CERT:-}" ]] || pip_environment+=(PIP_CERT="${PIP_CERT}")
if [[ -d "${script_root}/wheelhouse" ]]; then
  log 'Installing Python dependencies from the offline wheelhouse'
  env "${pip_environment[@]}" PIP_NO_INDEX=1 \
    "${release_root}/.venv/bin/python" -m pip install \
    --find-links "${script_root}/wheelhouse" --requirement "${release_root}/backend/requirements.lock"
else
  log 'Installing Python dependencies from the configured package index'
  env "${pip_environment[@]}" \
    "${release_root}/.venv/bin/python" -m pip install --requirement "${release_root}/backend/requirements.lock"
fi

find "${release_root}" -type d -exec chmod a+rx,go-w {} +
find "${release_root}" -type f -exec chmod go-w {} +
chown -R root:root "${release_root}"

runtime_environment=(
  ANALYSIS_DB_BACKEND=postgresql
  DATABASE_URL="${DATABASE_URL}"
  DEPLOYMENT_PROFILE=rocky8
  AUTH_MODE="${AUTH_MODE}"
  AUTH_SECRET_KEY="${AUTH_SECRET_KEY}"
  AUTH_TOKEN_TTL_MINUTES="${AUTH_TOKEN_TTL_MINUTES}"
  AUTH_COOKIE_SECURE=true
  CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS}"
  DIRECTORY_MODE="${DIRECTORY_MODE}"
  SIM_DASH_APP_ROLE="${SIM_DASH_APP_ROLE}"
  UVICORN_WORKERS="${UVICORN_WORKERS}"
  POSTGRES_MAX_CONNECTIONS="${POSTGRES_MAX_CONNECTIONS}"
  POSTGRES_RESERVED_CONNECTIONS="${POSTGRES_RESERVED_CONNECTIONS}"
  POSTGRES_REQUEST_POOL_SIZE="${POSTGRES_REQUEST_POOL_SIZE}"
  POSTGRES_REQUEST_MAX_OVERFLOW="${POSTGRES_REQUEST_MAX_OVERFLOW}"
  POSTGRES_REQUEST_POOL_TIMEOUT_SECONDS="${POSTGRES_REQUEST_POOL_TIMEOUT_SECONDS}"
  POSTGRES_MEDIA_POOL_SIZE="${POSTGRES_MEDIA_POOL_SIZE}"
  POSTGRES_MEDIA_MAX_OVERFLOW="${POSTGRES_MEDIA_MAX_OVERFLOW}"
  POSTGRES_MEDIA_POOL_TIMEOUT_SECONDS="${POSTGRES_MEDIA_POOL_TIMEOUT_SECONDS}"
  POSTGRES_POOL_RECYCLE_SECONDS="${POSTGRES_POOL_RECYCLE_SECONDS}"
  SIMDASH_IMPORT_ROOT="${SIMDASH_IMPORT_ROOT}"
  SIMDASH_IMPORT_READINESS_POLICY="${SIMDASH_IMPORT_READINESS_POLICY}"
  SIMDASH_MEDIA_STORAGE_MODE="${SIMDASH_MEDIA_STORAGE_MODE}"
  SIMDASH_IMPORT_SNAPSHOT_ROOT="${SIMDASH_IMPORT_SNAPSHOT_ROOT}"
  SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES="${SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES}"
  SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES="${SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES}"
  SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS="${SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS}"
  SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT="${SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT}"
)
for optional_name in OIDC_ISSUER_URL OIDC_CLIENT_ID OIDC_CLIENT_SECRET OIDC_REDIRECT_URI OIDC_SCOPES \
  OIDC_EMPLOYEE_ID_CLAIM OIDC_USERNAME_CLAIM OIDC_DISPLAY_NAME_CLAIM OIDC_DEPARTMENT_CLAIM \
  OIDC_JOB_TITLE_CLAIM OIDC_LOGIN_SUCCESS_URL OIDC_LOGIN_FAILURE_URL OIDC_TIMEOUT_SECONDS \
  DIRECTORY_API_BASE_URL DIRECTORY_API_SEARCH_PATH DIRECTORY_API_TOKEN DIRECTORY_API_TIMEOUT_SECONDS \
  DIRECTORY_API_RESULT_LIMIT; do
  [[ -z "${!optional_name:-}" ]] || runtime_environment+=("${optional_name}=${!optional_name}")
done

if [[ "${BOOTSTRAP_DATABASE}" == 1 ]]; then
  log 'Creating or validating PostgreSQL roles and database'
  env POSTGRES_ADMIN_URL="${POSTGRES_ADMIN_URL}" \
    SIM_DASH_OWNER_PASSWORD="${SIM_DASH_OWNER_PASSWORD}" \
    SIM_DASH_APP_PASSWORD="${SIM_DASH_APP_PASSWORD}" \
    SIM_DASH_DATABASE="${SIM_DASH_DATABASE}" \
    SIM_DASH_OWNER_ROLE="${SIM_DASH_OWNER_ROLE}" \
    SIM_DASH_APP_ROLE="${SIM_DASH_APP_ROLE}" \
    SIM_DASH_PRESERVE_EXISTING_ROLES="${SIM_DASH_PRESERVE_EXISTING_ROLES}" \
    "${release_root}/.venv/bin/python" "${release_root}/backend/scripts/bootstrap_postgres.py"
fi

if [[ "${MIGRATE_DATABASE}" == 1 ]]; then
  log 'Applying Alembic migrations with the database owner role'
  (
    cd "${release_root}/backend"
    env ANALYSIS_DB_BACKEND=postgresql DATABASE_URL="${POSTGRES_OWNER_URL}" \
      SIM_DASH_OWNER_ROLE="${SIM_DASH_OWNER_ROLE}" SIM_DASH_APP_ROLE="${SIM_DASH_APP_ROLE}" \
      "${release_root}/.venv/bin/python" -m alembic -c alembic.ini upgrade head
    env ANALYSIS_DB_BACKEND=postgresql DATABASE_URL="${POSTGRES_OWNER_URL}" \
      SIM_DASH_APP_ROLE="${SIM_DASH_APP_ROLE}" \
      "${release_root}/.venv/bin/python" scripts/harden_postgres_privileges.py
  )
fi

log 'Checking PostgreSQL 18 and connection capacity'
database_info="$(
  env DATABASE_URL="${DATABASE_URL}" "${release_root}/.venv/bin/python" - <<'PY'
import os
import psycopg

url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(url, connect_timeout=5) as connection:
    version, maximum = connection.execute("SHOW server_version_num").fetchone()[0], connection.execute("SHOW max_connections").fetchone()[0]
print(f"{int(version) // 10000} {int(maximum)}")
PY
)"
read -r postgres_major actual_max_connections <<<"${database_info}"
[[ "${postgres_major}" == 18 ]] || die "PostgreSQL 18.x is required; server major is ${postgres_major}."
[[ "${POSTGRES_MAX_CONNECTIONS}" == "${actual_max_connections}" ]] || die "POSTGRES_MAX_CONNECTIONS=${POSTGRES_MAX_CONNECTIONS}, but the server reports ${actual_max_connections}."

if [[ "${SEED_MODE}" == reference ]]; then
  log 'Installing the explicit deterministic reference/example data set'
  (
    cd "${release_root}/backend"
    env "${runtime_environment[@]}" "${release_root}/.venv/bin/python" scripts/seed_database.py --mode reference
  )
fi

if [[ -n "${INITIAL_ADMIN_USERNAME}" ]]; then
  log "Creating or resetting initial password administrator ${INITIAL_ADMIN_USERNAME}"
  (
    cd "${release_root}/backend"
    env "${runtime_environment[@]}" SIM_DASH_USER_PASSWORD="${INITIAL_ADMIN_PASSWORD}" \
      "${release_root}/.venv/bin/python" scripts/create_user.py \
      --username "${INITIAL_ADMIN_USERNAME}" --display-name "${INITIAL_ADMIN_DISPLAY_NAME}" \
      --role admin --global-admin --replace
  )
fi

if [[ "${AUTH_MODE}" == password ]]; then
  password_user_count="$(
    env DATABASE_URL="${DATABASE_URL}" "${release_root}/.venv/bin/python" - <<'PY'
import os
import psycopg

url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(url, connect_timeout=5) as connection:
    count = connection.execute(
        "SELECT count(*) FROM users "
        "WHERE is_active=true AND account_status='ACTIVE' AND password_hash IS NOT NULL"
    ).fetchone()[0]
print(int(count))
PY
  )"
  (( password_user_count > 0 )) || die 'AUTH_MODE=password requires at least one active password user; set INITIAL_ADMIN_USERNAME/PASSWORD.'
fi

log 'Running application-role database and deployment preflight checks'
(
  cd "${release_root}/backend"
  env "${runtime_environment[@]}" "${release_root}/.venv/bin/python" scripts/check_deployment_profile.py
  env "${runtime_environment[@]}" "${release_root}/.venv/bin/python" scripts/check_media_storage_preflight.py
  env "${runtime_environment[@]}" "${release_root}/.venv/bin/python" scripts/check_postgres_connection.py
  env "${runtime_environment[@]}" "${release_root}/.venv/bin/python" scripts/check_postgres_pool_budget.py
)

write_environment_line() {
  local name="$1"
  local value="${!name:-}"
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || die "${name} contains a newline."
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s="%s"\n' "${name}" "${value}"
}

install -d -o root -g "${SERVICE_GROUP}" -m 0750 "$(dirname "${ENVIRONMENT_FILE}")"
environment_tmp="$(mktemp "${ENVIRONMENT_FILE}.tmp.XXXXXX")"
environment_names=(
  DATABASE_URL ANALYSIS_DB_BACKEND DEPLOYMENT_PROFILE AUTH_MODE AUTH_SECRET_KEY
  AUTH_TOKEN_TTL_MINUTES AUTH_COOKIE_SECURE CORS_ALLOWED_ORIGINS DIRECTORY_MODE
  SIM_DASH_APP_ROLE UVICORN_WORKERS POSTGRES_MAX_CONNECTIONS POSTGRES_RESERVED_CONNECTIONS
  POSTGRES_REQUEST_POOL_SIZE POSTGRES_REQUEST_MAX_OVERFLOW POSTGRES_REQUEST_POOL_TIMEOUT_SECONDS
  POSTGRES_MEDIA_POOL_SIZE POSTGRES_MEDIA_MAX_OVERFLOW POSTGRES_MEDIA_POOL_TIMEOUT_SECONDS
  POSTGRES_POOL_RECYCLE_SECONDS SIMDASH_IMPORT_ROOT SIMDASH_IMPORT_READINESS_POLICY SIMDASH_MEDIA_STORAGE_MODE
  SIMDASH_IMPORT_SNAPSHOT_ROOT SIMDASH_IMPORT_SNAPSHOT_RESERVE_BYTES
  SIMDASH_IMPORT_SNAPSHOT_MIN_FREE_BYTES SIMDASH_IMPORT_SNAPSHOT_STALE_SECONDS
  SIMDASH_IMPORT_REFRESH_MAX_CONCURRENT
)
ANALYSIS_DB_BACKEND=postgresql
DEPLOYMENT_PROFILE=rocky8
for optional_name in OIDC_ISSUER_URL OIDC_CLIENT_ID OIDC_CLIENT_SECRET OIDC_REDIRECT_URI OIDC_SCOPES \
  OIDC_EMPLOYEE_ID_CLAIM OIDC_USERNAME_CLAIM OIDC_DISPLAY_NAME_CLAIM OIDC_DEPARTMENT_CLAIM \
  OIDC_JOB_TITLE_CLAIM OIDC_LOGIN_SUCCESS_URL OIDC_LOGIN_FAILURE_URL OIDC_TIMEOUT_SECONDS \
  DIRECTORY_API_BASE_URL DIRECTORY_API_SEARCH_PATH DIRECTORY_API_TOKEN DIRECTORY_API_TIMEOUT_SECONDS \
  DIRECTORY_API_RESULT_LIMIT POSTGRES_BIN; do
  [[ -z "${!optional_name:-}" ]] || environment_names+=("${optional_name}")
done
for environment_name in "${environment_names[@]}"; do
  write_environment_line "${environment_name}" >>"${environment_tmp}"
done
chown root:"${SERVICE_GROUP}" "${environment_tmp}"
chmod 0640 "${environment_tmp}"
mv -f "${environment_tmp}" "${ENVIRONMENT_FILE}"

sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

render_template() {
  local source="$1"
  local destination="$2"
  shift 2
  local rendered
  rendered="$(mktemp)"
  cp "${source}" "${rendered}"
  while [[ $# -gt 0 ]]; do
    local token="$1"
    local replacement="$2"
    shift 2
    sed -i "s|${token}|$(sed_escape "${replacement}")|g" "${rendered}"
  done
  if grep -q '__REPLACE_' "${rendered}"; then
    rm -f "${rendered}"
    die "Unresolved placeholder while rendering ${source}."
  fi
  install -o root -g root -m 0644 "${rendered}" "${destination}"
  rm -f "${rendered}"
}

log 'Installing systemd and nginx configuration'
render_template "${script_root}/systemd/simdashboard.service.template" \
  /etc/systemd/system/simdashboard.service \
  __REPLACE_ENVIRONMENT__ rocky8 \
  __REPLACE_SERVICE_USER__ "${SERVICE_USER}" \
  __REPLACE_SERVICE_GROUP__ "${SERVICE_GROUP}" \
  __REPLACE_RELEASE_ROOT__ "${INSTALL_ROOT}/current" \
  __REPLACE_ENVIRONMENT_FILE__ "${ENVIRONMENT_FILE}" \
  __REPLACE_API_PORT__ "${API_PORT}" \
  __REPLACE_UVICORN_WORKERS__ "${UVICORN_WORKERS}" \
  __REPLACE_RUNTIME_DIRECTORY__ "${RUNTIME_DIRECTORY}" \
  __REPLACE_IMPORT_ROOT__ "${SIMDASH_IMPORT_ROOT}"
render_template "${script_root}/nginx/simdashboard.conf.template" \
  /etc/nginx/conf.d/simdashboard.conf \
  __REPLACE_SERVER_NAME__ "${SERVER_NAME}" \
  __REPLACE_TLS_CERTIFICATE__ "${TLS_CERTIFICATE}" \
  __REPLACE_TLS_CERTIFICATE_KEY__ "${TLS_CERTIFICATE_KEY}" \
  __REPLACE_RELEASE_ROOT__ "${INSTALL_ROOT}/current" \
  __REPLACE_API_PORT__ "${API_PORT}"
install -o root -g root -m 0755 "${script_root}/healthcheck.sh" /usr/local/sbin/simdashboard-healthcheck

previous_target=""
if [[ -L "${INSTALL_ROOT}/current" ]]; then
  previous_target="$(readlink "${INSTALL_ROOT}/current")"
elif [[ -e "${INSTALL_ROOT}/current" ]]; then
  die "${INSTALL_ROOT}/current exists but is not a symlink."
fi

restore_code_symlink() {
  if [[ -n "${previous_target}" ]]; then
    local restore_link="${INSTALL_ROOT}/.restore-${release_id}"
    ln -s "${previous_target}" "${restore_link}"
    mv -Tf "${restore_link}" "${INSTALL_ROOT}/current"
  elif [[ -L "${INSTALL_ROOT}/current" ]]; then
    unlink "${INSTALL_ROOT}/current"
  fi
}

next_link="${INSTALL_ROOT}/.current-${release_id}"
ln -s "releases/${release_id}" "${next_link}"
mv -Tf "${next_link}" "${INSTALL_ROOT}/current"

systemctl daemon-reload
if ! systemd-analyze verify /etc/systemd/system/simdashboard.service; then
  restore_code_symlink
  die 'systemd unit verification failed; the application symlink was restored.'
fi
if ! nginx -t; then
  restore_code_symlink
  die 'nginx configuration verification failed; the application symlink was restored.'
fi

if [[ "${CONFIGURE_SELINUX}" == 1 ]] && command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" != Disabled ]]; then
  log 'Configuring SELinux labels and nginx loopback proxy permission'
  setsebool -P httpd_can_network_connect 1
  semanage fcontext -a -t httpd_sys_content_t "${INSTALL_ROOT}/releases(/.*)?" 2>/dev/null || \
    semanage fcontext -m -t httpd_sys_content_t "${INSTALL_ROOT}/releases(/.*)?"
  restorecon -RF "${INSTALL_ROOT}/releases"
  # The API reads imported result files directly; do not use httpd_sys_content_t
  # because nginx never serves this source folder.  External mounts retain their
  # mount-specific SELinux policy and are checked through the service account's
  # ordinary filesystem access above.
  if [[ "${SIMDASH_IMPORT_ROOT}" == "${RUNTIME_DIRECTORY}/import" ]]; then
    semanage fcontext -a -t var_lib_t "${SIMDASH_IMPORT_ROOT}(/.*)?" 2>/dev/null || \
      semanage fcontext -m -t var_lib_t "${SIMDASH_IMPORT_ROOT}(/.*)?"
    restorecon -RF "${SIMDASH_IMPORT_ROOT}"
  fi
fi

if [[ "${CONFIGURE_FIREWALL}" == 1 ]]; then
  log 'Opening the HTTPS service in firewalld'
  systemctl enable --now firewalld
  firewall-cmd --permanent --add-service=https
  firewall-cmd --reload
fi

rollback_code_symlink() {
  restore_code_symlink
  if [[ -n "${previous_target}" ]]; then
    systemctl restart simdashboard.service || true
  else
    systemctl stop simdashboard.service || true
  fi
}

if [[ "${START_SERVICES}" == 1 ]]; then
  log 'Starting nginx and Analysis Canvas'
  systemctl enable nginx.service simdashboard.service
  systemctl restart nginx.service
  systemctl restart simdashboard.service
  healthy=0
  for _ in {1..30}; do
    if SIMDASH_HEALTH_URL="http://127.0.0.1:${API_PORT}/api/health" /usr/local/sbin/simdashboard-healthcheck >/dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 1
  done
  if [[ "${healthy}" != 1 ]]; then
    journalctl -u simdashboard.service --no-pager -n 80 >&2 || true
    rollback_code_symlink
    die 'Service health check failed; the application symlink was rolled back when a previous release existed.'
  fi
  if [[ -n "${HEALTHCHECK_URL}" ]]; then
    SIMDASH_HEALTH_URL="${HEALTHCHECK_URL}" /usr/local/sbin/simdashboard-healthcheck || {
      rollback_code_symlink
      die 'Public HTTPS health check failed; the application symlink was rolled back when possible.'
    }
  fi
fi

log "Installation complete: release=${release_id} current=${INSTALL_ROOT}/current"
log "Dashboard URL: https://${SERVER_NAME}/"
if [[ "${START_SERVICES}" != 1 ]]; then
  log 'Services were not started. Review the configuration, then enable/start nginx.service and simdashboard.service.'
fi
