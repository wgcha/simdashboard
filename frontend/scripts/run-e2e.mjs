import { spawn, spawnSync } from 'node:child_process'
import { existsSync, readdirSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const runnerPath = fileURLToPath(import.meta.url)
const frontendDir = path.resolve(path.dirname(runnerPath), '..')
const workspaceDir = path.resolve(frontendDir, '..')
const wslPython = path.join(workspaceDir, '.venv-wsl', 'bin', 'python')
const python = process.env.E2E_PYTHON ?? (
  process.platform === 'win32'
    ? path.join(workspaceDir, '.venv-runtime', 'Scripts', 'python.exe')
    : existsSync(wslPython) ? wslPython : 'python'
)
const children = []
const closedChildren = new WeakSet()
const isolatedRunnerChildren = new WeakSet()
let cancellationRequested = false
let cleanupPromise
const e2eDatabase = path.join(workspaceDir, 'backend', 'data', 'e2e-playwright.duckdb')
const e2eDirectory = path.join(frontendDir, 'e2e')
const e2eOutputDirectory = path.join(frontendDir, 'test-results')
const e2ePassword = 'e2e-validation-password'
const e2eSecret = 'e2e-secret-key-that-is-at-least-32-characters'

function specSelection(specPath) {
  const name = path.basename(specPath)
  if (!/^[a-z0-9][a-z0-9._-]*\.spec\.ts$/i.test(name)) {
    throw new Error(`Unsafe E2E spec filename: ${specPath}`)
  }
  return `e2e/${name}`
}

function specOutputDirectory(specPath) {
  const selection = specSelection(specPath)
  return path.join(e2eOutputDirectory, path.basename(selection, '.spec.ts'))
}

export function discoverE2eSpecs() {
  return readdirSync(e2eDirectory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && /^[a-z0-9][a-z0-9._-]*\.spec\.ts$/i.test(entry.name))
    .map((entry) => path.join(e2eDirectory, entry.name))
    .sort((left, right) => path.basename(left).localeCompare(path.basename(right), 'en'))
}

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
  const child = observeChild(spawn(command, args, {
    cwd: workspaceDir,
    stdio: 'inherit',
    detached: process.platform !== 'win32',
    ...options,
  }))
  children.push(child)
  return child
}

function observeChild(child) {
  child.once('close', () => closedChildren.add(child))
  return child
}

function pause(timeoutMs) {
  return new Promise((resolve) => setTimeout(resolve, timeoutMs))
}

function waitForExit(child) {
  if (closedChildren.has(child)) return Promise.resolve(child.exitCode ?? 1)
  return new Promise((resolve, reject) => {
    const onError = (error) => {
      child.off('close', onClose)
      reject(error)
    }
    const onClose = (code, signal) => {
      child.off('error', onError)
      resolve(code ?? (signal ? 1 : 0))
    }
    child.once('error', onError)
    child.once('close', onClose)
  })
}

async function exitsWithin(child, timeoutMs) {
  const result = await Promise.race([
    waitForExit(child).then(() => true, () => true),
    pause(timeoutMs).then(() => false),
  ])
  return result
}

function processIsAlive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error?.code === 'EPERM'
  }
}

function stopProcessTree(pid, signal) {
  if (process.platform === 'win32') {
    const result = spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', timeout: 5_000 })
    return `taskkill(status=${result.status ?? 'null'}, signal=${result.signal ?? 'none'}, error=${result.error?.message ?? 'none'})`
  }
  try {
    process.kill(-pid, signal)
    return `process.kill(-${pid}, ${signal})`
  } catch (error) {
    return `process.kill(-${pid}, ${signal}) error=${error instanceof Error ? error.message : String(error)}`
  }
}

export async function terminateChild(child, gracefulTimeoutMs = 5_000) {
  if (!child.pid || child.exitCode !== null || closedChildren.has(child)) return
  const attempts = [stopProcessTree(child.pid, 'SIGTERM')]
  if (await exitsWithin(child, gracefulTimeoutMs) || !processIsAlive(child.pid)) return
  attempts.push(stopProcessTree(child.pid, 'SIGKILL'))
  if (!await exitsWithin(child, 5_000) && processIsAlive(child.pid)) {
    throw new Error(`E2E child did not exit: pid ${child.pid}; ${attempts.join('; ')}`)
  }
}

