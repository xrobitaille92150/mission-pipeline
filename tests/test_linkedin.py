"""Page publique LinkedIn (jobs-guest) : parution et mode de candidature.

Les fragments ci-dessous reprennent les marqueurs relevés sur le Mac le 3 octobre 2026 sur sept offres réelles :
`posted-time-ago__text` (« il y a 3 jours », entouré d'espaces), `public_jobs_apply-link-onsite` (candidature
simplifiée, TOM People), `public_jobs_apply-link-offsite_…` (site de l'employeur : SimCorp, EY, PwC, Edmond de
Rothschild) et aucun bouton (KPMG, Citco).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from mp.linkedin import parse_job_html, posted_date

NOW = datetime(2026, 10, 3, 15, 0)
DESCRIPTION = ('<div class="show-more-less-html__markup">' + "Pilotage du PMO de la direction financière. " * 12
               + "</div></div>")
TOP = ('<h2 class="top-card-layout__title">Senior Manager – Digital Finance - PMO F/H</h2>'
       '<a class="topcard__org-name-link" href="#">KPMG France</a>'
       '<span class="topcard__flavor topcard__flavor--bullet">Courbevoie, Île-de-France, France</span>'
       '<span class="posted-time-ago__text topcard__flavor--metadata">\n            il y a 3 jours\n        </span>'
       '<figcaption class="num-applicants__caption">41 candidats</figcaption>')
ONSITE = ('<button class="apply-button apply-button--default top-card-layout__cta" '
          'data-tracking-control-name="public_jobs_apply-link-onsite">\n   Postuler\n </button>')
OFFSITE = ('<a class="sign-up-modal__outlet" data-tracking-control-name='
           '"public_jobs_apply-link-offsite_contextual-sign-in-modal_join-link">Inscrivez-vous maintenant</a>'
           '<button class="apply-button" data-tracking-control-name="public_jobs_apply-link-offsite_contextual-'
           'sign-in-modal_modal_dismiss">Postuler <icon data-svg-class-name="apply-button__offsite-apply-icon-svg">'
           '</icon></button>')


@pytest.mark.parametrize("button, mode, easy", [(ONSITE, "Simplifiée", True), (OFFSITE, "Site employeur", False),
                                                ("", "", False)])
def test_page_gives_posting_age_and_apply_mode(button, mode, easy):
    jd = parse_job_html(TOP + button + DESCRIPTION, now=NOW)
    assert jd.ok and jd.company == "KPMG France"
    assert (jd.posted_ago, jd.posted_on) == ("il y a 3 jours", "2026-09-30")
    assert (jd.apply_mode, jd.easy_apply) == (mode, easy)          # pas de bouton : mode inconnu, jamais deviné


@pytest.mark.parametrize("text, expected", [
    ("il y a 20 heures", "2026-10-02"), ("il y a 6 jours", "2026-09-27"), ("il y a 2 semaines", "2026-09-19"),
    ("il y a 1 mois", "2026-09-03"), ("Republiée il y a 2 jours", "2026-10-01"), ("5 days ago", "2026-09-28"),
    ("3 weeks ago", "2026-09-12"), ("il y a 12 minutes", "2026-10-03"), ("", ""), ("Nouveau", ""),
])
def test_posted_date_reads_linkedin_relative_ages(text, expected):
    assert posted_date(text, NOW) == expected
