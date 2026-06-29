#!/usr/bin/env python3
"""
RUN — orchestrateur du Managed Agent `email-triage-missions`.

À chaque exécution : récupère les emails (échantillon OU Gmail live via le pont n8n),
crée une session, classe en conversationnel, exécute l'outil custom `write_classification`
côté host (écriture Airtable avec TON PAT — aucun secret dans le sandbox).

  python3 ma_email_triage_run.py            # emails de email_samples.json
  python3 ma_email_triage_run.py mes.json   # autre fichier d'emails
  python3 ma_email_triage_run.py --live      # Gmail live (24h) via le pont n8n

Mode --live : appelle le workflow n8n "Gmail Bridge" qui réutilise verbatim tes nœuds
"Gmail — Emails de la veille" + "Filtrer & préparer" du Run quotidien. Credentials du pont
dans ~/.config/mission-pipeline/gmail-bridge.env.

Pré-requis : lancer ma_email_triage_setup.py une fois d'abord.
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import anthropic

CONFIG_DIR = Path.home() / ".config" / "mission-pipeline"
IDS_FILE = Path(__file__).with_name(".ma_email_triage.ids.json")
DEFAULT_SAMPLES = Path(__file__).with_name("email_samples.json")

AIRTABLE_BASE = "apphTpnW5vu0OdnfC"
AIRTABLE_TABLE = "tblOkwh1UtFHQpyct"  # Email Triage (test MA)

# NOTE : kickoff CONVERSATIONNEL (user.message), pas un Outcome.
# Un Outcome note ce qui est dans le sandbox ; ici le livrable est un effet de bord
# côté host (écriture Airtable via write_classification), invisible du grader → il
# boucle en needs_revision indéfiniment. Forme conversationnelle = classe une fois,
# appelle l'outil une fois, terminé.


def load_env(name: str, var: str) -> str:
    for line in (CONFIG_DIR / name).read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{var}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{var} introuvable dans {name}")


def fetch_live_emails() -> list[dict]:
    """Gmail live via le pont n8n (réutilise Filtrer & préparer du Run quotidien)."""
    url = load_env("gmail-bridge.env", "GMAIL_BRIDGE_URL")
    token = load_env("gmail-bridge.env", "GMAIL_BRIDGE_TOKEN")
    req = urllib.request.Request(url, headers={"X-Bridge-Token": token})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def existing_email_ids(pat: str) -> set[str]:
    """ID Email déjà présents dans la table (anti-doublon entre runs)."""
    ids: set[str] = set()
    offset = None
    while True:
        qs = urllib.parse.urlencode(
            [("fields[]", "ID Email"), ("pageSize", "100")] + ([("offset", offset)] if offset else [])
        )
        req = urllib.request.Request(
            f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}?{qs}",
            headers={"Authorization": f"Bearer {pat}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        for rec in data.get("records", []):
            val = rec.get("fields", {}).get("ID Email")
            if val:
                ids.add(val)
        offset = data.get("offset")
        if not offset:
            return ids


def write_to_airtable(classifications: list[dict], pat: str, email_by_id: dict) -> str:
    """Outil custom exécuté CÔTÉ HOST : écrit les classifications dans Airtable.

    Sujet/Expéditeur sont renseignés depuis la copie host des emails (jointure par
    id_email) — source autoritaire, pas dépendante de ce que l'agent ré-écho.
    """
    now = datetime.now(timezone.utc).isoformat()
    records = []
    for c in classifications:
        src = email_by_id.get(c.get("id_email", ""), {})
        fields = {
            "ID Email": c.get("id_email", ""),
            "Sujet": src.get("subject") or c.get("sujet", ""),
            "Expéditeur": src.get("from") or c.get("expediteur", ""),
            "Catégorie": c.get("categorie", ""),
            "Société": c.get("societe", ""),
            "Poste": c.get("poste", ""),
            "Confiance": c.get("confiance", ""),
            "Justification": c.get("justification", ""),
            "Date traitement": now,
        }
        # Offres LinkedIn : on stocke les champs structurés (pour le scoring par JOE).
        if src.get("type") == "offre_linkedin":
            fields["Lieu"] = src.get("lieu", "")
            fields["URL"] = src.get("url", "")
            fields["Mode"] = src.get("mode", "")
        records.append({"fields": fields})
    written = 0
    # Airtable accepte 10 records par POST
    for i in range(0, len(records), 10):
        batch = records[i : i + 10]
        body = json.dumps({"records": batch, "typecast": True}).encode()
        req = urllib.request.Request(
            f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {pat}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            written += len(json.loads(resp.read())["records"])
    return f"{written} email(s) écrit(s) dans Airtable."


def main() -> None:
    if not IDS_FILE.exists():
        sys.exit("Lance d'abord : python3 ma_email_triage_setup.py")
    ids = json.loads(IDS_FILE.read_text())

    if "--live" in sys.argv[1:]:
        emails = fetch_live_emails()
        print(f"Gmail live : {len(emails)} email(s) récupéré(s) sur 24h via le pont n8n.")
    else:
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        samples_path = Path(args[0]) if args else DEFAULT_SAMPLES
        if not samples_path.exists():
            sys.exit(f"Fichier d'emails introuvable : {samples_path}")
        emails = json.loads(samples_path.read_text())
    if not emails:
        sys.exit("Aucun email à classer.")

    pat = load_env("airtable.env", "AIRTABLE_PAT")
    client = anthropic.Anthropic(api_key=load_env("anthropic.env", "ANTHROPIC_API_KEY"))

    # Anti-doublon : on écarte les emails déjà en table avant de les envoyer à l'agent.
    seen = existing_email_ids(pat)
    before = len(emails)
    emails = [e for e in emails if e.get("id_email") and e["id_email"] not in seen]
    skipped = before - len(emails)
    if skipped:
        print(f"Anti-doublon : {skipped} email(s) déjà classé(s) ignoré(s).")
    if not emails:
        print("Aucun nouvel email à classer. Fin.")
        return

    session = client.beta.sessions.create(
        agent={"type": "agent", "id": ids["agent_id"], "version": ids["agent_version"]},
        environment_id=ids["environment_id"],
        title="BOB — triage des emails reçus",
    )
    print(f"Session : {session.id}")
    print(f"Suivre en direct : https://platform.claude.com/workspaces/default/sessions/{session.id}\n")

    email_by_id = {e.get("id_email", ""): e for e in emails}

    kickoff = (
        "Classe les emails suivants selon tes instructions, puis appelle write_classification "
        "UNE fois avec le tableau complet.\n\nEMAILS (JSON) :\n"
        + json.dumps(emails, ensure_ascii=False, indent=2)
    )

    # Stream-first : on ouvre le flux AVANT d'envoyer le kickoff.
    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": kickoff}]}],
        )

        for ev in stream:
            t = ev.type

            if t == "agent.message":
                for block in ev.content:
                    if getattr(block, "type", None) == "text":
                        print(block.text, end="", flush=True)

            elif t == "agent.custom_tool_use" and ev.name == "write_classification":
                result = write_to_airtable(ev.input["classifications"], pat, email_by_id)
                print(f"\n[host] {result}")
                client.beta.sessions.events.send(
                    session_id=session.id,
                    events=[
                        {
                            "type": "user.custom_tool_result",
                            "custom_tool_use_id": ev.id,
                            "content": [{"type": "text", "text": result}],
                        }
                    ],
                )

            elif t == "session.error":
                print(f"\n[erreur] {getattr(ev, 'error', ev)}")

            elif t == "session.status_terminated":
                break

            elif t == "session.status_idle":
                # Gate terminal : on attend si l'agent réclame une action (résultat d'outil),
                # sinon (end_turn) l'agent a fini → on sort.
                if getattr(ev.stop_reason, "type", None) == "requires_action":
                    continue
                break

    print(f"\nRésultat dans Airtable : "
          f"https://airtable.com/{AIRTABLE_BASE}/{AIRTABLE_TABLE}")


if __name__ == "__main__":
    main()
