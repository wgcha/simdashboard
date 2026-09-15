[CmdletBinding()]
param(
    [switch]$EnvironmentCreated,
    [switch]$NonInteractive,
    # Supports a managed runtime located outside the source tree; ordinary
    # deploy.ps1 leaves this empty and uses .venv-runtime.
    [string]$PythonPath = ''
)

# Provision only a missing or genuinely empty target database.  This wrapper
# never requests --replace-existing: populated databases proceed to the
# backup-gated migration stage in deploy.ps1.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Get-AdministratorUrl {
    if (-not [string]::IsNullOrWhiteSpace($env:POSTGRES_ADMIN_URL)) { return $env:POSTGRES_ADMIN_URL }
    if ($NonInteractive) {
        throw 'POSTGRES_ADMIN_URL is required for non-interactive initial PostgreSQL provisioning. Do not put the administrator password on the command line.'
    }
    $dbAdminHost = Read-Host 'PostgreSQL administrator host [127.0.0.1]'
    if ([string]::IsNullOrWhiteSpace($dbAdminHost)) { $dbAdminHost = '127.0.0.1' }
    if ($dbAdminHost -notmatch '^[A-Za-z0-9._:-]+$') { throw 'The PostgreSQL administrator host is invalid.' }
    $portText = Read-Host 'PostgreSQL administrator port [5432]'
    $port = 5432
    if ($portText -and (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535)) { throw 'The PostgreSQL administrator port must be between 1 and 65535.' }
    $user = Read-Host 'PostgreSQL administrator user [postgres]'
    if ([string]::IsNullOrWhiteSpace($user)) { $user = 'postgres' }
    $password = Read-Host 'PostgreSQL administrator password' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
    try {
        $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ([string]::IsNullOrWhiteSpace($plainPassword)) { throw 'A PostgreSQL administrator password is required for initial provisioning.' }
    try {
        $hostForUrl = if ($dbAdminHost.Contains(':') -and -not $dbAdminHost.StartsWith('[')) { "[$dbAdminHost]" } else { $dbAdminHost }
        return 'postgresql://{0}:{1}@{2}:{3}/postgres' -f [Uri]::EscapeDataString($user), [Uri]::EscapeDataString($plainPassword), $hostForUrl, $port
    }
    finally {
        $plainPassword = $null
    }
}

try {
    $envFile = Join-Path $Root '.env'
    $setup = Join-Path $Root 'backend\scripts\setup_local_postgres.py'
    $config = Join-Path $Root 'backend\scripts\source_deployment_database_config.py'
    $python = if ($PythonPath) { $PythonPath } else { Join-Path $Root '.venv-runtime\Scripts\python.exe' }
    if (-not (Test-Path -LiteralPath $setup -PathType Leaf) -or -not (Test-Path -LiteralPath $config -PathType Leaf) -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'The PostgreSQL initializer or prepared project Python runtime is missing.'
    }

    # Retain an explicitly selected disposable DuckDB process profile.  This
    # is useful for local validation and never comes from the generated .env.
    if ($env:ANALYSIS_DB_BACKEND -and $env:ANALYSIS_DB_BACKEND.Trim().ToLowerInvariant() -eq 'duckdb') {
        Write-Host 'Explicit process DuckDB selection was retained; PostgreSQL provisioning was skipped.' -ForegroundColor Yellow
        exit 0
    }

    $configOutput = & $python $config
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the effective PostgreSQL deployment configuration.' }
    $effective = $configOutput | ConvertFrom-Json
    $backend = [string]$effective.backend
    $hasDatabaseUrl = [bool]$effective.has_database_url

    # An explicit process DATABASE_URL may be supplied on the very first run
    # (for example by a managed installer).  Do not overwrite or reprovision
    # that selected target merely because deploy.ps1 just wrote its root .env.
    if ($hasDatabaseUrl) {
        Write-Host 'Existing PostgreSQL service configuration was preserved. Database replacement was not requested.' -ForegroundColor Yellow
        exit 0
    }

    if (-not $EnvironmentCreated) {
        if ($backend -and $backend.ToLowerInvariant() -eq 'duckdb') {
            Write-Host 'Existing explicit DuckDB selection was preserved; PostgreSQL provisioning was skipped.' -ForegroundColor Yellow
            exit 0
        }
        if ($backend -and $backend.ToLowerInvariant() -ne 'postgresql') {
            throw "The existing ANALYSIS_DB_BACKEND value '$backend' is invalid for PostgreSQL source deployment."
        }
        $legacyDuckdb = Join-Path $Root 'backend\data\analysis_dashboard.duckdb'
        if (Test-Path -LiteralPath $legacyDuckdb -PathType Leaf) {
            throw 'Existing DuckDB data was found while PostgreSQL has no DATABASE_URL. It was preserved; explicitly select DuckDB or complete a verified PostgreSQL migration before deployment.'
        }
        Write-Host 'PostgreSQL was explicitly selected without a service URL; safely provisioning a missing or empty target.' -ForegroundColor Cyan
    }

    # A generated .env starts with the development DuckDB example.  The
    # initializer rewrites it only after it has created or safely resumed an
    # empty PostgreSQL target and verified its application credentials.
    $env:POSTGRES_ADMIN_URL = Get-AdministratorUrl
    $env:PYTHONIOENCODING = 'utf-8'
    Write-Host 'Provisioning only a missing or empty PostgreSQL target database...' -ForegroundColor Cyan
    Push-Location (Join-Path $Root 'backend')
    try {
        & $python $setup '--seed-mode' 'empty'
        $exitCode = [int]$LASTEXITCODE
    }
    finally {
        Pop-Location
        Remove-Item -LiteralPath Env:POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue
    }
    if ($exitCode -ne 0) { throw "Initial PostgreSQL provisioning failed (exit code $exitCode). Existing data was not replaced." }
    Write-Host 'PostgreSQL application database was provisioned safely.' -ForegroundColor Green
}
catch {
    Remove-Item -LiteralPath Env:POSTGRES_ADMIN_URL -ErrorAction SilentlyContinue
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
