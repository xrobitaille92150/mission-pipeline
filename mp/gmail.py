"""Accès Gmail en IMAP (mot de passe d'application) + parseurs des emails LinkedIn.

Deux familles d'emails nous intéressent :
  1. les digests d'offres (alertes `jobalerts-noreply`, recommandations et offres
     enregistrées `jobs-noreply`) → cartes d'offres (`Card`) ;
  2. les emails de statut d'une candidature (LinkedIn : envoyée / vue / dernière nouvelle,
     ATS et recruteurs : accusé de réception, entretien, refus) → `StatusEvent`.

Les emails traités reçoivent le label Gmail `MissionPipeline` (configurable), ce qui rend
chaque run idempotent sans fenêtre de 24 h fragile.
"""
from __future__ import annotations

import email as email_lib
import imaplib
import re
import smtplib
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

from mp.config import LINKEDIN_ALERTS, LINKEDIN_JOBS
from mp.models import Card, Email

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"

JOB_URL = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d{6,})", re.I)
VIEW_JOB_LINE = re.compile(r"^(?:Voir l.offre d.emploi|View job|See job)\s*:\s*https?://", re.I)

# Lignes « badge » qui s'intercalent entre le lieu et l'URL d'une carte d'offre.
BADGES = re.compile(
    r"^(?:Top candidat|Croissance rapide|Recrutement actif|Cette entreprise recrute activement"
    r"|Soyez parmi les premiers|Postulez avec|Candidature simplifiée|Easy Apply|Reprend contact"
    r"|Forte affinité|Promu|En vedette|Actively recruiting|Be an early applicant|Top applicant"
    r"|Salaire\b|\d+\s+(?:relation|ancien|candidat|employé|connexion|connection|alumni|applicant)"
    r"|Il y a \d+|Posted \d+|\d+\s*(?:k|K)?\s*€|€\s*\d|Vous connaissez|Votre réseau"
    r"|Correspond à vos|Matches your|Nouveau|New\b)",
    re.I,
)
# Lignes d'en-tête de digest qui peuvent se coller à la première carte (bug « Poste = en-tête »).
HEADERS = re.compile(
    r"^(?:Votre alerte Emploi|De nouvelles offres|Une nouvelle offre|Poste de .+ chez"
    r"|.+ recrute au poste de|Voir toutes les offres|Déposez votre candidature|Offres d.emploi"
    r"|Jobs you may|New jobs|Offres recommandées|Gérez vos alertes)",
    re.I,
)
# Labels Gmail posés par les filtres de Xavier (nommage de recherche Gmail : « / » et espace → « - »).
STATUS_LABELS = [
    "Candidatures", "Candidatures-1_Confirmations", "Candidatures-2_R\u00e9ponses-n\u00e9gatives",
    "Candidatures-3_Suivi---En-attente", "Candidatures-4_R\u00e9ponses-positives",
]
EASY_APPLY = re.compile(r"candidature simplifiée|easy apply|postulez avec un cv et un profil", re.I)

DIGEST_SUBJECT_OK = re.compile(
    r"recrute au poste de|offres? d.emploi correspond|^Poste de .+ chez|et plus encore"
    r"|déposez votre candidature|new jobs|jobs? (?:for you|you may)",
    re.I,
)
DIGEST_SUBJECT_KO = re.compile(
    r"candidature a été (?:envoyée|vue|consultée)|votre candidature\s*:|dernière nouvelle"
    r"|application was (?:sent|viewed)|alerte emploi a été créée",
    re.I,
)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def clean_mode(raw: str) -> str:
    m = (raw or "").lower()
    if re.search(r"à\s*distance|\ba\s*distance\b|remote|télétravail|teletravail|full\s*remote", m):
        return "À distance"
    if re.search(r"hybride|hybrid", m):
        return "Hybride"
    if re.search(r"sur\s*(?:site|place)|on[-\s]?site|présentiel|presentiel", m):
        return "Sur site"
    return ""


def split_location_mode(location: str) -> tuple[str, str]:
    """« Paris (Hybride) » → (« Paris », « Hybride »)."""
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", location or "")
    if m:
        mode = clean_mode(m.group(2))
        if mode:
            return m.group(1).strip(), mode
    return (location or "").strip(), clean_mode(location or "")


def _norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace(" ", " ")).strip()


# ---------------------------------------------------------------------------
# Parseur des digests d'offres
# ---------------------------------------------------------------------------


def digest_source(from_email: str, subject: str) -> str | None:
    """Type de digest ou None si l'email n'est pas un digest d'offres."""
    f = (from_email or "").lower()
    s = subject or ""
    if DIGEST_SUBJECT_KO.search(s):
        return None
    if f == LINKEDIN_ALERTS:
        return "Alerte"
    if f == LINKEDIN_JOBS and DIGEST_SUBJECT_OK.search(s):
        if re.search(r"déposez votre candidature|enregistré", s, re.I):
            return "Enregistrée"
        return "Recommandation"
    return None


