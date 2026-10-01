"""Cockpit mobile (`mp app`) : API testée avec les doubles Airtable / Claude, sans réseau."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from mp import app as appmod
from mp.app import create_app
from tests.conftest import FakeAirtable, FakeClaude, FakeContext

LETTRE = ("Madame, Monsieur, " + "l'enjeu auquel doit faire face votre direction financière est peu commun. " * 12
          + "Je serais heureux d'en discuter avec vous. Bien cordialement. Xavier Robitaille")


def _offres() -> list[dict]:
    return [
        {"id": "rec1", "fields": {"jobId": "1", "Poste": "Directeur de programme", "Employeur": "Allianz",
                                  "Lieu": "Paris", "Score": 82, "Verdict IA": {"name": "POSTULER"},
                                  "Statut": {"name": "À étudier"}, "Pourquoi": "• bon fit\n• hybride",
                                  "Red flags": "", "Rang du jour": 1, "URL": "https://www.linkedin.com/jobs/view/1/",
                                  "Langue": {"name": "FR"}, "Lettre texte": LETTRE,
                                  "CV (fichiers)": [{"filename": "cv.pdf", "url": "https://x/cv.pdf", "type": "application/pdf"}]}},
        {"id": "rec2", "fields": {"jobId": "2", "Poste": "PMO IFRS 17", "Employeur": "AXA", "Score": 61,
                                  "Statut": {"name": "À étudier"}, "Rang du jour": None}},
    ]


def _client(claude: FakeClaude | None = None, tmp=None):
    at = FakeAirtable({"OFFRES": _offres(), "CANDIDATURES": []}, formulas=True)
    ctx = FakeContext(at=at, claude=claude, tmp=tmp)
    return TestClient(create_app(ctx)), ctx


def _wait_jobs(timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while time.time() < end and any(j["status"] == "running" for j in appmod.JOBS.items.values()):
        time.sleep(0.05)


def test_eval_formula_subset():
    from tests.conftest import eval_formula
    f = {"Statut": {"name": "À étudier"}, "Je postule": True, "Date 1ère vue": "2026-09-01", "Erreur": ""}
    assert eval_formula("AND({Je postule}=1, NOT({Statut}='Postulée'))", f)
    assert not eval_formula("AND({J'écarte}=1, NOT({Statut}='Écartée'))", f)
    assert eval_formula("IS_BEFORE(IF({Dernière vue},{Dernière vue},{Date 1ère vue}),DATETIME_PARSE('2026-09-17','YYYY-MM-DD'))", f)
    assert not eval_formula("{Erreur}", f) and eval_formula("NOT({Scoré le})", f)
    assert not eval_formula("IS_AFTER({Date 1ère vue},DATEADD(TODAY(),-1,'days'))", f)


def test_index_manifest_and_icon():
    c, _ = _client()
    assert "<title>Missions</title>" in c.get("/").text
    assert c.get("/manifest.webmanifest").json()["display"] == "standalone"
    assert c.get("/icon.png").headers["content-type"] == "image/png"


def test_list_tab_sorted_by_rank_then_score():
    c, ctx = _client()
    r = c.get("/api/offres?tab=decider").json()
    assert r["count"] == 2 and [o["id"] for o in r["items"]] == ["rec1", "rec2"]
    first = r["items"][0]
    assert first["verdict"] == "POSTULER" and first["pourquoi"] == ["bon fit", "hybride"]
    assert first["cv"][0]["name"] == "cv.pdf" and "airtable.com" in first["airtable_url"]
    assert "lettre_texte" not in first                      # la liste ne charge pas les gros champs
    assert c.get("/api/offres?tab=inconnu").status_code == 400
    assert ctx.at.calls[-1][2] == appmod.TABS["decider"][0]  # la formule de l'onglet est bien passée à Airtable


def test_detail_and_unknown():
    c, _ = _client()
    d = c.get("/api/offres/rec1").json()
    assert d["lettre_texte"] == LETTRE and d["description"] == "" and d["objections"] == []
    assert c.get("/api/offres/recZZ").status_code == 404


def test_decision_ecarte_and_annule():
    c, ctx = _client()
    d = c.post("/api/offres/rec2/decision", json={"action": "ecarte"}).json()
    assert d["j_ecarte"] is True and d["statut"] == "Écartée"       # sync immédiat
    d = c.post("/api/offres/rec2/decision", json={"action": "annule"}).json()
    assert d["j_ecarte"] is False and d["je_postule"] is False
    assert c.post("/api/offres/rec2/decision", json={"action": "boum"}).status_code == 400


def test_decision_postule_creates_candidature_and_starts_dossier(monkeypatch):
    c, ctx = _client()
    built: list[str] = []

    def fake_make_dossier(ctx_, rec):
        built.append(rec["id"])
        ctx_.at.patch(ctx_.offres, rec["id"], {"Dossier le": "2026-10-01", "Statut": "Postulée"})
        return {"profile": "FT", "lang": "FR"}

    monkeypatch.setattr("mp.pipeline.make_dossier", fake_make_dossier)
    d = c.post("/api/offres/rec1/decision", json={"action": "postule"}).json()
    assert d["je_postule"] is True and d["statut"] == "Postulée" and d["date_postule"]
    assert len(ctx.at.tables_data["CANDIDATURES"]) == 1       # ligne Candidatures créée par sync_decisions
    _wait_jobs()
    assert built == ["rec1"]
    assert c.get("/api/offres/rec1").json()["dossier_le"] == "2026-10-01"


def test_decision_postule_reports_sync_guard(monkeypatch):
    c, ctx = _client()
    monkeypatch.setattr("mp.pipeline.MAX_POSTULE_PER_RUN", 0)
    monkeypatch.setattr("mp.pipeline.make_dossier", lambda ctx_, rec: {"profile": "FT", "lang": "FR"})
    d = c.post("/api/offres/rec2/decision", json={"action": "postule"}).json()
    assert "erreur_sync" in d and "--force" in d["erreur_sync"]
    _wait_jobs()


def test_letter_proposer_and_enregistrer(monkeypatch, tmp_path):
    claude = FakeClaude([{"lettre": LETTRE.replace("peu commun", "singulier"), "objections": ["TJM"]}])
    c, ctx = _client(claude, tmp=tmp_path)
    # pas de lettre → 409 ; consigne vide → 400
    assert c.post("/api/offres/rec2/lettre/proposer", json={"consigne": "plus court"}).status_code == 409
    assert c.post("/api/offres/rec1/lettre/proposer", json={"consigne": "  "}).status_code == 400
    r = c.post("/api/offres/rec1/lettre/proposer", json={"consigne": "insiste sur IFRS 17"}).json()
    assert "singulier" in r["lettre"] and r["objections"] == ["TJM"] and r["mots"] > 100
    assert "CONSIGNE DE XAVIER : insiste sur IFRS 17" in claude.prompts[-1]["user"]
    assert "LETTRE ACTUELLE" in claude.prompts[-1]["user"]

    files: list[tuple] = []
    monkeypatch.setattr(appmod, "_letter_files", lambda ctx_, rec, text: files.append((rec["id"], text)) or "ok")
    assert c.put("/api/offres/rec1/lettre", json={"texte": "trop court"}).status_code == 400
    r = c.put("/api/offres/rec1/lettre", json={"texte": r["lettre"]}).json()
    assert r["ok"] and r["job"]["kind"] == "lettre"
    _wait_jobs()
    assert files and files[0][0] == "rec1" and "singulier" in files[0][1]
    assert "singulier" in ctx.at.get("OFFRES", "rec1")["fields"]["Lettre texte"]
    j = c.get("/api/jobs/" + r["job"]["id"]).json()
    assert j["status"] == "done" and j["message"] == "ok"
    assert c.get("/api/jobs/nope").status_code == 404


def test_run_and_sante(monkeypatch):
    c, _ = _client()
    calls: list = []
    monkeypatch.setattr("mp.cli.cmd_run", lambda args: calls.append(args.label) or 0)
    r = c.post("/api/run").json()
    assert r["kind"] == "run"
    _wait_jobs()
    assert calls == ["appli"]
    s = c.get("/api/sante").json()
    assert s["version"] and s["run_en_cours"] is False
    assert set(s["compteurs"]) == {"decider", "dossiers", "postulees_7j", "erreurs", "nouvelles_24h"}
