"""Wrapper Claude : un appel = un JSON validé (sorties structurées), cache de prompt sur les
blocs stables, fallbacks serveur en cas de refus de classifieur, erreurs explicites.

Modèles : `MP_MODEL_MAIN` (Opus 5.5 par défaut — scoring, retouches CV, lettre) et
`MP_MODEL_FAST` (Haiku 4.5 — classification des emails de statut).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

log = logging.getLogger("mp.claude")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeError(RuntimeError):
    pass


class ClaudeRefusal(ClaudeError):
    pass


def _is_haiku(model: str) -> bool:
    return "haiku" in model


class Claude:
    def __init__(self, api_key: str | None, model_main: str, model_fast: str):
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model_main = model_main
        self.model_fast = model_fast
        self.usage: dict[str, int] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0}

    # -- construction des requêtes ------------------------------------------
    @staticmethod
    def _system(blocks: list[str]) -> list[dict]:
        """Chaque bloc stable est un point de cache (max 4). Les blocs sont concaténés dans
        l'ordre : mettre les plus stables en premier."""
        out = []
        for text in [b for b in blocks if b and b.strip()][:4]:
            out.append({"type": "text", "text": text, "cache_control": {"type": "ephemeral"}})
        return out

    def _create(self, **kw) -> Any:
        model = kw["model"]
        try:
            if _is_haiku(model):
                return self.client.messages.create(**kw)
            try:
                return self.client.messages.create(
                    extra_headers={"anthropic-beta": FALLBACK_BETA},
                    extra_body={"fallbacks": "default"}, **kw)
            except anthropic.BadRequestError as e:
                if "fallback" in str(e).lower():
                    log.warning("fallbacks non acceptés (%s) — nouvel essai sans", str(e)[:120])
                    return self.client.messages.create(**kw)
                raise
        except anthropic.RateLimitError as e:
            raise ClaudeError(f"rate limit Anthropic : {e}") from e
        except anthropic.APIStatusError as e:
            raise ClaudeError(f"API Anthropic HTTP {e.status_code} : {str(e)[:300]}") from e
        except anthropic.APIConnectionError as e:
            raise ClaudeError(f"réseau Anthropic : {e}") from e

    def _account(self, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        if not u:
            return
        self.usage["calls"] += 1
        self.usage["input"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output"] += getattr(u, "output_tokens", 0) or 0
        self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        self.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0

    @staticmethod
    def _text(resp: Any) -> str:
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise ClaudeRefusal(f"refus du classifieur ({cat})")
        if resp.stop_reason == "max_tokens":
            raise ClaudeError("réponse tronquée (max_tokens) — augmenter max_tokens")
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()

    # -- API publique ----------------------------------------------------------
    def json(self, *, system: list[str], user: str, schema: dict, fast: bool = False,
             effort: str = "medium", max_tokens: int = 4000, model: str | None = None) -> dict:
        model = model or (self.model_fast if fast else self.model_main)
        kw: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens,
            "system": self._system(system),
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if _is_haiku(model):
            kw["temperature"] = 0
        else:
            kw["output_config"]["effort"] = effort
        resp = self._create(**kw)
        self._account(resp)
        if resp.stop_reason == "max_tokens" and max_tokens < 32000:
            # la réflexion du modèle compte dans max_tokens : on double une fois avant d'abandonner
            log.warning("réponse tronquée à %d tokens — nouvel essai à %d", max_tokens, max_tokens * 2)
            kw["max_tokens"] = max_tokens * 2
            resp = self._create(**kw)
            self._account(resp)
        text = self._text(resp)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ClaudeError(f"JSON invalide renvoyé par {model} : {text[:200]}") from e

    def text(self, *, system: list[str], user: str, fast: bool = False, effort: str = "high",
             max_tokens: int = 8000, model: str | None = None) -> str:
        model = model or (self.model_fast if fast else self.model_main)
        kw: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens,
            "system": self._system(system),
            "messages": [{"role": "user", "content": user}],
        }
        if not _is_haiku(model):
            kw["output_config"] = {"effort": effort}
        resp = self._create(**kw)
        self._account(resp)
        return self._text(resp)

    def usage_line(self) -> str:
        u = self.usage
        return (f"{u['calls']} appel(s) Claude — entrée {u['input']} tok (+{u['cache_read']} cache lus, "
                f"{u['cache_write']} écrits) / sortie {u['output']} tok")
