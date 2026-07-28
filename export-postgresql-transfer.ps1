param([string]$OutputDir = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = Join-Path $Root '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python virtual environment was not found. Run setup-windows.bat first.' }
$env:PYTHONIOENCODING = 'utf-8'
$arguments = @((Join-Path $Root 'backend\scripts\postgres_transfer.py'), 'export')
if ($OutputDir) { $arguments += @('--output-dir', $OutputDir) }
& $Python @arguments
exit $LASTEXITCODE

