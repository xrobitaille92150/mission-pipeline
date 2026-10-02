import os

from mp import config


def _reset(monkeypatch, tmp_path):
    for k in ("AIRTABLE_PAT", "ANTHROPIC_API_KEY", "GMAIL_USER", "GMAIL_APP_PASSWORD", "MP_SCORE_MIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "SOURCES", {})
    monkeypatch.setattr(config, "DUPLICATES", {})
    (tmp_path / "airtable.env").write_text("AIRTABLE_PAT=pat_a\nANTHROPIC_API_KEY=old-key-in-wrong-file\n")
    (tmp_path / "anthropic.env").write_text('ANTHROPIC_API_KEY="new-key"   # commentaire\nGMAIL_USER=x@y\nMP_SCORE_MIN=70\n')


def test_canonical_file_wins_and_duplicates_are_reported(tmp_path, monkeypatch):
    _reset(monkeypatch, tmp_path)
    (tmp_path / "airtable-codex.env").write_text("AIRTABLE_PAT=pat_codex\n")   # alphabétiquement avant airtable.env
    config.load_env_files(tmp_path)
    # la clé Claude vient d'anthropic.env (canonique), pas d'airtable.env qui la redéfinit par erreur
    assert os.environ["ANTHROPIC_API_KEY"] == "new-key"
    assert config.SOURCES["ANTHROPIC_API_KEY"] == "anthropic.env"
    assert config.DUPLICATES["ANTHROPIC_API_KEY"] == ["airtable.env"]
    # le PAT vient d'airtable.env (canonique), pas du fichier « codex »
    assert os.environ["AIRTABLE_PAT"] == "pat_a"
    assert config.SOURCES["AIRTABLE_PAT"] == "airtable.env"
    assert config.DUPLICATES["AIRTABLE_PAT"] == ["airtable-codex.env"]
    assert os.environ["GMAIL_USER"] == "x@y" and config.SOURCES["GMAIL_USER"] == "anthropic.env"


def test_secrets_file_beats_shell_but_settings_do_not(tmp_path, monkeypatch):
    _reset(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-key-exported-in-zshrc")
    monkeypatch.setenv("MP_SCORE_MIN", "55")
    config.load_env_files(tmp_path)
    # secret : la vieille clé du shell est écartée au profit du fichier canonique, et doctor saura le dire
    assert os.environ["ANTHROPIC_API_KEY"] == "new-key"
    assert config.DUPLICATES["ANTHROPIC_API_KEY"] == ["le shell", "airtable.env"]
    # réglage : l'environnement garde la main (essai ponctuel, GitHub Actions)
    assert os.environ["MP_SCORE_MIN"] == "55"
    assert config.SOURCES["MP_SCORE_MIN"] == config.SHELL


def test_doctor_never_prints_secret_characters():
    # Le 2 octobre, doctor affichait les 10 premiers caractères de chaque secret, dont 10 des 16 du mot de passe
    # d'application Gmail ; sa sortie est collée telle quelle dans les conversations.
    from mp.cli import shown_value
    secrets = {"ANTHROPIC_API_KEY": "sk-ant-abcdefghijklmnopqrstuvwxyz0123456789",
               "AIRTABLE_PAT": "patABCDEFGHIJKLMN.0123456789abcdef", "GMAIL_APP_PASSWORD": "abcdwxyzefghstuv"}
    for key, value in secrets.items():
        shown = shown_value(key, value)
        assert shown == f"présente ({len(value)} caractères)"
        assert not any(value[i:i + 3] in shown for i in range(len(value) - 2))
    assert shown_value("GMAIL_USER", "x@gmail.com") == "x@gmail.com"
    assert shown_value("GMAIL_APP_PASSWORD", "") == "absente"
