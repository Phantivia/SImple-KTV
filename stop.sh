#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
docker compose -f compose.yaml stop
echo 'Stopped. Persistent data and model volumes have NOT been deleted.'
