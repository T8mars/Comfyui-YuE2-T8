# ROCm counterpart of scripts/setup_unified.ps1.
#
# Upstream builds one embedded CPython 3.12 at runtime\python.exe and installs
# torch==2.10.0+cu128 from https://download.pytorch.org/whl/cu128, which is
# NVIDIA-only by construction. Everything else in the kit is device agnostic: on
# ROCm torch.cuda is the HIP alias, so the only CUDA-shaped checks on the
# generation path --
#     if not torch.cuda.is_available(): raise ...
#     if not torch.cuda.is_bf16_supported(): raise ...
# -- already pass on a HIP build (gfx1201 reports BF16 support natively).
#
# This script reproduces upstream's layout and verification contract and swaps
# only the wheel source:
#
#   * python-3.12.10-embed-amd64.zip          same archive, same sha256
#   * AMD TheRock ROCm runtime libraries      rocm[libraries,device-<Device>]
#   * torch + torchaudio                      rocm.nightlies.amd.com/v2/gfx120X-all/
#   * every other pin from requirements-unified.lock.txt, with the
#     --extra-index-url line and the torch/torchaudio lines stripped
#
# Two things it does that upstream's installer cannot, because both are ROCm
# specific:
#
#   * uv, not pip. AMD's `rocm` package is an sdist and an embedded interpreter
#     has no setuptools, so pip fails with "Cannot import 'setuptools.build_meta'".
#     uv builds sdists in an isolated environment with their own build deps.
#   * a final assert that torch.version.cuda is None. When the ROCm wheel install
#     fails, pip silently falls back to the PyPI CUDA wheel and everything still
#     looks installed; this assertion is what catches that.
#
# ASCII only, deliberately: cmd.exe/PowerShell consoles under a non-UTF-8 code
# page mangle non-ASCII here, and a mangled path is much harder to debug than a
# missing translation.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\rocm\setup_rocm_runtime.ps1
#   ... -Device gfx1100 -TorchVersion 2.11.0+rocm7.13.0a20260416 -Force
param(
    [string]$Device = 'gfx1201',
    [string]$TorchVersion = '2.11.0+rocm7.13.0a20260416',
    [string]$PythonVersion = '3.12.10',
    [string]$RocmIndex = 'https://rocm.nightlies.amd.com',
    [switch]$SkipPlaywright,
    [switch]$SkipRequirements,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# The embeddable interpreter honours user site-packages via its `import site`,
# which would silently pull packages from %APPDATA%\Python. Keep it hermetic.
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Runtime = Join-Path $SourceRoot 'runtime'
$Downloads = Join-Path $SourceRoot 'downloads'
New-Item -ItemType Directory -Force $Downloads | Out-Null

function Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }

# PowerShell does not throw when a native command exits non-zero, so a failed pip
# or a crashed torch import would otherwise be reported as success by whoever
# runs this script. Fail loudly instead.
function Assert-LastExit([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

# python.org is unreachable on some networks and an unbounded curl would hang
# rather than fall through to the next mirror.
function Get-FirstReachable([string[]]$Urls, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) { return }
    foreach ($url in $Urls) {
        Write-Host "  trying $(([uri]$url).Host)"
        & curl.exe -L --fail --connect-timeout 10 --max-time 300 --retry 1 -o "$Destination.partial" $url 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-Path "$Destination.partial") -and
            (Get-Item "$Destination.partial").Length -gt 1MB) {
            Move-Item "$Destination.partial" $Destination -Force
            return
        }
        Remove-Item "$Destination.partial" -Force -ErrorAction SilentlyContinue
    }
    throw "could not download $Destination from any mirror"
}

# --------------------------------------------------------- 1. interpreter
Step "embeddable Python $PythonVersion"
$TargetPython = Join-Path $Runtime 'python.exe'
if ($Force -or -not (Test-Path -LiteralPath $TargetPython)) {
    if (Test-Path -LiteralPath $Runtime) {
        # rmdir junctions first so a linked target is never recursively deleted.
        Get-ChildItem -LiteralPath $Runtime -Force | Where-Object { $_.LinkType } | ForEach-Object {
            & cmd.exe /c rmdir "$($_.FullName)" | Out-Null
        }
        Remove-Item -LiteralPath $Runtime -Recurse -Force
    }
    $Archive = Join-Path $Downloads "python-$PythonVersion-embed-amd64.zip"
    Get-FirstReachable @(
        "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip",
        "https://mirrors.huaweicloud.com/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip",
        "https://registry.npmmirror.com/-/binary/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip",
        "https://mirrors.aliyun.com/python-release/windows/python-$PythonVersion-embed-amd64.zip"
    ) $Archive
    New-Item -ItemType Directory -Force $Runtime | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Runtime -Force
    $Short = ($PythonVersion.Split('.')[0..1] -join '')
    $Pth = Join-Path $Runtime "python$Short._pth"
    # '..' is the kit root itself; the app imports both `app.*` and `vendor.*`.
    @("python$Short.zip", '.', 'Lib\site-packages', '..', 'import site') |
        Set-Content -LiteralPath $Pth -Encoding ascii
}
Write-Host "  $(& $TargetPython --version 2>&1)"

