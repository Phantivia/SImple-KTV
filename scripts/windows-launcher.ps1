#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Logs', 'Diagnose')][string]$Action = 'Start',
    [switch]$CPU,
    [switch]$NoBrowser,
    [switch]$NoFollow,
    [switch]$NoBuild,
    [ValidateRange(10, 1800)][int]$WaitSeconds = 240
)

$ErrorActionPreference = 'Stop'
# PowerShell 7 may opt into throwing for native exit codes; handle them ourselves.
$PSNativeCommandUseErrorActionPreference = $false
$Root = Split-Path -Parent $PSScriptRoot
$script:DockerPath = $null
$script:Compose = @()
$Transcribing = $false
$Result = 0
$Url = 'http://localhost:7860'

function Get-DockerResult {
    param([string[]]$Arguments)
    $Previous = $ErrorActionPreference
    try {
        # PS 5.1 wraps native stderr as ErrorRecord. Do not treat progress as failure.
        $ErrorActionPreference = 'Continue'
        $Output = @(& $script:DockerPath @Arguments 2>&1)
        $Code = $LASTEXITCODE
        return [pscustomobject]@{ Code = $Code; Text = ($Output | Out-String).Trim() }
    } finally { $ErrorActionPreference = $Previous }
}

function Invoke-DockerChecked {
    param([string[]]$Arguments)
    $Previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        # Stream build output immediately and include it in the transcript.
        & $script:DockerPath @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        $Code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $Previous }
    if ($Code -ne 0) { throw "docker $($Arguments -join ' ') failed (exit $Code)." }
}

