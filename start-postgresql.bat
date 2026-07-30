@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  Analysis Canvas - Start with PostgreSQL
echo ============================================================
echo.

if not exist "start-postgresql.ps1" (
  echo [ERROR] start-postgresql.ps1 was not found.
  goto :fail
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-postgresql.ps1"
if errorlevel 1 goto :fail

if /i not "%~1"=="--no-browser" (
  echo.
  echo Opening the dashboard in your browser.
  start "" "http://127.0.0.1:5173"
)

echo.
echo Servers are running in the background with PostgreSQL.
echo Run stop.bat to stop them.
exit /b 0

:fail
echo.
echo [ERROR] Failed to start the servers with PostgreSQL.
echo Check the app DATABASE_URL, PostgreSQL service, and owner credential file.
echo Pending migrations use .postgres-owner.env without granting DDL to the app role.
echo See docs\deployment-security-backup-guide.md for setup instructions.
echo.
pause
exit /b 1
