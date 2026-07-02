"""Appel Claude Messages API — déterministe, retry propre, erreurs explicites (fail-loud).
Aucune dépendance à l'Agent SDK : simple HTTP POST."""
import json
import time
import urllib.request
import urllib.error

from .config import load_env

_API_KEY = None
_URL = "https://api.anthropic.com/v1/messages"


def _key() -> str:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = load_env("anthropic.env", "ANTHROPIC_API_KEY")
    return _API_KEY


class ClaudeError(RuntimeError):
    """Erreur d'appel Claude — remonte le status + le message (jamais avalée en silence)."""


def call(model: str, prompt: str, max_tokens: int = 1000, retries: int = 3,
         temperature: float = None) -> str:
    """Retourne le texte de la réponse. Lève ClaudeError après épuisement des retries.
    temperature=0 pour les appels qui doivent être déterministes (scoring)."""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode()
    headers = {
        "x-api-key": _key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(_URL, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            return data["content"][0]["text"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last = f"HTTP {e.code}: {detail}"
            # 429/500/529 = surcharge → backoff et retry ; 4xx def = échec immédiat
            if e.code in (429, 500, 502, 503, 529) and attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise ClaudeError(last)
        except (urllib.error.URLError, TimeoutError) as e:
            last = f"network: {e}"
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise ClaudeError(last)
    raise ClaudeError(last or "unknown")
