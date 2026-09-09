@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Compatibility entrypoint. setup.ps1 owns pinned runtime setup and preserves .env/database state.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
exit /b %ERRORLEVEL%
