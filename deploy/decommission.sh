#!/bin/zsh
# Arrête l'ancien pipeline (décision du 1er octobre 2026) :
#   1. désactive tous les workflows n8n encore actifs sur le VPS (ingestion 04:15 / 16:15 UTC comprise)
#   2. retire les agents launchd v2 du Mac (com.xrobitaille.dossiers, purge horaire JACK, …)
# Le VPS Hostinger lui-même se résilie à la main, après une semaine de v3 stable.
#   zsh deploy/decommission.sh            # arrête
#   zsh deploy/decommission.sh --liste    # montre ce qui serait arrêté, sans rien toucher
# Ne touche jamais aux services v3 (com.xrobitaille.mp, com.xrobitaille.mp-app) ni au cockpit LinkedIn
# (com.xavieradvisory.cockpit, hors du motif *xrobitaille*).
set -euo pipefail
LISTE=0; [ "${1:-}" = "--liste" ] && LISTE=1

ENV="$HOME/.config/mission-pipeline/n8n.env"
if [ -f "$ENV" ]; then
  set -a; source "$ENV"; set +a
  BASE="${N8N_BASEURL%/}"
  echo "n8n : $BASE"
  # API publique n8n v1 : en-tête X-N8N-API-KEY (le Bearer est ajouté au cas où l'instance est derrière un proxy d'auth).
  curl -sS -H "X-N8N-API-KEY: $N8N_API_KEY" -H "Authorization: Bearer $N8N_API_KEY" \
       "$BASE/api/v1/workflows?active=true" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(w["id"], w["name"]) for w in d.get("data", [])]' \
    | while read -r id name; do
        if [ "$LISTE" = 1 ]; then echo "  serait désactivé : $id  ($name)"; continue; fi
        echo "  désactive $id  ($name)"
        curl -sS -o /dev/null -w "    HTTP %{http_code}\n" -X POST \
             -H "X-N8N-API-KEY: $N8N_API_KEY" -H "Authorization: Bearer $N8N_API_KEY" \
             "$BASE/api/v1/workflows/$id/deactivate"
      done
  echo "  (si HTTP 401 : vérifier la clé dans $ENV ou désactiver depuis l'UI n8n)"
else
  echo "n8n.env absent ($ENV) : désactiver les workflows depuis l'UI n8n."
fi

echo "launchd :"
for plist in "$HOME"/Library/LaunchAgents/*xrobitaille*.plist(N); do
  base="$(basename "$plist")"
  case "$base" in
    com.xrobitaille.mp.plist|com.xrobitaille.mp-app.plist) continue ;;   # services v3 : pipeline et cockpit
  esac
  label="${base%.plist}"
  if [ "$LISTE" = 1 ]; then echo "  serait arrêté : $label"; continue; fi
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  mv "$plist" "$plist.disabled"
  echo "  $label arrêté → $base.disabled"
done
echo "Terminé. Reste à faire à la main : résilier le VPS Hostinger après une semaine de v3 stable."
