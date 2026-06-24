#!/bin/bash
# Lance run_dossiers.py en mode non-interactif.
# Appelé par launchd à 6h, 12h et 19h.

PROJECT_DIR="/Users/xavierrobitaille/Claude/Artifacts/mission-pipeline"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Charger les clés API
source ~/.config/mission-pipeline/anthropic.env
export ANTHROPIC_API_KEY

# Le PAT Airtable est aussi dans ce fichier une fois ajouté
if [ -f ~/.config/mission-pipeline/airtable.env ]; then
    source ~/.config/mission-pipeline/airtable.env
    export AIRTABLE_PAT
fi

cd "$PROJECT_DIR"

/opt/homebrew/bin/python3 scripts/run_dossiers.py
