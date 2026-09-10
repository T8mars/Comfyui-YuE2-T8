$ErrorActionPreference = 'Stop'
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$State = Join-Path $KitRoot 'server.json'
if (-not (Test-Path -LiteralPath $State)) { Write-Host 'No server state file'; exit 0 }
$Info = Get-Content -LiteralPath $State -Raw | ConvertFrom-Json
$Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Info.pid)" -ErrorAction SilentlyContinue
if (-not $Process) { Write-Host 'YuE2 service is already stopped'; exit 0 }
if ($Process.CommandLine -notmatch 'app\.yue2_app\.service') {
    throw "Refusing to stop PID $($Info.pid): it is not the YuE2 service"
}
Stop-Process -Id $Info.pid
Write-Host "Stopped YuE2 service $($Info.pid)"
