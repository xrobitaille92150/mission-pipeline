#!/bin/zsh
# Met en pause l'ancien pipeline (décision du 1er octobre 2026, précisée le 2 octobre : après l'audit de parité).
#   1. désactive les workflows n8n de l'ancien pipeline (noms « Gmail Bridge », « Veille », « Mission »,
#      « Candidature », « Dossier », « LinkedIn ») ; les autres workflows du VPS restent actifs ;
#   2. retire les agents launchd v2 du Mac (missionrun, jack, applytool…) : le fichier .plist est renommé
#      en .plist.disabled, rien n'est supprimé.
# Tout ce qui est arrêté est noté dans ~/.config/mission-pipeline/ancien-pipeline-arrete.txt, et
# `--restaurer` remet en service exactement ces éléments-là, et seulement eux.
#   zsh deploy/decommission.sh --liste      # montre ce qui serait arrêté, sans rien toucher
#   zsh deploy/decommission.sh              # met en pause
#   zsh deploy/decommission.sh --restaurer  # annule la pause
# Ne touche jamais aux services v3 (com.xrobitaille.mp, com.xrobitaille.mp-app) ni au cockpit LinkedIn
# (com.xavieradvisory.cockpit, hors du motif *xrobitaille*). Le VPS Hostinger se résilie à la main, après
# une semaine de v3 stable, et seulement si les autres workflows qu'il héberge ne servent plus.
set -euo pipefail
MODE="arret"
case "${1:-}" in
  --liste) MODE="liste" ;;
  --restaurer) MODE="restaurer" ;;
  "") ;;
  *) echo "option inconnue : $1 (--liste, --restaurer)"; exit 2 ;;
esac
CONF="$HOME/.config/mission-pipeline"
ENV="$CONF/n8n.env"
ETAT="$CONF/ancien-pipeline-arrete.txt"
UIDN="$(id -u)"

# --- accès n8n --------------------------------------------------------------------------------------------
BASE=""; KEY=""
if [ -f "$ENV" ]; then
  set -a; source "$ENV"; set +a
  # Les scripts v1/v2 lisaient N8N_URL ; la doc v2 parlait de N8N_BASEURL : on accepte les deux.
  BASE="${N8N_URL:-${N8N_BASEURL:-${N8N_BASE_URL:-}}}"; BASE="${BASE%/}"
  KEY="${N8N_API_KEY:-}"
fi
n8n_post() {   # n8n_post <id> <activate|deactivate>
  curl -sS -m 30 -o /dev/null -w "    HTTP %{http_code}\n" -X POST \
       -H "X-N8N-API-KEY: $KEY" -H "Authorization: Bearer $KEY" "$BASE/api/v1/workflows/$1/$2"
}

# --- restauration -----------------------------------------------------------------------------------------
if [ "$MODE" = "restaurer" ]; then
  if [ ! -f "$ETAT" ]; then echo "rien à restaurer : $ETAT absent"; exit 0; fi
  echo "Restauration de ce qui a été mis en pause (liste : $ETAT)"
  while IFS=$'\t' read -r kind id name; do
    case "$kind" in
      n8n)
        echo "  n8n : réactive $id  ($name)"
        if [ -n "$BASE" ] && [ -n "$KEY" ]; then n8n_post "$id" activate; else echo "    accès n8n absent : réactiver à la main dans l'interface"; fi ;;
      launchd)
        plist="$HOME/Library/LaunchAgents/$id.plist"
        if [ -f "$plist.disabled" ]; then
          mv "$plist.disabled" "$plist"
          launchctl bootstrap "gui/$UIDN" "$plist" 2>/dev/null || true
          launchctl enable "gui/$UIDN/$id" 2>/dev/null || true
          echo "  launchd : $id relancé"
        else
          echo "  launchd : $id introuvable ($plist.disabled)"
        fi ;;
    esac
  done < "$ETAT"
  mv "$ETAT" "$ETAT.restaure-$(date +%Y%m%d-%H%M)"
  echo "Terminé : l'ancien pipeline tourne de nouveau."
  exit 0
fi

# --- n8n --------------------------------------------------------------------------------------------------
[ "$MODE" = "arret" ] && mkdir -p "$CONF" && touch "$ETAT"
if [ ! -f "$ENV" ]; then
  echo "n8n.env absent ($ENV) : désactiver les workflows depuis l'interface n8n."
elif [ -z "$BASE" ] || [ -z "$KEY" ]; then
  echo "n8n : adresse (N8N_URL) ou clé (N8N_API_KEY) absente de $ENV"
  echo "  → désactiver les workflows à la main dans l'interface n8n (bouton Active de chaque workflow)."
else
  echo "n8n : $BASE"
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
      if [ "$MODE" = "liste" ]; then echo "  serait désactivé : $id  ($name)"; continue; fi
      echo "  désactive $id  ($name)"
      n8n_post "$id" deactivate
      printf 'n8n\t%s\t%s\n' "$id" "$name" >> "$ETAT"
    done
  fi
fi

# --- launchd ----------------------------------------------------------------------------------------------
echo "launchd :"
for plist in "$HOME"/Library/LaunchAgents/*xrobitaille*.plist(N); do
  base="$(basename "$plist")"
  case "$base" in
    com.xrobitaille.mp.plist|com.xrobitaille.mp-app.plist) continue ;;   # services v3 : pipeline et cockpit
  esac
  label="${base%.plist}"
  if [ "$MODE" = "liste" ]; then echo "  serait arrêté : $label"; continue; fi
  launchctl bootout "gui/$UIDN/$label" 2>/dev/null || true
  mv "$plist" "$plist.disabled"
  printf 'launchd\t%s\t\n' "$label" >> "$ETAT"
  echo "  $label arrêté → $base.disabled"
done

if [ "$MODE" = "arret" ]; then
  echo "Terminé. Pour tout remettre en service : zsh deploy/decommission.sh --restaurer"
fi
echo "Le VPS Hostinger héberge aussi les workflows n8n « laissés actifs » ci-dessus :"
echo "le résilier les arrêterait. Décider de leur sort avant de le résilier."
