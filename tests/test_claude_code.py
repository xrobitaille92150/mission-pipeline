"""Claude Code en mode headless : commande lancée, sortie lue, repli sur l'API. Aucun vrai `claude` n'est appelé."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from mp import claude_code as cc
from mp.models import DOSSIER_SCHEMA

LETTRE = " ".join(["mot"] * 220)


class FakeRunner:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.calls: list[dict] = []

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, **kw})
        return SimpleNamespace(stdout=self.stdout, returncode=self.returncode, stderr=self.stderr)


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setenv("MP_CLAUDE_BIN", str(exe))
    return exe


def _ok(obj):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "fait",
                       "structured_output": obj, "total_cost_usd": 0.0})


def test_parse_output_variants():
    assert cc.parse_output(_ok({"a": 1})) == {"a": 1}
    assert cc.parse_output(json.dumps({"subtype": "success", "result": "```json\n{\"a\": 2}\n```"})) == {"a": 2}
    assert cc.parse_output(json.dumps([{"type": "system"}, {"type": "result", "subtype": "success",
                                                             "structured_output": {"a": 3}}])) == {"a": 3}
    for bad in (json.dumps({"subtype": "error_max_turns", "is_error": True, "result": ""}),
                json.dumps({"subtype": "success", "result": "pas de json"}), "pas du json"):
        with pytest.raises(cc.ClaudeCodeError):
            cc.parse_output(bad)


def test_run_json_command_env_and_stdin(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("MP_CLAUDE_CODE_MODEL", "opus")
    runner = FakeRunner(_ok({"lettre": "x", "objections": []}))
    out = cc.run_json("DEMANDE LONGUE", {"type": "object"}, cwd=tmp_path / "w", runner=runner)
    assert out == {"lettre": "x", "objections": []}
    call = runner.calls[0]
    cmd = call["cmd"]
    assert cmd[:3] == [str(fake_bin), "-p", cc.QUERY]
    assert cmd[cmd.index("--allowedTools") + 1] == "Skill,Read"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--output-format") + 1] == "json" and "--json-schema" in cmd
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert call["input"] == "DEMANDE LONGUE"                     # la demande passe par l'entrée standard
    assert "ANTHROPIC_API_KEY" not in call["env"]                # abonnement, pas l'API
    assert (tmp_path / "w").is_dir()


def test_run_json_errors(fake_bin, tmp_path):
    with pytest.raises(cc.ClaudeCodeError, match="code 1"):
        cc.run_json("x", {}, cwd=tmp_path, runner=FakeRunner("", returncode=1, stderr="not logged in"))

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(cc.ClaudeCodeError, match="pas répondu"):
        cc.run_json("x", {}, cwd=tmp_path, runner=timeout)


def test_engine_selection(fake_bin, monkeypatch):
    monkeypatch.setenv("MP_DOSSIER_ENGINE", "auto")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert cc.engine() == "claude-code"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert cc.engine() == "api"                                  # pas de skills du compte sur GitHub Actions
    monkeypatch.setenv("MP_DOSSIER_ENGINE", "api")
    assert cc.engine() == "api"
    monkeypatch.setenv("MP_CLAUDE_BIN", "/nulle/part/claude")
    monkeypatch.setenv("MP_DOSSIER_ENGINE", "auto")
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert cc.engine() == "api"


def _kw(**over):
    kw = dict(title="Directeur de programme", employer="AXA", location="Londres", jd_text="Programme IFRS 17.",
              lang="EN", profile="IFRS17SolvencyII", lang_cv="EN", cv_text="Led IFRS 17 programme.",
              clauses=["IR35 sentence"], keywords=["IFRS 17"], profile_md="Profil.", words=(220, 350),
              writing_rules="RULES")
    kw.update(over)
    return kw


def test_dossier_prompt_names_skills_and_carries_inputs():
    p = cc.dossier_prompt(**_kw())
    for s in ("cv-tailoring", "cover-letter", "voix-xavier"):
        assert f"`{s}`" in p
    assert "Led IFRS 17 programme." in p and "IR35 sentence" in p and "entre 220 et 350 mots" in p
    assert "ne crée aucun fichier" in p and "RULES" in p and "anglais" in p


def test_dossier_content_parses_and_validates(fake_bin):
    runner = FakeRunner(_ok({"edits": [{"old": "Led", "new": "Directed"}], "gaps": ["Aladdin"],
                             "lettre": LETTRE, "objections": ["TJM"]}))
    plan, letter = cc.dossier_content(runner=runner, **_kw())
    assert plan.edits[0].new == "Directed" and plan.gaps == ["Aladdin"] and letter.objections == ["TJM"]
    sent_schema = json.loads(runner.calls[0]["cmd"][runner.calls[0]["cmd"].index("--json-schema") + 1])
    assert sent_schema == DOSSIER_SCHEMA
    with pytest.raises(cc.ClaudeCodeError, match="trop courte"):
        cc.dossier_content(runner=FakeRunner(_ok({"edits": [], "gaps": [], "lettre": "court", "objections": []})),
                           **_kw())


def test_build_content_falls_back_to_api(monkeypatch, tmp_path):
    from mp import dossier
    from tests.conftest import FakeClaude, FakeContext
    claude = FakeClaude([{"edits": [], "gaps": []}, {"lettre": LETTRE, "objections": []}])
    ctx = FakeContext(claude=claude, tmp=tmp_path)
    monkeypatch.setattr(cc, "engine", lambda setting=None: "claude-code")

    def boom(**kw):
        raise cc.ClaudeCodeError("quota atteint")
    monkeypatch.setattr(cc, "dossier_content", boom)
    plan, letter, used = dossier.dossier_content(ctx, title="PMO", employer="AXA", location="Paris", jd_text="",
                                                 lang="FR", profile="FinanceTransformation", lang_cv="FR",
                                                 cv_text="CV", keywords=[], writing_rules="r")
    assert used == "api" and len(claude.prompts) == 2
    assert any("Claude Code indisponible" in n for n in ctx.report.notes)

    monkeypatch.setattr(cc, "dossier_content", lambda **kw: ("PLAN", "LETTRE"))
    assert dossier.dossier_content(ctx, title="PMO", employer="AXA", location="Paris", jd_text="", lang="FR",
                                   profile="FinanceTransformation", lang_cv="FR", cv_text="CV", keywords=[],
                                   writing_rules="r") == ("PLAN", "LETTRE", "claude-code")


def test_auth_status(fake_bin):
    assert cc.auth_status(runner=FakeRunner('{"loggedIn": true, "authMethod": "claude.ai"}'))["authMethod"] == "claude.ai"
    assert cc.auth_status(runner=FakeRunner("pas du json"))["authMethod"] == "inconnu"


def test_version_and_hints(fake_bin):
    # Le 3 octobre, le Mac avait Claude Code 2.1.193, non connecté au compte claude.ai (le Terminal passait par la
    # clé API du shell) : aucune skill synchronisée. doctor affiche la version et les commandes exactes.
    assert cc.version(runner=FakeRunner("2.1.193 (Claude Code)\n")) == "2.1.193"
    assert cc.version(runner=FakeRunner("")) == ""
    assert "env -u ANTHROPIC_API_KEY" in cc.LOGIN_HINT and "/login" in cc.LOGIN_HINT
    assert "claude update" in cc.SYNC_HINT and "CLAUDE_CODE_SYNC_SKILLS=1" in cc.SYNC_HINT
