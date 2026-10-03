from mp.models import JobDescription, Scoring
from mp.pipeline import scoring_fields
from mp.scoring import detect_language, excluded_scoring, hard_filter, rank_for_dossiers, score_offer
from tests.conftest import FakeClaude

FR = ("Nous recherchons un consultant senior pour accompagner la transformation de la fonction finance "
      "au sein de notre équipe. Vous interviendrez sur les missions de clôture et de reporting avec les équipes.")
EN = ("We are looking for a senior consultant to lead the finance transformation programme within our team. "
      "You will work with the CFO office on closing and reporting, and your experience will be key.")
DE = ("Wir suchen einen Senior Consultant für die Transformation der Finanzfunktion. Sie arbeiten mit dem Team "
      "an der Umsetzung und bringen Ihre Kenntnisse ein. Ihre Aufgaben sind vielfältig und nicht einfach.")
NL = ("Wij zoeken een senior consultant voor de transformatie van de financiële functie. Je werkt met het team "
      "aan de implementatie en brengt je ervaring in bij onze klanten voor deze functie.")


def test_detect_language():
    assert detect_language(FR) == "FR"
    assert detect_language(EN) == "EN"
    assert detect_language(DE) == "AUTRE"
    assert detect_language(NL) == "AUTRE"
    assert detect_language("") == "EN"


def test_hard_filter_junior_and_geo():
    assert hard_filter("Junior Financial Analyst", "Paris", None) is not None
    assert hard_filter("Senior Financial Analyst", "Paris", None) is None
    assert hard_filter("Strategic Project Lead, Finance", "Amérique du Nord", None) is not None
    assert hard_filter("Head of Finance", "Dublin", None) is None
    jd = JobDescription(ok=True, text=DE * 3)
    assert "langue" in hard_filter("Senior Consultant CFO Service (m/w/d)", "Allemagne", jd)
    assert hard_filter("Senior Consultant", "Paris", JobDescription(ok=True, text=FR * 3)) is None


def _scoring(**kw) -> dict:
    base = dict(cluster="C", posture="projet", langue="FR", pays="France", mode="hybride", contrat="freelance",
                junior=False, score=78, verdict="POSTULER", pourquoi=["Cluster C", "Paris hybride"],
                red_flags=[], profil_cv="FinanceTransformation", mots_cles=["PMO", "IFRS 17"])
    base.update(kw)
    return base


def test_score_offer_caps_without_jd():
    claude = FakeClaude([_scoring(score=85, verdict="POSTULER")])
    s = score_offer(claude, title="PMO Finance", employer="AXA", location="Paris", mode="", source="Alerte", jd=None)
    assert s.score == 69 and s.verdict == "ETUDIER"
    assert "FICHE DE POSTE : non disponible" in claude.prompts[0]["user"]
    assert len(claude.prompts[0]["system"]) == 2


def test_score_offer_with_jd():
    claude = FakeClaude([_scoring(score=85)])
    jd = JobDescription(ok=True, text=FR, criteria={"Niveau": "Senior"})
    s = score_offer(claude, title="PMO Finance", employer="AXA", location="Paris", mode="", source="Alerte", jd=jd)
    assert s.score == 85 and s.verdict == "POSTULER"
    assert "Critères LinkedIn : Niveau: Senior" in claude.prompts[0]["user"]


def test_scoring_validation_clamps():
    s = Scoring.model_validate(_scoring(score=140, pourquoi=["  a ", ""]))
    assert s.score == 100 and s.pourquoi == ["a"]


def test_excluded_and_fields_mapping():
    s = excluded_scoring("niveau junior / analyst dans l'intitulé")
    assert s.verdict == "ECARTER" and s.score == 0 and s.junior
    f = scoring_fields(Scoring.model_validate(_scoring()), JobDescription(ok=True, text=FR, easy_apply=True), True)
    assert f["Cluster"] == "C — Transformation/PMO" and f["Verdict IA"] == "Postuler"
    assert f["Mode"] == "Hybride" and f["Contrat"] == "Freelance" and f["Statut"] == "À étudier"
    assert f["Description"] == FR and f["Easy Apply"] is True
    assert f["Pourquoi"].startswith("• ")
    f2 = scoring_fields(s, None, False)
    assert f2["Statut"] == "Écartée" and "Description" not in f2


