#!/usr/bin/env python3
"""
run_dossiers.py — Génère CV + Cover Letter pour les offres Veille en attente.
Tourne en headless via launchd à 6h, 12h, 19h.
Usage : python3 run_dossiers.py
"""

import os, sys, json, subprocess, datetime, re, time, logging, shutil, textwrap
import requests
from docx import Document
import anthropic

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_DIR = os.path.expanduser("~/Desktop/Claude/Projects/Candidatures/pipeline/logs")
os.makedirs(LOG_DIR, exist_ok=True)
ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(f"{LOG_DIR}/dossiers_{ts}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
def load_env():
    env_file = os.path.expanduser("~/.config/mission-pipeline/anthropic.env")
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AIRTABLE_PAT      = os.environ.get("AIRTABLE_PAT", "")

if not ANTHROPIC_API_KEY:
    log.error("ANTHROPIC_API_KEY manquant — arrêt.")
    sys.exit(1)
if not AIRTABLE_PAT:
    log.error("AIRTABLE_PAT manquant — arrêt.")
    sys.exit(1)

AIRTABLE_BASE   = "apphTpnW5vu0OdnfC"
AIRTABLE_TABLE  = "tblrXH5Jiyg6w21lW"
GITHUB_REPO     = "xrobitaille92150/mission-pipeline"
REPO_PATH       = os.path.expanduser("~/Desktop/Claude/Projects/Candidatures/pipeline")
CV_BASE_DIR     = os.path.expanduser("~/Desktop/Claude/Projects/CV_Profiles/CV de base")
CV_OUT_DIR      = os.path.expanduser("~/Desktop/Claude/Projects/CV_Profiles")
PDF_DIR         = os.path.expanduser("~/Desktop/Claude/Projects/CV_Profiles/pdf")
CONTEXT_FILE    = os.path.expanduser("~/Desktop/Claude/Projects/_shared/context.md")
WRITING_EN      = os.path.expanduser("~/Desktop/Claude/Skills/WRITING RULES.md")
WRITING_FR      = os.path.expanduser("~/Desktop/Claude/Skills/REGLES-ECRITURE-FR.md")
CHROME          = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

os.makedirs(PDF_DIR, exist_ok=True)

# Airtable field IDs
F = {
    "preparer":   "fldqXeFmnWfTXxkM5",
    "cv":         "fldhcv8Pj9X10b5ez",
    "cl":         "fldXXebILLlx2Jxla",
    "ecarte":     "fld40nO9ydrZy4sA5",
    "jobid":      "fldw7NH5gGRREOC4m",
    "employeur":  "flddDYtu0fFNmEobL",
    "poste":      "fld4E08BVrNWqpJL2",
    "lieu":       "fldG71cQfCGTSZ3sm",
    "url":        "fldCicrtpsoT82Y5d",
    "pertinence": "fldapfJfbwD1vWNyy",
    "note_role":  "fld2nXfShq9rUgcbQ",
    "note_crit":  "fld9aATrVlyOkke77",
}

CV_FILES = {
    "FinanceTransformation": "CV_XRO_EN_FinanceTransformation_v4.docx",
    "AssetManagement":       "CV_XRO_EN_AssetManagement_v4.docx",
    "IFRS17SolvencyII":      "CV_XRO_EN_IFRS17_SolvencyII_v4.docx",
}

# ── Airtable helpers ───────────────────────────────────────────────────────────
def at_headers():
    return {"Authorization": f"Bearer {AIRTABLE_PAT}", "Content-Type": "application/json"}

def fetch_pending():
    """Retourne les offres avec Préparer dossier=true, CV vide, J'écarte=false."""
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}"
    formula = (
        f"AND({{{F['preparer']}}}=1,"
        f"{{{F['cv']}}}='',"
        f"NOT({{{F['ecarte']}}}=1))"
    )
    # Passer fields[] comme liste de tuples + retourner par ID de champ
    base_params = [
        ("filterByFormula", formula),
        ("sort[0][field]", F["pertinence"]),
        ("sort[0][direction]", "asc"),
        ("returnFieldsByFieldId", "true"),
    ]
    for fid in F.values():
        base_params.append(("fields[]", fid))

    records = []
    offset = None
    while True:
        params = base_params.copy()
        if offset:
            params.append(("offset", offset))
        r = requests.get(url, headers=at_headers(), params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return records

def update_record(record_id, cv_link, cl_link):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}/{record_id}"
    payload = {"fields": {F["cv"]: cv_link, F["cl"]: cl_link}}
    r = requests.patch(url, headers=at_headers(), json=payload, timeout=30)
    r.raise_for_status()

