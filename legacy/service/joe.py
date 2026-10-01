#!/usr/bin/env python3
"""
JOE — enrichissement des offres Veille 2 (notes Sonnet + score Haiku).
Port déterministe des nœuds n8n (Description LinkedIn / Notes / Prépa prompt scoring / Parser score).
Barème + règle langue lus depuis nodes/scoring-bareme-prompt.txt (source unique).

Modes :
  python3 joe.py --test [N]        # recompute le score de N offres DÉJÀ scorées, compare, N'ÉCRIT PAS
  python3 joe.py --dry-run         # enrichit les offres sans note, N'ÉCRIT PAS
  python3 joe.py --write           # enrichit les offres sans note ET écrit dans Veille 2
  python3 joe.py ingest            # pont Gmail → cartes offre_linkedin, N'ÉCRIT PAS (dry-run)
  python3 joe.py ingest --write    # idem + create des jobId absents de Veille 2 (J'écarte préservé)
  python3 joe.py all --write       # ingest + enrichissement (séquence run.py)
"""
import datetime
import json
import re
import sys
import time
import urllib.request
import urllib.error

from lib import config as C
from lib import claude, airtable, gmail
from lib.net import urlopen_retry

JD_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


# ---------- Fetch + extraction description LinkedIn (port de "Extraire desc") ----------
def clean_mode(raw: str) -> str:
    """Normalise vers les valeurs Airtable : À distance / Hybride / Sur site."""
    m = (raw or "").lower()
    if re.search(r"à\s*distance|\ba\s*distance\b|remote|télétravail|teletravail|full\s*remote", m):
        return "À distance"
    if re.search(r"hybride|hybrid", m):
        return "Hybride"
    if re.search(r"sur\s*(?:site|place)|on[-\s]?site|présentiel|presentiel", m):
        return "Sur site"
    return ""


