"""Digest email de fin de run — la brique anti-échec-silencieux.
Envoi via le webhook n8n « Gmail Bridge — send digest » (credential Gmail OAuth existant).
Fallback si le webhook est KO : notification macOS + le digest reste dans le log run.py."""
import json
import subprocess
import urllib.request

from .config import load_env


def send_digest(subject: str, text: str, timeout: int = 60) -> bool:
    """True si l'email est parti. Ne lève jamais (le digest ne doit pas faire échouer le run)."""
    try:
        url = load_env("gmail-bridge.env", "GMAIL_BRIDGE_SEND_URL")
        token = load_env("gmail-bridge.env", "GMAIL_BRIDGE_TOKEN")
        body = json.dumps({"subject": subject, "text": text}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "X-Bridge-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return True
    except Exception as e:  # noqa: BLE001 — fallback assumé, jamais silencieux (notif + log)
        try:
            msg = str(e).replace('"', "'")[:120]
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "Digest email KO : {msg}" with title "Mission Pipeline"'],
                capture_output=True, timeout=10)
        except Exception:
            pass
        return False
