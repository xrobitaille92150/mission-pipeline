from mp.airtable import fstr, record_url
from mp.cli import build_parser
from mp.models import Card, StatusEvent
from mp.pipeline import card_to_fields
from mp.tracking import CandidaturesIndex, apply_event, norm_company, rank, upsert_candidature
from tests.conftest import FakeAirtable, FakeContext


def test_card_to_fields():
    c = Card(job_id="1", url="u", title="T", employer="E", location="Paris", mode="Hybride",
             alert_name="recherche B1", easy_apply=True, source="Alerte", email_date="2026-10-01")
    f = card_to_fields(c)
    assert f["Statut"] == "Nouvelle" and f["Mode"] == "Hybride" and f["Easy Apply"] is True
    assert f["Recherche LI"] == "recherche B1" and f["Date 1ère vue"] == "2026-10-01"


def test_norm_company_and_rank():
    assert norm_company("Deloitte France SAS") == "deloitte"
    assert norm_company("Grant Thornton Ireland") == "grantthorntonireland"
    assert rank("Envoyé") == 1 and rank("A/R") == 2 and rank("Oui") == rank("Non") == 3 and rank("") == 0


def _ctx_with_candidatures():
    at = FakeAirtable({"CANDIDATURES": [
        {"id": "recA", "fields": {"Société": "PeersGroup", "Poste": "Senior Manager Finance - PMO", "Réponse": "Envoyé",
                                  "URL": "https://www.linkedin.com/jobs/view/4400004401/"}},
        {"id": "recB", "fields": {"Société": "Deloitte France", "Poste": "Manager Transformation Finance",
                                  "Réponse": {"name": "Non"}}},
    ], "OFFRES": [
        {"id": "recO", "fields": {"jobId": "4400004401", "Employeur": "PeersGroup", "Poste": "Senior Manager Finance - PMO",
                                  "Statut": "Dossier prêt", "URL": "https://www.linkedin.com/jobs/view/4400004401/"}},
    ]})
    return FakeContext(at=at)


def test_index_find_by_job_company_and_title():
    ctx = _ctx_with_candidatures()
    idx = CandidaturesIndex(ctx)
    assert idx.find("4400004401", "")["id"] == "recA"
    assert idx.find("", "Deloitte")["id"] == "recB"
    assert idx.find("", "Peers Group", "PMO")["id"] == "recA"
    assert idx.find("", "Inconnu") is None
    assert idx.mentions_known_company("Bonjour, suite à votre candidature chez PeersGroup…") == "peersgroup"


def test_upsert_candidature_update_skip_create():
    ctx = _ctx_with_candidatures()
    idx = CandidaturesIndex(ctx)
    assert upsert_candidature(ctx, company="PeersGroup", title="", url="", job_id="4400004401", status="A/R",
                              day="2026-09-10", index=idx) == "update"
    assert upsert_candidature(ctx, company="PeersGroup", title="", url="", job_id="4400004401", status="Envoyé",
                              day="2026-09-11", index=idx) == "skip"          # jamais de retour en arrière
    assert upsert_candidature(ctx, company="Deloitte", title="X", url="", job_id="", status="Oui",
                              day="2026-09-11", index=idx) == "skip"          # Non est terminal
    assert upsert_candidature(ctx, company="Swiss Re", title="Programme Manager", url="https://www.linkedin.com/jobs/view/77/",
                              job_id="77", status="Envoyé", day="2026-09-12", index=idx) == "create"
    created = [c for c in ctx.at.calls if c[0] == "create"][0][2][0]
    assert created["Canal"] == "LinkedIn" and created["Job ID"] == "77" and created["Date postulé"] == "2026-09-12"
    assert upsert_candidature(ctx, company="Swiss Re", title="", url="", job_id="77", status="A/R",
                              day="2026-09-13", index=idx) == "update"       # retrouvé via l'index mis à jour


def test_apply_event_updates_offer_and_candidature():
    ctx = _ctx_with_candidatures()
    idx = CandidaturesIndex(ctx)
    ev = StatusEvent(email_id="e1", date="2026-09-10", status="A/R", company="PeersGroup", job_id="4400004401",
                     subject="Votre candidature a été vue par PeersGroup")
    assert apply_event(ctx, ev, idx) == "update"
    offer_patch = [c for c in ctx.at.calls if c[0] == "patch" and c[1] == "OFFRES"][0][3]
    assert offer_patch["Réponse"] == "A/R" and offer_patch["Date réponse"] == "2026-09-10"


def test_helpers_and_parser():
    assert fstr("O'Brien") == "'O\\'Brien'"
    assert record_url("app1", "tbl1", "rec1") == "https://airtable.com/app1/tbl1/rec1"
    p = build_parser()
    a = p.parse_args(["--dry-run", "run", "--skip", "track", "--days", "1"])
    assert a.dry_run and a.skip == ["track"] and a.days == 1
    d = p.parse_args(["dossier", "--url", "https://www.linkedin.com/jobs/view/4444856066/"])
    assert d.url.endswith("/4444856066/")
