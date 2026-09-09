[CmdletBinding()]
param(
    [ValidateSet('duckdb', 'postgresql')]
    [string]$DatabaseBackend = '',
    [string]$DuckdbPath = '',
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 5173,
    [switch]$NoBrowser,
    [ValidateSet('auto', 'direct', 'proxy')]
    [string]$NetworkMode = '',
    [ValidateRange(5, 600)]
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RecoveryMarker = Join-Path $Root '.setup-recovery-required.json'
Import-Module (Join-Path $Root 'scripts\windows\Runtime.psm1') -Force
$LocalHttpModule = Join-Path $Root 'scripts\windows\LocalHttp.psm1'
if (-not (Test-Path -LiteralPath $LocalHttpModule -PathType Leaf)) {
    throw 'LocalHttp.psm1 was not found. Extract the complete source archive and retry.'
}
Import-Module $LocalHttpModule -Force
$NetworkModule = Join-Path $Root 'scripts\windows\Network.psm1'
if (Test-Path -LiteralPath $NetworkModule -PathType Leaf) {
    Import-Module $NetworkModule -Force
    $networkSummary = if ($NetworkMode) { Initialize-DeploymentNetwork -SettingsPath (Join-Path $Root 'deploy\windows\network-settings.json') -Mode $NetworkMode } else { Initialize-DeploymentNetwork -SettingsPath (Join-Path $Root 'deploy\windows\network-settings.json') }
}
elseif ($NetworkMode) {
    throw 'Network.psm1 was not found. Extract the complete source archive and retry.'
}

if (Test-Path -LiteralPath $RecoveryMarker) {
    throw 'A previous PostgreSQL replacement needs manual recovery. Review .setup-recovery-required.json before starting.'
}

$Node = Set-ProjectNodePath
$Python = Get-ProjectPython
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$PidFile = Join-Path $Root '.server-pids.json'
$Vite = Join-Path $Frontend 'node_modules\vite\bin\vite.js'
if (-not (Test-Path -LiteralPath $Vite -PathType Leaf)) {
    throw 'Vite was not found. Run setup.ps1 first.'
}

if ($DuckdbPath -and $DatabaseBackend -ne 'duckdb') {
    throw '-DuckdbPath requires -DatabaseBackend duckdb.'
}
if ($DatabaseBackend) {
    $env:ANALYSIS_DB_BACKEND = $DatabaseBackend
}
if ($DuckdbPath) {
    $isAbsoluteWindowsPath = $DuckdbPath -match '^[A-Za-z]:[\\/]' -or $DuckdbPath -match '^\\\\[^\\/]+[\\/][^\\/]+'
    if (-not $isAbsoluteWindowsPath) {
        throw '-DuckdbPath must be an absolute path.'
    }
    $env:ANALYSIS_DUCKDB_PATH = [System.IO.Path]::GetFullPath($DuckdbPath)
    Write-Host "Using the process-only DuckDB path: $env:ANALYSIS_DUCKDB_PATH" -ForegroundColor Yellow
}

function Stop-ProcessTree([int]$ProcessIdentifier) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessIdentifier" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessIdentifier ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessIdentifier -Force -ErrorAction SilentlyContinue
}

