#!/usr/bin/env python3
"""
backfill_scores.py
------------------
Re-score toutes les offres Veille qui ont Note rôle + Note critères remplis.
Écrit le Score dans Airtable. N'écrase pas les autres champs.

Usage : python3 backfill_scores.py [--dry-run]
"""

import json, time, sys, urllib.request, urllib.parse, urllib.error

# ── Credentials ─────────────────────────────────────────────────────────
AIRTABLE_PAT  = "***REMOVED_AIRTABLE_PAT***"
ANTHROPIC_KEY = "***REMOVED_ANTHROPIC_KEY***"

# ── Airtable ─────────────────────────────────────────────────────────────
BASE_ID  = "apphTpnW5vu0OdnfC"
TABLE_ID = "tblrXH5Jiyg6w21lW"
AT_BASE  = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# ── Barème ────────────────────────────────────────────────────────────────
BAREME_URL = ("https://raw.githubusercontent.com/xrobitaille92150/"
              "mission-pipeline/main/nodes/scoring-bareme-prompt.txt")

DRY_RUN = "--dry-run" in sys.argv

# ── Helpers ───────────────────────────────────────────────────────────────

def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def http_get_text(url):
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8")

def at_get(params=None):
    url = AT_BASE + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    return http_get(url, {"Authorization": f"Bearer {AIRTABLE_PAT}"})

def at_patch(record_id, fields):
    payload = json.dumps({"fields": fields}).encode()
    req = urllib.request.Request(
        f"{AT_BASE}/{record_id}",
        data=payload,
        headers={"Authorization": f"Bearer {AIRTABLE_PAT}",
                 "Content-Type": "application/json"},
        method="PATCH"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def call_haiku(prompt):
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
    )
    with urllib.request.urlopen(req) as r:
        body = json.loads(r.read())
    return (body.get("content") or [{}])[0].get("text", "")

def parse_score(text):
    """Extrait {score, justification, signaux} du JSON renvoyé par Haiku."""
    import re
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return None, "", []
    try:
        o = json.loads(m.group())
        raw = o.get("score")
        score = int(raw) if raw is not None else None
        if score is not None:
            score = max(0, min(100, score))
        return score, o.get("justification", ""), o.get("signaux", [])
    except Exception:
        return None, "", []

# ── Fetch barème ──────────────────────────────────────────────────────────
print("Chargement du barème…")
BAREME = http_get_text(BAREME_URL)[:6000]
print(f"Barème chargé ({len(BAREME)} c.)")

# ── Fetch records Airtable ────────────────────────────────────────────────
print("Listing des records Veille avec notes Sonnet…")
records = []
offset = None
while True:
    params = {
        "fields[]": ["Poste", "Employeur", "Lieu", "Pertinence",
                     "Note rôle", "Note critères", "Score"],
        "filterByFormula": "AND({Note rôle} != '', {Note critères} != '')",
        "pageSize": 100,
    }
    if offset:
        params["offset"] = offset
    data = at_get(params)
    records.extend(data.get("records", []))
    offset = data.get("offset")
    if not offset:
        break
    time.sleep(0.3)

print(f"{len(records)} records à re-scorer.")

# ── Boucle de scoring ─────────────────────────────────────────────────────
ok, skipped, errors = 0, 0, 0

for i, rec in enumerate(records):
    f = rec.get("fields", {})
    rec_id    = rec["id"]
    poste     = f.get("Poste", "(non renseigné)")
    employeur = f.get("Employeur", "(non renseigné)")
    lieu      = f.get("Lieu", "(non renseigné)")
    pertinence = f.get("Pertinence", "non définie")
    if isinstance(pertinence, dict):
        pertinence = pertinence.get("name", "non définie")
    note_role   = f.get("Note rôle", "")
    note_crit   = f.get("Note critères", "")

    prompt = (
        BAREME +
        "\n\nOFFRE À ÉVALUER :\n"
        f"Poste : {poste}\n"
        f"Employeur : {employeur}\n"
        f"Lieu : {lieu}\n"
        f"Pertinence pré-classifiée : {pertinence}\n\n"
        f"Analyse Sonnet du rôle :\n{note_role}\n\n"
        f"Critères recruteur :\n{note_crit}"
    )

    print(f"[{i+1}/{len(records)}] {employeur} — {poste[:50]}…", end=" ", flush=True)

    if DRY_RUN:
        print("(dry-run, skipped)")
        skipped += 1
        continue

    try:
        txt = call_haiku(prompt)
        score, justif, signaux = parse_score(txt)
        if score is None:
            print(f"⚠ parse failed: {txt[:80]}")
            errors += 1
        else:
            at_patch(rec_id, {"Score": score})
            print(f"→ {score}")
            ok += 1
        time.sleep(1.2)   # rate-limit Anthropic + Airtable
    except Exception as e:
        print(f"✗ {e}")
        errors += 1
        time.sleep(2)

print(f"\nTerminé. OK={ok}  skipped={skipped}  errors={errors}")
