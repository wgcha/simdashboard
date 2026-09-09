@echo off
setlocal
set "SIMDASH_UPDATE_BOOTSTRAP_ROOT=%~dp0"
set "SIMDASH_UPDATE_LAUNCHER=%~f0"
if exist "%~dp0update.ps1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*
  if errorlevel 1 (
    echo Update failed. Read the error above.
    pause
    exit /b 1
  ) else (
    exit /b 0
  )
)
if not "%~1"=="" (
  echo First-time setup accepts no command-line options. Run update.bat without options.
  echo For another repository or branch, set SIMDASH_UPDATE_REPOSITORY and SIMDASH_UPDATE_BRANCH.
  pause
  exit /b 2
)
(
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$body = [IO.File]::ReadAllText($env:SIMDASH_UPDATE_LAUNCHER); $parts = $body -split '(?m)^# WORKBENCH_BOOTSTRAP\r?$', 2; if ($parts.Count -ne 2) { exit 1 }; & ([scriptblock]::Create($parts[1]))"
  if errorlevel 1 (
    echo First-time update failed. Read the error above and run this file again.
    pause
    exit /b 1
  ) else (
    exit /b 0
  )
)
# WORKBENCH_BOOTSTRAP
$ErrorActionPreference = 'Stop'
try {
    $targetRoot = [IO.Path]::GetFullPath($env:SIMDASH_UPDATE_BOOTSTRAP_ROOT).TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $targetRoot 'stop.ps1') -PathType Leaf)) {
        throw 'Place update.bat in the existing deployment folder beside stop.ps1.'
    }
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) { throw 'Git is required. Install Git, then run update.bat again.' }
    $backupRoot = Join-Path $targetRoot 'backups'
    $ancestor = $targetRoot
    while ($ancestor) {
        if ((Get-Item -Force -LiteralPath $ancestor).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'The deployment is inside a linked folder. Use a physical deployment folder.'
        }
        $ancestor = Split-Path -Parent $ancestor
    }
    foreach ($checkedPath in @($targetRoot, $backupRoot)) {
        if ((Test-Path -LiteralPath $checkedPath) -and ((Get-Item -Force -LiteralPath $checkedPath).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'The deployment or backups folder is a link. Use a physical deployment folder.'
        }
    }
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $stageRoot = Join-Path $backupRoot ('updater-driver-' + [Guid]::NewGuid().ToString('N'))
    $repository = if ($env:SIMDASH_UPDATE_REPOSITORY) { $env:SIMDASH_UPDATE_REPOSITORY } else { 'https://github.com/wgcha/simdashboard.git' }
    $branch = if ($env:SIMDASH_UPDATE_BRANCH) { $env:SIMDASH_UPDATE_BRANCH } else { 'codex/windows-one-click-deploy' }
    Write-Host 'Downloading the first-time updater. Existing settings and database stay in this folder.' -ForegroundColor Cyan
    & git.exe clone --depth 1 --single-branch --branch $branch -- $repository $stageRoot
    if ($LASTEXITCODE -ne 0) { throw 'Git download failed. Check the existing Git access/proxy settings and retry.' }
    $driver = Join-Path $stageRoot 'update.ps1'
    $module = Join-Path $stageRoot 'scripts\windows\GitUpdate.psm1'
    if (-not (Test-Path -LiteralPath $driver -PathType Leaf) -or -not (Test-Path -LiteralPath $module -PathType Leaf)) {
        throw 'The remote branch does not contain the new updater yet. Publish the updater to that branch, then run this file again.'
    }
    $psExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $psExe -NoProfile -ExecutionPolicy Bypass -File $driver -ProjectRoot $targetRoot -RepositoryUrl $repository -Branch $branch
    exit $LASTEXITCODE
}
catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
