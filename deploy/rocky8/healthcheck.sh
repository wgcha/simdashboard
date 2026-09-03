#!/usr/bin/env bash
set -euo pipefail

health_url="${SIMDASH_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
payload="$(curl --fail --silent --show-error --max-time 5 "${health_url}")"
case "${payload}" in
  *'"status":"ok"'*'"database_backend":"postgresql"'*|*'"database_backend":"postgresql"'*'"status":"ok"'*)
    printf '%s\n' "ROCKY8_HEALTHCHECK_OK"
    ;;
  *)
    printf '%s\n' "Unexpected health payload: ${payload}" >&2
    exit 1
    ;;
esac
