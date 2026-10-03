#!/usr/bin/env python3
"""
Script de test du système de scoring.
Charge des offres Veille depuis Airtable, les score, affiche les résultats.

Usage:
  python3 scripts/test_scoring.py [mode] [nombre_offres]

Modes:
  test      - Teste avec 2 offres exemples (défaut)
  live      - Fetch les offres réelles Airtable Veille

Exemples:
  python3 scripts/test_scoring.py          # Mode test
  python3 scripts/test_scoring.py test 2   # Mode test (2 offres)
  python3 scripts/test_scoring.py live 5   # Mode live (5 premières offres)
"""

import sys
import json
import os
from pathlib import Path
import subprocess
import requests

try:
    from anthropic import Anthropic
except ImportError:
    print("❌ Lib anthropic requise : pip install anthropic")
    sys.exit(1)

# Chemins
BAREME_PATH = Path.home() / "Desktop" / "Claude" / "Projects" / "Candidatures" / "Scoring_Offres_XRO.xlsx"
PROMPT_PATH = Path(__file__).parent.parent / "nodes" / "scoring-prompt.md"
PARSER_PATH = Path(__file__).parent / "parse_scoring_bareme.py"

# Airtable
def load_env_var(env_file, var_name):
    """Charge une variable depuis un fichier .env"""
    try:
        with open(env_file, 'r') as f:
            for line in f:
                if line.startswith(var_name + "="):
                    return line.split("=", 1)[1].strip()
    except:
        pass
    return os.getenv(var_name)

AIRTABLE_PAT = load_env_var(
    Path.home() / ".config/mission-pipeline/airtable.env",
    "AIRTABLE_PAT"
)
BASE_ID = "apphTpnW5vu0OdnfC"
VEILLE_TABLE = "tblrXH5Jiyg6w21lW"

# Anthropic
ANTHROPIC_API_KEY = load_env_var(
    Path.home() / ".config/mission-pipeline/anthropic.env",
    "ANTHROPIC_API_KEY"
)


def load_bareme():
    """Charge le barème via le parser."""
    result = subprocess.run(
        ["python3", str(PARSER_PATH)],
        capture_output=True,
        text=True,
        cwd=str(PARSER_PATH.parent.parent)
    )
    if result.returncode != 0:
        raise RuntimeError(f"Parser error: {result.stderr}")
    return json.loads(result.stdout)


def format_bareme_for_claude(bareme):
    """Formate le barème pour Claude."""
    lines = []
    lines.append(f"# {bareme['titre']}")
    lines.append("")
    lines.append(f"**Règles** : {bareme['règles']}")
    lines.append(f"**Seuil actionnable** : ≥ {bareme['seuil_actionnable']}/100")
    lines.append("")

    for bloc in bareme['blocs']:
        lines.append(f"## BLOC {bloc['numero']} — {bloc['titre']}")
        lines.append(f"*Range: {bloc['range']}*")
        lines.append("")

        for crit in bloc['criteres']:
            points_str = f"+{crit['points']}" if crit['points'] > 0 else str(crit['points'])
            lines.append(f"- **{crit['nom']}** : {points_str} pts ({crit['nature']})")
            if crit['mots_cles']:
                lines.append(f"  - Mots-clés : {crit['mots_cles']}")

        lines.append("")

    return "\n".join(lines)


def fetch_veille_offres(limit=5):
    """Fetch les offres Veille depuis Airtable (Haute/Moyenne sans score)."""
    if not AIRTABLE_PAT:
        raise ValueError("AIRTABLE_PAT non défini")

    url = f"https://api.airtable.com/v0/{BASE_ID}/{VEILLE_TABLE}"

    # Filtre : Pertinence in (Haute, Moyenne) AND Score is empty
    params = {
        "maxRecords": limit,
        "filterByFormula": "{Pertinence} != 'Hors-cible'",
        "view": "Grid view"
    }

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {AIRTABLE_PAT}"},
        params=params,
        timeout=10
    )
    response.raise_for_status()

    records = response.json().get("records", [])
    offres = []

    for record in records:
        fields = record.get("fields", {})

        # Note: le champ Description provient du workflow Veille — notes IA
        # Si absent, on peut utiliser le titre + employeur + lieu comme fallback
        description = fields.get("Description") or ""

        offres.append({
            "id": record["id"],
            "poste": fields.get("Poste", "N/A"),
            "employeur": fields.get("Employeur", "N/A"),
            "lieu": fields.get("Lieu", "N/A"),
            "modalites": fields.get("Modalités", "Non spécifié"),
            "description": description,
            "pertinence": fields.get("Pertinence", "?"),
            "note_role": fields.get("Note rôle"),
            "note_criteres": fields.get("Note critères")
        })

    return offres


