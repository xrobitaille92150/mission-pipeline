from mp.airtable import fstr, record_url
from mp.cli import build_parser
from mp.models import Card, StatusEvent
from mp.pipeline import card_to_fields
from mp.tracking import CandidaturesIndex, apply_event, norm_company, rank, upsert_candidature
from tests.conftest import FakeAirtable, FakeClaude, FakeContext


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
    b = p.parse_args(["run", "--dry-run", "-v"])            # options acceptées aussi après la sous-commande
    assert b.dry_run and b.verbose
    c = p.parse_args(["--dry-run", "sync"])                  # et une valeur donnée avant n'est pas écrasée
    assert c.dry_run and not c.verbose
    assert not p.parse_args(["doctor", "--offline"]).dry_run
    d = p.parse_args(["dossier", "--url", "https://www.linkedin.com/jobs/view/4444856066/"])
    assert d.url.endswith("/4444856066/")


def test_sync_refuses_mass_postule_without_force():
    from mp.pipeline import sync_decisions
    recs = [{"id": f"rec{i:014d}", "fields": {"jobId": str(4400000000 + i), "Employeur": f"E{i}", "Poste": "P",
                                             "Je postule": True}} for i in range(20)]
    ctx = FakeContext(at=FakeAirtable({"OFFRES": recs, "CANDIDATURES": []}))
    stats = sync_decisions(ctx)
    assert stats["postulees"] == 0 and ctx.report.errors and "--force" in ctx.report.errors[0]
    assert not [c for c in ctx.at.calls if c[0] == "create"]
    stats = sync_decisions(ctx, force=True)
    assert stats["postulees"] == 20


# ---------------------------------------------------------------------------
# Doublons (règle de l'ancien JACK, sans suppression)
# ---------------------------------------------------------------------------

def test_norm_title_ignores_gender_marks_accents_and_punctuation():
    from mp.pipeline import norm_title
    assert norm_title("Chef de projet H/F") == norm_title("Chef de Projet (F/H)") == "chef de projet"
    assert norm_title("Senior Manager – Finance (m/w/d)") == norm_title("Senior manager - finance") == "senior manager finance"
    assert norm_title("Directeur Comptabilité") == "directeur comptabilite"


def _row(rid, emp, poste, **f):
    return {"id": rid, "fields": {"Employeur": emp, "Poste": poste, "jobId": rid[-3:], **f}}


def test_dedupe_keeps_best_and_marks_others_without_deleting():
    from mp.pipeline import dedupe
    rows = [
        _row("rec001", "KPMG France", "Manager Transformation Finance H/F", **{"Statut": "À étudier", "Score": 72,
             "Date 1ère vue": "2026-09-20"}),
        _row("rec002", "KPMG", "Manager Transformation Finance", **{"Statut": "Dossier prêt", "Score": 60,
             "Dossier le": "2026-09-25", "Date 1ère vue": "2026-09-24"}),            # dossier présent : gardée
        _row("rec003", "KPMG", "Manager transformation finance (F/H)", **{"Statut": "Nouvelle",
             "Date 1ère vue": "2026-10-01", "Préparer dossier": True}),
        _row("rec004", "AXA", "PMO IFRS 17", **{"Statut": "À étudier"}),                 # seule : intacte
    ]
    ctx = FakeContext(at=FakeAirtable({"OFFRES": rows}))
    stats = dedupe(ctx)
    assert stats == {"groupes": 1, "doublons": 2}
    by_id = {r["id"]: r["fields"] for r in ctx.at.tables_data["OFFRES"]}
    assert by_id["rec002"]["Statut"] == "Dossier prêt"
    assert by_id["rec001"]["Statut"] == by_id["rec003"]["Statut"] == "Doublon"
    assert "jobId 002" in by_id["rec001"]["Doublon de"] and by_id["rec003"]["Préparer dossier"] is False
    assert by_id["rec004"]["Statut"] == "À étudier"
    assert len(ctx.at.tables_data["OFFRES"]) == 4                                    # rien de supprimé
    assert dedupe(ctx)["doublons"] == 0                                              # idempotent


