@echo off
setlocal
chcp 65001 >nul
title T8star-Aix - YuE2 模型路径设置
call "%~dp0configure_models.bat" %*
