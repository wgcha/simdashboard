$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root 'backend'
$StartScript = Join-Path $Root 'start.ps1'
$PidFile = Join-Path $Root '.server-pids.json'

$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python virtual environment was not found. Run setup-windows.bat first.'
}
if (-not (Test-Path -LiteralPath $StartScript)) {
    throw 'start.ps1 was not found.'
}

if (Test-Path -LiteralPath $PidFile) {
    $serverPids = Get-Content -Raw -LiteralPath $PidFile | ConvertFrom-Json
    $runningServers = @($serverPids.backend, $serverPids.frontend) | Where-Object {
        $_ -and (Get-Process -Id $_ -ErrorAction SilentlyContinue)
    }
    if ($runningServers.Count -gt 0) {
        throw 'Analysis Canvas is already running. Run stop.bat before switching to PostgreSQL.'
    }
}

# Process environment variables take precedence over values loaded from .env.
$env:ANALYSIS_DB_BACKEND = 'postgresql'
$env:PYTHONIOENCODING = 'utf-8'

Push-Location $Backend
try {
    & $Python -c "from app.config import database_settings; import sys; sys.exit(0 if database_settings().database_url else 2)"
    if ($LASTEXITCODE -eq 2) {
        Write-Host ''
        Write-Host '[ERROR] DATABASE_URL is not configured.' -ForegroundColor Red
        Write-Host "Create $(Join-Path $Root '.env') and add:" -ForegroundColor Yellow
        Write-Host ''
        Write-Host '  ANALYSIS_DB_BACKEND=postgresql'
        Write-Host '  DATABASE_URL=postgresql+psycopg://simdashboard_app:APP_PASSWORD@127.0.0.1:5432/simulation_dashboard'
        Write-Host ''
        Write-Host 'Replace APP_PASSWORD with the application-role password.' -ForegroundColor Yellow
        Write-Host 'If the database and roles have not been created yet, follow:'
        Write-Host '  docs\deployment-security-backup-guide.md'
        exit 2
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not read the PostgreSQL configuration.'
    }

    Write-Host 'Checking the PostgreSQL connection and schema...' -ForegroundColor Cyan
    & $Python 'scripts\check_postgres_connection.py'
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

Write-Host 'PostgreSQL preflight check passed.' -ForegroundColor Green
& $StartScript
