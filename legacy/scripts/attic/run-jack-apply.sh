#!/bin/bash
# run-jack-apply.sh — Déclenche JACK auto-apply pour les offres Easy Apply Score≥35
# Appelé par launchd à 7h, après n8n (6h) et MATT (6h15).

LOG_DIR="$HOME/Desktop/Claude/Projects/Candidatures/pipeline/logs"
LOG="$LOG_DIR/jackrun_$(date +%Y-%m-%d_%H%M%S).log"
mkdir -p "$LOG_DIR"

exec >> "$LOG" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] JACK auto-apply démarré"

# Ouvrir Chrome s'il n'est pas en cours d'exécution
if ! pgrep -x "Google Chrome" > /dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ouverture de Chrome..."
    open -a "Google Chrome"
    sleep 8
fi

# Lancer JACK via Claude Code non-interactif
cd "$HOME/Desktop/Claude/Projects/Candidatures"

SKILL=$(cat .claude/skills/jack-apply/SKILL.md)
PROMPT="Tu es JACK. Exécute la procédure jack-apply complète telle que décrite ci-dessous. Lance-toi immédiatement sans attendre de confirmation — c'est un run automatique déclenché à 7h.

$SKILL"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Lancement claude..."
/Users/xavierrobitaille/.local/bin/claude -p "$PROMPT" --allowedTools "mcp__Claude_in_Chrome__*,mcp__1efbcac8-6973-473a-8b67-ed8c9049d15d__*" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] JACK terminé"