def test_rank_for_dossiers():
    scored = [("r1", Scoring.model_validate(_scoring(score=72))),
              ("r2", Scoring.model_validate(_scoring(score=91))),
              ("r3", Scoring.model_validate(_scoring(score=65, verdict="ETUDIER"))),
              ("r4", Scoring.model_validate(_scoring(score=80))),
              ("r5", Scoring.model_validate(_scoring(score=55)))]
    ranks = rank_for_dossiers(scored, score_min=60, max_auto=2)
    assert ranks == {"r2": 1, "r4": 2}


def _sub(**kw) -> dict:
    base = dict(cluster="C", posture="projet", langue="FR", pays="France", europe=True, mode="hybride",
                contrat="freelance", junior=False, fit=36, seniorite=13, geo=19, format_poste=8, signaux=6,
                pourquoi=["x"], red_flags=[], profil_cv="FinanceTransformation", mots_cles=[])
    base.update(kw)
    return base


def test_subscores_sum_and_verdict():
    s = Scoring.model_validate(_sub())
    assert s.score == 82 and s.verdict == "POSTULER"
    assert "adéquation 36/40" in s.detail and "signaux 6/10" in s.detail
    s = Scoring.model_validate(_sub(fit=30, geo=9, format_poste=6, signaux=4))       # UK, CDD
    assert s.score == 62 and s.verdict == "ETUDIER"
    s = Scoring.model_validate(_sub(fit=18, geo=11, format_poste=4, signaux=2))      # cluster A Londres
    assert s.score == 48 and s.verdict == "ECARTER"


def test_subscores_caps():
    assert Scoring.model_validate(_sub(junior=True)).score <= 15
    assert Scoring.model_validate(_sub(langue="AUTRE")).verdict == "ECARTER"
    assert Scoring.model_validate(_sub(europe=False)).score <= 20
    assert Scoring.model_validate(_sub(cluster="HORS_AXE")).score <= 40
    assert Scoring.model_validate(_sub(fit=12)).score <= 49                          # sans adéquation, jamais Étudier
    s = Scoring.model_validate(_sub(fit=99, seniorite=99, geo=99, format_poste=99, signaux=99))
    assert s.score == 100 and (s.fit, s.geo) == (40, 20)                              # bornes par dimension


def test_scoring_fields_includes_detail():
    f = scoring_fields(Scoring.model_validate(_sub()), None, False)
    assert f["Pourquoi"].endswith("signaux 6/10") and f["Score"] == 82


# ---------------------------------------------------------------------------
# Notation d'une offre isolée (cockpit « Ajouter une offre », mp dossier) et boucle du run
# ---------------------------------------------------------------------------

DESC = "Pilotage d'un programme de transformation finance pour un assureur, PMO, IFRS 17. " * 6


def test_score_one_writes_fields_and_report():
    from mp.pipeline import score_one
    from tests.conftest import FakeAirtable, FakeClaude, FakeContext
    rec = {"id": "rec1", "fields": {"jobId": "1", "Poste": "Directeur de programme", "Employeur": "AXA",
                                    "Lieu": "Paris", "Description": DESC}}
    ctx = FakeContext(at=FakeAirtable({"OFFRES": [rec]}), claude=FakeClaude([_sub()]))
    s = score_one(ctx, rec)
    assert s.score == 82
    f = ctx.at.tables_data["OFFRES"][0]["fields"]
    assert f["Score"] == 82 and f["Statut"] == "À étudier" and f["Scoré le"]
    assert ctx.report.scored[0]["employer"] == "AXA"


