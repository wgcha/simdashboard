@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo.
echo ============================================================
echo  Analysis Canvas - Import PostgreSQL Transfer Bundle
echo ============================================================
echo.
set "VALIDATE="
if /i "%~2"=="--validate-only" set "VALIDATE=-ValidateOnly"
if "%~1"=="" (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0import-postgresql-transfer.ps1" %VALIDATE%
) else (
  powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0import-postgresql-transfer.ps1" -Bundle "%~1" %VALIDATE%
)
if errorlevel 1 goto :fail
echo.
echo Transfer import completed.
exit /b 0
:fail
echo.
echo [ERROR] Transfer import failed. No existing database was overwritten.
pause
exit /b 1

