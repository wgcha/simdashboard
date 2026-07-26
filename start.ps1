$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Start-Process fails when the inherited Windows environment contains both
# "Path" and "PATH" entries. Keep the effective value under one canonical key.
$processPath = $env:Path
[Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
[Environment]::SetEnvironmentVariable('Path', $null, 'Process')
[Environment]::SetEnvironmentVariable('Path', $processPath, 'Process')

$Python = Join-Path $Root '.venv-runtime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
}
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'

if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python virtual environment was not found. Run setup-windows.bat first.'
}

$backendProcess = Start-Process -FilePath $Python `
    -ArgumentList '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000' `
    -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $Backend 'uvicorn.log') `
    -RedirectStandardError (Join-Path $Backend 'uvicorn-error.log')

$pnpm = (Get-Command pnpm.cmd -ErrorAction SilentlyContinue).Source
if (-not $pnpm) { $pnpm = (Get-Command pnpm -ErrorAction SilentlyContinue).Source }
if (-not $pnpm) {
    $bundledPnpm = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd'
    if (Test-Path -LiteralPath $bundledPnpm) { $pnpm = $bundledPnpm }
}
if (-not $pnpm) { throw 'pnpm was not found. Run setup-windows.bat first.' }

$frontendProcess = Start-Process -FilePath $pnpm -ArgumentList 'run', 'dev' `
    -WorkingDirectory $Frontend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $Frontend 'vite.log') `
    -RedirectStandardError (Join-Path $Frontend 'vite-error.log')

@{
    backend = $backendProcess.Id
    frontend = $frontendProcess.Id
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root '.server-pids.json') -Encoding UTF8

Write-Host 'Analysis Canvas started successfully.' -ForegroundColor Cyan
Write-Host 'Dashboard: http://127.0.0.1:5173'
Write-Host 'API docs : http://127.0.0.1:8000/docs'
