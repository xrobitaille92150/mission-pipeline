"""Accès Gmail DIRECT — IMAP (lecture) + SMTP (envoi) — sans pont n8n (P4, 28/07/2026).

Port fidèle des nœuds « Filtrer & préparer » + « → format MA » du pont
UXlPuUZcQDcDlPr4. Sortie STRICTEMENT identique au pont :
  - type "offre_linkedin" : 1 offre/item — subject=poste, from=employeur,
    + jobId/lieu/url/mode ; id_email = "li-<jobId>"
  - type "email"          : {id_email, from, subject, body, type}
    id_email = id Gmail API (hex de X-GM-MSGID) → compatible avec l'historique
    Email Triage (aucun doublon au changement de circuit).

Secrets : ~/.config/mission-pipeline/gmail-direct.env
    GMAIL_MODE=direct|bridge      (interrupteur lu par gmail.py / notify.py)
    GMAIL_USER=xrobitaille92150@gmail.com
    GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx   (mot de passe d'application Google)
    DIGEST_FROM=xro@xavier-robitaille.com (alias d'envoi enregistré dans Gmail)
    DIGEST_TO=xrobitaille92150@gmail.com
"""
import email as email_lib
import imaplib
import re
import smtplib
from email.header import decode_header, make_header
from email.mime.text import MIMEText

from .config import load_env

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"

# ── Helpers portés du nœud « Filtrer & préparer » ──────────────────────────────

VEILLE_BADGES = re.compile(
    r"^(Top candidat|Croissance rapide|Recrutement actif|Cette entreprise recrute activement"
    r"|Soyez parmi les premiers|Postulez avec|Candidature simplifiée|Easy Apply|Reprend contact"
    r"|Forte affinité|Promu|En vedette|Actively recruiting|Be an early applicant|Top applicant"
    r"|Salaire\b|\d+\s+relation)", re.I)

_JOB_URL = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I)


def clean_mode(raw: str) -> str:
    m = (raw or "").lower()
    if re.search(r"(à\s*distance|\ba\s*distance\b|remote|télétravail|teletravail|full\s*remote)", m):
        return "À distance"
    if re.search(r"(hybride|hybrid)", m):
        return "Hybride"
    if re.search(r"(sur\s*(?:site|place)|on[-\s]?site|présentiel|presentiel)", m):
        return "Sur site"
    return ""


def parse_jobalert_digest(plain: str) -> list:
    """Découpe le digest text/plain jobalerts en cartes (1 par jobId, dédup intra-email)."""
    cards, seen = [], set()
    if not plain:
        return cards
    am = re.search(r"Votre alerte Emploi pour\s+(.+)", plain, re.I)
    alert_name = am.group(1).strip()[:200] if am else ""
    for block in re.split(r"\n[ \t]*-{3,}[ \t]*\n", plain):
        jm = _JOB_URL.search(block)
        if not jm:
            continue
        job_id = jm.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        lines = []
        for line in block.split("\n"):
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            if re.match(r"^Votre alerte Emploi pour", line, re.I):
                continue
            if re.match(r"^Voir l.offre d.emploi\s*:", line, re.I):
                continue
            if VEILLE_BADGES.match(line):
                continue
            lines.append(line)
        poste = (lines[0] if len(lines) > 0 else "")[:200]
        employeur = (lines[1] if len(lines) > 1 else "")[:120]
        lieu = (lines[2] if len(lines) > 2 else "")[:120]
        mode = ""
        lp = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", lieu)
        if lp:
            m = clean_mode(lp.group(2))
            if m:
                mode, lieu = m, lp.group(1).strip()
        if not mode:
            mode = clean_mode(lieu)
        cards.append({"jobId": job_id, "url": url, "employeur": employeur,
                      "poste": poste, "lieu": lieu, "mode": mode, "alertName": alert_name})
    return cards


def _decode_hdr(raw) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:  # noqa: BLE001
        return str(raw).strip()


def _bodies(msg) -> tuple:
    """Retourne (plain, html) concaténés de toutes les parties."""
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
        text = payload.decode(charset, errors="replace")
        # IMAP livre les corps en CRLF — normaliser, sinon le découpage du digest
        # jobalerts (séparateurs \n---\n) ne voit que la première offre par email.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        (plain if ctype == "text/plain" else html).append(text)
    return "\n".join(plain), "\n".join(html)


