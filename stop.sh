#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pid_file="${project_root}/.server-pids.env"
[[ -f "${pid_file}" ]] || { echo "No Analysis Canvas PID file found."; exit 0; }
# shellcheck disable=SC1090
source "${pid_file}"

stop_owned_process() {
  local pid="$1"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 0
  [[ -r "/proc/${pid}/cmdline" ]] || return 0
  local command_line
  command_line="$(tr '\0' ' ' <"/proc/${pid}/cmdline")"
  local process_cwd
  process_cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
  [[ "${process_cwd}" == "${project_root}"* || "${command_line}" == *"${project_root}"* || "${command_line}" == *"uvicorn app.main:app"* ]] || {
    echo "Refusing to stop PID ${pid}: it does not belong to this workspace." >&2
    return 1
  }
  kill "${pid}" 2>/dev/null || true
  for _ in {1..20}; do
    kill -0 "${pid}" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -9 "${pid}" 2>/dev/null || true
}

stop_owned_process "${BACKEND_PID:-}"
stop_owned_process "${FRONTEND_PID:-}"
rm -f "${pid_file}"
echo "Analysis Canvas servers stopped successfully."
