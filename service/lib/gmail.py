"""Client du pont Gmail n8n (« Gmail Bridge — fetch (service Mac) », id UXlPuUZcQDcDlPr4).
GET webhook + X-Bridge-Token (gmail-bridge.env). Renvoie les emails des 24 h typés :
  - type "offre_linkedin" : 1 offre/item — subject=poste, from=employeur, + jobId/lieu/url/mode
  - type "email"          : flux BOB (candidatures)
Contrat vérifié en live le 02/07/2026."""
import json
import re
import urllib.request

from .config import load_env

# Emails de confirmation de création d'alerte LinkedIn : parsés à tort en offres
# par le pont (décalage de champs). Détectés sur subject OU from. Cf. bug du 02/07
# (10 records poubelle dans Veille 2, certains scorés).
_JUNK = re.compile(r"votre alerte emploi a été créée|vous recevrez des notifications", re.I)


def _mode() -> str:
    """Interrupteur P4 : 'direct' (IMAP, sans VPS) ou 'bridge' (webhook n8n).
    Lu dans gmail-direct.env ; absent = bridge (comportement historique)."""
    try:
        return load_env("gmail-direct.env", "GMAIL_MODE").lower()
    except Exception:  # noqa: BLE001 — fichier absent = mode historique
        return "bridge"


def fetch_bridge(timeout: int = 120) -> list:
    """Récupère les emails des 24 h, typés (offre_linkedin / email).
    Mode 'direct' : IMAP local (gmail_direct.py). Mode 'bridge' : webhook n8n.
    Lève sur erreur (fail-loud)."""
    if _mode() == "direct":
        from .gmail_direct import fetch_direct
        return fetch_direct(timeout=timeout)
    url = load_env("gmail-bridge.env", "GMAIL_BRIDGE_URL")
    token = load_env("gmail-bridge.env", "GMAIL_BRIDGE_TOKEN")
    req = urllib.request.Request(url, headers={"X-Bridge-Token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def offres(emails: list) -> tuple:
    """Filtre les items offre_linkedin → cartes normalisées, dédup intra-lot par jobId.
    Retourne (cards, stats) avec stats = {"junk": n, "invalid": n, "dup": n}.
    NB : le pont ne renvoie ni easyApply ni alertName (Recherche LI) — Easy Apply est
    détecté à l'enrichissement dans le HTML de la JD (comme n8n)."""
    cards, seen = [], set()
    stats = {"junk": 0, "invalid": 0, "dup": 0}
    for e in emails:
        if e.get("type") != "offre_linkedin":
            continue
        subject = (e.get("subject") or "").strip()
        job_id = str(e.get("jobId") or "").strip()
        if _JUNK.search(subject) or _JUNK.search(e.get("from") or ""):
            stats["junk"] += 1
            continue
        if not job_id or not subject:
            stats["invalid"] += 1
            continue
        if job_id in seen:
            stats["dup"] += 1
            continue
        seen.add(job_id)
        cards.append({
            "jobId": job_id,
            "poste": subject[:200],
            "employeur": (e.get("from") or "").strip()[:120],
            "lieu": (e.get("lieu") or "").strip()[:120],
            "url": e.get("url") or f"https://www.linkedin.com/jobs/view/{job_id}/",
            "mode": (e.get("mode") or "").strip(),
        })
    return cards, stats


def candidatures(emails: list) -> list:
    """Items type 'email' (flux BOB — Étape 2)."""
    return [e for e in emails if e.get("type") == "email"]
