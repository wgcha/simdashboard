import type { LocalHelperDistributionReady } from '../../shared/api/localHelperDistribution'

export type { LocalHelperDistributionReady } from '../../shared/api/localHelperDistribution'

export type LocalHelperSetupConfig = Readonly<{
  serverUrl: string
  webOrigin: string
  autoStart: boolean
  distribution: LocalHelperDistributionReady
}>

type NormalizedSetupConfig = {
  serverUrl: string
  webOrigin: string
  autoStart: boolean
  distribution: LocalHelperDistributionReady
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
  const distribution = config.distribution
  if (
    distribution.status !== 'ready' ||
    !/^\/[A-Za-z0-9._/-]+$/.test(distribution.artifact_url) || distribution.artifact_url.startsWith('//') ||
    !/^[a-f0-9]{64}$/i.test(distribution.sha256) ||
    !Number.isSafeInteger(distribution.size_bytes) || distribution.size_bytes <= 0 ||
    !/^[A-Za-z0-9._-]+\.zip$/i.test(distribution.filename) ||
    !distribution.version.trim() || !distribution.released_at.trim()
  ) throw new TypeError('로컬 도우미 배포 manifest가 올바르지 않습니다.')
  return {
    serverUrl: normalizedHttpUrl(config.serverUrl, 'ServerUrl', true),
    webOrigin: normalizedHttpUrl(config.webOrigin, '웹 origin', false),
    autoStart: Boolean(config.autoStart),
    distribution,
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

function Get-Sha256([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Assert-ChildPath([string]$Candidate, [string]$Parent, [string]$Name) {
    $resolvedCandidate = [IO.Path]::GetFullPath($Candidate).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $resolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedCandidate.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase)) { throw "$Name 경로가 허용된 설치 범위를 벗어납니다." }
}
function Assert-SafeRelativePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.IndexOf([char]0) -ge 0) { throw '배포 파일 경로가 올바르지 않습니다.' }
    $normalized = $Path.Replace('/', '\\')
    if ([IO.Path]::IsPathRooted($normalized) -or $normalized.Split('\\') -contains '..') { throw '안전하지 않은 압축 파일 경로가 포함되어 있습니다.' }
    return $normalized
}
function Expand-VerifiedArchive([string]$Archive, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        $base = [IO.Path]::GetFullPath($Destination).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        $maximumBytes = [Math]::Min(512MB, [Math]::Max(64MB, (Get-Item -LiteralPath $Archive).Length * 25)); $expandedBytes = [int64]0; $entryCount = 0
        foreach ($entry in $zip.Entries) {
            if ([string]::IsNullOrEmpty($entry.Name)) { continue }
            $entryCount += 1; $expandedBytes += [int64]$entry.Length
            if ($entryCount -gt 128 -or $entry.Length -gt 256MB -or $expandedBytes -gt $maximumBytes) { throw '압축 해제 크기 또는 파일 수가 안전 한도를 초과했습니다.' }
            $relative = Assert-SafeRelativePath $entry.FullName
            $target = [IO.Path]::GetFullPath((Join-Path $Destination $relative))
            if (-not $target.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { throw '압축 파일 경로가 설치 경로를 벗어납니다.' }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
            $input = $entry.Open(); $output = [IO.File]::Create($target)
            try {
                $buffer = New-Object byte[] 65536; $written = [int64]0
                while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $written += $read
                    if ($written -gt 256MB -or ($expandedBytes - [int64]$entry.Length + $written) -gt $maximumBytes) { throw '압축 해제 크기가 안전 한도를 초과했습니다.' }
                    $output.Write($buffer, 0, $read)
                }
            } catch { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue; throw } finally { $output.Dispose(); $input.Dispose() }
        }
    } finally { $zip.Dispose() }
}
function Test-ExtractedManifest([string]$Directory) {
    $manifestPath = Join-Path $Directory 'artifact-manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw '배포 파일 manifest가 없습니다.' }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($null -eq $manifest -or $manifest.format -ne 1 -or $null -eq $manifest.files) { throw '배포 파일 manifest 형식이 올바르지 않습니다.' }
    $base = [IO.Path]::GetFullPath($Directory).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    foreach ($item in @($manifest.files)) {
        $relative = Assert-SafeRelativePath ([string]$item.path)
        if ([string]$item.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or [int64]$item.size_bytes -lt 0) { throw '배포 파일 manifest 항목이 올바르지 않습니다.' }
        $file = [IO.Path]::GetFullPath((Join-Path $Directory $relative))
        if (-not $file.StartsWith($base, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $file -PathType Leaf)) { throw '배포 파일이 누락되었습니다.' }
        if ((Get-Item -LiteralPath $file).Length -ne [int64]$item.size_bytes -or (Get-Sha256 $file) -ine [string]$item.sha256) { throw "배포 파일 검증에 실패했습니다: $relative" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Directory 'SimulationWorkbenchLocalHelper.exe') -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $Directory 'start-local-runner.ps1') -PathType Leaf)) { throw '실행 파일 또는 시작 스크립트가 없습니다.' }
}
function Get-RunningHelperIdentity {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8766/v1/identity' -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        $identity = $response.Content | ConvertFrom-Json
        if ($response.StatusCode -eq 200 -and $identity.managed -eq $true -and $identity.device_id) { return $identity }
    } catch { }
    return $null
}

