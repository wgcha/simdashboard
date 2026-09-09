@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-accounts.ps1"
exit /b %ERRORLEVEL%
