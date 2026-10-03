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
    for icon in c.get("/manifest.webmanifest").json()["icons"]:
        assert c.get(icon["src"]).status_code == 200, icon["src"]
    assert c.get("/apple-touch-icon.png").content == c.get("/icon.png").content


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
    assert "dossier" in s["drive"]


def test_cli_drive_sync_parser():
    from mp.cli import build_parser
    a = build_parser().parse_args(["drive-sync", "--days", "30"])
    assert a.days == 30 and a.fn.__name__ == "cmd_drive_sync"


def test_nouvelle_offre_validation():
    c, _ = _client()
    assert c.post("/api/offres/nouvelle", json={}).status_code == 400
    assert c.post("/api/offres/nouvelle", json={"url": "https://example.com/emploi/12"}).status_code == 400
    assert c.post("/api/offres/nouvelle", json={"texte": "court"}).status_code == 400
    long = "Mission de pilotage de programme IFRS 17 pour un assureur vie. " * 5
    r = c.post("/api/offres/nouvelle", json={"texte": long})
    assert r.status_code == 400 and "employeur" in r.json()["detail"]


def test_nouvelle_offre_from_text_creates_row_and_starts_job(monkeypatch):
    c, ctx = _client()
    started: list[str] = []
    monkeypatch.setattr("mp.pipeline.process_new_offer", lambda ctx_, rid: started.append(rid) or "ok")
    long = "Mission de pilotage de programme IFRS 17 pour un assureur vie. " * 5
    o = c.post("/api/offres/nouvelle", json={"texte": long, "poste": "Directeur IFRS 17", "employeur": "Generali",
                                             "lieu": "Paris"}).json()
    assert o["employeur"] == "Generali" and o["poste"] == "Directeur IFRS 17" and o["statut"] == "À étudier"
    row = next(r for r in ctx.at.tables_data["OFFRES"] if r["id"] == o["id"])["fields"]
    assert row["Source"] == "Manuelle" and row["Description"].startswith("Mission de pilotage")
    _wait_jobs()
    assert started == [o["id"]]


def test_nouvelle_offre_from_linkedin_url_fetches_the_posting(monkeypatch):
    from mp.models import JobDescription
    c, ctx = _client()
    monkeypatch.setattr("mp.pipeline.process_new_offer", lambda ctx_, rid: "ok")
    monkeypatch.setattr("mp.pipeline.fetch_jd", lambda jid: JobDescription(
        ok=True, text="Fiche complète " * 30, title="Head of Finance Transformation", company="SCOR", location="Paris"))
    o = c.post("/api/offres/nouvelle", json={"url": "https://www.linkedin.com/jobs/view/4471234567/"}).json()
    assert o["jobId"] == "4471234567" and o["employeur"] == "SCOR" and o["poste"] == "Head of Finance Transformation"
    assert o["url"] == "https://www.linkedin.com/jobs/view/4471234567/"
    # même lien une seconde fois : la ligne existante est reprise, pas de doublon
    o2 = c.post("/api/offres/nouvelle", json={"url": "https://www.linkedin.com/jobs/view/4471234567/"}).json()
    assert o2["id"] == o["id"]
    _wait_jobs()


def test_offer_summary_and_criteria_for_the_cockpit():
    # Demande de Xavier (3 octobre) : comprendre le poste sans aller sur LinkedIn.
    c, ctx = _client()
    ctx.at.tables_data["OFFRES"][0]["fields"].update({
        "Note rôle": "Allianz cherche un directeur de programme pour sa transformation finance. Mission de 12 mois.",
        "Note critères": "✓ Domaine : transformation finance (C)\n✗ Rémunération : 700 €/j\n? Langue : non précisé",
    })
    ctx.at.tables_data["OFFRES"][1]["fields"]["Note critères"] = "- Séniorité : 7+ ans requis"   # ancienne note
    items = {o["id"]: o for o in c.get("/api/offres?tab=decider").json()["items"]}
    assert items["rec1"]["resume_court"] == "Allianz cherche un directeur de programme pour sa transformation finance."
    d = c.get("/api/offres/rec1").json()
    assert d["resume"].endswith("Mission de 12 mois.")
    assert [x["statut"] for x in d["criteres"]] == ["ok", "ecart", "inconnu"]
    assert d["criteres"][1]["texte"] == "Rémunération : 700 €/j"
    assert c.get("/api/offres/rec2").json()["criteres"] == [{"statut": "", "texte": "Séniorité : 7+ ans requis"}]
    page = c.get("/").text
    assert "L'offre en bref" in page and "Tes critères" in page


def test_cluster_name_posting_age_and_apply_mode_for_the_cockpit(monkeypatch):
    # Demande de Xavier (3 octobre) : « Que signifie le C ? », date de parution, candidature simplifiée ou non.
    from datetime import date, timedelta

    from mp.app import _published
    c, ctx = _client()
    three_days_ago = (date.today() - timedelta(days=3)).isoformat()
    ctx.at.tables_data["OFFRES"][0]["fields"].update({
        "Cluster": "C — Transformation/PMO", "Publiée le": three_days_ago, "Mode candidature": "Site employeur"})
    ctx.at.tables_data["OFFRES"][1]["fields"].update({"Cluster": "Hors-axe", "Easy Apply": True})
    items = {o["id"]: o for o in c.get("/api/offres?tab=decider").json()["items"]}
    o1, o2 = items["rec1"], items["rec2"]
    assert (o1["cluster_nom"], o1["publiee"], o1["candidature"]) == ("Transformation/PMO", "publiée il y a 3 j",
                                                                     "Site employeur")
    # ancienne ligne : pas de date ni de mode lus sur la page, mais le badge « Candidature simplifiée » de l'email
    assert (o2["cluster_nom"], o2["publiee"], o2["candidature"]) == ("Hors-axe", "", "Simplifiée")
    today = date(2026, 10, 3)
    assert [_published(d, today) for d in ("2026-10-03", "2026-10-02", "2026-09-27", "2026-09-19", "2026-08-01",
                                           "2025-06-01", "", "n/a")] == [
        "publiée aujourd'hui", "publiée hier", "publiée il y a 6 j", "publiée il y a 2 sem.",
        "publiée il y a 2 mois", "publiée il y a plus d'un an", "", ""]
    page = c.get("/").text
    assert "cluster_nom" in page and "Candidature simplifiée" in page and "o.publiee" in page


def test_offer_added_from_a_link_keeps_posting_date_and_apply_mode(monkeypatch):
    from mp.models import JobDescription
    c, ctx = _client()
    monkeypatch.setattr("mp.pipeline.process_new_offer", lambda ctx_, rid: "ok")
    monkeypatch.setattr("mp.pipeline.fetch_jd", lambda jid: JobDescription(
        ok=True, text="Fiche complète " * 30, title="Head of Finance Transformation", company="SCOR", location="Paris",
        posted_on="2026-10-01", apply_mode="Simplifiée"))
    o = c.post("/api/offres/nouvelle", json={"url": "https://www.linkedin.com/jobs/view/4471234568/"}).json()
    f = ctx.at.get(ctx.offres, o["id"])["fields"]
    assert (f["Publiée le"], f["Mode candidature"], f["Easy Apply"]) == ("2026-10-01", "Simplifiée", True)
    _wait_jobs()
