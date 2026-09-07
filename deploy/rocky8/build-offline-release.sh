#!/usr/bin/env bash
# Build a completely network-independent Rocky 8.6 x86_64 deployment bundle.
# Run this only in a clean Rocky Linux 8.6 x86_64 builder with internet access.
set -Eeuo pipefail
umask 027

readonly PYTHON_VERSION='3.12.13'
readonly PYTHON_ARCHIVE='cpython-3.12.13+20260414-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'
readonly PYTHON_URL='https://github.com/astral-sh/python-build-standalone/releases/download/20260414/cpython-3.12.13%2B20260414-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz'
readonly PYTHON_SHA256='3c3427e5628648478da2aa227472c350475a68bc58109f1b43849636a4aecb89'
readonly VAULT_BASE='https://dl.rockylinux.org/vault/rocky/8.6'

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_root}/../.." && pwd)"
release_id=''
output=''
skip_frontend_build=0
allow_dirty=0

die() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: deploy/rocky8/build-offline-release.sh [options]

Builds an immutable, self-contained Rocky Linux 8.6 x86_64 bundle. It includes
the pinned Python runtime, an offline wheelhouse, and the complete RPM closure
for the OS packages used by install.sh. PostgreSQL 18, TLS certificates, the
root-owned configuration file, and systemd itself remain target prerequisites.

Options:
  --release-id ID          Immutable release identifier (default: UTC time + git SHA)
  --output FILE            Output .tar.gz path (default: dist/simdashboard-rocky8-offline-<release>.tar.gz)
  --skip-frontend-build    Package an already-built frontend/dist (for CI)
  --allow-dirty            Permit a development build from a dirty worktree
  -h, --help               Show this help

The builder must be Rocky Linux 8.6 x86_64 and have dnf-plugins-core and
createrepo_c installed beforehand. RPM repositories are overridden only for
the download command and are pinned to the Rocky 8.6 vault.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-id) release_id="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --skip-frontend-build) skip_frontend_build=1; shift ;;
    --allow-dirty) allow_dirty=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "Unknown option: $1" ;;
  esac
done

for command_name in awk curl dnf find git mktemp rpm rpmkeys sha256sum tar createrepo_c; do
  command -v "${command_name}" >/dev/null 2>&1 || die "Required builder command not found: ${command_name}"
done
rpm -q dnf-plugins-core createrepo_c >/dev/null 2>&1 || \
  die 'Builder prerequisites are missing: install dnf-plugins-core and createrepo_c before building.'

[[ "$(uname -m)" == x86_64 ]] || die "This builder must be x86_64; detected $(uname -m)."
[[ -r /etc/os-release ]] || die '/etc/os-release is missing.'
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == rocky && "${VERSION_ID:-}" == 8.6 ]] || \
  die "This builder must be Rocky Linux 8.6 exactly; detected ${PRETTY_NAME:-unknown}."

if [[ "${allow_dirty}" != 1 ]] && [[ -n "$(git -C "${project_root}" status --porcelain)" ]]; then
  die 'Refusing to build from a dirty Git worktree. Commit/stash changes or pass --allow-dirty for development only.'
fi

git_sha="$(git -C "${project_root}" rev-parse --short=12 HEAD 2>/dev/null || printf unknown)"
if [[ -z "${release_id}" ]]; then
  release_id="$(date -u +%Y%m%dT%H%M%SZ)-${git_sha}"
  [[ "${allow_dirty}" != 1 ]] || release_id="${release_id}-dirty"
fi
[[ "${release_id}" =~ ^[A-Za-z0-9._-]+$ ]] || die 'Release ID may contain only letters, numbers, dot, underscore, and hyphen.'

if [[ -z "${output}" ]]; then
  output="${project_root}/dist/simdashboard-rocky8-offline-${release_id}.tar.gz"
