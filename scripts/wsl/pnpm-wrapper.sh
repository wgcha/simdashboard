#!/usr/bin/env bash
set -euo pipefail

# WSL imports Windows' LOCALAPPDATA by default. Corepack otherwise tries to
# create its Linux cache below /mnt/c and can miss the already prepared pnpm.
export COREPACK_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}/node/corepack"
export COREPACK_DEFAULT_TO_LATEST=0

exec corepack pnpm "$@"
