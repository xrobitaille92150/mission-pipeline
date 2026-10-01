import os

from mp import config


def test_env_files_precedence_and_provenance(tmp_path, monkeypatch):
    (tmp_path / "airtable.env").write_text("AIRTABLE_PAT=pat_a\nANTHROPIC_API_KEY=old-key-in-wrong-file\n")
    (tmp_path / "anthropic.env").write_text('ANTHROPIC_API_KEY="new-key"   # commentaire\nGMAIL_USER=x@y\n')
    for k in ("AIRTABLE_PAT", "ANTHROPIC_API_KEY", "GMAIL_USER", "GMAIL_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "from-shell")
    monkeypatch.setattr(config, "SOURCES", {})
    monkeypatch.setattr(config, "DUPLICATES", {})
    config.load_env_files(tmp_path)
    # premier fichier (ordre alphabétique) gagnant ; le doublon est signalé, pas appliqué
    assert os.environ["ANTHROPIC_API_KEY"] == "old-key-in-wrong-file"
    assert config.SOURCES["ANTHROPIC_API_KEY"] == "airtable.env"
    assert config.DUPLICATES["ANTHROPIC_API_KEY"] == ["anthropic.env"]
    # guillemets et commentaire retirés
    assert os.environ["GMAIL_USER"] == "x@y" and config.SOURCES["GMAIL_USER"] == "anthropic.env"
    # une variable exportée par le shell prime sur les fichiers
    assert os.environ["GMAIL_APP_PASSWORD"] == "from-shell"
    assert "GMAIL_APP_PASSWORD" not in config.SOURCES
