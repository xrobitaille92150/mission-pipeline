#!/bin/zsh
# Lanceur launchd de Mission Pipeline v3 (Mac). Chemins absolus : launchd n'a pas de PATH utilisateur.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
export PATH="/opt/homebrew/bin:/usr/local/bin:/Applications/LibreOffice.app/Contents/MacOS:$PATH"
export TZ="Europe/Paris"
if [ ! -x .venv/bin/mp ]; then
  # Python 3.11 minimum : le python3 par défaut du Mac peut être plus ancien.
  PY="$(command -v python3.12 || command -v python3.11 || command -v python3)"
  "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || { echo "Python ≥ 3.11 requis ($PY)"; exit 1; }
  "$PY" -m venv .venv
  .venv/bin/pip install -q -U pip
  .venv/bin/pip install -q -e .
fi
HOUR=$(date +%H)
if [ "$HOUR" -lt 12 ]; then LABEL="matin"; else LABEL="soir"; fi
exec .venv/bin/mp run --label "$LABEL" "$@"
