param(
    [Parameter(Mandatory=$true)][string]$Backup,
    [Parameter(Mandatory=$true)][string]$ConfirmDatabase,
    [switch]$Clean
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $ProjectRoot '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python virtual environment not found.' }
if (-not $env:DATABASE_URL) { throw 'DATABASE_URL environment variable is required.' }
$backupPath = (Resolve-Path -LiteralPath $Backup).Path
$arguments = @((Join-Path $ProjectRoot 'backend\scripts\restore_postgres.py'), $backupPath, '--confirm-database', $ConfirmDatabase)
if ($Clean) { $arguments += '--clean' }
& $Python @arguments
exit $LASTEXITCODE
