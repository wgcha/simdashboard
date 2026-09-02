#!/usr/bin/env bash
set -euo pipefail

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_root}/../.." && pwd)"
release_id=""
output=""
build_frontend=1
with_wheels=0
allow_dirty=0

usage() {
  cat <<'EOF'
Usage: deploy/rocky8/build-release.sh [options]

Options:
  --release-id ID          Immutable release identifier (default: UTC time + git SHA)
  --output FILE            Output .tar.gz path (default: dist/<release>.tar.gz)
  --skip-frontend-build    Package the existing frontend/dist directory
  --with-wheels            Include an offline Python wheelhouse for this build OS/arch
  --allow-dirty            Permit a bundle from a dirty Git worktree
  -h, --help               Show this help

For an offline production bundle, run --with-wheels on Rocky Linux 8 with the
same architecture and Python 3.12 minor version as the target server.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-id) release_id="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --skip-frontend-build) build_frontend=0; shift ;;
    --with-wheels) with_wheels=1; shift ;;
    --allow-dirty) allow_dirty=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

for command_name in tar sha256sum git; do
  command -v "${command_name}" >/dev/null 2>&1 || {
    printf 'Required command not found: %s\n' "${command_name}" >&2
    exit 1
  }
done

if [[ "${allow_dirty}" != 1 ]] && [[ -n "$(git -C "${project_root}" status --porcelain)" ]]; then
  printf '%s\n' 'Refusing to build from a dirty Git worktree. Commit/stash changes or pass --allow-dirty.' >&2
  exit 1
fi

git_sha="$(git -C "${project_root}" rev-parse --short=12 HEAD 2>/dev/null || printf unknown)"
if [[ -z "${release_id}" ]]; then
  release_id="$(date -u +%Y%m%dT%H%M%SZ)-${git_sha}"
  [[ "${allow_dirty}" != 1 ]] || release_id="${release_id}-dirty"
fi
[[ "${release_id}" =~ ^[A-Za-z0-9._-]+$ ]] || {
  printf '%s\n' 'Release ID may contain only letters, numbers, dot, underscore, and hyphen.' >&2
  exit 1
}

if [[ "${build_frontend}" == 1 ]]; then
  command -v node >/dev/null 2>&1 || { printf '%s\n' 'Node.js is required to build the frontend.' >&2; exit 1; }
  command -v corepack >/dev/null 2>&1 || { printf '%s\n' 'corepack is required to run the pinned pnpm version.' >&2; exit 1; }
  expected_node="$(tr -d '[:space:]' <"${project_root}/.node-version")"
  actual_node="$(node --version)"
  [[ "${actual_node}" == "${expected_node}" ]] || {
    printf 'Node.js version mismatch: expected=%s actual=%s\n' "${expected_node}" "${actual_node}" >&2
    exit 1
  }
  build_corepack_home="${SIMDASH_COREPACK_HOME:-${XDG_CACHE_HOME:-${HOME}/.cache}/node/corepack}"
  mkdir -p "${build_corepack_home}"
  (
    cd "${project_root}/frontend"
    env COREPACK_HOME="${build_corepack_home}" COREPACK_DEFAULT_TO_LATEST=0 corepack pnpm install --frozen-lockfile
    env COREPACK_HOME="${build_corepack_home}" COREPACK_DEFAULT_TO_LATEST=0 corepack pnpm run build
  )
fi

[[ -f "${project_root}/frontend/dist/index.html" ]] || {
  printf '%s\n' 'frontend/dist/index.html is missing; build the frontend first.' >&2
  exit 1
}

if [[ -z "${output}" ]]; then
  output="${project_root}/dist/simdashboard-rocky8-${release_id}.tar.gz"
elif [[ "${output}" != /* ]]; then
  output="$(pwd)/${output}"
fi
mkdir -p "$(dirname "${output}")"
[[ ! -e "${output}" && ! -e "${output}.sha256" ]] || {
  printf 'Refusing to overwrite an existing release artifact: %s\n' "${output}" >&2
  exit 1
}

stage_root="$(mktemp -d)"
trap 'rm -rf -- "${stage_root}"' EXIT
bundle_name="simdashboard-rocky8-${release_id}"
bundle_root="${stage_root}/${bundle_name}"
payload_root="${bundle_root}/payload"
mkdir -p "${payload_root}/backend" "${payload_root}/frontend"

cp "${script_root}/install.sh" "${script_root}/install.env.example" "${script_root}/healthcheck.sh" "${bundle_root}/"
cp -a "${script_root}/systemd" "${script_root}/nginx" "${bundle_root}/"
cp "${project_root}/.python-version" "${payload_root}/"
cp -a "${project_root}/frontend/dist" "${payload_root}/frontend/"
cp -a "${project_root}/backend/app" "${project_root}/backend/migrations" \
  "${project_root}/backend/scripts" "${project_root}/backend/public_assets" \
  "${payload_root}/backend/"
mkdir -p "${payload_root}/backend/assets"
shopt -s dotglob nullglob
for asset_item in "${project_root}/backend/assets/"*; do
  # Imported business/runtime media belongs in PostgreSQL or a separate transfer
  # artifact, never in an application release bundle.
  [[ "$(basename "${asset_item}")" == imports ]] || cp -a "${asset_item}" "${payload_root}/backend/assets/"
done
shopt -u dotglob nullglob
cp "${project_root}/backend/alembic.ini" "${project_root}/backend/requirements.txt" \
  "${project_root}/backend/requirements.lock" "${payload_root}/backend/"
if [[ -d "${project_root}/video_example" ]]; then
  cp -a "${project_root}/video_example" "${payload_root}/"
fi
if [[ -d "${project_root}/examples" ]]; then
  cp -a "${project_root}/examples" "${payload_root}/"
fi

find "${payload_root}" -type f \( -name '*.pyc' -o -name '*.log' \) -delete
find "${payload_root}" -depth -type d \( -name '__pycache__' -o -name '.pytest_cache' \) -exec rmdir {} + 2>/dev/null || true
printf '%s\n' "${release_id}" >"${bundle_root}/RELEASE_ID"
printf 'git_sha=%s\npython=%s\nnode=%s\ncreated_at=%s\n' \
  "${git_sha}" "$(tr -d '[:space:]' <"${project_root}/.python-version")" \
  "$(tr -d '[:space:]' <"${project_root}/.node-version")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  >"${bundle_root}/RELEASE_MANIFEST"

if [[ "${with_wheels}" == 1 ]]; then
  command -v python3.12 >/dev/null 2>&1 || {
    printf '%s\n' 'python3.12 is required to create the offline wheelhouse.' >&2
    exit 1
  }
  actual_python="$(python3.12 -c 'import platform; print(platform.python_version())')"
  expected_python="$(tr -d '[:space:]' <"${project_root}/.python-version")"
  [[ "${actual_python}" == "${expected_python}" ]] || {
    printf 'Python version mismatch for wheelhouse: expected=%s actual=%s\n' "${expected_python}" "${actual_python}" >&2
    exit 1
  }
  mkdir -p "${bundle_root}/wheelhouse"
  python3.12 -m pip download \
    --only-binary=:all: \
    --requirement "${project_root}/backend/requirements.lock" \
    --dest "${bundle_root}/wheelhouse"
fi

(
  cd "${bundle_root}"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS
)
tar -C "${stage_root}" -czf "${output}" "${bundle_name}"
(
  cd "$(dirname "${output}")"
  sha256sum "$(basename "${output}")" >"$(basename "${output}").sha256"
)
printf 'Rocky 8 release bundle created: %s\n' "${output}"
printf 'Transfer checksum created: %s.sha256\n' "${output}"
