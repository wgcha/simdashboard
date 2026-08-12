import { spawn, spawnSync } from 'node:child_process'
import { rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const workspaceDir = path.resolve(frontendDir, '..')
const python = process.env.E2E_PYTHON ?? (
  process.platform === 'win32'
    ? path.join(workspaceDir, '.venv-runtime', 'Scripts', 'python.exe')
    : 'python'
)
const children = []
const e2eDatabase = path.join(workspaceDir, 'backend', 'data', 'e2e-playwright.duckdb')
const e2ePassword = 'e2e-validation-password'
const e2eSecret = 'e2e-secret-key-that-is-at-least-32-characters'

function prepareUser(username, displayName, role, extraArgs = []) {
  const result = spawnSync(python, [
    path.join(workspaceDir, 'backend', 'scripts', 'create_user.py'),
    '--username', username, '--display-name', displayName, '--role', role, '--replace', ...extraArgs,
  ], {
    cwd: workspaceDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      ANALYSIS_DB_BACKEND: 'duckdb',
      ANALYSIS_DUCKDB_PATH: e2eDatabase,
      SIM_DASH_USER_PASSWORD: e2ePassword,
    },
  })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

function launch(command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: workspaceDir,
    stdio: 'inherit',
    detached: process.platform !== 'win32',
    ...options,
  })
  children.push(child)
  return child
}

function terminate(child) {
  if (!child.pid || child.exitCode !== null) return
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', timeout: 5_000 })
    return
  }
  try { process.kill(-child.pid, 'SIGTERM') } catch { /* already stopped */ }
}

async function waitFor(url, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch { /* server is starting */ }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`E2E server did not become ready: ${url}`)
}

function waitForExit(child) {
  return new Promise((resolve, reject) => {
    child.once('error', reject)
    child.once('exit', (code, signal) => resolve(code ?? (signal ? 1 : 0)))
  })
}

async function main() {
  rmSync(e2eDatabase, { force: true })
  rmSync(`${e2eDatabase}.wal`, { force: true })
  prepareUser('e2e-admin', 'E2E 관리자', 'admin')
  prepareUser('e2e-viewer', 'E2E 조회자', 'viewer')
  prepareUser('e2e-power', 'E2E 파워 사용자', 'editor')
  prepareUser('e2e-project-admin', 'E2E 프로젝트 관리자', 'viewer', ['--project-role', 'admin', '--no-global-admin'])
  const backend = launch(python, [
    '-m', 'uvicorn', 'app.main:app', '--app-dir', path.join(workspaceDir, 'backend'),
    '--host', '127.0.0.1', '--port', '18000',
  ], {
    env: {
      ...process.env,
      ANALYSIS_DB_BACKEND: 'duckdb',
      ANALYSIS_DUCKDB_PATH: e2eDatabase,
      AUTH_MODE: 'password',
      AUTH_SECRET_KEY: e2eSecret,
    },
  })
  const vite = launch(process.execPath, [
    path.join(frontendDir, 'node_modules', 'vite', 'bin', 'vite.js'),
    '--host', '127.0.0.1', '--port', '15173',
  ], {
    cwd: frontendDir,
    env: { ...process.env, VITE_API_TARGET: 'http://127.0.0.1:18000' },
  })

  await Promise.all([
    waitFor('http://127.0.0.1:18000/api/health'),
    waitFor('http://127.0.0.1:15173'),
  ])
  if (backend.exitCode !== null || vite.exitCode !== null) {
    throw new Error('E2E server exited before the tests started.')
  }

  const playwright = launch(process.execPath, [
    path.join(frontendDir, 'node_modules', '@playwright', 'test', 'cli.js'), 'test',
    ...process.argv.slice(2),
  ], { cwd: frontendDir })
  return await waitForExit(playwright)
}

let exitCode = 1
try {
  exitCode = await main()
} finally {
  for (const child of children.reverse()) terminate(child)
}
process.exit(exitCode)