function Find-Docker {
    $Command = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Command) { return $Command.Source }
    $Candidates = @()
    if ($env:ProgramFiles) { $Candidates += Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe' }
    if ($env:LOCALAPPDATA) { $Candidates += Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe' }
    foreach ($Candidate in $Candidates) { if (Test-Path -LiteralPath $Candidate) { return $Candidate } }
    throw 'Docker Desktop was not found. Install it with the WSL2 backend, then run this BAT again. See docs/WINDOWS.md.'
}

function Assert-LocalDocker {
    if ($env:DOCKER_HOST -and -not $env:DOCKER_CONTEXT) {
        $Endpoint = $env:DOCKER_HOST
    } else {
        $Context = Get-DockerResult -Arguments @('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}')
        if ($Context.Code -ne 0) { throw "Cannot inspect the Docker context: $($Context.Text)" }
        $Endpoint = $Context.Text
    }
    if ($Endpoint -notmatch '^(npipe|unix)://') {
        throw 'A remote/TCP Docker endpoint is selected. Select a local Docker Desktop Linux context first. No files were sent to that daemon.'
    }
}

function Wait-DockerEngine {
    $Info = Get-DockerResult -Arguments @('info', '--format', '{{.OSType}}')
    if ($Info.Code -ne 0 -and $Action -eq 'Start') {
        $Candidates = @()
        if ($env:ProgramFiles) { $Candidates += Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe' }
        if ($env:LOCALAPPDATA) { $Candidates += Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe' }
        $Desktop = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($Desktop) {
            Write-Host '[Docker] Opening Docker Desktop. Complete any first-run prompts there.'
            Start-Process -FilePath $Desktop | Out-Null
            $Deadline = (Get-Date).AddSeconds($WaitSeconds)
            do {
                Start-Sleep -Seconds 3
                $Info = Get-DockerResult -Arguments @('info', '--format', '{{.OSType}}')
            } while ($Info.Code -ne 0 -and (Get-Date) -lt $Deadline)
        }
    }
    if ($Info.Code -ne 0) { throw "Docker Engine is not ready. Start Docker Desktop and enable WSL2. $($Info.Text)" }
    if ($Info.Text -ne 'linux') { throw 'Docker is in Windows-container mode. Switch Docker Desktop to Linux containers (WSL2 for GPU).' }
}

try {
    Set-Location -LiteralPath $Root
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'Dockerfile'))) {
        throw 'Project files are missing. Extract the entire ZIP to a writable local folder; do not run the BAT inside the ZIP.'
    }
    $LogDir = Join-Path $Root 'logs'
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $LogFile = Join-Path $LogDir ("{0}-{1}-{2}.log" -f $Action.ToLowerInvariant(), (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID)
    Start-Transcript -LiteralPath $LogFile -Force | Out-Null
    $Transcribing = $true
    Write-Host ''
    Write-Host '  SIMPLE / KTV  --  local audio studio'
    Write-Host "  Action: $Action   Log: $LogFile"
    Write-Host ''
    $script:DockerPath = Find-Docker
    Assert-LocalDocker
    $Version = Get-DockerResult -Arguments @('compose', 'version', '--short')
    if ($Version.Code -ne 0 -or $Version.Text -notmatch '(\d+\.\d+\.\d+)') {
        throw 'Docker Compose v2 is missing. Update Docker Desktop (Compose 2.30.0 or newer is required).'
    }
    if ([version]$Matches[1] -lt [version]'2.30.0') {
        throw "Compose $($Version.Text) is too old. Update Docker Desktop to include Compose 2.30.0 or newer."
    }
    Wait-DockerEngine
    $script:Compose = @('compose', '--project-name', 'simple-ktv', '--project-directory', $Root, '-f', (Join-Path $Root 'compose.yaml'))
    # Only START chooses an image. Stop/logs/diagnose always address the existing
    # service, whether it was started in GPU or CPU mode; they never recreate it.
    if ($Action -eq 'Start' -and -not $CPU) { $script:Compose += @('-f', (Join-Path $Root 'compose.gpu.yaml')) }
    $env:COMPOSE_ANSI = 'never'
    $env:BUILDKIT_PROGRESS = 'plain'
    switch ($Action) {
        'Start' {
            $Mode = if ($CPU) { 'CPU / DSP only' } else { 'NVIDIA GPU / local AI' }
            Write-Host "[1/3] $Mode. First build needs internet and substantial free disk space."
            Write-Host '      Existing Docker layers are reused; songs/models stay in persistent volumes.'
            $Up = @('up', '--detach', '--wait', '--wait-timeout', "$WaitSeconds")
            if ($NoBuild) { $Up += '--no-build' } else { $Up += '--build' }
            Invoke-DockerChecked -Arguments ($script:Compose + $Up)
            Write-Host '[2/3] Checking native audio libraries and, in GPU mode, real CUDA kernels...'
            Invoke-DockerChecked -Arguments ($script:Compose + @('exec', '-T', 'ktv', 'python', 'scripts/doctor.py'))
            Write-Host "[3/3] Ready: $Url"
            Write-Host '      stop.bat stops the app without deleting recordings or model caches.'
            if (-not $NoBrowser) {
                try { Start-Process $Url | Out-Null }
                catch { Write-Warning "The service is ready; open $Url manually. Browser launch failed: $_" }
            }
        }
        'Stop' {
            Invoke-DockerChecked -Arguments ($script:Compose + @('stop', 'ktv'))
            Write-Host 'Stopped. Projects, recordings, model caches and undo history are retained.'
        }
        'Logs' {
            Write-Host 'Close this log window (or press Ctrl+C) to stop viewing; the app keeps running.'
            $LogArgs = @('logs', '--no-color', '--tail', '200')
            if (-not $NoFollow) { $LogArgs += '--follow' }
            Invoke-DockerChecked -Arguments ($script:Compose + $LogArgs + @('ktv'))
        }
        'Diagnose' {
            Invoke-DockerChecked -Arguments ($script:Compose + @('ps'))
            Invoke-DockerChecked -Arguments ($script:Compose + @('exec', '-T', 'ktv', 'python', 'scripts/doctor.py'))
            Write-Host 'Diagnostics passed. Model quality and microphone timing still need hardware testing.'
        }
    }
} catch {
    $Result = 1
    Write-Host ''
    Write-Host "[FAILED] $($_.Exception.Message)"
    if ($script:DockerPath -and $script:Compose.Count -gt 0 -and $Action -eq 'Start') {
        Write-Host '--- Recent container logs (if the container was created) ---'
        try { Invoke-DockerChecked -Arguments ($script:Compose + @('logs', '--no-color', '--tail', '100', 'ktv')) } catch { Write-Host 'No container logs available.' }
    }
    Write-Host 'See docs/WINDOWS.md and the logs folder. Never use "down -v" to fix startup.'
    Write-Host 'The container may still be running. Use stop.bat to stop it safely.'
} finally {
    if ($Transcribing) { Stop-Transcript | Out-Null }
}
exit $Result
