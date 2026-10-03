from datetime import datetime

from mp.context import RunReport
from mp.digest import render


def _report() -> RunReport:
    r = RunReport(started=datetime(2026, 10, 3, 18, 30))
    r.ingest = {"emails": 1, "cartes": 6, "nouvelles": 4}
    r.decisions = {"postulees": 1, "ecartees": 2, "expirees": 0}
    r.notes = ["suivi des réponses : 3 email(s) lu(s), 1 événement(s) (1 créé(s), 0 mis à jour, 0 déjà à jour, "
               "0 à vérifier)",
               "Claude Code indisponible, dossier rédigé par l'API : <délai dépassé>",
               "12 appel(s) Claude — entrée 1000 tok (+0 cache lus, 0 écrits) / sortie 500 tok"]
    return r


def test_html_digest_shows_decisions_and_notes():
    """Le digest lu dans Gmail est la version HTML : rien de la version texte ne doit y manquer."""
    _, text, html = render(_report())
    for part in ("Décisions : 1 postulée(s), 2 écartée(s), 0 expirée(s).", "suivi des réponses : 3 email(s) lu(s)",
                 "12 appel(s) Claude", "Claude Code indisponible"):
        assert part in text
        assert part in html
    assert "{'postulees'" not in text
    assert "&lt;délai dépassé&gt;" in html and "<délai dépassé>" not in html


def test_html_digest_without_notes_or_decisions():
    r = _report()
    r.decisions, r.notes = {}, []
    _, text, html = render(r)
    assert "Décisions" not in text and "Décisions" not in html
    assert "<ul style='color:#666" not in html
