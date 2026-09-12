param([switch]$NoBrowser, [ValidateRange(1024,65535)][int]$Port = 8189, [switch]$NoSwitch)
& (Join-Path $PSScriptRoot 'start_local.ps1') @PSBoundParameters
exit $LASTEXITCODE
