#!/usr/bin/env python3
"""
apply_tool.py — Outil "Postuler proprement" (app web locale).

Colle une URL LinkedIn OU le texte d'une offre → génère les 2 notes IA, le rating,
le CV et la Cover Letter (réutilise MATT / run_dossiers.py), puis bouton « Je postule »
qui crée la ligne dans la table Candidatures.

Lancement :
    python3 apply_tool.py            # puis ouvrir http://localhost:8765
    PORT=9000 python3 apply_tool.py  # autre port

Zéro dépendance hors de celles de MATT (requests, python-docx, anthropic) + pandoc/Chrome.
"""
import os, sys, json, re, datetime, traceback, unicodedata, subprocess, tempfile, shutil, uuid, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# ── Charger les secrets AVANT d'importer MATT (qui exige les deux clés) ──────────
def _load(envf):
    p = os.path.expanduser(envf)
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
_load("~/.config/mission-pipeline/anthropic.env")
_load("~/.config/mission-pipeline/airtable.env")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_dossiers as matt   # réutilise claude(), select_cv(), fetch_linkedin(), apply_cv_edits(), docx_to_pdf(), text_to_pdf(), CV_FILES, etc.
import requests

PORT          = int(os.environ.get("PORT", "8765"))
DOSSIERS_SH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run-dossiers.sh")
DOSSIERS_LOG  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")

# ── État MATT (run manuel) ────────────────────────────────────────────────────
_matt_proc   = None   # subprocess.Popen en cours
_matt_start  = None   # datetime du lancement
AIRTABLE_BASE = "apphTpnW5vu0OdnfC"
CAND_TABLE    = "tblF3jpncEXA647ou"   # Candidatures
BAREME_URL    = "https://raw.githubusercontent.com/xrobitaille92150/mission-pipeline/main/nodes/scoring-bareme-prompt.txt"
PROFIL = ("Profil candidat : consultant senior independant, 25 ans d'experience, 100% assurance & finance. "
          "Cherche mission freelance/contract/interim (CDI senior finance possible), full remote ou hybride "
          "privilegie, TJM >= 1000 EUR/j. Ouvert a l'international en remote (EMEA, Amerique du Nord, APAC).")

def at_headers():
    return {"Authorization": f"Bearer {os.environ['AIRTABLE_PAT']}", "Content-Type": "application/json"}

# ── Étapes ──────────────────────────────────────────────────────────────────────
def extract_jobid(text):
    m = re.search(r'(?:jobs/view/|currentJobId=|jobPosting/)(\d{6,})', text)
    return m.group(1) if m else None

def get_jd(raw):
    """Retourne (jd_text, url, jobid). raw = URL LinkedIn ou texte d'offre."""
    raw = raw.strip()
    if re.match(r'https?://', raw) and 'linkedin.com' in raw:
        jid = extract_jobid(raw)
        jd = matt.fetch_linkedin(jid) if jid else None
        url = f"https://www.linkedin.com/jobs/view/{jid}/" if jid else raw
        return (jd or ""), url, jid
    # sinon : texte d'offre collé
    return raw, "", None

def extract_meta(jd):
    """Employeur / poste / lieu depuis le JD (Claude)."""
    prompt = (f"Voici une offre d'emploi. Extrais l'employeur, l'intitulé du poste et le lieu.\n\n"
              f"OFFRE :\n{jd[:3000]}\n\n"
              f'Réponds UNIQUEMENT en JSON : {{"employeur":"...","poste":"...","lieu":"..."}}')
    try:
        m = re.search(r'\{[\s\S]*\}', matt.claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=300))
        o = json.loads(m.group()) if m else {}
    except Exception:
        o = {}
    return o.get("employeur", "") or "Inconnu", o.get("poste", ""), o.get("lieu", "")

