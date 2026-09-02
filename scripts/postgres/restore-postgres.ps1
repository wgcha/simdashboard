param(
    [Parameter(Mandatory=$true)][string]$Backup,
    [Parameter(Mandatory=$true)][string]$ConfirmDatabase,
    [Parameter(Mandatory=$false)][string]$AppRoleVerifyUrl,
    [switch]$Clean
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Python = Join-Path $ProjectRoot '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python virtual environment not found.' }
if (-not $env:DATABASE_URL) { throw 'DATABASE_URL environment variable is required.' }
if (-not $AppRoleVerifyUrl) { $AppRoleVerifyUrl = $env:SIMDASH_APP_DATABASE_URL }
if (-not $AppRoleVerifyUrl) { throw 'AppRoleVerifyUrl or SIMDASH_APP_DATABASE_URL is required.' }
$backupPath = (Resolve-Path -LiteralPath $Backup).Path
$arguments = @((Join-Path $ProjectRoot 'backend\scripts\restore_postgres.py'), $backupPath, '--confirm-database', $ConfirmDatabase, '--verify-database-url', $AppRoleVerifyUrl)
if ($Clean) { $arguments += '--clean' }
& $Python @arguments
exit $LASTEXITCODE
