"""Étapes du run quotidien : ingestion → scoring → dossiers → synchronisation des décisions.

Chaque étape est idempotente et écrit ses erreurs dans le champ « Erreur » de l'offre concernée
plutôt que d'interrompre le run.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import date, timedelta

from mp.airtable import record_url
from mp.context import Context
from mp.cv import select_profile
from mp.dossier import build_dossier
from mp.gmail import cards_from_email, query_job_digests
from mp.linkedin import fetch_jd
from mp.models import (
    CLUSTER_LABELS,
    CONTRAT_LABELS,
    MODE_LABELS,
    POSTURE_LABELS,
    STATUT_SYMBOLS,
    VERDICT_LABELS,
    Card,
    JobDescription,
    format_criteres,
)
from mp.scoring import excluded_scoring, hard_filter, notes_offer, rank_for_dossiers, score_offer, today

log = logging.getLogger("mp.pipeline")
LINKEDIN_PAUSE = 1.2  # secondes entre deux pages LinkedIn (endpoint public rate-limité par IP)

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
# 1 bis. Doublons : même employeur + même poste (règle de l'ancien JACK), sans jamais rien supprimer
# ---------------------------------------------------------------------------

_GENRE = re.compile(r"\(?\b[hfm]\s*/\s*[hfmwd](?:\s*/\s*[hfmwd])?\b\)?", re.I)   # (H/F), F/H, m/w/d


def norm_title(s: str) -> str:
    s = _GENRE.sub(" ", str(s or ""))
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


ACTIVE = ("Nouvelle", "À étudier", "Dossier prêt", "")
DECIDED = ("Postulée", "Écartée")        # une décision déjà prise couvre les doublons qui reviennent


def dedupe(ctx: Context) -> dict:
    """Range en « Doublon » les offres actives qui répètent une offre déjà présente (même employeur, même poste).

    Ordre de conservation repris de JACK : dossier présent > case cochée > meilleur score > plus ancienne.
    Si l'offre a déjà été décidée (Postulée ou Écartée), ses réapparitions sont toutes rangées en Doublon.
    Jamais modifiées : offres Postulée / Écartée / Expirée / Doublon et lignes cochées « Je postule ».
    """
    from mp.tracking import norm_company

    recs = ctx.at.list(ctx.offres, fields=["jobId", "Employeur", "Poste", "Statut", "Score", "Je postule",
                                           "Préparer dossier", "Dossier le", "CV (fichiers)", "CV", "Date 1ère vue"])
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in recs:
        f = r["fields"]
        key = (norm_company(f.get("Employeur", "")), norm_title(f.get("Poste", "")))
        if key[0] and key[1]:
            groups.setdefault(key, []).append(r)

    def statut(r: dict) -> str:
        v = r["fields"].get("Statut") or ""
        return v.get("name", "") if isinstance(v, dict) else v

    def rang(r: dict) -> tuple:
        f = r["fields"]
        return (bool(f.get("Dossier le") or f.get("CV (fichiers)") or f.get("CV")),
                bool(f.get("Préparer dossier") or f.get("Je postule")), f.get("Score") or 0)

    updates = []
    for rows in groups.values():
        if len(rows) < 2:
            continue
        active = [r for r in rows if statut(r) in ACTIVE and not r["fields"].get("Je postule")]
        if not active:
            continue
        decided = [r for r in rows if statut(r) in DECIDED]
        if decided:
            keep, marked = decided[0], active
        else:
            best = max(rang(r) for r in active)
            tete = [r for r in active if rang(r) == best]
            keep = min(tete, key=lambda r: r["fields"].get("Date 1ère vue") or "9999-12-31")
            marked = [r for r in active if r is not keep]
        ref = f"{keep['fields'].get('Employeur', '')} — {keep['fields'].get('Poste', '')} (jobId {keep['fields'].get('jobId', '')})"
        updates += [{"id": r["id"], "fields": {"Statut": "Doublon", "Doublon de": ref[:250], "Préparer dossier": False}}
                    for r in marked]
    if updates:
        ctx.at.patch_many(ctx.offres, updates)
    stats = {"groupes": sum(1 for g in groups.values() if len(g) > 1), "doublons": len(updates)}
    log.info("doublons : %s", stats)
    if updates:
        ctx.report.notes.append(f"doublons rangés : {len(updates)}")
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
    time.sleep(LINKEDIN_PAUSE)
    return jd, True


def listing_fields(jd: JobDescription | None) -> dict:
    """Parution et mode de candidature lus sur la page LinkedIn (rien de ce que la page ne donne pas)."""
    out: dict = {}
    if jd and jd.posted_on:
        out["Publiée le"] = jd.posted_on
    if jd and jd.apply_mode:
        out["Mode candidature"] = jd.apply_mode
        if jd.apply_mode == "Simplifiée":
            out["Easy Apply"] = True
    return out


def notes_fields(n) -> dict:
    """Colonnes historiques « Note rôle » / « Note critères » (vides pour une offre exclue par filtre dur)."""
    if not getattr(n, "resume", ""):
        return {}
    return {"Note rôle": n.resume.strip(), "Note critères": format_criteres(n.criteres)}


def is_v3_notes(text: str | None) -> bool:
    """Notes rédigées par la v3 (« ✓ Domaine : … ») ; les anciennes commencent par « - Séniorité : … »."""
    return (text or "").lstrip()[:1] in STATUT_SYMBOLS.values()


def refresh_notes(ctx: Context, limit: int = 200) -> dict:
    """Complète les offres en cours (À étudier, Dossier prêt) sans toucher à leur score :
    - « L'offre en bref » et « Tes critères » si elles n'ont pas encore de notes v3 (un appel Claude) ;
    - « Publiée le » et « Mode candidature » lus sur la page LinkedIn si la date manque (aucun appel Claude).
    Idempotent : une offre complète est sautée."""
    recs = ctx.at.list(ctx.offres, formula="OR({Statut}='À étudier',{Statut}='Dossier prêt')",
                       sort=[("Score", "desc")], max_records=limit,
                       fields=["jobId", "Poste", "Employeur", "Lieu", "Mode", "Source", "Description",
                               "Easy Apply", "Note critères", "Publiée le"])
    stats = {"offres": len(recs), "notes": 0, "parution": 0, "deja": 0, "erreurs": 0}
    for rec in recs:
        f = rec["fields"]
        need_notes = not is_v3_notes(f.get("Note critères"))
        need_listing = f.get("jobId", "").isdigit() and not f.get("Publiée le")
        if not (need_notes or need_listing):
            stats["deja"] += 1
            continue
        try:
            fields: dict = {}
            page = None
            if need_listing:
                page = fetch_jd(f["jobId"])
                time.sleep(LINKEDIN_PAUSE)
                fields.update(listing_fields(page))
            if need_notes:
                stored = len((f.get("Description") or "").strip()) > 200
                jd = page if (page and page.ok and not stored) else _jd_for(ctx, rec)[0]
                n = notes_offer(ctx.claude, title=f.get("Poste", ""), employer=f.get("Employeur", ""),
                                location=f.get("Lieu", ""), mode=f.get("Mode", ""),
                                source=f.get("Source", "Alerte"), jd=jd)
                fields.update(notes_fields(n))
            if fields:
                ctx.at.patch(ctx.offres, rec["id"], fields)
            stats["notes"] += int("Note critères" in fields)
            stats["parution"] += int("Publiée le" in fields)
            log.info("  fiche   %-28.28s | %s | %s", f.get("Employeur", ""), f.get("Poste", "")[:50],
                     ", ".join(k for k in ("Note critères", "Publiée le", "Mode candidature") if k in fields) or "rien")
        except Exception as e:  # noqa: BLE001 — une offre en échec n'arrête pas le rattrapage
            stats["erreurs"] += 1
            ctx.report.error(f"notes KO : {f.get('Employeur', '')} — {f.get('Poste', '')[:50]} : {e}")
    log.info("notes : %s", stats)
    return stats


def scoring_fields(s, jd: JobDescription | None, fetched: bool) -> dict:
    fields = {
        "Score": s.score,
        "Verdict IA": VERDICT_LABELS[s.verdict],
        "Cluster": CLUSTER_LABELS[s.cluster],
        "Posture": POSTURE_LABELS[s.posture],
        "Langue": "Autre" if s.langue == "AUTRE" else s.langue,
        "Pays": s.pays,
        "Contrat": CONTRAT_LABELS[s.contrat],
        "Pourquoi": "\n".join([f"• {p}" for p in s.pourquoi] + ([s.detail] if getattr(s, "detail", "") else [])),
        "Red flags": "\n".join(f"• {r}" for r in s.red_flags),
        "Mots-clés": ", ".join(s.mots_cles)[:250],
        **notes_fields(s),
        "Profil CV": s.profil_cv,
        "Scoré le": today(),
        "Statut": "Écartée" if s.verdict == "ECARTER" else "À étudier",
        "Erreur": "",
    }
    mode = MODE_LABELS.get(s.mode, "")
    if mode:
        fields["Mode"] = mode
    if fetched:
        fields.update(listing_fields(jd))
    if jd and jd.ok and fetched:
        fields["Description"] = jd.text[:95000]
        if jd.easy_apply:
            fields["Easy Apply"] = True
        if jd.mode and not mode:
            fields["Mode"] = jd.mode
    return fields


def score_one(ctx: Context, rec: dict):
    """Note une offre (fiche, filtres durs, Claude) et écrit le résultat sur sa ligne. Lève en cas d'échec."""
    f = rec["fields"]
    title, employer, location = f.get("Poste", ""), f.get("Employeur", ""), f.get("Lieu", "")
    jd, fetched = _jd_for(ctx, rec)
    reason = hard_filter(title, location, jd if jd.ok else None)
    if reason:
        s = excluded_scoring(reason)
    else:
        s = score_offer(ctx.claude, title=title, employer=employer, location=location,
                        mode=f.get("Mode", ""), source=f.get("Source", "Alerte"), jd=jd)
    fields = scoring_fields(s, jd, fetched)
    if jd.ok:  # profil affiché = celui que le dossier prendra (arbre de cv_profiles.md, l'avis de Claude ne tranche pas)
        fields["Profil CV"] = select_profile(f"{title} {jd.text}", s.profil_cv, has_jd=True)
    if not jd.ok and jd.error:
        fields["Erreur"] = f"Fiche LinkedIn non récupérée : {jd.error}"
    ctx.at.patch(ctx.offres, rec["id"], fields)
    rec["fields"].update(fields)
    ctx.report.scored.append({
        "record": rec["id"], "title": title, "employer": employer, "score": s.score,
        "verdict": VERDICT_LABELS[s.verdict], "why": s.pourquoi, "url": f.get("URL", ""),
        "airtable": record_url(ctx.s.airtable_base, ctx.offres, rec["id"]), "rank": None,
    })
    log.info("  %3d %-8s %-28.28s | %s", s.score, VERDICT_LABELS[s.verdict], employer, title[:60])
    return s


