@echo off
setlocal
chcp 65001 >nul
title T8star-Aix - YuE2 Music T8
cd /d "%~dp0"
echo.
echo [YuE2] Starting the local studio. Please wait...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_webui.ps1"
set "YUE2_EXIT=%ERRORLEVEL%"
echo.
if not "%YUE2_EXIT%"=="0" (
    echo [FAILED] See the message above and logs\server.stderr.log.
) else (
    echo [READY] Local studio: http://127.0.0.1:8189
    echo Closing this window will not stop the background service.
)
if /i not "%YUE2_NO_PAUSE%"=="1" (
    echo.
    pause
)
exit /b %YUE2_EXIT%
