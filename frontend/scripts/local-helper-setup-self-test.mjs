import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'
import { buildLocalHelperSetup, resolveHelperSetupAddress } from '../src/features/local-pc/setupLauncher.ts'

function decodedPowerShell(batch) {
  const match = batch.match(/:: WORKBENCH_LOCAL_HELPER_SETUP_PAYLOAD\r?\n([A-Za-z0-9+/=]+)\r?\n?$/)
  assert.ok(match, 'batch file must contain a UTF-16LE payload after its marker')
  return Buffer.from(match[1], 'base64').toString('utf16le')
}

function decodedConfig(script) {
  const match = script.match(/\$configBase64 = '([A-Za-z0-9+/=]+)'/)
  assert.ok(match, 'PowerShell command must contain base64 configuration')
  return JSON.parse(Buffer.from(match[1], 'base64').toString('utf8'))
}

test('launcher uses a UTF-16LE encoded payload and keeps the configuration data-only', () => {
  const serverUrl = 'https://workbench.example.test'
  const webOrigin = 'https://workbench.example.test'
  const batch = buildLocalHelperSetup({ serverUrl, webOrigin, autoStart: true })
  const script = decodedPowerShell(batch)

  assert.match(batch, /@echo off\r\nsetlocal/)
  assert.match(batch, /powershell\.exe -NoProfile -ExecutionPolicy Bypass -Command/)
  assert.match(batch, /ReadAllText\(\$env:SIMULATION_WORKBENCH_SETUP_LAUNCHER\)/)
  assert.match(batch, /WORKBENCH_LOCAL_HELPER_SETUP_PAYLOAD/)
  assert.ok(Math.max(...batch.split(/\r?\n/).slice(0, -2).map((line) => line.length)) < 8191, 'every executed cmd.exe line must fit its command limit')
  assert.doesNotMatch(batch, /workbench\.example\.test/)
  assert.doesNotMatch(script, /workbench\.example\.test/)
  assert.match(script, /FolderBrowserDialog/)
  assert.match(script, /start-local-runner\.ps1/)
  assert.match(script, /-InstallAutoStart/)
  assert.match(script, /update\.bat/)
  assert.deepEqual(decodedConfig(script), { serverUrl, webOrigin, autoStart: true })
})

test('launcher rejects unsafe central server and browser origin addresses', () => {
  const safe = { serverUrl: 'https://workbench.example.test', webOrigin: 'https://workbench.example.test', autoStart: false }
  for (const serverUrl of [
    'http://workbench.example.test',
    'https://user:password@workbench.example.test',
    'https://workbench.example.test/?token=secret',
    'https://workbench.example.test/#fragment',
    'https://workbench.example.test/api',
  ]) {
    assert.throws(() => buildLocalHelperSetup({ ...safe, serverUrl }), TypeError)
  }
  assert.doesNotThrow(() => buildLocalHelperSetup({ ...safe, serverUrl: 'http://127.0.0.1:8000' }))
  assert.doesNotThrow(() => buildLocalHelperSetup({ ...safe, serverUrl: 'http://localhost:8000' }))
  assert.throws(() => buildLocalHelperSetup({ ...safe, webOrigin: 'https://workbench.example.test/path' }), TypeError)
})

test('resolved addresses use the page origin for both proxy development and hosted deployments', () => {
  const previous = globalThis.location
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { origin: 'http://127.0.0.1:5173' } })
  try {
    assert.deepEqual(resolveHelperSetupAddress(), { serverUrl: 'http://127.0.0.1:5173', webOrigin: 'http://127.0.0.1:5173' })
  } finally {
    Object.defineProperty(globalThis, 'location', { configurable: true, value: previous })
  }
})

test('generated PowerShell parses without executing a launcher or changing startup settings', () => {
  const script = decodedPowerShell(buildLocalHelperSetup({
    serverUrl: 'https://workbench.example.test',
    webOrigin: 'https://workbench.example.test',
    autoStart: false,
  }))
  const encoded = Buffer.from(script, 'utf16le').toString('base64')
  execFileSync('powershell.exe', [
    '-NoProfile', '-NonInteractive', '-Command',
    `[void][ScriptBlock]::Create([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('${encoded}')))`
  ], { stdio: 'pipe' })
})

function stagedLauncher({ autoStart, exitCode }) {
  const stage = mkdtempSync(join(tmpdir(), 'simulation-workbench-도우미-'))
  assert.notEqual(resolve(stage), resolve(process.cwd()), 'test stage must never be the frontend workspace')
  assert.ok(resolve(stage).startsWith(resolve(tmpdir())), 'test stage must remain below the system temp directory')
  mkdirSync(join(stage, '.venv-runtime', 'Scripts'), { recursive: true })
  writeFileSync(join(stage, '.venv-runtime', 'Scripts', 'python.exe'), '')
  writeFileSync(join(stage, 'start-local-runner.ps1'), String.raw`param([string]$ServerUrl, [string[]]$Origin, [switch]$InstallAutoStart)
[pscustomobject]@{ serverUrl = $ServerUrl; origin = @($Origin); installAutoStart = [bool]$InstallAutoStart } | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'received.json') -Encoding UTF8
Write-Host 'fake runner invoked'
exit [int]$env:SIMULATION_WORKBENCH_FAKE_EXIT
`)
  const batchPath = join(stage, 'SimulationWorkbench-local-helper-setup.bat')
  writeFileSync(batchPath, buildLocalHelperSetup({
    serverUrl: 'https://workbench.example.test',
    webOrigin: 'https://workbench.example.test',
    autoStart,
  }), 'utf8')
  const result = spawnSync('cmd.exe', ['/d', '/c', batchPath], {
    cwd: stage,
    input: '',
    encoding: 'utf8',
    env: { ...process.env, SIMULATION_WORKBENCH_FAKE_EXIT: String(exitCode) },
  })
  return { stage, result }
}

test('generated BAT really runs from a Unicode/spaces deployment folder and preserves success arguments', () => {
  const { stage, result } = stagedLauncher({ autoStart: true, exitCode: 0 })
  try {
    assert.equal(result.status, 0, result.stderr || result.stdout)
    const received = JSON.parse(readFileSync(join(stage, 'received.json'), 'utf8').replace(/^\uFEFF/, ''))
    assert.deepEqual(received, {
      serverUrl: 'https://workbench.example.test',
      origin: ['https://workbench.example.test'],
      installAutoStart: true,
    })
  } finally {
    rmSync(stage, { recursive: true, force: true })
  }
})

test('generated BAT returns launcher failures and omits the auto-start switch when disabled', () => {
  const { stage, result } = stagedLauncher({ autoStart: false, exitCode: 7 })
  try {
    assert.equal(result.status, 7, result.stderr || result.stdout)
    const received = JSON.parse(readFileSync(join(stage, 'received.json'), 'utf8').replace(/^\uFEFF/, ''))
    assert.equal(received.installAutoStart, false)
  } finally {
    rmSync(stage, { recursive: true, force: true })
  }
})