def test_score_run_ranks_best_offer_for_a_dossier():
    from mp.pipeline import score
    from tests.conftest import FakeAirtable, FakeClaude, FakeContext
    rows = [{"id": "rec1", "fields": {"jobId": "1", "Poste": "Directeur de programme", "Employeur": "AXA",
                                      "Lieu": "Paris", "Description": DESC, "Date 1ère vue": "2026-10-01"}}]
    ctx = FakeContext(at=FakeAirtable({"OFFRES": rows}, formulas=True), claude=FakeClaude([_sub()]))
    score(ctx)
    f = ctx.at.tables_data["OFFRES"][0]["fields"]
    assert f["Préparer dossier"] is True and f["Rang du jour"] == 1


def test_process_new_offer_scores_then_builds_dossier(monkeypatch):
    import mp.pipeline as pl
    from tests.conftest import FakeAirtable, FakeClaude, FakeContext
    rows = [{"id": "rec9", "fields": {"jobId": "manuel-1", "Poste": "PMO", "Employeur": "Allianz", "Lieu": "Paris",
                                      "Description": DESC, "Statut": "À étudier"}}]
    ctx = FakeContext(at=FakeAirtable({"OFFRES": rows}), claude=FakeClaude([_sub()]))
    monkeypatch.setattr(pl, "make_dossier", lambda c, rec: {"profile": "FinanceTransformation", "lang": "FR"})
    msg = pl.process_new_offer(ctx, "rec9")
    assert msg.startswith("notée 82/100") and "dossier prêt (FinanceTransformation, FR)" in msg
    monkeypatch.setattr(pl, "make_dossier", lambda c, rec: None)
    import pytest
    with pytest.raises(RuntimeError):                        # déjà notée : pas de second appel Claude
        pl.process_new_offer(ctx, "rec9")


# ---------------------------------------------------------------------------
# Notes du cockpit : « L'offre en bref » et « Tes critères » (colonnes Note rôle / Note critères)
# ---------------------------------------------------------------------------

NOTES = {"resume": "Alpha FMC recrute un Senior Consultant pour son équipe Insurance Finance Transformation à Paris. "
                   "Missions : comptabilité des investissements, IFRS 9 / IFRS 17, implémentation SimCorp.",
         "criteres": [{"critere": "Domaine", "constat": "investissement et transformation finance", "statut": "ok"},
                      {"critere": "Rémunération", "constat": "CDI à 55 k€ de base : très en deçà de la cible",
                       "statut": "ecart"},
                      {"critere": "Langue", "constat": "non précisé dans l'annonce", "statut": "inconnu"}]}


def test_scoring_carries_notes_and_writes_them():
    from mp.pipeline import scoring_fields
    claude = FakeClaude([_sub(**NOTES)])
    jd = JobDescription(ok=True, text=DESC)
    s = score_offer(claude, title="Senior Consultant", employer="Alpha FMC", location="Paris", mode="",
                    source="Alerte", jd=jd)
    assert "Tes critères" not in claude.prompts[0]["system"][0]
    assert "Notes pour Xavier" in claude.prompts[0]["system"][1]          # consigne notes.md dans la notation
    assert claude.prompts[0]["schema"]["required"][-2:] == ["resume", "criteres"]
    f = scoring_fields(s, jd, False)
    assert f["Note rôle"].startswith("Alpha FMC recrute")
    assert f["Note critères"].splitlines() == [
        "✓ Domaine : investissement et transformation finance",
        "✗ Rémunération : CDI à 55 k€ de base : très en deçà de la cible",
        "? Langue : non précisé dans l'annonce"]
    # offre exclue par filtre dur : pas de notes, rien n'est écrasé
    assert "Note rôle" not in scoring_fields(excluded_scoring("poste hors Europe"), None, False)


