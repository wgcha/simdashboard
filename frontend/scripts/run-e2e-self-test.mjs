import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawn, spawnSync } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { fileURLToPath } from 'node:url'
import { cleanupAfterChildren, cleanupRunnerResources, discoverE2eSpecs, isolatedE2eEnvironment, processIsAlive, runIsolatedSpecs, terminateChild } from './run-e2e.mjs'

const temporaryRoot = process.platform === 'win32' ? os.tmpdir() : '/tmp'
const fixtureDirectory = mkdtempSync(path.join(temporaryRoot, 'run-e2e-self-test-'))
const stubPath = path.join(fixtureDirectory, 'child-stub.mjs')
const visitPath = path.join(fixtureDirectory, 'visits.txt')

function runStub(specName, failingSpecs) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [stubPath, visitPath, specName, JSON.stringify(failingSpecs)], { stdio: 'ignore' })
    child.once('error', reject)
    child.once('close', (code, signal) => resolve(code ?? (signal ? 1 : 0)))
  })
}

async function waitForFile(target, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (existsSync(target)) return
    await new Promise((resolve) => setTimeout(resolve, 20))
  }
  throw new Error(`timed out waiting for ${target}`)
}

async function verifyPosixGracefulChild() {
  if (process.platform === 'win32') return
  const readyPath = path.join(fixtureDirectory, 'signal-ready')
  const signalPath = path.join(fixtureDirectory, 'signal-received')
  const signalStubPath = path.join(fixtureDirectory, 'signal-stub.mjs')
  writeFileSync(signalStubPath, [
    "import { writeFileSync } from 'node:fs'",
    'const [readyPath, signalPath] = process.argv.slice(2)',
    "process.once('SIGTERM', () => { writeFileSync(signalPath, 'received'); setTimeout(() => process.exit(0), 150) })",
    "writeFileSync(readyPath, 'ready')",
    'setInterval(() => {}, 1_000)',
    '',
  ].join('\n'))
  const child = spawn(process.execPath, [signalStubPath, readyPath, signalPath], { detached: true, stdio: 'ignore' })
  try {
    await waitForFile(readyPath)
    await terminateChild(child, 1_000)
    if (!existsSync(signalPath) || child.exitCode !== 0) throw new Error('POSIX child did not receive SIGTERM and exit gracefully')
  } finally {
    if (child.exitCode === null) {
      try { process.kill(-child.pid, 'SIGKILL') } catch { /* already stopped */ }
    }
  }
}

async function verifyExitedPidBeforeCloseRace() {
  const exited = spawn(process.execPath, ['-e', 'process.exit(0)'], { stdio: 'ignore' })
  await new Promise((resolve, reject) => {
    exited.once('close', resolve)
    exited.once('error', reject)
  })
  // Model an owner handle whose `close` event was not observed although its PID is gone.
  const delayedClose = new EventEmitter()
  Object.assign(delayedClose, { pid: exited.pid, exitCode: null })
  delayedClose.kill = () => false
  await terminateChild(delayedClose, 80)
}

async function verifyWindowsOwnedChildTermination() {
  if (process.platform !== 'win32') return
  const pidPath = path.join(fixtureDirectory, 'owned-tree-pids.json')
  const parentScript = [
    "const { spawn } = require('node:child_process')",
    "const { writeFileSync } = require('node:fs')",
    "const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1_000)'], { stdio: 'ignore' })",
    `writeFileSync(${JSON.stringify(pidPath)}, JSON.stringify({ parent: process.pid, child: child.pid }))`,
    'setInterval(() => {}, 1_000)',
  ].join(';')
  const child = spawn(process.execPath, ['-e', parentScript], { stdio: 'ignore' })
  let grandchildPid
  await new Promise((resolve, reject) => {
    child.once('spawn', resolve)
    child.once('error', reject)
  })
  try {
    await waitForFile(pidPath)
    const pids = JSON.parse(readFileSync(pidPath, 'utf8'))
    grandchildPid = pids.child
    if (pids.parent !== child.pid || !processIsAlive(grandchildPid)) throw new Error('Windows E2E descendant fixture did not start')
    await terminateChild(child, 1_000)
    if (processIsAlive(child.pid) || processIsAlive(grandchildPid)) throw new Error('Windows E2E process tree remains alive after termination')
  } finally {
    for (const pid of [grandchildPid, child.pid]) {
      if (pid && processIsAlive(pid)) spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', timeout: 5_000 })
    }
  }
}

