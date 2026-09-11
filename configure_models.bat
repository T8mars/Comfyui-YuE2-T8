@echo off
setlocal
chcp 65001 >nul
title T8star-Aix - YuE2 Model Path
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\configure_models.ps1" %*
echo.
if errorlevel 1 echo [FAILED] 模型路径保存失败，请查看上方提示。
pause
