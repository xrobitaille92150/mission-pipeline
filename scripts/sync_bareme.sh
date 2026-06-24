#!/bin/zsh
# sync_bareme.sh — Synchronise le barème Excel vers GitHub (n8n le fetchera au prochain run)
# Usage : ./scripts/sync_bareme.sh
# Prérequis : openpyxl installé, git configuré avec remote origin

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXCEL="$HOME/Desktop/Claude/Projects/Candidatures/Scoring_Offres_XRO.xlsx"
OUTPUT="$REPO_DIR/nodes/scoring-bareme-prompt.txt"

echo "🔄 Lecture du barème Excel..."
cd "$REPO_DIR"

/opt/homebrew/bin/python3 -c "
import sys
sys.path.insert(0, 'scripts')
from parse_scoring_bareme import parse_bareme, format_bareme_for_claude

bareme = parse_bareme()
text = format_bareme_for_claude(bareme)
text += '''
SORTIE STRICTE — JSON pur, sans markdown, sans texte autour :
{\"score\": <entier 0-100>, \"justification\": \"<phrase max 25 mots>\", \"signaux\": [\"signal1\", ...max 5]}'''

with open('nodes/scoring-bareme-prompt.txt', 'w') as f:
    f.write(text)
print('✅ nodes/scoring-bareme-prompt.txt généré (' + str(len(text)) + ' chars)')
"

echo "📤 Push vers GitHub..."
git add nodes/scoring-bareme-prompt.txt
git commit -m "chore(bareme): sync depuis Excel $(date +%Y-%m-%d)" || echo "(aucun changement)"
git push origin main

echo "✅ Barème synchronisé — le workflow n8n utilisera la nouvelle version au prochain run (6h30)"