async function assertPortAvailable(port) {
  await new Promise((resolve, reject) => {
    const probe = createServer()
    probe.once('error', (error) => reject(new Error(`E2E port ${port} is unavailable: ${error.message}`)))
    probe.listen(port, '127.0.0.1', () => probe.close((error) => error ? reject(error) : resolve()))
  })
}

async function waitForService(url, child, label, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode !== null || closedChildren.has(child)) {
      throw new Error(`E2E ${label} exited before becoming ready.`)
    }
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch { /* server is starting */ }
    await pause(250)
  }
  throw new Error(`E2E ${label} did not become ready: ${url}`)
}

async function runOneE2eInvocation(args) {
  await Promise.all([assertPortAvailable(18000), assertPortAvailable(15173)])
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
    '--host', '127.0.0.1', '--port', '15173', '--strictPort', '--configLoader', 'runner',
  ], {
    cwd: frontendDir,
    env: { ...process.env, VITE_API_TARGET: 'http://127.0.0.1:18000' },
  })

  await Promise.all([
    waitForService('http://127.0.0.1:18000/api/health', backend, 'backend'),
    waitForService('http://127.0.0.1:15173', vite, 'Vite'),
  ])
  if (backend.exitCode !== null || vite.exitCode !== null) {
    throw new Error('E2E server exited before the tests started.')
  }

  const playwright = launch(process.execPath, [
    path.join(frontendDir, 'node_modules', '@playwright', 'test', 'cli.js'), 'test',
    ...args,
  ], { cwd: frontendDir, env: { ...process.env, E2E_PYTHON: python, E2E_PASSWORD_RELEASE: process.env.E2E_PASSWORD_RELEASE ?? 'true' } })
  return await waitForExit(playwright)
}

function runSpecInChild(specPath) {
  const child = observeChild(spawn(process.execPath, [
    runnerPath,
    specSelection(specPath),
    '--output', specOutputDirectory(specPath),
  ], {
    cwd: frontendDir,
    stdio: 'inherit',
    detached: process.platform !== 'win32',
    env: { ...process.env, E2E_PYTHON: python },
  }))
  isolatedRunnerChildren.add(child)
  children.push(child)
  return waitForExit(child)
}

export async function runIsolatedSpecs(specs, runSpec = runSpecInChild) {
  if (!specs.length) {
    console.error('No E2E spec files were discovered.')
    return 1
  }
  const failures = []
  for (const specPath of specs) {
    if (cancellationRequested) return 1
    const selection = specSelection(specPath)
    console.log(`\n=== E2E isolated spec: ${selection} ===`)
    try {
      const code = await runSpec(specPath)
      if (code !== 0) failures.push({ selection, code })
    } catch (error) {
      console.error(`E2E spec could not start: ${selection}`, error)
      failures.push({ selection, code: 1 })
    }
  }
  if (failures.length) {
    console.error(`\nE2E failures (${failures.length}/${specs.length}):`)
    for (const failure of failures) console.error(`- ${failure.selection} (exit ${failure.code})`)
    return 1
  }
  return 0
}

async function main() {
  const args = process.argv.slice(2)
  return args.length ? runOneE2eInvocation(args) : runIsolatedSpecs(discoverE2eSpecs())
}

async function cleanupChildren() {
  cleanupPromise ??= (async () => {
    const failures = []
    for (const child of children.reverse()) {
      try {
        await terminateChild(child, isolatedRunnerChildren.has(child) ? 40_000 : 5_000)
      } catch (error) {
        failures.push(error)
      }
    }
    if (failures.length) throw new AggregateError(failures, 'One or more E2E child processes did not exit.')
  })()
  return cleanupPromise
}

if (process.argv[1] && path.resolve(process.argv[1]) === runnerPath) {
  let stopping = false
  const stopFromSignal = async (exitCode) => {
    if (stopping) return
    stopping = true
    cancellationRequested = true
    await cleanupChildren()
    process.exit(exitCode)
  }
  const handleSignal = (signalExitCode) => {
    void stopFromSignal(signalExitCode).catch((error) => {
      console.error('E2E cleanup failed after signal:', error)
      process.exit(1)
    })
  }
  process.once('SIGINT', () => handleSignal(130))
  process.once('SIGTERM', () => handleSignal(143))
  let exitCode = 1
  try {
    exitCode = await main()
  } finally {
    stopping = true
    await cleanupChildren()
  }
  process.exit(exitCode)
}