def score(ctx: Context, limit: int = 80, rescore: bool = False) -> list[dict]:
    live = "NOT({J'écarte}=1),NOT({Statut}='Écartée'),NOT({Statut}='Expirée'),NOT({Statut}='Doublon')"
    formula = f"AND(NOT({{Scoré le}}),{live})" if not rescore else f"AND({live})"
    recs = ctx.at.list(ctx.offres, formula=formula, sort=[("Date 1ère vue", "desc")], max_records=limit,
                       fields=["jobId", "Poste", "Employeur", "Lieu", "Mode", "Source", "Description",
                               "Easy Apply", "Statut"])
    log.info("scoring : %d offre(s) à évaluer", len(recs))
    scored: list[tuple[str, object]] = []
    for rec in recs:
        f = rec["fields"]
        title, employer = f.get("Poste", ""), f.get("Employeur", "")
        try:
            scored.append((rec["id"], score_one(ctx, rec)))
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
               "NOT({Statut}='Écartée'),NOT({Statut}='Expirée'),NOT({Statut}='Doublon'))")
    recs = ctx.at.list(ctx.offres, formula=formula, sort=[("Score", "desc")], max_records=max_n)
    log.info("dossiers : %d offre(s) en attente", len(recs))
    for rec in recs:
        make_dossier(ctx, rec)
    return ctx.report.dossiers


