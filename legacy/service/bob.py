#!/usr/bin/env python3
"""
BOB — triage des emails de candidature (port déterministe du flux n8n BOB).

Pont Gmail (items type "email") → classification Haiku 5 catégories (UN SEUL appel,
comme le nœud "Prépa Triage BOB") → règles déterministes LinkedIn (Envoyé/A-R/Non)
→ matching contre Candidatures (port de "Matcher & décider") → décision
create / update / skip / review.

Écritures (mode --write) :
  - Email Triage  : upsert par ID Email (log de classification, tous les emails)
  - Candidatures  : create (nouvelle candidature) / update Réponse (entonnoir
                    Néant→Envoyé→A/R→Oui/Non, jamais de rétrogradation)
  - A traiter     : upsert par ID Email (société non identifiable → revue manuelle)

Limites du pont (items email = {id_email, from, subject, body, type}) :
  - pas de date d'email → Date = date du jour (fenêtre 24h, dérive max 1 jour)
  - pas de payload HTML → URL/Lieu/Mode LinkedIn extraits du body texte (dégradé)

Modes :
  python3 bob.py             # dry-run : classifie + décide, N'ÉCRIT RIEN
  python3 bob.py --write     # écrit Email Triage + Candidatures + A traiter
"""
import datetime
import json
import re
import sys
import unicodedata

from lib import config as C
from lib import claude, airtable, gmail

DENY = {"indigoneo"}
RANK = {"": 0, "neant": 0, "envoye": 1, "a/r": 2, "ar": 2, "oui": 3, "non": 3}

# Catégorie BOB -> {rec, reponse} (port du mapping CAT de "Matcher & décider")
CAT = {
    "Accusé de réception":    {"rec": True, "reponse": "A/R"},
    "Réponse positive":       {"rec": True, "reponse": "Oui"},
    "Refus":                  {"rec": True, "reponse": "Non"},
    "Proposition de mission": {"rec": False},
    "Autre/non-pertinent":    {"rec": False},
}
VALID_CAT = list(CAT.keys())
VALID_CONF = ["Haute", "Moyenne", "Basse"]

SYSTEM = """Tu es BOB, l'agent de Xavier Robitaille. Ton rôle : trier les emails liés à sa recherche de mission de conseil.
Pour CHAQUE email, attribue EXACTEMENT UNE catégorie :
- "Proposition de mission" : un recruteur/client propose une mission ou un poste précis.
- "Accusé de réception" : confirmation (souvent automatique) qu'une candidature a bien été reçue/envoyée, SANS décision.
- "Réponse positive" : intérêt, demande d'entretien/échange/disponibilités, suite favorable.
- "Refus" : candidature non retenue (« ne donnerons pas suite », « pas retenu », « unfortunately »).
- "Autre/non-pertinent" : newsletter, alerte non sollicitée, authentification/code, bienvenue/création de compte, invitation à une conférence/webinaire, notif bancaire, spam — tout ce qui n'est pas lié à une candidature précise.
Extrais la société (l'entreprise qui recrute, jamais l'ATS/plateforme : ignore LinkedIn, Welcome to the Jungle, Greenhouse, Lever, Workday) et le poste quand ils sont identifiables, sinon laisse vide.
Un accusé de réception n'est ni un refus ni une réponse positive. En cas de doute entre les trois (si c'est bien du recrutement) → "Accusé de réception"."""


