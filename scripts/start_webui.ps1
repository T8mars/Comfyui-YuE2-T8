param([switch]$NoBrowser)
& (Join-Path $PSScriptRoot 'start_local.ps1') @PSBoundParameters
exit $LASTEXITCODE
