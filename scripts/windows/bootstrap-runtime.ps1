[CmdletBinding()]
param(
    [ValidateSet('auto', 'direct', 'proxy')]
    [string]$NetworkMode = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Import-Module (Join-Path $Root 'scripts\windows\Network.psm1') -Force
$Tools = Join-Path $Root '.tools'
$Downloads = Join-Path $Tools 'downloads'
$env:UV_CACHE_DIR = Join-Path $Tools 'uv-cache'
$env:UV_PYTHON_BIN_DIR = Join-Path $Tools 'python-bin'
$NodeVersion = (Get-Content -Raw -LiteralPath (Join-Path $Root '.node-version')).Trim()
$PythonVersion = (Get-Content -Raw -LiteralPath (Join-Path $Root '.python-version')).Trim()
$PnpmVersion = ((Get-Content -Raw -LiteralPath (Join-Path $Root 'frontend\package.json') | ConvertFrom-Json).packageManager -replace '^pnpm@', '').Trim()
$UvVersion = '0.11.32'
$UvSha256 = 'acfde570451cfdb8689fa159a138ee805ba4e241c466432750302c86254b0984'

$networkSettings = Join-Path $Root 'deploy\windows\network-settings.json'
if ($NetworkMode) { Initialize-DeploymentNetwork -SettingsPath $networkSettings -Mode $NetworkMode | Out-Null }
else { Initialize-DeploymentNetwork -SettingsPath $networkSettings | Out-Null }

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Download-File([string]$Uri, [string]$Path, [string]$ExpectedSha256 = '') {
    Write-Host "Downloading $(Split-Path -Leaf $Path)..." -ForegroundColor DarkCyan
    Invoke-AtomicDownload -Uri $Uri -Destination $Path -ExpectedSha256 $ExpectedSha256 | Out-Null
}

function Require-Hash([string]$Path, [string]$Expected, [string]$Name) {
    if ((Get-Sha256 -Path $Path) -ne $Expected.ToLowerInvariant()) {
        throw "$Name checksum verification failed: $Path"
    }
}

try {
    New-Item -ItemType Directory -Force -Path $Tools, $Downloads | Out-Null

    $nodeArchiveName = "node-$NodeVersion-win-x64.zip"
    $nodeArchive = Join-Path $Downloads $nodeArchiveName
    $nodeChecksums = Join-Path $Downloads 'node-SHASUMS256.txt'
    Download-File -Uri "https://nodejs.org/dist/$NodeVersion/SHASUMS256.txt" -Path $nodeChecksums
    $nodeHashLine = Get-Content -LiteralPath $nodeChecksums | Where-Object { $_ -match "\s$([regex]::Escape($nodeArchiveName))$" } | Select-Object -First 1
    if (-not $nodeHashLine) { throw "Official Node.js checksum was not found for $nodeArchiveName." }
    $nodeExpectedSha256 = ($nodeHashLine -split '\s+')[0]
    Download-File -Uri "https://nodejs.org/dist/$NodeVersion/$nodeArchiveName" -Path $nodeArchive -ExpectedSha256 $nodeExpectedSha256
    Require-Hash -Path $nodeArchive -Expected $nodeExpectedSha256 -Name 'Node.js runtime'
    $nodeDirectory = Join-Path $Tools "node-$NodeVersion-win-x64"
    if (-not (Test-Path -LiteralPath (Join-Path $nodeDirectory 'node.exe') -PathType Leaf)) {
        Write-Host 'Extracting Node.js runtime...' -ForegroundColor DarkCyan
        Expand-Archive -LiteralPath $nodeArchive -DestinationPath $Tools -Force
    }
    $node = Join-Path $nodeDirectory 'node.exe'
    if ((& $node --version).Trim() -ne $NodeVersion) { throw "Node.js version verification failed for $node." }

    $uvArchive = Join-Path $Downloads 'uv-x86_64-pc-windows-msvc.zip'
    $legacyUvArchive = Join-Path $Downloads 'uv.zip'
    if (-not (Test-Path -LiteralPath $uvArchive -PathType Leaf) -and (Test-Path -LiteralPath $legacyUvArchive -PathType Leaf)) {
        $uvArchive = $legacyUvArchive
    }
    Download-File -Uri "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -Path $uvArchive -ExpectedSha256 $UvSha256
    Require-Hash -Path $uvArchive -Expected $UvSha256 -Name 'uv runtime'
    $uvDirectory = Join-Path $Tools 'uv'
    $uv = Join-Path $uvDirectory 'uv.exe'
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path $uvDirectory | Out-Null
        Write-Host 'Extracting uv runtime...' -ForegroundColor DarkCyan
        Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvDirectory -Force
    }
    if (-not (Test-Path -LiteralPath $uv -PathType Leaf)) { throw 'uv.exe was not extracted.' }

    $pythonDirectory = Join-Path $Tools 'python'
    $python = Join-Path $pythonDirectory "cpython-$PythonVersion-windows-x86_64-none\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Write-Host "Installing project-local Python $PythonVersion..." -ForegroundColor DarkCyan
        & $uv python install $PythonVersion --install-dir $pythonDirectory --no-registry
        if ($LASTEXITCODE -ne 0) { throw "Python $PythonVersion installation failed (exit code $LASTEXITCODE)." }
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python $PythonVersion was not installed in the expected project location." }

    $env:COREPACK_HOME = Join-Path $Tools 'corepack'
    $corepack = Join-Path $nodeDirectory 'corepack.cmd'
    Push-Location (Join-Path $Root 'frontend')
    try {
        Write-Host "Preparing pnpm $PnpmVersion with Corepack..." -ForegroundColor DarkCyan
        & $corepack install
        if ($LASTEXITCODE -ne 0) { throw "pnpm $PnpmVersion installation failed (exit code $LASTEXITCODE)." }
    }
    finally {
        Pop-Location
    }
    $pnpmWrapper = Join-Path $Tools 'pnpm.cmd'
    @"
@echo off
set "COREPACK_HOME=%~dp0corepack"
"%~dp0node-$NodeVersion-win-x64\\corepack.cmd" pnpm %*
"@ | Set-Content -LiteralPath $pnpmWrapper -Encoding ASCII
    if ((& $pnpmWrapper --version).Trim() -ne $PnpmVersion) { throw "pnpm $PnpmVersion verification failed." }

    Write-Host "Project runtimes are ready: Node $NodeVersion, Python $PythonVersion, pnpm $PnpmVersion." -ForegroundColor Green
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
