#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

readonly UV_VERSION=0.11.8
readonly PYTHON_VERSION=3.12.13
readonly RUNTIME_ROOT='/opt/simdashboard/runtime/python'
readonly STABLE_PYTHON="${RUNTIME_ROOT}/bin/python3.12"
readonly DEFAULT_CACHE_ROOT='/var/cache/simdashboard/runtime'

die() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[simdashboard-python] %s\n' "$*"
}

usage() {
  cat <<'EOF'
Usage: sudo ./bootstrap-python-runtime.sh

Prepares the exact Python 3.12.13 runtime used by the Rocky 8 installer at:
  /opt/simdashboard/runtime/python/bin/python3.12

The helper first reuses an existing exact runtime, then checks
SIMDASH_PYTHON_RUNTIME_CACHE (default: /var/cache/simdashboard/runtime) for the
architecture-specific uv 0.11.8 archive, and downloads it only when absent.
curl honors HTTPS_PROXY/HTTP_PROXY and CURL_CA_BUNDLE/SSL_CERT_FILE for
corporate egress and CA trust. A preseed archive uses this filename:
  uv-0.11.8-x86_64-unknown-linux-gnu.tar.gz
  uv-0.11.8-aarch64-unknown-linux-gnu.tar.gz
EOF
}

[[ "${EUID}" -eq 0 ]] || die 'Run this helper as root (sudo).'
if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
fi
[[ $# -eq 0 ]] || { usage >&2; die 'Unknown argument.'; }

[[ -r /etc/os-release ]] || die '/etc/os-release is missing.'
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == rocky ]] || die "Rocky Linux is required; detected ${PRETTY_NAME:-unknown}."

rocky8_version_supported() {
  local version_id="${1:-}"
  local major minor normalized_minor
  [[ "${version_id}" =~ ^([0-9]+)\.([0-9]+)(\.[0-9]+)?$ ]] || return 1
  major="${BASH_REMATCH[1]}"
  minor="${BASH_REMATCH[2]}"
  [[ "${major}" == 8 ]] || return 1
  normalized_minor="${minor#${minor%%[!0]*}}"
  normalized_minor="${normalized_minor:-0}"
  [[ "${#normalized_minor}" -gt 1 || "${normalized_minor}" =~ ^[6-9]$ ]]
}

rocky8_version_supported "${VERSION_ID:-}" || \
  die "Rocky Linux 8.6 or later (8.x) is required; detected ${PRETTY_NAME:-unknown} (VERSION_ID=${VERSION_ID:-unknown})."

for command_name in awk find install ln mktemp mv readlink sha256sum tar uname; do
  command -v "${command_name}" >/dev/null 2>&1 || die "Required command not found: ${command_name}"
done

if [[ -x "${STABLE_PYTHON}" ]]; then
  existing_python_version="$("${STABLE_PYTHON}" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
  if [[ "${existing_python_version}" == "${PYTHON_VERSION}" ]]; then
    log "Existing Python ${PYTHON_VERSION} runtime is ready: ${STABLE_PYTHON}"
    exit 0
  fi
  log "Existing runtime is not Python ${PYTHON_VERSION}; preparing the pinned runtime."
fi

machine="$(uname -m)"
case "${machine}" in
  x86_64|amd64)
    uv_target='x86_64-unknown-linux-gnu'
    uv_sha256='56dd1b66701ecb62fe896abb919444e4b83c5e8645cca953e6ddd496ff8a0feb'
    ;;
  aarch64|arm64)
    uv_target='aarch64-unknown-linux-gnu'
    uv_sha256='eee8dd658d20e5ac85fec9c2326b6cbc9d83a1eef09ef07433e58698ac849591'
    ;;
  *)
    die "Unsupported Linux architecture for uv ${UV_VERSION}: ${machine}"
    ;;
esac

