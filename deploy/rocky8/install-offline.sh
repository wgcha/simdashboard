#!/usr/bin/env bash
# Install a self-contained Rocky 8.6/x86_64 release.  This wrapper deliberately
# has no network fallback: install.sh remains the application installer.
set -Eeuo pipefail
umask 077

readonly PYTHON_VERSION='3.12.13'
readonly PYTHON_BUILD='20260414'
readonly PYTHON_ARCHIVE_SHA256='3c3427e5628648478da2aa227472c350475a68bc58109f1b43849636a4aecb89'
readonly PYTHON_RUNTIME_NAME="python-${PYTHON_VERSION}-${PYTHON_BUILD}"
readonly PYTHON_RUNTIME="/opt/simdashboard/runtimes/${PYTHON_RUNTIME_NAME}"
readonly PYTHON_BIN="${PYTHON_RUNTIME}/python/bin/python3.12"

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
config_file=''
check_only=0
dev_http=0
temp_root=''

usage() {
  cat <<'EOF'
Usage: sudo ./install-offline.sh --config /root/simdashboard-install.env [--check] [--dev-http]

Installs only from this self-contained Rocky Linux 8.6 x86_64 release bundle.
The trusted configuration must be a root-owned 0600 file outside this extracted
bundle.  This command never enables an online repository or downloads anything.

Options:
  --config FILE   Required root-owned mode-0600 configuration outside the bundle
  --check         Validate bundle, config, runtime and offline wheels only; no
                  system changes (temporary files are used for validation)
  --dev-http      Development-only HTTP installation; no TLS certificate is used
  -h, --help      Show this help
EOF
}

die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
log() { printf '[simdashboard-offline] %s\n' "$*"; }

cleanup() {
  [[ -z "${temp_root}" ]] || rm -rf -- "${temp_root}"
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) config_file="${2:-}"; shift 2 ;;
    --check) check_only=1; shift ;;
    --dev-http) dev_http=1; shift ;;
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
case "${config_file}/" in "${script_root}/"*) die 'The config file must be outside the extracted bundle.' ;; esac

for command_name in awk dnf find mktemp mv readlink rpm sha256sum stat tar uname; do
  command -v "${command_name}" >/dev/null 2>&1 || die "Required command not found: ${command_name}"
done

[[ -r /etc/os-release ]] || die '/etc/os-release is missing.'
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == rocky && "${VERSION_ID:-}" == 8.6 ]] || \
  die "This offline bundle targets Rocky Linux 8.6 exactly; detected ${PRETTY_NAME:-unknown} (VERSION_ID=${VERSION_ID:-unknown})."
[[ "$(uname -m)" == x86_64 ]] || die "This offline bundle targets x86_64; detected $(uname -m)."

for required_path in install.sh payload/backend/app payload/backend/migrations \
  payload/backend/requirements.lock payload/.python-version wheelhouse \
  runtime/python.tar.gz rpm-repo/repodata rpm-repo/RPM-GPG-KEY-Rocky-8 \
  SHA256SUMS RELEASE_ID; do
  [[ -e "${script_root}/${required_path}" ]] || die "Offline bundle is incomplete: ${required_path}"
done
[[ -x "${script_root}/install.sh" ]] || die 'Offline bundle install.sh is not executable.'
release_id="$(tr -d '[:space:]' <"${script_root}/RELEASE_ID")"
[[ "${release_id}" =~ ^[A-Za-z0-9._-]+$ ]] || die 'RELEASE_ID contains unsupported characters.'
# The release is meant to be an ordinary extracted tree.  Do not let an outer
# bundle symlink redirect either checksum validation or a later installer read.
bundle_symlink="$(cd "${script_root}" && find . -type l -print -quit)"
[[ -z "${bundle_symlink}" ]] || die "Offline bundle must not contain symlinks: ${bundle_symlink}"