def score_offre(offre, bareme, bareme_text):
    """Appelle Claude Sonnet pour scorer une offre."""

    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY non défini")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    # Charge le prompt
    with open(PROMPT_PATH, 'r') as f:
        prompt = f.read()

    # Remplace les placeholders
    prompt = prompt.replace("{BAREME}", bareme_text)
    prompt = prompt.replace("{JOB_TITLE}", offre['poste'])
    prompt = prompt.replace("{COMPANY}", offre['employeur'])
    prompt = prompt.replace("{LOCATION}", offre['lieu'])
    prompt = prompt.replace("{WORK_MODE}", offre['modalites'])
    prompt = prompt.replace("{LANGUAGE}", "Non spécifié")
    prompt = prompt.replace("{JOB_DESCRIPTION}", offre['description'][:6000])  # Limiter la taille

    # Appelle Claude (Sonnet pour scoring detaillé)
    response = client.messages.create(
        model="claude-opus-4-1",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    # Parse la réponse
    text = response.content[0].text

    # Extrait le JSON
    json_match = None
    for part in text.split('\n'):
        if part.strip().startswith('{'):
            try:
                json_match = json.loads(part)
                break
            except:
                continue

    if not json_match:
        # Essayer de trouver un bloc JSON complet
        import re
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            json_match = json.loads(match.group())

    if not json_match:
        raise ValueError(f"No JSON found in response: {text[:200]}")

    return json_match


def print_result(offre, score_result):
    """Affiche le résultat de manière lisible."""
    print("\n" + "="*80)
    print(f"📌 {offre['poste']} @ {offre['employeur']}")
    print(f"   Lieu: {offre['lieu']} | Mode: {offre['modalites']}")
    print(f"   Pertinence IA: {offre['pertinence']}")
    print("="*80)

    score = score_result.get('score_final', '?')
    seuil = score_result.get('seuil_atteint', False)

    # Couleur pour le score (simple)
    status = "✅ ACTIONNABLE" if seuil else "⚠️ MARGINAL" if score >= 40 else "❌ BAS"
    print(f"🎯 SCORE: {score}/100 — {status}")

    if score_result.get('resume'):
        print(f"   {score_result['resume']}")

    # Détail par bloc
    print("\n📊 Détail par bloc :")
    justif = score_result.get('justification', {})
    for bloc_name in ['bloc_1_cluster', 'bloc_2_outils', 'bloc_3_seniorie', 'bloc_4_mode_travail',
                      'bloc_5_geo_langue', 'bloc_6_structure', 'bloc_7_red_flags']:
        if bloc_name in justif:
            bloc = justif[bloc_name]
            points_str = f"+{bloc['points']}" if bloc['points'] > 0 else str(bloc['points'])
            print(f"   {bloc_name.replace('bloc_', 'BLOC ').replace('_', ' ')}: {points_str:>4} — {bloc['raison']}")

    print()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    print("🚀 Test du système de scoring Veille")
    print(f"   Chargement du barème...")

    bareme = load_bareme()
    bareme_text = format_bareme_for_claude(bareme)

    if mode == "live":
        print(f"   Chargement des offres Airtable Veille...")
        offres = fetch_veille_offres(limit)

        if not offres:
            print("❌ Aucune offre trouvée (Haute/Moyenne avec JD)")
            sys.exit(1)
    else:
        # Mode test avec exemples
        print(f"   Mode TEST (exemples hardcodés)")
        offres = [
            {
                "id": "test1",
                "poste": "Senior Investment Accounting Manager",
                "employeur": "AXA Investment Managers",
                "lieu": "Paris",
                "modalites": "Full remote",
                "description": """Leading IFRS 9 & IFRS 17 implementation on SimCorp platform.
                Fund Accounting responsibilities. Technical provisions management.
                Senior role (10+ years). English & French required.
                Investment accounting team of 5 people.
                Competitive salary. Remote-first culture.""",
                "pertinence": "Haute",
                "note_role": None,
                "note_criteres": None
            },
            {
                "id": "test2",
                "poste": "Junior Analyst",
                "employeur": "BNP Paribas Retail Banking",
                "lieu": "Lyon (on-site)",
                "modalites": "Présentiel obligatoire",
                "description": """Entry-level position for graduates and junior analysts.
                Basic financial reporting and analysis. Data entry. Excel skills required.
                No remote work. Paris or Lyon office mandatory. German language required.
                Contract to start immediately.""",
                "pertinence": "Hors-cible",
                "note_role": None,
                "note_criteres": None
            }
        ]
        print(f"   {len(offres)} offres test chargées\n")

    print(f"   {len(offres)} offres chargées\n")

    for i, offre in enumerate(offres, 1):
        print(f"[{i}/{len(offres)}] Scoring {offre['employeur']}...")

        try:
            result = score_offre(offre, bareme, bareme_text)
            print_result(offre, result)
        except Exception as e:
            print(f"   ❌ Erreur: {e}\n")


if __name__ == "__main__":
    main()
