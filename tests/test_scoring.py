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
