"""Copie des dossiers vers le miroir Drive : réglage par défaut et rattrapage depuis Airtable."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from mp import config, drive
from tests.conftest import FakeAirtable, FakeContext


def _ctx(tmp_path, recs):
    ctx = FakeContext(at=FakeAirtable({"OFFRES": recs}, formulas=True), tmp=tmp_path)
    ctx.s = replace(ctx.s, drive_dossiers_dir=tmp_path / "Drive" / "dossiers")
    return ctx


def _att(name, size=None):
    return {"filename": name, "url": f"https://dl.airtable/{name}", "size": size}


def test_sync_downloads_missing_files_once(tmp_path):
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=40)).isoformat()
    recs = [
        {"id": "rec1", "fields": {"Employeur": "AXA", "Dossier le": today,
                                  "CV (fichiers)": [_att("CV_AXA.pdf", 3), _att("CV_AXA.docx", 3)],
                                  "Lettre (fichiers)": [_att("LM_AXA.pdf", 3)]}},
        {"id": "rec2", "fields": {"Employeur": "Vieux", "Dossier le": old, "CV (fichiers)": [_att("CV_old.pdf")]}},
        {"id": "rec3", "fields": {"Employeur": "Sans dossier", "CV (fichiers)": [_att("CV_x.pdf")]}},
    ]
    ctx = _ctx(tmp_path, recs)
    calls = []

    def fake_fetch(url, target):
        calls.append(url)
        target.write_bytes(b"abc")

    s1 = drive.sync(ctx, days=14, fetch=fake_fetch)
    assert s1["copies"] == 3 and s1["offres"] == 1 and s1["erreurs"] == 0
    assert (tmp_path / "Drive" / "dossiers" / today / "CV_AXA.docx").read_bytes() == b"abc"
    s2 = drive.sync(ctx, days=14, fetch=fake_fetch)       # idempotent : rien de retéléchargé
    assert s2["copies"] == 0 and s2["deja"] == 3 and len(calls) == 3


def test_sync_counts_errors_and_continues(tmp_path):
    today = date.today().isoformat()
    ctx = _ctx(tmp_path, [{"id": "r", "fields": {"Dossier le": today,
                                                 "CV (fichiers)": [_att("a.pdf"), _att("b.pdf")]}}])

    def flaky(url, target):
        if url.endswith("a.pdf"):
            raise OSError("réseau")
        target.write_bytes(b"x")

    s = drive.sync(ctx, fetch=flaky)
    assert s["erreurs"] == 1 and s["copies"] == 1


def test_sync_without_drive_dir_does_nothing(tmp_path):
    ctx = _ctx(tmp_path, [])
    ctx.s = replace(ctx.s, drive_dossiers_dir=None)
    assert drive.sync(ctx)["copies"] == 0


def test_drive_dir_default_and_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DRIVE_DEFAULT", tmp_path / "Candidatures" / "dossiers")
    monkeypatch.delenv("MP_DRIVE_DOSSIERS_DIR", raising=False)
    assert config._drive_dir() is None                    # Candidatures absent : pas de copie, rien de créé
    (tmp_path / "Candidatures").mkdir()
    assert config._drive_dir() == tmp_path / "Candidatures" / "dossiers"
    monkeypatch.setenv("MP_DRIVE_DOSSIERS_DIR", "off")
    assert config._drive_dir() is None
    monkeypatch.setenv("MP_DRIVE_DOSSIERS_DIR", str(tmp_path / "ailleurs"))
    assert config._drive_dir() == tmp_path / "ailleurs"
