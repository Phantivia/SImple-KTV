#Requires -Version 5.1
[CmdletBinding()]
param([switch]$Yes)
# Run only on the user's computer. Uses their existing gh login, never a pasted token.
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$Root = Split-Path -Parent $PSScriptRoot
$Repo = 'Phantivia/SImple-KTV'
$Checkout = $null
$Transcribing = $false
$Result = 0

function Run-Checked {
    param([string]$Command, [string[]]$Arguments)
    $Previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Command @Arguments 2>&1 | ForEach-Object { Write-Host "$_" }
        $Code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $Previous }
    if ($Code -ne 0) { throw "$Command failed (exit $Code). No force push will be attempted." }
}
function Read-Checked {
    param([string]$Command, [string[]]$Arguments)
    $Previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $Output = @(& $Command @Arguments 2>&1)
        $Code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $Previous }
    if ($Code -ne 0) { throw (($Output | Out-String).Trim()) }
    return (($Output | Out-String).Trim())
}

try {
    Set-Location -LiteralPath $Root
    foreach ($Tool in @('git', 'gh')) {
        if (-not (Get-Command $Tool -CommandType Application -ErrorAction SilentlyContinue)) {
            throw "Install $Tool first. Upload needs Git + GitHub CLI; starting the app does not. See docs/WINDOWS.md."
        }
    }
    $LogDir = Join-Path $Root 'logs'
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $LogFile = Join-Path $LogDir ("upload-{0}-{1}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID)
    Start-Transcript -LiteralPath $LogFile -Force | Out-Null
    $Transcribing = $true
    # No tokens are requested, echoed, stored in source, or put in the remote URL.
    Run-Checked 'gh' @('auth', 'status', '--hostname', 'github.com')
    $Metadata = (Read-Checked 'gh' @('api', "repos/$Repo")) | ConvertFrom-Json
    if ($Metadata.full_name -ine $Repo -or $Metadata.private -or -not $Metadata.permissions.push) {
        throw 'The target must be the existing public Phantivia/SImple-KTV repository with push permission.'
    }
    $Actor = (Read-Checked 'gh' @('api', 'user')) | ConvertFrom-Json
    $Branch = $Metadata.default_branch
    if (-not $Branch -or $Branch.StartsWith('-')) { throw 'The repository default branch is invalid.' }
    $Manifest = Join-Path $PSScriptRoot 'source-files.txt'
    $Paths = @(Get-Content -LiteralPath $Manifest | Where-Object { $_ -and -not $_.StartsWith('#') })
    if ($Paths.Count -lt 20) { throw 'The source manifest is missing or incomplete. Extract the entire release ZIP.' }
    foreach ($Relative in $Paths) {
        if ($Relative -match '(^/|\\|:|(^|/)\.\.(/|$)|(^|/)\.git(/|$))') { throw "Unsafe manifest path: $Relative" }
        if ($Relative -match '(^|/)(data|models|exports|backups|logs|node_modules|\.venv)(/|$)' -or
            $Relative -match '(?i)(\.(mp3|wav|flac|m4a|webm|ogg|opus|pth|pt|ckpt|onnx|safetensors|db|sqlite|pem|key|token)$|(^|/)\.env)') {
            throw "Private/runtime content must not be published: $Relative"
        }
        $Source = Get-Item -LiteralPath (Join-Path $Root $Relative)
        if ($Source.PSIsContainer -or ($Source.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Only regular source files may be uploaded: $Relative"
        }
    }
    $Checkout = Join-Path ([IO.Path]::GetTempPath()) ('simple-ktv-upload-' + [guid]::NewGuid().ToString('N'))
    # Per-command helper; do not change the user's global Git credential settings.
    $Auth = @('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential')
    Run-Checked 'git' ($Auth + @('clone', '--depth', '1', '--branch', $Branch, '--', "https://github.com/$Repo.git", $Checkout))
    foreach ($Relative in $Paths) {
        $Destination = Join-Path $Checkout $Relative
        $Parent = Split-Path -Parent $Destination
        New-Item -ItemType Directory -Path $Parent -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $Root $Relative) -Destination $Destination -Force
    }
    # Stage only the reviewed manifest paths, not arbitrary local songs or models.
    foreach ($Relative in $Paths) { Run-Checked 'git' @('-C', $Checkout, 'add', '--', $Relative) }
    $Changes = Read-Checked 'git' @('-C', $Checkout, 'diff', '--cached', '--name-only')
    if (-not $Changes) {
        Write-Host 'The repository already contains this source version. Nothing to upload.'
    } else {
        Run-Checked 'git' @('-C', $Checkout, 'diff', '--cached', '--stat')
        Write-Host ''
        Write-Host "Target: https://github.com/$Repo  branch: $Branch"
        Write-Host 'Matching source files will be updated. Unrelated repository files are retained.'
        Write-Host 'Recordings, model weights, databases, credentials and logs are excluded.'
        if (-not $Yes) {
            $Answer = Read-Host 'Publish these changes? Type YES to continue'
            if ($Answer -cne 'YES') { throw 'Upload cancelled before commit/push. The remote branch was not changed.' }
        }
        $Identity = @('-c', "user.name=$($Actor.login)", '-c', "user.email=$($Actor.id)+$($Actor.login)@users.noreply.github.com")
        Run-Checked 'git' ($Identity + @('-C', $Checkout, 'commit', '-m', 'feat: import Simple KTV with Windows BAT launchers'))
        # Normal fast-forward push: concurrent updates or branch protection fail safely.
        Run-Checked 'git' ($Auth + @('-C', $Checkout, 'push', 'origin', "HEAD:refs/heads/$Branch"))
        $Commit = Read-Checked 'git' @('-C', $Checkout, 'rev-parse', 'HEAD')
        Write-Host "Uploaded: https://github.com/$Repo/commit/$Commit"
    }
} catch {
    $Result = 1
    Write-Host "[FAILED] $($_.Exception.Message)"
    Write-Host 'Authenticate locally with: gh auth login --hostname github.com'
    Write-Host 'Then rerun upload.bat. Never paste a token into chat or source code.'
} finally {
    # Keep any checkout for inspection rather than deleting the user's recovery copy.
    if ($Checkout -and (Test-Path -LiteralPath $Checkout)) { Write-Host "Review/recovery checkout: $Checkout" }
    if ($Transcribing) { Stop-Transcript | Out-Null }
}
exit $Result
