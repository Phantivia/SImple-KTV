#Requires -Version 5.1
& (Join-Path $PSScriptRoot 'scripts/windows-launcher.ps1') -Action Stop
exit $LASTEXITCODE
