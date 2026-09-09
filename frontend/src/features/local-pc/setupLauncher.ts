export type LocalHelperSetupConfig = Readonly<{
  serverUrl: string
  webOrigin: string
  autoStart: boolean
}>

type NormalizedSetupConfig = {
  serverUrl: string
  webOrigin: string
  autoStart: boolean
}

const loopbackHosts = new Set(['localhost', '127.0.0.1', '::1'])

function normalizedHttpUrl(value: string, label: string, allowHttpOnlyOnLoopback: boolean): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new TypeError(`${label}은(는) http 또는 https 주소여야 합니다.`)
  }

  const hostname = url.hostname.replace(/^\[|\]$/g, '').toLowerCase()
  if ((url.protocol !== 'http:' && url.protocol !== 'https:') || !hostname) {
    throw new TypeError(`${label}은(는) http 또는 https 주소여야 합니다.`)
  }
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new TypeError(`${label}에는 계정 정보, 경로, 쿼리 또는 해시를 포함할 수 없습니다.`)
  }
  if (allowHttpOnlyOnLoopback && url.protocol === 'http:' && !loopbackHosts.has(hostname)) {
    throw new TypeError('ServerUrl은 HTTPS여야 합니다. HTTP는 localhost 또는 127.0.0.1 개발 주소에서만 허용됩니다.')
  }
  return url.origin
}

function normalizeConfig(config: LocalHelperSetupConfig): NormalizedSetupConfig {
  return {
    serverUrl: normalizedHttpUrl(config.serverUrl, 'ServerUrl', true),
    webOrigin: normalizedHttpUrl(config.webOrigin, '웹 origin', false),
    autoStart: Boolean(config.autoStart),
  }
}

/**
 * The page and API share an origin in every deployment.  In Vite development
 * this deliberately keeps the 5173 origin: the helper uses Vite's /api proxy
 * instead of receiving its private upstream target.
 */
export function resolveHelperSetupAddress(): { serverUrl: string; webOrigin: string } {
  if (!globalThis.location?.origin) throw new Error('브라우저 주소를 확인할 수 없습니다.')
  const origin = normalizedHttpUrl(globalThis.location.origin, '웹 origin', false)
  return { serverUrl: origin, webOrigin: origin }
}

