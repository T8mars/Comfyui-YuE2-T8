param(
    [switch]$SkipRenderer,
    [switch]$SkipModels,
    [switch]$Force,
    [switch]$PrepareOnly,
    [switch]$KeepPrevious,
    [string]$InstallRoot = '',
    [string]$PreparedRuntime = '',
    [string]$ModelsDirectory = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime_paths.ps1')
Set-StrictMode -Version Latest
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$KitRoot = if ($InstallRoot) { (Resolve-Path -LiteralPath $InstallRoot).Path } else { $SourceRoot }
$Runtime = Join-Path $KitRoot 'runtime'
$InstallHome = Join-Path $KitRoot 'cache\unified-install'
$Downloads = Join-Path $KitRoot 'downloads'
New-Item -ItemType Directory -Force $InstallHome,$Downloads,(Join-Path $KitRoot 'logs') | Out-Null

function Assert-OwnedPath([string]$Path, [string]$Parent) {
    $Resolved = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $ResolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not $Resolved.StartsWith($ResolvedParent + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "路径超出本次安装目录：$Resolved"
    }
    if ((Test-Path -LiteralPath $Resolved) -and (Get-Item -LiteralPath $Resolved).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
        throw "安装目录不能是链接：$Resolved"
    }
    return $Resolved
}
function Assert-ExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
function Download([string]$Url, [string]$Destination, [string]$Sha = '') {
    if ((Test-Path -LiteralPath $Destination) -and (-not $Sha -or (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ieq $Sha)) { return }
    $Partial = "$Destination.partial"
    & curl.exe -L --fail --retry 4 --retry-delay 2 $Url -o $Partial
    Assert-ExitCode 'Download'
    if ($Sha -and (Get-FileHash -LiteralPath $Partial -Algorithm SHA256).Hash -ine $Sha) { throw '下载校验失败' }
    Move-Item -LiteralPath $Partial -Destination $Destination -Force
}
function Verify-Runtime([string]$Directory) {
    $VerifyArgs = @('--root',$KitRoot,'--source',$SourceRoot,'--runtime',$Directory,'--verify-only')
    if ($SkipRenderer) { $VerifyArgs += '--skip-renderer' }
    & (Join-Path $Directory 'python.exe') -X utf8 (Join-Path $PSScriptRoot 'install_unified_runtime.py') @VerifyArgs
    Assert-ExitCode 'Unified runtime verification'
}
function Assert-NoRuntimeProcesses {
    $Prefix = [IO.Path]::GetFullPath($Runtime).TrimEnd('\') + '\'
    $Active = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase) })
    if ($Active.Count) { throw '当前整合包仍在运行。请结束任务并关闭本地服务，再重新运行安装；已准备好的环境会保留。' }
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:YUE2_HOME = $KitRoot
$env:YUE2_KIT = $KitRoot
$Stage = ''
if ($PreparedRuntime) {
    $Stage = Assert-OwnedPath $PreparedRuntime $InstallHome
    Verify-Runtime $Stage
} else {
    $Stage = Join-Path $InstallHome (([guid]::NewGuid().ToString('N')) + '\runtime')
    $Stage = Assert-OwnedPath $Stage $InstallHome
    New-Item -ItemType Directory -Force $Stage | Out-Null
    $PythonArchive = Join-Path $Downloads 'python-3.12.10-embed-amd64.zip'
    Download 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' $PythonArchive '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'
    Expand-Archive -LiteralPath $PythonArchive -DestinationPath $Stage
    "python312.zip`n.`nLib\site-packages`n..`nimport site`n" | Set-Content -LiteralPath (Join-Path $Stage 'python312._pth') -Encoding ascii
    $Bootstrap = Join-Path $Downloads 'get-pip.py'
    Download 'https://bootstrap.pypa.io/get-pip.py' $Bootstrap
    $StagePython = Join-Path $Stage 'python.exe'
    & $StagePython -X utf8 $Bootstrap 'pip==26.2.1' 'setuptools==78.1.1' 'wheel==0.45.1' --no-warn-script-location
    Assert-ExitCode 'pip bootstrap'
    $InstallArgs = @('--root',$KitRoot,'--source',$SourceRoot,'--runtime',$Stage)
    if ($SkipRenderer) { $InstallArgs += '--skip-renderer' }
    & $StagePython -X utf8 (Join-Path $PSScriptRoot 'install_unified_runtime.py') @InstallArgs
    Assert-ExitCode 'Unified dependency installation'
}
$PendingPath = Join-Path $InstallHome 'prepared.json'
@{schema=1;runtime=$Stage;root=$KitRoot;prepared_at=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $PendingPath -Encoding utf8
if ($PrepareOnly) { Write-Host "统一环境准备完成：$Stage"; return }

Assert-NoRuntimeProcesses
$Runtime = Assert-OwnedPath $Runtime $KitRoot
$Previous = Assert-OwnedPath (Join-Path $InstallHome ('previous-' + [guid]::NewGuid().ToString('N'))) $InstallHome
$HadPrevious = Test-Path -LiteralPath $Runtime
$Promoted = $false
$Failed = ''
try {
    if ($HadPrevious) { Move-Item -LiteralPath $Runtime -Destination $Previous }
    Move-Item -LiteralPath $Stage -Destination $Runtime
    $Promoted = $true
    Verify-Runtime $Runtime
} catch {
    if ($Promoted) {
        $Failed = Assert-OwnedPath (Join-Path $InstallHome ('failed-' + [guid]::NewGuid().ToString('N'))) $InstallHome
        Move-Item -LiteralPath $Runtime -Destination $Failed
    }
    if ($HadPrevious -and (Test-Path -LiteralPath $Previous)) { Move-Item -LiteralPath $Previous -Destination $Runtime }
    @{schema=1;root=$KitRoot;original_stage=$Stage;staged_runtime=$(if ($Failed) {$Failed} else {$Stage})} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallHome 'recovery.json') -Encoding utf8
    throw
}
@{schema=1;runtime=$Runtime;previous=$(if ($HadPrevious) {$Previous} else {$null});retained=[bool]$KeepPrevious} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallHome 'last-install.json') -Encoding utf8
if ($HadPrevious -and -not $KeepPrevious) {
    # Both directory promotion and imports passed. Only this installation's owned old runtime is removed.
    $VerifiedPrevious = Assert-OwnedPath $Previous $InstallHome
    Remove-OwnedRuntimeTree $VerifiedPrevious $InstallHome
}

