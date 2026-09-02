#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
export COREPACK_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}/node/corepack"
export COREPACK_DEFAULT_TO_LATEST=0
python_bin="${project_root}/.venv-runtime/bin/python"
[[ -x "${project_root}/.venv-wsl/bin/python" ]] && python_bin="${project_root}/.venv-wsl/bin/python"
[[ -x "${python_bin}" ]] || python_bin="${project_root}/.venv/bin/python"
[[ -x "${python_bin}" ]] || { echo "Python virtual environment not found. On WSL run ./setup-wsl.sh first." >&2; exit 1; }
command -v pnpm >/dev/null || { echo "pnpm was not found. On WSL run ./setup-wsl.sh first." >&2; exit 1; }
pid_file="${project_root}/.server-pids.env"
[[ ! -f "${pid_file}" ]] || { echo "PID file already exists. Run ./stop.sh first." >&2; exit 1; }

(
  cd "${project_root}/backend"
  nohup "${python_bin}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >uvicorn.log 2>uvicorn-error.log &
  echo "$!" >"${project_root}/.backend.pid"
)
(
  cd "${project_root}/frontend"
  nohup pnpm run dev >vite.log 2>vite-error.log &
  echo "$!" >"${project_root}/.frontend.pid"
)
backend_pid="$(cat "${project_root}/.backend.pid")"
frontend_pid="$(cat "${project_root}/.frontend.pid")"
rm -f "${project_root}/.backend.pid" "${project_root}/.frontend.pid"
printf 'BACKEND_PID=%s\nFRONTEND_PID=%s\n' "${backend_pid}" "${frontend_pid}" >"${pid_file}"
echo "Analysis Canvas started: dashboard=http://127.0.0.1:5173 api=http://127.0.0.1:8000/docs"