validate_manifest() {
  local line_hash line_path actual_path
  # Reject malformed or escaping checksum paths before sha256sum sees them.
  while IFS=' ' read -r line_hash line_path; do
    [[ "${line_hash}" =~ ^[0-9a-fA-F]{64}$ && -n "${line_path}" ]] || die 'SHA256SUMS has an invalid entry.'
    line_path="${line_path# }" # sha256sum's optional binary marker
    [[ "${line_path}" != /* && "${line_path}" != *$'\n'* && "${line_path}" != *$'\r'* ]] || die 'SHA256SUMS contains an unsafe path.'
    case "/${line_path}/" in */../*) die 'SHA256SUMS contains an escaping path.' ;; esac
    actual_path="${script_root}/${line_path#./}"
    [[ -f "${actual_path}" ]] || die "SHA256SUMS references a missing file: ${line_path}"
  done <"${script_root}/SHA256SUMS"
  (cd "${script_root}" && sha256sum --check --quiet SHA256SUMS) || die 'Release bundle checksum validation failed.'

  # A signed-looking manifest that omits a payload file would leave the omitted
  # file mutable.  Require exact coverage of every regular file except itself.
  while IFS= read -r -d '' actual_path; do
    actual_path="${actual_path#./}"
    [[ "${actual_path}" == SHA256SUMS ]] && continue
    grep -Fqx -- "$(sha256sum "${script_root}/${actual_path}" | awk '{print $1}')  ./${actual_path}" "${script_root}/SHA256SUMS" || \
      die "SHA256SUMS does not cover bundle file: ${actual_path}"
  done < <(cd "${script_root}" && find . -type f -print0)
}

archive_entry_is_safe() {
  local entry="$1"
  [[ "${entry}" != /* && "${entry}" != .. && "${entry}" != *$'\n'* && "${entry}" != *$'\r'* ]] || return 1
  case "/${entry}/" in */../*) return 1 ;; esac
}

unpack_runtime() {
  local destination="$1" entry
  while IFS= read -r entry; do
    archive_entry_is_safe "${entry}" || die 'Python runtime archive contains an unsafe path.'
    [[ "${entry}" == python || "${entry}" == python/* ]] || die 'Python runtime archive must have only the python/ top-level directory.'
  done < <(tar -tzf "${script_root}/runtime/python.tar.gz")
  mkdir -p "${destination}"
  tar -xzf "${script_root}/runtime/python.tar.gz" -C "${destination}" --no-same-owner --no-same-permissions
  [[ -x "${destination}/python/bin/python3.12" ]] || die 'Python runtime archive does not contain python/bin/python3.12.'
  local actual_python
  actual_python="$("${destination}/python/bin/python3.12" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
  [[ "${actual_python}" == "${PYTHON_VERSION}" ]] || die "Python runtime version mismatch: expected ${PYTHON_VERSION}, got ${actual_python:-unreadable}."
}

validate_wheels() {
  local runtime_root="$1" wheel_venv="${temp_root}/wheel-venv"
  "${runtime_root}/python/bin/python3.12" -m venv "${wheel_venv}" || die 'Unable to create a temporary venv from the bundled Python runtime.'
  PIP_CONFIG_FILE=/dev/null PIP_FIND_LINKS= PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 PIP_NO_INDEX=1 \
    "${wheel_venv}/bin/python" -m pip install --find-links "${script_root}/wheelhouse" \
    --requirement "${script_root}/payload/backend/requirements.lock" || \
    die 'Offline wheelhouse does not satisfy payload/backend/requirements.lock.'
}

validate_manifest
[[ "$(sha256sum "${script_root}/runtime/python.tar.gz" | awk '{print $1}')" == "${PYTHON_ARCHIVE_SHA256}" ]] || \
  die 'Bundled Python runtime archive does not match the required 3.12.13-20260414 baseline.'
temp_root="$(mktemp -d /tmp/simdashboard-offline.XXXXXX)"
runtime_validation_root="${temp_root}/runtime"
unpack_runtime "${runtime_validation_root}"
validate_wheels "${runtime_validation_root}"

generated_config="${temp_root}/install.env"
cp -- "${config_file}" "${generated_config}"
chmod 0600 "${generated_config}"
# Check operator settings using the temporary interpreter before publishing
# anything under /opt or installing OS packages, including in real install mode.
effective_python="${runtime_validation_root}/python/bin/python3.12"
printf '\n# Values pinned by install-offline.sh; leave operator SSL/auth/database settings above.\nINSTALL_SOURCE_ROOT=%q/payload\nINSTALL_OS_PACKAGES=0\nPYTHON_BIN=%q\n' \
  "${script_root}" "${effective_python}" >>"${generated_config}"

installer_args=(--config "${generated_config}")
if [[ "${dev_http}" == 1 ]]; then
  installer_args+=(--dev-http)
fi
"${script_root}/install.sh" "${installer_args[@]}" --check
if [[ "${check_only}" == 1 ]]; then
  log "Offline validation passed for release ${release_id}; no system changes were made (temporary files were used)."
  exit 0
fi

if [[ -e "${PYTHON_RUNTIME}" || -L "${PYTHON_RUNTIME}" ]]; then
  [[ -d "${PYTHON_RUNTIME}" && ! -L "${PYTHON_RUNTIME}" && -x "${PYTHON_BIN}" ]] || \
    die "Existing immutable runtime is invalid: ${PYTHON_RUNTIME}"
  actual_python="$("${PYTHON_BIN}" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
  [[ "${actual_python}" == "${PYTHON_VERSION}" ]] || \
    die "Existing immutable runtime has Python ${actual_python:-unknown}, expected ${PYTHON_VERSION}; it will not be overwritten."
  log "Reusing immutable Python runtime: ${PYTHON_RUNTIME}"
else
  runtime_parent="$(dirname "${PYTHON_RUNTIME}")"
  install -d -o root -g root -m 0755 "${runtime_parent}"
  runtime_stage="${runtime_parent}/.${PYTHON_RUNTIME_NAME}.stage.${BASHPID}"
  [[ ! -e "${runtime_stage}" && ! -L "${runtime_stage}" ]] || die "Runtime staging path already exists: ${runtime_stage}"
  unpack_runtime "${runtime_stage}"
  chown -R root:root "${runtime_stage}"
  find "${runtime_stage}" -type d -exec chmod 0755 {} +
  # --no-same-permissions respects umask 077, so normalize the published
  # runtime explicitly: the non-root service needs to read modules and execute
  # interpreter/helper binaries, while nobody except root may modify it.
  find "${runtime_stage}" -type f -exec chmod a+r,go-w {} +
  find "${runtime_stage}" -type f -perm /111 -exec chmod a+rx,go-w {} +
  mv -T "${runtime_stage}" "${PYTHON_RUNTIME}"
  log "Installed immutable Python runtime: ${PYTHON_RUNTIME}"
fi

offline_repo="file://${script_root}/rpm-repo"
runtime_packages=(nginx curl ca-certificates tar findutils shadow-utils policycoreutils-python-utils firewalld)
log 'Installing Rocky runtime packages from the bundled offline repository.'
dnf -y --disablerepo='*' --repofrompath="simdashboard-offline,${offline_repo}" \
  --setopt=install_weak_deps=False \
  --setopt=simdashboard-offline.gpgcheck=1 \
  --setopt="simdashboard-offline.gpgkey=file://${script_root}/rpm-repo/RPM-GPG-KEY-Rocky-8" \
  --setopt=simdashboard-offline.module_hotfixes=1 \
  --setopt=simdashboard-offline.skip_if_unavailable=False \
  install "${runtime_packages[@]}"

printf '\nPYTHON_BIN=%q\n' "${PYTHON_BIN}" >>"${generated_config}"
"${script_root}/install.sh" "${installer_args[@]}"
