$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RecoveryMarker = Join-Path $Root '.setup-recovery-required.json'

if (Test-Path -LiteralPath $RecoveryMarker) {
    throw 'A previous PostgreSQL replacement needs manual recovery. Review .setup-recovery-required.json before starting.'
}

# Start-Process fails when the inherited Windows environment contains both
# "Path" and "PATH" entries. Keep the effective value under one canonical key.
$processPath = $env:Path
[Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
[Environment]::SetEnvironmentVariable('Path', $null, 'Process')
[Environment]::SetEnvironmentVariable('Path', $processPath, 'Process')

$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
}
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$PidFile = Join-Path $Root '.server-pids.json'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python virtual environment was not found. Run setup.ps1 first.'
}

$pnpm = (Get-Command pnpm.cmd -ErrorAction SilentlyContinue).Source
if (-not $pnpm) { $pnpm = (Get-Command pnpm -ErrorAction SilentlyContinue).Source }
if (-not $pnpm) {
    $bundledPnpm = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd'
    if (Test-Path -LiteralPath $bundledPnpm) { $pnpm = $bundledPnpm }
}
if (-not $pnpm) { throw 'pnpm was not found. Run setup.ps1 first.' }

function Stop-ProcessTree([int]$ProcessIdentifier) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessIdentifier" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessIdentifier ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessIdentifier -Force -ErrorAction SilentlyContinue
}

function Wait-HttpReady([string]$Name, [string]$Uri, $Process, [int]$TimeoutSeconds = 40) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "$Name process exited before readiness."
        }
        $response = $null
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
        }
        catch {
            # Only transport failures are retried. Health contract failures below
            # are fatal and must not be swallowed by this catch block.
            Start-Sleep -Milliseconds 500
            continue
        }
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
            if ($Name -eq 'Backend') {
                $health = $response.Content | ConvertFrom-Json
                if ($health.database_backend -ne $script:databaseBackend) {
                    throw 'Backend health database mismatch.'
                }
            }
            Start-Sleep -Milliseconds 250
            $Process.Refresh()
            if ($Process.HasExited) {
                throw "$Name process exited during readiness verification."
            }
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name readiness timed out."
}

function Test-LocalPortInUse([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $attempt = $client.ConnectAsync('127.0.0.1', $Port)
        return $attempt.Wait(300) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

if (Test-Path -LiteralPath $PidFile) {
    try {
        $serverPids = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
        $runningServers = @($serverPids.backend, $serverPids.frontend) | Where-Object {
            $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue)
        }
        if ($runningServers.Count -gt 0) {
            throw 'Analysis Canvas is already running. Run stop.ps1 first.'
        }
        Remove-Item -LiteralPath $PidFile -Force
    }
    catch {
        if ($_.Exception.Message -like 'Analysis Canvas is already running*') { throw }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

Push-Location $Backend
try {
    $script:databaseBackend = (& $Python -c "from app.config import database_settings; print(database_settings().backend)").Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the database configuration.' }
    if ($script:databaseBackend -eq 'postgresql') {
        Write-Host 'Preparing the PostgreSQL schema...' -ForegroundColor Cyan
        & $Python 'scripts\upgrade_postgres_schema.py'
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL schema preparation failed.' }
        & $Python 'scripts\check_postgres_connection.py'
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL application-role preflight failed.' }
    }
    & $Python 'scripts\check_deployment_profile.py'
    if ($LASTEXITCODE -ne 0) { throw 'Deployment profile preflight failed.' }
}
finally {
    Pop-Location
}

if (Test-LocalPortInUse -Port 8000) { throw 'Backend port 8000 is already in use.' }
if (Test-LocalPortInUse -Port 5173) { throw 'Frontend port 5173 is already in use.' }

$backendProcess = $null
$frontendProcess = $null
$temporaryPidFile = "$PidFile.tmp"
try {
    $backendProcess = Start-Process -FilePath $Python `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
        -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Backend 'uvicorn.log') `
        -RedirectStandardError (Join-Path $Backend 'uvicorn-error.log')
    Wait-HttpReady -Name 'Backend' -Uri 'http://127.0.0.1:8000/api/health' -Process $backendProcess

    $frontendProcess = Start-Process -FilePath $pnpm -ArgumentList 'run', 'dev', '--', '--port', '5173', '--strictPort' `
        -WorkingDirectory $Frontend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Frontend 'vite.log') `
        -RedirectStandardError (Join-Path $Frontend 'vite-error.log')
    Wait-HttpReady -Name 'Frontend' -Uri 'http://127.0.0.1:5173' -Process $frontendProcess

    @{
        backend = $backendProcess.Id
        frontend = $frontendProcess.Id
    } | ConvertTo-Json | Set-Content -LiteralPath $temporaryPidFile -Encoding UTF8
    Move-Item -LiteralPath $temporaryPidFile -Destination $PidFile -Force
}
catch {
    if ($frontendProcess -and -not $frontendProcess.HasExited) { Stop-ProcessTree -ProcessIdentifier $frontendProcess.Id }
    if ($backendProcess -and -not $backendProcess.HasExited) { Stop-ProcessTree -ProcessIdentifier $backendProcess.Id }
    Remove-Item -LiteralPath $temporaryPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host 'Analysis Canvas started successfully.' -ForegroundColor Cyan
Write-Host 'Dashboard: http://127.0.0.1:5173'
Write-Host 'API docs : http://127.0.0.1:8000/docs'
