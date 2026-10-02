"""Suivi des réponses : emails de statut → table Offres (par jobId) et table Candidatures (entonnoir).

Règle de l'entonnoir : Néant (0) → Envoyé (1) → A/R (2) → Oui / Non (3). Un statut ne recule jamais.
Les emails LinkedIn sont lus par des règles déterministes ; les autres (ATS, recruteurs) par Claude
(modèle rapide), uniquement s'ils ressemblent à un échange de candidature.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from mp.config import prompt
from mp.context import Context
from mp.gmail import email_body_text, parse_linkedin_status, query_status_emails
from mp.linkedin import extract_job_id
from mp.models import EMAIL_CLASS_SCHEMA, RESPONSE_RANK, Email, EmailClass, StatusEvent

log = logging.getLogger("mp.tracking")

DENY = {"indigoneo"}
PLATFORMS = re.compile(r"linkedin|welcome to the jungle|greenhouse|lever|workday|teamtailor|smartrecruiters"
                       r"|indeed|glassdoor|jobteaser|hellowork|apec", re.I)
CAT_TO_STATUS = {"Accusé de réception": "A/R", "Réponse positive": "Oui", "Refus": "Non"}


def _deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def norm_company(s: str) -> str:
    s = _deaccent(str(s or "").lower())
    s = re.sub(r"\b(sas|sasu|sa|sarl|inc|ltd|llc|gmbh|plc|group|groupe|technologies|tech|recruitment|recrutement"
               r"|staffing|consulting|conseil|france|luxembourg|uk|europe)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


_RANK_NORM = {_deaccent(k).strip().lower(): v for k, v in RESPONSE_RANK.items()}


def rank(status: str) -> int:
    """Rang d'avancement d'une réponse (0 = rien, 3 = terminal), insensible aux accents et à la casse."""
    return _RANK_NORM.get(_deaccent(str(status or "")).strip().lower(), 0)


# ---------------------------------------------------------------------------
# Candidatures (entonnoir)
# ---------------------------------------------------------------------------


class CandidaturesIndex:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.records = ctx.at.list(ctx.candidatures, fields=["Société", "Poste", "Réponse", "URL", "Job ID",
                                                            "Date postulé", "Date réponse"])
        self.by_job: dict[str, dict] = {}
        self.by_company: dict[str, list[dict]] = {}
        for r in self.records:
            f = r["fields"]
            jid = f.get("Job ID") or extract_job_id(f.get("URL", "") or "")
            if jid:
                self.by_job[jid] = r
            self.by_company.setdefault(norm_company(f.get("Société", "")), []).append(r)
        self.known = {k for k in self.by_company if len(k) >= 4}

    def find(self, job_id: str, company: str, title: str = "") -> dict | None:
        if job_id and job_id in self.by_job:
            return self.by_job[job_id]
        key = norm_company(company)
        if len(key) < 2:
            return None
        hits = self.by_company.get(key) or [r for k, rs in self.by_company.items()
                                            if k and (k in key or key in k) and len(k) >= 4 for r in rs]
        if not hits:
            return None
        if title:
            kt = norm_company(title)
            for r in hits:
                rt = norm_company(r["fields"].get("Poste", ""))
                if kt and rt and (kt == rt or kt in rt or rt in kt):
                    return r
        return hits[0]

    def mentions_known_company(self, text: str) -> str | None:
        hay = norm_company(text)
        for k in sorted(self.known, key=len, reverse=True):
            if k in hay:
                return k
        return None


def upsert_candidature(ctx: Context, *, company: str, title: str, url: str, job_id: str, status: str, day: str,
                       cluster: str | None = None, location: str = "", mode: str = "", note: str = "",
                       email_id: str = "", index: CandidaturesIndex | None = None) -> str:
    """Crée ou avance une candidature. Retourne 'create' / 'update' / 'skip'."""
    index = index or CandidaturesIndex(ctx)
    match = index.find(job_id, company, title)
    date_field = "Date postulé" if status == "Envoyé" else "Date réponse"
    if match:
        current = match["fields"].get("Réponse") or ""
        current = current["name"] if isinstance(current, dict) else current
        if rank(status) <= rank(current):
            return "skip"
        fields = {"Réponse": status, date_field: day}
        if note:
            fields["Note"] = note[:900]
        if email_id:
            fields["Dernier email"] = email_id
        if job_id and not match["fields"].get("Job ID"):
            fields["Job ID"] = job_id
        ctx.at.patch(ctx.candidatures, match["id"], fields)
        match["fields"]["Réponse"] = status
        return "update"
    if norm_company(company) in DENY or len(norm_company(company)) < 2:
        return "skip"
    fields = {"Société": company, "Poste": title, "Réponse": status, date_field: day,
              "Canal": "LinkedIn" if "linkedin.com" in (url or "") else "Direct"}
    if url:
        fields["URL"] = url
    if job_id:
        fields["Job ID"] = job_id
    if cluster:
        fields["Axe"] = cluster
    if location:
        fields["Lieu"] = location
    if mode:
        fields["Mode"] = mode
    if note:
        fields["Note"] = note[:900]
    if email_id:
        fields["Dernier email"] = email_id
    created = ctx.at.create(ctx.candidatures, [fields])
    if created:
        index.records.append(created[0])
        if job_id:
            index.by_job[job_id] = created[0]
        index.by_company.setdefault(norm_company(company), []).append(created[0])
    return "create"


# ---------------------------------------------------------------------------
# Offres (par jobId)
# ---------------------------------------------------------------------------


def update_offer(ctx: Context, job_id: str, status: str, day: str) -> dict | None:
    from mp.airtable import fstr

    if not job_id:
        return None
    found = ctx.at.list(ctx.offres, formula=f"{{jobId}}={fstr(job_id)}", max_records=1,
                        fields=["jobId", "Employeur", "Poste", "Statut", "Réponse", "Date postulé", "Cluster",
                                "Lieu", "Mode", "URL"])
    if not found:
        return None
    rec = found[0]
    f = rec["fields"]
    current = f.get("Réponse") or ""
    current = current["name"] if isinstance(current, dict) else current
    fields = {}
    if status == "Envoyé":
        fields.update({"Statut": "Postulée", "Je postule": True})
        if not f.get("Date postulé"):
            fields["Date postulé"] = day
    if rank(status) > rank(current):
        fields["Réponse"] = status
        if status != "Envoyé":
            fields["Date réponse"] = day
    if fields:
        ctx.at.patch(ctx.offres, rec["id"], fields)
    return rec


# ---------------------------------------------------------------------------
# Emails → événements
# ---------------------------------------------------------------------------


def classify_email(ctx: Context, e: Email) -> EmailClass:
    user = (f"Expéditeur : {e.from_name} <{e.from_email}>\nObjet : {e.subject}\nDate : {e.date}\n"
            f"Corps :\n{email_body_text(e, 2500)}\n\nClasse cet email (JSON).")
    data = ctx.claude.json(system=[prompt("tracking")], user=user, schema=EMAIL_CLASS_SCHEMA, fast=True,
                           max_tokens=400)
    return EmailClass.model_validate(data)


def event_from_email(ctx: Context, e: Email, index: CandidaturesIndex) -> StatusEvent | None:
    if "linkedin.com" in e.domain:
        parsed = parse_linkedin_status(e.subject, e.plain or "")
        if not parsed:
            return None
        return StatusEvent(email_id=e.id, date=e.date, status=parsed["status"], company=parsed["company"],
                           title=parsed["title"], job_id=parsed["job_id"], url=parsed["url"], subject=e.subject,
                           source="linkedin", confidence="Haute")
    body = email_body_text(e, 2500)
    known = index.mentions_known_company(f"{e.from_name} {e.from_email} {e.subject} {body[:800]}")
    has_label = any(lab.lower().startswith("candidatures") for lab in e.labels)
    looks_like = re.search(r"candidature|application|applied|entretien|interview|recrutement|mission", e.subject, re.I)
    if not (known or has_label or looks_like):
        return None
    c = classify_email(ctx, e)
    if c.categorie == "Autre":
        return None
    company = c.societe if c.societe and not PLATFORMS.search(c.societe) else ""
    reliable = bool(company or known)
    if not company and known:
        company = next((r["fields"].get("Société", "") for r in index.records
                        if norm_company(r["fields"].get("Société", "")) == known), "")
    if not company:                     # nom de l'expéditeur : simple indice, à faire valider (comme BOB)
        company = e.from_name if not PLATFORMS.search(e.from_name) else ""
    if c.confiance == "Basse" and not known:
        reliable = False
    return StatusEvent(email_id=e.id, date=e.date, status=CAT_TO_STATUS[c.categorie], company=company,
                       title=c.poste, job_id=extract_job_id(body) or "", subject=e.subject, source="classif",
                       confidence=c.confiance, note=c.justification, reliable=reliable,
                       sender=f"{e.from_name} <{e.from_email}>".strip())


def to_review(ctx: Context, ev: StatusEvent) -> str:
    """Réponse dont la société n'est pas identifiable avec certitude : table « A traiter » (upsert par ID Email),
    comme le faisait BOB, au lieu de l'ignorer ou de créer une candidature au nom de l'expéditeur."""
    fields = {"ID Email": ev.email_id, "Société": ev.company, "Poste": ev.title, "Réponse": ev.status,
              "Sujet": ev.subject[:250], "Expéditeur": ev.sender[:250], "Date": ev.date,
              "Note": (ev.note or "")[:900], "Lien Gmail": f"https://mail.google.com/mail/u/0/#all/{ev.email_id}"}
    ctx.at.upsert(ctx.a_traiter, [{k: v for k, v in fields.items() if v}], merge_on=["ID Email"])
    return "review"


def apply_event(ctx: Context, ev: StatusEvent, index: CandidaturesIndex) -> str:
    if not ev.reliable and not index.find(ev.job_id, ev.company, ev.title):
        return to_review(ctx, ev)
    offer = update_offer(ctx, ev.job_id, ev.status, ev.date)
    company = ev.company or (offer["fields"].get("Employeur", "") if offer else "")
    title = ev.title or (offer["fields"].get("Poste", "") if offer else "")
    url = ev.url or (offer["fields"].get("URL", "") if offer else "")
    cluster = offer["fields"].get("Cluster") if offer else None
    cluster = cluster["name"] if isinstance(cluster, dict) else cluster
    note = f"[{ev.date}] {ev.subject[:80]}" + (f" — {ev.note}" if ev.note else "")
    return upsert_candidature(ctx, company=company, title=title, url=url, job_id=ev.job_id, status=ev.status,
                              day=ev.date, cluster=cluster, note=note, email_id=ev.email_id, index=index)


def track(ctx: Context, days: int = 3) -> list[dict]:
    index = CandidaturesIndex(ctx)
    stats = {"emails": 0, "events": 0, "create": 0, "update": 0, "skip": 0, "review": 0}
    with ctx.gmail() as g:
        done: list[bytes] = []
        for e in g.iter(query_status_emails(days)):
            stats["emails"] += 1
            try:
                ev = event_from_email(ctx, e, index)
                if ev:
                    action = apply_event(ctx, ev, index)
                    stats["events"] += 1
                    stats[action] += 1
                    ctx.report.events.append({"status": ev.status, "company": ev.company, "title": ev.title,
                                              "action": action, "source": ev.source, "date": ev.date})
                    log.info("  %-7s %-6s %-28.28s | %s", ev.status, action, ev.company, ev.title[:50])
                done.append(e.uid)
            except Exception as ex:  # noqa: BLE001 — l'email restera non labellisé et repassera au run suivant
                ctx.report.error(f"suivi KO sur « {e.subject[:60]} » : {ex}")
        if not ctx.dry_run:
            g.mark_done(done)
    log.info("suivi : %s", stats)
    ctx.report.notes.append(f"suivi des réponses : {stats}")
    return ctx.report.events
