$ErrorActionPreference = 'Stop'
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$State = Join-Path $KitRoot 'server.json'
if (-not (Test-Path -LiteralPath $State)) { Write-Host 'No server state file'; exit 0 }
$Info = Get-Content -LiteralPath $State -Raw | ConvertFrom-Json
$Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Info.pid)" -ErrorAction SilentlyContinue
if (-not $Process) {
    Remove-Item -LiteralPath $State -Force -ErrorAction SilentlyContinue
    Write-Host 'YuE2 service is already stopped'
    exit 0
}
if ($Process.CommandLine -notmatch 'app\.yue2_app\.service') {
    throw "Refusing to stop PID $($Info.pid): it is not the YuE2 service"
}
$AllowedPython = @(
    [IO.Path]::GetFullPath((Join-Path $KitRoot 'runtime\python.exe')),
    [IO.Path]::GetFullPath((Join-Path $KitRoot 'runtime\core\python.exe'))
)
if (-not $Process.ExecutablePath -or [IO.Path]::GetFullPath([string]$Process.ExecutablePath) -notin $AllowedPython) {
    throw "Refusing to stop PID $($Info.pid): executable does not belong to this installation"
}
& taskkill.exe /PID ([int]$Info.pid) /T /F | Out-Host
if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $Info.pid -ErrorAction SilentlyContinue)) {
    throw "Failed to stop YuE2 service process tree $($Info.pid)"
}
Remove-Item -LiteralPath $State -Force -ErrorAction SilentlyContinue
Write-Host "Stopped YuE2 service $($Info.pid)"
