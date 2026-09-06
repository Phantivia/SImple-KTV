#Requires -Version 5.1
# No Pester dependency. This exercises real BAT -> Windows PowerShell -> mock
# docker.cmd calls. It does NOT claim Docker/GPU integration coverage.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Failures = @()
$Checks = @()
$OriginalPath = $env:PATH
$OriginalProgramFiles = $env:ProgramFiles
$OriginalLocalAppData = $env:LOCALAPPDATA
$OriginalDockerHost = $env:DOCKER_HOST
$OriginalDockerContext = $env:DOCKER_CONTEXT
$OriginalPause = $env:KTV_NO_PAUSE
$Temp = Join-Path ([IO.Path]::GetTempPath()) ('ktv-bat-' + [guid]::NewGuid().ToString('N'))
# Exercise spaces, non-ASCII, exclamation marks, ampersands and parentheses.
$Folder = 'Simple KTV ' + [char]0x6D4B + [char]0x8BD5 + ' ! & (preview)'
$Project = Join-Path $Temp $Folder
$Mock = Join-Path $Temp 'mock-bin'
$Records = Join-Path $Temp 'calls.jsonl'
New-Item -ItemType Directory -Path (Join-Path $Project 'scripts'), $Mock -Force | Out-Null
Get-ChildItem -LiteralPath $Root -Filter '*.bat' | Copy-Item -Destination $Project
foreach ($Name in @('start.ps1', 'stop.ps1', 'Dockerfile', 'compose.yaml', 'compose.gpu.yaml')) {
    Copy-Item -LiteralPath (Join-Path $Root $Name) -Destination $Project
}
Copy-Item -LiteralPath (Join-Path $Root 'scripts/windows-launcher.ps1') -Destination (Join-Path $Project 'scripts')

$MockScript = @'
$A = @($args)
ConvertTo-Json -InputObject $A -Compress | Add-Content -LiteralPath $env:KTV_TEST_CALLS -Encoding UTF8
$Line = $A -join ' '
$Scenario = $env:KTV_TEST_SCENARIO
if ($A[0] -eq 'context') {
    if ($Scenario -eq 'remote') { 'tcp://example.invalid:2376' } else { 'npipe:////./pipe/dockerDesktopLinuxEngine' }
    exit 0
}
if ($Line -eq 'compose version --short') {
    if ($Scenario -eq 'old-compose') { '2.29.0' }
    elseif ($Scenario -eq 'bad-compose') { 'unknown' }
    else { 'v2.30.0-desktop.1' }
    exit 0
}
if ($A[0] -eq 'info') {
    if ($Scenario -eq 'no-engine') { 'Docker Engine not running'; exit 1 }
    if ($Scenario -eq 'windows-containers') { 'windows' } else { 'linux' }
    exit 0
}
if ($A -contains 'up') {
    if ($Scenario -eq 'build-failure') { 'Mock build failed'; exit 33 }
    if ($Scenario -eq 'stderr-progress') { [Console]::Error.WriteLine('Mock build progress on stderr') }
    'Mock service healthy'; exit 0
}
if ($A -contains 'exec') {
    if ($Scenario -eq 'doctor-failure') { 'Mock CUDA diagnostic failed'; exit 18 }
    '{"ok":true,"test":"mock only"}'; exit 0
}
if ($A -contains 'stop') { 'Mock stopped'; exit 0 }
if ($A -contains 'logs') { 'Mock log entry'; exit 0 }
if ($A -contains 'ps') { 'Mock container status'; exit 0 }
'Unexpected docker arguments: ' + $Line
exit 99
'@
Set-Content -LiteralPath (Join-Path $Mock 'mock-docker.ps1') -Value $MockScript -Encoding ASCII
$MockBat = '@echo off' + "`r`n" + '"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0mock-docker.ps1" %*' + "`r`n" + 'exit /b %ERRORLEVEL%' + "`r`n"
[IO.File]::WriteAllText((Join-Path $Mock 'docker.cmd'), $MockBat, [Text.Encoding]::ASCII)

