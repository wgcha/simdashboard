import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { discoverE2eSpecs, runIsolatedSpecs, terminateChild } from './run-e2e.mjs'

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
  console.log(`E2E runner self-test passed: ${names.length} discovered specs, real child failures aggregate after every spec runs.`)
} finally {
  rmSync(fixtureDirectory, { recursive: true, force: true })
}
