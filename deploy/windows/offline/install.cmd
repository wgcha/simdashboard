@echo off
setlocal
rem Windows Server 2022 offline installer. The PowerShell script performs all
rem validation before copying files or changing services. No network fallback.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "code=%ERRORLEVEL%"
if not "%code%"=="0" (
  echo.
  echo Installation failed with exit code %code%. Review the message above.
)
if "%code%"=="0" echo Installation completed. Review the service address above.
pause
exit /b %code%
