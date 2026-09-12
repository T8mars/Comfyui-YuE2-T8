param(
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [int]$ServicePid = 0,
    [int]$BootstrapPid = 0,
    [ValidateRange(1,65535)][int]$Port = 8189,
    [string]$PreparedRuntime = '',
    [switch]$NoBrowser,
    [switch]$ReuseRuntime,
    [switch]$SkipModels
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'runtime_paths.ps1')
$Target = (Resolve-Path -LiteralPath $Target).Path.TrimEnd('\')
$Source = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\')
$UpdateRoot = Join-Path $Target 'cache\updates'
if (-not $Source.StartsWith($UpdateRoot + '\',[StringComparison]::OrdinalIgnoreCase)) { throw '更新来源不在当前安装目录的 cache/updates 中' }
$Release = Get-Content -LiteralPath $Manifest -Raw -Encoding utf8 | ConvertFrom-Json
$Version = [string]$Release.version
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw '更新版本号无效' }
$InstallHome = Join-Path $Target 'cache\unified-install'
$StatusPath = Join-Path $Target 'logs\update-status.json'
$ProgressScript = Join-Path $Target 'logs\update-progress.js'
$ProgressPage = Join-Path $Target 'logs\update-progress.html'
$ServiceUrl = "http://127.0.0.1:$Port/"
New-Item -ItemType Directory -Force (Join-Path $Target 'logs'),$InstallHome | Out-Null
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$OriginalMarker = Join-Path $Target 'app\yue2_app\__init__.py'
$OriginalVersion = if (Test-Path -LiteralPath $OriginalMarker) { [regex]::Match((Get-Content -LiteralPath $OriginalMarker -Raw -Encoding utf8),'__version__\s*=\s*"([^"]+)"').Groups[1].Value } else { '' }
function Write-Status([string]$State, [string]$Message, [hashtable]$Extra = @{}) {
    $Value = @{state=$State;target_version=$Version;message=$Message;updated_at=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()}
    foreach ($Key in $Extra.Keys) { $Value[$Key] = $Extra[$Key] }
    $Json = $Value | ConvertTo-Json -Depth 10
    $Temporary = "$StatusPath.$([guid]::NewGuid().ToString('N')).tmp"
    [IO.File]::WriteAllText($Temporary,$Json,$Utf8)
    Move-Item -LiteralPath $Temporary -Destination $StatusPath -Force
    [IO.File]::WriteAllText($ProgressScript,"window.yue2UpdateProgress($Json);",$Utf8)
}
function Assert-Child([string]$Path, [string]$Parent) {
    $Value = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $Owner = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not $Value.StartsWith($Owner + '\',[StringComparison]::OrdinalIgnoreCase)) { throw '迁移路径超出安装目录' }
    if ((Test-Path -LiteralPath $Value) -and (Get-Item -LiteralPath $Value).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) { throw '迁移目录不能是链接' }
    return $Value
}
function Check-Exit([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step 失败：$LASTEXITCODE" } }
function Wait-Exit([int]$ProcessId) {
    if ($ProcessId -le 0) { return }
    $Limit = (Get-Date).AddSeconds(90)
    while ((Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $Limit) { Start-Sleep -Milliseconds 250 }
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) { throw "进程 $ProcessId 尚未安全退出，更新未切换" }
}
function Start-InstalledService {
    $ServicePython = Join-Path $Target 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $ServicePython)) { $ServicePython = Join-Path $Target 'runtime\core\python.exe' }
    if (-not (Test-Path -LiteralPath $ServicePython)) { throw '找不到整合包运行时' }
    $env:YUE2_HOME = $Target; $env:YUE2_KIT = $Target
    $env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
    $env:HF_HUB_OFFLINE = '1'; $env:TRANSFORMERS_OFFLINE = '1'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Target 'runtime\playwright'
    return Start-Process -FilePath $ServicePython -ArgumentList '-X','utf8','-m','app.yue2_app.service','--host','127.0.0.1','--port',([string]$Port) -WorkingDirectory $Target -WindowStyle Hidden -RedirectStandardOutput (Join-Path $Target 'logs\server.stdout.log') -RedirectStandardError (Join-Path $Target 'logs\server.stderr.log') -PassThru
}
function Wait-Health($Process, [string]$ExpectedVersion) {
    $Limit = (Get-Date).AddSeconds(45)
    do {
        $Process.Refresh()
        if ($Process.HasExited) { throw '更新后的服务未能启动，请查看服务日志' }
        try {
            $Health = Invoke-RestMethod -Uri ($ServiceUrl + 'api/health') -TimeoutSec 2
            if ($Health.ok -and $Health.version -eq $ExpectedVersion -and [IO.Path]::GetFullPath([string]$Health.root).TrimEnd('\') -ieq $Target) { return }
        } catch { }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $Limit)
    throw '更新后的服务未能通过启动验证'
}
$Html = @'
<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>YuE2 更新进度</title>
<style>body{font:17px system-ui;background:#f5f4fb;color:#17213e;max-width:760px;margin:12vh auto;padding:28px}main{background:white;border-radius:20px;padding:35px;border:1px solid #e7d2e3}h1{font-size:28px}p{line-height:1.8}a{color:#a43d71}#state{color:#a43d71}</style>
<main><p>YuE2 · 本地工作室</p><h1>正在更新整合包</h1><p id="state">正在准备升级，请保持此页面打开。</p><p>首次升级需要安装统一的 Python 运行环境。更新完成后会自动返回工作室，音色、模型和作品会保留。</p><p><a id="return" href="__SERVICE__">返回工作室</a> · <a href="update.stdout.log" target="_blank">查看安装日志</a></p></main>
<script>let done=false;window.yue2UpdateProgress=s=>{document.getElementById('state').textContent=s.message;document.querySelector('h1').textContent=s.state==='error'?'更新未完成':s.state==='complete'?'更新完成':'正在更新整合包';if(s.state==='complete'&&!done){done=true;setTimeout(()=>location.replace('__SERVICE__'),1200)}};function poll(){const script=document.createElement('script');script.src='update-progress.js?t='+Date.now();script.onload=script.onerror=()=>script.remove();document.head.append(script)}poll();setInterval(poll,1500);</script></html>
'@
$TransactionLock = [IO.File]::Open((Join-Path $InstallHome 'update.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
$Backup = $null; $Stage = $null; $Previous = $null; $Promoted = $false; $UpdatedService = $null; $Committed = $false
try {
    [IO.File]::WriteAllText($ProgressPage,$Html.Replace('__SERVICE__',$ServiceUrl),$Utf8)
    Write-Status 'waiting_for_service' '正在等待本地服务安全退出'
    if (-not $NoBrowser) { Start-Process -FilePath $ProgressPage -WindowStyle Hidden }
    Wait-Exit $ServicePid
    Wait-Exit $BootstrapPid
    Write-Status 'preparing_runtime' '正在准备统一 Python 运行环境，请保持页面打开；安装日志可查看下载和验证进度'
    if ($ReuseRuntime) {
        $Stage = Assert-Child (Join-Path $Target 'runtime') $Target
        & (Join-Path $Stage 'python.exe') -X utf8 (Join-Path $Source 'scripts\install_unified_runtime.py') --root $Target --source $Source --runtime $Stage --verify-only
        Check-Exit '现有统一运行环境验证'
    } elseif ($PreparedRuntime) {
        $Stage = Assert-Child $PreparedRuntime $InstallHome
        & (Join-Path $Stage 'python.exe') -X utf8 (Join-Path $Source 'scripts\install_unified_runtime.py') --root $Target --source $Source --runtime $Stage --verify-only
        Check-Exit '预备运行环境验证'
    } else {
        & (Join-Path $Source 'scripts\setup_unified.ps1') -InstallRoot $Target -PrepareOnly -SkipModels
        $Prepared = Get-Content -LiteralPath (Join-Path $InstallHome 'prepared.json') -Raw -Encoding utf8 | ConvertFrom-Json
        $Stage = Assert-Child ([string]$Prepared.runtime) $InstallHome
    }
    $StagePython = Join-Path $Stage 'python.exe'
    if (-not $SkipModels) {
        Write-Status 'preparing_models' '正在下载并校验新增的 RVC 基础模型；已有模型与作品保持原位'
        $env:HF_HUB_OFFLINE = '0'
        & $StagePython -X utf8 (Join-Path $Source 'scripts\install_staged_models.py') --source $Source --root $Target
        Check-Exit 'RVC 模型安装'
    }
    Write-Status 'installing' '正在备份并安装新版代码'
    & $StagePython -X utf8 (Join-Path $Source 'scripts\apply_update.py') --target $Target --source $Source --manifest $Manifest --pid 0 --files-only
    Check-Exit '新版代码安装'
    $FileState = Get-Content -LiteralPath $StatusPath -Raw -Encoding utf8 | ConvertFrom-Json
    $Backup = Assert-Child ([string]$FileState.backup) (Join-Path $Target 'logs\backups')
    if (-not $ReuseRuntime) {
        Write-Status 'switching_runtime' '正在切换到统一运行环境'
        & (Join-Path $Source 'scripts\setup_unified.ps1') -InstallRoot $Target -PreparedRuntime $Stage -KeepPrevious -SkipModels
        $Installed = Get-Content -LiteralPath (Join-Path $InstallHome 'last-install.json') -Raw -Encoding utf8 | ConvertFrom-Json
        $Previous = if ($Installed.previous) { Assert-Child ([string]$Installed.previous) $InstallHome } else { $null }
        $Promoted = $true
    }
    Write-Status 'verifying_service' '正在启动并验证新版工作室'
    $UpdatedService = Start-InstalledService
    Wait-Health $UpdatedService $Version
    $Committed = $true
    if ($Previous) {
        Write-Status 'cleaning_runtime' '新版已通过启动验证，正在清理旧运行环境'
        Remove-OwnedRuntimeTree $Previous $InstallHome
    }
    Write-Status 'complete' "已更新到 v$Version，所有功能共用一个 Python 运行环境" @{version=$Version;backup=$Backup}
} catch {
    $Failure = $_.Exception.Message
    if ($Committed) {
        Write-Status 'complete' "已更新到 v$Version；旧运行环境备份暂未清理：$Failure" @{version=$Version;cleanup_pending=$Previous;backup=$Backup}
    } else {
        try {
            if ($UpdatedService) {
                $UpdatedService.Refresh()
                if (-not $UpdatedService.HasExited) { Stop-Process -Id $UpdatedService.Id; $UpdatedService.WaitForExit(10000) | Out-Null }
            }
            $RollbackPython = if ($Promoted) { Join-Path $Target 'runtime\python.exe' } elseif ($Stage) { Join-Path $Stage 'python.exe' } else { $null }
            if ($Backup -and (-not $RollbackPython -or -not (Test-Path -LiteralPath $RollbackPython))) {
                $Recovery = Get-Content -LiteralPath (Join-Path $InstallHome 'recovery.json') -Raw -Encoding utf8 | ConvertFrom-Json
                if ([string]$Recovery.original_stage -ine $Stage -or [string]$Recovery.root -ine $Target) { throw '安装恢复记录不属于本次更新' }
                $RecoveredRuntime = Assert-Child ([string]$Recovery.staged_runtime) $InstallHome
                $RollbackPython = Join-Path $RecoveredRuntime 'python.exe'
            }
            if ($Backup) {
                & $RollbackPython -X utf8 (Join-Path $Source 'scripts\apply_update.py') --target $Target --source $Source --manifest $Manifest --pid 0 --restore-backup $Backup
                Check-Exit '旧版本代码恢复'
            }
            if ($Promoted) {
                $Failed = Assert-Child (Join-Path $InstallHome ('failed-update-' + [guid]::NewGuid().ToString('N'))) $InstallHome
                $CurrentRuntime = Assert-Child (Join-Path $Target 'runtime') $Target
                Move-Item -LiteralPath $CurrentRuntime -Destination $Failed
                if ($Previous) { Move-Item -LiteralPath $Previous -Destination $CurrentRuntime }
            }
            $StillRunning = $ServicePid -gt 0 -and (Get-Process -Id $ServicePid -ErrorAction SilentlyContinue)
            if (-not $StillRunning -and (Test-Path -LiteralPath (Join-Path $Target 'app\yue2_app\service.py'))) {
                $RestoredService = Start-InstalledService
                if ($OriginalVersion) { Wait-Health $RestoredService $OriginalVersion }
            }
            Write-Status 'error' "更新失败，已恢复原版本：$Failure" @{backup=$Backup}
        } catch {
            Write-Status 'error' "更新失败：$Failure；恢复未完成：$($_.Exception.Message)。请保留日志和备份。" @{backup=$Backup}
        }
        exit 1
    }
} finally {
    if ($TransactionLock) { $TransactionLock.Dispose() }
}
