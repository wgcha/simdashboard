param(
    [string]$MigrateSource = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $ProjectRoot '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python virtual environment not found. Run setup-windows.bat first.' }

foreach ($name in @('POSTGRES_ADMIN_URL', 'SIM_DASH_OWNER_PASSWORD', 'SIM_DASH_APP_PASSWORD', 'DATABASE_URL')) {
    if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) { throw "$name environment variable is required." }
}

& $Python (Join-Path $ProjectRoot 'backend\scripts\bootstrap_postgres.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:ANALYSIS_DB_BACKEND = 'postgresql'
Push-Location (Join-Path $ProjectRoot 'backend')
try {
    & $Python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python scripts\harden_postgres_privileges.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($MigrateSource) {
        $sourcePath = (Resolve-Path -LiteralPath $MigrateSource).Path
        & $Python scripts\migrate_duckdb_to_postgres.py --source $sourcePath --target-url $env:DATABASE_URL --execute
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        & $Python scripts\seed_database.py --mode reference
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} finally {
    Pop-Location
}

Write-Host 'PostgreSQL schema and initial data setup completed.' -ForegroundColor Cyan
Write-Host 'For normal service startup, keep ANALYSIS_DB_BACKEND=postgresql and use the simdashboard_app DATABASE_URL.'
