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
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Project = Get-Content -LiteralPath (Join-Path $Root 'pyproject.toml') -Raw
$Match = [regex]::Match($Project, '(?m)^version\s*=\s*"(\d+\.\d+\.\d+)"$')
if (-not $Match.Success) { throw '无法从 pyproject.toml 读取版本号。' }
$Version = $Match.Groups[1].Value
$AssemblyVersion = "$Version.0"
$AssemblyInfo = Join-Path ([System.IO.Path]::GetTempPath()) ("YuE2Launcher-AssemblyInfo-{0}.cs" -f [guid]::NewGuid().ToString('N'))
@"
using System.Reflection;
[assembly: AssemblyTitle("YuE2-T8")]
[assembly: AssemblyProduct("YuE2-T8 本地音乐工作室")]
[assembly: AssemblyDescription("YuE2-T8 Windows launcher")]
[assembly: AssemblyCompany("T8star-Aix")]
[assembly: AssemblyVersion("$AssemblyVersion")]
[assembly: AssemblyFileVersion("$AssemblyVersion")]
[assembly: AssemblyInformationalVersion("$Version")]
"@ | Set-Content -LiteralPath $AssemblyInfo -Encoding UTF8
try {
    & $Compiler /nologo /target:exe /platform:anycpu "/win32icon:$Icon" "/out:$OutputPath" $Source $AssemblyInfo
    if ($LASTEXITCODE -ne 0) { throw "启动器编译失败，退出代码 $LASTEXITCODE。" }
} finally {
    Remove-Item -LiteralPath $AssemblyInfo -Force -ErrorAction SilentlyContinue
}
Write-Host "已生成：$OutputPath" -ForegroundColor Green
