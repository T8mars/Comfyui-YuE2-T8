param([switch]$NoBrowser)
$Script = Join-Path $PSScriptRoot 'scripts\start_local.ps1'
if ($NoBrowser) {
    & $Script -NoBrowser
} else {
    & $Script
}
exit $LASTEXITCODE