# ── Claude API helper ──────────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=4096):
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()

# ── CV selection ───────────────────────────────────────────────────────────────
def select_cv(jd_text):
    """Choisit le profil CV selon le decision tree de cv_profiles.md."""
    jd = jd_text.lower()
    # Cluster B — Investissement/actif (AssetManagement CV)
    am_kw = ["oms","simcorp","clearwater","aladdin","bloomberg aim","murex","linedata","wealthsuite",
             "front-to-back","investment platform","investment accounting","investment reporting",
             "comptabilité des placements","comptabilité investissement","reporting investissement",
             "abor","ibor","custodian","securities migration","fund accounting",
             "middle office","back office"]
    # Cluster A — Assurance/passif (IFRS17SolvencyII CV)
    ifrs_kw = ["ifrs 17","ifrs17","ifrs 9","ifrs9","solvency ii","solvency 2","qrt","orsa",
               "actuarial","technical provisions","dry-run","paa","gmm","ecl","reserving","solvency",
               "comptabilité technique","provisions techniques","reporting prudentiel","pillar 3"]
    # FR back-office / interfaces comptables (AO CV)
    ao_kw = ["migration comptable","bascule","apurement","comptes d'attente","recettes (uat)",
             "cahier des charges","cdc","amoa back-office","interfaces comptables"]

    if any(k in jd for k in ifrs_kw):
        return "IFRS17SolvencyII"
    if any(k in jd for k in am_kw):
        return "AssetManagement"
    return "FinanceTransformation"

# ── LinkedIn fetch ─────────────────────────────────────────────────────────────
def fetch_linkedin(job_id):
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            return None
        # Extraire la description principale
        html = r.text
        m = re.search(r'class="show-more-less-html__markup[^"]*">(.*?)</div>', html, re.DOTALL)
        if m:
            raw = re.sub(r"<[^>]+>", " ", m.group(1))
            return re.sub(r"\s+", " ", raw).strip()[:6000]
        # Fallback : extraire tout le texte
        raw = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", raw).strip()[:6000]
    except Exception as e:
        log.warning(f"LinkedIn fetch échoué ({e})")
        return None

# ── python-docx : retouches chirurgicales ─────────────────────────────────────
def apply_cv_edits(base_path, out_path, edits):
    """
    edits = liste de (old_text, new_text) à remplacer dans les paragraphes.
    Préserve la mise en forme. Ne modifie que les runs contenant old_text.
    """
    doc = Document(base_path)

    def replace_in_para(para, old, new):
        full = "".join(r.text for r in para.runs)
        if old in full:
            for r in para.runs:
                r.text = r.text.replace(old, new)
            return True
        return False

    for old, new in edits:
        if old == new:
            continue
        found = False
        for para in doc.paragraphs:
            if replace_in_para(para, old, new):
                found = True
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        if replace_in_para(para, old, new):
                            found = True
        if not found:
            log.debug(f"Texte non trouvé pour remplacement : '{old[:40]}'")

    doc.save(out_path)

# ── PDF conversion ─────────────────────────────────────────────────────────────
CSS = """
@page { size: A4; margin: 11mm 13mm; }
body { font-size: 8.8pt; line-height: 1.26; font-family: "Helvetica Neue", Arial, sans-serif; }
h1, h2, h3 { color: #0b3d62; margin: 4pt 0 2pt; }
p, li { margin: 1pt 0; }
"""

def docx_to_pdf(docx_path, pdf_path):
    css_path = "/tmp/cv_dossiers_style.css"
    html_path = "/tmp/cv_dossiers_tmp.html"
    with open(css_path, "w") as f:
        f.write(CSS)
    subprocess.run([
        "/usr/local/bin/pandoc", docx_path, "-o", html_path,
        "--standalone", f"--css={css_path}",
        "--embed-resources", '--metadata', 'title=CV Xavier Robitaille'
    ], check=True, capture_output=True)
    subprocess.run([
        CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}", html_path
    ], check=True, capture_output=True)
    log.info(f"PDF généré : {pdf_path}")

def text_to_pdf(text, pdf_path, title="Cover Letter Xavier Robitaille"):
    html_path = "/tmp/cl_dossiers_tmp.html"
    text_html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text_html = "<br>".join(text_html.split("\n"))
    html = f"""<html><head><style>
@page{{size:A4;margin:20mm 22mm}}
body{{font-size:10pt;line-height:1.5;font-family:"Helvetica Neue",Arial}}
.hdr{{margin-bottom:18pt;color:#0b3d62;font-weight:bold}}
</style></head><body>
<div class="hdr">Xavier Robitaille · xro@xavier-robitaille.com · +33 6 64 89 09 43</div>
<div>{text_html}</div>
</body></html>"""
    with open(html_path, "w") as f:
        f.write(html)
    subprocess.run([
        CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}", html_path
    ], check=True, capture_output=True)
    log.info(f"PDF CL généré : {pdf_path}")

