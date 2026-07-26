@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ============================================================
echo  Analysis Canvas - Start
echo ============================================================
echo.

if not exist "start.ps1" (
  echo [ERROR] start.ps1 was not found.
  goto :fail
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 goto :fail

if /i not "%~1"=="--no-browser" (
  echo.
  echo Opening the dashboard in your browser.
  start "" "http://127.0.0.1:5173"
)

echo.
echo Servers are running in the background.
echo Run stop.bat to stop them.
exit /b 0

:fail
echo.
echo [ERROR] Failed to start the servers.
echo Run setup-windows.bat first, then try again.
echo.
pause
exit /b 1
