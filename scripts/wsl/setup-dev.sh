#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
local_bin="${HOME}/.local/bin"
tool_root="${HOME}/.local/share/analysis-canvas"
uv_version="0.11.32"
pnpm_version="11.15.1"

log() { printf '\n[setup-wsl] %s\n' "$*"; }
fail() { printf '\n[setup-wsl] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || fail "This installer must run inside WSL/Linux."
for command_name in curl git tar xz sha256sum; do
  command -v "${command_name}" >/dev/null || fail "Missing prerequisite: ${command_name}. Install it with the Ubuntu package manager, then retry."
done

case "${project_root}" in
  /mnt/*) printf '[setup-wsl] WARNING: the repository is on a Windows-mounted drive (%s).\n' "${project_root}" >&2
          printf '[setup-wsl]          For faster installs and file watching, use ~/src/simulation_dashboard.\n' >&2 ;;
esac

mkdir -p "${local_bin}" "${tool_root}"
export PATH="${local_bin}:${PATH}"

if ! command -v uv >/dev/null || [[ "$(uv --version 2>/dev/null || true)" != "uv ${uv_version}"* ]]; then
  log "Installing uv ${uv_version} in ${local_bin}"
  curl -LsSf "https://astral.sh/uv/${uv_version}/install.sh" | env UV_INSTALL_DIR="${local_bin}" UV_NO_MODIFY_PATH=1 sh
fi

node_version="$(tr -d '[:space:]' < "${project_root}/.node-version")"
case "$(uname -m)" in
  x86_64) node_arch="x64" ;;
  aarch64|arm64) node_arch="arm64" ;;
  *) fail "Unsupported CPU architecture: $(uname -m)" ;;
esac
node_archive="node-${node_version}-linux-${node_arch}.tar.xz"
node_home="${tool_root}/node-${node_version}-linux-${node_arch}"

if [[ ! -x "${node_home}/bin/node" ]]; then
  log "Installing Node.js ${node_version} (${node_arch}) with checksum verification"
  temp_dir="$(mktemp -d)"
  cleanup() { rm -rf -- "${temp_dir}"; }
  trap cleanup EXIT
  curl -fsSLo "${temp_dir}/SHASUMS256.txt" "https://nodejs.org/dist/${node_version}/SHASUMS256.txt"
  curl -fsSLo "${temp_dir}/${node_archive}" "https://nodejs.org/dist/${node_version}/${node_archive}"
  expected_checksum="$(awk -v filename="${node_archive}" '$2 == filename { print $1 }' "${temp_dir}/SHASUMS256.txt")"
  [[ -n "${expected_checksum}" ]] || fail "Node.js checksum was not published for ${node_archive}."
  actual_checksum="$(sha256sum "${temp_dir}/${node_archive}" | awk '{ print $1 }')"
  [[ "${actual_checksum}" == "${expected_checksum}" ]] || fail "Node.js checksum verification failed."
  tar -xJf "${temp_dir}/${node_archive}" -C "${temp_dir}"
  mv "${temp_dir}/node-${node_version}-linux-${node_arch}" "${node_home}"
fi

for executable in node npm npx corepack; do
  ln -sfn "${node_home}/bin/${executable}" "${local_bin}/${executable}"
done
hash -r

log "Activating pnpm ${pnpm_version} through Corepack"
corepack enable --install-directory "${local_bin}"
corepack prepare "pnpm@${pnpm_version}" --activate

python_version="$(tr -d '[:space:]' < "${project_root}/.python-version")"
log "Installing managed Python ${python_version} and creating .venv-wsl"
uv python install "${python_version}"
if [[ ! -x "${project_root}/.venv-wsl/bin/python" ]]; then
  uv venv --python "${python_version}" "${project_root}/.venv-wsl"
fi
uv pip install --python "${project_root}/.venv-wsl/bin/python" -r "${project_root}/backend/requirements.txt"

log "Installing frontend dependencies from the lockfile"
CI=true pnpm --dir "${project_root}/frontend" install --frozen-lockfile

log "Running environment checks"
"${project_root}/scripts/wsl/doctor.sh"

printf '\nWSL development environment is ready.\n'
printf 'Start:  cd %q && ./start.sh\n' "${project_root}"
printf 'Stop:   cd %q && ./stop.sh\n' "${project_root}"
