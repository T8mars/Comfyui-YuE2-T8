param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $KitRoot 'runtime\core\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw "Runtime is not installed. Run the setup launcher first." }
$env:YUE2_HOME = $KitRoot
$env:YUE2_KIT = $KitRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HOME = Join-Path $KitRoot 'cache\huggingface'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $KitRoot 'runtime\playwright'
$env:PATH = "$(Join-Path $KitRoot 'runtime\ffmpeg');$env:PATH"
$ServiceUrl = 'http://127.0.0.1:8189'
$Running = $false
$ExpectedVersion = (& $Python -X utf8 -c 'from app.yue2_app import __version__; print(__version__)' | Out-String).Trim()
$Health = $null
try { $Health = Invoke-RestMethod -Uri "$ServiceUrl/api/health" -TimeoutSec 2 } catch { }
if ($Health -and $Health.ok -eq $true) {
    $ActualRoot = [IO.Path]::GetFullPath([string]$Health.root).TrimEnd('\')
    if ($ActualRoot -ine $KitRoot.TrimEnd('\')) {
        throw "Port 8189 is used by another YuE2 installation: $ActualRoot"
    }
    if ([string]$Health.version -ne $ExpectedVersion) {
        throw "YuE2 service version $($Health.version) is stale; run stop_service.bat first"
    }
    $Running = $true
}
if (-not $Running) {
    New-Item -ItemType Directory -Force (Join-Path $KitRoot 'logs') | Out-Null
    $Process = Start-Process -FilePath $Python -ArgumentList '-X','utf8','-m','app.yue2_app.service','--host','127.0.0.1','--port','8189' `
        -WorkingDirectory $KitRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $KitRoot 'logs\server.stdout.log') `
        -RedirectStandardError (Join-Path $KitRoot 'logs\server.stderr.log')
    $Deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 400
        if ($Process.HasExited) { throw "YuE2 service exited with code $($Process.ExitCode). Check logs\server.stderr.log" }
        try { $Running = (Invoke-RestMethod -Uri "$ServiceUrl/api/health" -TimeoutSec 2).ok -eq $true } catch { }
    } while (-not $Running -and (Get-Date) -lt $Deadline)
    if (-not $Running) {
        & taskkill.exe /PID $Process.Id /T /F | Out-Null
        throw 'YuE2 service startup timed out'
    }
}
if (-not $NoBrowser) { Start-Process $ServiceUrl }
