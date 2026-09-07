[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [ValidateSet('auto', 'direct', 'proxy')]
    [string]$NetworkMode = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $Root 'scripts\windows\Runtime.psm1') -Force
Import-Module (Join-Path $Root 'scripts\windows\Network.psm1') -Force

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

try {
    Set-Location -LiteralPath $Root
    $networkSettings = Join-Path $Root 'deploy\windows\network-settings.json'
    if ($NetworkMode) { Initialize-DeploymentNetwork -SettingsPath $networkSettings -Mode $NetworkMode | Out-Null }
    else { Initialize-DeploymentNetwork -SettingsPath $networkSettings | Out-Null }
    $versions = Get-ExpectedRuntimeVersions
    Write-Host "Windows development setup: Python $($versions.Python), Node $($versions.Node), pnpm $($versions.Pnpm)"
    Write-Host 'Existing .env is preserved. This command does not create, migrate, seed, import, or replace a database.' -ForegroundColor Yellow

    $runtimeBootstrap = Join-Path $Root 'scripts\windows\bootstrap-runtime.ps1'
    $projectNode = Join-Path $Root ".tools\node-$($versions.Node)-win-x64\node.exe"
    $projectPython = Join-Path $Root ".tools\python\cpython-$($versions.Python)-windows-x86_64-none\python.exe"
    $projectUv = Join-Path $Root '.tools\uv\uv.exe'
    $projectPnpm = Join-Path $Root '.tools\pnpm.cmd'
    if (-not (Test-Path -LiteralPath $projectNode -PathType Leaf) -or
        -not (Test-Path -LiteralPath $projectPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath $projectUv -PathType Leaf) -or
        -not (Test-Path -LiteralPath $projectPnpm -PathType Leaf)) {
        Write-Step 'Bootstrapping pinned project runtimes'
        if ($NetworkMode) { & $runtimeBootstrap -NetworkMode $NetworkMode }
        else { & $runtimeBootstrap }
        if ($LASTEXITCODE -ne 0) { throw "Pinned project runtime bootstrap failed (exit code $LASTEXITCODE)." }
    }

    Write-Step 'Checking the pinned Node.js runtime'
    $node = Set-ProjectNodePath
    Write-Host "Node: $((& $node --version).Trim())"

    Write-Step 'Preparing the Windows Python virtual environment'
    $venvPython = Join-Path $Root '.venv-runtime\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $venvVersion = (& $venvPython -c 'import sys;print(chr(46).join(map(str,sys.version_info[:3])))').Trim()
        if ($LASTEXITCODE -ne 0 -or $venvVersion -ne $versions.Python) {
            throw "Existing .venv-runtime uses Python $venvVersion. Do not delete it automatically; recreate it with Python $($versions.Python) after preserving any local work."
        }
        Write-Host "Reusing .venv-runtime (Python $venvVersion)."
    }
    else {
        Invoke-ProjectPythonBootstrap -Arguments @('-m', 'venv', (Join-Path $Root '.venv-runtime'))
    }
    $python = Get-ProjectPython

    Write-Step 'Installing pinned backend dependencies'
    $uv = Get-ProjectUv
    $env:UV_CACHE_DIR = Join-Path $Root '.tools\uv-cache'
    & $uv pip sync --python $python (Join-Path $Root 'backend\requirements.lock')
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed (exit code $LASTEXITCODE)." }

    Write-Step 'Installing pinned frontend dependencies'
    $pnpm = Get-ProjectPnpm
    Push-Location (Join-Path $Root 'frontend')
    try {
        & $pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed (exit code $LASTEXITCODE)." }
        if (-not $SkipFrontendBuild) {
            Write-Step 'Verifying the frontend production build'
            & $pnpm run build
            if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed (exit code $LASTEXITCODE)." }
        }
    }
    finally {
        Pop-Location
    }

    Write-Host "`nWindows development environment is ready." -ForegroundColor Green
    Write-Host 'Start with .\start.ps1. For a disposable local validation database, use .\start.ps1 -DatabaseBackend duckdb -DuckdbPath <absolute-path>.'
}
catch {
    Write-Host "`n[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
