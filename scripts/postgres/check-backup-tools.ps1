param([string]$ProjectRoot = '')
$ErrorActionPreference = 'Stop'
if ($ProjectRoot) {
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
} else {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}
$Python = Join-Path $ProjectRoot '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { $Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Python virtual environment not found.' }
$Script = Join-Path $ProjectRoot 'backend\scripts\check_postgres_backup_tools.py'
# Leave native stderr on the console: merging it into the PowerShell pipeline
# with ErrorActionPreference=Stop can hide the following safe DETAIL line.
& $Python $Script --project-root $ProjectRoot | Out-Host
$ExitCode = $LASTEXITCODE
exit $ExitCode
