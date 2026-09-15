[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [ValidateSet('auto', 'direct', 'proxy')]
    [string]$NetworkMode = ''
)

# This is deliberately runtime-only.  Database provisioning, replacement, and
# migration are orchestrated by deploy.ps1 so that they cannot be hidden in a
# dependency-install step.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Invoke-Stage {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "`n==> $Name" -ForegroundColor Cyan
    & $Action
}

try {
    Set-Location -LiteralPath $Root
    $runtimeModule = Join-Path $Root 'scripts\windows\Runtime.psm1'
    $bootstrapScript = Join-Path $Root 'scripts\windows\bootstrap-runtime.ps1'
    if (-not (Test-Path -LiteralPath $runtimeModule -PathType Leaf) -or -not (Test-Path -LiteralPath $bootstrapScript -PathType Leaf)) {
        throw 'The Windows runtime preparation files are missing. Extract the complete source package and retry.'
    }

    Import-Module $runtimeModule -Force
    $versions = Get-ExpectedRuntimeVersions
    $node = Join-Path $Root ('.tools\node-{0}-win-x64\node.exe' -f $versions.Node)
    $python = Join-Path $Root ('.tools\python\cpython-{0}-windows-x86_64-none\python.exe' -f $versions.Python)
    $uv = Join-Path $Root '.tools\uv\uv.exe'
    $pnpm = Join-Path $Root '.tools\pnpm.cmd'
    if (-not (Test-Path -LiteralPath $node -PathType Leaf) -or
        -not (Test-Path -LiteralPath $python -PathType Leaf) -or
        -not (Test-Path -LiteralPath $uv -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pnpm -PathType Leaf)) {
        Invoke-Stage 'Bootstrapping pinned Node.js, Python, uv, and pnpm runtimes' {
            $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $bootstrapScript)
            if ($NetworkMode) { $arguments += @('-NetworkMode', $NetworkMode) }
            & powershell.exe @arguments
            if ($LASTEXITCODE -ne 0) { throw "Pinned runtime bootstrap failed (exit code $LASTEXITCODE)." }
        }
    }

    Invoke-Stage 'Checking pinned Node.js runtime' {
        $null = Set-ProjectNodePath
    }
    $venvPython = Join-Path $Root '.venv-runtime\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Invoke-Stage 'Creating the project Python environment' {
            Invoke-ProjectPythonBootstrap -Arguments @('-m', 'venv', (Join-Path $Root '.venv-runtime'))
        }
    }
    $venvPython = Get-ProjectPython

    Invoke-Stage 'Installing pinned backend dependencies' {
        $projectUv = Get-ProjectUv
        $env:UV_CACHE_DIR = Join-Path $Root '.tools\uv-cache'
        & $projectUv pip sync --python $venvPython (Join-Path $Root 'backend\requirements.lock')
        if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed (exit code $LASTEXITCODE)." }
    }

    Invoke-Stage 'Installing pinned frontend dependencies' {
        $projectPnpm = Get-ProjectPnpm
        Push-Location (Join-Path $Root 'frontend')
        try {
            & $projectPnpm install --frozen-lockfile
            if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed (exit code $LASTEXITCODE)." }
            if (-not $SkipFrontendBuild) {
                & $projectPnpm run build
                if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed (exit code $LASTEXITCODE)." }
            }
        }
        finally { Pop-Location }
    }
    Write-Host "Windows source runtime is ready: Python $($versions.Python), Node $($versions.Node), pnpm $($versions.Pnpm)." -ForegroundColor Green
}
catch {
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
