#!/usr/bin/env python3
"""One-shot : crée + active le workflow n8n « Gmail Bridge — send digest (service Mac) ».
Webhook POST (header-auth, même token que le pont fetch) → Gmail send → Respond.
Réutilise les credentials existants : Gmail OAuth (wN9kkTh8npM257wD) + Bridge Token (TmTQjfiIJqzDZmta).
Imprime l'URL webhook à reporter dans ~/.config/mission-pipeline/gmail-bridge.env (GMAIL_BRIDGE_SEND_URL)."""
import json
import urllib.request
from pathlib import Path

PATH_SEG = "gmail-bridge-send-7c1d"
DEST = "xrobitaille92150@gmail.com"


def env(fname, var):
    for line in (Path.home() / ".config" / "mission-pipeline" / fname).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.split("=", 1)[0] == var:
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit(f"{var} introuvable dans {fname}")


N8N_URL = env("n8n.env", "N8N_URL").rstrip("/")
KEY = env("n8n.env", "N8N_API_KEY")

wf = {
    "name": "Gmail Bridge — send digest (service Mac)",
    "nodes": [
        {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
         "position": [0, 0],
         "parameters": {"httpMethod": "POST", "path": PATH_SEG,
                        "authentication": "headerAuth",
                        "responseMode": "responseNode", "options": {}},
         "credentials": {"httpHeaderAuth": {"id": "TmTQjfiIJqzDZmta", "name": "Gmail Bridge Token"}}},
        {"name": "Gmail — send", "type": "n8n-nodes-base.gmail", "typeVersion": 2,
         "position": [220, 0],
         "parameters": {"operation": "send", "sendTo": DEST,
                        "subject": "={{ $json.body.subject }}",
                        "emailType": "text",
                        "message": "={{ $json.body.text }}",
                        "options": {"appendAttribution": False}},
         "credentials": {"gmailOAuth2": {"id": "wN9kkTh8npM257wD", "name": "Gmail account"}}},
        {"name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1,
         "position": [440, 0],
         "parameters": {"respondWith": "json",
                        "responseBody": '={{ {"ok": true} }}', "options": {}}},
    ],
    "connections": {
        "Webhook": {"main": [[{"node": "Gmail — send", "type": "main", "index": 0}]]},
        "Gmail — send": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
    },
    "settings": {},
}


def api(method, path, payload=None):
    req = urllib.request.Request(
        f"{N8N_URL}/api/v1{path}",
        data=json.dumps(payload).encode() if payload is not None else b"{}",
        headers={"X-N8N-API-KEY": KEY, "Content-Type": "application/json"},
        method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


created = api("POST", "/workflows", wf)
wid = created["id"]
api("POST", f"/workflows/{wid}/activate")
print("workflow id :", wid)
print("webhook URL :", f"{N8N_URL}/webhook/{PATH_SEG}")
