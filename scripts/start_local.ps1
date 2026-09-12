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
$Python = Join-Path $KitRoot 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw '尚未安装运行环境，请先双击 安装运行环境.bat。' }
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
        $Queued = [int]$Health.queued
        $CurrentJob = $Health.current_job
        if ($CurrentJob -or $Queued -gt 0) {
            $JobId = if ($CurrentJob -and $CurrentJob.id) { [string]$CurrentJob.id } else { '等待中的任务' }
            throw "另一套 YuE2 正在处理 $JobId，后面还有 $Queued 个排队任务。为避免中断任务，本次没有自动切换；请任务结束后重新启动。"
        }
        Write-Host "[YuE2] 检测到另一套空闲服务：$ActualRoot" -ForegroundColor Yellow
        Write-Host '[YuE2] 正在安全切换到当前整合包...' -ForegroundColor Cyan
        $OtherStatePath = Join-Path $ActualRoot 'server.json'
        if (-not (Test-Path -LiteralPath $OtherStatePath)) {
            throw "无法确认另一套服务的进程信息，请在 $ActualRoot 中先运行停止脚本。"
        }
        $OtherState = Get-Content -LiteralPath $OtherStatePath -Raw | ConvertFrom-Json
        $OtherPid = [int]$OtherState.pid
        $OtherProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$OtherPid" -ErrorAction SilentlyContinue
        $ExpectedOtherPythons = @([IO.Path]::GetFullPath((Join-Path $ActualRoot 'runtime\python.exe')), [IO.Path]::GetFullPath((Join-Path $ActualRoot 'runtime\core\python.exe')))
        if (-not $OtherProcess -or
            $OtherProcess.CommandLine -notmatch 'app\.yue2_app\.service' -or
            ($ExpectedOtherPythons -inotcontains [IO.Path]::GetFullPath([string]$OtherProcess.ExecutablePath))) {
            throw "无法安全识别占用端口的进程，请在 $ActualRoot 中先运行停止脚本。"
        }
        & taskkill.exe /PID $OtherPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $OtherPid -ErrorAction SilentlyContinue)) {
            throw "无法停止另一套 YuE2 服务进程 $OtherPid。"
        }
        Remove-Item -LiteralPath $OtherStatePath -Force -ErrorAction SilentlyContinue
        $SwitchDeadline = (Get-Date).AddSeconds(10)
        do {
            Start-Sleep -Milliseconds 300
            $OtherStillRunning = $false
            try { $OtherStillRunning = (Invoke-RestMethod -Uri "$ServiceUrl/api/health" -TimeoutSec 1).ok -eq $true } catch { }
        } while ($OtherStillRunning -and (Get-Date) -lt $SwitchDeadline)
        if ($OtherStillRunning) { throw '另一套 YuE2 服务未能在 10 秒内停止。' }
        Write-Host '[YuE2] 已切换，正在启动当前整合包。' -ForegroundColor Green
    } else {
        if ([string]$Health.version -ne $ExpectedVersion) {
            throw "后台服务版本为 $($Health.version)，当前整合包版本为 $ExpectedVersion。请先运行 停止本地服务.ps1 后重试。"
        }
        $Running = $true
        Write-Host "[YuE2] 后台服务已在运行，版本 $ExpectedVersion。" -ForegroundColor Green
    }
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
