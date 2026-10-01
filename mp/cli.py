"""Ligne de commande `mp`.

  mp run                 run complet : ingest → sync → score → dossiers → track → digest
  mp ingest              lit les digests LinkedIn (Gmail) → table Offres
  mp score               évalue les offres non scorées (Claude) et coche les meilleures
  mp dossiers            génère CV + lettre pour les offres cochées « Préparer dossier » / « Je postule »
  mp dossier --url …     dossier à la demande (URL LinkedIn, jobId, ou --text pour une annonce collée)
  mp sync                applique les cases cochées (Je postule / J'écarte) et expire les vieilles offres
  mp track               lit les emails de statut → Offres + Candidatures
  mp digest              envoie le digest du dernier run (ou --force)
  mp airtable-setup      vérifie / crée les champs Airtable (--apply)
  mp doctor              vérifie secrets, LibreOffice, CV de base, Airtable, Gmail
  mp app                 cockpit mobile : serveur web local (127.0.0.1:8765), exposé à l'iPhone via Tailscale
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from mp import __version__
from mp.config import settings

log = logging.getLogger("mp")


def _setup_logging(verbose: bool, out_dir: Path) -> Path:
    out_dir.joinpath("logs").mkdir(parents=True, exist_ok=True)
    path = out_dir / "logs" / f"mp_{datetime.now():%Y-%m-%d}.log"
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt,
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(path, encoding="utf-8")])
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return path


def _ctx(args):
    from mp.context import Context
    return Context(dry_run=getattr(args, "dry_run", False))


# ---------------------------------------------------------------------------
# Commandes
# ---------------------------------------------------------------------------


def cmd_run(args) -> int:
    from mp import digest, pipeline, tracking
    ctx = _ctx(args)
    steps = [
        ("ingest", lambda: pipeline.ingest(ctx, args.days)),
        ("sync", lambda: pipeline.sync_decisions(ctx)),      # décisions + expiration AVANT le scoring
        ("score", lambda: pipeline.score(ctx, limit=args.limit)),
        ("dossiers", lambda: pipeline.dossiers(ctx, max_n=args.max_dossiers)),
        ("track", lambda: tracking.track(ctx, days=max(args.days or ctx.s.ingest_days, 3))),
    ]
    for name, fn in steps:
        if name in (args.skip or []):
            continue
        log.info("══ %s ══", name)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — une étape KO n'empêche pas les suivantes
            ctx.report.error(f"étape {name} KO : {e}")
            log.exception("étape %s", name)
    try:
        ctx.report.notes.append(ctx.claude.usage_line())
    except Exception:  # noqa: BLE001
        pass
    digest.send(ctx, label=args.label or "")
    return 1 if ctx.report.errors else 0


def cmd_ingest(args) -> int:
    from mp import pipeline
    pipeline.ingest(_ctx(args), args.days)
    return 0


def cmd_score(args) -> int:
    from mp import pipeline
    ctx = _ctx(args)
    pipeline.score(ctx, limit=args.limit, rescore=args.rescore)
    log.info(ctx.claude.usage_line())
    return 1 if ctx.report.errors else 0


def cmd_dossiers(args) -> int:
    from mp import pipeline
    ctx = _ctx(args)
    pipeline.dossiers(ctx, max_n=args.max_dossiers)
    log.info(ctx.claude.usage_line())
    return 1 if ctx.report.errors else 0


def cmd_dossier(args) -> int:
    from mp.dossier import build_dossier, ensure_record, local_paths
    from mp.linkedin import extract_job_id, fetch_jd
    from mp.pipeline import F_CV_FILES, F_LETTER_FILES
    from mp.scoring import today

    ctx = _ctx(args)
    job_id = args.job_id or (extract_job_id(args.url) if args.url else None)
    url = args.url or (f"https://www.linkedin.com/jobs/view/{job_id}/" if job_id else "")
    text = Path(args.text).read_text(encoding="utf-8") if args.text and Path(args.text).exists() else (args.text or "")
    title, employer, location = args.title or "", args.employer or "", args.location or ""
    if job_id and not (title and employer):
        jd = fetch_jd(job_id)
        title, employer, location = title or jd.title, employer or jd.company, location or jd.location
        text = text or jd.text
    rec = ensure_record(ctx, job_id=job_id, url=url, title=title, employer=employer, location=location,
                        description=text)
    res = build_dossier(ctx, rec)
    if not ctx.dry_run and rec["id"] != "dry-run":
        ctx.at.replace_attachments(ctx.offres, rec["id"], F_CV_FILES, [Path(res.cv_pdf), Path(res.cv_docx)])
        ctx.at.replace_attachments(ctx.offres, rec["id"], F_LETTER_FILES, [Path(res.letter_pdf), Path(res.letter_docx)])
        ctx.at.patch(ctx.offres, rec["id"], {
            "Lettre texte": res.letter_text, "Objections": "\n".join(f"• {o}" for o in res.objections),
            "Profil CV": res.profile, "Langue": res.lang, "Dossier le": today(), "Statut": "Dossier prêt",
            "Erreur": ""})
    print("\n=== DOSSIER PRÊT ===")
    print(f"{res.employer} — {res.title}  [{res.profile} / {res.lang}] "
          f"({res.edits_applied} retouche(s) CV, {res.edits_skipped} ignorée(s))")
    for p in local_paths(res):
        print(f"  {p}")
    if res.gaps:
        print("Écarts à préparer : " + " | ".join(res.gaps))
    print("\n--- Texte de candidature ---\n" + res.letter_text)
    if res.objections:
        print("\n--- Objections à préparer ---\n" + "\n".join(f"• {o}" for o in res.objections))
    log.info(ctx.claude.usage_line())
    return 0


def cmd_sync(args) -> int:
    from mp import pipeline
    ctx = _ctx(args)
    pipeline.sync_decisions(ctx, force=args.force)
    for err in ctx.report.errors:
        print(f"[KO] {err}")
    return 1 if ctx.report.errors else 0


def cmd_track(args) -> int:
    from mp import tracking
    ctx = _ctx(args)
    tracking.track(ctx, days=args.days)
    return 1 if ctx.report.errors else 0


def cmd_digest(args) -> int:
    from mp import digest
    ctx = _ctx(args)
    ctx.report.note("digest envoyé à la demande")
    return 0 if digest.send(ctx, force=True) else 1


def cmd_airtable_setup(args) -> int:
    from mp.airtable import CANDIDATURES_FIELDS, OFFRES_FIELDS
    ctx = _ctx(args)
    wanted_offres = OFFRES_FIELDS + [{
        "name": "Candidature", "type": "multipleRecordLinks",
        "description": "Lien vers la ligne Candidatures créée au moment de la candidature.",
        "options": {"linkedTableId": ctx.candidatures},
    }]
    plan = [(ctx.offres, "Offres (Veille 2)", ctx.at.missing_fields(ctx.offres, wanted_offres)),
            (ctx.candidatures, "Candidatures", ctx.at.missing_fields(ctx.candidatures, CANDIDATURES_FIELDS))]
    total = 0
    for table_id, label, missing in plan:
        print(f"\n{label} ({table_id}) — {len(missing)} champ(s) manquant(s)")
        for spec in missing:
            total += 1
            print(f"  + {spec['name']:22s} {spec['type']}")
            if args.apply:
                ctx.at.create_field(table_id, spec)
                print("    créé")
    if total and not args.apply:
        print("\nRelancer avec --apply pour créer ces champs (le PAT doit avoir le scope schema.bases:write).")
    return 0


def cmd_doctor(args) -> int:
    from mp.cv import CV_FILES
    from mp.letter import WRITING_RULES_FILES
    from mp.pdf import soffice_binary
    s = settings()
    ok = True

    def check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'OK' if good else 'KO'}] {label}{' — ' + detail if detail else ''}")

    print(f"mp {__version__} — diagnostic\n")
    missing = s.missing_secrets()
    check("secrets", not missing, ", ".join(missing) if missing else "ANTHROPIC / AIRTABLE / GMAIL présents")
    from mp.config import DUPLICATES, SOURCES
    for key in ("ANTHROPIC_API_KEY", "AIRTABLE_PAT", "GMAIL_USER", "GMAIL_APP_PASSWORD"):
        value = os.environ.get(key, "")
        shown = f"{value[:10]}… ({len(value)} caractères)" if len(value) > 14 else ("présente" if value else "absente")
        detail = f"{shown} — lue depuis {SOURCES.get(key, '?')}" if value else "absente"
        if DUPLICATES.get(key):
            detail += f" ; définie aussi dans {', '.join(DUPLICATES[key])} (ignorée là)"
        check(f"    {key}", bool(value), detail)
    check("LibreOffice", soffice_binary() is not None, soffice_binary() or "soffice introuvable")
    for lang, files in CV_FILES.items():
        for profile, name in files.items():
            p = s.cv_base_dir / name
            check(f"CV de base {lang} {profile}", p.exists(), str(p))
    for lang, name in WRITING_RULES_FILES.items():
        p = s.writing_rules_dir / name
        check(f"règles d'écriture {lang}", p.exists(), str(p))
    check("profil candidat", s.profile_file.exists(), str(s.profile_file))
    if s.airtable_pat:
        ctx = _ctx(args)
        try:
            names = {t["id"]: t["name"] for t in ctx.at.tables()}
            check("Airtable", s.t_offres in names and s.t_candidatures in names,
                  f"{names.get(s.t_offres, '?')} / {names.get(s.t_candidatures, '?')}")
            from mp.airtable import CANDIDATURES_FIELDS, OFFRES_FIELDS
            miss = ctx.at.missing_fields(s.t_offres, OFFRES_FIELDS) + ctx.at.missing_fields(s.t_candidatures, CANDIDATURES_FIELDS)
            check("schéma Airtable", not miss, f"{len(miss)} champ(s) manquant(s) → mp airtable-setup --apply" if miss else "complet")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "INVALID_PERMISSIONS" in msg or "HTTP 403" in msg:
                # L'API Meta (schéma) est refusée : le PAT n'a pas le scope schema.bases:read. Le pipeline n'en a
                # pas besoin (il lit et écrit des enregistrements) ; on vérifie donc l'accès aux données.
                try:
                    ctx.at.list(s.t_offres, max_records=1, fields=["jobId"])
                    ctx.at.list(s.t_candidatures, max_records=1, fields=["Société"])
                    check("Airtable (données)", True, "lecture OK ; schéma non vérifiable : ajouter le scope "
                          "schema.bases:read au PAT (airtable.com/create/tokens) pour mp airtable-setup")
                except Exception as e2:  # noqa: BLE001
                    check("Airtable", False, str(e2)[:160])
            else:
                check("Airtable", False, msg[:160])
    if s.gmail_user and s.gmail_app_password and not args.offline:
        try:
            with _ctx(args).gmail() as g:
                n = len(g.search("newer_than:1d"))
            check("Gmail IMAP", True, f"{n} email(s) sur 24 h")
        except Exception as e:  # noqa: BLE001
            check("Gmail IMAP", False, str(e)[:120])
    if s.anthropic_api_key and not args.offline:
        try:
            m = _ctx(args).claude.client.models.retrieve(s.model_main)
            check("Claude", True, f"{m.id} ({s.model_fast} pour le rapide)")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "401" in msg or "authentication_error" in msg:
                msg = ("clé refusée (401) : la régénérer sur console.anthropic.com → Settings → API keys, puis la "
                       "coller dans ~/.config/mission-pipeline/anthropic.env (ANTHROPIC_API_KEY=sk-ant-…)")
            elif "404" in msg or "not_found" in msg:
                msg = f"modèle {s.model_main} introuvable pour cette clé : vérifier MP_MODEL_MAIN"
            check("Claude", False, msg[:200])
    print("\nTout est prêt." if ok else "\nCorriger les points KO avant de lancer `mp run`.")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Parseur
# ---------------------------------------------------------------------------


def cmd_app(args) -> int:
    from mp.app import serve
    log.info("cockpit : http://%s:%d (exposer avec `tailscale serve --bg %d`)", args.host, args.port, args.port)
    serve(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"mp {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="n'écrit rien (Airtable, Gmail, email)")
    # Les mêmes options sont acceptées après la sous-commande (`mp run --dry-run -v`), sans écraser
    # une valeur donnée avant (`mp --dry-run run`) : SUPPRESS ne touche au namespace que si l'option est présente.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run complet", parents=[common])
    r.add_argument("--days", type=int, default=None)
    r.add_argument("--limit", type=int, default=80, help="offres à scorer au maximum")
    r.add_argument("--max-dossiers", type=int, default=6)
    r.add_argument("--skip", nargs="*", choices=["ingest", "score", "sync", "dossiers", "track"])
    r.add_argument("--label", default="", help="étiquette du digest (ex. matin / soir)")
    r.set_defaults(fn=cmd_run)

    i = sub.add_parser("ingest", parents=[common])
    i.add_argument("--days", type=int, default=None)
    i.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("score", parents=[common])
    s.add_argument("--limit", type=int, default=80)
    s.add_argument("--rescore", action="store_true", help="ré-évalue aussi les offres déjà scorées")
    s.set_defaults(fn=cmd_score)

    d = sub.add_parser("dossiers", parents=[common])
    d.add_argument("--max-dossiers", type=int, default=6)
    d.set_defaults(fn=cmd_dossiers)

    o = sub.add_parser("dossier", help="dossier à la demande", parents=[common])
    o.add_argument("--url")
    o.add_argument("--job-id")
    o.add_argument("--text", help="texte de l'annonce (ou chemin d'un fichier) si LinkedIn est bloqué")
    o.add_argument("--title")
    o.add_argument("--employer")
    o.add_argument("--location")
    o.set_defaults(fn=cmd_dossier)

    sy = sub.add_parser("sync", parents=[common])
    sy.add_argument("--force", action="store_true", help="applique les « Je postule » même au-delà du plafond de sécurité")
    sy.set_defaults(fn=cmd_sync)

    t = sub.add_parser("track", parents=[common])
    t.add_argument("--days", type=int, default=3)
    t.set_defaults(fn=cmd_track)

    sub.add_parser("digest", parents=[common]).set_defaults(fn=cmd_digest)

    a = sub.add_parser("airtable-setup", parents=[common])
    a.add_argument("--apply", action="store_true")
    a.set_defaults(fn=cmd_airtable_setup)

    dr = sub.add_parser("doctor", parents=[common])
    dr.add_argument("--offline", action="store_true", help="ne teste ni Gmail ni Claude")
    dr.set_defaults(fn=cmd_doctor)

    ap = sub.add_parser("app", help="cockpit mobile (serveur web local)", parents=[common])
    ap.add_argument("--host", default="127.0.0.1", help="laisser 127.0.0.1 : Tailscale fait le pont")
    ap.add_argument("--port", type=int, default=8765)
    ap.set_defaults(fn=cmd_app)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    s = settings()
    _setup_logging(args.verbose, s.out_dir)
    if args.cmd == "dossier" and not (args.url or args.job_id or args.text):
        print("mp dossier : indiquer --url, --job-id ou --text", file=sys.stderr)
        return 2
    try:
        return int(args.fn(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        log.exception("échec : %s", e)
        return 1