$configBase64 = '${configBase64}'
$configJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($configBase64))
$config = $configJson | ConvertFrom-Json
if ($null -eq $config -or [string]::IsNullOrWhiteSpace([string]$config.serverUrl) -or [string]::IsNullOrWhiteSpace([string]$config.webOrigin) -or $null -eq $config.distribution) {
    throw '설정 데이터가 올바르지 않습니다. 웹 페이지에서 새 설정 파일을 다시 내려받아 주세요.'
}
if ([string]$config.distribution.artifact_url -notmatch '^/[A-Za-z0-9._/-]+$' -or ([string]$config.distribution.artifact_url).StartsWith('//') -or [string]$config.distribution.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw '배포 manifest가 올바르지 않습니다.' }
$server = [Uri]([string]$config.serverUrl)
$downloadUri = [Uri]::new($server, [string]$config.distribution.artifact_url)
$installParent = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'SimulationWorkbench'
$installRoot = Join-Path $installParent 'local-helper'
$backup = Join-Path $installParent 'local-helper.previous'
$stagingParent = Join-Path $installParent '.staging'
$temporaryParent = [IO.Path]::GetTempPath()
$temporary = Join-Path $temporaryParent ('simulation-workbench-helper-' + [Guid]::NewGuid().ToString('N'))
$archive = Join-Path $temporary ([string]$config.distribution.filename)
$stage = Join-Path $stagingParent ([Guid]::NewGuid().ToString('N'))
Assert-ChildPath -Candidate $installRoot -Parent $installParent -Name '설치'
Assert-ChildPath -Candidate $backup -Parent $installParent -Name '복구'
Assert-ChildPath -Candidate $stagingParent -Parent $installParent -Name '설치 준비'
Assert-ChildPath -Candidate $temporary -Parent $temporaryParent -Name '임시 설치'
Assert-ChildPath -Candidate $archive -Parent $temporary -Name '다운로드'
Assert-ChildPath -Candidate $stage -Parent $stagingParent -Name '임시 배포'
New-Item -ItemType Directory -Force -Path $installParent, $stagingParent, $temporary, $stage | Out-Null
try {
    Write-Host '로컬 도우미를 안전하게 내려받는 중입니다...'
    Invoke-WebRequest -Uri $downloadUri.AbsoluteUri -OutFile $archive -UseBasicParsing -MaximumRedirection 0
    if ((Get-Item -LiteralPath $archive).Length -ne [int64]$config.distribution.size_bytes -or (Get-Sha256 $archive) -ine [string]$config.distribution.sha256) { throw '다운로드한 배포 파일의 무결성 검증에 실패했습니다.' }
    Expand-VerifiedArchive -Archive $archive -Destination $stage
    Test-ExtractedManifest -Directory $stage
    if ((Test-Path -LiteralPath $installRoot) -and (Get-RunningHelperIdentity)) { throw '실행 중인 로컬 도우미가 있습니다. 작업 관리자에서 도우미를 종료한 뒤 업데이트를 다시 실행하세요.' }
    $movedPrevious = $false
    try {
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
        if (Test-Path -LiteralPath $installRoot) { Move-Item -LiteralPath $installRoot -Destination $backup; $movedPrevious = $true }
        Move-Item -LiteralPath $stage -Destination $installRoot
    } catch {
        if ($movedPrevious -and (Test-Path -LiteralPath $installRoot)) { Remove-Item -LiteralPath $installRoot -Recurse -Force }
        if ($movedPrevious -and (Test-Path -LiteralPath $backup)) { Move-Item -LiteralPath $backup -Destination $installRoot }
        throw
    }
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue }
}
$launcher = Join-Path -Path $installRoot -ChildPath 'start-local-runner.ps1'
Write-Host '현재 Windows 사용자 범위에 설치합니다. 관리자 권한은 필요하지 않습니다.'
try {
    if ([bool]$config.autoStart) {
        & $launcher -ServerUrl ([string]$config.serverUrl) -Origin ([string]$config.webOrigin) -InstallAutoStart
    } else {
        & $launcher -ServerUrl ([string]$config.serverUrl) -Origin ([string]$config.webOrigin)
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "로컬 도우미 시작에 실패했습니다. 종료 코드: $LASTEXITCODE" }
    if (-not (Get-RunningHelperIdentity)) { throw '로컬 도우미가 시작된 뒤 상태를 확인하지 못했습니다.' }
    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
} catch {
    $failure = $_
    # The pre-update check proves no previous helper owned the default port.
    # Restore the known-good installation only when the failed release is not
    # running and can therefore be removed safely.
    if (-not (Get-RunningHelperIdentity) -and (Test-Path -LiteralPath $installRoot)) {
        try { Remove-Item -LiteralPath $installRoot -Recurse -Force } catch { }
    }
    if (-not (Test-Path -LiteralPath $installRoot) -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $installRoot
    }
    throw $failure
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
