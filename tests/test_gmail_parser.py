from mp.gmail import (
    digest_source,
    parse_job_cards,
    parse_linkedin_status,
    query_job_digests,
    query_status_emails,
    split_location_mode,
)

DIGEST = """Votre alerte Emploi pour ("finance transformation" OR "programme manager" OR PMO) (Europe, Moyen-Orient et Afrique)Gérez vos alertes Emploi : https://www.linkedin.com/comm/jobs/alerts?lipi=x

Finance Transformation Director
Airalo
Espagne
Croissance rapide
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4444856066/?trackingId=abc

---------------------------------------------------------

Senior Programme Manager
DXC Technology
Royaume-Uni
Top candidat
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4472313699/?trackingId=def

---------------------------------------------------------

Senior Technical Project Manager - Financial Services (AI-first)
SymphonyAI
Allemagne

Cette entreprise recrute activement
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4462800910/?trackingId=ghi

---------------------------------------------------------

IT Project Manager
apreel
Pologne

Cette entreprise recrute activement
Postulez avec un CV et un profil
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4472183667/?trackingId=jkl

---------------------------------------------------------

Lead Consultant Finance D365 H/F
HSO
Paris (Hybride)
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4473738064/?trackingId=mno

---------------------------------------------------------

Head of Post-Merger-Integration (w/m/d)
Arsipa Gruppe
Berlin
Croissance rapide
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4472616058/?trackingId=pqr

---------------------------------------------------------

Voir toutes les offres d’emploi sur LinkedIn : https://www.linkedin.com/comm/jobs/search-results/?keywords=x&originToLandingJobPostings=4444856066,4472313699

----------------------------------------

Cet e-mail est destiné à Xavier Robitaille
"""


def test_parse_digest_six_cards():
    cards = parse_job_cards(DIGEST, source="Alerte")
    assert [c.job_id for c in cards] == ["4444856066", "4472313699", "4462800910", "4472183667",
                                         "4473738064", "4472616058"]
    a = cards[0]
    assert (a.title, a.employer, a.location) == ("Finance Transformation Director", "Airalo", "Espagne")
    assert a.alert_name.startswith('("finance transformation"')
    assert a.url == "https://www.linkedin.com/jobs/view/4444856066/"
    s = cards[2]
    assert (s.title, s.employer, s.location) == (
        "Senior Technical Project Manager - Financial Services (AI-first)", "SymphonyAI", "Allemagne")
    p = cards[3]
    assert p.easy_apply is True and p.location == "Pologne"
    h = cards[4]
    assert (h.location, h.mode) == ("Paris", "Hybride")
    assert cards[5].employer == "Arsipa Gruppe"


def test_header_glued_to_first_card():
    plain = ("De nouvelles offres d’emploi correspondent à vos préférences.\n"
             "Head of Financial Reporting\nSome Insurer\nLondres\n"
             "Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4458677759/?x=1\n")
    cards = parse_job_cards(plain, source="Recommandation")
    assert len(cards) == 1
    assert cards[0].title == "Head of Financial Reporting"
    assert cards[0].employer == "Some Insurer"
    assert cards[0].location == "Londres"


def test_url_on_next_line_and_separators():
    plain = ("Poste de X chez Y et plus encore\n\n----\nInvestment Accounting Manager\nAXA IM\nFrance (à distance)\n"
             "Voir l’offre d’emploi :\nhttps://www.linkedin.com/comm/jobs/view/4400000001/?a=b\n----\n")
    cards = parse_job_cards(plain)
    assert len(cards) == 1
    c = cards[0]
    assert (c.title, c.employer, c.location, c.mode) == ("Investment Accounting Manager", "AXA IM", "France", "À distance")


def test_dedup_within_email():
    plain = ("T1\nE1\nL1\nVoir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4400000002/\n\n---\n"
             "T1\nE1\nL1\nVoir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4400000002/\n")
    assert len(parse_job_cards(plain)) == 1


def test_split_location_mode():
    assert split_location_mode("Paris (Hybride)") == ("Paris", "Hybride")
    assert split_location_mode("France (à distance)") == ("France", "À distance")
    assert split_location_mode("Luxembourg, Luxembourg") == ("Luxembourg, Luxembourg", "")


def test_digest_source():
    assert digest_source("jobalerts-noreply@linkedin.com", "Finance Transformation Director chez Airalo") == "Alerte"
    assert digest_source("jobalerts-noreply@linkedin.com", "Votre alerte Emploi a été créée") is None
    assert digest_source("jobs-noreply@linkedin.com", "KONE recrute au poste de Head of Financial controlling") == "Recommandation"
    assert digest_source("jobs-noreply@linkedin.com",
                         "Xavier Robitaille, déposez votre candidature maintenant pour ‘X chez Y’") == "Enregistrée"
    assert digest_source("jobs-noreply@linkedin.com", "Xavier Robitaille, votre candidature a été envoyée à Quik Hire") is None
    assert digest_source("jobs-noreply@linkedin.com", "Votre candidature a été vue par PeersGroup") is None
    assert digest_source("hit-reply@linkedin.com", "Consultation Opportunity") is None


CONFIRMATION = """Votre candidature a été envoyée à Quik Hire Staffing

Strategic Project Manager (Finance) - Remote
Quik Hire Staffing
France
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4467445427/?trackingId=UOo

---------------------------------------------------------

Candidature envoyée le 29 septembre 2026
"""


def test_parse_linkedin_status_sent():
    r = parse_linkedin_status("Xavier Robitaille, votre candidature a été envoyée à Quik Hire Staffing", CONFIRMATION)
    assert r["status"] == "Envoyé"
    assert r["company"] == "Quik Hire Staffing"
    assert r["job_id"] == "4467445427"
    assert r["title"] == "Strategic Project Manager (Finance) - Remote"
    assert r["url"] == "https://www.linkedin.com/jobs/view/4467445427/"


def test_parse_linkedin_status_viewed_and_rejected():
    assert parse_linkedin_status("Votre candidature a été vue par PeersGroup", "")["status"] == "A/R"
    assert parse_linkedin_status("Your application was viewed by Swiss Re", "")["company"] == "Swiss Re"
    assert parse_linkedin_status("Dernière nouvelle de Deloitte", "")["status"] == "Non"
    assert parse_linkedin_status("Poste de X chez Y et plus encore", "") is None


def test_queries_exclude_processed_label_at_runtime():
    assert "newer_than:2d" in query_job_digests(2)
    assert "from:jobalerts-noreply@linkedin.com" in query_job_digests(2)
    assert "subject:candidature" in query_status_emails(3)
