param([string]$OutputDir = '')
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $ProjectRoot '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python virtual environment not found.' }
if (-not $env:DATABASE_URL) { throw 'DATABASE_URL environment variable is required.' }
$arguments = @((Join-Path $ProjectRoot 'backend\scripts\backup_postgres.py'))
if ($OutputDir) { $arguments += @('--output-dir', $OutputDir) }
& $Python @arguments
exit $LASTEXITCODE
