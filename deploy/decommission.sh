#!/bin/zsh
# Arrête l'ancien pipeline (décision du 1er octobre 2026) :
#   1. désactive les workflows n8n de l'ancien pipeline (noms « Gmail Bridge », « Veille », « Mission »,
#      « Candidature », « Dossier », « LinkedIn ») ; les autres workflows du VPS restent actifs
#   2. retire les agents launchd v2 du Mac (com.xrobitaille.dossiers, purge horaire JACK, …)
# Le VPS Hostinger se résilie à la main, après une semaine de v3 stable, et seulement si les autres
# workflows qu'il héberge (automatisations hors pipeline, laissées actives) ne servent plus.
#   zsh deploy/decommission.sh            # arrête
#   zsh deploy/decommission.sh --liste    # montre ce qui serait arrêté, sans rien toucher
# Ne touche jamais aux services v3 (com.xrobitaille.mp, com.xrobitaille.mp-app) ni au cockpit LinkedIn
# (com.xavieradvisory.cockpit, hors du motif *xrobitaille*).
set -euo pipefail
LISTE=0; [ "${1:-}" = "--liste" ] && LISTE=1

ENV="$HOME/.config/mission-pipeline/n8n.env"
if [ -f "$ENV" ]; then
  set -a; source "$ENV"; set +a
  # Les scripts v1/v2 lisaient N8N_URL ; la doc v2 parlait de N8N_BASEURL : on accepte les deux.
  BASE="${N8N_URL:-${N8N_BASEURL:-${N8N_BASE_URL:-}}}"
  BASE="${BASE%/}"
  KEY="${N8N_API_KEY:-}"
  if [ -z "$BASE" ] || [ -z "$KEY" ]; then
    echo "n8n : adresse (N8N_URL) ou clé (N8N_API_KEY) absente de $ENV"
    echo "  → désactiver les workflows à la main dans l'interface n8n (bouton Active de chaque workflow)."
  else
    echo "n8n : $BASE"
    # API publique n8n v1 : en-tête X-N8N-API-KEY (le Bearer est ajouté au cas où l'instance est derrière un proxy d'auth).
    REP="$(curl -sS -m 30 -w '\n%{http_code}' -H "X-N8N-API-KEY: $KEY" -H "Authorization: Bearer $KEY" \
               "$BASE/api/v1/workflows?active=true" 2>&1 || true)"
    CODE="${REP##*$'\n'}"; CORPS="${REP%$'\n'*}"
    if [ "$CODE" != "200" ]; then
      echo "  n8n répond HTTP $CODE : clé refusée ou serveur injoignable ($(printf '%s' "$CORPS" | head -c 200))"
      echo "  → désactiver les workflows à la main dans l'interface n8n, ou renouveler la clé dans $ENV."
    else
      # printf et non echo : l'echo de zsh transforme les « \n » du JSON en vrais sauts de ligne.
      LISTE_WF="$(printf '%s' "$CORPS" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read(), strict=False)
except ValueError as e:
    sys.exit(f"réponse n8n illisible : {e}")
import re
# Seuls les workflows de l ancien pipeline sont visés ; le VPS héberge aussi d autres automatisations.
PIPELINE = re.compile(r"gmail bridge|veille|mission|candidat|dossier|linkedin", re.I)
for w in d.get("data", []):
    name = w.get("name", "").replace("\t", " ")
    print("P" if PIPELINE.search(name) else "A", w["id"], name, sep="\t")
')" || { echo "  → lecture impossible : désactiver les workflows à la main dans l'interface n8n."; LISTE_WF=""; }
      [ -z "$LISTE_WF" ] && echo "  aucun workflow actif (ou liste illisible, voir ci-dessus)"
      printf '%s\n' "$LISTE_WF" | while IFS=$'\t' read -r cible id name; do
        [ -z "$id" ] && continue
        if [ "$cible" != "P" ]; then echo "  laissé actif (hors pipeline) : $id  ($name)"; continue; fi
        if [ "$LISTE" = 1 ]; then echo "  serait désactivé : $id  ($name)"; continue; fi
        echo "  désactive $id  ($name)"
        curl -sS -m 30 -o /dev/null -w "    HTTP %{http_code}\n" -X POST \
             -H "X-N8N-API-KEY: $KEY" -H "Authorization: Bearer $KEY" \
             "$BASE/api/v1/workflows/$id/deactivate"
      done
    fi
  fi
else
  echo "n8n.env absent ($ENV) : désactiver les workflows depuis l'interface n8n."
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
echo "Terminé. Le VPS Hostinger héberge aussi les workflows n8n « laissés actifs » ci-dessus :"
echo "le résilier les arrêterait. Décider de leur sort avant de le résilier."
