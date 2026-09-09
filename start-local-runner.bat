@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local-runner.ps1" %*