def parse_job_cards(plain: str, source: str = "Alerte") -> list[Card]:
    """Découpe le corps text/plain d'un digest LinkedIn en cartes d'offres.

    Chaque carte se termine par une ligne « Voir l'offre d'emploi : <URL jobs/view/ID> ».
    On remonte depuis cette ligne pour lire lieu / employeur / poste (3 lignes utiles),
    en ignorant les badges et les en-têtes de digest, et en s'arrêtant à une ligne vide.
    """
    cards: list[Card] = []
    if not plain:
        return cards
    text = plain.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_norm_line(ln) for ln in text.split("\n")]
    alert = re.search(r"Votre alerte Emploi pour\s+(.+)", text, re.I)
    alert_name = _norm_line(alert.group(1))[:200] if alert else ""
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = JOB_URL.search(line)
        if not m or not (VIEW_JOB_LINE.match(line) or line.lower().startswith("http")):
            continue
        job_id = m.group(1)
        if job_id in seen:
            continue
        group: list[str] = []
        easy = False
        j = i - 1
        while j >= 0 and len(group) < 3:
            ln = lines[j]
            j -= 1
            if not ln or re.match(r"^[-_=*\u2014]{3,}$", ln):
                if group:
                    break
                continue
            if JOB_URL.search(ln):
                break
            if re.match(r"^(?:Voir l.offre|View job|See job)", ln, re.I):
                continue
            if EASY_APPLY.search(ln):
                easy = True
            if BADGES.match(ln) or HEADERS.match(ln):
                continue
            group.insert(0, ln)
        if not group:
            continue
        seen.add(job_id)
        title = group[0][:200]
        employer = group[1][:120] if len(group) > 1 else ""
        location, mode = split_location_mode(group[2][:120] if len(group) > 2 else "")
        cards.append(Card(
            job_id=job_id,
            url=f"https://www.linkedin.com/jobs/view/{job_id}/",
            title=title, employer=employer, location=location, mode=mode,
            alert_name=alert_name, easy_apply=easy, source=source,
        ))
    return cards


# ---------------------------------------------------------------------------
# Parseur des emails de statut LinkedIn (règles déterministes)
# ---------------------------------------------------------------------------

STATUS_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"your application was sent to\s+(.+)", re.I), "Envoyé"),
    (re.compile(r"votre candidature a (?:bien )?été envoyée à\s+(.+)", re.I), "Envoyé"),
    (re.compile(r"your application was viewed by\s+(.+)", re.I), "A/R"),
    (re.compile(r"votre candidature a été (?:vue|consultée) par\s+(.+)", re.I), "A/R"),
    (re.compile(r"derni[eè]re nouvelle de\s+(.+)", re.I), "Non"),
    (re.compile(r"update from\s+(.+?)\s+(?:about|on) your application", re.I), "Non"),
]


def _cut_name(s: str) -> str:
    return re.split(r"\s[-|–—:]\s|\n|\bpour\b|\bfor\b", s, maxsplit=1, flags=re.IGNORECASE)[0].strip().strip(".")[:80]


def parse_linkedin_status(subject: str, plain: str) -> dict | None:
    """Retourne {status, company, title, job_id, url} pour un email de statut LinkedIn, sinon None."""
    head = f"{subject}\n{(plain or '')[:600]}"
    for pattern, status in STATUS_RULES:
        m = pattern.search(head)
        if not m:
            continue
        company = _cut_name(m.group(1))
        text = (plain or "").replace("\r\n", "\n")
        um = JOB_URL.search(text)
        job_id = um.group(1) if um else ""
        title = ""
        if um:
            # La carte « Poste / Employeur / Lieu / Voir l'offre » suit l'en-tête de confirmation.
            before = [_norm_line(ln) for ln in text[: um.start()].split("\n")]
            before = [ln for ln in before if ln and not BADGES.match(ln) and not VIEW_JOB_LINE.match(ln)]
            if len(before) >= 3:
                title = before[-3][:200]
        return {
            "status": status, "company": company, "title": title, "job_id": job_id,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else "",
        }
    return None


# ---------------------------------------------------------------------------
# Client IMAP / SMTP
# ---------------------------------------------------------------------------


def _decode_hdr(raw) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:  # noqa: BLE001 — en-tête mal formé : on garde le brut
        return str(raw).strip()


def _bodies(msg) -> tuple[str, str]:
    plain, html = [], []
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        (plain if ctype == "text/plain" else html).append(text)
    return "\n".join(plain), "\n".join(html)


