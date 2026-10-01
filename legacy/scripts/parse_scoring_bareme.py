#!/usr/bin/env python3
"""
Parser du barème de scoring Candidatures (Excel → JSON).
Lit le fichier Excel et extrait les 7 blocs de critères.
Sortie : JSON structuré pour utilisation dans n8n / prompts Claude.

Usage:
  python3 scripts/parse_scoring_bareme.py > bareme.json
"""

import openpyxl
import json
import re
from pathlib import Path

# Chemin du barème Excel (toujours en référence au fichier source)
BAREME_PATH = Path.home() / "Desktop" / "Claude" / "Projects" / "Candidatures" / "Scoring_Offres_XRO.xlsx"


def parse_bareme():
    """Parse le fichier Excel et retourne une structure de blocs."""

    if not BAREME_PATH.exists():
        raise FileNotFoundError(f"Barème introuvable : {BAREME_PATH}")

    wb = openpyxl.load_workbook(BAREME_PATH)
    ws = wb['Barème']

    rows = list(ws.iter_rows(values_only=True))

    bareme = {
        "titre": "Barème de scoring des offres — Xavier Robitaille",
        "règles": "Score = somme algébrique des 7 blocs • plafonné [0, 100] • seuil actionnable ≥ 50",
        "seuil_actionnable": 50,
        "blocs": []
    }

    current_bloc = None

    for row in rows:
        if not row or not any(cell is not None for cell in row):
            continue

        text = str(row[0] or "").strip() if row[0] else ""

        # Détecte un nouveau bloc (commence par "BLOC")
        if text.startswith("BLOC"):
            if current_bloc:
                bareme["blocs"].append(current_bloc)

            # Parse le header du bloc : "BLOC N — Titre (range)"
            match = re.match(r".*BLOC (\d+).*?—\s*(.+?)\s*\(([^)]+)\)", text)
            if match:
                num, titre, range_str = match.groups()
                current_bloc = {
                    "numero": int(num),
                    "titre": titre.strip(),
                    "range": range_str.strip(),
                    "criteres": []
                }

        # Détecte un critère dans un bloc (indentation + Points + Nature)
        elif current_bloc and len(row) >= 4 and row[1] and row[2] is not None:
            criterion_name = str(row[1]).strip()
            points_str = str(row[2]).strip()
            nature = str(row[3]).strip() if row[3] else ""
            keywords = str(row[4]).strip() if row[4] else ""

            # Extracte la valeur numérique (peut être "40", "+4", "-5", "-100", etc.)
            try:
                points = int(points_str.replace("+", ""))
            except ValueError:
                continue

            current_bloc["criteres"].append({
                "nom": criterion_name,
                "points": points,
                "nature": nature,
                "mots_cles": keywords if keywords else None
            })

    # Ajoute le dernier bloc
    if current_bloc:
        bareme["blocs"].append(current_bloc)

    return bareme


def format_bareme_for_claude(bareme):
    """Formate le barème pour utilisation dans un prompt Claude."""

    lines = [
        f"# {bareme['titre']}",
        "",
        f"**Règles** : {bareme['règles']}",
        f"**Seuil actionnable** : ≥ {bareme['seuil_actionnable']}/100",
        ""
    ]

    for bloc in bareme['blocs']:
        lines.append(f"## {bloc['titre']}")
        lines.append(f"*Range: {bloc['range']}*")
        lines.append("")

        for crit in bloc['criteres']:
            lines.append(f"- **{crit['nom']}** : {crit['points']:+d} pts ({crit['nature']})")
            if crit['mots_cles']:
                lines.append(f"  - Mots-clés : {crit['mots_cles']}")

        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    bareme = parse_bareme()

    # Sortie JSON (pour n8n / scripts)
    print(json.dumps(bareme, ensure_ascii=False, indent=2))
