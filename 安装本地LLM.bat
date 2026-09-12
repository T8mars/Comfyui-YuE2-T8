@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "runtime\python.exe" (
  echo 请先运行安装运行环境，升级为统一 Python 环境。
  pause
  exit /b 1
)
"runtime\python.exe" -X utf8 "scripts\install_llm.py"
if errorlevel 1 (
  echo 本地 LLM 组件验证失败，请保留上方错误信息。
) else (
  echo 本地 LLM 使用统一 Python。请在 AI 创作助手选择 GGUF 并测试模型连接。
)
pause
