#!/usr/bin/env python3
"""
make_fr_base_cvs.py — Génère les 3 CV de base FR par cluster à partir des 3 CV EN.

Traduction EN→FR "en place" : préserve exactement la mise en forme (gras intra-phrase,
polices, layout) en traduisant SEGMENT PAR SEGMENT (runs de même format fusionnés).
Les noms d'outils, normes, sociétés, certifications, chiffres, email et URL ne sont
JAMAIS traduits. Le CV FR générique existant sert de référence de vocabulaire.

Usage :
    python3 make_fr_base_cvs.py            # génère les 3 CV FR
    python3 make_fr_base_cvs.py --dry-run  # affiche les traductions sans écrire les .docx

Réutilisable : quand les CV EN passent en v5, relancer pour régénérer les FR.
"""

import os, sys, json, re, zipfile, html
from docx import Document
import anthropic

# ── Config ───────────────────────────────────────────────────────────────────────
def load_env():
    env_file = os.path.expanduser("~/.config/mission-pipeline/anthropic.env")
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    print("ANTHROPIC_API_KEY manquant — arrêt."); sys.exit(1)

client = anthropic.Anthropic(api_key=API_KEY)
MODEL  = "claude-sonnet-5"

CV_BASE_DIR = os.path.expanduser("~/Desktop/Claude/Projects/CV_Profiles/CV de base")
GENERIC_FR  = os.path.join(CV_BASE_DIR, "CV_XRO_FR.docx")

# EN source -> FR target (même dossier "CV de base")
CVS = {
    "CV_XRO_EN_FinanceTransformation_v4.docx": "CV_XRO_FR_FinanceTransformation_v4.docx",
    "CV_XRO_EN_AssetManagement_v4.docx":       "CV_XRO_FR_AssetManagement_v4.docx",
    "CV_XRO_EN_IFRS17_SolvencyII_v4.docx":     "CV_XRO_FR_IFRS17_SolvencyII_v4.docx",
}

DRY = "--dry-run" in sys.argv

# ── Glossaire : ne JAMAIS traduire ───────────────────────────────────────────────
KEEP = """SimCorp Dimension, Clearwater Analytics, CW CORE, OMS/Jump, Jump, LPx, Bloomberg,
Bloomberg TSOX, Bloomberg Data License, SAP S/4HANA, SAP BFC, SAP FC Publisher, CODA, SAS,
Power BI, Power Query, VBA, Excel, Neoxam, GP3, GP4, YFI, Linedata Chorus, CACEIS,
IFRS 9, IFRS 17, Solvency II, French GAAP, PAA, GMM, ECL, QRT, ORSA, RSR, SFCR, R2R,
Record-to-Report, PMO, UAT, STP, TOM, Target Operating Model, AUM, TCN, NAV, OMS, GL,
AP/AR, go-live, Hypercare, RAID, SteerCo, Front Office, Middle Office, Back Office,
Coface, AXA XL, CNP Assurances, SCOR Investment Partners, SCOR IP, Mazars, Clearwater,
ESCP, Université Paris Dauphine, D.E.C.F., DECF, CEA, PMI, PMP, Vanderbilt University,
Yale University, PMP®, linkedin.com/in/xrobitaille, xrobitaille92150@gmail.com,
Bornhuetter-Ferguson, EDP, LOB, CxO, CFO, COO, CIO, CFO-office"""

def build_prompt(segments, glossary_ref):
    seg_json = json.dumps(segments, ensure_ascii=False)
    return f"""Tu traduis en FRANÇAIS des segments d'un CV senior finance/assurance (Xavier Robitaille).

RÈGLES :
- Traduis chaque segment en français professionnel (registre CV, sobre, précis).
- NE TRADUIS JAMAIS : noms d'outils, logiciels, normes, sociétés, écoles, certifications,
  acronymes métier, chiffres, montants, dates, email, URL. Liste (non exhaustive) : {KEEP}
- Conserve la ponctuation structurante (« | », « · », « / », « : », tirets, parenthèses).
- Conserve les majuscules des titres de section.
- Un segment vide ou purement symbolique (ex. « | », « · », un nombre) est renvoyé À L'IDENTIQUE.
- Ne développe pas, ne résume pas : même longueur d'information.

RÉFÉRENCE DE VOCABULAIRE (CV FR déjà validé par Xavier — calque son style et ses termes) :
{glossary_ref[:2500]}

TÂCHE : traduis la liste ORDONNÉE de segments ci-dessous. Réponds UNIQUEMENT avec un tableau
JSON de chaînes, EXACTEMENT le même nombre d'éléments, dans le même ordre. Aucun autre texte.

SEGMENTS :
{seg_json}
"""

