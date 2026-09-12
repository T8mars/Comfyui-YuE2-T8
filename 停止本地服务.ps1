$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'scripts\stop_service.ps1')
exit $LASTEXITCODE
