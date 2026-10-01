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