def html_to_text(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
            .replace("&rsquo;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def email_body_text(e: Email, limit: int = 2500) -> str:
    body = e.plain or html_to_text(e.html)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > limit:
        body = body[: int(limit * 0.72)] + " […] " + body[-int(limit * 0.28):]
    return body


class Gmail:
    """Session IMAP sur « Tous les messages » + envoi SMTP, avec le mot de passe d'application."""

    def __init__(self, user: str, app_password: str, label_done: str = "MissionPipeline"):
        self.user = user
        self.pwd = app_password.replace(" ", "")
        self.label_done = label_done
        self._imap: imaplib.IMAP4_SSL | None = None

    # -- session -----------------------------------------------------------
    def __enter__(self) -> Gmail:
        self._imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=120)
        self._imap.login(self.user, self.pwd)
        self._ensure_label()
        self._imap.select(f'"{self._all_mail()}"', readonly=False)
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._imap:
                self._imap.logout()
        except Exception:  # noqa: BLE001
            pass

    def _all_mail(self) -> str:
        typ, boxes = self._imap.list()
        for raw in boxes or []:
            line = raw.decode("utf-8", errors="replace")
            if "\\All" in line:
                mm = re.search(r'"([^"]+)"\s*$', line)
                if mm:
                    return mm.group(1)
        return "INBOX"

    def _ensure_label(self) -> None:
        try:
            typ, boxes = self._imap.list()
            names = [b.decode("utf-8", errors="replace") for b in (boxes or [])]
            if not any(f'"{self.label_done}"' in n or n.endswith(f" {self.label_done}") for n in names):
                self._imap.create(self.label_done)
        except Exception:  # noqa: BLE001 — le label existe déjà ou droits insuffisants
            pass

    # -- lecture -------------------------------------------------------------
    def search(self, gmail_query: str) -> list[bytes]:
        typ, data = self._imap.uid("SEARCH", "X-GM-RAW", f'"{gmail_query}"')
        if typ != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def fetch(self, uid: bytes) -> Email | None:
        typ, fetched = self._imap.uid("fetch", uid, "(X-GM-MSGID X-GM-LABELS RFC822)")
        if typ != "OK" or not fetched or fetched[0] is None or not isinstance(fetched[0], tuple):
            return None
        meta = fetched[0][0].decode("utf-8", errors="replace")
        gm = re.search(r"X-GM-MSGID\s+(\d+)", meta)
        email_id = format(int(gm.group(1)), "x") if gm else uid.decode()
        lm = re.search(r"X-GM-LABELS\s+\((.*?)\)", meta)
        labels = re.findall(r'"([^"]+)"|(\S+)', lm.group(1)) if lm else []
        labels = [a or b for a, b in labels]
        msg = email_lib.message_from_bytes(fetched[0][1])
        subject = _decode_hdr(msg.get("Subject"))
        from_raw = _decode_hdr(msg.get("From"))
        fm = re.search(r"<([^>]+)>", from_raw)
        from_email = (fm.group(1) if fm else from_raw).strip().lower()
        from_name = re.sub(r"<[^>]+>", "", from_raw).strip().strip('"') or from_email
        try:
            dt = parsedate_to_datetime(msg.get("Date"))
            date = dt.astimezone(UTC).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            date = datetime.now(UTC).strftime("%Y-%m-%d")
        plain, html = _bodies(msg)
        return Email(uid=uid, id=email_id, subject=subject, from_name=from_name, from_email=from_email,
                     date=date, plain=plain, html=html, labels=labels)

    def iter(self, gmail_query: str, exclude_done: bool = True):
        q = gmail_query
        if exclude_done:
            q = f"{q} -label:{self.label_done}"
        for uid in self.search(q):
            e = self.fetch(uid)
            if e:
                yield e

    def mark_done(self, uids: list[bytes]) -> None:
        for uid in uids:
            try:
                self._imap.uid("STORE", uid, "+X-GM-LABELS", f'("{self.label_done}")')
            except Exception:  # noqa: BLE001 — non bloquant : l'idempotence est aussi assurée côté Airtable
                pass

    # -- envoi ---------------------------------------------------------------
    def send(self, subject: str, text: str, html: str | None = None,
             sender: str | None = None, to: str | None = None) -> None:
        sender = sender or self.user
        to = to or self.user
        if html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(text, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
        else:
            msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=60) as s:
            s.login(self.user, self.pwd)
            s.sendmail(sender, [to], msg.as_string())


# ---------------------------------------------------------------------------
# Requêtes prêtes à l'emploi
# ---------------------------------------------------------------------------


def query_job_digests(days: int) -> str:
    return (f"newer_than:{days}d (from:{LINKEDIN_ALERTS} OR "
            f"(from:{LINKEDIN_JOBS} -subject:candidature -subject:application))")


def query_status_emails(days: int) -> str:
    # LinkedIn (statuts), labels Candidatures/* posés par les filtres Gmail de Xavier,
    # et tout email mentionnant « candidature » / « application » dans l'objet.
    labels = " OR ".join(f"label:{lab}" for lab in STATUS_LABELS)
    return (f"newer_than:{days}d -from:{LINKEDIN_ALERTS} "
            f"((from:{LINKEDIN_JOBS} (subject:candidature OR subject:application)) "
            f"OR {labels} OR subject:candidature OR subject:application OR subject:applied "
            f"OR subject:entretien OR subject:interview OR subject:mission OR subject:recrutement)")


def cards_from_email(e: Email) -> list[Card]:
    source = digest_source(e.from_email, e.subject)
    if not source:
        return []
    plain = e.plain or html_to_text(e.html)
    cards = parse_job_cards(plain, source=source)
    for c in cards:
        c.email_id = e.id
        c.email_date = e.date
    return cards
