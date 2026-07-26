@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  Analysis Canvas - Stop
echo ============================================================
echo.

if not exist "stop.ps1" (
  echo [ERROR] stop.ps1 was not found.
  goto :fail
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 1 goto :fail

echo.
echo Servers stopped successfully.
exit /b 0

:fail
echo.
echo [ERROR] Failed to stop the servers.
echo.
pause
exit /b 1
