param(
    [string]$Bundle = '',
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python virtual environment was not found. Run setup.ps1 first.' }
if (-not $Bundle) { $Bundle = Read-Host 'Transfer bundle directory' }
if (-not $Bundle) { throw 'Transfer bundle directory is required.' }
$env:PYTHONIOENCODING = 'utf-8'
$arguments = @((Join-Path $Root 'backend\scripts\postgres_transfer.py'), 'import', $Bundle)
if ($ValidateOnly) { $arguments += '--validate-only' }
& $Python @arguments
exit $LASTEXITCODE
