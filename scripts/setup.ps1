param([switch]$SkipRenderer, [switch]$Force, [string]$ModelsDirectory = '')
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'setup_unified.ps1') @PSBoundParameters
