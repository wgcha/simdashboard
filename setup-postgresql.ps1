$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python virtual environment was not found. Run setup-windows.bat first.'
}

$env:PYTHONIOENCODING = 'utf-8'
& $Python (Join-Path $Root 'backend\scripts\setup_local_postgres.py')
exit $LASTEXITCODE
