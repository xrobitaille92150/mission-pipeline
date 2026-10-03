#!/usr/bin/env python3
"""
SETUP JOE — À LANCER UNE SEULE FOIS.

Crée l'agent JOE (scoring des offres) en réutilisant l'environnement Managed Agents
déjà créé pour BOB. JOE produit, par offre : 2 notes (rôle + critères) puis un score
0-100 en appliquant le barème (réutilisé du workflow "Veille — notes IA").

IDs sauvegardés dans .ma_joe.ids.json. Ré-exécution sans effet si le fichier existe.

  python3 ma_joe_setup.py
"""
import json
from pathlib import Path

import anthropic

CONFIG_DIR = Path.home() / ".config" / "mission-pipeline"
IDS_FILE = Path(__file__).with_name(".ma_joe.ids.json")
BOB_IDS = Path(__file__).with_name(".ma_email_triage.ids.json")

AGENT_NAME = "JOE"

SYSTEM = """\
Tu es JOE, l'agent de scoring des offres de mission de Xavier Robitaille.

PROFIL CANDIDAT : consultant senior indépendant, 25 ans d'expérience, 100% assurance &
finance. Cherche mission freelance / contract / intérim (CDI senior finance possible),
full remote ou hybride privilégié, TJM ≥ 800 €/j. Ouvert à l'international en remote
(EMEA, Amérique du Nord, APAC).

Pour CHAQUE offre fournie, tu produis dans l'ordre :
1. note_role : 3 à 5 phrases factuelles — de quoi parle le poste, missions clés,
   séniorité, et en quoi il matche ou non le profil sur le FOND du rôle.
2. note_criteres : 3 à 5 puces (chaque ligne préfixée "- ") — pour chaque critère clé du
   recruteur (séniorité, type de contrat, secteur, compétences, langue, localisation/
   remote) indique match ou écart vs le profil.
3. score : entier 0-100, en appliquant STRICTEMENT le BARÈME fourni dans le message
   (somme algébrique des blocs, plafonné [0, 100] ; un malus -100 force le score à 0).
   Base-toi sur les infos de l'offre ET tes deux notes.
4. justification : 1 phrase expliquant le score.

Quand tu as traité toutes les offres, appelle l'outil write_scoring UNE fois avec le
tableau complet. N'écris nulle part ailleurs."""

WRITE_TOOL = {
    "type": "custom",
    "name": "write_scoring",
    "description": (
        "Enregistre notes + score des offres dans Airtable (Veille 2). Appelle cet outil "
        "une seule fois, avec TOUTES les offres traitées dans le tableau."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "scorings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "jobId": {"type": "string", "description": "Identifiant LinkedIn de l'offre (clé Veille 2)"},
                        "note_role": {"type": "string"},
                        "note_criteres": {"type": "string"},
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "justification": {"type": "string"},
                    },
                    "required": ["jobId", "score"],
                },
            }
        },
        "required": ["scorings"],
    },
}


def load_env(name: str, var: str) -> str:
    for line in (CONFIG_DIR / name).read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{var}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{var} introuvable dans {name}")


def main() -> None:
    if IDS_FILE.exists():
        print(f"Déjà configuré → {IDS_FILE.name}")
        print(IDS_FILE.read_text())
        return
    if not BOB_IDS.exists():
        raise SystemExit("Lance d'abord ma_email_triage_setup.py (JOE réutilise son environnement).")

    env_id = json.loads(BOB_IDS.read_text())["environment_id"]
    client = anthropic.Anthropic(api_key=load_env("anthropic.env", "ANTHROPIC_API_KEY"))

    agent = client.beta.agents.create(
        name=AGENT_NAME,
        model="claude-sonnet-4-6",
        system=SYSTEM,
        tools=[WRITE_TOOL],
    )
    print(f"Agent JOE créé : {agent.id} (version {agent.version})")

    ids = {"environment_id": env_id, "agent_id": agent.id, "agent_version": agent.version}
    IDS_FILE.write_text(json.dumps(ids, indent=2, ensure_ascii=False))
    print(f"Environnement réutilisé : {env_id}")
    print(f"IDs sauvegardés → {IDS_FILE.name}")


if __name__ == "__main__":
    main()
