#!/usr/bin/env bash
# Explicitly publishes this project's reviewed source to a NEW PUBLIC repository.
# No token is accepted by this script; GitHub CLI manages your local authentication.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
owner=Phantivia
name="${1:-simple-ktv}"
[[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo 'Invalid repository name' >&2; exit 2; }
command -v git >/dev/null; command -v gh >/dev/null
login=$(gh api user --jq .login)
[[ "${login,,}" == "${owner,,}" ]] || { echo "Authenticated as $login, expected $owner. Run gh auth switch." >&2; exit 1; }
if gh repo view "$owner/$name" --json name >/dev/null 2>&1; then
  echo 'Repository already exists. Nothing was overwritten. Choose another name or push manually after review.' >&2; exit 1
fi
if [[ -e .git ]]; then
  echo 'This directory already has Git history. Review and publish it manually; this helper only publishes a fresh source bundle.' >&2; exit 1
fi
# Scan only the explicit allowlist; no blanket `git add .`.
paths=(backend frontend scripts docs .github README.md LICENSE THIRD_PARTY_NOTICES.md SECURITY.md CHANGELOG.md Dockerfile compose.yaml compose.gpu.yaml pyproject.toml .gitignore .dockerignore .gitattributes start.ps1 start.sh stop.ps1 stop.sh)
find backend frontend scripts docs .github -type f \( -iname '*.wav' -o -iname '*.mp3' -o -iname '*.ckpt' -o -iname '*.safetensors' -o -name '.env' -o -name '*.pem' \) -not -path '*/node_modules/*' -print | grep . && { echo 'Unexpected private assets in source directories; remove before publishing.' >&2; exit 1; }
echo "Publishing reviewed source to PUBLIC $owner/$name. Audio, model and data directories are excluded."
git init -b main
if ! git config user.name >/dev/null; then git config user.name "$login"; fi
if ! git config user.email >/dev/null; then
  id=$(gh api user --jq .id)
  git config user.email "${id}+${login}@users.noreply.github.com"
fi
git add -- "${paths[@]}"
git diff --cached --check
git commit -m 'feat: local-first Simple KTV pixel audio workstation'
gh repo create "$owner/$name" --public --description 'Local-first KTV workstation: RoFormer separation, recording, pitch editing and pixel-wave UI' --source . --remote origin --push
gh repo view "$owner/$name" --json url --jq .url
