[CmdletBinding()]
param(
    [ValidateSet('duckdb', 'postgresql')]
    [string]$DatabaseBackend = '',
    [string]$DuckdbPath = '',
    [ValidateRange(1, 65535)]
    [int]$BackendPort = 8000,
    [ValidateRange(1, 65535)]
    [int]$FrontendPort = 80,
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
    if ($NetworkMode) { $null = Initialize-DeploymentNetwork -SettingsPath (Join-Path $Root 'deploy\windows\network-settings.json') -Mode $NetworkMode }
    else { $null = Initialize-DeploymentNetwork -SettingsPath (Join-Path $Root 'deploy\windows\network-settings.json') }
}
elseif ($NetworkMode) {
    throw 'Network.psm1 was not found. Extract the complete source archive and retry.'
}

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
$WebAccessConfig = Join-Path $Backend 'scripts\windows_web_access.py'
$Vite = Join-Path $Frontend 'node_modules\vite\bin\vite.js'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python virtual environment was not found. Run setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath $WebAccessConfig -PathType Leaf)) {
    throw 'Windows web access configuration was not found. Run setup.ps1 first.'
}
if (-not (Test-Path -LiteralPath $Vite -PathType Leaf)) {
    throw 'Vite was not found. Run setup.ps1 first.'
}

$webAccessOutput = & $Python $WebAccessConfig | Out-String
if ($LASTEXITCODE -ne 0) { throw 'Could not read the Windows web listener configuration.' }
$webAccess = $webAccessOutput | ConvertFrom-Json
$FrontendHost = [string]$webAccess.host
if ($FrontendHost -notin @('127.0.0.1', '0.0.0.0') -or [string]$webAccess.mode -notin @('local', 'lan')) {
    throw 'Windows web listener configuration returned an unsupported setting.'
}
if (-not $PSBoundParameters.ContainsKey('FrontendPort')) { $FrontendPort = [int]$webAccess.frontend_port }
$AppBasePath = [string]$webAccess.app_base_path
if ([string]::IsNullOrWhiteSpace($FrontendHost) -or $FrontendPort -lt 1 -or [string]::IsNullOrWhiteSpace($AppBasePath)) {
    throw 'Windows web listener configuration was incomplete.'
}
if ($FrontendHost -eq '0.0.0.0') {
    $authCheckOutput = & $Python $WebAccessConfig '--check-auth' 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "LAN authentication preflight failed. $authCheckOutput" }
}
$Node = Set-ProjectNodePath

if ($DuckdbPath -and $DatabaseBackend -ne 'duckdb') { throw '-DuckdbPath requires -DatabaseBackend duckdb.' }
if ($DatabaseBackend) { $env:ANALYSIS_DB_BACKEND = $DatabaseBackend }
if ($DuckdbPath) {
    $isAbsoluteWindowsPath = $DuckdbPath -match '^[A-Za-z]:[\\/]' -or $DuckdbPath -match '^\\\\[^\\/]+[\\/][^\\/]+'
    if (-not $isAbsoluteWindowsPath) { throw '-DuckdbPath must be an absolute path.' }
    $env:ANALYSIS_DUCKDB_PATH = [System.IO.Path]::GetFullPath($DuckdbPath)
    Write-Host "Using the process-only DuckDB path: $env:ANALYSIS_DUCKDB_PATH" -ForegroundColor Yellow
}

function Test-FrontendHostRecord($Record, [string]$ExpectedHost) {
    return $null -ne $Record -and [string]$Record.host -eq $ExpectedHost
}

function Test-FrontendBasePathRecord($Record, [string]$ExpectedBasePath) {
    return $null -ne $Record -and [string]$Record.basePath -eq $ExpectedBasePath
}

function Get-WebUrl([string]$Address, [int]$Port, [string]$Path) {
    $normalizedPath = if ([string]::IsNullOrWhiteSpace($Path)) { '/' } elseif ($Path.StartsWith('/')) { $Path } else { "/$Path" }
    if (-not $normalizedPath.EndsWith('/')) { $normalizedPath += '/' }
    $portSuffix = if ($Port -eq 80) { '' } else { ":$Port" }
    return "http://$Address$portSuffix$normalizedPath"
}

