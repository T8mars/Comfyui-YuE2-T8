$ErrorActionPreference = 'Stop'
$KitRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $KitRoot 'runtime\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Runtime is not installed' }
$env:YUE2_HOME = $KitRoot
$env:PYTHONUTF8 = '1'
& $Python -X utf8 -m unittest discover -s (Join-Path $KitRoot 'tests') -v
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }
$Doctor = Join-Path $KitRoot 'outputs\jobs\verify-doctor'
New-Item -ItemType Directory -Force $Doctor | Out-Null
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $Doctor 'job.json'), (@{id='verify-doctor';kind='doctor';request=@{verify_hashes=$true}} | ConvertTo-Json -Depth 5), $Utf8)
[System.IO.File]::WriteAllText((Join-Path $Doctor 'status.json'), (@{id='verify-doctor';kind='doctor';status='queued';stage='queued'} | ConvertTo-Json), $Utf8)
& $Python -X utf8 -m app.yue2_app.core_worker --root $KitRoot --job-dir $Doctor
if ($LASTEXITCODE -ne 0) { throw 'Model and CUDA doctor failed' }
Write-Host 'Verification passed' -ForegroundColor Green
