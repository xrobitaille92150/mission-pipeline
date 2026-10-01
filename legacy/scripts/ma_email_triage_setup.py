#!/usr/bin/env python3
"""
SETUP — À LANCER UNE SEULE FOIS.

Crée (a) un environnement Managed Agents cloud et (b) l'agent `email-triage-missions`,
puis sauvegarde leurs IDs dans .ma_email_triage.ids.json. Le script de run lit ce fichier.

Ré-exécution : si le fichier d'IDs existe déjà, ne recrée rien (anti-doublon d'agents,
l'anti-pattern n°1 des Managed Agents). Supprime le fichier pour forcer une recréation.

  python3 ma_email_triage_setup.py
"""
import json
import os
from pathlib import Path

import anthropic

CONFIG_DIR = Path.home() / ".config" / "mission-pipeline"
IDS_FILE = Path(__file__).with_name(".ma_email_triage.ids.json")


def load_env(name: str, var: str) -> str:
    for line in (CONFIG_DIR / name).read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{var}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{var} introuvable dans {name}")


AGENT_NAME = "BOB"

SYSTEM = """\
Tu es BOB, l'agent de Xavier Robitaille. Ton rôle : récupérer et trier les emails reçus
liés à sa recherche de mission de conseil.

Pour CHAQUE email fourni, attribue EXACTEMENT UNE catégorie :
- "Proposition de mission" : un recruteur/client propose une mission ou un poste précis.
- "Accusé de réception"    : confirmation automatique qu'une candidature a bien été reçue.
- "Réponse positive"       : intérêt, demande d'entretien, suite favorable.
- "Refus"                  : candidature non retenue.
- "Autre/non-pertinent"    : newsletter, spam, sujet sans lien avec la recherche de mission.

RÈGLE TYPE — chaque email porte un champ `type` :
- `type` = "offre_linkedin" : offre d'emploi diffusée par une alerte LinkedIn.
  Catégorie OBLIGATOIRE "Proposition de mission". société = l'employeur, poste =
  l'intitulé (déjà fournis dans subject/body). Confiance "Haute".
- `type` = "email" : classe selon le contenu (règles de catégories ci-dessus).

Extrais la société et le poste quand ils sont identifiables (sinon laisse vide).
Donne une confiance : "Haute", "Moyenne" ou "Basse", et une justification d'une phrase.

Quand tu as classé tous les emails, appelle l'outil `write_classification` UNE fois
avec le tableau complet. N'écris nulle part ailleurs."""

WRITE_TOOL = {
    "type": "custom",
    "name": "write_classification",
    "description": (
        "Enregistre la classification des emails dans Airtable (table de test). "
        "Appelle cet outil une seule fois, avec TOUS les emails classés dans le tableau."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id_email": {"type": "string", "description": "Identifiant unique de l'email"},
                        "sujet": {"type": "string"},
                        "expediteur": {"type": "string"},
                        "categorie": {
                            "type": "string",
                            "enum": [
                                "Proposition de mission",
                                "Accusé de réception",
                                "Réponse positive",
                                "Refus",
                                "Autre/non-pertinent",
                            ],
                        },
                        "societe": {"type": "string"},
                        "poste": {"type": "string"},
                        "confiance": {"type": "string", "enum": ["Haute", "Moyenne", "Basse"]},
                        "justification": {"type": "string"},
                    },
                    "required": ["id_email", "categorie", "confiance"],
                },
            }
        },
        "required": ["classifications"],
    },
}


def main() -> None:
    if IDS_FILE.exists():
        ids = json.loads(IDS_FILE.read_text())
        print(f"Déjà configuré → {IDS_FILE.name}")
        print(json.dumps(ids, indent=2, ensure_ascii=False))
        print("Supprime ce fichier pour forcer une recréation.")
        return

    client = anthropic.Anthropic(api_key=load_env("anthropic.env", "ANTHROPIC_API_KEY"))

    env = client.beta.environments.create(
        name="ma-email-triage",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"Environnement créé : {env.id}")

    agent = client.beta.agents.create(
        name=AGENT_NAME,
        model="claude-haiku-4-5",
        system=SYSTEM,
        tools=[WRITE_TOOL],
    )
    print(f"Agent créé         : {agent.id} (version {agent.version})")

    ids = {"environment_id": env.id, "agent_id": agent.id, "agent_version": agent.version}
    IDS_FILE.write_text(json.dumps(ids, indent=2, ensure_ascii=False))
    print(f"IDs sauvegardés    → {IDS_FILE.name}")


if __name__ == "__main__":
    main()
