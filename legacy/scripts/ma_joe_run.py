#!/usr/bin/env python3
"""
RUN JOE — orchestrateur du Managed Agent JOE (scoring des offres).

Séquence (réintègre le notes+scoring de "Veille — notes IA" dans le flux BOB) :
1. Lit les offres LinkedIn triées par BOB (Email Triage, Catégorie="Proposition de mission",
   ID Email préfixé "li-").
2. Upsert dans Veille 2 par jobId (anti-doublon ; on ne re-traite pas une offre déjà scorée).
3. JOE (Sonnet) produit 2 notes (rôle + critères) + un score 0-100 (barème GitHub).
4. Outil custom write_scoring (host) écrit Note rôle / Note critères / Score dans Veille 2.

  python3 ma_joe_run.py

Pré-requis : ma_joe_setup.py lancé une fois ; BOB a peuplé Email Triage.
"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import anthropic

CONFIG_DIR = Path.home() / ".config" / "mission-pipeline"
IDS_FILE = Path(__file__).with_name(".ma_joe.ids.json")

BASE = "apphTpnW5vu0OdnfC"
EMAIL_TRIAGE = "tblOkwh1UtFHQpyct"
VEILLE2 = "tblrCyL6huHkUPZbF"
BAREME_URL = "https://raw.githubusercontent.com/xrobitaille92150/mission-pipeline/main/nodes/scoring-bareme-prompt.txt"


def load_env(name: str, var: str) -> str:
    for line in (CONFIG_DIR / name).read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{var}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{var} introuvable dans {name}")


def at_list(table: str, pat: str, fields: list[str]) -> list[dict]:
    """Liste tous les records d'une table (pagination)."""
    out, offset = [], None
    while True:
        params = [("fields[]", f) for f in fields] + [("pageSize", "100")]
        if offset:
            params.append(("offset", offset))
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(
            f"https://api.airtable.com/v0/{BASE}/{table}?{qs}",
            headers={"Authorization": f"Bearer {pat}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        out.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            return out


def at_write(table: str, pat: str, records: list[dict], method: str) -> int:
    """POST (create) ou PATCH (update) par lots de 10. records = [{fields:..} | {id, fields:..}]."""
    n = 0
    for i in range(0, len(records), 10):
        body = json.dumps({"records": records[i : i + 10], "typecast": True}).encode()
        req = urllib.request.Request(
            f"https://api.airtable.com/v0/{BASE}/{table}",
            data=body, method=method,
            headers={"Authorization": f"Bearer {pat}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            n += len(json.loads(resp.read())["records"])
    return n


def main() -> None:
    if not IDS_FILE.exists():
        sys.exit("Lance d'abord : python3 ma_joe_setup.py")
    ids = json.loads(IDS_FILE.read_text())
    pat = load_env("airtable.env", "AIRTABLE_PAT")
    client = anthropic.Anthropic(api_key=load_env("anthropic.env", "ANTHROPIC_API_KEY"))

    # 1. Offres LinkedIn triées par BOB
    et = at_list(EMAIL_TRIAGE, pat, ["ID Email", "Catégorie", "Poste", "Société", "Expéditeur", "Lieu", "URL", "Mode"])
    offers = {}
    for rec in et:
        f = rec.get("fields", {})
        idv = f.get("ID Email", "")
        if f.get("Catégorie") == "Proposition de mission" and idv.startswith("li-"):
            jobid = idv[3:]
            offers[jobid] = {  # dédup intra-table par jobId
                "jobId": jobid,
                "poste": f.get("Poste") or "",
                "employeur": f.get("Société") or f.get("Expéditeur") or "",
                "lieu": f.get("Lieu") or "",
                "mode": f.get("Mode") or "",
                "url": f.get("URL") or "",
            }
    print(f"Email Triage : {len(offers)} offre(s) LinkedIn (Proposition de mission).")
    if not offers:
        print("Aucune offre à traiter. Fin.")
        return

    # 2. Index Veille 2 par jobId (recordId + déjà scorée ?)
    v2 = at_list(VEILLE2, pat, ["jobId", "Score"])
    by_job = {}
    for rec in v2:
        f = rec.get("fields", {})
        j = f.get("jobId")
        if j:
            by_job[j] = {"id": rec["id"], "scored": f.get("Score") is not None}

    # Upsert : créer les offres absentes de Veille 2
    to_create = [
        {"fields": {"jobId": o["jobId"], "Employeur": o["employeur"], "Poste": o["poste"],
                    "Lieu": o["lieu"], "Mode": o["mode"], "URL": o["url"]}}
        for jid, o in offers.items() if jid not in by_job
    ]
    if to_create:
        created = at_write(VEILLE2, pat, to_create, "POST")
        print(f"Veille 2 : {created} offre(s) créée(s).")
        # rafraîchir l'index (pour récupérer les recordId des nouvelles)
        v2 = at_list(VEILLE2, pat, ["jobId", "Score"])
        by_job = {f.get("jobId"): {"id": r["id"], "scored": f.get("Score") is not None}
                  for r in v2 for f in [r.get("fields", {})] if f.get("jobId")}

    # 3. Offres à scorer = présentes dans Veille 2 mais sans Score
    to_score = [o for jid, o in offers.items() if jid in by_job and not by_job[jid]["scored"]]
    already = len(offers) - len(to_score)
    if already:
        print(f"Anti-doublon : {already} offre(s) déjà scorée(s) ignorée(s).")
    if not to_score:
        print("Aucune nouvelle offre à scorer. Fin.")
        return

    # Barème live (même source que le n8n "Fetch barème")
    bareme = urllib.request.urlopen(BAREME_URL, timeout=30).read().decode()

    session = client.beta.sessions.create(
        agent={"type": "agent", "id": ids["agent_id"], "version": ids["agent_version"]},
        environment_id=ids["environment_id"],
        title="JOE — scoring des offres",
    )
    print(f"Session : {session.id}")
    print(f"Suivre : https://platform.claude.com/workspaces/default/sessions/{session.id}\n")
    print(f"{len(to_score)} offre(s) envoyée(s) à JOE pour notation.")

    kickoff = (
        "Applique le BARÈME suivant pour scorer chaque offre, après avoir rédigé les 2 notes.\n\n"
        "=== BARÈME ===\n" + bareme +
        "\n\n=== OFFRES À TRAITER (JSON) ===\n" + json.dumps(to_score, ensure_ascii=False, indent=2) +
        "\n\nAppelle write_scoring UNE fois avec le tableau complet (jobId, note_role, "
        "note_criteres, score, justification)."
    )

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
            elif t == "agent.custom_tool_use" and ev.name == "write_scoring":
                # 4. Écriture host-side dans Veille 2 (PATCH par jobId)
                patch = []
                for s in ev.input["scorings"]:
                    rid = by_job.get(s.get("jobId", ""), {}).get("id")
                    if not rid:
                        continue
                    patch.append({"id": rid, "fields": {
                        "Note rôle": s.get("note_role", ""),
                        "Note critères": s.get("note_criteres", ""),
                        "Score": s.get("score"),
                    }})
                done = at_write(VEILLE2, pat, patch, "PATCH") if patch else 0
                msg = f"{done} offre(s) scorée(s) écrite(s) dans Veille 2."
                print(f"\n[host] {msg}")
                client.beta.sessions.events.send(
                    session_id=session.id,
                    events=[{"type": "user.custom_tool_result", "custom_tool_use_id": ev.id,
                             "content": [{"type": "text", "text": msg}]}],
                )
            elif t == "session.error":
                print(f"\n[erreur] {getattr(ev, 'error', ev)}")
            elif t == "session.status_terminated":
                break
            elif t == "session.status_idle":
                if getattr(ev.stop_reason, "type", None) == "requires_action":
                    continue
                break

    print(f"\nVeille 2 : https://airtable.com/{BASE}/{VEILLE2}")


if __name__ == "__main__":
    main()
