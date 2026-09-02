#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
export COREPACK_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}/node/corepack"
export COREPACK_DEFAULT_TO_LATEST=0

failed=0
check() {
  local label="$1"
  shift
  if output="$("$@" 2>&1)"; then
    printf '[ok]   %-24s %s\n' "${label}" "$(printf '%s' "${output}" | head -n 1)"
  else
    printf '[fail] %-24s %s\n' "${label}" "$(printf '%s' "${output}" | head -n 1)" >&2
    failed=1
  fi
}

check_exact() {
  local label="$1"
  local expected="$2"
  shift 2
  if output="$("$@" 2>&1)" && [[ "${output}" == "${expected}" ]]; then
    printf '[ok]   %-24s %s\n' "${label}" "${output}"
  else
    printf '[fail] %-24s expected %s, got %s\n' "${label}" "${expected}" "${output:-<command failed>}" >&2
    failed=1
  fi
}

check_lf_file() {
  local path="$1"
  local label="$2"
  if [[ ! -f "${path}" ]]; then
    printf '[warn] %-24s not present (optional)\n' "${label}"
  elif LC_ALL=C grep -q $'\r' "${path}"; then
    printf '[fail] %-24s CRLF detected; convert to LF before running WSL scripts\n' "${label}" >&2
    failed=1
  else
    printf '[ok]   %-24s LF\n' "${label}"
  fi
}

expected_node="$(tr -d '[:space:]' < "${project_root}/.node-version")"
expected_python="$(tr -d '[:space:]' < "${project_root}/.python-version")"
expected_pnpm="$({
  node -e '
    const fs = require("node:fs")
    const packageManager = JSON.parse(fs.readFileSync(process.argv[1], "utf8")).packageManager
    const match = /^pnpm@(.+)$/.exec(packageManager ?? "")
    if (!match) process.exit(1)
    process.stdout.write(match[1])
  ' "${project_root}/frontend/package.json"
})"

printf 'Analysis Canvas WSL doctor\n'
printf 'repository: %s\n' "${project_root}"
case "${project_root}" in
  /mnt/*) printf '[warn] repository filesystem    Windows mount; ~/src is recommended\n' ;;
  *) printf '[ok]   repository filesystem    WSL native filesystem\n' ;;
esac

check_lf_file "${project_root}/.env" ".env line endings"

check_exact "node version" "${expected_node}" node --version
check_exact "pnpm offline version" "${expected_pnpm}" env COREPACK_ENABLE_NETWORK=0 pnpm --version
check "uv" uv --version
check_exact "python version" "${expected_python}" "${project_root}/.venv-wsl/bin/python" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
check "backend imports" "${project_root}/.venv-wsl/bin/python" -c "import fastapi, duckdb, sqlalchemy; print('fastapi/duckdb/sqlalchemy')"
check "frontend lockfile" test -f "${project_root}/frontend/pnpm-lock.yaml"
check "python lockfile" test -f "${project_root}/backend/requirements.lock"
check "TestClient/uvloop" "${project_root}/.venv-wsl/bin/python" "${project_root}/backend/scripts/check_testclient_compatibility.py"

(( failed == 0 )) || exit 1