# ---------- Normalisation / parsing (ports JS → Python) ----------
def _deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def normalize(s: str) -> str:
    s = _deaccent(str(s or "").lower())
    s = re.sub(r"\b(sas|sasu|sa|sarl|inc|ltd|llc|gmbh|group|groupe|technologies|tech|recruitment|recrutement|staffing)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def rank_of(rp: str) -> int:
    return RANK.get(_deaccent(str(rp or "").strip().lower()), 0)


def parse_from(from_raw: str) -> tuple:
    """'Nom <addr@dom>' → (email, domaine)."""
    m = re.search(r"<([^>]+)>", from_raw or "")
    email = (m.group(1) if m else (from_raw or "")).strip().lower()
    domain = email.split("@")[1] if "@" in email else ""
    return email, domain


def cut_name(s: str) -> str:
    return re.split(r"\s[-|–—:]\s|\n|\bpour\b|\bfor\b", s, 1, flags=re.I)[0].strip()[:60]


def forced_rules(subject: str, body: str, from_email: str) -> tuple:
    """Règles déterministes LinkedIn → (reponse, societe) ou (None, None)."""
    if "linkedin.com" not in (from_email.split("@")[1] if "@" in from_email else ""):
        return None, None
    head = f"{subject}\n{(body or '')[:400]}"
    for pat, rep in [
        (r"your application was sent to\s+(.+)", "Envoyé"),
        (r"votre candidature a été envoyée à\s+(.+)", "Envoyé"),
        (r"candidature(?:\s+a(?:\s+bien)?\s+été)?\s+envoyée\s+à\s+(.+)", "Envoyé"),
        (r"your application was viewed by\s+(.+)", "A/R"),
        (r"candidature a été consultée par\s+(.+)", "A/R"),
        (r"derni[eè]re nouvelle de\s+(.+)", "Non"),
    ]:
        m = re.search(pat, head, re.I)
        if m:
            return rep, cut_name(m.group(1))
    return None, None


def find_linkedin_url(text: str) -> str:
    m = re.search(r"https?://(?:[\w.-]+\.)?linkedin\.com/(?:comm/)?jobs/view/(\d+)", text or "", re.I)
    return f"https://www.linkedin.com/jobs/view/{m.group(1)}/" if m else ""


# ---------- Classification (port de "Prépa Triage BOB" + "Parser Triage BOB") ----------
def classify(emails: list) -> dict:
    """UN appel Haiku pour tous les emails. Retourne {id_email: {categorie, societe,
    poste, confiance, justification}}. Lève ClaudeError si l'appel échoue (fail-loud :
    on n'écrit rien plutôt que de tout classer 'Autre')."""
    if not emails:
        return {}
    lignes = "\n\n".join(
        f"### Email {i + 1}\nExpéditeur : {e.get('from', '')}\nSujet : {e.get('subject', '')}\n"
        f"Corps : {str(e.get('body', ''))[:1500]}"
        for i, e in enumerate(emails))
    prompt = (f"{SYSTEM}\n\nClasse CHAQUE email ci-dessous. Réponds UNIQUEMENT par un tableau JSON, "
              'un objet par email, DANS L\'ORDRE, sans texte autour :\n'
              '[{"i":1,"categorie":"<une des 5>","societe":"","poste":"","confiance":"Haute|Moyenne|Basse","justification":"<=15 mots"}]\n\n'
              f"EMAILS :\n{lignes}")
    txt = claude.call(C.MODEL_SCORE, prompt, max_tokens=min(16000, 120 * len(emails) + 1000), temperature=0)
    by_i = {}
    for frag in re.findall(r"\{[^{}]*\}", txt):
        try:
            o = json.loads(frag)
            if o.get("i") is not None:
                by_i[int(o["i"])] = o
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    out = {}
    for i, e in enumerate(emails):
        o = by_i.get(i + 1)
        if not o:
            continue  # réponse tronquée → repassera au run suivant (fenêtre 24h)
        out[e["id_email"]] = {
            "categorie": o.get("categorie") if o.get("categorie") in VALID_CAT else "Autre/non-pertinent",
            "societe": o.get("societe") or "",
            "poste": o.get("poste") or "",
            "confiance": o.get("confiance") if o.get("confiance") in VALID_CONF else "Basse",
            "justification": o.get("justification") or "",
        }
    return out


# ---------- Matching (port de "Matcher & décider") ----------
def find_match(indexed: list, societe: str, hay_text: str, domain: str):
    key_s = normalize(societe)
    if len(key_s) > 1:
        for r in indexed:
            if r["key"] and (r["key"] == key_s or key_s in r["key"] or r["key"] in key_s):
                return r
    hay = normalize(hay_text)
    if hay:
        hits = sorted((r for r in indexed if r["key"] and len(r["key"]) >= 4 and r["key"] in hay),
                      key=lambda r: -len(r["key"]))
        if hits:
            return hits[0]
    key_d = normalize((domain or "").split(".")[0])
    if len(key_d) > 2:
        for r in indexed:
            if r["key"] and (key_d in r["key"] or r["key"] in key_d):
                return r
    return None


def decide(emails: list, classif: dict) -> list:
    """Retourne la liste des décisions (dicts) : action create/update/skip/review + payload."""
    cands = airtable.list_records(C.T_CANDIDATURES, fields=["Société", "Poste", "Réponse"])
    indexed = [{"id": r["id"],
                "societe": r["fields"].get("Société", ""),
                "reponse": (r["fields"].get("Réponse") or ""),
                "key": normalize(r["fields"].get("Société", ""))} for r in cands]
    today = datetime.date.today().isoformat()
    out = []
    for e in emails:
        subject, body = e.get("subject", ""), e.get("body", "")
        from_email, domain = parse_from(e.get("from", ""))
        bob = classif.get(e["id_email"], {})
        cat = CAT.get(bob.get("categorie"), {"rec": False})
        f_rep, f_soc = forced_rules(subject, body, from_email)
        if not f_rep and cat["rec"] is not True:
            continue
        if f_soc:
            societe, reliable = f_soc, True
        elif bob.get("societe"):
            societe, reliable = bob["societe"], True
        else:
            societe, reliable = (domain.split(".")[0] if domain else ""), False
        if normalize(societe) in DENY:
            continue
        reponse = f_rep or cat.get("reponse", "A/R")
        match = find_match(indexed, societe, f"{subject} {body} {e.get('from', '')}", domain)
        if match:
            action, at_id = ("update" if rank_of(reponse) > rank_of(match["reponse"]) else "skip"), match["id"]
        elif reliable and len(normalize(societe)) >= 2:
            action, at_id = "create", None
        else:
            action, at_id = "review", None
        out.append({"action": action, "airtableId": at_id, "societe": societe,
                    "poste": bob.get("poste", ""), "reponse": reponse, "date": today,
                    "note": bob.get("justification", ""), "subject": subject,
                    "fromRaw": e.get("from", ""), "emailId": e["id_email"],
                    "gmailLink": f"https://mail.google.com/mail/u/0/#all/{e['id_email']}",
                    "linkedinUrl": find_linkedin_url(body), "forced": bool(f_rep),
                    "existing": match["reponse"] if match else None})
    # Dédoublonnage intra-run (create/update ; review jamais — l'upsert gère le cross-run)
    best, reviews = {}, []
    for o in out:
        if o["action"] == "review":
            reviews.append(o)
            continue
        k = ("soc:" + normalize(o["societe"])) if o["action"] == "create" else ("id:" + str(o["airtableId"]))
        if k not in best or rank_of(o["reponse"]) > rank_of(best[k]["reponse"]):
            best[k] = o
    return list(best.values()) + reviews


# ---------- Écritures ----------
def write_all(decisions: list, emails: list, classif: dict) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stats = {"triage": 0, "created": 0, "updated": 0, "review": 0}
    # 1. Email Triage (log, tous les emails classifiés)
    rows = [{"ID Email": eid, "Sujet": next((e["subject"] for e in emails if e["id_email"] == eid), ""),
             "Expéditeur": next((e["from"] for e in emails if e["id_email"] == eid), ""),
             "Catégorie": c["categorie"], "Société": c["societe"], "Poste": c["poste"],
             "Confiance": c["confiance"], "Justification": c["justification"],
             "Date traitement": now,
             "URL Email": f"https://mail.google.com/mail/u/0/#all/{eid}"}
            for eid, c in classif.items()]
    if rows:
        cr, up = airtable.upsert(C.T_TRIAGE, rows, ["ID Email"])
        stats["triage"] = cr + up
    # 2. Candidatures
    for d in decisions:
        if d["action"] == "create":
            f = {"Société": d["societe"], "Poste": d["poste"], "Réponse": d["reponse"], "Note": d["note"]}
            f["Date postulé" if d["reponse"] == "Envoyé" else "Date réponse"] = d["date"]
            if d["linkedinUrl"]:
                f["URL"] = d["linkedinUrl"]
            airtable.create(C.T_CANDIDATURES, [f])
            stats["created"] += 1
        elif d["action"] == "update":
            f = {"Réponse": d["reponse"], "Note": d["note"]}
            f["Date postulé" if d["reponse"] == "Envoyé" else "Date réponse"] = d["date"]
            airtable.patch(C.T_CANDIDATURES, d["airtableId"], f)
            stats["updated"] += 1
    # 3. A traiter (review)
    reviews = [{"Société": d["societe"], "Poste": d["poste"], "Sujet": d["subject"],
                "Réponse": d["reponse"], "Expéditeur": d["fromRaw"], "Date": d["date"],
                "Note": d["note"], "Lien Gmail": d["gmailLink"], "ID Email": d["emailId"]}
               for d in decisions if d["action"] == "review"]
    if reviews:
        cr, up = airtable.upsert(C.T_A_TRAITER, reviews, ["ID Email"])
        stats["review"] = cr + up
    return stats


def main():
    write = "--write" in sys.argv[1:]
    emails = gmail.candidatures(gmail.fetch_bridge())
    print(f"BOB{'' if write else ' (dry-run)'} — {len(emails)} email(s) à classifier")
    classif = classify(emails)
    n_rec = sum(1 for c in classif.values() if CAT.get(c["categorie"], {}).get("rec"))
    print(f"Classifiés : {len(classif)}/{len(emails)} | recrutement : {n_rec}\n")
    decisions = decide(emails, classif)
    for d in decisions:
        arrow = f" (était {d['existing']})" if d["action"] in ("update", "skip") and d["existing"] else ""
        print(f"  [{d['action']:6}] {d['societe']:26.26} | {d['reponse']:6} | {d['subject']:44.44}"
              f"{arrow}{' [forcé]' if d['forced'] else ''}")
    if not decisions:
        print("  (aucune action candidature)")
    if write:
        stats = write_all(decisions, emails, classif)
        print(f"\nÉCRIT — triage={stats['triage']} créées={stats['created']} "
              f"màj={stats['updated']} à-traiter={stats['review']}")
    else:
        acts = {a: sum(1 for d in decisions if d["action"] == a) for a in ("create", "update", "skip", "review")}
        print(f"\nDRY-RUN — create={acts['create']} update={acts['update']} "
              f"skip={acts['skip']} review={acts['review']} (rien écrit)")


if __name__ == "__main__":
    main()
