$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
$PidFile = Join-Path $Root '.server-pids.json'
$ManagedPorts = @(8000, 5173)

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
    if ($Identity) {
        $belongsToWorkspace = (Test-ContainsWorkspacePath $Identity.CommandLine) -or (Test-ContainsWorkspacePath $Identity.ExecutablePath)
        if ($belongsToWorkspace -and $Port -eq 5173 -and
            $Identity.CommandLine -match '(?i)vite' -and
            $Identity.CommandLine -match '(?i)(--port\s+5173|vite(?:\.js)?\b)') {
            return $true
        }
        if ($belongsToWorkspace -and $Port -eq 8000 -and
            $Identity.CommandLine -match '(?i)uvicorn' -and
            $Identity.CommandLine -match '(?i)app\.main:app' -and
            $Identity.CommandLine -match '(?i)--port\s+8000') {
            return $true
        }
    }
    return Test-IsAnalysisCanvasEndpoint -Port $Port
}

function Test-IsAnalysisCanvasEndpoint([int]$Port) {
    try {
        if ($Port -eq 5173) {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2
            return $response.StatusCode -eq 200 -and
                $response.Content -match '<title>\s*Analysis Canvas\s*</title>' -and
                $response.Content -match '/src/main\.tsx'
        }
        if ($Port -eq 8000) {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
            $openApi = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 2
            return $health.status -eq 'ok' -and
                $health.database_backend -in @('duckdb', 'postgresql') -and
                $openApi.info.title -eq 'Analysis Canvas API'
        }
    }
    catch {
        return $false
    }
    return $false
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
        foreach ($serverPid in @($serverPids.backend, $serverPids.frontend)) {
            if ($serverPid) { $stoppedCount += Stop-ProcessTree -TargetProcessId ([int]$serverPid) }
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
