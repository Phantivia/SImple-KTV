#Requires -Version 5.1
[CmdletBinding()]
param([switch]$CPU, [switch]$NoBrowser, [switch]$NoBuild, [ValidateRange(10, 1800)][int]$WaitSeconds = 240)
& (Join-Path $PSScriptRoot 'scripts/windows-launcher.ps1') -Action Start -CPU:$CPU -NoBrowser:$NoBrowser -NoBuild:$NoBuild -WaitSeconds $WaitSeconds
exit $LASTEXITCODE