def _clean_body(plain: str, html: str) -> str:
    body = plain or html or ""
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > 2500:
        body = body[:1800] + " […] " + body[-700:]
    return body


# ── Lecture (remplace le webhook fetch) ────────────────────────────────────────

def _find_all_mail(m: imaplib.IMAP4_SSL) -> str:
    """Trouve le dossier « Tous les messages » (nom localisé) via l'attribut \\All."""
    typ, boxes = m.list()
    for raw in boxes or []:
        line = raw.decode("utf-8", errors="replace")
        if "\\All" in line:
            mm = re.search(r'"([^"]+)"\s*$', line)
            if mm:
                return mm.group(1)
    return "INBOX"  # dégradé : les emails du jour y sont


def fetch_direct(timeout: int = 120) -> list:
    """Lit les emails des 24 h en IMAP et renvoie la même structure que le pont n8n.
    Lève sur erreur (fail-loud)."""
    user = load_env("gmail-direct.env", "GMAIL_USER")
    pwd = load_env("gmail-direct.env", "GMAIL_APP_PASSWORD").replace(" ", "")
    out = []
    m = imaplib.IMAP4_SSL(IMAP_HOST, timeout=timeout)
    try:
        m.login(user, pwd)
        m.select(f'"{_find_all_mail(m)}"', readonly=True)
        # X-GM-RAW = syntaxe de recherche Gmail native → sémantique identique au pont
        typ, data = m.uid("SEARCH", "X-GM-RAW", '"newer_than:1d"')
        uids = (data[0] or b"").split()
        for uid in uids:
            typ, fetched = m.uid("fetch", uid, "(X-GM-MSGID RFC822)")
            if typ != "OK" or not fetched or fetched[0] is None:
                continue
            meta = fetched[0][0].decode("utf-8", errors="replace")
            gm = re.search(r"X-GM-MSGID\s+(\d+)", meta)
            email_id = format(int(gm.group(1)), "x") if gm else uid.decode()
            msg = email_lib.message_from_bytes(fetched[0][1])

            subject = _decode_hdr(msg.get("Subject"))
            from_raw = _decode_hdr(msg.get("From"))
            fm = re.search(r"<([^>]+)>", from_raw)
            from_email = (fm.group(1) if fm else from_raw).strip().lower()
            plain, html = _bodies(msg)

            if from_email == "jobalerts-noreply@linkedin.com":
                for c in parse_jobalert_digest(plain):
                    out.append({
                        "id_email": "li-" + c["jobId"],
                        "from": c["employeur"] or "LinkedIn",
                        "subject": c["poste"],
                        "body": " · ".join(x for x in [c["poste"], c["employeur"], c["lieu"],
                                                       c["mode"], c["alertName"], c["url"]] if x),
                        "type": "offre_linkedin",
                        "jobId": c["jobId"], "lieu": c["lieu"],
                        "url": c["url"], "mode": c["mode"],
                    })
                continue

            out.append({
                "id_email": email_id,
                "from": from_raw,
                "subject": subject,
                "body": _clean_body(plain, html),
                "type": "email",
            })
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


# ── Envoi (remplace le webhook digest) ─────────────────────────────────────────

def send_smtp(subject: str, text: str, timeout: int = 60) -> None:
    """Envoie le digest en SMTP direct. Lève sur erreur (le fallback vit dans notify.py)."""
    user = load_env("gmail-direct.env", "GMAIL_USER")
    pwd = load_env("gmail-direct.env", "GMAIL_APP_PASSWORD").replace(" ", "")
    sender = load_env("gmail-direct.env", "DIGEST_FROM")
    to = load_env("gmail-direct.env", "DIGEST_TO")
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    with smtplib.SMTP_SSL(SMTP_HOST, 465, timeout=timeout) as s:
        s.login(user, pwd)
        s.sendmail(sender, [to], msg.as_string())


# ── Test manuel : python3 -m lib.gmail_direct ──────────────────────────────────
if __name__ == "__main__":
    emails = fetch_direct()
    offres = [e for e in emails if e["type"] == "offre_linkedin"]
    autres = [e for e in emails if e["type"] == "email"]
    print(f"DIRECT — total={len(emails)} offres={len(offres)} emails={len(autres)}")
    for o in offres[:5]:
        print(f"  offre : {o['from']:30.30} | {o['subject']:50.50} | {o['jobId']}")
    for e in autres[:5]:
        print(f"  email : {e['from']:40.40} | {e['subject']:50.50}")
