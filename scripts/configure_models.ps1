param([string]$ModelsDirectory = '')
$ErrorActionPreference = 'Stop'
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$SettingsPath = Join-Path $KitRoot 'settings.json'
$DefaultModels = Join-Path $KitRoot 'models'

Write-Host 'YuE2 模型路径设置' -ForegroundColor Cyan
Write-Host "模型仓库：https://huggingface.co/t8star/YuE2-Comfy"
Write-Host "默认目录：$DefaultModels"
Write-Host '指定的文件夹必须直接包含 YuE2-3B、YuE2-Vae、SheetSage2、MERT-v2-FullSong、Seed-VC 和 Demucs。'
if (-not $ModelsDirectory) {
    $ModelsDirectory = Read-Host '输入模型文件夹；直接回车恢复默认目录'
}
if (-not $ModelsDirectory) { $ModelsDirectory = $DefaultModels }
if (-not [IO.Path]::IsPathRooted($ModelsDirectory)) { $ModelsDirectory = Join-Path $KitRoot $ModelsDirectory }
$Models = [IO.Path]::GetFullPath($ModelsDirectory)
if ((Test-Path -LiteralPath $Models) -and -not (Get-Item -LiteralPath $Models).PSIsContainer) {
    throw "模型路径不是文件夹：$Models"
}
New-Item -ItemType Directory -Force $Models | Out-Null
[ordered]@{schema = 1; model_directory = $(if ($Models -ieq $DefaultModels) {'models'} else {$Models})} |
    ConvertTo-Json | Set-Content -LiteralPath $SettingsPath -Encoding utf8

Write-Host "`n已保存模型路径：$Models" -ForegroundColor Green
Write-Host '如果模型仍在旧位置，请复制整个 models 文件夹的内容到上方目录。'
Write-Host '之后启动本地工作室并点击“重新进行完整自检”。'