def make_notes(employeur, poste, lieu, jd):
    prompt = (f"{PROFIL}\n\nVoici une offre d'emploi. Redige DEUX notes courtes en francais, factuelles, "
              f"pour aider ce candidat a decider de postuler.\n\n"
              f"OFFRE : {poste} — {employeur} — {lieu or 'lieu n.c.'}\n"
              f"DESCRIPTION :\n{(jd or '')[:6000]}\n\n"
              f"Reponds UNIQUEMENT avec un objet JSON, sans texte autour :\n"
              f'{{"note_role":"3 a 5 phrases : de quoi parle le poste, missions cles, seniorite, et en quoi '
              f'il matche ou non le profil sur le FOND du role","note_criteres":"3 a 5 puces separees par des '
              f"retours a la ligne, chaque ligne prefixee '- ' : pour chaque critere cle du recruteur (seniorite, "
              f'type de contrat, secteur, competences, langue, localisation/remote) indique match ou ecart vs le profil"}}')
    try:
        txt = matt.claude(prompt, model="claude-sonnet-4-6", max_tokens=1500)
        o = json.loads(re.search(r'\{[\s\S]*\}', txt).group())
        return o.get("note_role", ""), o.get("note_criteres", "")
    except Exception as e:
        return f"(note rôle indisponible : {e})", ""

_BAREME = None
def fetch_bareme():
    global _BAREME
    if _BAREME is None:
        try:
            _BAREME = requests.get(BAREME_URL, timeout=20).text[:6000]
        except Exception:
            _BAREME = ""
    return _BAREME

def make_score(employeur, poste, lieu, note_role, note_crit, jd):
    bareme = fetch_bareme()
    hasdesc = jd and len(jd) > 100
    descpart = ("\nDescription (extrait, ≤4000 c.) :\n" + jd[:4000]) if hasdesc else \
               "\n(Fiche de poste non disponible — score basé sur titre + employeur + lieu uniquement)"
    notepart = ("\n\nAnalyse Sonnet du rôle :\n" + (note_role or "") +
                "\n\nCritères recruteur :\n" + (note_crit or "")) if (note_role or note_crit) else \
               "\n(Pas d'analyse Sonnet disponible)"
    prompt = (bareme + "\n\nOFFRE À ÉVALUER :\n"
              f"Poste : {poste or '(non renseigné)'}\n"
              f"Employeur : {employeur or '(non renseigné)'}\n"
              f"Lieu : {lieu or '(non renseigné)'}"
              + notepart + descpart +
              '\n\nRéponds UNIQUEMENT en JSON : {"score": <entier 0-100>, "justification": "1-2 phrases", "signaux": ["..."]}')
    try:
        txt = matt.claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=800)
        o = json.loads(re.search(r'\{[\s\S]*\}', txt).group())
        raw = o.get("score")
        score = max(0, min(100, int(raw))) if raw is not None else None
        return score, o.get("justification", ""), o.get("signaux", [])
    except Exception as e:
        return None, f"(scoring indisponible : {e})", []

