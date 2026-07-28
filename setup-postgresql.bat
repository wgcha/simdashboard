@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  Analysis Canvas - First-time PostgreSQL Setup
echo ============================================================
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-postgresql.ps1"
if errorlevel 1 goto :fail

echo.
echo PostgreSQL setup completed. Run start-postgresql.bat next.
exit /b 0

:fail
echo.
echo [ERROR] PostgreSQL setup did not complete.
echo No DuckDB source data was deleted.
pause
exit /b 1
