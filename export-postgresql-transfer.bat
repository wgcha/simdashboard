@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo.
echo ============================================================
echo  Analysis Canvas - Export PostgreSQL Transfer Bundle
echo ============================================================
echo.
if "%~1"=="" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0export-postgresql-transfer.ps1"
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0export-postgresql-transfer.ps1" -OutputDir "%~1"
)
if errorlevel 1 goto :fail
echo.
echo Transfer export completed.
exit /b 0
:fail
echo.
echo [ERROR] Transfer export failed. Run stop.bat and retry.
pause
exit /b 1

