#!/bin/bash
# run-jack-hourly.sh — JACK horaire : purge J'écarte + génération CV/CL (service/jack.py)
# Appelé par launchd com.xrobitaille.jack toutes les heures à :45.
# Remplace l'ancien run-jack-apply.sh (Easy Apply Chrome, supprimé le 28/07/2026).

PIPELINE="/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/pipeline"
LOGDIR="$PIPELINE/logs"
mkdir -p "$LOGDIR"
exec >> "$LOGDIR/jack_$(date +%Y-%m-%d).log" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] JACK horaire démarré (PID $$)"
/opt/homebrew/bin/python3 "$PIPELINE/service/jack.py" "$@"
EXIT=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] JACK horaire terminé (exit $EXIT)"
exit $EXIT