async function verifyIsolatedSpdmRootAndCleanupOrder() {
  let childrenStopped = false
  let rootRemoved = false
  await cleanupAfterChildren(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); childrenStopped = true }, () => {
    if (!childrenStopped) throw new Error('SPDM root cleanup ran before E2E child cleanup')
    rootRemoved = true
  })
  if (!rootRemoved) throw new Error('SPDM root cleanup did not run after child cleanup')
  let cleanedAfterFailure = false
  try {
    await cleanupAfterChildren(async () => { throw new Error('simulated live child') }, () => { cleanedAfterFailure = true })
    throw new Error('failed child shutdown should reject runner cleanup')
  } catch (error) {
    if (error instanceof Error && error.message === 'failed child shutdown should reject runner cleanup') throw error
  }
  if (cleanedAfterFailure) throw new Error('SPDM root cleanup ran after a child shutdown failure')

  const externalRoot = path.join(fixtureDirectory, 'configured-user-storage')
  const firstEnvironment = isolatedE2eEnvironment({ SIMDASH_SPDM_ROOT: externalRoot }, { E2E_FIXTURE: 'true' })
  const firstRoot = firstEnvironment.SIMDASH_SPDM_ROOT
  if (firstRoot === externalRoot || !path.basename(firstRoot).startsWith('simulation-workbench-e2e-spdm-') || !existsSync(firstRoot)) {
    throw new Error('E2E invocation did not override the configured SPDM root with an isolated temporary root')
  }
  await cleanupRunnerResources()
  if (existsSync(firstRoot)) throw new Error('E2E SPDM root was not cleaned after child cleanup')
  const secondEnvironment = isolatedE2eEnvironment({ SIMDASH_SPDM_ROOT: externalRoot })
  if (secondEnvironment.SIMDASH_SPDM_ROOT === firstRoot) throw new Error('E2E invocations reused an SPDM root')
  await cleanupRunnerResources()
}

try {
  let launchedEmptySpec = false
  if (await runIsolatedSpecs([], async () => {
    launchedEmptySpec = true
    return 0
  }) !== 1 || launchedEmptySpec) {
    throw new Error('an empty E2E discovery must fail without launching a child')
  }

  const specs = discoverE2eSpecs()
  if (!specs.length) throw new Error('the E2E discovery fixture unexpectedly found no specs')
  const names = specs.map((specPath) => path.basename(specPath))
  const failingSpecs = [names[0], names[Math.floor(names.length / 2)], names.at(-1)]
  writeFileSync(stubPath, [
    "import { appendFileSync } from 'node:fs'",
    "const [visitPath, specName, failureJson] = process.argv.slice(2)",
    "appendFileSync(visitPath, `${specName}\\n`)",
    'process.exit(JSON.parse(failureJson).includes(specName) ? 7 : 0)',
    '',
  ].join('\n'))

  const exitCode = await runIsolatedSpecs(specs, (specPath) => runStub(path.basename(specPath), failingSpecs))
  const visited = readFileSync(visitPath, 'utf8').trim().split('\n')
  if (exitCode !== 1) throw new Error(`child failures must produce aggregate exit 1, received ${exitCode}`)
  if (visited.length !== names.length || visited.join('\n') !== names.join('\n')) {
    throw new Error('every discovered E2E spec must run in order after a child failure')
  }
  await verifyPosixGracefulChild()
  await verifyExitedPidBeforeCloseRace()
  await verifyWindowsOwnedChildTermination()
  await verifyIsolatedSpdmRootAndCleanupOrder()
  console.log(`E2E runner self-test passed: ${names.length} discovered specs, real child failures aggregate after every spec runs.`)
} finally {
  rmSync(fixtureDirectory, { recursive: true, force: true })
}
