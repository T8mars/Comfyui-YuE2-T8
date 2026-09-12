param([string]$ComfyUIPath = '')
$ErrorActionPreference = 'Stop'
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $ComfyUIPath) { $ComfyUIPath = Read-Host '请输入 ComfyUI 根目录（包含 custom_nodes）' }
$ComfyUIPath = (Resolve-Path -LiteralPath $ComfyUIPath).Path
$CustomNodes = Join-Path $ComfyUIPath 'custom_nodes'
if (-not (Test-Path -LiteralPath $CustomNodes -PathType Container)) { throw "Invalid ComfyUI directory: $ComfyUIPath" }
$Destination = Join-Path $CustomNodes 'ComfyUI-YuE2'
New-Item -ItemType Directory -Force $Destination | Out-Null
$NodeSource = Join-Path $KitRoot 'comfyui_nodes'
if (-not (Test-Path -LiteralPath $NodeSource -PathType Container)) { $NodeSource = $KitRoot }
foreach ($Name in @('__init__.py', 'client.py', 'nodes.py')) {
    Copy-Item -LiteralPath (Join-Path $NodeSource $Name) -Destination (Join-Path $Destination $Name) -Force
}
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $Destination 'yue2_home.txt'), ($KitRoot -replace '\\','/'), $Utf8)
Write-Host "Installed or updated at $Destination" -ForegroundColor Green
Write-Host 'Restart ComfyUI and find the nodes in the YuE2 category.'
