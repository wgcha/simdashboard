@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo  Analysis Canvas - Windows first-time setup
echo ============================================================
echo.

if not exist "backend\requirements.txt" (
  echo [ERROR] backend\requirements.txt was not found.
  echo Run this file from the project root directory.
  goto :fail
)

if not exist "frontend\package.json" (
  echo [ERROR] frontend\package.json was not found.
  echo Confirm that the complete source repository was downloaded.
  goto :fail
)

if not exist "frontend\pnpm-lock.yaml" (
  echo [ERROR] frontend\pnpm-lock.yaml was not found.
  echo The lockfile is required for a reproducible installation.
  goto :fail
)

echo [1/6] Checking Python 3.12
set "PYTHON_CMD="

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=.venv\Scripts\python.exe"
)

where py.exe >nul 2>&1
if not defined PYTHON_CMD if not errorlevel 1 (
  py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=py -3.12"
)

if not defined PYTHON_CMD (
  where python.exe >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
  )
)

if not defined PYTHON_CMD (
  echo [ERROR] Python 3.12 x64 was not found.
  echo Install Python 3.12 and select "Add Python to PATH".
  echo Download: https://www.python.org/downloads/
  echo Python 3.14 is not compatible with the pinned DuckDB 1.3.0 package.
  goto :fail
)

for /f "delims=" %%V in ('%PYTHON_CMD% --version 2^>^&1') do echo       %%V

echo [2/6] Preparing the Python virtual environment
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] The existing .venv does not use Python 3.12.
    echo Remove the .venv directory and run this file again.
    goto :fail
  )
  echo       Reusing the existing .venv.
) else (
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create the Python virtual environment.
    goto :fail
  )
)

echo [3/6] Installing backend packages
".venv\Scripts\python.exe" -m pip install -r "backend\requirements.txt"
if errorlevel 1 (
  echo [ERROR] Backend package installation failed.
  echo Check the Internet connection or corporate proxy.
  goto :fail
)

echo [4/6] Checking Node.js
where node.exe >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js was not found.
  echo Install Node.js 20 LTS or newer.
  echo Download: https://nodejs.org/
  goto :fail
)

for /f %%V in ('node -p "Number(process.versions.node.split('.')[0])"') do set "NODE_MAJOR=%%V"
if not defined NODE_MAJOR (
  echo [ERROR] Could not determine the Node.js version.
  goto :fail
)
if %NODE_MAJOR% LSS 20 (
  echo [ERROR] Node.js 20 or newer is required. Current version:
  node --version
  goto :fail
)
for /f "delims=" %%V in ('node --version') do echo       Node.js %%V

echo [5/6] Installing frontend packages
set "PNPM_EXE="
set "PNPM_PREFIX="
where pnpm.cmd >nul 2>&1
if not errorlevel 1 set "PNPM_EXE=pnpm.cmd"

if not defined PNPM_EXE (
  where corepack.cmd >nul 2>&1
  if not errorlevel 1 (
    set "PNPM_EXE=corepack.cmd"
    set "PNPM_PREFIX=pnpm"
  )
)

if not defined PNPM_EXE (
  echo [ERROR] pnpm or Corepack was not found.
  echo Reinstall Node.js 20 LTS or run: npm install -g pnpm
  goto :fail
)

set "SETUP_PROJECT_ROOT=%CD%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Set-Location -LiteralPath (Join-Path $env:SETUP_PROJECT_ROOT 'frontend'); if ($env:PNPM_PREFIX) { & $env:PNPM_EXE $env:PNPM_PREFIX install --frozen-lockfile } else { & $env:PNPM_EXE install --frozen-lockfile }; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
if errorlevel 1 (
  echo [ERROR] Frontend package installation failed.
  goto :fail
)

echo [6/6] Verifying the frontend production build
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Set-Location -LiteralPath (Join-Path $env:SETUP_PROJECT_ROOT 'frontend'); if ($env:PNPM_PREFIX) { & $env:PNPM_EXE $env:PNPM_PREFIX run build } else { & $env:PNPM_EXE run build }; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
if errorlevel 1 (
  echo [ERROR] Frontend production build failed.
  goto :fail
)

echo.
echo ============================================================
echo  Setup completed successfully.
echo ============================================================
echo.
echo Start command:
echo   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
echo.
echo Dashboard:
echo   http://127.0.0.1:5173
echo.
if /i not "%SETUP_NO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo Setup did not complete.
if /i not "%SETUP_NO_PAUSE%"=="1" pause
exit /b 1
