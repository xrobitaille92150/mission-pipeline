#!/bin/bash
# Lanceur run.py — appelé par launchd com.xrobitaille.missionrun (06:15 / 18:15).
# Chemins absolus obligatoires (PATH launchd minimal). Log shell-level pour diagnostics.
LOGDIR="/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/pipeline/logs"
TS=$(date +%Y-%m-%d_%H%M%S)
LOG="$LOGDIR/pipeline_${TS}.log"
mkdir -p "$LOGDIR"
exec >> "$LOG" 2>&1
echo "[$(date '+%F %T')] run-pipeline.sh démarré (PID $$)"
/opt/homebrew/bin/python3 /Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/pipeline/service/run.py "$@"
RC=$?
echo "[$(date '+%F %T')] run.py terminé (exit $RC)"
exit $RC