cache_root="${SIMDASH_PYTHON_RUNTIME_CACHE:-${DEFAULT_CACHE_ROOT}}"
[[ "${cache_root}" == /* && "${cache_root}" != / ]] || \
  die 'SIMDASH_PYTHON_RUNTIME_CACHE must be an absolute, non-root directory.'
[[ "${cache_root}" != *$'\n'* && "${cache_root}" != *$'\r'* ]] || \
  die 'SIMDASH_PYTHON_RUNTIME_CACHE contains a newline.'
install -d -o root -g root -m 0750 "${cache_root}"

archive_name="uv-${UV_VERSION}-${uv_target}.tar.gz"
archive_path="${cache_root}/${archive_name}"
if [[ ! -f "${archive_path}" ]]; then
  command -v curl >/dev/null 2>&1 || die 'curl is required to download uv. Preseed the archive in SIMDASH_PYTHON_RUNTIME_CACHE instead.'
  download_tmp="$(mktemp "${cache_root}/.${archive_name}.XXXXXX")"
  trap 'rm -f -- "${download_tmp}"' EXIT
  uv_asset="uv-${uv_target}.tar.gz"
  uv_url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${uv_asset}"
  log "Downloading uv ${UV_VERSION} for ${machine} (proxy/CA settings are read from curl environment)."
  curl --fail --location --silent --show-error --retry 3 --output "${download_tmp}" "${uv_url}" || \
    die 'uv download failed. Configure HTTPS_PROXY/HTTP_PROXY and CURL_CA_BUNDLE or preseed SIMDASH_PYTHON_RUNTIME_CACHE.'
  mv -f -- "${download_tmp}" "${archive_path}"
  trap - EXIT
fi

actual_sha256="$(sha256sum "${archive_path}" | awk '{print $1}')"
[[ "${actual_sha256}" == "${uv_sha256}" ]] || \
  die "uv archive checksum mismatch for ${archive_name}. Replace the cached archive with the official release."

archive_entry_is_safe() {
  local entry="$1"
  [[ "${entry}" != /* && "${entry}" != .. && "${entry}" != *$'\n'* && "${entry}" != *$'\r'* ]] || return 1
  case "/${entry}/" in
    */../*) return 1 ;;
  esac
}

while IFS= read -r archive_entry; do
  archive_entry_is_safe "${archive_entry}" || die "Unsafe path in uv archive: ${archive_entry}"
done < <(tar -tzf "${archive_path}")

uv_extract_root="$(mktemp -d)"
trap 'rm -rf -- "${uv_extract_root}"' EXIT
tar -xzf "${archive_path}" -C "${uv_extract_root}" --no-same-owner --no-same-permissions
uv_bin="$(find "${uv_extract_root}" -type f -name uv -perm -u+x -print -quit)"
[[ -n "${uv_bin}" ]] || die 'The verified uv archive does not contain an executable uv binary.'

install -d -o root -g root -m 0755 "${RUNTIME_ROOT}"
"${uv_bin}" --system-certs --no-progress python install "${PYTHON_VERSION}" \
  --install-dir "${RUNTIME_ROOT}" --force

managed_python="$(
  UV_PYTHON_INSTALL_DIR="${RUNTIME_ROOT}" "${uv_bin}" --system-certs --no-progress python find "${PYTHON_VERSION}" \
    --managed-python --no-project --resolve-links
)" || die 'uv installed Python but could not locate the managed interpreter.'
[[ -f "${managed_python}" && -x "${managed_python}" ]] || \
  die "uv managed interpreter is not executable: ${managed_python}"
managed_python="$(readlink -f "${managed_python}")"
case "${managed_python}" in
  "${RUNTIME_ROOT}"/*) ;;
  *) die "uv managed interpreter escaped the runtime root: ${managed_python}" ;;
esac
stable_tmp="${RUNTIME_ROOT}/.python3.12.${BASHPID}.tmp"
ln -s "${managed_python}" "${stable_tmp}"
mv -Tf "${stable_tmp}" "${STABLE_PYTHON}"
[[ -x "${STABLE_PYTHON}" ]] || die "uv did not create the stable Python path: ${STABLE_PYTHON}"
installed_python_version="$("${STABLE_PYTHON}" -c 'import platform; print(platform.python_version())')"
[[ "${installed_python_version}" == "${PYTHON_VERSION}" ]] || \
  die "Python runtime version mismatch after installation: ${installed_python_version}"
chown -R root:root "${RUNTIME_ROOT}"
find "${RUNTIME_ROOT}" -type d -exec chmod 0755 {} +
chmod 0755 "${STABLE_PYTHON}"
log "Python ${PYTHON_VERSION} runtime ready: ${STABLE_PYTHON}"
