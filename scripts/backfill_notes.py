#!/usr/bin/env python3
"""
backfill_notes.py
-----------------
Génère Note rôle + Note critères (Sonnet) PUIS Score (Haiku) pour les offres
Veille Haute/Moyenne dont la Note rôle est vide — y compris celles dont la
description LinkedIn est indisponible (notes dégradées sur titre+employeur+lieu).

Contourne la fragilité du workflow n8n (branche false de 'Desc OK ?' et error
output de 'Notes (Claude)' = culs-de-sac). Déterministe, retries propres.

Usage : python3 backfill_notes.py [--dry-run]
"""

import json, time, sys, urllib.request, urllib.parse, urllib.error, re

import os

def _load_env(path):
    try:
        with open(os.path.expanduser(path)) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass

_load_env("~/.config/mission-pipeline/airtable.env")
_load_env("~/.config/mission-pipeline/anthropic.env")
AIRTABLE_PAT  = os.environ["AIRTABLE_PAT"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

BASE_ID  = "apphTpnW5vu0OdnfC"
TABLE_ID = "tblrXH5Jiyg6w21lW"
AT_BASE  = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
BAREME_URL = ("https://raw.githubusercontent.com/xrobitaille92150/"
              "mission-pipeline/main/nodes/scoring-bareme-prompt.txt")

# Field IDs
F_POSTE = "fld4E08BVrNWqpJL2"
F_PERTINENCE = "fldapfJfbwD1vWNyy"
F_EMPLOYEUR = "Employeur"
F_LIEU = "Lieu"
F_NOTE_ROLE = "Note rôle"
F_NOTE_CRIT = "Note critères"
F_SCORE = "Score"
F_PREPARER = "Préparer dossier"

DRY_RUN = "--dry-run" in sys.argv

PROFIL = ("Profil candidat : consultant senior independant, 25 ans d'experience, "
          "100% assurance & finance. Cherche mission freelance/contract/interim "
          "(CDI senior finance possible), full remote ou hybride privilegie, "
          "TJM >= 800 EUR/j. Ouvert a l'international en remote (EMEA, Amerique du Nord, APAC).")


def http_json(url, headers=None, data=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def http_text(url):
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8")

def at_headers():
    return {"Authorization": f"Bearer {AIRTABLE_PAT}", "Content-Type": "application/json"}

def at_patch(record_id, fields):
    payload = json.dumps({"fields": fields}).encode()
    return http_json(f"{AT_BASE}/{record_id}", at_headers(), payload, "PATCH")

def call_claude(model, prompt, max_tokens, retries=4):
    payload = json.dumps({
        "model": model, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    hdr = {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
           "content-type": "application/json"}
    last = None
    for attempt in range(retries):
        try:
            body = http_json("https://api.anthropic.com/v1/messages", hdr, payload, "POST")
            return (body.get("content") or [{}])[0].get("text", "")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 529):
                time.sleep(4 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(4 * (attempt + 1))
    raise last


def fetch_linkedin_desc(job_id):
    """Best-effort. Renvoie '' si indisponible."""
    if not job_id:
        return ""
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    req = urllib.request.Request(url, headers={"User-Agent":
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", "ignore")
    except Exception:
        return ""
    i = html.find("show-more-less-html__markup")
    if i < 0:
        return ""
    chunk = html[i:i+14000]
    chunk = re.sub(r"show-more-less-html__markup[^>]*>", "", chunk, count=1)
    cut = re.search(r"show-more-less__button|</section>", chunk)
    if cut:
        chunk = chunk[:cut.start()]
    txt = re.sub(r"<[^>]+>", " ", chunk)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&")
              .replace("&#39;", "'").replace("&rsquo;", "'").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", txt).strip()


def build_notes_prompt(poste, employeur, lieu, desc):
    return (f"{PROFIL}\n\nVoici une offre d'emploi. Redige DEUX notes courtes en francais, "
            f"factuelles, pour aider ce candidat a decider de postuler.\n\n"
            f"OFFRE : {poste} — {employeur} — {lieu or 'lieu n.c.'}\n"
            f"DESCRIPTION :\n{(desc or '(non disponible — base toi sur le titre, l employeur et le lieu)')[:6000]}\n\n"
            f"Reponds UNIQUEMENT avec un objet JSON, sans texte autour :\n"
            f'{{"note_role":"3 a 5 phrases : de quoi parle le poste, missions cles, seniorite, '
            f'et en quoi il matche ou non le profil sur le FOND du role",'
            f'"note_criteres":"3 a 5 puces separees par des retours a la ligne, chaque ligne '
            f"prefixee '- ' : pour chaque critere cle du recruteur (seniorite, type de contrat, "
            f'secteur, competences, langue, localisation/remote) indique match ou ecart vs le profil"}}')


def parse_notes(txt):
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m:
        return "", ""
    try:
        o = json.loads(m.group())
        return o.get("note_role", ""), o.get("note_criteres", "")
    except Exception:
        return "", ""


def build_score_prompt(bareme, poste, employeur, lieu, pertinence, note_role, note_crit, desc):
    note_part = (f"\n\nAnalyse Sonnet du rôle :\n{note_role}\n\nCritères recruteur :\n{note_crit}"
                 if (note_role or note_crit) else "\n(Pas d'analyse Sonnet disponible)")
    desc_part = (f"\nDescription (extrait, <=4000 c.) :\n{desc[:4000]}"
                 if desc and len(desc) > 100
                 else "\n(Fiche de poste non disponible — score sur titre + employeur + lieu)")
    return (bareme + "\n\nOFFRE À ÉVALUER :\n" +
            f"Poste : {poste or '(non renseigné)'}\n" +
            f"Employeur : {employeur or '(non renseigné)'}\n" +
            f"Lieu : {lieu or '(non renseigné)'}\n" +
            f"Pertinence pré-classifiée : {pertinence or 'non définie'}" +
            note_part + desc_part)


def parse_score(txt):
    m = re.search(r"\{[\s\S]*\}", txt)
    if not m:
        return None
    try:
        o = json.loads(m.group())
        raw = o.get("score")
        s = int(raw) if raw is not None else None
        return max(0, min(100, s)) if s is not None else None
    except Exception:
        return None

# ── Main ────────────────────────────────────────────────────────────────────

PERT_HAUTE = "sel0dlskzgzO7anZO"
PERT_MOYENNE = "selcFm5U8eddPMnzl"

def list_target_records():
    """Haute/Moyenne avec Note rôle vide. Pagination."""
    records, offset = [], None
    formula = ("AND({Note rôle} = '', "
               "OR({Pertinence} = 'Haute', {Pertinence} = 'Moyenne'))")
    while True:
        params = {"filterByFormula": formula, "pageSize": 100,
                  "fields[]": ["jobId", "Poste", "Employeur", "Lieu", "Pertinence"]}
        if offset:
            params["offset"] = offset
        url = AT_BASE + "?" + urllib.parse.urlencode(params, doseq=True)
        data = http_json(url, {"Authorization": f"Bearer {AIRTABLE_PAT}"})
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.3)
    return records


def main():
    print("Chargement du barème…")
    bareme = http_text(BAREME_URL)[:6000]
    print(f"Barème : {len(bareme)} c.")

    recs = list_target_records()
    print(f"{len(recs)} offres Haute/Moyenne sans Note rôle.\n")

    ok = notes_fail = 0
    for i, rec in enumerate(recs):
        f = rec.get("fields", {})
        rid = rec["id"]
        poste = f.get("Poste", "")
        employeur = f.get("Employeur", "")
        lieu = f.get("Lieu", "")
        pert = f.get("Pertinence", "")
        if isinstance(pert, dict):
            pert = pert.get("name", "")
        job_id = f.get("jobId", "")

        label = f"[{i+1}/{len(recs)}] {employeur or '?'} — {poste[:45]}"
        print(label, end=" ", flush=True)

        if DRY_RUN:
            print("(dry-run)")
            continue

        # 1) Description LinkedIn best-effort
        desc = fetch_linkedin_desc(job_id)

        # 2) Notes Sonnet
        try:
            ntxt = call_claude("claude-sonnet-4-6", build_notes_prompt(poste, employeur, lieu, desc), 1500)
            note_role, note_crit = parse_notes(ntxt)
        except Exception as e:
            print(f"✗ notes: {e}")
            notes_fail += 1
            time.sleep(2)
            continue
        if not note_role and not note_crit:
            print("⚠ notes vides (parse)")
            notes_fail += 1
            time.sleep(1.2)
            continue

        # 3) Score Haiku
        score = None
        try:
            stxt = call_claude("claude-haiku-4-5-20251001",
                               build_score_prompt(bareme, poste, employeur, lieu, pert,
                                                  note_role, note_crit, desc), 400)
            score = parse_score(stxt)
        except Exception as e:
            print(f"(score échoué: {e}) ", end="")

        # 4) Écriture Airtable (notes + score + Préparer dossier si Haute)
        fields = {F_NOTE_ROLE: note_role, F_NOTE_CRIT: note_crit}
        if score is not None:
            fields[F_SCORE] = score
        if pert == "Haute":
            fields[F_PREPARER] = True
        at_patch(rid, fields)
        print(f"→ notes OK, score={score}{' [desc]' if desc else ' [titre]'}")
        ok += 1
        time.sleep(1.2)

    print(f"\nTerminé. OK={ok}  notes_fail={notes_fail}  total={len(recs)}")


if __name__ == "__main__":
    main()
