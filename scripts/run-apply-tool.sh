#!/bin/bash
# Lance l'outil "Postuler proprement" (app web locale) et ouvre le navigateur.
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
/opt/homebrew/bin/python3 apply_tool.py &
SRV=$!
sleep 1.5
open "http://localhost:${PORT}"
echo "Outil lancé sur http://localhost:${PORT} (PID $SRV). Ctrl+C pour arrêter."
wait $SRV
