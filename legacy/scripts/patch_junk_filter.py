#!/usr/bin/env python3
"""Patch ponctuel (02/07/2026) : dans le workflow n8n CYcYMoYxChce2755, nœud
« Filtrer & préparer », ignorer les emails de confirmation « Votre alerte Emploi
a été créée » (jobalerts-noreply) qui étaient parsés à tort en offres Veille.
PUT REST = publication directe. Usage : python3 patch_junk_filter.py [--dry-run]"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path.home() / ".config" / "mission-pipeline" / "n8n.env"
WF_ID = "CYcYMoYxChce2755"
ANCHOR = "jobalerts-noreply@linkedin.com') {"
GUARD = "votre alerte emploi a été créée"
PATCH_LINE = ("\n    // Confirmations de création d'alerte : pas des offres (bug 02/07 — records poubelle)"
              "\n    if (/votre alerte emploi a été créée/i.test(subject)) continue;")

env = dict(l.strip().split("=", 1) for l in CONFIG.read_text().splitlines()
           if l.strip() and not l.startswith("#") and "=" in l)
base = env["N8N_URL"].rstrip("/")
headers = {"X-N8N-API-KEY": env["N8N_API_KEY"].strip('"'), "Content-Type": "application/json"}

req = urllib.request.Request(f"{base}/api/v1/workflows/{WF_ID}", headers=headers)
wf = json.loads(urllib.request.urlopen(req, timeout=60).read())

node = next(n for n in wf["nodes"] if n["name"] == "Filtrer & préparer")
code = node["parameters"]["jsCode"]
if GUARD in code.lower():
    sys.exit("Déjà patché — rien à faire.")
if ANCHOR not in code:
    sys.exit("ANCRE INTROUVABLE — code du nœud inattendu, ne rien toucher.")
node["parameters"]["jsCode"] = code.replace(ANCHOR, ANCHOR + PATCH_LINE, 1)

if "--dry-run" in sys.argv:
    i = node["parameters"]["jsCode"].find(PATCH_LINE.strip()[:30])
    print("DRY-RUN — patch appliqué en mémoire, extrait :")
    print(node["parameters"]["jsCode"][i - 120:i + 220])
    sys.exit(0)

# L'API publique n8n n'accepte qu'un sous-ensemble de settings (400 sinon)
ALLOWED = {"saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
           "saveDataSuccessExecution", "executionTimeout", "errorWorkflow", "timezone",
           "executionOrder"}
settings = {k: v for k, v in (wf.get("settings") or {}).items() if k in ALLOWED}
payload = json.dumps({"name": wf["name"], "nodes": wf["nodes"],
                      "connections": wf["connections"], "settings": settings}).encode()
req = urllib.request.Request(f"{base}/api/v1/workflows/{WF_ID}", data=payload,
                             headers=headers, method="PUT")
try:
    out = json.loads(urllib.request.urlopen(req, timeout=60).read())
    print(f"PUT OK — workflow '{out.get('name')}' mis à jour (active={out.get('active')})")
except urllib.error.HTTPError as e:
    print(f"PUT {e.code} — détail : {e.read().decode(errors='replace')[:800]}")
    sys.exit(1)
