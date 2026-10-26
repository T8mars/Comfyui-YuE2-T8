@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
echo 请先关闭工作台及使用同一模型目录的 ComfyUI。
echo 正常文件不会重新下载。异常时只下载固定版本 VAE，并校验大小和 SHA-256。
echo 原文件备份到 logs\backups，不删除歌曲、乐谱、训练模型或设置。
if not exist "runtime\python.exe" (
  echo 找不到整合包运行环境，请在完整整合包目录中运行。
  popd
  pause
  exit /b 1
)
"runtime\python.exe" -X utf8 -m app.yue2_app.model_repair --root "."
set "repair_result=%errorlevel%"
if "%repair_result%"=="0" echo VAE 检查完成。请重启工作台，从已保存的乐谱新建生成任务。
popd
pause
exit /b %repair_result%
