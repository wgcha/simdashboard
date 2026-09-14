@echo off
setlocal

REM Usage: setup-accounts.bat [-NoPause] [-NonInteractive]
REM -NoPause is recognized only as the first switch so interactive server use
REM always leaves the result on screen after a double-click.
set "NO_PAUSE="
if /I "%~1"=="-NoPause" (
    set "NO_PAUSE=1"
    shift
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-accounts.ps1" %*
set "SETUP_EXIT_CODE=%ERRORLEVEL%"

if defined NO_PAUSE exit /b %SETUP_EXIT_CODE%

if "%SETUP_EXIT_CODE%"=="0" (
    echo.
    echo Account setup completed successfully.
) else (
    echo.
    echo Account setup did not complete. Review the messages above and retry after correcting the reported problem.
)
echo.
pause
exit /b %SETUP_EXIT_CODE%