if (-not $SkipModels) {
    $ModelArgs = @('--root',$KitRoot)
    if ($ModelsDirectory) {
        if (-not [IO.Path]::IsPathRooted($ModelsDirectory)) { $ModelsDirectory = Join-Path $KitRoot $ModelsDirectory }
        $ModelsDirectory = [IO.Path]::GetFullPath($ModelsDirectory)
        New-Item -ItemType Directory -Force $ModelsDirectory | Out-Null
        $SettingsPath = Join-Path $KitRoot 'settings.json'
        $Settings = if (Test-Path -LiteralPath $SettingsPath) { Get-Content -LiteralPath $SettingsPath -Raw -Encoding utf8 | ConvertFrom-Json } else { [pscustomobject]@{schema=1} }
        $Settings | Add-Member -NotePropertyName model_directory -NotePropertyValue $ModelsDirectory -Force
        $Settings | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $SettingsPath -Encoding utf8
    }
    $Python = Join-Path $Runtime 'python.exe'
    $Models = (& $Python -X utf8 -c 'from app.yue2_app.config import ROOT; from app.yue2_app.settings import model_directory; print(model_directory(ROOT,strict=False))' | Out-String).Trim()
    Assert-ExitCode 'Model directory settings'
    & $Python -X utf8 -m huggingface_hub.cli.hf download t8star/YuE2-Comfy --revision a083f106499daead99259dd0c443a5494254cfc5 --local-dir $Models
    Assert-ExitCode 'YuE2 model download'
    & $Python -X utf8 (Join-Path $PSScriptRoot 'verify_models.py') @ModelArgs
    Assert-ExitCode 'YuE2 models verification'
    & $Python -X utf8 (Join-Path $PSScriptRoot 'verify_voice_models.py') @ModelArgs
    Assert-ExitCode 'Voice models verification'
    & $Python -X utf8 (Join-Path $PSScriptRoot 'download_rvc_models.py') @ModelArgs
    Assert-ExitCode 'RVC models installation'
}
Write-Host '统一 Python 环境安装完成。所有音乐、音色和本地 LLM 功能使用 runtime\python.exe。' -ForegroundColor Green