def make_dossier(ctx: Context, rec: dict) -> dict | None:
    """Construit et attache le dossier d'une offre ; consigne l'erreur sur la ligne en cas d'échec."""
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
        item = {
            "record": rec["id"], "title": res.title, "employer": res.employer, "lang": res.lang,
            "profile": res.profile, "cv": str(res.cv_pdf), "letter": str(res.letter_pdf),
            "url": f.get("URL", ""), "airtable": record_url(ctx.s.airtable_base, ctx.offres, rec["id"]),
            "edits": res.edits_applied, "gaps": res.gaps, "engine": res.engine,
        }
        ctx.report.dossiers.append(item)
        log.info("  dossier OK : %s — %s (%s, %s, %s)", res.employer, res.title[:50], res.profile, res.lang,
                 "skills" if res.engine == "claude-code" else "API")
        return item
    except Exception as e:  # noqa: BLE001
        msg = f"{f.get('Employeur', '?')} — {f.get('Poste', '?')[:50]} : {e}"
        ctx.report.error(f"dossier KO : {msg}")
        try:
            ctx.at.patch(ctx.offres, rec["id"], {"Erreur": f"Dossier KO : {str(e)[:900]}"})
        except Exception:  # noqa: BLE001
            pass
        return None


# ---------------------------------------------------------------------------
# 4. Décisions prises dans Airtable (cases à cocher) → statuts + Candidatures
# ---------------------------------------------------------------------------


