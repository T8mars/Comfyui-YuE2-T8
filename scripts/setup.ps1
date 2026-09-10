param(
    [switch]$SkipRenderer,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Downloads = Join-Path $KitRoot 'downloads'
$Runtime = Join-Path $KitRoot 'runtime'
$Core = Join-Path $Runtime 'core'
$Transcribe = Join-Path $Runtime 'transcribe'
New-Item -ItemType Directory -Force $Downloads,$Runtime,(Join-Path $KitRoot 'cache'),(Join-Path $KitRoot 'logs') | Out-Null
$Internet = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
$HasProxyEnable = $Internet -and $Internet.PSObject.Properties['ProxyEnable']
$HasProxyServer = $Internet -and $Internet.PSObject.Properties['ProxyServer']
if ($HasProxyEnable -and $HasProxyServer -and [bool]$Internet.ProxyEnable -and $Internet.ProxyServer) {
    $ProxyText = [string]$Internet.ProxyServer
    $ProxyMap = @{}
    foreach ($Entry in $ProxyText.Split(';', [System.StringSplitOptions]::RemoveEmptyEntries)) {
        if ($Entry.Contains('=')) {
            $Pair = $Entry.Split('=', 2)
            $ProxyMap[$Pair[0].Trim().ToLowerInvariant()] = $Pair[1].Trim()
        } else {
            $ProxyMap['default'] = $Entry.Trim()
        }
    }
    $HttpProxy = if ($ProxyMap['http']) { $ProxyMap['http'] } else { $ProxyMap['default'] }
    $HttpsProxy = if ($ProxyMap['https']) { $ProxyMap['https'] } elseif ($HttpProxy) { $HttpProxy } else { $ProxyMap['default'] }
    if ($HttpProxy -and $HttpProxy -notmatch '^https?://') { $HttpProxy = "http://$HttpProxy" }
    if ($HttpsProxy -and $HttpsProxy -notmatch '^https?://') { $HttpsProxy = "http://$HttpsProxy" }
    if ($HttpProxy) { $env:HTTP_PROXY = $HttpProxy }
    if ($HttpsProxy) { $env:HTTPS_PROXY = $HttpsProxy }
    Write-Host "Using Windows proxy configuration"
}

function Assert-ExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}

function Get-Download([string]$Uri, [string]$Destination) {
    if ((Test-Path -LiteralPath $Destination) -and -not $Force) { return }
    Write-Host "Downloading $Uri"
    $Partial = "$Destination.partial"
    if (Test-Path -LiteralPath $Partial) {
        & curl.exe -L --fail --retry 5 --retry-delay 2 -C - $Uri -o $Partial
    } else {
        & curl.exe -L --fail --retry 5 --retry-delay 2 $Uri -o $Partial
    }
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Uri" }
    Move-Item -LiteralPath $Partial -Destination $Destination -Force
}

function Install-EmbeddedPython([string]$Version, [string]$Directory) {
    $Short = ($Version.Split('.')[0..1] -join '')
    $Archive = Join-Path $Downloads "python-$Version-embed-amd64.zip"
    Get-Download "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip" $Archive
    if ($Force -or -not (Test-Path -LiteralPath (Join-Path $Directory 'python.exe'))) {
        New-Item -ItemType Directory -Force $Directory | Out-Null
        Expand-Archive -LiteralPath $Archive -DestinationPath $Directory -Force
    }
    $Pth = Join-Path $Directory "python$Short._pth"
    $Lines = Get-Content -LiteralPath $Pth | Where-Object { $_ -ne '#import site' -and $_ -ne 'import site' -and $_ -ne 'Lib\site-packages' -and $_ -ne '..\..' }
    @($Lines + 'Lib\site-packages' + '..\..' + 'import site') | Set-Content -LiteralPath $Pth -Encoding ascii
    $Pip = Join-Path $Directory 'Scripts\pip.exe'
    if (-not (Test-Path -LiteralPath $Pip)) {
        $GetPip = Join-Path $Downloads 'get-pip.py'
        Get-Download 'https://bootstrap.pypa.io/get-pip.py' $GetPip
        & (Join-Path $Directory 'python.exe') -X utf8 $GetPip --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed: $Directory" }
    }
}