def test_dedupe_decided_offer_covers_reposts_and_protects_decisions():
    from mp.pipeline import dedupe
    rows = [
        _row("rec101", "EY", "Senior Manager Actuarial", **{"Statut": "Écartée"}),
        _row("rec102", "EY", "Senior Manager Actuarial", **{"Statut": "À étudier", "Score": 80}),
        _row("rec103", "EY", "Senior Manager Actuarial", **{"Statut": "À étudier", "Je postule": True}),  # protégée
        _row("rec201", "Allianz", "Head of Finance", **{"Statut": "Expirée"}),        # n'empêche rien
        _row("rec202", "Allianz", "Head of Finance", **{"Statut": "Nouvelle"}),
    ]
    ctx = FakeContext(at=FakeAirtable({"OFFRES": rows}))
    dedupe(ctx)
    by_id = {r["id"]: r["fields"] for r in ctx.at.tables_data["OFFRES"]}
    assert by_id["rec102"]["Statut"] == "Doublon" and "jobId 101" in by_id["rec102"]["Doublon de"]
    assert by_id["rec103"]["Statut"] == "À étudier"
    assert by_id["rec101"]["Statut"] == "Écartée"
    assert by_id["rec201"]["Statut"] == "Expirée" and by_id["rec202"]["Statut"] == "Nouvelle"


def test_duplicates_are_never_scored_nor_given_a_dossier():
    import inspect

    import mp.pipeline as pl
    from tests.conftest import eval_formula
    src = inspect.getsource(pl.score) + inspect.getsource(pl.dossiers)
    assert src.count("NOT({Statut}='Doublon')") == 2
    live = "AND(NOT({Scoré le}),NOT({J'écarte}=1),NOT({Statut}='Écartée'),NOT({Statut}='Expirée'),NOT({Statut}='Doublon'))"
    assert not eval_formula(live, {"Statut": {"name": "Doublon"}})
    assert eval_formula(live, {"Statut": {"name": "Nouvelle"}})


# ---------------------------------------------------------------------------
# File « A traiter » (revue manuelle, comme BOB)
# ---------------------------------------------------------------------------

def _mail(subject, from_name, from_email="no-reply@ats-mail.com"):
    from mp.models import Email
    return Email(uid=b"1", id="18c2f0a1b2c3d4e5", subject=subject, from_name=from_name, from_email=from_email,
                 date="2026-10-02", plain="Nous avons bien reçu votre candidature. Cordialement, l'équipe.")


def _classif(**kw):
    base = {"categorie": "Accusé de réception", "societe": "", "poste": "", "confiance": "Moyenne",
            "justification": "accusé automatique"}
    return {**base, **kw}


def test_unidentified_company_goes_to_a_traiter_not_candidatures():
    from mp.tracking import event_from_email
    ctx = _ctx_with_candidatures()
    ctx.claude = FakeClaude([_classif()])
    idx = CandidaturesIndex(ctx)
    ev = event_from_email(ctx, _mail("Votre candidature", "Talent Acquisition Team"), idx)
    assert ev is not None and ev.reliable is False and ev.company == "Talent Acquisition Team"
    assert apply_event(ctx, ev, idx) == "review"
    up = [c for c in ctx.at.calls if c[0] == "upsert"]
    assert up and up[0][1] == "A_TRAITER" and up[0][3] == ["ID Email"]
    row = up[0][2][0]
    assert row["ID Email"] == "18c2f0a1b2c3d4e5" and row["Réponse"] == "A/R"
    assert row["Lien Gmail"].endswith("#all/18c2f0a1b2c3d4e5") and "Talent Acquisition Team" in row["Expéditeur"]
    assert not [c for c in ctx.at.calls if c[0] == "create" and c[1] == "CANDIDATURES"]


def test_low_confidence_unknown_company_goes_to_review_instead_of_being_dropped():
    from mp.tracking import event_from_email
    ctx = _ctx_with_candidatures()
    ctx.claude = FakeClaude([_classif(categorie="Refus", societe="Inconnue SA", confiance="Basse")])
    idx = CandidaturesIndex(ctx)
    ev = event_from_email(ctx, _mail("Votre candidature", "RH"), idx)
    assert ev.reliable is False and apply_event(ctx, ev, idx) == "review"


def test_identified_company_still_creates_or_updates_candidature():
    from mp.tracking import event_from_email
    ctx = _ctx_with_candidatures()
    ctx.claude = FakeClaude([_classif(categorie="Réponse positive", societe="Mazars", confiance="Haute"),
                             _classif(categorie="Refus", confiance="Basse")])
    idx = CandidaturesIndex(ctx)
    ev = event_from_email(ctx, _mail("Votre candidature chez Mazars", "Mazars Recrutement"), idx)
    assert ev.reliable and apply_event(ctx, ev, idx) == "create"
    # société devinée mais déjà connue des Candidatures (Deloitte) : mise à jour normale, pas de revue
    ev2 = event_from_email(ctx, _mail("Candidature Deloitte", "Deloitte"), idx)
    assert ev2.reliable is True and apply_event(ctx, ev2, idx) in ("update", "skip")
    assert not [c for c in ctx.at.calls if c[0] == "upsert" and c[1] == "A_TRAITER"]
