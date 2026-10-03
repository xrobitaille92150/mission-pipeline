"""Wrapper Claude : chaque requête doit être acceptée par la signature réelle du SDK installé.

Premier run réel du 3 octobre : le SDK 1.x refusait `temperature` (TypeError) et tout le tri des emails de statut
(Haiku) échouait, alors que les tests, qui remplacent Claude par un double, restaient verts.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from anthropic.resources.messages import Messages

from mp.claude import FALLBACK_BETA, Claude

SIGNATURE = inspect.signature(Messages.create)


def _client_with_real_signature(calls: list[dict]):
    def create(**kw):
        SIGNATURE.bind(None, **kw)  # TypeError si un argument n'existe plus dans le SDK installé
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", usage=None,
                               content=[SimpleNamespace(type="text", text='{"ok": true}')])
    return SimpleNamespace(messages=SimpleNamespace(create=create))


@pytest.mark.parametrize("fast", [True, False])
def test_json_requests_fit_installed_sdk(fast):
    c = Claude("sk-test", model_main="claude-opus-5-5", model_fast="claude-haiku-4-5-20251001")
    calls: list[dict] = []
    c.client = _client_with_real_signature(calls)
    assert c.json(system=["s"], user="u", schema={"type": "object"}, fast=fast) == {"ok": True}
    kw = calls[-1]
    assert "temperature" not in kw
    if fast:
        assert kw["model"].startswith("claude-haiku") and kw["extra_body"] == {"temperature": 0}
    else:
        assert kw["extra_headers"] == {"anthropic-beta": FALLBACK_BETA} and kw["output_config"]["effort"] == "medium"


def test_text_requests_fit_installed_sdk():
    c = Claude("sk-test", model_main="claude-opus-5-5", model_fast="claude-haiku-4-5-20251001")
    calls: list[dict] = []
    c.client = _client_with_real_signature(calls)
    assert c.text(system=["s"], user="u") == '{"ok": true}'
    assert c.text(system=["s"], user="u", fast=True) == '{"ok": true}'
