[CmdletBinding()]
param(
    [string]$ProjectRoot = '',
    [string]$RepositoryUrl = '',
    [string]$Branch = '',
    [switch]$NoBrowser,
    [string]$LocalHelperDistributionSource = '',
    [Uri]$LocalHelperManifestUrl,
    [ValidateSet('auto', 'direct', 'proxy')]
    [string]$NetworkMode = '',
    # Intended for automated validation. A normal double-click remains interactive
    # for the first ZIP/bootstrap run.
    [switch]$Yes,
    # Keep unattended jobs fail-closed when a first administrator must be
    # created. Interactive update.bat continues to prompt through deploy.ps1.
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$DriverRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path)).TrimEnd('\')
$Root = if ($ProjectRoot) {
    [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
}
else {
    $DriverRoot
}
$LockPath = Join-Path $Root '.update.lock'
$lockStream = $null
$lockAcquired = $false
$logPath = $null
$stage = 'startup'
$failureExitCode = 1

if ($LocalHelperDistributionSource -and $null -ne $LocalHelperManifestUrl) {
    throw 'Use either -LocalHelperDistributionSource or -LocalHelperManifestUrl, not both.'
}

function Write-UpdateLog {
    param([string]$Message)
    if (-not $script:logPath) { return }
    try {
        $line = '{0} {1}' -f [DateTime]::UtcNow.ToString('o'), $Message
        Add-Content -LiteralPath $script:logPath -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    }
    catch { }
}

function Write-Stage {
    param([string]$Name, [string]$Message)
    $script:stage = $Name
    Write-Host "`n==> $Message" -ForegroundColor Cyan
    Write-UpdateLog "STAGE=$Name START"
}

function Complete-Stage {
    param([string]$Name, [int]$ExitCode = 0)
    Write-UpdateLog "STAGE=$Name EXIT=$ExitCode"
}

function Invoke-ChildPowerShell {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string[]]$Arguments = @()
    )

    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
        throw "Required script was not found: $ScriptPath"
    }
    # Passing an argument array to the native call operator lets Windows
    # PowerShell perform the quoting, including paths containing spaces or Hangul.
    $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $windowsPowerShell -PathType Leaf)) {
        $command = Get-Command powershell.exe -ErrorAction SilentlyContinue
        if (-not $command) { throw 'Windows PowerShell (powershell.exe) was not found.' }
        $windowsPowerShell = $command.Source
    }
    $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath) + @($Arguments)
    # Child output is intentionally sent straight to the console. Keeping it
    # out of the pipeline is important: callers must receive only the integer
    # process exit code, even when deploy/start print normal progress.
    & $windowsPowerShell @childArgs | Out-Host
    $code = [int]$LASTEXITCODE
    return $code
}

function Get-ServerPortArguments {
    $pidPath = Join-Path $Root '.server-pids.json'
    if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
        return @()
    }

    try {
        $records = Get-Content -Raw -LiteralPath $pidPath | ConvertFrom-Json
    }
    catch {
        throw 'The existing .server-pids.json is invalid; it was preserved. Review it before updating so custom ports are not lost.'
    }
    if ($null -eq $records) { throw 'The existing .server-pids.json is empty; it was preserved. Review it before updating.' }

    $arguments = @()
    foreach ($entry in @(
        [pscustomobject]@{ Name = 'backend'; Parameter = '-BackendPort'; Default = 8000 },
        [pscustomobject]@{ Name = 'frontend'; Parameter = '-FrontendPort'; Default = 5173 }
    )) {
        $record = $records.($entry.Name)
        if ($null -eq $record) { continue }
        if (-not ($record.PSObject.Properties.Name -contains 'port')) {
            continue
        }
        $port = 0
        if (-not [int]::TryParse([string]$record.port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
            throw "The existing .server-pids.json has an invalid $($entry.Name) port; it was preserved. Review it before updating."
        }
        $arguments += @($entry.Parameter, [string]$port)
    }
    return $arguments
}

