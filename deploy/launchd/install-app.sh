#!/bin/zsh
# Installe (ou réinstalle) le cockpit comme service permanent du Mac : démarre à l'ouverture de session,
# redémarre tout seul s'il tombe. Puis expose-le sur le réseau Tailscale (deploy/tailscale/README.md).
#   zsh deploy/launchd/install-app.sh          # installe et démarre
#   zsh deploy/launchd/install-app.sh --remove # arrête et désinstalle
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.xrobitaille.mp-app"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$DEST"; echo "service $LABEL retiré"; exit 0
fi
[ -x "$REPO/.venv/bin/mp" ] || { echo "pas de .venv dans $REPO : faire l'installation d'abord (README)"; exit 1; }
"$REPO/.venv/bin/python" -c 'import fastapi, uvicorn' 2>/dev/null || "$REPO/.venv/bin/pip" install -q -e "$REPO"
mkdir -p "$REPO/out/logs" "$HOME/Library/LaunchAgents"
sed "s#__REPO__#$REPO#g" "$REPO/deploy/launchd/$LABEL.plist" > "$DEST"
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable "gui/$(id -u)/$LABEL"
sleep 2
if curl -fsS -o /dev/null http://127.0.0.1:8765/api/sante; then
  echo "cockpit démarré : http://127.0.0.1:8765 (journal : out/logs/app.log)"
else
  echo "le cockpit n'a pas répondu tout de suite : regarder out/logs/app.err.log"; exit 1
fi