def test_scoring_writes_posting_date_and_apply_mode_read_on_the_page():
    from mp.pipeline import scoring_fields
    page = JobDescription(ok=True, text=DESC, posted_on="2026-09-30", apply_mode="Simplifiée")
    s = excluded_scoring("poste hors Europe")
    f = scoring_fields(s, page, True)
    assert (f["Publiée le"], f["Mode candidature"], f["Easy Apply"]) == ("2026-09-30", "Simplifiée", True)
    # fiche reprise du champ Description (rien de lu sur LinkedIn) : rien n'est écrit
    assert "Publiée le" not in scoring_fields(s, page, False)
    # page sans bouton « Postuler » : le mode reste vide, jamais deviné
    f = scoring_fields(s, JobDescription(ok=True, text=DESC, posted_on="2026-10-01"), True)
    assert "Mode candidature" not in f and "Easy Apply" not in f


def test_refresh_notes_only_for_active_offers_without_v3_notes(monkeypatch):
    import mp.pipeline as pl
    from mp.pipeline import is_v3_notes, refresh_notes
    from tests.conftest import FakeAirtable, FakeClaude, FakeContext
    old = "- Séniorité : 7+ ans requis\n- Contrat : CDI → écart avec TJM >= 800 EUR/j"
    rows = [
        {"id": "r1", "fields": {"jobId": "1", "Poste": "PMO", "Employeur": "AXA", "Statut": "À étudier", "Score": 80,
                                "Description": DESC, "Note critères": old}},
        {"id": "r2", "fields": {"jobId": "2", "Poste": "Head", "Employeur": "CNP", "Statut": "Dossier prêt",
                                "Score": 75, "Description": DESC, "Note critères": "✓ Domaine : B"}},
        {"id": "r3", "fields": {"jobId": "3", "Poste": "Analyst", "Employeur": "X", "Statut": "Écartée", "Score": 20,
                                "Description": DESC}},
        {"id": "r4", "fields": {"jobId": "manuel-2026-10-03-1", "Poste": "PMO", "Employeur": "Y", "Score": 70,
                                "Statut": "À étudier", "Description": DESC, "Note critères": "? Langue : non précisé"}},
    ]
    pages = {"1": JobDescription(ok=True, text=DESC, posted_on="2026-09-30", apply_mode="Site employeur"),
             "2": JobDescription(ok=True, text=DESC, posted_on="2026-10-01", apply_mode="Simplifiée")}
    fetched = []
    monkeypatch.setattr(pl, "fetch_jd", lambda jid: fetched.append(jid) or pages[jid])
    ctx = FakeContext(at=FakeAirtable({"OFFRES": rows}, formulas=True), claude=FakeClaude([NOTES]))
    stats = refresh_notes(ctx)
    assert stats == {"offres": 3, "notes": 1, "parution": 2, "deja": 1, "erreurs": 0}
    f1, f2, f3, f4 = (r["fields"] for r in ctx.at.tables_data["OFFRES"])
    assert is_v3_notes(f1["Note critères"]) and f1["Note rôle"].startswith("Alpha FMC")
    assert f1["Score"] == 80 and "Scoré le" not in f1                 # le score n'est pas touché
    assert f2["Note critères"] == "✓ Domaine : B" and "Note rôle" not in f3
    # parution et mode lus sur la page, sans appel Claude pour r2 ; offre manuelle (sans page LinkedIn) sautée
    assert (f1["Publiée le"], f1["Mode candidature"], f1.get("Easy Apply")) == ("2026-09-30", "Site employeur", None)
    assert (f2["Publiée le"], f2["Mode candidature"], f2["Easy Apply"]) == ("2026-10-01", "Simplifiée", True)
    assert fetched == ["1", "2"] and "Publiée le" not in f4
    assert "Tes critères" not in ctx.claude.prompts[0]["system"][0] and len(ctx.claude.prompts) == 1
    again = refresh_notes(ctx)                                         # rejouable : tout est déjà fait
    assert (again["notes"], again["parution"], again["deja"], fetched) == (0, 0, 3, ["1", "2"])