function encodePowerShell(script: string): string {
  const bytes = new Uint8Array(script.length * 2)
  for (let index = 0; index < script.length; index += 1) {
    const value = script.charCodeAt(index)
    bytes[index * 2] = value & 0xff
    bytes[index * 2 + 1] = value >> 8
  }
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

function encodeUtf8Base64(value: string): string {
  const bytes = new TextEncoder().encode(value)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

/** Builds the one-time, user-run launcher without embedding browser credentials or session data. */
export function buildLocalHelperSetup(config: LocalHelperSetupConfig): string {
  const normalized = normalizeConfig(config)
  const configBase64 = encodeUtf8Base64(JSON.stringify(normalized))
  const script = String.raw`$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Test-HelperDeployment {
    param([string]$Candidate)
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    try { $resolved = [IO.Path]::GetFullPath($Candidate) } catch { return $false }
    return (Test-Path -LiteralPath $resolved -PathType Container) -and
        (Test-Path -LiteralPath (Join-Path -Path $resolved -ChildPath 'start-local-runner.ps1') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path -Path $resolved -ChildPath '.venv-runtime\Scripts\python.exe') -PathType Leaf)
}

function Stop-InvalidDeployment {
    param([string]$Candidate)
    $resolved = [IO.Path]::GetFullPath($Candidate)
    $looksLikeOldSourceFolder = (Test-Path -LiteralPath (Join-Path -Path $resolved -ChildPath '.git') -PathType Container) -or
        (Test-Path -LiteralPath (Join-Path -Path $resolved -ChildPath 'update.bat') -PathType Leaf)
    if ($looksLikeOldSourceFolder) {
        throw "선택한 폴더에는 새 로컬 도우미 실행 파일이 없습니다. 해당 배포본의 update.bat를 실행하거나 새 버전을 다시 받아 주세요. ($resolved)"
    }
    throw "선택한 폴더는 로컬 도우미 배포 폴더가 아닙니다. start-local-runner.ps1 및 .venv-runtime\\Scripts\\python.exe가 필요합니다. ($resolved)"
}

$configBase64 = '${configBase64}'
$configJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($configBase64))
$config = $configJson | ConvertFrom-Json
if ($null -eq $config -or [string]::IsNullOrWhiteSpace([string]$config.serverUrl) -or [string]::IsNullOrWhiteSpace([string]$config.webOrigin)) {
    throw '설정 데이터가 올바르지 않습니다. 웹 페이지에서 새 설정 파일을 다시 내려받아 주세요.'
}

$downloadFolder = [Environment]::GetEnvironmentVariable('SIMULATION_WORKBENCH_SETUP_DIRECTORY')
if (Test-HelperDeployment $downloadFolder) {
    $deploymentFolder = [IO.Path]::GetFullPath($downloadFolder)
    Write-Host "배포 폴더를 찾았습니다: $deploymentFolder"
} else {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = 'start-local-runner.ps1가 있는 로컬 도우미 배포 폴더를 선택하세요.'
    $picker.ShowNewFolderButton = $false
    if ($picker.ShowDialog() -ne [Windows.Forms.DialogResult]::OK) {
        throw '폴더를 선택하지 않아 설정을 취소했습니다. 다시 실행하여 로컬 도우미 배포 폴더를 선택하세요.'
    }
    $deploymentFolder = [IO.Path]::GetFullPath($picker.SelectedPath)
    if (-not (Test-HelperDeployment $deploymentFolder)) { Stop-InvalidDeployment $deploymentFolder }
}

$launcher = Join-Path -Path $deploymentFolder -ChildPath 'start-local-runner.ps1'
Write-Host '현재 Windows 사용자에게만 설정합니다. 관리자 권한은 필요하지 않습니다.'
if ([bool]$config.autoStart) {
    & $launcher -ServerUrl ([string]$config.serverUrl) -Origin ([string]$config.webOrigin) -InstallAutoStart
} else {
    & $launcher -ServerUrl ([string]$config.serverUrl) -Origin ([string]$config.webOrigin)
}
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    Write-Host "로컬 도우미 시작에 실패했습니다. 종료 코드: $LASTEXITCODE"
    exit $LASTEXITCODE
}
Write-Host ''
Write-Host '설정이 완료되었습니다. 웹 페이지로 돌아가서 “다시 확인”을 선택하세요.'
`
  const encodedScript = encodePowerShell(script)
  // cmd.exe has an 8,191-character command-line limit. Keep its PowerShell
  // invocation short and read the large UTF-16LE payload from this BAT file.
  return `@echo off\r\nsetlocal DisableDelayedExpansion\r\nset "SIMULATION_WORKBENCH_SETUP_DIRECTORY=%~dp0"\r\nset "SIMULATION_WORKBENCH_SETUP_LAUNCHER=%~f0"\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$body = [IO.File]::ReadAllText($env:SIMULATION_WORKBENCH_SETUP_LAUNCHER); $parts = $body -split '(?m)^:: WORKBENCH_LOCAL_HELPER_SETUP_PAYLOAD\\r?$', 2; if ($parts.Count -ne 2) { throw 'Setup payload is missing.' }; $payload = $parts[1].Trim(); $bytes = [Convert]::FromBase64String($payload); & ([ScriptBlock]::Create([Text.Encoding]::Unicode.GetString($bytes)))"\r\nset "SIMULATION_WORKBENCH_SETUP_EXIT=%ERRORLEVEL%"\r\nif not "%SIMULATION_WORKBENCH_SETUP_EXIT%"=="0" (\r\n  echo.\r\n  echo Local helper setup failed. Read the error above, then press any key to close this window.\r\n  pause\r\n  goto :setup_finished\r\n)\r\necho.\r\necho Setup complete. Return to the web page and select "다시 확인".\r\npause\r\n:setup_finished\r\nendlocal & exit /b %SIMULATION_WORKBENCH_SETUP_EXIT%\r\n:: WORKBENCH_LOCAL_HELPER_SETUP_PAYLOAD\r\n${encodedScript}\r\n`
}

export function downloadLocalHelperSetup(config: LocalHelperSetupConfig): void {
  const setup = buildLocalHelperSetup(config)
  const blob = new Blob([setup], { type: 'application/octet-stream' })
  const href = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.download = 'SimulationWorkbench-local-helper-setup.bat'
  anchor.style.display = 'none'
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  globalThis.setTimeout(() => URL.revokeObjectURL(href), 0)
}
