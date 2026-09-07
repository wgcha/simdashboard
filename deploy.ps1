[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [ValidateSet('auto', 'direct', 'proxy')]
    [string]$NetworkMode = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RecoveryMarker = Join-Path $Root '.setup-recovery-required.json'
$EnvFile = Join-Path $Root '.env'
$EnvExample = Join-Path $Root '.env.example'
$ReadyStamp = Join-Path $Root '.windows-deploy-ready.json'
$Setup = Join-Path $Root 'setup.ps1'
$envCreated = $false

try {
    Set-Location -LiteralPath $Root
    if (Test-Path -LiteralPath $RecoveryMarker -PathType Leaf) {
        throw 'A previous database replacement needs manual recovery. Review .setup-recovery-required.json before deploying again.'
    }
    if (-not (Test-Path -LiteralPath $Setup -PathType Leaf)) { throw 'setup.ps1 was not found. Extract the complete source archive and retry.' }
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        if (-not (Test-Path -LiteralPath $EnvExample -PathType Leaf)) { throw '.env.example was not found; the source archive is incomplete.' }
        Copy-Item -LiteralPath $EnvExample -Destination $EnvFile -ErrorAction Stop
        $envCreated = $true
        Write-Host 'Created .env from .env.example because no local environment file existed.' -ForegroundColor Cyan
    }
    else { Write-Host 'Preserving the existing .env and database configuration.' -ForegroundColor Yellow }

    $networkModule = Join-Path $Root 'scripts\windows\Network.psm1'
    if (-not (Test-Path -LiteralPath $networkModule -PathType Leaf)) { throw 'Network.psm1 was not found. Extract the complete source archive and retry.' }
    Import-Module $networkModule -Force
    $networkSummary = if ($NetworkMode) { Initialize-DeploymentNetwork -Mode $NetworkMode } else { Initialize-DeploymentNetwork }

    $setupArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Setup)
    if ($SkipFrontendBuild) { $setupArgs += '-SkipFrontendBuild' }
    if ($NetworkMode) { $setupArgs += @('-NetworkMode', $NetworkMode) }
    & powershell.exe @setupArgs
    if ($LASTEXITCODE -ne 0) { throw "Environment setup failed (exit code $LASTEXITCODE)." }

    Import-Module (Join-Path $Root 'scripts\windows\Runtime.psm1') -Force
    $versions = Get-ExpectedRuntimeVersions
    $stamp = [ordered]@{
        version = 1
        ready_at_utc = [DateTime]::UtcNow.ToString('o')
        runtime = [ordered]@{ node = $versions.Node; python = $versions.Python; pnpm = $versions.Pnpm }
        env_created = $envCreated
        network_mode = $networkSummary.Mode
    }
    $temporary = "$ReadyStamp.$PID.tmp"
    try {
        $stamp | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $ReadyStamp -Force
    }
    finally { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }

    Write-Host "`nWindows deployment environment is ready." -ForegroundColor Green
    Write-Host 'Start the application with start.bat or .\start.ps1.'
    Write-Host 'The default browser opens http://127.0.0.1:5173/workspace/overview after both services report healthy.'
}
catch {
    Write-Host "`n[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No database migration, seed, replacement, or reset was performed by deploy.ps1.' -ForegroundColor Yellow
    exit 1
}
