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

function Test-EsbuildTemporaryFileSharingViolation {
    param([string]$Output)

    # Vite/esbuild reports this Windows sharing violation while removing its
    # hashed temporary file. Keep the retry signature narrow so unrelated
    # frontend build failures still stop immediately.
    return $Output -match '(?i)\[vite:esbuild-transpile\]\s+remove\s+[^\r\n]*?[\\/]Temp[\\/]esbuild-[0-9a-f]{64}\s*:\s*The process cannot access the file because it is being used by another process\.'
}

function Invoke-FrontendBuild {
    param(
        [string]$PnpmPath,
        [string]$WorkingDirectory,
        [int]$MaxAttempts = 2,
        [int]$RetryDelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $logId = [Guid]::NewGuid().ToString('N')
        $stdoutPath = Join-Path ([IO.Path]::GetTempPath()) ("workbench-frontend-build-$logId.stdout")
        $stderrPath = Join-Path ([IO.Path]::GetTempPath()) ("workbench-frontend-build-$logId.stderr")
        try {
            $process = Start-Process -FilePath $PnpmPath -ArgumentList @('run', 'build') `
                -WorkingDirectory $WorkingDirectory -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
            $stdout = if (Test-Path -LiteralPath $stdoutPath) { [IO.File]::ReadAllText($stdoutPath) } else { '' }
            $stderr = if (Test-Path -LiteralPath $stderrPath) { [IO.File]::ReadAllText($stderrPath) } else { '' }

            # Print captured output from both streams before handling the exit
            # code so operators retain pnpm/Vite diagnostics on every attempt.
            if ($stdout) { [Console]::Out.Write($stdout) }
            if ($stderr) { [Console]::Error.Write($stderr) }
            if ($process.ExitCode -eq 0) { return }

            $combinedOutput = $stdout + "`n" + $stderr
            if (($attempt -lt $MaxAttempts) -and (Test-EsbuildTemporaryFileSharingViolation -Output $combinedOutput)) {
                Write-Warning "Detected the known Windows esbuild temporary-file sharing violation. Retrying frontend build ($($attempt + 1)/$MaxAttempts) in $RetryDelaySeconds seconds."
                Start-Sleep -Seconds $RetryDelaySeconds
                continue
            }

            if (Test-EsbuildTemporaryFileSharingViolation -Output $combinedOutput) {
                throw "Frontend production build failed after $MaxAttempts attempts due to a persistent Windows esbuild temporary-file sharing violation (exit code $($process.ExitCode))."
            }
            throw "Frontend production build failed (exit code $($process.ExitCode))."
        }
        finally {
            Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
        }
    }
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
                Invoke-FrontendBuild -PnpmPath $projectPnpm -WorkingDirectory (Join-Path $Root 'frontend')
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