# ── PDF parallèle-safe (chemins temp + profil Chrome uniques par appel) ─────────
def _docx_to_pdf_safe(docx_path, pdf_path):
    d = tempfile.mkdtemp(prefix="apply_pdf_")
    try:
        css, html = os.path.join(d, "style.css"), os.path.join(d, "cv.html")
        open(css, "w").write(matt.CSS)
        subprocess.run(["/usr/local/bin/pandoc", docx_path, "-o", html, "--standalone",
                        f"--css={css}", "--embed-resources", "--metadata", "title=CV Xavier Robitaille"],
                       check=True, capture_output=True)
        subprocess.run([matt.CHROME, "--headless", "--disable-gpu", f"--user-data-dir={d}/chrome",
                        "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html],
                       check=True, capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

def _text_to_pdf_safe(text, pdf_path):
    d = tempfile.mkdtemp(prefix="apply_cl_")
    try:
        html = os.path.join(d, "cl.html")
        th = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        th = "<br>".join(th.split("\n"))
        open(html, "w").write(
            '<html><head><style>@page{size:A4;margin:20mm 22mm}'
            'body{font-size:10pt;line-height:1.5;font-family:"Helvetica Neue",Arial}'
            '.hdr{margin-bottom:18pt;color:#0b3d62;font-weight:bold}</style></head><body>'
            '<div class="hdr">Xavier Robitaille · xro@xavier-robitaille.com · +33 6 64 89 09 43</div>'
            f'<div>{th}</div></body></html>')
        subprocess.run([matt.CHROME, "--headless", "--disable-gpu", f"--user-data-dir={d}/chrome",
                        "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html],
                       check=True, capture_output=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)

def make_cv_cl(employeur, poste, lieu, jd):
    """Réutilise MATT (select_cv, gap, docx) mais PDF parallèle-safe + noms uniques. Retourne noms de fichiers PDF."""
    context = jd or f"{poste} chez {employeur} à {lieu}"
    cv_type = matt.select_cv(context + " " + poste)
    cv_base = os.path.join(matt.CV_BASE_DIR, matt.CV_FILES[cv_type])
    context_md = open(matt.CONTEXT_FILE).read()[:8000] if os.path.exists(matt.CONTEXT_FILE) else ""
    today = datetime.date.today().strftime("%Y%m%d")
    safe = re.sub(r"[^\w\-]", "", (employeur or "offre").replace(" ", ""))[:30] or "offre"
    uniq = uuid.uuid4().hex[:6]   # évite toute collision de nom (parallèle / même employeur même jour)

    gap_prompt = (f"Tu aides à adapter un CV senior (25 ans d'expérience en assurance et finance) à une offre.\n\n"
                  f"OFFRE :\nEmployeur : {employeur}\nPoste : {poste}\nLieu : {lieu}\nDescription : {context[:3000]}\n\n"
                  f"PROFIL :\n{context_md[:3000]}\n\nTÂCHE :\nIdentifie 3 à 6 retouches CHIRURGICALES au CV de base "
                  f"(type {cv_type}). Chaque retouche = remplacer une courte phrase existante par une version "
                  f"légèrement améliorée. Ne pas inventer de chiffres ni d'expériences ; ne pas changer la structure.\n"
                  f'Réponds UNIQUEMENT en JSON : [{{"old":"texte exact du CV","new":"remplacement"}}]. Si rien : []')
    try:
        edits = [(e["old"], e["new"]) for e in json.loads(re.search(r'\[[\s\S]*\]', matt.claude(gap_prompt)).group())
                 if e.get("old") and e.get("new")]
    except Exception:
        edits = []
    cv_docx = os.path.join(matt.CV_OUT_DIR, f"CV_XRO_EN_{cv_type}_{safe}_{today}_{uniq}.docx")
    matt.apply_cv_edits(cv_base, cv_docx, edits)
    cv_pdf = os.path.join(matt.PDF_DIR, os.path.basename(cv_docx).replace(".docx", ".pdf"))
    _docx_to_pdf_safe(cv_docx, cv_pdf)

    rules = open(matt.WRITING_EN).read()[:1500] if os.path.exists(matt.WRITING_EN) else ""
    cl_prompt = (f"Tu rédiges une lettre de motivation courte (200-350 mots) pour Xavier Robitaille.\n\n"
                 f"OFFRE :\nEmployeur : {employeur}\nPoste : {poste}\nLieu : {lieu}\nDescription : {context[:2000]}\n\n"
                 f"PROFIL :\n{context_md[:2000]}\n\nRÈGLES D'ÉCRITURE :\n{rules}\n\n"
                 f"STRUCTURE : 1) proposition de valeur pour l'entreprise 2) preuve concrète (chiffres réels) "
                 f"3) gap honnête si pertinent 4) call to action. Langue : anglais. Ne pas commencer par le prénom. "
                 f"Réponds avec le texte de la lettre uniquement.")
    cl_text = matt.claude(cl_prompt, max_tokens=1000)
    cl_pdf = os.path.join(matt.PDF_DIR, f"CL_XRO_EN_{safe}_{today}_{uniq}.pdf")
    _text_to_pdf_safe(cl_text, cl_pdf)
    return cv_type, os.path.basename(cv_pdf), os.path.basename(cl_pdf), cl_text

def prepare(raw):
    jd, url, jid = get_jd(raw)
    employeur, poste, lieu = extract_meta(jd) if jd else ("Inconnu", "", "")
    note_role, note_crit = make_notes(employeur, poste, lieu, jd)
    score, justif, signaux = make_score(employeur, poste, lieu, note_role, note_crit, jd)
    cv_type, cv_pdf, cl_pdf, cl_text = make_cv_cl(employeur, poste, lieu, jd)
    return {
        "employeur": employeur, "poste": poste, "lieu": lieu, "url": url, "jobid": jid,
        "note_role": note_role, "note_criteres": note_crit,
        "score": score, "justification": justif, "signaux": signaux,
        "cv_type": cv_type, "cv_pdf": cv_pdf, "cl_pdf": cl_pdf, "cl_text": cl_text,
        "cv_path": os.path.join(matt.PDF_DIR, cv_pdf), "cl_path": os.path.join(matt.PDF_DIR, cl_pdf),
        "jd_present": bool(jd and len(jd) > 100),
    }

# ── Anti-doublon ────────────────────────────────────────────────────────────────
def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\b(sas|sasu|sa|sarl|inc|ltd|llc|gmbh|group|groupe|technologies|tech|recruitment|recrutement|staffing)\b", "", s)
    return re.sub(r"[^a-z0-9]", "", s)

def list_candidatures():
    recs, offset = [], None
    while True:
        params = [("fields[]", "Société"), ("fields[]", "Poste"), ("fields[]", "URL"), ("fields[]", "Réponse"), ("pageSize", 100)]
        if offset:
            params.append(("offset", offset))
        r = requests.get(f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{CAND_TABLE}", headers=at_headers(), params=params, timeout=30)
        r.raise_for_status(); j = r.json()
        recs += j.get("records", []); offset = j.get("offset")
        if not offset:
            break
    return recs

def find_existing(societe, poste, url):
    """Cherche une candidature déjà présente : par jobId d'URL (fort), sinon société+poste."""
    jid = extract_jobid(url or "")
    ksoc, kpos = _norm(societe), _norm(poste)
    for rec in list_candidatures():
        f = rec.get("fields", {})
        if jid and jid in (f.get("URL", "") or ""):
            return rec
        rsoc, rpos = _norm(f.get("Société", "")), _norm(f.get("Poste", ""))
        if ksoc and rsoc == ksoc and kpos and rpos and (kpos == rpos or kpos in rpos or rpos in kpos):
            return rec
    return None

def apply_candidature(p):
    if not p.get("force"):
        ex = find_existing(p.get("employeur"), p.get("poste"), p.get("url"))
        if ex:
            f = ex.get("fields", {})
            return {"duplicate": True, "id": ex["id"],
                    "url": f"https://airtable.com/{AIRTABLE_BASE}/{CAND_TABLE}/{ex['id']}",
                    "societe": f.get("Société", ""), "poste": f.get("Poste", ""), "reponse": f.get("Réponse", "")}
    canal = "LinkedIn" if "linkedin.com" in (p.get("url") or "") else "Direct"
    note = f"Postulé via l'outil « Postuler proprement ». Rating IA : {p.get('score')}. {p.get('justification','')}".strip()
    fields = {
        "Société": p.get("employeur") or "Inconnu",
        "Poste": p.get("poste") or "",
        "Lieu": p.get("lieu") or "",
        "URL": p.get("url") or None,
        "Réponse": "Envoyé",
        "Canal": canal,
        "Date postulé": datetime.date.today().strftime("%Y-%m-%d"),
        "Note": note[:900],
    }
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    r = requests.post(f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{CAND_TABLE}",
                      headers=at_headers(), json={"typecast": False, "records": [{"fields": fields}]}, timeout=30)
    r.raise_for_status()
    rec = r.json()["records"][0]
    return {"id": rec["id"], "url": f"https://airtable.com/{AIRTABLE_BASE}/{CAND_TABLE}/{rec['id']}"}

# ── MATT — run manuel ───────────────────────────────────────────────────────────
def run_matt():
    """Lance run-dossiers.sh en tâche de fond. Un seul run à la fois."""
    global _matt_proc, _matt_start
    if _matt_proc is not None and _matt_proc.poll() is None:
        elapsed = int(time.time() - _matt_start)
        return {"running": True, "pid": _matt_proc.pid, "elapsed_s": elapsed,
                "msg": f"MATT tourne déjà (PID {_matt_proc.pid}, {elapsed}s écoulées)"}
    os.makedirs(DOSSIERS_LOG, exist_ok=True)
    log_path = os.path.join(DOSSIERS_LOG, f"dossiers_manual_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    log_file = open(log_path, "w")
    _matt_proc = subprocess.Popen(
        ["/bin/zsh", DOSSIERS_SH],
        stdout=log_file, stderr=log_file,
        start_new_session=True   # détaché — survit si apply_tool redémarre
    )
    _matt_start = time.time()
    return {"running": True, "pid": _matt_proc.pid, "log": log_path,
            "msg": f"MATT lancé (PID {_matt_proc.pid}) — log : {log_path}"}

def matt_status():
    global _matt_proc, _matt_start
    if _matt_proc is None:
        return {"running": False, "msg": "Aucun run manuel en cours"}
    rc = _matt_proc.poll()
    if rc is None:
        elapsed = int(time.time() - _matt_start)
        return {"running": True, "pid": _matt_proc.pid, "elapsed_s": elapsed,
                "msg": f"En cours — {elapsed}s écoulées"}
    return {"running": False, "pid": _matt_proc.pid, "returncode": rc,
            "msg": f"Terminé (code {rc})"}

# ── Serveur HTTP ────────────────────────────────────────────────────────────────
HTML = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Postuler proprement — Xavier Advisory</title>
<style>
:root{--navy:#0b3d62;--bg:#f4f6f8;--ok:#1a7f4b;--warn:#b5650f;--bad:#a32626;--line:#dde3e8}
*{box-sizing:border-box}body{font-family:"Helvetica Neue",Arial,sans-serif;margin:0;background:var(--bg);color:#1b2733}
header{background:var(--navy);color:#fff;padding:16px 24px}header h1{margin:0;font-size:18px;font-weight:600}
header span{opacity:.8;font-size:13px}
main{max-width:920px;margin:24px auto;padding:0 16px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px}
textarea{width:100%;min-height:120px;border:1px solid var(--line);border-radius:8px;padding:10px;font-size:14px;font-family:inherit}
input[type=text]{width:100%;border:1px solid var(--line);border-radius:6px;padding:8px;font-size:14px}
label{display:block;font-size:12px;color:#5a6b7b;margin:10px 0 3px;text-transform:uppercase;letter-spacing:.04em}
button{background:var(--navy);color:#fff;border:0;border-radius:8px;padding:11px 20px;font-size:14px;font-weight:600;cursor:pointer}
button:disabled{opacity:.5;cursor:wait}
button.apply{background:var(--ok)}
.row{display:flex;gap:14px;flex-wrap:wrap}.row>div{flex:1;min-width:180px}
.score{font-size:42px;font-weight:700;line-height:1}.score small{font-size:14px;color:#5a6b7b;font-weight:400}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600;color:#fff}
.note{white-space:pre-wrap;font-size:14px;line-height:1.5;background:#fafbfc;border:1px solid var(--line);border-radius:8px;padding:10px}
a.dl{display:inline-block;margin-right:12px;color:var(--navy);font-weight:600;text-decoration:none;border:1px solid var(--navy);border-radius:6px;padding:7px 12px}
.hidden{display:none}.muted{color:#5a6b7b;font-size:13px}
.spin{display:inline-block;width:16px;height:16px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:s .7s linear infinite;vertical-align:-3px;margin-right:6px}
@keyframes s{to{transform:rotate(360deg)}}
.ok{color:var(--ok);font-weight:600}.err{color:var(--bad);font-weight:600}
.pathrow{display:flex;gap:8px;align-items:center;margin-top:3px}.pathrow input{font-size:12px;color:#33414f;background:#fafbfc}
button.mini{padding:7px 12px;font-size:12px;font-weight:500;white-space:nowrap}
</style></head><body>
<header><h1>Postuler proprement</h1>
<span>URL LinkedIn ou texte d'offre → notes IA · rating · CV · CL · 1 clic pour postuler &nbsp;·&nbsp;
<button id="mattbtn" onclick="runMatt()" style="background:#1a5276;border:1px solid #aac;padding:5px 12px;font-size:12px;border-radius:6px;cursor:pointer;color:#fff">▶ Run MATT</button>
<span id="mattmsg" style="margin-left:8px;font-size:12px;opacity:.85"></span></span></header>
<main>
  <div class="card">
    <label>URL LinkedIn ou texte de l'offre</label>
    <textarea id="inp" placeholder="Colle une URL https://www.linkedin.com/jobs/view/... ou tout le texte de l'offre"></textarea>
    <div style="margin-top:12px"><button id="prep" onclick="prepare()">Préparer</button>
      <span id="prepmsg" class="muted"></span></div>
  </div>

  <div id="result" class="hidden">
    <div class="card">
      <div class="row">
        <div><label>Employeur</label><input type="text" id="employeur"></div>
        <div><label>Poste</label><input type="text" id="poste"></div>
        <div><label>Lieu</label><input type="text" id="lieu"></div>
      </div>
      <div style="margin-top:14px" class="row">
        <div style="flex:0 0 140px">
          <label>Rating IA</label>
          <div class="score"><span id="score">–</span><small>/100</small></div>
          <div id="scorebadge"></div>
        </div>
        <div style="flex:3"><label>Justification du score</label><div class="note" id="justif"></div></div>
      </div>
    </div>
    <div class="card">
      <label>Note rôle (IA)</label><div class="note" id="note_role"></div>
      <label style="margin-top:12px">Note critères recruteur (IA)</label><div class="note" id="note_criteres"></div>
    </div>
    <div class="card">
      <label>Dossier généré</label>
      <a class="dl" id="cvlink" target="_blank">📄 CV (PDF)</a>
      <a class="dl" id="cllink" target="_blank">✉️ Cover letter (PDF)</a>
      <span class="muted" id="cvtype"></span>
      <div style="margin-top:12px">
        <label>Chemin du CV customisé (à joindre à ta candidature)</label>
        <div class="pathrow"><input type="text" id="cvpath" readonly>
          <button class="mini" onclick="copyPath('cvpath')">Copier</button>
          <button class="mini" onclick="reveal(PREP&&PREP.cv_pdf)">📁 Finder</button></div>
        <label style="margin-top:8px">Chemin de la Cover letter</label>
        <div class="pathrow"><input type="text" id="clpath" readonly>
          <button class="mini" onclick="copyPath('clpath')">Copier</button>
          <button class="mini" onclick="reveal(PREP&&PREP.cl_pdf)">📁 Finder</button></div>
      </div>
      <div id="jdwarn" class="muted hidden" style="margin-top:8px">⚠️ Description non récupérée — notes/score basés sur titre+employeur+lieu.</div>
    </div>
    <div class="card">
      <button class="apply" id="applybtn" onclick="apply()">✓ Je postule</button>
      <span id="applymsg" class="muted"></span>
    </div>
  </div>
</main>
<script>
let PREP=null;
async function prepare(){
  const inp=document.getElementById('inp').value.trim();
  if(!inp){document.getElementById('prepmsg').textContent='Colle une URL ou un texte.';return;}
  const b=document.getElementById('prep');b.disabled=true;
  document.getElementById('prepmsg').innerHTML='<span class="spin"></span>Génération en cours (notes, rating, CV, CL)…';
  try{
    const r=await fetch('/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({input:inp})});
    const d=await r.json();
    if(d.error){throw new Error(d.error);}
    PREP=d;
    document.getElementById('employeur').value=d.employeur||'';
    document.getElementById('poste').value=d.poste||'';
    document.getElementById('lieu').value=d.lieu||'';
    document.getElementById('score').textContent=(d.score==null?'–':d.score);
    const sb=document.getElementById('scorebadge');
    if(d.score==null){sb.innerHTML='';}
    else{const c=d.score>=50?'var(--ok)':(d.score>=35?'var(--warn)':'var(--bad)');
      const t=d.score>=35?'Actionnable':'Sous le seuil';
      sb.innerHTML='<span class="badge" style="background:'+c+'">'+t+'</span>';}
    document.getElementById('justif').textContent=d.justification||'';
    document.getElementById('note_role').textContent=d.note_role||'';
    document.getElementById('note_criteres').textContent=d.note_criteres||'';
    document.getElementById('cvlink').href='/files/'+d.cv_pdf;
    document.getElementById('cllink').href='/files/'+d.cl_pdf;
    document.getElementById('cvtype').textContent='Profil : '+(d.cv_type||'');
    document.getElementById('cvpath').value=d.cv_path||'';
    document.getElementById('clpath').value=d.cl_path||'';
    document.getElementById('jdwarn').classList.toggle('hidden',!!d.jd_present);
    document.getElementById('result').classList.remove('hidden');
    document.getElementById('prepmsg').innerHTML='<span class="ok">Prêt.</span>';
    document.getElementById('applymsg').textContent='';
    document.getElementById('applybtn').disabled=false;
  }catch(e){document.getElementById('prepmsg').innerHTML='<span class="err">Erreur : '+e.message+'</span>';}
  b.disabled=false;
}
async function apply(force){
  if(!PREP)return;
  const ab=document.getElementById('applybtn');ab.disabled=true;
  document.getElementById('applymsg').innerHTML='<span class="spin" style="border-color:#1a7f4b;border-top-color:transparent"></span>Écriture dans Candidatures…';
  const payload=Object.assign({},PREP,{
    force:force===true,
    employeur:document.getElementById('employeur').value,
    poste:document.getElementById('poste').value,
    lieu:document.getElementById('lieu').value});
  try{
    const r=await fetch('/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error)throw new Error(d.error);
    if(d.duplicate){
      document.getElementById('applymsg').innerHTML='<span style="color:var(--warn);font-weight:600">⚠️ Déjà dans ta table : '+(d.societe||'')+' — '+(d.poste||'')+' (statut '+(d.reponse||'?')+'). Aucun doublon créé.</span> <a href="'+d.url+'" target="_blank">Ouvrir la ligne</a> &nbsp;·&nbsp; <a href="#" onclick="apply(true);return false;">Créer quand même</a>';
      ab.disabled=false;return;
    }
    document.getElementById('applymsg').innerHTML='<span class="ok">✓ Candidature créée (Envoyé).</span> <a href="'+d.url+'" target="_blank">Ouvrir dans Airtable</a>';
  }catch(e){document.getElementById('applymsg').innerHTML='<span class="err">Erreur : '+e.message+'</span>';ab.disabled=false;}
}
async function runMatt(){
  const btn=document.getElementById('mattbtn');
  const msg=document.getElementById('mattmsg');
  btn.disabled=true;
  msg.innerHTML='<span class="spin" style="border-color:#fff;border-top-color:transparent"></span>Lancement…';
  try{
    const r=await fetch('/run-matt',{method:'POST'});
    const d=await r.json();
    msg.textContent=d.msg||'Lancé';
    // Poll statut toutes les 5s
    const iv=setInterval(async()=>{
      const s=await(await fetch('/matt-status')).json();
      msg.textContent=s.msg;
      if(!s.running){clearInterval(iv);btn.disabled=false;}
    },5000);
  }catch(e){msg.textContent='Erreur : '+e.message;btn.disabled=false;}
}
function copyPath(id){const el=document.getElementById(id);el.select();
  try{navigator.clipboard.writeText(el.value);}catch(e){document.execCommand('copy');}
  const b=el.nextElementSibling;const t=b.textContent;b.textContent='Copié ✓';setTimeout(()=>b.textContent=t,1200);}
async function reveal(name){if(!name)return;try{await fetch('/reveal?f='+encodeURIComponent(name));}catch(e){}}
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code); self.send_header("Content-Type", ctype)
        if isinstance(body, str): body = body.encode("utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0));
        return json.loads(self.rfile.read(n) or b"{}")

    def log_message(self, *a): pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, HTML, "text/html; charset=utf-8")
        if self.path.startswith("/files/"):
            name = os.path.basename(self.path[len("/files/"):])
            fp = os.path.join(matt.PDF_DIR, name)
            if os.path.exists(fp) and name.endswith(".pdf"):
                return self._send(200, open(fp, "rb").read(), "application/pdf")
            return self._send(404, b"not found", "text/plain")
        if self.path.startswith("/reveal"):
            name = os.path.basename((parse_qs(urlparse(self.path).query).get("f") or [""])[0])
            fp = os.path.join(matt.PDF_DIR, name)
            if name.endswith(".pdf") and os.path.exists(fp):
                subprocess.run(["open", "-R", fp])
                return self._send(200, json.dumps({"ok": True}))
            return self._send(404, json.dumps({"ok": False}))
        if self.path == "/matt-status":
            return self._send(200, json.dumps(matt_status(), ensure_ascii=False))
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        try:
            if self.path == "/prepare":
                return self._send(200, json.dumps(prepare(self._json().get("input", "")), ensure_ascii=False))
            if self.path == "/apply":
                return self._send(200, json.dumps(apply_candidature(self._json()), ensure_ascii=False))
            if self.path == "/run-matt":
                return self._send(200, json.dumps(run_matt(), ensure_ascii=False))
            return self._send(404, json.dumps({"error": "route inconnue"}))
        except Exception as e:
            traceback.print_exc()
            return self._send(200, json.dumps({"error": str(e)}, ensure_ascii=False))

if __name__ == "__main__":
    print(f"\n  ▶ Postuler proprement : http://localhost:{PORT}\n  (Ctrl+C pour arrêter)\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
