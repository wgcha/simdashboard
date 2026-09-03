#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Build a release as the invoking (non-root) user, then perform the privileged
# installation from the verified extracted bundle.  The configuration is copied
# into /root so install.sh can enforce its root ownership and 0600 mode.
script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd "${script_root}/../.." && pwd -P)"
config_file=""
with_wheels=1
allow_dirty=0
runtime_bootstrap=1

usage() {
  cat <<'EOF'
Usage: deploy/rocky8/deploy-from-source.sh --config FILE [options]

Builds and installs a Rocky Linux 8 release from this repository. Run it from
the repository root as a normal user; sudo is requested only for installation.

Options:
  --config FILE       Trusted install configuration (required)
  --with-wheels       Build an offline Python wheelhouse with the release (default)
  --without-wheels    Do not include the offline Python wheelhouse
  --allow-dirty       Allow building from an uncommitted Git worktree
  --no-runtime-bootstrap
                      Do not download a pinned Node.js runtime when unavailable
  -h, --help          Show this help
EOF
}

die() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

require_value() {
  [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "$1 requires a value."
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      require_value --config "${2:-}"
      config_file="$2"
      shift 2
      ;;
    --with-wheels) with_wheels=1; shift ;;
    --without-wheels) with_wheels=0; shift ;;
    --allow-dirty) allow_dirty=1; shift ;;
    --no-runtime-bootstrap) runtime_bootstrap=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "${config_file}" ]] || die '--config is required.'
