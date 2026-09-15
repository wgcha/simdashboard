@echo off
setlocal EnableExtensions
chcp 65001 >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -StartAfterDeploy %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Deployment failed with exit code %EXIT_CODE%.
  pause
) else (
  echo.
  echo Deployment completed and the application launch was requested.
  pause
)
exit /b %EXIT_CODE%
