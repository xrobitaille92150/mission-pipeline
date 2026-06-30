#!/bin/bash
# Lance run_dossiers.py en mode non-interactif.
# Appelé par launchd à 6h15, 12h00 et 19h00.

PROJECT_DIR="/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/pipeline"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Log shell-level vers un fichier dédié (debug launchd)
SHELL_LOG="$LOG_DIR/shell_$(date +%Y-%m-%d_%H%M%S).log"
exec >> "$SHELL_LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] run-dossiers.sh démarré (PID $$)"

# Charger les clés API — ne pas planter si le fichier manque
if [ -f ~/.config/mission-pipeline/anthropic.env ]; then
    source ~/.config/mission-pipeline/anthropic.env
    export ANTHROPIC_API_KEY
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] anthropic.env chargé"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: anthropic.env introuvable"
fi

if [ -f ~/.config/mission-pipeline/airtable.env ]; then
    source ~/.config/mission-pipeline/airtable.env
    export AIRTABLE_PAT
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] airtable.env chargé"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: airtable.env introuvable"
fi

cd "$PROJECT_DIR"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Lancement Python..."

/opt/homebrew/bin/python3 scripts/run_dossiers.py
EXIT=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Python terminé (exit $EXIT)"
exit $EXIT
