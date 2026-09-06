param([ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')][string]$Name = 'simple-ktv')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$Owner = 'Phantivia'
foreach ($Command in @('git','gh')) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { throw "Install $Command and authenticate GitHub CLI with gh auth login first." }
}
$Login = & gh api user --jq .login
if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI authentication failed. Run gh auth login.' }
$Login = $Login.Trim()
if ($Login -ine $Owner) { throw "Authenticated as $Login, expected $Owner. Run gh auth switch." }
& gh repo view "$Owner/$Name" --json name *> $null
if ($LASTEXITCODE -eq 0) { throw 'Repository already exists. Nothing was overwritten. Use a different name or publish manually after review.' }
if (Test-Path '.git') { throw 'This folder already has Git history. Review and publish manually; this helper requires a fresh source bundle.' }
$Paths = @('backend','frontend','scripts','docs','.github','README.md','LICENSE','THIRD_PARTY_NOTICES.md','SECURITY.md','CHANGELOG.md','Dockerfile','compose.yaml','compose.gpu.yaml','pyproject.toml','.gitignore','.dockerignore','.gitattributes','start.ps1','start.sh','stop.ps1','stop.sh')
$Unexpected = Get-ChildItem backend,frontend,scripts,docs,.github -Recurse -File -Force | Where-Object {
    $_.FullName -notmatch '[\\/]node_modules[\\/]' -and ($_.Extension -in @('.wav','.mp3','.ckpt','.safetensors','.pem') -or $_.Name -eq '.env')
}
if ($Unexpected) { throw 'Unexpected audio/model/secret files in source directories. Remove them before publishing.' }
function Run-Git { & git @args; if ($LASTEXITCODE -ne 0) { throw "git failed: $args" } }
Write-Host "Publishing reviewed source to PUBLIC $Owner/$Name. User audio/data/models are excluded."
Run-Git init -b main
$GitName = & git config user.name
if (-not $GitName) { Run-Git config user.name $Login }
$GitEmail = & git config user.email
if (-not $GitEmail) {
    $Id = & gh api user --jq .id
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve local commit identity.' }
    Run-Git config user.email "$($Id.Trim())+$($Login.Trim())@users.noreply.github.com"
}
Run-Git add -- @Paths
Run-Git diff --cached --check
Run-Git commit -m 'feat: local-first Simple KTV pixel audio workstation'
& gh repo create "$Owner/$Name" --public --description 'Local-first KTV workstation: RoFormer separation, recording, pitch editing and pixel-wave UI' --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw 'Publishing failed. Your local commit is retained. Inspect gh output; no force push was attempted.' }
& gh repo view "$Owner/$Name" --json url --jq .url