function Confirm-Bootstrap {
    param($Plan)
    if ([string]$Plan.Mode -ne 'Bootstrap' -or $Yes) { return }
    $backup = if ($Plan.BackupDirectory) { [string]$Plan.BackupDirectory } else { '(module will choose a backup directory)' }
    Write-Host ''
    Write-Host 'This folder has no usable Git checkout. Initial bootstrap is ready for review.' -ForegroundColor Yellow
    Write-Host "Target repository : $($Plan.Remote)"
    Write-Host "Target branch     : $($Plan.Branch)"
    Write-Host "Project folder    : $Root"
    Write-Host "Source backup     : $backup"
    Write-Host 'Personal .env, database, runtime, results, and logs are preserved.' -ForegroundColor Yellow
    [void](Read-Host 'Press Enter to continue')
}

try {
    # Validate the destination before creating logs or locks. The Git module
    # repeats its checks before checkout, including source-path junctions.
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        throw "Deployment folder does not exist: $Root"
    }
    if ($Root.TrimEnd('\') -eq [IO.Path]::GetPathRoot($Root).TrimEnd('\')) {
        throw 'Use the existing deployment folder, not a drive root.'
    }
    $ancestor = $Root
    while ($ancestor) {
        if ((Get-Item -Force -LiteralPath $ancestor).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'The deployment is inside a linked folder. Use a physical deployment folder.'
        }
        $ancestor = Split-Path -Parent $ancestor
    }
    foreach ($statePath in @($LockPath, (Join-Path $Root 'log'))) {
        if ((Test-Path -LiteralPath $statePath) -and ((Get-Item -Force -LiteralPath $statePath).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "The updater state path is a link: $statePath"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'stop.ps1') -PathType Leaf)) {
        throw 'Run update.bat in the existing deployment folder beside stop.ps1.'
    }
    try {
        $lockStream = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $lockAcquired = $true
    }
    catch [System.IO.IOException] {
        Write-Host '[ERROR] Another update is already running in this folder. Wait for it to finish and retry.' -ForegroundColor Red
        exit 2
    }

    $logDirectory = Join-Path $Root 'log'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $logPath = Join-Path $logDirectory ('update-{0}-{1}.log' -f (Get-Date).ToString('yyyyMMdd-HHmmss'), $PID)
    Write-UpdateLog 'UPDATE START'

    Set-Location -LiteralPath $Root
    # During ZIP bootstrap the driver can run from a temporary staging folder;
    # its Git module stays immutable while Root is being populated.
    $gitModulePath = Join-Path $DriverRoot 'scripts\windows\GitUpdate.psm1'
    if (-not (Test-Path -LiteralPath $gitModulePath -PathType Leaf)) {
        throw 'The update driver is incomplete: scripts\windows\GitUpdate.psm1 was not found next to update.ps1. Copy update.bat with its companion update.ps1 and scripts\windows files, then retry.'
    }
    Import-Module $gitModulePath -Force

    Write-Stage 'git-plan' 'Checking Git, the remote, and the downloadable target'
    $planArguments = @{ Root = $Root }
    if ($RepositoryUrl) { $planArguments.RepositoryUrl = $RepositoryUrl }
    if ($Branch) { $planArguments.Branch = $Branch }
    $plan = Get-WorkbenchGitUpdatePlan @planArguments
    if ($null -eq $plan) { throw 'Git update planning returned no plan.' }
    Write-Host "Remote: $($plan.Remote)  Branch: $($plan.Branch)  Target: $($plan.TargetCommit)"
    Write-Host "Mode: $($plan.Mode); Changed: $($plan.Changed)"
    Write-UpdateLog ("PLAN Mode={0} Branch={1} Changed={2}" -f $plan.Mode, $plan.Branch, $plan.Changed)
    Complete-Stage 'git-plan'

    Confirm-Bootstrap -Plan $plan

    # Read this before stop.ps1 can remove the metadata. Only valid, explicitly
    # recorded ports are forwarded; defaults remain owned by stop/start.ps1.
    $portArguments = Get-ServerPortArguments
    Write-UpdateLog ("PORT_ARGUMENTS_COUNT={0}" -f $portArguments.Count)

    Write-Stage 'stop' 'Stopping the current application'
    $stopCode = Invoke-ChildPowerShell -ScriptPath (Join-Path $Root 'stop.ps1') -Arguments $portArguments
    Complete-Stage 'stop' $stopCode
    if ($stopCode -ne 0) { $failureExitCode = $stopCode; throw "Stopping the application failed (exit code $stopCode). The source was not changed." }

    Write-Stage 'git-apply' 'Applying the verified Git update'
    Invoke-WorkbenchGitUpdate -Plan $plan | Out-Null
    Complete-Stage 'git-apply'

    Write-Stage 'deploy' 'Refreshing the deployment environment'
    $deployArguments = @()
    if ($NetworkMode) { $deployArguments += @('-NetworkMode', $NetworkMode) }
    if ($NonInteractive) { $deployArguments += '-NonInteractive' }
    $deployCode = Invoke-ChildPowerShell -ScriptPath (Join-Path $Root 'deploy.ps1') -Arguments $deployArguments
    Complete-Stage 'deploy' $deployCode
    if ($deployCode -ne 0) { $failureExitCode = $deployCode; throw "Deployment failed (exit code $deployCode). The local-helper import and restart were skipped; review the reported deployment stage." }

    if ($LocalHelperDistributionSource -or $null -ne $LocalHelperManifestUrl) {
        Write-Stage 'local-helper-distribution' 'Importing the requested Windows local helper distribution'
        $helperScript = Join-Path $Root 'scripts\windows\import-local-helper-distribution.ps1'
        $helperArguments = @('-ProjectRoot', $Root)
        if ($LocalHelperDistributionSource) { $helperArguments += @('-LocalHelperDistributionSource', $LocalHelperDistributionSource) }
        else { $helperArguments += @('-LocalHelperManifestUrl', $LocalHelperManifestUrl.AbsoluteUri) }
        $helperCode = Invoke-ChildPowerShell -ScriptPath $helperScript -Arguments $helperArguments
        Complete-Stage 'local-helper-distribution' $helperCode
        if ($helperCode -ne 0) { $failureExitCode = $helperCode; throw 'Local helper distribution import failed. The existing distribution was preserved and the server was not started.' }
    }
    else {
        Write-Host 'Local helper distribution was not supplied; existing server distribution was not changed.' -ForegroundColor Yellow
    }

    Write-Stage 'start' 'Starting the updated application'
    $startArguments = @()
    $startArguments += $portArguments
    if ($NoBrowser) { $startArguments += '-NoBrowser' }
    if ($NetworkMode) { $startArguments += @('-NetworkMode', $NetworkMode) }
    $startCode = Invoke-ChildPowerShell -ScriptPath (Join-Path $Root 'start.ps1') -Arguments $startArguments
    Complete-Stage 'start' $startCode
    if ($startCode -ne 0) { $failureExitCode = $startCode; throw "Starting the updated application failed (exit code $startCode). Review the service logs and retry." }

    Write-UpdateLog 'UPDATE SUCCESS'
    Write-Host "`nUpdate completed successfully. Log: $logPath" -ForegroundColor Green
    exit 0
}
catch {
    Write-UpdateLog ("UPDATE FAILED STAGE={0} EXIT={1}" -f $stage, $failureExitCode)
    Write-Host "`n[ERROR] Update failed during '$stage' (exit code $failureExitCode)." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Review the stage log: $logPath" -ForegroundColor Yellow
    Write-Host 'No automatic database rollback was attempted. Fix the reported stage, then run update.bat again.' -ForegroundColor Yellow
    exit $failureExitCode
}
finally {
    if ($lockAcquired) {
        if ($lockStream) { $lockStream.Dispose() }
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
}
