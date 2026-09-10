@echo off
setlocal EnableExtensions
chcp 65001 >nul

echo 서버 중지 상태를 확인하고 있습니다...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
  echo [완료] 서버 중지 확인이 끝났습니다.
) else (
  echo [실패] 서버 중지를 완료하지 못했습니다. 오류 코드: %EXIT_CODE%
)
echo 아무 키나 누르면 이 창을 닫습니다.
pause >nul
exit /b %EXIT_CODE%