def add_offer(ctx: Context, *, url: str = "", job_id: str = "", text: str = "", title: str = "", employer: str = "",
              location: str = "") -> dict:
    """Offre trouvée ailleurs (URL LinkedIn, jobId ou texte collé) : retrouve ou crée sa ligne dans Offres.
    Reprend l'ancien outil « Postuler proprement » et `mp dossier`."""
    from mp.dossier import ensure_record
    from mp.linkedin import extract_job_id

    job_id = job_id or (extract_job_id(url) if url else "") or ""
    if job_id and not url:
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    jd = None
    if job_id and not (title and employer):
        jd = fetch_jd(job_id)
        title, employer, location = title or jd.title, employer or jd.company, location or jd.location
        text = text or jd.text
    rec = ensure_record(ctx, job_id=job_id or None, url=url, title=title, employer=employer, location=location,
                        description=text)
    facts = listing_fields(jd)
    if facts and not rec["fields"].get("Publiée le"):
        ctx.at.patch(ctx.offres, rec["id"], facts)
        rec["fields"].update(facts)
    return rec


def process_new_offer(ctx: Context, record_id: str) -> str:
    """Note l'offre si besoin, puis prépare son dossier (CV + lettre). Pour le cockpit, en arrière-plan."""
    rec = ctx.at.get(ctx.offres, record_id)
    note = ""
    if not rec["fields"].get("Scoré le"):
        s = score_one(ctx, rec)
        note = f"notée {s.score}/100 ({VERDICT_LABELS[s.verdict]}), "
    item = make_dossier(ctx, ctx.at.get(ctx.offres, record_id))
    if item is None:
        raise RuntimeError(note + (ctx.report.errors[-1] if ctx.report.errors else "dossier KO"))
    return note + f"dossier prêt ({item['profile']}, {item['lang']})"


MAX_POSTULE_PER_RUN = 15   # au-delà, c'est presque sûrement un stock de coches anciennes : on demande --force


def statut_reactive(f: dict) -> str:
    """Statut rendu à une offre écartée à la main puis réactivée (« Annuler » ou « Réactiver » du cockpit) :
    sans lui, elle garderait « Écartée » et ne reviendrait jamais dans « À décider »."""
    return "Dossier prêt" if f.get("Dossier le") else "À étudier"


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
    # Offres jamais traitées depuis N jours (MP_EXPIRE_DAYS) → Expirée (rien n'est supprimé)
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
