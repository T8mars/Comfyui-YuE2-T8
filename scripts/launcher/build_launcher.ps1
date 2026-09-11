param(
    [string]$OutputPath = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path 'YuE2-T8.exe')
)
$ErrorActionPreference = 'Stop'
$Compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $Compiler)) {
    $Compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $Compiler)) { throw '找不到 .NET Framework C# 编译器 csc.exe。' }
$Source = Join-Path $PSScriptRoot 'YuE2Launcher.cs'
$Icon = Join-Path $PSScriptRoot 'yue2.ico'
if (-not (Test-Path -LiteralPath $Icon)) { throw '找不到启动器图标 yue2.ico。' }
& $Compiler /nologo /target:exe /platform:anycpu "/win32icon:$Icon" "/out:$OutputPath" $Source
if ($LASTEXITCODE -ne 0) { throw "启动器编译失败，退出代码 $LASTEXITCODE。" }
Write-Host "已生成：$OutputPath" -ForegroundColor Green
