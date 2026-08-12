#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"

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

printf 'Analysis Canvas WSL doctor\n'
printf 'repository: %s\n' "${project_root}"
case "${project_root}" in
  /mnt/*) printf '[warn] repository filesystem    Windows mount; ~/src is recommended\n' ;;
  *) printf '[ok]   repository filesystem    WSL native filesystem\n' ;;
esac

check "node" node --version
check "pnpm" pnpm --version
check "uv" uv --version
check "python" "${project_root}/.venv-wsl/bin/python" --version
check "backend imports" "${project_root}/.venv-wsl/bin/python" -c "import fastapi, duckdb, sqlalchemy; print('fastapi/duckdb/sqlalchemy')"
check "frontend lockfile" test -f "${project_root}/frontend/pnpm-lock.yaml"

(( failed == 0 )) || exit 1