# ── GitHub push ────────────────────────────────────────────────────────────────
def push_to_github(files, commit_msg):
    """files = liste de chemins locaux à copier dans candidatures/YYYY-MM-DD/."""
    today = datetime.date.today().strftime("%Y-%m-%d")
    dest_dir = os.path.join(REPO_PATH, "candidatures", today)
    os.makedirs(dest_dir, exist_ok=True)
    pushed = {}
    for f in files:
        name = os.path.basename(f)
        dst = os.path.join(dest_dir, name)
        shutil.copy2(f, dst)
        pushed[name] = f"https://github.com/{GITHUB_REPO}/blob/main/candidatures/{today}/{name}"
    subprocess.run(["git", "-C", REPO_PATH, "add", "candidatures/"], check=True, capture_output=True)
    subprocess.run(["git", "-C", REPO_PATH, "commit", "-m", commit_msg],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", REPO_PATH, "push", "origin", "main"],
                   check=True, capture_output=True)
    log.info(f"GitHub push OK : {list(pushed.values())}")
    return pushed

# ── Traitement d'une offre ─────────────────────────────────────────────────────
def process_offer(rec):
    c = rec["fields"]
    record_id = rec["id"]
    job_id    = c.get(F["jobid"], "")
    employeur = c.get(F["employeur"], "Inconnu")
    poste     = c.get(F["poste"], "")
    lieu      = c.get(F["lieu"], "")
    note_role = c.get(F["note_role"], "")
    note_crit = c.get(F["note_crit"], "")
    pertinence_obj = c.get(F["pertinence"])
    pertinence = pertinence_obj.get("name", "") if isinstance(pertinence_obj, dict) else str(pertinence_obj)

    log.info(f"→ {employeur} — {poste} [{pertinence}]")
    today_str = datetime.date.today().strftime("%Y%m%d")
    safe_emp  = re.sub(r"[^\w\-]", "", employeur.replace(" ", ""))[:30]

    # 1. Fetch LinkedIn
    jd_text = None
    if job_id:
        jd_text = fetch_linkedin(job_id)
        if jd_text:
            log.info(f"  Description LinkedIn : {len(jd_text)} caractères")
        else:
            log.warning("  ⚠ Description LinkedIn non disponible — utilisation des notes")
        time.sleep(2)

    context_for_claude = jd_text or f"{note_role}\n\n{note_crit}" or f"{poste} chez {employeur} à {lieu}"

    # 2. Sélection CV
    cv_type = select_cv(context_for_claude + " " + poste)
    lang    = "FR" if cv_type == "AO" else "EN"
    cv_base_file = CV_FILES[cv_type]
    cv_base_path = os.path.join(CV_BASE_DIR, cv_base_file)
    if not os.path.exists(cv_base_path):
        log.error(f"  CV de base introuvable : {cv_base_path}")
        return None
    log.info(f"  CV sélectionné : {cv_type}")

    # 3. Gap analysis + retouches via Claude
    context_md = open(CONTEXT_FILE).read()[:8000] if os.path.exists(CONTEXT_FILE) else ""

    gap_prompt = f"""Tu aides à adapter un CV senior (25 ans d'expérience en assurance et finance) à une offre d'emploi.

OFFRE :
Employeur : {employeur}
Poste : {poste}
Lieu : {lieu}
Description : {context_for_claude[:3000]}

PROFIL :
{context_md[:3000]}

TÂCHE :
Identifie 3 à 6 retouches CHIRURGICALES à apporter au CV de base (type {cv_type}).
Chaque retouche = remplacer une courte phrase ou expression existante par une version légèrement améliorée.
Règles absolues : ne pas inventer de chiffres ni d'expériences absentes du profil ; ne pas changer la structure.
Réponds UNIQUEMENT en JSON :
[
  {{"old": "texte exact à trouver dans le CV", "new": "texte de remplacement"}},
  ...
]
Si aucune retouche n'est utile, réponds : []
"""
    try:
        edits_raw = claude(gap_prompt, model="claude-haiku-4-5-20251001")
        # Extraire uniquement le bloc JSON [ ... ] pour éviter les textes parasites
        m = re.search(r'\[[\s\S]*\]', edits_raw)
        if not m:
            raise ValueError("Pas de JSON array dans la réponse")
        edits = [(e["old"], e["new"]) for e in json.loads(m.group()) if e.get("old") and e.get("new")]
        log.info(f"  {len(edits)} retouche(s) CV")
    except Exception as e:
        log.warning(f"  Gap analysis échoué ({e}) — CV sans retouche")
        edits = []

    # 4. Appliquer les retouches python-docx
    cv_out_name = f"CV_XRO_{lang}_{cv_type}_{safe_emp}_{today_str}.docx"
    cv_out_path = os.path.join(CV_OUT_DIR, cv_out_name)
    apply_cv_edits(cv_base_path, cv_out_path, edits)
    log.info(f"  CV .docx sauvegardé : {cv_out_name}")

    # 5. Cover letter via Claude
    writing_path = WRITING_FR if lang == "FR" else WRITING_EN
    writing_rules = open(writing_path).read()[:3000] if os.path.exists(writing_path) else ""

    cl_prompt = f"""Tu rédiges une lettre de motivation courte (200-350 mots) pour Xavier Robitaille.

OFFRE :
Employeur : {employeur}
Poste : {poste}
Lieu : {lieu}
Description : {context_for_claude[:2000]}

PROFIL :
{context_md[:2000]}

RÈGLES D'ÉCRITURE :
{writing_rules[:1500]}

STRUCTURE (4 parties) :
1. Proposition de valeur pour l'entreprise (pas de compliment générique)
2. Preuve concrète (1-2 missions clés, chiffres réels uniquement)
3. Gap honnête si pertinent (ex : CDI vs freelance)
4. Call to action simple

Langue : {"français" if lang == "FR" else "anglais"}
Ne pas commencer par le prénom du candidat.
Réponds avec le texte de la lettre uniquement, sans titre ni en-tête.
"""
    cl_text = claude(cl_prompt, model="claude-haiku-4-5-20251001", max_tokens=1000)
    log.info(f"  Cover letter générée : {len(cl_text)} caractères")

    # 6. Conversion PDF
    cv_pdf_name = cv_out_name.replace(".docx", ".pdf")
    cv_pdf_path = os.path.join(PDF_DIR, cv_pdf_name)
    cl_pdf_name = f"CL_XRO_{lang}_{safe_emp}_{today_str}.pdf"
    cl_pdf_path = os.path.join(PDF_DIR, cl_pdf_name)

    try:
        docx_to_pdf(cv_out_path, cv_pdf_path)
        text_to_pdf(cl_text, cl_pdf_path)
    except subprocess.CalledProcessError as e:
        log.error(f"  Conversion PDF échouée : {e}")
        return None

    # 7. Push GitHub
    try:
        links = push_to_github(
            [cv_pdf_path, cl_pdf_path],
            f"dossiers: {employeur} {today_str}"
        )
        cv_link = links.get(cv_pdf_name, "")
        cl_link = links.get(cl_pdf_name, "")
    except subprocess.CalledProcessError as e:
        log.error(f"  Git push échoué : {e}")
        return None

    # 8. Mise à jour Airtable
    try:
        update_record(record_id, cv_link, cl_link)
        log.info(f"  ✓ Airtable mis à jour")
    except Exception as e:
        log.error(f"  Airtable update échoué : {e}")
        return None

    return {"employeur": employeur, "cv": cv_link, "cl": cl_link}

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    log.info("=== run_dossiers.py démarré ===")

    try:
        records = fetch_pending()
    except Exception as e:
        log.error(f"Airtable fetch échoué : {e}")
        sys.exit(1)

    if not records:
        log.info("Aucun dossier en attente. Fin.")
        return

    log.info(f"{len(records)} dossier(s) à générer")
    for rec in records:
        c = rec.get("fields", {})
        emp = c.get(F["employeur"], "?")
        pos = c.get(F["poste"], "?")
        log.info(f"  • {emp} — {pos}")

    results = []
    errors  = []
    for rec in records:
        try:
            res = process_offer(rec)
            if res:
                results.append(res)
            else:
                errors.append(rec["fields"].get(F["employeur"], "?"))
        except Exception as e:
            emp = rec["fields"].get(F["employeur"], "?")
            log.error(f"Erreur sur {emp} : {e}", exc_info=True)
            errors.append(emp)
        time.sleep(3)

    log.info(f"=== Terminé : {len(results)}/{len(records)} OK, {len(errors)} erreur(s) ===")
    for r in results:
        log.info(f"  ✓ {r['employeur']} | CV → {r['cv']}")
    for e in errors:
        log.info(f"  ✗ {e}")

if __name__ == "__main__":
    main()
