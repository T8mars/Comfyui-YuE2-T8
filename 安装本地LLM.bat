@echo off
chcp 65001 >nul
cd /d "%~dp0"
"runtime\core\python.exe" -X utf8 "scripts\install_llm.py"
if errorlevel 1 (
  echo 本地 LLM 安装失败。原有音乐环境未修改，请保留上方错误信息。
) else (
  echo 本地 LLM 环境安装完成。请在 AI 创作助手选择 GGUF 并测试模型连接。
)
pause