Write-Host 'Configuring YuE2 Python 3.12 runtime'
Install-EmbeddedPython '3.12.10' $Core
$CorePython = Join-Path $Core 'python.exe'
& $CorePython -m pip install --upgrade pip
Assert-ExitCode 'Core pip upgrade'
& $CorePython -m pip install --index-url https://download.pytorch.org/whl/cu128 'torch==2.10.0'
Assert-ExitCode 'Core Torch install'
& $CorePython -m pip install -r (Join-Path $KitRoot 'requirements-core.txt')
Assert-ExitCode 'YuE2 core dependencies install'

Write-Host 'Downloading YuE2 model bundle from Hugging Face'
& $CorePython -X utf8 -m huggingface_hub.commands.huggingface_cli download t8star/YuE2-Comfy --revision 553a4778c81403bc15ad2c56fde56894c3a2ed24 --local-dir (Join-Path $KitRoot 'models')
Assert-ExitCode 'YuE2 model bundle download'
& $CorePython -X utf8 (Join-Path $KitRoot 'scripts\verify_models.py') --root $KitRoot
Assert-ExitCode 'YuE2 model bundle verification'

Write-Host 'Configuring SheetSage2 Python 3.11 runtime'
Install-EmbeddedPython '3.11.9' $Transcribe
$TranscribePython = Join-Path $Transcribe 'python.exe'
& $TranscribePython -m pip install --upgrade pip
Assert-ExitCode 'Transcription pip upgrade'
& $TranscribePython -m pip install --index-url https://download.pytorch.org/whl/cu128 'torch==2.8.0' 'torchaudio==2.8.0'
Assert-ExitCode 'Transcription Torch install'
& $TranscribePython -m pip install -r (Join-Path $KitRoot 'requirements-transcribe.txt')
Assert-ExitCode 'SheetSage2 dependencies install'

$FfmpegDir = Join-Path $Runtime 'ffmpeg'
New-Item -ItemType Directory -Force $FfmpegDir | Out-Null
$Ffmpeg = (& $CorePython -X utf8 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())' | Out-String).Trim()
if (-not (Test-Path -LiteralPath $Ffmpeg)) { throw 'Bundled FFmpeg was not found' }
Copy-Item -LiteralPath $Ffmpeg -Destination (Join-Path $FfmpegDir 'ffmpeg.exe') -Force

if (-not $SkipRenderer) {
    Write-Host 'Installing offline score renderer browser'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Runtime 'playwright'
    & $TranscribePython -m playwright install --only-shell chromium
    Assert-ExitCode 'Chromium renderer install'
}

$env:YUE2_HOME = $KitRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PATH = "$FfmpegDir;$env:PATH"
Write-Host 'Verifying core imports and CUDA'
& $CorePython -X utf8 -c "import torch,transformers,numpy,soundfile; assert torch.cuda.is_available(); assert torch.cuda.is_bf16_supported(); print('core',torch.__version__,torch.version.cuda,torch.cuda.get_device_name(0))"
Assert-ExitCode 'Core runtime verification'
Write-Host 'Verifying transcription imports and CUDA'
& $TranscribePython -X utf8 -c "import torch,torchaudio,transformers,numpy,pretty_midi,mir_eval; assert torch.cuda.is_available(); print('transcribe',torch.__version__,torchaudio.__version__,torch.version.cuda)"
Assert-ExitCode 'Transcription runtime verification'

$Manifest = [ordered]@{
    installed_at = (Get-Date).ToString('o')
    core_python = (& $CorePython --version 2>&1 | Out-String).Trim()
    core_torch = (& $CorePython -c 'import torch;print(torch.__version__)' | Out-String).Trim()
    transcribe_python = (& $TranscribePython --version 2>&1 | Out-String).Trim()
    transcribe_torch = (& $TranscribePython -c 'import torch;print(torch.__version__)' | Out-String).Trim()
    renderer = -not $SkipRenderer
    ffmpeg = (Join-Path $FfmpegDir 'ffmpeg.exe')
}
$Manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Runtime 'installed.json') -Encoding utf8
Write-Host "`nInstallation complete. Run the local launcher in $KitRoot" -ForegroundColor Green
