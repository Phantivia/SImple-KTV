#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
compose=(docker compose -f compose.yaml)
case "${1:-}" in
  --cpu) ;;
  "") compose+=(-f compose.gpu.yaml) ;;
  *) echo "Usage: bash start.sh [--cpu]" >&2; exit 2;;
esac
command -v docker >/dev/null || { echo 'Install and start Docker first.' >&2; exit 1; }
docker info >/dev/null
"${compose[@]}" up --build -d --wait --wait-timeout 180
"${compose[@]}" exec -T ktv python scripts/doctor.py
printf '\nSimple KTV: http://localhost:7860\n'
