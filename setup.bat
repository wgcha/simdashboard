@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo  Analysis Canvas - Integrated Windows Setup
echo ============================================================
echo.

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 goto :fail

echo.
echo Setup completed. Run start-postgresql.bat to start the service.
if /i not "%SETUP_NO_PAUSE%"=="1" pause
exit /b 0

:fail
echo.
echo [ERROR] Integrated setup did not complete.
if /i not "%SETUP_NO_PAUSE%"=="1" pause
exit /b 1
