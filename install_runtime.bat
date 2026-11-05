@echo off
setlocal
"%SystemRoot%\System32\chcp.com" 65001 >nul
call "%~dp0scripts\windows_powershell.cmd"
if errorlevel 1 exit /b 6
"%YUE2_POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
if errorlevel 1 pause