def fetch_jd(job_id: str) -> dict:
    empty = {"ok": False, "descText": "", "criteres": "", "modeFromJd": "", "easyApply": False}
    if not job_id:
        return empty
    try:
        req = urllib.request.Request(JD_URL.format(job_id), headers={"User-Agent": "Mozilla/5.0"})
        with urlopen_retry(req, timeout=30) as r:
            html = r.read().decode(errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return empty   # dégradé, jamais d'abandon

    def clean(s):
        s = re.sub(r"<[^>]+>", " ", s)
        s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&#39;", "'").replace("&rsquo;", "'").replace("&quot;", '"'))
        return re.sub(r"\s+", " ", s).strip()

    desc = ""
    i = html.find("show-more-less-html__markup")
    if i >= 0:
        chunk = re.sub(r"show-more-less-html__markup[^>]*>", "", html[i:i + 14000], count=1)
        cut = re.search(r"show-more-less__button|</section>", chunk)
        if cut:
            chunk = chunk[:cut.start()]
        desc = clean(chunk)
    crit = []
    for m in re.finditer(r"job-criteria-subheader[^>]*>([\s\S]*?)</h3>[\s\S]*?job-criteria-text[^>]*>([\s\S]*?)</span>", html):
        crit.append(clean(m.group(1)) + ": " + clean(m.group(2)))
    # Mode depuis les critères JD (ex. "Télétravail: À distance") — port d'extraire-desc.js
    mode_jd = ""
    for c in crit:
        if re.search(r"télétravail|remote|workplace|travail.à.distance", c, re.I):
            parts = c.split(":")
            if len(parts) > 1:
                mode_jd = clean_mode(":".join(parts[1:]))
            break
    easy = bool(re.search(r"candidature simplifiée|easy apply", html, re.I))
    return {"ok": len(desc) > 200, "descText": desc, "criteres": " | ".join(crit),
            "modeFromJd": mode_jd, "easyApply": easy}


# ---------- Notes Sonnet (port de "Prépa prompt notes" + "Parser notes") ----------
def notes_prompt(poste, employeur, lieu, criteres, desc):
    return (f"{C.PROFIL}\n\nVoici une offre d'emploi. Redige DEUX notes courtes en francais, "
            f"factuelles, pour aider ce candidat a decider de postuler.\n\n"
            f"OFFRE : {poste} — {employeur} — {lieu or 'lieu n.c.'}\n"
            f"CRITERES LINKEDIN : {criteres or 'n.c.'}\n"
            f"DESCRIPTION :\n{(desc or '')[:6000]}\n\n"
            'Reponds UNIQUEMENT avec un objet JSON, sans texte autour :\n'
            '{"note_role":"3 a 5 phrases : missions cles, seniorite, match ou non sur le FOND",'
            '"note_criteres":"puces \'- \' : seniorite, contrat, secteur, competences, langue, localisation/remote — match ou ecart vs profil",'
            '"mode":"A distance | Hybride | Sur site — ou null"}')


def parse_notes(txt):
    m = re.search(r"\{[\s\S]*\}", txt or "")
    if not m:
        return "", "", ""
    try:
        o = json.loads(m.group(0))
        return o.get("note_role", ""), o.get("note_criteres", ""), o.get("mode") or ""
    except json.JSONDecodeError:
        return "", "", ""


# ---------- Score Haiku (port de "Prépa prompt scoring" + "Parser score") ----------
def score_prompt(bareme, poste, employeur, lieu, note_role, note_crit, desc):
    note_part = (f"\n\nAnalyse Sonnet du rôle :\n{note_role or ''}\n\nCritères recruteur :\n{note_crit or ''}"
                 if (note_role or note_crit) else "(Pas d'analyse Sonnet disponible)")
    desc_part = (f"\nDescription (extrait, max 4000 c.) :\n{desc[:4000]}"
                 if (desc and len(desc) > 100)
                 else "\n(Fiche de poste non disponible — score basé sur titre + employeur + lieu)")
    return (bareme + "\n\nOFFRE A EVALUER :\n"
            f"Poste : {poste or '(non renseigné)'}\n"
            f"Employeur : {employeur or '(non renseigné)'}\n"
            f"Lieu : {lieu or '(non renseigné)'}"
            + note_part + desc_part)


def parse_score(txt):
    m = re.search(r"\{[\s\S]*\}", txt or "")
    if not m:
        return None
    try:
        o = json.loads(m.group(0))
        s = o.get("score")
        return int(s) if s is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


# ---------- Ingestion (Étape 1b — pont Gmail → upsert Veille 2) ----------
def ingest(write: bool = False) -> dict:
    """Pont Gmail → cartes offre_linkedin → create des jobId absents de Veille 2.
    Create-only : les records existants ne sont jamais modifiés (J'écarte, Score,
    notes… préservés). Retourne les stats (fail-loud : lève sur erreur pont/Airtable)."""
    emails = gmail.fetch_bridge()
    cards, fstats = gmail.offres(emails)
    existing = {str(r["fields"].get("jobId", "")) for r in
                airtable.list_records(C.T_VEILLE2, fields=["jobId"])}
    new = [c for c in cards if c["jobId"] not in existing]
    today = datetime.date.today().isoformat()
    stats = {"emails": len(emails), "cartes": len(cards), "junk": fstats["junk"],
             "deja_connues": len(cards) - len(new), "nouvelles": len(new), "creees": 0}
    print(f"INGEST{'' if write else ' (dry-run)'} — {stats['emails']} emails → {stats['cartes']} cartes "
          f"(junk filtrés={stats['junk']}) | déjà connues={stats['deja_connues']} | nouvelles={stats['nouvelles']}\n")
    for c in new:
        print(f"  + {c['employeur']:28.28} | {c['poste']:50.50} | {c['lieu']:24.24}"
              f"{' | ' + c['mode'] if c['mode'] else ''}")
    if write and new:
        fields_list = []
        for c in new:
            f = {"jobId": c["jobId"], "Poste": c["poste"], "Employeur": c["employeur"],
                 "Lieu": c["lieu"], "URL": c["url"], "Date 1ère vue": today}
            if c["mode"]:
                f["Mode"] = c["mode"]
            fields_list.append(f)
        stats["creees"] = airtable.create(C.T_VEILLE2, fields_list)
        print(f"\n  → {stats['creees']} record(s) créé(s) dans Veille 2")
    return stats


# ---------- Orchestration ----------
def enrich_one(rec, bareme):
    f = rec["fields"]
    job_id = f.get("jobId", "")
    poste, employeur, lieu = f.get("Poste", ""), f.get("Employeur", ""), f.get("Lieu", "")
    jd = fetch_jd(job_id)
    ntxt = claude.call(C.MODEL_NOTES, notes_prompt(poste, employeur, lieu, jd["criteres"], jd["descText"]), 1500)
    note_role, note_crit, mode_notes = parse_notes(ntxt)
    if not note_role and not note_crit:
        raise claude.ClaudeError("notes vides (parse)")
    stxt = claude.call(C.MODEL_SCORE, score_prompt(bareme, poste, employeur, lieu, note_role, note_crit, jd["descText"]), 400,
                       temperature=0)
    score = parse_score(stxt)
    # Priorité Mode : critères JD > notes Sonnet (comme n8n)
    mode = jd["modeFromJd"] or clean_mode(mode_notes)
    # Easy Apply : HTML JD, fallback valeur Airtable existante (comme extraire-desc.js)
    easy = jd["easyApply"] or bool(f.get("Easy Apply"))
    return {"note_role": note_role, "note_crit": note_crit, "score": score,
            "desc": bool(jd["descText"]), "easyApply": easy, "mode": mode}


def main():
    args = sys.argv[1:]
    do_ingest = "ingest" in args or "all" in args
    do_enrich = "ingest" not in args          # "ingest" seul = ingestion uniquement
    mode = "dry-run"
    if "--write" in args:
        mode = "write"
    elif "--test" in args:
        mode = "test"
    n = next((int(a) for a in args if a.isdigit()), 5)

    if do_ingest:
        ingest(write=(mode == "write"))
        if not do_enrich:
            return
        print()
    bareme = C.bareme()

    if mode == "test":
        recs = airtable.list_records(C.T_VEILLE2, fields=["jobId", "Poste", "Employeur", "Lieu", "Score", "Easy Apply"],
                                     formula="{Score} != ''")[:n]
        print(f"TEST — recompute {len(recs)} offres déjà scorées (aucune écriture)\n")
        for r in recs:
            old = r["fields"].get("Score")
            try:
                res = enrich_one(r, bareme)
                print(f"  {r['fields'].get('Employeur','?'):28.28} | ancien={old:>3} → recalcul={res['score']}  {'[desc]' if res['desc'] else '[titre]'}")
            except Exception as e:
                print(f"  {r['fields'].get('Employeur','?'):28.28} | ERREUR: {e}")
            time.sleep(1.0)
        return

    recs = airtable.list_records(C.T_VEILLE2, fields=["jobId", "Poste", "Employeur", "Lieu", "Easy Apply", "Note rôle"],
                                 formula="{Note rôle} = ''")
    stats = {"total": len(recs), "notes_ok": 0, "fail": 0, "written": 0, "errors": []}
    print(f"{mode.upper()} — {len(recs)} offre(s) sans note\n")
    for r in recs:
        emp = r["fields"].get("Employeur", "?")
        try:
            res = enrich_one(r, bareme)
            stats["notes_ok"] += 1
            fields = {"Note rôle": res["note_role"], "Note critères": res["note_crit"]}
            if res["mode"]:
                fields["Mode"] = res["mode"]
            if res["easyApply"]:
                fields["Easy Apply"] = True
            if res["score"] is not None:
                fields["Score"] = res["score"]
                if res["score"] >= C.SEUIL:
                    fields["Préparer dossier"] = True
                    if res["easyApply"]:
                        fields["Je postule"] = True
            if mode == "write":
                airtable.patch(C.T_VEILLE2, r["id"], fields)
                stats["written"] += 1
            print(f"  {emp:28.28} | score={res['score']} {'[desc]' if res['desc'] else '[titre]'}"
                  f"{' → écrit' if mode=='write' else ' (dry-run)'}")
        except Exception as e:
            stats["fail"] += 1
            stats["errors"].append(f"{emp}: {e}")
            print(f"  {emp:28.28} | ERREUR: {e}")
        time.sleep(1.0)
    print(f"\nRÉSUMÉ JOE — total={stats['total']} notes_ok={stats['notes_ok']} "
          f"écrites={stats['written']} erreurs={stats['fail']}")
    for e in stats["errors"]:
        print("   ✗", e)


if __name__ == "__main__":
    main()