[[ "$(pwd -P)" == "${project_root}" ]] || die "Run this command from the repository root: ${project_root}"
[[ "${EUID}" -ne 0 ]] || die 'Run the build as a normal user; sudo is used automatically for installation.'
command -v sudo >/dev/null 2>&1 || die 'sudo is required for installation.'
command -v sha256sum >/dev/null 2>&1 || die 'sha256sum is required.'
command -v tar >/dev/null 2>&1 || die 'tar is required.'
command -v git >/dev/null 2>&1 || die 'git is required.'
[[ -r "${config_file}" ]] || die "Config file is not readable: ${config_file}"
[[ ! -L "${config_file}" ]] || die 'Config file must not be a symbolic link.'
config_file="$(readlink -f "${config_file}")"
case "${config_file}" in
  "${project_root}"/*) ;;
  *) die 'Config file must be stored in this repository and excluded from Git.' ;;
esac
config_relative_path="${config_file#"${project_root}"/}"
git -C "${project_root}" ls-files --error-unmatch -- "${config_relative_path}" >/dev/null 2>&1 && \
  die 'Config file is tracked by Git; move it to an ignored local .env file before deploying.'
git -C "${project_root}" check-ignore -q -- "${config_relative_path}" || \
  die 'Config file is not Git-ignored; add an ignore rule before deploying.'
[[ "$(stat -c '%u' "${config_file}")" == "${EUID}" ]] || \
  die 'Config file must be owned by the user running this command.'
config_mode="$(stat -c '%a' "${config_file}")"
(( (8#${config_mode} & 077) == 0 )) || \
  die 'Config file must not be accessible by group or others (use chmod 0600).'

# Do this before any sudo invocation, including runtime bootstrap. A clone with
# uncommitted code must never be able to replace a root-executed helper unless
# the operator deliberately accepts that risk.
if [[ -n "$(git -C "${project_root}" status --porcelain --untracked-files=normal)" ]]; then
  if [[ "${allow_dirty}" != 1 ]]; then
    die 'Refusing to deploy from a dirty Git worktree. Commit/stash changes or pass --allow-dirty.'
  fi
  printf '%s\n' '[WARNING] Proceeding from a dirty Git worktree because --allow-dirty was supplied.' >&2
fi

# Authenticate only after source integrity gates have passed. Keep proxy/CA
# values out of arguments and logs; only their names are preserved later for
# the root-owned Python runtime helper.
sudo -v

ensure_pinned_node_runtime() {
  local expected_node='v22.23.2'
  local runtime_cache node_platform node_archive_name node_sha256 node_archive
  local runtime_directory_name runtime_directory extraction_directory candidate
  local downloaded_archive runtime_cache_mode

  [[ "$(tr -d '[:space:]' <"${project_root}/.node-version")" == "${expected_node}" ]] || \
    die "This deployment entrypoint only supports the pinned Node.js ${expected_node} runtime."
  if command -v node >/dev/null 2>&1 && command -v corepack >/dev/null 2>&1 && \
    [[ "$(node --version)" == "${expected_node}" ]]; then
    return 0
  fi
  [[ "${runtime_bootstrap}" == 1 ]] || \
    die "Node.js ${expected_node} and corepack are required; remove --no-runtime-bootstrap or install them first."
  command -v curl >/dev/null 2>&1 || \
    die "Node.js ${expected_node} is missing and curl is required to download it. Install curl or preseed SIMDASH_NODE_RUNTIME_CACHE."
  case "$(uname -m)" in
    x86_64)
      node_platform='x64'
      node_sha256='b294a556e639d64338823920e5866c21c02741742d2e1529ee1a225c1ec9252a'
      ;;
    aarch64)
      node_platform='arm64'
      node_sha256='013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30'
      ;;
    *) die "Unsupported CPU architecture for Node.js runtime bootstrap: $(uname -m)" ;;
  esac

  runtime_cache="${SIMDASH_NODE_RUNTIME_CACHE:-${XDG_CACHE_HOME:-${HOME}/.cache}/simdashboard-runtime}"
  [[ "${runtime_cache}" == /* ]] || die 'SIMDASH_NODE_RUNTIME_CACHE must be an absolute path.'
  [[ "${runtime_cache}" != *$'\n'* && "${runtime_cache}" != *$'\r'* ]] || \
    die 'SIMDASH_NODE_RUNTIME_CACHE must not contain a newline.'
  [[ ! -L "${runtime_cache}" ]] || die 'SIMDASH_NODE_RUNTIME_CACHE must not be a symbolic link.'
  mkdir -p -m 0700 "${runtime_cache}"
  [[ "$(stat -c '%u' "${runtime_cache}")" == "${EUID}" ]] || \
    die 'SIMDASH_NODE_RUNTIME_CACHE must be owned by the user running this command.'
  runtime_cache_mode="$(stat -c '%a' "${runtime_cache}")"
  (( (8#${runtime_cache_mode} & 077) == 0 )) || \
    die 'SIMDASH_NODE_RUNTIME_CACHE must not be accessible by group or others.'

  runtime_directory_name="node-${expected_node}-linux-${node_platform}"
  node_archive_name="${runtime_directory_name}.tar.gz"
  node_archive="${runtime_cache}/${node_archive_name}"
  runtime_directory="${runtime_cache}/${runtime_directory_name}"
  [[ ! -L "${node_archive}" ]] || die 'Cached Node.js archive must not be a symbolic link.'
  [[ ! -L "${runtime_directory}" ]] || die 'Cached Node.js runtime must not be a symbolic link.'
  if [[ -x "${runtime_directory}/bin/node" && -x "${runtime_directory}/bin/corepack" ]] && \
    [[ "$("${runtime_directory}/bin/node" --version)" == "${expected_node}" ]]; then
    PATH="${runtime_directory}/bin:${PATH}"
    export PATH
    return 0
  fi
  [[ ! -e "${runtime_directory}" ]] || \
    die "Cached Node.js runtime is invalid: ${runtime_directory}. Remove it and retry."

  if [[ ! -f "${node_archive}" ]]; then
    printf '%s\n' "Downloading verified Node.js ${expected_node} runtime for ${node_platform}."
    downloaded_archive="${runtime_cache}/.${node_archive_name}.download.XXXXXX"
    downloaded_archive="$(mktemp "${downloaded_archive}")"
    if ! curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --output "${downloaded_archive}" \
      "https://nodejs.org/dist/${expected_node}/${node_archive_name}"; then
      rm -f -- "${downloaded_archive}"
      die 'Node.js download failed. Configure HTTPS_PROXY/NO_PROXY and your corporate CA (for example CURL_CA_BUNDLE), or preseed SIMDASH_NODE_RUNTIME_CACHE with the verified archive.'
    fi
    mv -f "${downloaded_archive}" "${node_archive}"
  fi

  printf '%s  %s\n' "${node_sha256}" "${node_archive}" | sha256sum --check --strict --status - || \
    die "Node.js archive checksum verification failed: ${node_archive}"
  while IFS= read -r archive_entry; do
    [[ "${archive_entry}" != /* && "${archive_entry}" != *$'\n'* && "${archive_entry}" != *$'\r'* ]] || \
      die 'Node.js archive contains an unsafe path.'
    case "/${archive_entry}" in
      */../*|*/..|../*) die 'Node.js archive contains parent traversal.' ;;
    esac
    case "${archive_entry}" in
      "${runtime_directory_name}"|"${runtime_directory_name}"/*) ;;
      *) die 'Node.js archive has an unexpected top-level path.' ;;
    esac
  done < <(tar -tzf "${node_archive}")

  extraction_directory="$(mktemp -d "${runtime_cache}/.node-extract.XXXXXX")"
  if ! tar --no-same-owner --no-same-permissions -xzf "${node_archive}" -C "${extraction_directory}"; then
    rm -rf -- "${extraction_directory}"
    die 'Node.js archive extraction failed.'
  fi
  candidate="${extraction_directory}/${runtime_directory_name}"
  if [[ ! -x "${candidate}/bin/node" || ! -x "${candidate}/bin/corepack" ]] || \
    [[ "$("${candidate}/bin/node" --version)" != "${expected_node}" ]]; then
    rm -rf -- "${extraction_directory}"
    die 'Extracted Node.js runtime is incomplete or has an unexpected version.'
  fi
  mv "${candidate}" "${runtime_directory}"
  rmdir "${extraction_directory}"
  PATH="${runtime_directory}/bin:${PATH}"
  export PATH
}

ensure_pinned_node_runtime

# Rocky 8.6 may not provide Python 3.12 from the configured AppStream mirror.
# This privileged helper installs only the pinned runtime; the application
# build itself remains under the invoking normal user.
python_runtime_bin='/opt/simdashboard/runtime/python/bin/python3.12'
sudo --preserve-env=HTTP_PROXY,HTTPS_PROXY,NO_PROXY,http_proxy,https_proxy,no_proxy,CURL_CA_BUNDLE,SSL_CERT_FILE \
  "${script_root}/bootstrap-python-runtime.sh"
sudo test -x "${python_runtime_bin}" || \
  die "Pinned Python runtime was not created: ${python_runtime_bin}"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/simdashboard-deploy.XXXXXX")"
extract_dir="${work_dir}/extracted"
mkdir -p "${extract_dir}"
cleanup() { rm -rf -- "${work_dir}"; }
trap cleanup EXIT

build_args=(--output "${work_dir}/release.tar.gz")
[[ "${with_wheels}" == 1 ]] && build_args+=(--with-wheels)
[[ "${allow_dirty}" == 1 ]] && build_args+=(--allow-dirty)
PYTHON_BIN="${python_runtime_bin}" "${script_root}/build-release.sh" "${build_args[@]}"

printf '%s\n' 'Verifying release archive checksum.'
(cd "${work_dir}" && sha256sum --check --strict --quiet release.tar.gz.sha256) || \
  die 'Release archive checksum verification failed.'

# Refuse absolute paths and parent traversal before handing the archive to tar.
while IFS= read -r archive_entry; do
  [[ "${archive_entry}" != /* && "${archive_entry}" != *$'\n'* && "${archive_entry}" != *$'\r'* ]] || \
    die 'Release archive contains an unsafe path.'
  case "/${archive_entry}" in
    */../*|*/..|../*) die 'Release archive contains parent traversal.' ;;
  esac
done < <(tar -tzf "${work_dir}/release.tar.gz")

tar -xzf "${work_dir}/release.tar.gz" -C "${extract_dir}"
mapfile -t bundle_roots < <(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d -print)
[[ "${#bundle_roots[@]}" -eq 1 ]] || die 'Release archive must contain exactly one bundle directory.'
bundle_root="${bundle_roots[0]}"
[[ -f "${bundle_root}/SHA256SUMS" && -x "${bundle_root}/install.sh" ]] || \
  die 'Extracted release bundle is incomplete.'
printf '%s\n' 'Verifying extracted release contents.'
(cd "${bundle_root}" && sha256sum --check --strict --quiet SHA256SUMS) || \
  die 'Extracted release checksum verification failed.'

# The config copy begins only after all build and checksum work succeeds. sudo
# was authenticated after the clean-worktree gate, before runtime bootstrap;
# no secret is placed in an argument, environment variable, or generated log.
root_config='/root/simdashboard-install.env'
sudo test ! -L "${root_config}" || die 'Root installation configuration must not be a symbolic link.'
sudo install -o root -g root -m 0600 "${config_file}" "${root_config}"
if grep -Eq '^[[:space:]]*(export[[:space:]]+)?PYTHON_BIN[[:space:]]*=' "${config_file}"; then
  # The installer treats its config as a trusted root-owned shell file. Honor
  # an explicit path only when it is the pinned helper runtime used to build
  # this bundle, so wheel creation and installation cannot drift apart.
  sudo env CONFIG_FILE="${root_config}" EXPECTED_PYTHON_BIN="${python_runtime_bin}" \
    bash -c 'source "${CONFIG_FILE}"; [[ "${PYTHON_BIN:-}" == "${EXPECTED_PYTHON_BIN}" ]]' >/dev/null || \
    die "PYTHON_BIN, when configured, must equal ${python_runtime_bin}."
else
  printf 'PYTHON_BIN=%q\n' "${python_runtime_bin}" | sudo tee -a "${root_config}" >/dev/null
fi
sudo test "$(sudo stat -c '%u:%g:%a' "${root_config}")" = '0:0:600' || \
  die 'Root installation configuration has unexpected ownership or mode.'

printf '%s\n' 'Running installer preflight.'
sudo "${bundle_root}/install.sh" --config "${root_config}" --check
printf '%s\n' 'Installing and starting services.'
sudo "${bundle_root}/install.sh" --config "${root_config}"

sudo systemctl is-active --quiet nginx.service || die 'nginx.service is not active.'
sudo systemctl is-active --quiet simdashboard.service || die 'simdashboard.service is not active.'
sudo /usr/local/sbin/simdashboard-healthcheck >/dev/null || die 'Application health check failed.'
sudo nginx -t >/dev/null || die 'nginx configuration test failed.'
printf '%s\n' 'Rocky deployment completed; nginx and simdashboard are healthy.'
