"""Étapes du run quotidien : ingestion → scoring → dossiers → synchronisation des décisions.

Chaque étape est idempotente et écrit ses erreurs dans le champ « Erreur » de l'offre concernée
plutôt que d'interrompre le run.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from mp.airtable import record_url
from mp.context import Context
from mp.dossier import build_dossier
from mp.gmail import cards_from_email, query_job_digests
from mp.linkedin import fetch_jd
from mp.models import (
    CLUSTER_LABELS,
    CONTRAT_LABELS,
    MODE_LABELS,
    POSTURE_LABELS,
    VERDICT_LABELS,
    Card,
    JobDescription,
)
from mp.scoring import excluded_scoring, hard_filter, rank_for_dossiers, score_offer, today

log = logging.getLogger("mp.pipeline")

F_CV_FILES = "CV (fichiers)"
F_LETTER_FILES = "Lettre (fichiers)"


# ---------------------------------------------------------------------------
# 1. Ingestion
# ---------------------------------------------------------------------------


def card_to_fields(c: Card) -> dict:
    f = {
        "jobId": c.job_id, "Poste": c.title, "Employeur": c.employer, "Lieu": c.location,
        "URL": c.url, "Date 1ère vue": c.email_date or today(), "Dernière vue": c.email_date or today(),
        "Source": c.source, "Statut": "Nouvelle",
    }
    if c.mode:
        f["Mode"] = c.mode
    if c.alert_name:
        f["Recherche LI"] = c.alert_name
    if c.easy_apply:
        f["Easy Apply"] = True
    return f


def ingest(ctx: Context, days: int | None = None) -> dict:
    days = days or ctx.s.ingest_days
    stats = {"emails": 0, "cartes": 0, "nouvelles": 0, "revues": 0, "creees": 0}
    cards: dict[str, Card] = {}
    uids = []
    with ctx.gmail() as g:
        for e in g.iter(query_job_digests(days)):
            stats["emails"] += 1
            found = cards_from_email(e)
            for c in found:
                stats["cartes"] += 1
                prev = cards.get(c.job_id)
                if prev is None or (prev.source != "Alerte" and c.source == "Alerte"):
                    cards[c.job_id] = c
            uids.append(e.uid)
        existing = {r["fields"].get("jobId", ""): r for r in
                    ctx.at.list(ctx.offres, fields=["jobId", "Dernière vue", "Statut"])}
        new, seen_again = [], []
        for jid, c in cards.items():
            if jid in existing:
                rec = existing[jid]
                last = rec["fields"].get("Dernière vue") or ""
                if (c.email_date or today()) > last:
                    seen_again.append({"id": rec["id"], "fields": {"Dernière vue": c.email_date or today()}})
            else:
                new.append(card_to_fields(c))
        stats["nouvelles"] = len(new)
        stats["revues"] = len(seen_again)
        for c in list(cards.values())[:200]:
            log.info("  carte %s | %-28.28s | %-50.50s | %s", c.source[:5], c.employer, c.title, c.location)
        if new:
            created = ctx.at.create(ctx.offres, new)
            stats["creees"] = len(created)
        if seen_again:
            ctx.at.patch_many(ctx.offres, seen_again)
        if not ctx.dry_run:
            g.mark_done(uids)
    log.info("ingestion : %s", stats)
    ctx.report.ingest = stats
    return stats


# ---------------------------------------------------------------------------
# 2. Scoring
# ---------------------------------------------------------------------------


def _jd_for(ctx: Context, rec: dict) -> tuple[JobDescription, bool]:
    """Fiche depuis le champ Description si présent, sinon téléchargement. (jd, fetched)"""
    f = rec["fields"]
    desc = (f.get("Description") or "").strip()
    if len(desc) > 200:
        return JobDescription(ok=True, text=desc, easy_apply=bool(f.get("Easy Apply")), mode=f.get("Mode", "")), False
    jd = fetch_jd(f.get("jobId", ""))
    time.sleep(1.2)
    return jd, True


def scoring_fields(s, jd: JobDescription | None, fetched: bool) -> dict:
    fields = {
        "Score": s.score,
        "Verdict IA": VERDICT_LABELS[s.verdict],
        "Cluster": CLUSTER_LABELS[s.cluster],
        "Posture": POSTURE_LABELS[s.posture],
        "Langue": "Autre" if s.langue == "AUTRE" else s.langue,
        "Pays": s.pays,
        "Contrat": CONTRAT_LABELS[s.contrat],
        "Pourquoi": "\n".join(f"• {p}" for p in s.pourquoi),
        "Red flags": "\n".join(f"• {r}" for r in s.red_flags),
        "Mots-clés": ", ".join(s.mots_cles)[:250],
        "Profil CV": s.profil_cv,
        "Scoré le": today(),
        "Statut": "Écartée" if s.verdict == "ECARTER" else "À étudier",
        "Erreur": "",
    }
    mode = MODE_LABELS.get(s.mode, "")
    if mode:
        fields["Mode"] = mode
    if jd and jd.ok and fetched:
        fields["Description"] = jd.text[:95000]
        if jd.easy_apply:
            fields["Easy Apply"] = True
        if jd.mode and not mode:
            fields["Mode"] = jd.mode
    return fields


def score(ctx: Context, limit: int = 80, rescore: bool = False) -> list[dict]:
    live = "NOT({J'écarte}=1),NOT({Statut}='Écartée'),NOT({Statut}='Expirée')"
    formula = f"AND(NOT({{Scoré le}}),{live})" if not rescore else f"AND({live})"
    recs = ctx.at.list(ctx.offres, formula=formula, sort=[("Date 1ère vue", "desc")], max_records=limit,
                       fields=["jobId", "Poste", "Employeur", "Lieu", "Mode", "Source", "Description",
                               "Easy Apply", "Statut"])
    log.info("scoring : %d offre(s) à évaluer", len(recs))
    scored: list[tuple[str, object]] = []
    for rec in recs:
        f = rec["fields"]
        title, employer, location = f.get("Poste", ""), f.get("Employeur", ""), f.get("Lieu", "")
        try:
            jd, fetched = _jd_for(ctx, rec)
            reason = hard_filter(title, location, jd if jd.ok else None)
            if reason:
                s = excluded_scoring(reason)
            else:
                s = score_offer(ctx.claude, title=title, employer=employer, location=location,
                                mode=f.get("Mode", ""), source=f.get("Source", "Alerte"), jd=jd)
            fields = scoring_fields(s, jd, fetched)
            if not jd.ok and jd.error:
                fields["Erreur"] = f"Fiche LinkedIn non récupérée : {jd.error}"
            ctx.at.patch(ctx.offres, rec["id"], fields)
            scored.append((rec["id"], s))
            ctx.report.scored.append({
                "record": rec["id"], "title": title, "employer": employer, "score": s.score,
                "verdict": VERDICT_LABELS[s.verdict], "why": s.pourquoi, "url": f.get("URL", ""),
                "airtable": record_url(ctx.s.airtable_base, ctx.offres, rec["id"]), "rank": None,
            })
            log.info("  %3d %-8s %-28.28s | %s", s.score, VERDICT_LABELS[s.verdict], employer, title[:60])
        except Exception as e:  # noqa: BLE001 — on consigne l'erreur sur la ligne et on continue
            msg = f"{employer} — {title[:50]} : {e}"
            ctx.report.error(f"scoring KO : {msg}")
            try:
                ctx.at.patch(ctx.offres, rec["id"], {"Erreur": f"Scoring KO : {str(e)[:900]}"})
            except Exception:  # noqa: BLE001
                pass
    ranks = rank_for_dossiers(scored, ctx.s.score_min, ctx.s.auto_dossiers)
    if ranks:
        ctx.at.patch_many(ctx.offres, [{"id": rid, "fields": {"Rang du jour": rank, "Préparer dossier": True}}
                                       for rid, rank in ranks.items()])
        for item in ctx.report.scored:
            item["rank"] = ranks.get(item["record"])
        log.info("dossiers automatiques : %d (top %d, score ≥ %d)", len(ranks), ctx.s.auto_dossiers, ctx.s.score_min)
    return ctx.report.scored


# ---------------------------------------------------------------------------
# 3. Dossiers (CV + lettre) pour les offres cochées
# ---------------------------------------------------------------------------


def dossiers(ctx: Context, max_n: int = 6) -> list[dict]:
    formula = ("AND(OR({Préparer dossier}=1,{Je postule}=1),NOT({Dossier le}),NOT({J'écarte}=1),"
               "NOT({Statut}='Écartée'),NOT({Statut}='Expirée'))")
    recs = ctx.at.list(ctx.offres, formula=formula, sort=[("Score", "desc")], max_records=max_n)
    log.info("dossiers : %d offre(s) en attente", len(recs))
    for rec in recs:
        f = rec["fields"]
        try:
            res = build_dossier(ctx, rec)
            if not ctx.dry_run:
                ctx.at.replace_attachments(ctx.offres, rec["id"], F_CV_FILES, [res.cv_pdf, res.cv_docx])
                ctx.at.replace_attachments(ctx.offres, rec["id"], F_LETTER_FILES, [res.letter_pdf, res.letter_docx])
            fields = {
                "Lettre texte": res.letter_text,
                "Objections": "\n".join(f"• {o}" for o in res.objections),
                "Profil CV": res.profile, "Langue": res.lang, "Dossier le": today(), "Erreur": "",
            }
            if f.get("Statut") not in ("Postulée",):
                fields["Statut"] = "Dossier prêt"
            ctx.at.patch(ctx.offres, rec["id"], fields)
            ctx.report.dossiers.append({
                "record": rec["id"], "title": res.title, "employer": res.employer, "lang": res.lang,
                "profile": res.profile, "cv": str(res.cv_pdf), "letter": str(res.letter_pdf),
                "url": f.get("URL", ""), "airtable": record_url(ctx.s.airtable_base, ctx.offres, rec["id"]),
                "edits": res.edits_applied, "gaps": res.gaps,
            })
            log.info("  dossier OK : %s — %s (%s, %s)", res.employer, res.title[:50], res.profile, res.lang)
        except Exception as e:  # noqa: BLE001
            msg = f"{f.get('Employeur', '?')} — {f.get('Poste', '?')[:50]} : {e}"
            ctx.report.error(f"dossier KO : {msg}")
            try:
                ctx.at.patch(ctx.offres, rec["id"], {"Erreur": f"Dossier KO : {str(e)[:900]}"})
            except Exception:  # noqa: BLE001
                pass
    return ctx.report.dossiers


# ---------------------------------------------------------------------------
# 4. Décisions prises dans Airtable (cases à cocher) → statuts + Candidatures
# ---------------------------------------------------------------------------


MAX_POSTULE_PER_RUN = 15   # au-delà, c'est presque sûrement un stock de coches anciennes : on demande --force


def sync_decisions(ctx: Context, expire_after_days: int | None = None, force: bool = False) -> dict:
    from mp.tracking import upsert_candidature  # import tardif (dépendance croisée)

    expire_after_days = expire_after_days or ctx.s.expire_days

    stats = {"postulees": 0, "ecartees": 0, "expirees": 0}
    d = today()
    # Je postule → Postulée + Candidature
    pending = ctx.at.list(ctx.offres, formula="AND({Je postule}=1, NOT({Statut}='Postulée'))")
    if len(pending) > MAX_POSTULE_PER_RUN and not force:
        ctx.report.error(f"{len(pending)} offres cochées « Je postule » en attente : au-delà de {MAX_POSTULE_PER_RUN}, "
                         "rien n'est appliqué par sécurité. Vérifier les coches, puis `mp sync --force` si c'est voulu.")
        pending = []
    for rec in pending:
        f = rec["fields"]
        fields = {"Statut": "Postulée"}
        if not f.get("Date postulé"):
            fields["Date postulé"] = d
        if (f.get("Réponse") or "Néant") == "Néant":
            fields["Réponse"] = "Envoyé"
        ctx.at.patch(ctx.offres, rec["id"], fields)
        upsert_candidature(ctx, company=f.get("Employeur", ""), title=f.get("Poste", ""), url=f.get("URL", ""),
                           job_id=f.get("jobId", ""), status="Envoyé", day=f.get("Date postulé") or d,
                           cluster=f.get("Cluster"), location=f.get("Lieu", ""), mode=f.get("Mode", ""),
                           note="Décision « Je postule » cochée dans Offres.")
        stats["postulees"] += 1
    # J'écarte → Écartée
    for rec in ctx.at.list(ctx.offres, formula="AND({J'écarte}=1, NOT({Statut}='Écartée'))", fields=["jobId"]):
        ctx.at.patch(ctx.offres, rec["id"], {"Statut": "Écartée", "Préparer dossier": False})
        stats["ecartees"] += 1
    # Offres jamais traitées depuis N jours → Expirée (rien n'est supprimé)
    cutoff = (date.today() - timedelta(days=expire_after_days)).isoformat()
    # Une offre « Préparer dossier » cochée n'expire pas (le dossier arrive au même run), sauf si elle vient de
    # l'ancien pipeline (aucun Statut) : ces coches de l'été sont des reliquats, on les retire en expirant.
    last_seen = "IF({Dernière vue},{Dernière vue},{Date 1ère vue})"
    formula = (f"AND(OR({{Statut}}='Nouvelle',{{Statut}}='À étudier',NOT({{Statut}})),"
               f"OR(NOT({{Préparer dossier}}=1),NOT({{Statut}})),"
               f"NOT({{Je postule}}=1),NOT({{Dossier le}}),"
               f"IS_BEFORE({last_seen},DATETIME_PARSE('{cutoff}','YYYY-MM-DD')))")
    old = ctx.at.list(ctx.offres, formula=formula, fields=["jobId"])
    if old:
        ctx.at.patch_many(ctx.offres, [{"id": r["id"], "fields": {"Statut": "Expirée", "Préparer dossier": False}}
                                       for r in old])
        stats["expirees"] = len(old)
    ctx.report.decisions = stats
    log.info("décisions : %s", stats)
    return stats
