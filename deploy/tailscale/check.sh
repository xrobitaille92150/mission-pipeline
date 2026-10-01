#!/bin/zsh
# Diagnostic du cockpit en une commande : service, page locale, Tailscale, adresse publiée, erreurs récentes.
#   zsh deploy/tailscale/check.sh
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.xrobitaille.mp-app"
TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"

echo "== 1. Service du cockpit sur le Mac"
if launchctl print "gui/$(id -u)/$LABEL" >/tmp/mp-app-launchd.txt 2>/dev/null; then
  grep -E "^\s*(state|pid|last exit code) =" /tmp/mp-app-launchd.txt | sed 's/^\s*/   /'
else
  echo "   ABSENT : le service n'est pas installé → zsh deploy/launchd/install-app.sh"
fi

echo "== 2. Cockpit en local (127.0.0.1:8765)"
code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/)
echo "   page : HTTP $code (attendu : 200)"
code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/icon.png)
echo "   icône : HTTP $code (attendu : 200)"
echo "   santé : $(curl -s -m 30 http://127.0.0.1:8765/api/sante | head -c 400)"

echo "== 3. Tailscale"
if [ -x "$TS" ] || command -v tailscale >/dev/null; then
  "$TS" status 2>&1 | head -n 4 | sed 's/^/   /'
  echo "   -- publication (tailscale serve status) :"
  "$TS" serve status 2>&1 | sed 's/^/   /'
  STATUS="$("$TS" serve status 2>/dev/null)"
  URL=$(echo "$STATUS" | grep -o 'https://[^ ]*:8443' | head -n 1)
  if [ -n "$URL" ]; then
    code=$(curl -s -m 30 -o /dev/null -w "%{http_code}" "$URL/")
    echo "   Missions : $URL → HTTP $code (attendu : 200)"
  else
    echo "   Missions n'est pas publié sur 8443 → tailscale serve --bg --https=8443 8765"
  fi
  # L'adresse sans port (HTTPS 443) appartient au cockpit LinkedIn (port local 8766).
  if echo "$STATUS" | awk '/:8443/{p=0} /https:\/\/[^ ]*ts\.net( |$)/{p=1} p' | grep -q '127.0.0.1:8765'; then
    echo "   ATTENTION : l'adresse sans port pointe vers Missions et masque le cockpit LinkedIn → tailscale serve --bg 8766"
  fi
else
  echo "   Tailscale introuvable sur ce Mac"
fi

echo "== 4. Dernières erreurs du cockpit (out/logs/app.err.log)"
tail -n 15 "$REPO/out/logs/app.err.log" 2>/dev/null | sed 's/^/   /' || echo "   (aucun journal)"
