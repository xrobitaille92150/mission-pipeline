"""Construction d'un dossier de candidature : CV adapté + lettre, en DOCX et PDF."""
from __future__ import annotations

import logging
import shutil
from datetime import date
from pathlib import Path

from mp import cv as cvmod
from mp import letter as lettermod
from mp.context import Context
from mp.linkedin import fetch_jd
from mp.models import DossierResult
from mp.pdf import docx_to_pdf
from mp.scoring import detect_language

log = logging.getLogger("mp.dossier")


def build_dossier(ctx: Context, rec: dict) -> DossierResult:
    f = rec["fields"]
    job_id = f.get("jobId", "")
    title, employer, location = f.get("Poste", ""), f.get("Employeur", ""), f.get("Lieu", "")
    keywords = [k.strip() for k in (f.get("Mots-clés") or "").split(",") if k.strip()]

    # 1. Fiche de poste (Airtable d'abord, LinkedIn ensuite, titre seul en dernier recours)
    jd_text = (f.get("Description") or "").strip()
    if len(jd_text) <= 200 and job_id:
        jd = fetch_jd(job_id)
        if jd.ok:
            jd_text = jd.text
            ctx.at.patch(ctx.offres, rec["id"], {"Description": jd_text[:95000]})
        else:
            log.warning("fiche LinkedIn indisponible pour %s (%s) — dossier sur le titre", job_id, jd.error)

    # 2. Langue + profil CV
    lang = f.get("Langue") if f.get("Langue") in ("FR", "EN") else detect_language(jd_text or title)
    if lang == "AUTRE":
        lang = "EN"
    profile = cvmod.select_profile(f"{title} {jd_text}", hint=f.get("Profil CV"))
    base, lang_cv = cvmod.base_cv_path(ctx.s.cv_base_dir, profile, lang)

    # 3. Retouches CV
    cv_text = cvmod.docx_text(base)
    plan = cvmod.propose_edits(ctx.claude, cv_text=cv_text, profile=profile, lang=lang_cv, title=title,
                               employer=employer, jd_text=jd_text, keywords=keywords, profile_md=ctx.profile_md)
    day = date.today().strftime("%Y%m%d")
    out_dir = ctx.out("dossiers", date.today().isoformat())
    cv_docx = out_dir / (cvmod.output_name(lang_cv, profile, employer, job_id, day) + ".docx")
    applied, skipped = cvmod.apply_edits(base, cv_docx, plan.edits)
    cv_pdf = docx_to_pdf(cv_docx, out_dir)

    # 4. Lettre
    rules = lettermod.load_writing_rules(ctx.s.writing_rules_dir, lang)
    letter = lettermod.write_letter(ctx.claude, lang=lang, title=title, employer=employer, location=location,
                                    jd_text=jd_text, profile_md=ctx.profile_md, writing_rules=rules, gaps=plan.gaps)
    letter_docx = out_dir / (lettermod.output_name(lang, employer, job_id, day) + ".docx")
    lettermod.letter_docx(letter.lettre, letter_docx, employer=employer, title=title, lang=lang)
    letter_pdf = docx_to_pdf(letter_docx, out_dir)

    # 5. Copie éditable vers le miroir Drive (facultatif, non bloquant)
    if ctx.s.drive_dossiers_dir:
        try:
            dest = ctx.s.drive_dossiers_dir / date.today().isoformat()
            dest.mkdir(parents=True, exist_ok=True)
            for p in (cv_docx, cv_pdf, letter_docx, letter_pdf):
                shutil.copy2(p, dest / p.name)
        except OSError as e:
            log.warning("copie Drive échouée (non bloquant) : %s", e)

    return DossierResult(
        job_id=job_id, employer=employer, title=title, lang=lang, profile=profile,
        cv_docx=str(cv_docx), cv_pdf=str(cv_pdf), letter_docx=str(letter_docx), letter_pdf=str(letter_pdf),
        letter_text=letter.lettre, objections=letter.objections, edits_applied=applied, edits_skipped=skipped,
        gaps=plan.gaps,
    )


def ensure_record(ctx: Context, *, job_id: str | None, url: str, title: str = "", employer: str = "",
                  location: str = "", description: str = "") -> dict:
    """Récupère ou crée l'enregistrement Offres pour un dossier à la demande (`mp dossier`)."""
    from mp.airtable import fstr
    from mp.scoring import today

    rec = None
    if job_id:
        found = ctx.at.list(ctx.offres, formula=f"{{jobId}}={fstr(job_id)}", max_records=1)
        rec = found[0] if found else None
    if rec is None:
        fields = {"jobId": job_id or f"manuel-{today()}-{abs(hash(url or title)) % 100000}",
                  "Poste": title or "(à compléter)", "Employeur": employer or "(à compléter)", "Lieu": location,
                  "URL": url or "", "Date 1ère vue": today(), "Dernière vue": today(), "Source": "Manuelle",
                  "Statut": "À étudier", "Préparer dossier": True}
        if description:
            fields["Description"] = description[:95000]
        created = ctx.at.create(ctx.offres, [fields])
        rec = created[0] if created else {"id": "dry-run", "fields": fields}
    elif description and len(rec["fields"].get("Description", "")) <= 200:
        ctx.at.patch(ctx.offres, rec["id"], {"Description": description[:95000]})
        rec["fields"]["Description"] = description
    return rec


def local_paths(res: DossierResult) -> list[Path]:
    return [Path(res.cv_pdf), Path(res.cv_docx), Path(res.letter_pdf), Path(res.letter_docx)]
