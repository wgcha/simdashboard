[CmdletBinding()]
param(
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0
)

if (($BackendPort -ne 0 -and ($BackendPort -lt 1 -or $BackendPort -gt 65535)) -or ($FrontendPort -ne 0 -and ($FrontendPort -lt 1 -or $FrontendPort -gt 65535))) {
    throw 'BackendPort and FrontendPort must be 1~65535 when provided.'
}
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
$PidFile = Join-Path $Root '.server-pids.json'
$ExpectedPython = Join-Path $Root '.venv-runtime\Scripts\python.exe'
$NodeVersion = (Get-Content -Raw -LiteralPath (Join-Path $Root '.node-version')).Trim()
$ExpectedNode = Join-Path $Root ".tools\node-$NodeVersion-win-x64\node.exe"
$ExpectedVite = Join-Path $Root 'frontend\node_modules\vite\bin\vite.js'
$ManagedPorts = @(
    if ($BackendPort) { $BackendPort } else { 8000 }
    if ($FrontendPort) { $FrontendPort } else { 5173 }
) | Select-Object -Unique

function Get-ProcessIdentity([int]$TargetProcessId) {
    $process = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $null }

    $commandLine = ''
    $executablePath = ''
    try {
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetProcessId" -ErrorAction Stop
        if ($record) {
            $commandLine = [string]$record.CommandLine
            $executablePath = [string]$record.ExecutablePath
        }
    }
    catch {
        # Process metadata can be restricted by Windows policy. In that case the
        # listener is treated as unmanaged and is never terminated automatically.
    }

    if (-not $executablePath) {
        try { $executablePath = [string]$process.Path } catch { $executablePath = '' }
    }

    return [pscustomobject]@{
        Id = $TargetProcessId
        Name = [string]$process.ProcessName
        CommandLine = $commandLine
        ExecutablePath = $executablePath
    }
}

function Test-ContainsWorkspacePath([string]$Value) {
    return $Value -and $Value.IndexOf($RootPath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-IsAnalysisCanvasListener($Identity, [int]$Port) {
    if (-not $Identity) { return $false }
    $belongsToWorkspace = (Test-ContainsWorkspacePath $Identity.CommandLine) -or (Test-ContainsWorkspacePath $Identity.ExecutablePath)
    if (-not $belongsToWorkspace) { return $false }
    $portPattern = [regex]::Escape([string]$Port)
    $frontendCommand = $Identity.CommandLine -match '(?i)vite' -and
        $Identity.CommandLine.IndexOf($ExpectedVite, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $Identity.CommandLine -match "(?i)--port\s+$portPattern(?!\d)"
    $nodeMatches = [string]::IsNullOrWhiteSpace($Identity.ExecutablePath) -or -not (Test-Path -LiteralPath $ExpectedNode -PathType Leaf) -or [string]::Equals([System.IO.Path]::GetFullPath($Identity.ExecutablePath), [System.IO.Path]::GetFullPath($ExpectedNode), [System.StringComparison]::OrdinalIgnoreCase)
    if ($frontendCommand -and $nodeMatches) {
        return $true
    }
    $pythonMatches = [string]::Equals([System.IO.Path]::GetFullPath($Identity.ExecutablePath), [System.IO.Path]::GetFullPath($ExpectedPython), [System.StringComparison]::OrdinalIgnoreCase)
    if ($pythonMatches -and $Identity.CommandLine.IndexOf($ExpectedPython, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $Identity.CommandLine -match '(?i)uvicorn' -and
        $Identity.CommandLine -match '(?i)app\.main:app' -and
        $Identity.CommandLine -match "(?i)--port\s+$portPattern(?!\d)") {
        return $true
    }
    return $false
}

function Test-IsAnalysisCanvasEndpoint([int]$Port) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200 -and
            $response.Content -match '<title>\s*VD simulation workbench\s*</title>' -and
            $response.Content -match '/src/main\.tsx') {
            return $true
        }
    }
    catch { }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        $openApi = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/openapi.json" -TimeoutSec 2
        return $health.status -eq 'ok' -and
            $health.database_backend -in @('duckdb', 'postgresql') -and
            $openApi.info.title -eq 'Analysis Canvas API'
    }
    catch { return $false }
    return $false
}

function Test-ManagedServerRecord($Record, [int]$Port) {
    if ($null -eq $Record -or -not ($Record.PSObject.Properties.Name -contains 'id') -or -not ($Record.PSObject.Properties.Name -contains 'startedAtUtc')) {
        return $false
    }
    try {
        if ($Record.PSObject.Properties.Name -contains 'port' -and [int]$Record.port -ne $Port) { return $false }
        $expected = if ($Record.startedAtUtc -is [DateTime]) {
            $Record.startedAtUtc.ToUniversalTime()
        }
        elseif ($Record.startedAtUtc -is [DateTimeOffset]) {
            $Record.startedAtUtc.UtcDateTime
        }
        else {
            [DateTime]::Parse([string]$Record.startedAtUtc).ToUniversalTime()
        }
        $identity = Get-ProcessIdentity -TargetProcessId ([int]$Record.id)
        if (-not $identity) { return $false }
        $actual = (Get-Process -Id $identity.Id -ErrorAction Stop).StartTime.ToUniversalTime()
        if ($actual.Ticks -ne $expected.Ticks) { return $false }
        return Test-IsAnalysisCanvasListener -Identity $identity -Port $Port
    }
    catch {
        return $false
    }
}

