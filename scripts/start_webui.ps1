param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ServiceUrl = 'http://127.0.0.1:8189'
trap {
    Write-Host ''
    Write-Host "[启动失败] $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "错误日志：$(Join-Path $KitRoot 'logs\server.stderr.log')" -ForegroundColor DarkGray
    exit 1
}
Write-Host '[YuE2] 正在检查运行环境...' -ForegroundColor Cyan
$Python = Join-Path $KitRoot 'runtime\core\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw '尚未安装运行环境，请先双击 install_runtime.bat。' }
$env:YUE2_HOME = $KitRoot
$env:YUE2_KIT = $KitRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HOME = Join-Path $KitRoot 'cache\huggingface'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $KitRoot 'runtime\playwright'
$env:PATH = "$(Join-Path $KitRoot 'runtime\ffmpeg');$env:PATH"
$Running = $false
$ExpectedVersion = (& $Python -X utf8 -c 'from app.yue2_app import __version__; print(__version__)' | Out-String).Trim()
$Health = $null
try { $Health = Invoke-RestMethod -Uri "$ServiceUrl/api/health" -TimeoutSec 2 } catch { }
if ($Health -and $Health.ok -eq $true) {
    $ActualRoot = [IO.Path]::GetFullPath([string]$Health.root).TrimEnd('\')
    if ($ActualRoot -ine $KitRoot.TrimEnd('\')) {
        throw "端口 8189 已被另一套 YuE2 占用：$ActualRoot"
    }
    if ([string]$Health.version -ne $ExpectedVersion) {
        throw "后台服务版本为 $($Health.version)，当前节点版本为 $ExpectedVersion。请先运行 stop_service.bat 后重试。"
    }
    $Running = $true
    Write-Host "[YuE2] 后台服务已在运行，版本 $ExpectedVersion。" -ForegroundColor Green
}
if (-not $Running) {
    Write-Host "[YuE2] 正在启动后台服务，版本 $ExpectedVersion..." -ForegroundColor Cyan
    $LogDirectory = Join-Path $KitRoot 'logs'
    New-Item -ItemType Directory -Force $LogDirectory | Out-Null
    foreach ($Name in @('server.stdout.log','server.stderr.log')) {
        $LogPath = Join-Path $LogDirectory $Name
        if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -ge 20MB) {
            $Oldest = "$LogPath.3"
            if (Test-Path -LiteralPath $Oldest) { Remove-Item -LiteralPath $Oldest -Force }
            for ($Index = 2; $Index -ge 1; $Index--) {
                $Source = "$LogPath.$Index"
                if (Test-Path -LiteralPath $Source) { Move-Item -LiteralPath $Source -Destination "$LogPath.$($Index + 1)" -Force }
            }
            Move-Item -LiteralPath $LogPath -Destination "$LogPath.1" -Force
        }
    }
    $Process = Start-Process -FilePath $Python -ArgumentList '-X','utf8','-m','app.yue2_app.service','--host','127.0.0.1','--port','8189' `
        -WorkingDirectory $KitRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $KitRoot 'logs\server.stdout.log') `
        -RedirectStandardError (Join-Path $KitRoot 'logs\server.stderr.log')
    $Deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 400
        if ($Process.HasExited) { throw "后台服务异常退出（代码 $($Process.ExitCode)），请查看 logs\server.stderr.log。" }
        try { $Running = (Invoke-RestMethod -Uri "$ServiceUrl/api/health" -TimeoutSec 2).ok -eq $true } catch { }
    } while (-not $Running -and (Get-Date) -lt $Deadline)
    if (-not $Running) {
        & taskkill.exe /PID $Process.Id /T /F | Out-Null
        throw '后台服务启动超过 30 秒，请查看 logs\server.stderr.log。'
    }
    Write-Host '[YuE2] 后台服务已就绪。' -ForegroundColor Green
}
if (-not $NoBrowser) {
    try {
        Start-Process $ServiceUrl
        Write-Host '[YuE2] 已请求系统浏览器打开工作室。' -ForegroundColor Green
    } catch {
        Write-Host "[YuE2] 浏览器未能自动打开，请手动访问：$ServiceUrl" -ForegroundColor Yellow
    }
}
Write-Host "[YuE2] 工作室地址：$ServiceUrl" -ForegroundColor Cyan
exit 0