function Get-LanDashboardUrls([int]$Port, [string]$BasePath) {
    $urls = @()
    try {
        foreach ($address in @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop)) {
            $ip = [string]$address.IPAddress
            if ($ip -eq '127.0.0.1' -or $ip -like '169.254.*' -or $address.AddressState -ne 'Preferred') { continue }
            try {
                $adapter = Get-NetAdapter -InterfaceIndex $address.InterfaceIndex -ErrorAction Stop
                if ($adapter.Status -ne 'Up') { continue }
            }
            catch { continue }
            $baseUrl = Get-WebUrl -Address $ip -Port $Port -Path $BasePath
            $url = $baseUrl
            if ($urls -notcontains $url) { $urls += $url }
        }
    }
    catch { }
    return $urls
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
        if ($Process.HasExited) { throw "$Name process exited before readiness." }
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
                    if ($health.database_backend -ne $script:databaseBackend) { throw 'Backend health database mismatch.' }
                }
                Start-Sleep -Milliseconds 250
                $Process.Refresh()
                if ($Process.HasExited) { throw "$Name process exited during readiness verification." }
                return
            }
        }
        catch {
            if ($_.Exception.Message -eq 'Backend health database mismatch.' -or $_.Exception.Message -like "$Name process exited*") { throw }
            $baseException = $_.Exception.GetBaseException()
            if ($baseException -is [Net.WebException]) {
                $lastObservation = "transport error: $($baseException.GetType().Name), status $($baseException.Status)"
            }
            else { $lastObservation = "transport or response error: $($baseException.GetType().Name)" }
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

function Test-CurrentServerEndpoint([ValidateSet('backend', 'frontend')][string]$Role, [int]$Port) {
    try {
        if ($Role -eq 'frontend') {
            $response = Invoke-LocalHttp -Uri "http://127.0.0.1:$Port$AppBasePath" -TimeoutMilliseconds 2000
            return $response.StatusCode -eq 200 -and
                $response.Content -match '<title>\s*(Analysis Canvas|VD simulation workbench)\s*</title>' -and
                $response.Content -match 'src/main\.tsx'
        }
        return (Get-CurrentBackendDatabaseBackend -Port $Port) -eq $script:databaseBackend
    }
    catch { return $false }
}

function Test-CurrentServerRecord($Record, [int]$ExpectedPort, [ValidateSet('backend', 'frontend')][string]$Role, [string]$ExpectedExecutable) {
    if ($null -eq $Record -or -not ($Record.PSObject.Properties.Name -contains 'id') -or -not ($Record.PSObject.Properties.Name -contains 'startedAtUtc')) { return $false }
    if ($Record.PSObject.Properties.Name -contains 'port' -and [int]$Record.port -ne $ExpectedPort) { return $false }
    $process = Get-Process -Id ([int]$Record.id) -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    try {
        $expected = if ($Record.startedAtUtc -is [DateTime]) { $Record.startedAtUtc.ToUniversalTime() }
        elseif ($Record.startedAtUtc -is [DateTimeOffset]) { $Record.startedAtUtc.UtcDateTime }
        else { [DateTime]::Parse([string]$Record.startedAtUtc).ToUniversalTime() }
        if ($process.StartTime.ToUniversalTime().Ticks -ne $expected.Ticks) { return $false }
        $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.id)" -ErrorAction SilentlyContinue
        $commandLine = if ($identity) { [string]$identity.CommandLine } else { '' }
        $executablePath = if ($identity) { [string]$identity.ExecutablePath } else { [string]$process.Path }
        if (-not $executablePath -and $process.Path) { $executablePath = [string]$process.Path }
        if (-not [string]::Equals([System.IO.Path]::GetFullPath($executablePath), [System.IO.Path]::GetFullPath($ExpectedExecutable), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        $portPattern = [regex]::Escape([string]$ExpectedPort)
        if ($Role -eq 'frontend' -and ($commandLine -notmatch '(?i)vite' -or $commandLine -notmatch "(?i)--port\s+$portPattern(?!\d)")) { return $false }
        if ($Role -eq 'backend' -and ($commandLine -notmatch '(?i)uvicorn' -or $commandLine -notmatch '(?i)app\.main:app' -or $commandLine -notmatch "(?i)--port\s+$portPattern(?!\d)")) { return $false }
        return Test-CurrentServerEndpoint -Role $Role -Port $ExpectedPort
    }
    catch { return $false }
}

function Test-RecordedProcessExists($Record) {
    if ($null -eq $Record) { return $false }
    $recordedId = if ($Record.PSObject.Properties.Name -contains 'id') { [string]$Record.id } else { [string]$Record }
    $parsedId = 0
    if (-not [int]::TryParse($recordedId, [ref]$parsedId) -or $parsedId -lt 1) { return $false }
    return [bool](Get-Process -Id $parsedId -ErrorAction SilentlyContinue)
}

Push-Location $Backend
try {
    $script:databaseBackend = (& $Python -c "from app.config import database_settings; print(database_settings().backend)").Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the database configuration.' }
    & $Python 'scripts\check_database_startup_preflight.py'
    if ($LASTEXITCODE -ne 0) { throw 'Database configuration preflight failed. Review the existing DuckDB or PostgreSQL selection before starting.' }
    if ($script:databaseBackend -eq 'postgresql') {
        & $Python 'scripts\check_postgres_schema.py'
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL schema check failed. Run deploy.bat or update.bat to back up and apply pending migrations.' }
    }
    & $Python 'scripts\check_deployment_profile.py'
    if ($LASTEXITCODE -ne 0) { throw 'Deployment profile preflight failed.' }
}
finally { Pop-Location }

if (Test-Path -LiteralPath $PidFile) {
    try { $serverPids = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json }
    catch { throw 'The server PID metadata is invalid. It was preserved; run stop.ps1 after reviewing .server-pids.json.' }
    if ($null -eq $serverPids -or $null -eq $serverPids.backend -or $null -eq $serverPids.frontend) {
        throw 'The server PID metadata is incomplete. It was preserved; run stop.ps1 after reviewing .server-pids.json.'
    }
    $runningBackend = Get-CurrentBackendDatabaseBackend -Port $BackendPort
    if ($runningBackend -and $runningBackend -ne $script:databaseBackend) {
        throw "A healthy Analysis Canvas backend is using $runningBackend, but this start request selects $script:databaseBackend. Run stop.ps1 and start again with the intended database configuration."
    }
    $backendRunning = Test-CurrentServerRecord $serverPids.backend $BackendPort 'backend' $Python
    $frontendRunning = Test-CurrentServerRecord $serverPids.frontend $FrontendPort 'frontend' $Node
    if ($backendRunning -and $frontendRunning) {
        if (-not (Test-FrontendHostRecord $serverPids.frontend $FrontendHost) -or
            -not (Test-FrontendBasePathRecord $serverPids.frontend $AppBasePath)) {
            throw 'The requested web host or base path changed. Run stop.ps1, then start.ps1 to apply the new listener setting.'
        }
        Push-Location $Backend
        try {
            & $Python 'scripts\setup_accounts.py' '--check'
            if ($LASTEXITCODE -ne 0) { throw 'Account setup preflight failed. Run setup-accounts.bat before starting the web server.' }
        }
        finally { Pop-Location }
        $dashboardUrl = Get-WebUrl -Address '127.0.0.1' -Port $FrontendPort -Path $AppBasePath
        if (-not $NoBrowser) { Start-Process -FilePath $dashboardUrl }
        Write-Host "Analysis Canvas is already running. Dashboard: $dashboardUrl" -ForegroundColor Cyan
        if ($FrontendHost -eq '0.0.0.0') {
            foreach ($url in @(Get-LanDashboardUrls -Port $FrontendPort -BasePath $AppBasePath)) { Write-Host "LAN: $url" }
        }
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
        & $Python 'scripts\check_postgres_connection.py'
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL application-role preflight failed.' }
    }
    & $Python 'scripts\setup_accounts.py' '--check'
    if ($LASTEXITCODE -ne 0) { throw 'Account setup preflight failed. Run setup-accounts.bat before starting the web server.' }
}
finally {
    Pop-Location
}

if (Test-LocalPortInUse -Port $BackendPort) { throw "Backend port $BackendPort is already in use." }
if (Test-LocalPortInUse -Port $FrontendPort) { throw "Frontend port $FrontendPort is already in use." }

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

    $previousBasePath = $env:VITE_APP_BASE_PATH
    $previousApiTarget = $env:VITE_API_TARGET
    $env:VITE_APP_BASE_PATH = $AppBasePath
    $env:VITE_API_TARGET = "http://127.0.0.1:$BackendPort"
    try {
        $frontendProcess = Start-Process -FilePath $Node -ArgumentList ('"{0}"' -f $Vite), '--host', $FrontendHost, '--port', ([string]$FrontendPort), '--strictPort' `
            -WorkingDirectory $Frontend -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $Frontend 'vite.log') `
            -RedirectStandardError (Join-Path $Frontend 'vite-error.log')
    }
    finally {
        if ($null -eq $previousBasePath) { Remove-Item Env:VITE_APP_BASE_PATH -ErrorAction SilentlyContinue } else { $env:VITE_APP_BASE_PATH = $previousBasePath }
        if ($null -eq $previousApiTarget) { Remove-Item Env:VITE_API_TARGET -ErrorAction SilentlyContinue } else { $env:VITE_API_TARGET = $previousApiTarget }
    }
    Wait-HttpReady -Name 'Frontend' -Role 'frontend' -Uri "http://127.0.0.1:$FrontendPort$AppBasePath" -Process $frontendProcess -TimeoutSeconds $StartupTimeoutSeconds

    @{
        backend = @{ id = $backendProcess.Id; port = $BackendPort; startedAtUtc = $backendProcess.StartTime.ToUniversalTime().ToString('o') }
        frontend = @{ id = $frontendProcess.Id; port = $FrontendPort; host = $FrontendHost; basePath = $AppBasePath; startedAtUtc = $frontendProcess.StartTime.ToUniversalTime().ToString('o') }
        frontendPort = $FrontendPort
        basePath = $AppBasePath
        host = $FrontendHost
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryPidFile -Encoding UTF8
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
$dashboardUrl = Get-WebUrl -Address '127.0.0.1' -Port $FrontendPort -Path $AppBasePath
Write-Host "Dashboard: $dashboardUrl"
if ($FrontendHost -eq '0.0.0.0') {
    $computerName = [System.Net.Dns]::GetHostName().ToLowerInvariant()
    Write-Host "Computer:  $(Get-WebUrl -Address $computerName -Port $FrontendPort -Path $AppBasePath)"
    foreach ($url in @(Get-LanDashboardUrls -Port $FrontendPort -BasePath $AppBasePath)) { Write-Host "LAN:       $url" }
}
Write-Host "API docs : http://127.0.0.1:$BackendPort/docs"
if (-not $NoBrowser) { Start-Process -FilePath $dashboardUrl }
$ErrorActionPreference = 'Stop'
