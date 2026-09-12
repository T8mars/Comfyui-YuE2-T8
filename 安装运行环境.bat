@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 (
  echo.
  echo 安装失败，请保留窗口并检查上方错误。
  pause
  exit /b 1
)
pause
