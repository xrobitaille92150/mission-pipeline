#!/bin/zsh
# Lanceur launchd de Mission Pipeline v3 (Mac). Chemins absolus : launchd n'a pas de PATH utilisateur.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
export PATH="/opt/homebrew/bin:/usr/local/bin:/Applications/LibreOffice.app/Contents/MacOS:$PATH"
export TZ="Europe/Paris"
if [ ! -x .venv/bin/mp ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -e .
fi
HOUR=$(date +%H)
if [ "$HOUR" -lt 12 ]; then LABEL="matin"; else LABEL="soir"; fi
exec .venv/bin/mp run --label "$LABEL" "$@"
