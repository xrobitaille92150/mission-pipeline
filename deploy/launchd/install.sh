#!/bin/zsh
# Installe (ou réinstalle) l'agent launchd : 06:30 et 18:30 tous les jours, sur ce clone du dépôt.
#   zsh deploy/launchd/install.sh          # installe
#   zsh deploy/launchd/install.sh --remove # désinstalle
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.xrobitaille.mp"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$DEST"; echo "agent $LABEL retiré"; exit 0
fi
mkdir -p "$REPO/out/logs" "$HOME/Library/LaunchAgents"
sed "s#__REPO__#$REPO#g" "$REPO/deploy/launchd/$LABEL.plist" > "$DEST"
launchctl bootstrap "gui/$(id -u)" "$DEST"
launchctl enable "gui/$(id -u)/$LABEL"
echo "agent $LABEL installé → $DEST (06:30 et 18:30). Test immédiat : launchctl kickstart gui/$(id -u)/$LABEL"
