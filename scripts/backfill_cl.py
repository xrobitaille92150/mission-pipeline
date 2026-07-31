#!/usr/bin/env python3
"""backfill_cl.py — Régénère les cover letters des N derniers jours avec le prompt
CL v2.4 (standards EN/FR + faits durs Xavier du 29/07/2026) et REMPLACE les PDF
sur GitHub. Met à jour le lien CL dans Airtable, copie le .docx éditable sur Drive.
Les CV ne sont pas touchés.

Usage :
  python3 scripts/backfill_cl.py --dry-run        # liste les cibles, n'écrit rien
  python3 scripts/backfill_cl.py                  # run réel (défaut : 5 jours)
  python3 scripts/backfill_cl.py --days 7
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_dossiers as rd  # noqa: E402 — réutilise prompt, PDF, docx, Drive, config

log = rd.log


def fetch_recent_cl(days):
    """Records Veille 2 dont le lien CL pointe vers candidatures/<date>/ des N derniers jours."""
    dates = {(datetime.date.today() - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
             for i in range(days)}
    url = f"https://api.airtable.com/v0/{rd.AIRTABLE_BASE}/{rd.AIRTABLE_TABLE}"
    base_params = [("returnFieldsByFieldId", "true")]
    for fid in rd.F.values():
        base_params.append(("fields[]", fid))
    records, offset = [], None
    while True:
        params = base_params.copy()
        if offset:
            params.append(("offset", offset))
        r = requests.get(url, headers=rd.at_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    out = []
    for rec in records:
        cl = rec.get("fields", {}).get(rd.F["cl"], "") or ""
        m = re.search(r"candidatures/(\d{4}-\d{2}-\d{2})/([^/]+\.pdf)", cl)
        if m and m.group(1) in dates:
            out.append((rec, m.group(1), m.group(2)))
    return out


def patch_cl(record_id, cl_link):
    url = f"https://api.airtable.com/v0/{rd.AIRTABLE_BASE}/{rd.AIRTABLE_TABLE}/{record_id}"
    r = requests.patch(url, headers=rd.at_headers(),
                       json={"fields": {rd.F["cl"]: cl_link}}, timeout=30)
    r.raise_for_status()


def rebuild_cl(rec, orig_date, old_name):
    """Régénère la CL d'un record. Retourne (old_repo_path, new_repo_path, cl_link) ou None."""
    c = rec["fields"]
    job_id = c.get(rd.F["jobid"], "")
    employeur = c.get(rd.F["employeur"], "Inconnu")
    poste = c.get(rd.F["poste"], "")
    lieu = c.get(rd.F["lieu"], "")
    note_role = c.get(rd.F["note_role"], "")
    note_crit = c.get(rd.F["note_crit"], "")
    log.info(f"→ {employeur} — {poste} [{orig_date}/{old_name}]")

    jd_text = None
    if job_id:
        try:
            jd_text = rd.fetch_linkedin(job_id)
        except Exception:
            jd_text = None
    context = jd_text or f"{note_role}\n\n{note_crit}" or f"{poste} chez {employeur} à {lieu}"
    cl_lang = rd.detect_language(jd_text or poste or context)

    clauses = rd.geo_role_clauses(lieu, jd_text, poste)
    precisions = ""
    if clauses:
        precisions = ("\n═══ PRÉCISIONS À INTÉGRER (OBLIGATOIRE) ═══\n"
                      + "\n".join(f"- {cc}" for cc in clauses)
                      + "\nIntègre ces précisions de façon fluide dans le corps ou la clôture, "
                        "jamais en liste.\n")

    context_md = open(rd.CONTEXT_FILE).read()[:8000] if os.path.exists(rd.CONTEXT_FILE) else ""
    writing_path = rd.WRITING_FR if cl_lang == "FR" else rd.WRITING_EN
    writing_rules = open(writing_path).read() if os.path.exists(writing_path) else ""

    prompt = rd.build_cl_prompt(employeur, poste, lieu, context, context_md,
                                writing_rules, cl_lang, precisions)
    cl_text = rd.claude(prompt, model=rd.SONNET_MODEL, max_tokens=12000)
    if len(cl_text.strip()) < 800:
        log.warning(f"  CL courte ({len(cl_text.strip())} c.) — retry budget élargi")
        cl_text = rd.claude(prompt, model=rd.SONNET_MODEL, max_tokens=16000)
    cl_text = cl_text.strip()
    if len(cl_text) < 400:
        log.error(f"  CL vide après retry ({len(cl_text)} c.) — record ignoré")
        return None
    log.info(f"  CL régénérée ({cl_lang}) : {len(cl_text)} caractères")

    safe_emp = re.sub(r"[^\w\-]", "", employeur.replace(" ", ""))[:30]
    jid = f"_{job_id}" if job_id else ""
    date_compact = orig_date.replace("-", "")
    new_name = f"CL_XRO_{cl_lang}_{safe_emp}{jid}_{date_compact}.pdf"

    pdf_tmp = os.path.join(rd.PDF_DIR, new_name)
    rd.text_to_pdf(cl_text, pdf_tmp, employeur=employeur, poste=poste, lang=cl_lang)

    # Remplacement dans le repo (même dossier daté que l'original)
    dest_dir = os.path.join(rd.REPO_PATH, "candidatures", orig_date)
    os.makedirs(dest_dir, exist_ok=True)
    import shutil
    shutil.copy2(pdf_tmp, os.path.join(dest_dir, new_name))
    old_repo_path = os.path.join(dest_dir, old_name)
    if old_name != new_name and os.path.exists(old_repo_path):
        os.remove(old_repo_path)

    # Copie .docx éditable → Drive
    try:
        docx_tmp = os.path.join(rd.CV_OUT_DIR, new_name.replace(".pdf", ".docx"))
        rd.cl_to_docx(cl_text, docx_tmp, employeur=employeur, poste=poste, lang=cl_lang)
        rd.copy_to_drive([docx_tmp])
    except Exception as e:  # noqa: BLE001 — non bloquant
        log.warning(f"  docx/Drive KO (non bloquant) : {e}")

    cl_link = f"https://github.com/{rd.GITHUB_REPO}/blob/main/candidatures/{orig_date}/{new_name}"
    return old_repo_path, os.path.join(dest_dir, new_name), cl_link


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    targets = fetch_recent_cl(args.days)
    log.info(f"=== backfill_cl : {len(targets)} CL des {args.days} derniers jours ===")
    if args.dry_run:
        for rec, d, name in targets:
            c = rec["fields"]
            log.info(f"  • {c.get(rd.F['employeur'], '?')} — {c.get(rd.F['poste'], '?')} ({d}/{name})")
        return

    done, errors = 0, []
    for rec, orig_date, old_name in targets:
        try:
            res = rebuild_cl(rec, orig_date, old_name)
            if not res:
                errors.append(old_name)
                continue
            _, _, cl_link = res
            patch_cl(rec["id"], cl_link)
            done += 1
        except Exception as e:  # noqa: BLE001
            log.error(f"  KO {old_name} : {e}")
            errors.append(old_name)
        time.sleep(1.2)

    # Un seul commit pour tout le backfill
    try:
        subprocess.run(["git", "-C", rd.REPO_PATH, "add", "-A", "candidatures/"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", rd.REPO_PATH, "commit",
                        "-m", f"backfill CL v2.4 : {done} CL régénérées (faits durs + standards EN/FR du 29/07)"],
                       check=True, capture_output=True)
        # MATT peut avoir poussé pendant le run — rebase avant push
        subprocess.run(["git", "-C", rd.REPO_PATH, "pull", "--rebase", "origin", "main"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", rd.REPO_PATH, "push", "origin", "main"],
                       check=True, capture_output=True)
        log.info("GitHub push OK")
    except subprocess.CalledProcessError as e:
        log.error(f"git KO : {(e.stderr or b'').decode('utf-8', 'replace')[:400]}")

    log.info(f"=== backfill terminé : {done} OK, {len(errors)} KO {errors if errors else ''} ===")


if __name__ == "__main__":
    main()
