#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"

fail() { printf '[setup-e2e] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || fail "Run this script inside WSL."
command -v pnpm >/dev/null || fail "pnpm was not found. Run ./setup-wsl.sh first."
[[ -d "${project_root}/frontend/node_modules" ]] || fail "Frontend dependencies are missing. Run ./setup-wsl.sh first."

if [[ "${1:-}" == "--install-system-deps" ]]; then
  command -v sudo >/dev/null || fail "sudo is required to install Ubuntu browser libraries."
  sudo env "PATH=${PATH}" pnpm --dir "${project_root}/frontend" exec playwright install-deps chromium
elif [[ $# -gt 0 ]]; then
  fail "Unknown option: $1"
fi

pnpm --dir "${project_root}/frontend" exec playwright install chromium

(
  cd "${project_root}/frontend"
  node --input-type=module -e "import { chromium } from '@playwright/test'; const browser = await chromium.launch({ headless: true }); await browser.close();"
)

printf '[setup-e2e] WSL Chromium launch check passed.\n'