function Get-RecordPort($Record, [int]$Fallback) {
    if ($Record -and $Record.PSObject.Properties.Name -contains 'port' -and [int]$Record.port -ge 1 -and [int]$Record.port -le 65535) {
        return [int]$Record.port
    }
    return $Fallback
}

function Stop-ProcessTree([int]$TargetProcessId) {
    $stopped = 0
    $children = @()
    try {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $TargetProcessId" -ErrorAction Stop)
    }
    catch {
        # The listener recovery pass below still stops an identifiable child that
        # owns port 8000 or 5173 when child-process enumeration is restricted.
    }
    foreach ($child in $children) {
        $stopped += Stop-ProcessTree -TargetProcessId ([int]$child.ProcessId)
    }
    if (Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
        $stopped += 1
    }
    return $stopped
}

function Get-PortListeners([int]$Port) {
    $owners = @{}
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        try {
            foreach ($listener in @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)) {
                $owners[[int]$listener.OwningProcess] = [pscustomobject]@{ OwningProcess = [int]$listener.OwningProcess }
            }
        }
        catch {
            # Standard user policies can deny the CIM-backed cmdlet. netstat does
            # not require that access and provides the same listener PID safely.
        }
    }
    if (Get-Command netstat.exe -ErrorAction SilentlyContinue) {
        $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
        foreach ($line in @(& netstat.exe -ano -p tcp)) {
            if ($line -match $pattern) {
                $ownerId = [int]$Matches[1]
                $owners[$ownerId] = [pscustomobject]@{ OwningProcess = $ownerId }
            }
        }
    }
    return @($owners.Values)
}

$stoppedCount = 0
$hadPidFile = Test-Path -LiteralPath $PidFile

if ($hadPidFile) {
    try {
        $serverPids = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
        $backendRecordPort = if ($BackendPort) { $BackendPort } else { Get-RecordPort $serverPids.backend 8000 }
        $frontendRecordPort = if ($FrontendPort) { $FrontendPort } else { Get-RecordPort $serverPids.frontend 5173 }
        $ManagedPorts = @($backendRecordPort, $frontendRecordPort) | Select-Object -Unique
        foreach ($serverRecord in @(
            [pscustomobject]@{ Value = $serverPids.backend; Port = $backendRecordPort },
            [pscustomobject]@{ Value = $serverPids.frontend; Port = $frontendRecordPort }
        )) {
            if (Test-ManagedServerRecord -Record $serverRecord.Value -Port $serverRecord.Port) {
                $stoppedCount += Stop-ProcessTree -TargetProcessId ([int]$serverRecord.Value.id)
            }
            elseif ($serverRecord.Value) {
                Write-Warning "Ignoring unverified server PID metadata for port $($serverRecord.Port)."
            }
        }
    }
    catch {
        Write-Warning 'The server PID file was invalid. Falling back to verified port-owner recovery.'
    }
    finally {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
}

# A package runner can outlive the PID recorded by Start-Process, and an old PID
# file can be missing after an interrupted startup. Recover only listeners whose
# workspace path and expected command, or the app's unique HTTP contract,
# identifies this project. An unverified listener is never terminated.
Start-Sleep -Milliseconds 250
foreach ($port in $ManagedPorts) {
    foreach ($listener in (Get-PortListeners -Port $port)) {
        $identity = Get-ProcessIdentity -TargetProcessId ([int]$listener.OwningProcess)
        if (Test-IsAnalysisCanvasListener -Identity $identity -Port $port) {
            Write-Host "Recovering Analysis Canvas listener on port $port (PID $($listener.OwningProcess))." -ForegroundColor Yellow
            $stoppedCount += Stop-ProcessTree -TargetProcessId ([int]$listener.OwningProcess)
        }
    }
}

Start-Sleep -Milliseconds 250
$remainingListeners = @()
foreach ($port in $ManagedPorts) {
    foreach ($listener in (Get-PortListeners -Port $port)) {
        $identity = Get-ProcessIdentity -TargetProcessId ([int]$listener.OwningProcess)
        $remainingListeners += [pscustomobject]@{
            Port = $port
            ProcessId = [int]$listener.OwningProcess
            ProcessName = if ($identity) { $identity.Name } else { 'unknown' }
        }
    }
}

if ($remainingListeners.Count -gt 0) {
    foreach ($listener in $remainingListeners) {
        Write-Host "[ERROR] Port $($listener.Port) is still owned by unmanaged process PID $($listener.ProcessId) ($($listener.ProcessName))." -ForegroundColor Red
    }
    Write-Host 'The process was not terminated because it could not be verified as an Analysis Canvas server.' -ForegroundColor Yellow
    exit 2
}

if ($stoppedCount -gt 0) {
    Write-Host "Analysis Canvas servers stopped successfully ($stoppedCount process(es))."
}
elseif ($hadPidFile) {
    Write-Host 'No live Analysis Canvas server processes remained; stale PID metadata was removed.'
}
else {
    Write-Host 'No Analysis Canvas server processes were running.'
}