# ------------------------------------------------------------ 2. pip + uv
Step 'bootstrapping pip and uv'
$GetPip = Join-Path $Downloads 'get-pip.py'
Get-FirstReachable @('https://bootstrap.pypa.io/get-pip.py') $GetPip
& $TargetPython -X utf8 $GetPip --no-warn-script-location
Assert-LastExit 'get-pip bootstrap'
& $TargetPython -m pip install --upgrade --quiet uv
Assert-LastExit 'uv install'
& $TargetPython -m uv --version

function Invoke-Uv([string[]]$UvArgs) {
    & $TargetPython -m uv pip install --python $TargetPython --system --link-mode copy @UvArgs
    Assert-LastExit ("uv pip install " + ($UvArgs -join ' '))
}

# ------------------------------------------------- 3. ROCm runtime libraries
Step "ROCm runtime libraries ($Device) -- AMD TheRock"
Invoke-Uv @('--extra-index-url', "$RocmIndex/v4/whl/", '--pre', "rocm[libraries,device-$Device]")

# -------------------------------------------------- 4. ROCm torch/torchaudio
# torch and torchaudio must be the same full build string. uv resolves them
# independently and will happily pair e.g. torch 2.9.0+rocm7.10.0a20251117 with
# torchaudio 2.9.0+rocm7.13.0a20260416, which mismatches at the ABI level; and
# torch 2.9.0 (a 2025-11 build) needs `hipsparselt`, which is absent from both
# the rocm 7.10 SDK it pairs with and the whole v4 index
# ("Unknown rocm library 'hipsparselt'" at import time). Pinning one verified
# build for both avoids the whole class of problem.
Step "ROCm PyTorch $TorchVersion (matched torch + torchaudio)"
Invoke-Uv @('--index-url', "$RocmIndex/v2/gfx120X-all/", "torch==$TorchVersion", "torchaudio==$TorchVersion")

if (-not $SkipRequirements) {
    # ---------------------------------------------------- 5. the rest of the pins
    Step 'remaining pins from requirements-unified.lock.txt'
    $Lock = Join-Path $SourceRoot 'requirements-unified.lock.txt'
    if (-not (Test-Path -LiteralPath $Lock)) { throw "missing $Lock" }
    # The lock pins torch==2.10.0+cu128 and carries the cu128 --extra-index-url.
    # Dropping those lines keeps every other pin byte-identical to upstream's
    # verified set while leaving the ROCm wheels we just installed alone.
    $Filtered = Join-Path $Downloads 'requirements-unified.rocm.txt'
    Get-Content -LiteralPath $Lock | Where-Object {
        $line = $_.Trim()
        $line -ne '' -and $line -notlike '#*' -and
        $line -notlike '--extra-index-url*' -and
        $line -notmatch '^torch(audio|-stoi)?\s*=='
    } | Set-Content -LiteralPath $Filtered -Encoding utf8
    Write-Host "  $(($(Get-Content -LiteralPath $Filtered) | Measure-Object).Count) pins (torch lines removed)"
    Invoke-Uv @('-r', $Filtered)
}

# --------------------------------------------------------- 6. renderer
if (-not $SkipPlaywright) {
    Step 'playwright chromium (offline score renderer)'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $Runtime 'playwright'
    & $TargetPython -m playwright install --only-shell chromium
    Assert-LastExit 'playwright chromium install'
}

# --------------------------------------------------------- 7. verify
Step 'verifying the ROCm runtime'
& $TargetPython -X utf8 -c @"
import torch
assert torch.version.hip is not None, (
    'CUDA build detected: torch.version.hip is None. The ROCm wheel install '
    'failed and pip fell back to the PyPI CUDA build; re-run with -Force.')
assert torch.version.cuda is None, 'CUDA build detected; expected a HIP build'
print('torch       ', torch.__version__)
print('hip         ', torch.version.hip, '| cuda field:', torch.version.cuda)
assert torch.cuda.is_available(), 'no HIP device visible to torch'
assert torch.cuda.is_bf16_supported(), 'device does not report BF16 support'
print('device      ', torch.cuda.get_device_name(0))
print('capability  ', torch.cuda.get_device_capability(0))
print('OK: runtime is a working ROCm build')
"@
Assert-LastExit 'ROCm runtime verification (torch import / HIP)'

Write-Host "`nROCm runtime ready: $TargetPython" -ForegroundColor Green
Write-Host "Next: python scripts\rocm\apply_rocm_port.py ." -ForegroundColor Green
