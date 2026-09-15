import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import test from 'node:test'
import { buildLocalHelperSetup, resolveHelperSetupAddress } from '../src/features/local-pc/setupLauncher.ts'

const distribution = {
  status: 'ready',
  version: '1.0.0',
  filename: 'SimulationWorkbenchLocalHelper-1.0.0-windows-x64.zip',
  artifact_url: '/api/local-helper/distribution/download',
  sha256: 'a'.repeat(64),
  size_bytes: 12345,
  released_at: '2026-09-09T00:00:00Z',
}

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
  const batch = buildLocalHelperSetup({ serverUrl, webOrigin, autoStart: true, distribution })
  const script = decodedPowerShell(batch)

  assert.match(batch, /@echo off\r\nsetlocal/)
  assert.match(batch, /powershell\.exe -NoProfile -ExecutionPolicy Bypass -Command/)
  assert.match(batch, /ReadAllText\(\$env:SIMULATION_WORKBENCH_SETUP_LAUNCHER\)/)
  assert.match(batch, /WORKBENCH_LOCAL_HELPER_SETUP_PAYLOAD/)
  assert.ok(Math.max(...batch.split(/\r?\n/).slice(0, -2).map((line) => line.length)) < 8191, 'every executed cmd.exe line must fit its command limit')
  assert.doesNotMatch(batch, /workbench\.example\.test/)
  assert.doesNotMatch(script, /workbench\.example\.test/)
  assert.match(script, /LocalApplicationData/)
  assert.match(script, /artifact-manifest\.json/)
  assert.match(script, /start-local-runner\.ps1/)
  assert.match(script, /-InstallAutoStart/)
  assert.match(script, /Get-FileHash/)
  assert.deepEqual(decodedConfig(script), { serverUrl, webOrigin, autoStart: true, distribution })
})

test('launcher rejects unsafe central server and browser origin addresses', () => {
  const safe = { serverUrl: 'https://workbench.example.test', webOrigin: 'https://workbench.example.test', autoStart: false, distribution }
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
    distribution,
  }))
  const encoded = Buffer.from(script, 'utf16le').toString('base64')
  execFileSync('powershell.exe', [
    '-NoProfile', '-NonInteractive', '-Command',
    `[void][ScriptBlock]::Create([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('${encoded}')))`
  ], { stdio: 'pipe' })
})

test('manifest accepts only a safe same-origin download path and complete archive checksum', () => {
  assert.throws(() => buildLocalHelperSetup({
    serverUrl: 'https://workbench.example.test', webOrigin: 'https://workbench.example.test', autoStart: false,
    distribution: { ...distribution, artifact_url: 'https://unexpected.example/helper.zip' },
  }), TypeError)
  assert.throws(() => buildLocalHelperSetup({
    serverUrl: 'https://workbench.example.test', webOrigin: 'https://workbench.example.test', autoStart: false,
    distribution: { ...distribution, sha256: 'nope' },
  }), TypeError)
})
