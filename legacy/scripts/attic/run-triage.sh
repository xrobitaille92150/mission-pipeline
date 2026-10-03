#!/bin/bash
# Lance le triage email (Managed Agent) en Gmail live.
# Appelé par launchd à 6h et 18h. PATH minimal de launchd → chemins absolus.

PROJECT_DIR="/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/pipeline"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

STAMP=$(/bin/date +%Y-%m-%d_%H%M%S)
cd "$PROJECT_DIR"

# Le script lit ses clés depuis ~/.config/mission-pipeline/*.env directement.
/opt/homebrew/bin/python3 scripts/ma_email_triage_run.py --live \
  > "$LOG_DIR/triage_${STAMP}.log" 2>&1
