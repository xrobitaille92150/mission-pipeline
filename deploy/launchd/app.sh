#!/bin/zsh
# Lanceur launchd du cockpit (`mp app`) : serveur web local sur 127.0.0.1:8765, maintenu en vie par launchd.
# Tailscale Serve l'expose en HTTPS sur le réseau privé (voir deploy/tailscale/README.md).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
export PATH="$HOME/.local/bin:$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:/Applications/LibreOffice.app/Contents/MacOS:$PATH"   # claude (Claude Code) et soffice
export TZ="Europe/Paris"
if [ ! -x .venv/bin/mp ]; then
  echo "pas de .venv : lancer d'abord l'installation (README § Installation)"; exit 1
fi
exec .venv/bin/mp app --host 127.0.0.1 --port "${MP_APP_PORT:-8765}"
