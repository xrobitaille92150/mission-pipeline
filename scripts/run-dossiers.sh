#!/bin/bash
# Lance run_dossiers.py en mode non-interactif.
# Depuis le 03/07/2026 : appelé par service/run.py (launchd com.xrobitaille.missionrun, 06:15/18:15).

PROJECT_DIR="/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/pipeline"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Verrou anti-double-run (cron résiduel vs run.py) — lock périmé après 90 min
LOCKDIR="/tmp/run-dossiers.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    if [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +90 2>/dev/null)" ]; then
        rmdir "$LOCKDIR" 2>/dev/null && mkdir "$LOCKDIR" 2>/dev/null || exit 0
    else
        echo "run-dossiers déjà en cours (lock) — abandon" >> "$LOG_DIR/shell_lock_skips.log"
        exit 0
    fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

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