elif [[ "${output}" != /* ]]; then
  output="$(pwd)/${output}"
fi
mkdir -p "$(dirname "${output}")"
[[ ! -e "${output}" && ! -e "${output}.sha256" ]] || die "Refusing to overwrite existing artifact: ${output}"

[[ -f "${script_root}/install-offline.sh" ]] || \
  die 'install-offline.sh is missing; build the offline installer before packaging.'
[[ -r /etc/pki/rpm-gpg/RPM-GPG-KEY-rockyofficial ]] || \
  die 'Builder Rocky 8 signing key is missing: /etc/pki/rpm-gpg/RPM-GPG-KEY-rockyofficial'

stage_root="$(mktemp -d)"
trap 'rm -rf -- "${stage_root}"' EXIT
runtime_archive="${stage_root}/${PYTHON_ARCHIVE}"
curl --fail --location --silent --show-error --retry 3 --output "${runtime_archive}" "${PYTHON_URL}"
actual_sha="$(sha256sum "${runtime_archive}" | awk '{print $1}')"
[[ "${actual_sha}" == "${PYTHON_SHA256}" ]] || die 'Pinned Python runtime checksum verification failed.'

# Verify archive paths before extraction. The archive is not executed or
# unpacked until the official SHA-256 has matched.
while IFS= read -r entry; do
  [[ "${entry}" != /* && "${entry}" != *$'\n'* && "${entry}" != *$'\r'* ]] || die "Unsafe path in Python archive: ${entry}"
  case "/${entry}/" in */../*) die "Unsafe path in Python archive: ${entry}" ;; esac
done < <(tar -tzf "${runtime_archive}")
runtime_extract="${stage_root}/python-extract"
mkdir -p "${runtime_extract}"
tar -xzf "${runtime_archive}" -C "${runtime_extract}" --no-same-owner --no-same-permissions
python_bin="${runtime_extract}/python/bin/python3.12"
[[ -x "${python_bin}" ]] || die 'Verified Python archive did not contain python/bin/python3.12.'
[[ "$("${python_bin}" -c 'import platform; print(platform.python_version())')" == "${PYTHON_VERSION}" ]] || \
  die 'Extracted Python runtime has an unexpected version.'

# Let the existing release builder own payload selection and wheel resolution.
# Its clean-worktree gate remains effective unless this script was explicitly
# invoked with --allow-dirty.
base_archive="${stage_root}/base-release.tar.gz"
base_args=(--release-id "${release_id}" --output "${base_archive}" --with-wheels)
[[ "${skip_frontend_build}" == 1 ]] && base_args+=(--skip-frontend-build)
[[ "${allow_dirty}" == 1 ]] && base_args+=(--allow-dirty)
PYTHON_BIN="${python_bin}" "${script_root}/build-release.sh" "${base_args[@]}"

tar -xzf "${base_archive}" -C "${stage_root}" --no-same-owner --no-same-permissions
bundle_name="simdashboard-rocky8-${release_id}"
bundle_root="${stage_root}/${bundle_name}"
[[ -d "${bundle_root}/payload" && -d "${bundle_root}/wheelhouse" ]] || die 'Base release payload is incomplete.'
mv "${bundle_root}" "${stage_root}/simdashboard-rocky8-offline-${release_id}"
bundle_name="simdashboard-rocky8-offline-${release_id}"
bundle_root="${stage_root}/${bundle_name}"

mkdir -p "${bundle_root}/runtime" "${bundle_root}/rpm-repo"
cp "${runtime_archive}" "${bundle_root}/runtime/python.tar.gz"
cp "${script_root}/install-offline.sh" "${bundle_root}/install-offline.sh"
cp /etc/pki/rpm-gpg/RPM-GPG-KEY-rockyofficial "${bundle_root}/rpm-repo/RPM-GPG-KEY-Rocky-8"
if [[ -f "${script_root}/OFFLINE-README.ko.md" ]]; then
  cp "${script_root}/OFFLINE-README.ko.md" "${bundle_root}/OFFLINE-README.ko.md"
fi

# Do not alter the builder's enabled repositories or installed packages. This
# transient DNF invocation resolves every dependency against only Rocky 8.6
# vault BaseOS, AppStream, and extras (including already-installed packages).
dnf -q download --resolve --alldeps --destdir "${bundle_root}/rpm-repo" \
  --disablerepo='*' --releasever=8.6 --setopt=module_platform_id=platform:el8 \
  --setopt=install_weak_deps=False \
  --repofrompath=rocky86-baseos,"${VAULT_BASE}/BaseOS/x86_64/os/" \
  --repofrompath=rocky86-appstream,"${VAULT_BASE}/AppStream/x86_64/os/" \
  --repofrompath=rocky86-extras,"${VAULT_BASE}/extras/x86_64/os/" \
  nginx curl ca-certificates tar findutils shadow-utils policycoreutils-python-utils firewalld

rpm_count=0
while IFS= read -r -d '' rpm_file; do
  rpm_count=$((rpm_count + 1))
  # Capture first: grep -q in a pipe can SIGPIPE rpmkeys under pipefail even
  # when a valid signature was found before the remaining digest output.
  if ! signature_output="$(rpmkeys --checksig --verbose "${rpm_file}" 2>&1)"; then
    printf '%s\n' "${signature_output}" >&2
    die "RPM signature verification failed: $(basename "${rpm_file}")"
  fi
  if ! grep -Eqi '(pgp|rsa|dsa).*ok' <<<"${signature_output}"; then
    printf '%s\n' "${signature_output}" >&2
    die "RPM signature missing: $(basename "${rpm_file}")"
  fi
done < <(find "${bundle_root}/rpm-repo" -maxdepth 1 -type f -name '*.rpm' -print0)
(( rpm_count > 0 )) || die 'DNF downloaded no RPMs.'
createrepo_c --quiet "${bundle_root}/rpm-repo"

# Prove the pinned runtime and wheelhouse work without PyPI or an index. This
# catches accidental source-only wheels and missing OpenSSL/SQLite modules on
# the same Rocky 8.6 ABI that will run the release.
smoke_venv="${stage_root}/wheel-smoke"
"${python_bin}" -m venv "${smoke_venv}"
"${smoke_venv}/bin/python" -m pip install --no-index --find-links "${bundle_root}/wheelhouse" \
  --requirement "${bundle_root}/payload/backend/requirements.lock"
"${smoke_venv}/bin/python" -c 'import ssl, sqlite3, cryptography, psycopg, fastapi, duckdb; print(ssl.OPENSSL_VERSION)'

rm -f "${bundle_root}/SHA256SUMS"
(
  cd "${bundle_root}"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS
)
tar -C "${stage_root}" -czf "${output}" "${bundle_name}"
(
  cd "$(dirname "${output}")"
  sha256sum "$(basename "${output}")" >"$(basename "${output}").sha256"
)
printf 'Rocky 8.6 offline release bundle created: %s\n' "${output}"
printf 'Transfer checksum created: %s.sha256\n' "${output}"