function Wait-HttpReady([string]$Name, [string]$Uri, $Process, [ValidateSet('backend', 'frontend')][string]$Role, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastObservation = 'no response received'
    $logPaths = if ($Role -eq 'backend') {
        "$(Join-Path $Backend 'uvicorn.log'), $(Join-Path $Backend 'uvicorn-error.log')"
    }
    else {
        "$(Join-Path $Frontend 'vite.log'), $(Join-Path $Frontend 'vite-error.log')"
    }
    while ([DateTime]::UtcNow -lt $deadline) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "$Name process exited before readiness."
        }
        try {
            $response = Invoke-LocalHttp -Uri $Uri -TimeoutMilliseconds 2000
            $lastObservation = "HTTP $($response.StatusCode) $($response.StatusDescription)"
            if ($response.StatusCode -eq 200) {
                if ($Role -eq 'backend') {
                    try { $health = $response.Content | ConvertFrom-Json }
                    catch {
                        $lastObservation = 'HTTP response did not contain valid JSON health data'
                        Start-Sleep -Milliseconds 500
                        continue
                    }
                    if ($health.status -ne 'ok') {
                        $lastObservation = 'HTTP 200 health status was not ok'
                        Start-Sleep -Milliseconds 500
                        continue
                    }
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
        }
        catch {
            if ($_.Exception.Message -eq 'Backend health database mismatch.' -or $_.Exception.Message -like "$Name process exited*") { throw }
            $baseException = $_.Exception.GetBaseException()
            if ($baseException -is [Net.WebException]) {
                $lastObservation = "transport error: $($baseException.GetType().Name), status $($baseException.Status)"
            }
            else {
                $lastObservation = "transport or response error: $($baseException.GetType().Name)"
            }
            Start-Sleep -Milliseconds 500
            continue
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name readiness timed out at local URI $Uri (last $lastObservation). Check service logs: $logPaths."
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

function Test-CurrentServerEndpoint([string]$Role, [int]$Port) {
    try {
        if ($Role -eq 'frontend') {
            $response = Invoke-LocalHttp -Uri "http://127.0.0.1:$Port/" -TimeoutMilliseconds 2000
            return $response.StatusCode -eq 200 -and $response.Content -match '<title>\s*VD simulation workbench\s*</title>' -and $response.Content -match '/src/main\.tsx'
        }
        return (Get-CurrentBackendDatabaseBackend -Port $Port) -eq $script:databaseBackend
    }
    catch { return $false }
}

function Get-CurrentBackendDatabaseBackend([int]$Port) {
    try {
        $response = Invoke-LocalHttp -Uri "http://127.0.0.1:$Port/api/health" -TimeoutMilliseconds 2000
        if ($response.StatusCode -ne 200) { return $null }
        $health = $response.Content | ConvertFrom-Json
        if ($health.status -ne 'ok' -or $health.database_backend -notin @('duckdb', 'postgresql')) { return $null }
        return [string]$health.database_backend
    }
    catch { return $null }
}

function Test-CurrentServerRecord($Record, [int]$ExpectedPort, [ValidateSet('backend', 'frontend')][string]$Role, [string]$ExpectedExecutable, [string]$ExpectedScript = '') {
    if ($null -eq $Record -or -not ($Record.PSObject.Properties.Name -contains 'id') -or -not ($Record.PSObject.Properties.Name -contains 'startedAtUtc')) {
        return $false
    }
    if ($Record.PSObject.Properties.Name -contains 'port' -and [int]$Record.port -ne $ExpectedPort) { return $false }
    $process = Get-Process -Id ([int]$Record.id) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    try {
        $expected = if ($Record.startedAtUtc -is [DateTime]) {
            $Record.startedAtUtc.ToUniversalTime()
        }
        elseif ($Record.startedAtUtc -is [DateTimeOffset]) {
            $Record.startedAtUtc.UtcDateTime
        }
        else {
            [DateTime]::Parse([string]$Record.startedAtUtc).ToUniversalTime()
        }
        if ($process.StartTime.ToUniversalTime().Ticks -ne $expected.Ticks) { return $false }
        $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.id)" -ErrorAction SilentlyContinue
        $commandLine = if ($identity) { [string]$identity.CommandLine } else { '' }
        $executablePath = if ($identity) { [string]$identity.ExecutablePath } else { [string]$process.Path }
        if (-not $executablePath -and $process.Path) { $executablePath = [string]$process.Path }
        if (-not [string]::Equals([System.IO.Path]::GetFullPath($executablePath), [System.IO.Path]::GetFullPath($ExpectedExecutable), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        $portPattern = [regex]::Escape([string]$ExpectedPort)
        if ($Role -eq 'frontend' -and ($commandLine -notmatch '(?i)vite' -or $commandLine -notmatch "(?i)--port\s+$portPattern(?!\d)")) { return $false }
        if ($Role -eq 'backend' -and ($commandLine -notmatch '(?i)uvicorn' -or $commandLine -notmatch '(?i)app\.main:app' -or $commandLine -notmatch "(?i)--port\s+$portPattern(?!\d)")) { return $false }
        if ($ExpectedScript -and $commandLine.IndexOf($ExpectedScript, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
        return Test-CurrentServerEndpoint -Role $Role -Port $ExpectedPort
    }
    catch {
        return $false
    }
}

function Test-RecordedProcessExists($Record) {
    return $Record -and ($Record.PSObject.Properties.Name -contains 'id') -and (Get-Process -Id ([int]$Record.id) -ErrorAction SilentlyContinue)
}

Push-Location $Backend
try {
    # Read only the effective configuration and validate that it can safely
    # select a database before accepting an already-running service.
    $script:databaseBackend = (& $Python -c "from app.config import database_settings; print(database_settings().backend)").Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the database configuration.' }
    & $Python 'scripts\check_database_startup_preflight.py'
    if ($LASTEXITCODE -ne 0) {
        throw 'PostgreSQL configuration preflight failed. DATABASE_URL and the provisioned PostgreSQL server connection are required; DuckDB fallback was not used. PostgreSQL 연결 설정(DATABASE_URL)이 필요하며 DuckDB로 자동 전환하지 않습니다.'
    }
}
finally {
    Pop-Location
}

if (Test-Path -LiteralPath $PidFile) {
    try { $serverPids = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json }
    catch { throw 'The server PID metadata is invalid. It was preserved; run stop.ps1 after reviewing .server-pids.json.' }
    if ($null -eq $serverPids -or $null -eq $serverPids.backend -or $null -eq $serverPids.frontend) { throw 'The server PID metadata is incomplete. It was preserved; run stop.ps1 after reviewing .server-pids.json.' }
    $runningBackend = Get-CurrentBackendDatabaseBackend -Port $BackendPort
    if ($runningBackend -and $runningBackend -ne $script:databaseBackend) {
        throw "A healthy Analysis Canvas backend is using $runningBackend, but this start request selects $script:databaseBackend. Run stop.ps1 and start again with the intended database configuration. 실행 중인 정상 백엔드의 데이터베이스가 요청한 설정과 다릅니다. stop.ps1로 중지한 뒤 의도한 데이터베이스 설정으로 다시 시작하십시오."
    }
    $backendRunning = Test-CurrentServerRecord $serverPids.backend $BackendPort 'backend' $Python
    $frontendRunning = Test-CurrentServerRecord $serverPids.frontend $FrontendPort 'frontend' $Node $Vite
    if ($backendRunning -and $frontendRunning) {
        $dashboardUrl = "http://127.0.0.1:$FrontendPort/workspace/overview"
        if (-not $NoBrowser) { Start-Process -FilePath $dashboardUrl }
        Write-Host "Analysis Canvas is already running. Dashboard: $dashboardUrl" -ForegroundColor Cyan
        exit 0
    }
    if ((Test-RecordedProcessExists $serverPids.backend) -or (Test-RecordedProcessExists $serverPids.frontend)) {
        throw 'An existing Analysis Canvas process could not be verified as healthy. PID metadata was preserved; inspect logs or run stop.ps1.'
    }
    Remove-Item -LiteralPath $PidFile -Force
}

Push-Location $Backend
try {
    if ($script:databaseBackend -eq 'postgresql') {
        Write-Host 'Checking the existing PostgreSQL connection and schema (no migration will run)...' -ForegroundColor Cyan
        & $Python 'scripts\check_postgres_connection.py'
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL application-role preflight failed.' }
    }
    & $Python 'scripts\check_deployment_profile.py'
    if ($LASTEXITCODE -ne 0) { throw 'Deployment profile preflight failed.' }

    # Account bootstrap is server-local and intentionally blocks launch when
    # noninteractive execution cannot complete a required initial-admin setup.
    & $Python 'scripts\setup_accounts.py'
    if ($LASTEXITCODE -ne 0) { throw 'Account setup is pending or failed. Run setup-accounts.bat from an interactive server console.' }
}
finally {
    Pop-Location
}

if (Test-LocalPortInUse -Port $BackendPort) { throw "Backend port $BackendPort is already in use." }
if (Test-LocalPortInUse -Port $FrontendPort) { throw "Frontend port $FrontendPort is already in use." }

$env:VITE_API_TARGET = "http://127.0.0.1:$BackendPort"
$backendProcess = $null
$frontendProcess = $null
$temporaryPidFile = "$PidFile.tmp"
try {
    $backendProcess = Start-Process -FilePath $Python `
        -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', ([string]$BackendPort) `
        -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Backend 'uvicorn.log') `
        -RedirectStandardError (Join-Path $Backend 'uvicorn-error.log')
    Wait-HttpReady -Name 'Backend' -Role 'backend' -Uri "http://127.0.0.1:$BackendPort/api/health" -Process $backendProcess -TimeoutSeconds $StartupTimeoutSeconds

    $viteArgument = '"' + $Vite + '"'
    $frontendProcess = Start-Process -FilePath $Node -ArgumentList $viteArgument, '--host', '127.0.0.1', '--port', ([string]$FrontendPort), '--strictPort' `
        -WorkingDirectory $Frontend -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Frontend 'vite.log') `
        -RedirectStandardError (Join-Path $Frontend 'vite-error.log')
    Wait-HttpReady -Name 'Frontend' -Role 'frontend' -Uri "http://127.0.0.1:$FrontendPort" -Process $frontendProcess -TimeoutSeconds $StartupTimeoutSeconds

    @{
        backend = @{ id = $backendProcess.Id; port = $BackendPort; startedAtUtc = $backendProcess.StartTime.ToUniversalTime().ToString('o') }
        frontend = @{ id = $frontendProcess.Id; port = $FrontendPort; startedAtUtc = $frontendProcess.StartTime.ToUniversalTime().ToString('o') }
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
$dashboardUrl = "http://127.0.0.1:$FrontendPort/workspace/overview"
Write-Host "Dashboard: $dashboardUrl"
Write-Host "API docs : http://127.0.0.1:$BackendPort/docs"
if (-not $NoBrowser) { Start-Process -FilePath $dashboardUrl }
