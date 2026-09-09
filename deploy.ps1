[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    # Automation may use this to verify an already prepared deployment.  The
    # normal interactive deployment deliberately prompts for the first server
    # administrator when one is required.
    [switch]$NonInteractive,
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

function Set-NewEnvironmentOwnerOnlyAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $owner = $acl.Owner
    if ([string]::IsNullOrWhiteSpace($owner)) { throw 'Could not determine the owner for the new .env file.' }
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleAll($rule) }
    $ownerRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $owner,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $acl.SetAccessRule($ownerRule)
    Set-Acl -LiteralPath $Path -AclObject $acl -ErrorAction Stop
}

function Invoke-DeploymentPython {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string[]]$Arguments = @()
    )

    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
        throw "Required deployment script was not found: $ScriptPath"
    }
    $exitCode = 1
    Push-Location (Join-Path $Root 'backend')
    try {
        # Keep child progress on the console.  Returning it through this
        # function would turn a successful text line into a nonzero stage
        # value when PowerShell compares the resulting array to zero.
        & $Python $ScriptPath @Arguments | Out-Host
        $exitCode = [int]$LASTEXITCODE
    }
    finally { Pop-Location }
    return $exitCode
}

function Test-EnvlessLegacyDuckdb {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    # A caller can deliberately retain the legacy backend through the inherited
    # process environment. Do not replace that explicit selection.
    if ($env:ANALYSIS_DB_BACKEND -and $env:ANALYSIS_DB_BACKEND.Trim().ToLowerInvariant() -eq 'duckdb') { return }
    throw "Existing DuckDB data was found at $Path while no .env or backend/.env selects it. Migration is required before creating PostgreSQL defaults. Run setup-postgresql.ps1 to migrate it, or explicitly set ANALYSIS_DB_BACKEND=duckdb to preserve the legacy installation. .env/backend/.env이 없는 기존 DuckDB 데이터가 발견되었습니다. PostgreSQL 기본값을 만들기 전에 마이그레이션이 필요합니다. setup-postgresql.ps1로 이전하거나 ANALYSIS_DB_BACKEND=duckdb를 명시하여 기존 설치를 유지하십시오."
}

try {
    Set-Location -LiteralPath $Root
    # A previous ready stamp cannot describe an in-progress deployment.  It is
    # recreated only after account protection and schema work complete.
    Remove-Item -LiteralPath $ReadyStamp -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $RecoveryMarker -PathType Leaf) {
        throw 'A previous database replacement needs manual recovery. Review .setup-recovery-required.json before deploying again.'
    }
    if (-not (Test-Path -LiteralPath $Setup -PathType Leaf)) { throw 'setup.ps1 was not found. Extract the complete source archive and retry.' }
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        $legacyBackendEnv = Join-Path $Root 'backend\.env'
        if (Test-Path -LiteralPath $legacyBackendEnv -PathType Leaf) {
            $legacyItem = Get-Item -Force -LiteralPath $legacyBackendEnv
            if ($legacyItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Legacy backend/.env is linked. It was preserved; replace it with a regular file before deploying.'
            }
            # app.config deliberately loads the root file and then backend/.env
            # without overriding.  Do not create a default root file here: it
            # could mask a legacy database or authentication configuration.
            Write-Host 'Preserving the existing backend/.env as the effective deployment configuration.' -ForegroundColor Yellow
        }
        else {
            Test-EnvlessLegacyDuckdb -Path (Join-Path $Root 'backend\data\analysis_dashboard.duckdb')
            if (-not (Test-Path -LiteralPath $EnvExample -PathType Leaf)) { throw '.env.example was not found; the source archive is incomplete.' }
            Copy-Item -LiteralPath $EnvExample -Destination $EnvFile -ErrorAction Stop
            Set-NewEnvironmentOwnerOnlyAcl -Path $EnvFile
            $envCreated = $true
            Write-Host 'Created .env from .env.example because no local environment file existed.' -ForegroundColor Cyan
        }
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

    $python = Get-ProjectPython
    $databasePreflightScript = Join-Path $Root 'backend\scripts\check_database_startup_preflight.py'
    Write-Host 'Checking the selected database configuration before stopping services...' -ForegroundColor Cyan
    $databasePreflightCode = Invoke-DeploymentPython -Python $python -ScriptPath $databasePreflightScript
    if ($databasePreflightCode -ne 0) { throw 'PostgreSQL configuration preflight failed. DATABASE_URL and the provisioned PostgreSQL server connection are required; DuckDB fallback was not used.' }

    # setup.ps1 only prepares pinned runtimes.  Stop after that point, before
    # the account backup gate and any database migration can change state.
    $stopScript = Join-Path $Root 'stop.ps1'
    if (-not (Test-Path -LiteralPath $stopScript -PathType Leaf)) { throw 'stop.ps1 was not found. Extract the complete source archive and retry.' }
    Write-Host "`nStopping any currently managed application before database preparation..." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript
    if ($LASTEXITCODE -ne 0) { throw "Stopping the existing application failed (exit code $LASTEXITCODE). No database action was performed." }

    $prepareScript = Join-Path $Root 'backend\scripts\prepare_account_deployment.py'
    Write-Host 'Checking account state and creating a verified database backup when required...' -ForegroundColor Cyan
    $prepareCode = Invoke-DeploymentPython -Python $python -ScriptPath $prepareScript -Arguments @('--project-root', $Root)
    if ($prepareCode -ne 0) { throw "Account backup preparation failed (exit code $prepareCode). Database migration was not performed." }

    $migrationScript = Join-Path $Root 'backend\scripts\upgrade_postgres_schema.py'
    Write-Host 'Applying pending PostgreSQL schema migrations...' -ForegroundColor Cyan
    $migrationArguments = @('--allow-empty-bootstrap')
    $migrationCode = Invoke-DeploymentPython -Python $python -ScriptPath $migrationScript -Arguments $migrationArguments
    if ($migrationCode -ne 0) { throw "Database migration failed (exit code $migrationCode). Account setup and readiness publication were skipped." }

    $accountSetupScript = Join-Path $Root 'backend\scripts\setup_accounts.py'
    $accountSetupArguments = @()
    if ($NonInteractive) { $accountSetupArguments += '--non-interactive' }
    Write-Host 'Preparing the initial administrator when required...' -ForegroundColor Cyan
    $accountSetupCode = Invoke-DeploymentPython -Python $python -ScriptPath $accountSetupScript -Arguments $accountSetupArguments
    if ($accountSetupCode -ne 0) { throw "Account setup is pending or failed (exit code $accountSetupCode). Readiness was not published." }

    $stamp = [ordered]@{
        version = 1
        ready_at_utc = [DateTime]::UtcNow.ToString('o')
        runtime = [ordered]@{ node = $versions.Node; python = $versions.Python; pnpm = $versions.Pnpm }
        env_created = $envCreated
        network_mode = $networkSummary.Mode
        account_setup = 'ready'
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
    Write-Host 'Deployment stopped before readiness was published. Review the reported account-backup, migration, or account-setup stage before retrying.' -ForegroundColor Yellow
    exit 1
}