def claude_translate(segments, glossary_ref):
    """Traduit une liste de segments ; renvoie une liste de même longueur (ou None si échec)."""
    if not segments:
        return []
    msg = client.messages.create(
        model=MODEL, max_tokens=4096,
        messages=[{"role": "user", "content": build_prompt(segments, glossary_ref)}],
    )
    # Sonnet 5 peut renvoyer des blocs "thinking" avant le texte → prendre les blocs texte.
    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return None
    try:
        out = json.loads(m.group())
    except Exception:
        return None
    if not isinstance(out, list) or len(out) != len(segments):
        return None
    return [str(x) for x in out]

def fmt_key(run):
    """Clé de format d'un run (pour fusionner les runs adjacents identiques)."""
    return (run.bold, run.italic, run.underline)

def merge_runs(para):
    """Groupe les runs adjacents de même format. Retourne [(indices, texte)]."""
    groups = []
    for idx, r in enumerate(para.runs):
        if groups and fmt_key(r) == groups[-1]["key"]:
            groups[-1]["idx"].append(idx); groups[-1]["text"] += r.text
        else:
            groups.append({"key": fmt_key(r), "idx": [idx], "text": r.text})
    return groups

def collect_segments(doc):
    """Retourne (segments, plan) : segments = textes à traduire ; plan permet la réécriture."""
    segments, plan = [], []
    for pi, para in enumerate(doc.paragraphs):
        if not para.text.strip():
            continue
        groups = merge_runs(para)
        for gi, g in enumerate(groups):
            if g["text"].strip():
                plan.append((pi, gi, g["idx"]))
                segments.append(g["text"])
    return segments, plan

def apply_translations(doc, plan, translations):
    """Réécrit chaque groupe : run principal = traduction, runs fusionnés = vidés."""
    # index rapide paragraphe -> groupes
    for (pi, gi, idx_list), tr in zip(plan, translations):
        runs = doc.paragraphs[pi].runs
        runs[idx_list[0]].text = tr
        for j in idx_list[1:]:
            runs[j].text = ""

def docx_plain(path):
    z = zipfile.ZipFile(path); xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml); xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml)

def process(src, dst, glossary_ref):
    src_path = os.path.join(CV_BASE_DIR, src)
    dst_path = os.path.join(CV_BASE_DIR, dst)
    doc = Document(src_path)
    segments, plan = collect_segments(doc)
    print(f"\n=== {src} → {dst} : {len(segments)} segments ===")

    # Traduction par lots (préserve l'alignement 1:1)
    BATCH = 40
    translations = []
    for i in range(0, len(segments), BATCH):
        chunk = segments[i:i+BATCH]
        out = claude_translate(chunk, glossary_ref)
        if out is None:
            print(f"  ⚠ lot {i//BATCH}: échec alignement — traduction segment par segment (fallback)")
            out = []
            for s in chunk:
                one = claude_translate([s], glossary_ref)
                out.append(one[0] if one else s)  # si échec, garde l'original
        translations.extend(out)
        print(f"  lot {i//BATCH+1}/{(len(segments)+BATCH-1)//BATCH} OK")

    if DRY:
        for s, t in list(zip(segments, translations))[:25]:
            print(f"    EN: {s[:70]}\n    FR: {t[:70]}\n")
        return

    apply_translations(doc, plan, translations)
    doc.save(dst_path)
    print(f"  ✔ écrit : {dst_path}")
    # contrôle : longueur FR
    print(f"  FR chars: {len(docx_plain(dst_path))}")

def main():
    if not os.path.exists(GENERIC_FR):
        print(f"CV FR générique introuvable : {GENERIC_FR}"); sys.exit(1)
    glossary_ref = docx_plain(GENERIC_FR)
    for src, dst in CVS.items():
        if not os.path.exists(os.path.join(CV_BASE_DIR, src)):
            print(f"⚠ source absente, ignorée : {src}"); continue
        process(src, dst, glossary_ref)
    print("\nTerminé.")

if __name__ == "__main__":
    main()
