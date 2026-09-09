[CmdletBinding()]
param(
    [ValidateRange(1, 65535)] [int]$Port = 8766,
    [Parameter(Mandatory = $true)] [ValidatePattern('^https?://[^\s"'']+$')] [string]$ServerUrl,
    [string[]]$Origin,
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA "SimulationWorkbench\local-runner"),
    [switch]$InstallAutoStart
)

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $workspace ".venv-runtime\Scripts\python.exe"
$frozenRunner = Join-Path $workspace "SimulationWorkbenchLocalHelper.exe"
if (-not (Test-Path -LiteralPath $frozenRunner -PathType Leaf) -and -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Local helper runtime was not found. Expected SimulationWorkbenchLocalHelper.exe or $python"
}
$serverUri = [Uri]$ServerUrl
if ($serverUri.Scheme -ne 'https' -and $serverUri.Host -notin @('localhost', '127.0.0.1', '::1')) { throw "ServerUrl must use HTTPS (HTTP is accepted only for loopback development)." }
if (-not $Origin -or $Origin.Count -eq 0) { $Origin = @($serverUri.GetLeftPart([UriPartial]::Authority)) }
$DataDir = [IO.Path]::GetFullPath($DataDir)
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$managedConfig = Join-Path $DataDir 'managed-config.json'
function Get-ManagedDeviceId {
    if (-not (Test-Path -LiteralPath $managedConfig)) { return $null }
    try {
        $config = Get-Content -LiteralPath $managedConfig -Raw | ConvertFrom-Json
        if (-not $config.device_id -or -not $config.server_url) { throw "Managed runner configuration is invalid. Use a new -DataDir." }
        if ($config.server_url.TrimEnd('/') -ne $ServerUrl.TrimEnd('/')) { throw "This data directory is paired with a different ServerUrl. Use a separate -DataDir or stop the existing helper." }
        return [string]$config.device_id
    } catch [System.Management.Automation.RuntimeException] { throw }
}
$expectedDeviceId = Get-ManagedDeviceId
if (Test-Path -LiteralPath $managedConfig) {
    # Get-ManagedDeviceId validates the fixed central server before any process
    # is contacted or a new runner could overwrite local logs.
}

function Get-LocalRunnerIdentity {
    param([int]$RunnerPort)
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$RunnerPort/v1/identity" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        $identity = ConvertFrom-Json $response.Content
        if ($response.StatusCode -eq 200 -and $identity.managed -eq $true -and $identity.device_id) { return $identity }
    } catch { }
    return $null
}
function ConvertTo-WindowsCommandLineArgument {
    param([string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Install-LocalRunnerAutoStart {
    if (-not $InstallAutoStart) { return }
    $quote = { param([string]$Value) "'" + $Value.Replace("'", "''") + "'" }
    $originExpression = '@(' + (($Origin | ForEach-Object { & $quote $_ }) -join ',') + ')'
    $command = "& $(& $quote (Join-Path $workspace 'start-local-runner.ps1')) -Port $Port -ServerUrl $(& $quote $ServerUrl) -DataDir $(& $quote $DataDir) -Origin $originExpression"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $startupFile = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)) 'SimulationWorkbenchLocalRunner.lnk'
    $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $windowsPowerShell)) {
        $windowsPowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    }
    if (-not (Test-Path -LiteralPath $windowsPowerShell)) { throw "Windows PowerShell executable was not found for the startup launcher." }
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($startupFile)
    $shortcut.TargetPath = $windowsPowerShell
    $shortcut.Arguments = "-NoProfile -WindowStyle Hidden -EncodedCommand $encoded"
    $shortcut.WorkingDirectory = $workspace; $shortcut.Save()
    Write-Host "Installed current-user startup launcher: $startupFile"
}
$runningIdentity = Get-LocalRunnerIdentity -RunnerPort $Port
if ($runningIdentity) {
    if (-not $expectedDeviceId) { throw "A managed local runner already uses port $Port, but this data directory has no managed identity. Choose another port or stop the existing helper." }
    if ($runningIdentity.device_id -ne $expectedDeviceId) { throw "A different managed local runner already uses port $Port. Stop it or choose another port." }
    Install-LocalRunnerAutoStart
    Write-Host "Local runner is already running at http://127.0.0.1:$Port"; return
}

$runnerArgs = if (Test-Path -LiteralPath $frozenRunner -PathType Leaf) {
    @('--data-dir', $DataDir, '--port', "$Port", '--server-url', $ServerUrl)
} else {
    @('-m', 'local_runner', '--data-dir', $DataDir, '--port', "$Port", '--server-url', $ServerUrl)
}
foreach ($item in $Origin) { $runnerArgs += @('--origin', $item) }
$stdoutLog = Join-Path $DataDir 'local-runner.stdout.log'; $stderrLog = Join-Path $DataDir 'local-runner.stderr.log'
$quotedRunnerArgs = ($runnerArgs | ForEach-Object { ConvertTo-WindowsCommandLineArgument $_ }) -join ' '
$runnerBinary = if (Test-Path -LiteralPath $frozenRunner -PathType Leaf) { $frozenRunner } else { $python }
$process = Start-Process -FilePath $runnerBinary -ArgumentList $quotedRunnerArgs -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
try {
    # A frozen runtime may need time for first-run extraction and antivirus scanning.
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $startedIdentity = Get-LocalRunnerIdentity -RunnerPort $Port
        if ($startedIdentity) {
            $expectedDeviceId = Get-ManagedDeviceId
            if ($expectedDeviceId -and $startedIdentity.device_id -eq $expectedDeviceId) { Install-LocalRunnerAutoStart; Write-Host "Local runner started at http://127.0.0.1:$Port"; return }
            break
        }
        if ($process.HasExited) { break }; Start-Sleep -Milliseconds 250
    }
    throw "Local runner did not become available within 30 seconds. Check $stdoutLog and $stderrLog."
} catch {
    # Only this invocation's new process tree is owned here. In particular, do
    # not kill a process discovered through the shared loopback port.
    if (-not $process.HasExited) {
        & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $process.Id /T /F 2>$null | Out-Null
        $process.WaitForExit(5000) | Out-Null
    }
    throw
}