function Assert-True { param([bool]$Value, [string]$Message) if (-not $Value) { throw $Message } }
function Check {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Checks += $Name; Write-Host "PASS $Name" }
    catch { $script:Failures += "$Name : $_"; Write-Host "FAIL $Name : $_" }
}
function Run-Bat {
    param([string]$Name, [string[]]$Arguments = @(), [string]$Scenario = 'ok')
    $env:KTV_TEST_SCENARIO = $Scenario
    if (Test-Path -LiteralPath $Records) { Remove-Item -LiteralPath $Records }
    $Output = @(& (Join-Path $Project $Name) @Arguments 2>&1)
    $Code = $LASTEXITCODE
    $Calls = @()
    if (Test-Path -LiteralPath $Records) {
        $Calls = @(Get-Content -LiteralPath $Records | ForEach-Object { ,($_ | ConvertFrom-Json) })
    }
    return [pscustomobject]@{ Code = $Code; Calls = $Calls; Text = ($Output | Out-String); Flat = (($Calls | ForEach-Object { $_ -join ' ' }) -join "`n") }
}

try {
    $env:PATH = "$Mock;$OriginalPath"
    $env:ProgramFiles = Join-Path $Temp 'no-installed-desktop'
    $env:LOCALAPPDATA = Join-Path $Temp 'no-user-desktop'
    $env:DOCKER_HOST = $null
    $env:DOCKER_CONTEXT = $null
    $env:KTV_NO_PAUSE = '1'
    $env:KTV_TEST_CALLS = $Records
    Assert-True ((Get-Command docker).Source -eq (Join-Path $Mock 'docker.cmd')) 'Mock Docker must take priority; refusing to invoke a real daemon.'

    Check 'PowerShell syntax' {
        foreach ($File in Get-ChildItem -LiteralPath $Root -Filter '*.ps1' -Recurse) {
            $Tokens = $null; $ParseErrors = $null
            [Management.Automation.Language.Parser]::ParseFile($File.FullName, [ref]$Tokens, [ref]$ParseErrors) | Out-Null
            Assert-True ($ParseErrors.Count -eq 0) "$($File.FullName): $ParseErrors"
        }
    }
    Check 'GPU default, build, doctor, special-character path' {
        $R = Run-Bat 'start.bat' @('-NoBrowser')
        Assert-True ($R.Code -eq 0) $R.Text
        Assert-True ($R.Flat -match 'compose.gpu.yaml') 'GPU override missing'
        Assert-True ($R.Flat -match '--build' -and $R.Flat -match 'scripts/doctor.py') 'Build or doctor missing'
        Assert-True ($R.Text -match 'Ready: http://localhost:7860') 'Ready message missing'
    }
    Check 'CPU BAT does not request GPU' {
        $R = Run-Bat 'start-cpu.bat' @('-NoBrowser')
        Assert-True ($R.Code -eq 0) $R.Text
        Assert-True ($R.Flat -notmatch 'compose.gpu.yaml') 'CPU requested GPU'
        Assert-True ($R.Flat -match 'scripts/doctor.py') 'CPU skipped native diagnostics'
    }
    Check 'Forward CPU and NoBuild switches' {
        $R = Run-Bat 'start.bat' @('-CPU', '-NoBrowser', '-NoBuild')
        Assert-True ($R.Code -eq 0) $R.Text
        Assert-True ($R.Flat -notmatch 'compose.gpu.yaml' -and $R.Flat -match '--no-build') 'Switch forwarding failed'
    }
    Check 'Stop preserves volumes and does not recreate services' {
        $R = Run-Bat 'stop.bat'
        Assert-True ($R.Code -eq 0) $R.Text
        Assert-True ($R.Flat -match 'stop ktv' -and $R.Flat -notmatch '\b(down|rm|prune|up)\b') 'Unsafe stop command'
    }
    Check 'Finite log viewing' {
        $R = Run-Bat 'logs.bat' @('-NoFollow')
        Assert-True ($R.Code -eq 0) $R.Text
        Assert-True ($R.Flat -match 'logs --no-color --tail 200 ktv' -and $R.Flat -notmatch '--follow') 'Log switch forwarding failed'
    }
    Check 'Diagnose uses the running service' {
        $R = Run-Bat 'diagnose.bat'
        Assert-True ($R.Code -eq 0) $R.Text
        Assert-True ($R.Flat -match 'scripts/doctor.py' -and $R.Flat -notmatch '\bup\b') 'Diagnosis recreated the service'
    }
    Check 'Old Compose fails before build' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'old-compose'
        Assert-True ($R.Code -ne 0 -and $R.Flat -notmatch '\bup\b') 'Old Compose was accepted'
        Assert-True ($R.Text -match 'too old') $R.Text
    }
    Check 'Malformed Compose version fails safely' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'bad-compose'
        Assert-True ($R.Code -ne 0 -and $R.Flat -notmatch '\bup\b') 'Malformed Compose was accepted'
    }
    Check 'Windows-container mode fails before build' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'windows-containers'
        Assert-True ($R.Code -ne 0 -and $R.Text -match 'Windows-container mode') $R.Text
        Assert-True ($R.Flat -notmatch '\bup\b') 'Built in Windows-container mode'
    }
    Check 'Remote Docker context rejected before contacting daemon' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'remote'
        Assert-True ($R.Code -ne 0 -and $R.Flat -notmatch '\b(info|up)\b') 'Remote daemon was contacted'
    }
    Check 'Native stderr progress is not an error' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'stderr-progress'
        Assert-True ($R.Code -eq 0) $R.Text
        Assert-True ($R.Text -match 'progress on stderr') 'Progress was not streamed'
    }
    Check 'Build failure propagates and collects logs' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'build-failure'
        Assert-True ($R.Code -ne 0 -and $R.Text -match 'exit 33') $R.Text
        Assert-True ($R.Flat -match 'logs --no-color --tail 100 ktv' -and $R.Flat -notmatch 'scripts/doctor.py') 'Failure path incorrect'
    }
    Check 'Doctor failure is not reported as GPU ready' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'doctor-failure'
        Assert-True ($R.Code -ne 0 -and $R.Text -notmatch '\[3/3\] Ready') 'Failed doctor reported success'
        Assert-True ($R.Flat -notmatch '\b(stop|down)\b') 'Failure path stopped an existing service'
    }
    Check 'Stopped daemon fails with guidance' {
        $R = Run-Bat 'start.bat' @('-NoBrowser') 'no-engine'
        Assert-True ($R.Code -ne 0 -and $R.Text -match 'Docker Engine is not ready') $R.Text
        Assert-True ($R.Flat -notmatch '\bup\b') 'Built without a daemon'
    }
    Check 'Missing Docker fails clearly' {
        try {
            $env:PATH = Join-Path $Temp 'empty-path'
            $R = Run-Bat 'start.bat' @('-NoBrowser')
            Assert-True ($R.Code -ne 0 -and $R.Text -match 'Docker Desktop was not found') $R.Text
        } finally { $env:PATH = "$Mock;$OriginalPath" }
    }
    Check 'Launch transcripts are persisted' {
        Assert-True (@(Get-ChildItem -LiteralPath (Join-Path $Project 'logs') -Filter '*.log').Count -ge 10) 'Transcripts missing'
    }
} finally {
    $env:PATH = $OriginalPath
    $env:ProgramFiles = $OriginalProgramFiles
    $env:LOCALAPPDATA = $OriginalLocalAppData
    $env:DOCKER_HOST = $OriginalDockerHost
    $env:DOCKER_CONTEXT = $OriginalDockerContext
    $env:KTV_NO_PAUSE = $OriginalPause
    Remove-Item Env:KTV_TEST_SCENARIO, Env:KTV_TEST_CALLS -ErrorAction SilentlyContinue
    $ReportDir = Join-Path $Root 'reports'
    New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
    @{ runner = 'Windows PowerShell / mocked Docker'; passed = $Checks; failed = $Failures } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ReportDir 'windows-launcher.json') -Encoding UTF8
    Remove-Item -LiteralPath $Temp -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "$($Checks.Count) passed; $($Failures.Count) failed. Docker and GPU inference were not exercised."
if ($Failures.Count) { exit 1 }
exit 0
