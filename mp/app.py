"""Cockpit mobile : `mp app` sert une petite application web sur le Mac, atteinte depuis l'iPhone via Tailscale.

Lecture et écriture passent par le moteur (Airtable, Claude) avec les secrets du Mac : rien ne transite par
le téléphone. L'application écoute sur 127.0.0.1 ; `tailscale serve` l'expose en HTTPS sur le réseau privé.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from mp import __version__
from mp.airtable import record_url
from mp.context import Context

log = logging.getLogger("mp.app")
WEB = Path(__file__).parent / "web"

F_LIST = ["jobId", "Poste", "Employeur", "Lieu", "Mode", "Pays", "Score", "Verdict IA", "Cluster", "Contrat",
          "Langue", "Pourquoi", "Red flags", "Statut", "Rang du jour", "Scoré le", "Dossier le", "Date postulé",
          "Réponse", "URL", "CV (fichiers)", "Lettre (fichiers)", "Je postule", "J'écarte", "Préparer dossier",
          "Erreur", "Profil CV", "Mots-clés", "Easy Apply"]
F_DETAIL = F_LIST + ["Description", "Lettre texte", "Objections"]

TABS = {
    "decider": ("AND(OR({Statut}='À étudier',{Statut}='Dossier prêt'),NOT({J'écarte}=1),NOT({Je postule}=1))",
                [("Score", "desc")]),
    "dossiers": ("AND({Dossier le},NOT({J'écarte}=1),NOT({Statut}='Postulée'),NOT({Statut}='Écartée'))",
                 [("Dossier le", "desc"), ("Score", "desc")]),
    "postulees": ("{Statut}='Postulée'", [("Date postulé", "desc")]),
}


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------


def _sel(v: Any) -> str:
    return v.get("name", "") if isinstance(v, dict) else (v or "")


def _att(v: Any) -> list[dict]:
    return [{"name": a.get("filename", ""), "url": a.get("url", ""), "type": a.get("type", "")} for a in (v or [])]


def _lines(v: Any) -> list[str]:
    return [ln.lstrip("•- ").strip() for ln in str(v or "").splitlines() if ln.strip()]


def to_json(ctx: Context, rec: dict, detail: bool = False) -> dict:
    f = rec["fields"]
    d = {
        "id": rec["id"], "jobId": f.get("jobId", ""), "poste": f.get("Poste", ""), "employeur": f.get("Employeur", ""),
        "lieu": f.get("Lieu", ""), "mode": f.get("Mode", ""), "pays": f.get("Pays", ""),
        "score": f.get("Score"), "verdict": _sel(f.get("Verdict IA")), "cluster": _sel(f.get("Cluster")),
        "contrat": _sel(f.get("Contrat")), "langue": _sel(f.get("Langue")), "statut": _sel(f.get("Statut")),
        "rang": f.get("Rang du jour"), "score_le": f.get("Scoré le", ""), "dossier_le": f.get("Dossier le", ""),
        "date_postule": f.get("Date postulé", ""), "reponse": _sel(f.get("Réponse")),
        "pourquoi": _lines(f.get("Pourquoi")), "red_flags": _lines(f.get("Red flags")),
        "url": f.get("URL", ""), "airtable_url": record_url(ctx.s.airtable_base, ctx.offres, rec["id"]),
        "cv": _att(f.get("CV (fichiers)")), "lettre_fichiers": _att(f.get("Lettre (fichiers)")),
        "je_postule": bool(f.get("Je postule")), "j_ecarte": bool(f.get("J'écarte")),
        "preparer_dossier": bool(f.get("Préparer dossier")), "erreur": f.get("Erreur", ""),
        "profil_cv": _sel(f.get("Profil CV")), "mots_cles": f.get("Mots-clés", ""),
        "easy_apply": bool(f.get("Easy Apply")),
    }
    if detail:
        d["description"] = f.get("Description", "")
        d["lettre_texte"] = f.get("Lettre texte", "")
        d["objections"] = _lines(f.get("Objections"))
    job = JOBS.for_record(rec["id"])
    if job:
        d["job"] = job
    return d


# ---------------------------------------------------------------------------
# Travaux en arrière-plan (dossier, fichiers de lettre, run complet)
# ---------------------------------------------------------------------------


class Jobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.items: dict[str, dict] = {}

    def start(self, kind: str, fn, record_id: str | None = None) -> dict:
        job = {"id": uuid.uuid4().hex[:8], "kind": kind, "record": record_id, "status": "running",
               "started": datetime.now().isoformat(timespec="seconds"), "ended": None, "message": ""}
        with self._lock:
            self.items[job["id"]] = job

        def run() -> None:
            try:
                msg = fn()
                job["status"], job["message"] = "done", (msg or "")
            except Exception as e:  # noqa: BLE001
                log.exception("job %s", kind)
                job["status"], job["message"] = "error", str(e)[:400]
            job["ended"] = datetime.now().isoformat(timespec="seconds")

        threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        return job

    def for_record(self, record_id: str) -> dict | None:
        with self._lock:
            running = [j for j in self.items.values() if j["record"] == record_id and j["status"] == "running"]
            return running[-1] if running else None

    def running(self, kind: str) -> bool:
        with self._lock:
            return any(j["kind"] == kind and j["status"] == "running" for j in self.items.values())


JOBS = Jobs()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class Decision(BaseModel):
    action: str  # postule | ecarte | dossier | annule


class Consigne(BaseModel):
    consigne: str


class Texte(BaseModel):
    texte: str


def _letter_files(ctx: Context, rec: dict, text: str) -> str:
    """Régénère DOCX + PDF de la lettre et les attache à la ligne. Appelé en arrière-plan."""
    from mp import letter as lettermod
    from mp.pdf import docx_to_pdf
    from mp.pipeline import F_LETTER_FILES

    f = rec["fields"]
    lang = _sel(f.get("Langue")) or "FR"
    lang = "FR" if lang == "FR" else "EN"
    out_dir = ctx.out("dossiers", date.today().isoformat())
    docx = out_dir / (lettermod.output_name(lang, f.get("Employeur", ""), f.get("jobId", ""),
                                            date.today().strftime("%Y%m%d")) + ".docx")
    lettermod.letter_docx(text, docx, employer=f.get("Employeur", ""), title=f.get("Poste", ""), lang=lang)
    pdf = docx_to_pdf(docx, out_dir)
    ctx.at.replace_attachments(ctx.offres, rec["id"], F_LETTER_FILES, [pdf, docx])
    return "lettre : PDF et DOCX régénérés"


def create_app(ctx: Context | None = None) -> FastAPI:
    app = FastAPI(title="Mission Pipeline", version=__version__, docs_url=None, redoc_url=None)
    state: dict[str, Any] = {"ctx": ctx}

    def get_ctx() -> Context:
        if state["ctx"] is None:
            state["ctx"] = Context()
        return state["ctx"]

    def record(record_id: str) -> dict:
        ctx = get_ctx()
        try:
            return ctx.at.get(ctx.offres, record_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(404, f"offre introuvable : {e}") from e

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):   # la page affiche `detail` dans un toast : pas de page blanche
        log.exception("api %s", request.url.path)
        return JSONResponse({"detail": f"{type(exc).__name__} : {exc}"[:400]}, status_code=500)

    # -- pages statiques -----------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (WEB / "index.html").read_text(encoding="utf-8")

    @app.get("/manifest.webmanifest")
    def manifest() -> JSONResponse:
        return JSONResponse({
            "name": "Mission Pipeline", "short_name": "Missions", "start_url": "/", "display": "standalone",
            "background_color": "#1F2A44", "theme_color": "#1F2A44", "lang": "fr",
            "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    @app.get("/icon.png")
    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def icon() -> FileResponse:
        return FileResponse(WEB / "icon.png", media_type="image/png")

    @app.get("/favicon.ico")
    def favicon() -> FileResponse:
        return FileResponse(WEB / "icon.png", media_type="image/png")

    @app.get("/icon-192.png")
    def icon192() -> FileResponse:
        return FileResponse(WEB / "icon-192.png", media_type="image/png")

    @app.get("/icon-512.png")
    def icon512() -> FileResponse:
        return FileResponse(WEB / "icon-512.png", media_type="image/png")

    @app.get("/icon.svg")
    def icon_svg() -> FileResponse:
        return FileResponse(WEB / "icon.svg", media_type="image/svg+xml")

    # -- API -----------------------------------------------------------------
    @app.get("/api/offres")
    def offres(tab: str = "decider") -> dict:
        if tab not in TABS:
            raise HTTPException(400, "onglet inconnu")
        ctx = get_ctx()
        formula, sort = TABS[tab]
        recs = ctx.at.list(ctx.offres, formula=formula, sort=sort, max_records=100, fields=F_LIST)
        items = [to_json(ctx, r) for r in recs]
        if tab == "decider":   # rang du jour d'abord, puis score
            items.sort(key=lambda o: (o["rang"] is None, o["rang"] or 0, -(o["score"] or 0)))
        return {"tab": tab, "items": items, "count": len(items)}

    @app.get("/api/offres/{record_id}")
    def offre(record_id: str) -> dict:
        return to_json(get_ctx(), record(record_id), detail=True)

    @app.post("/api/offres/{record_id}/decision")
    def decision(record_id: str, body: Decision) -> dict:
        from mp.pipeline import make_dossier, sync_decisions
        ctx = get_ctx()
        rec = record(record_id)
        patch = {
            "postule": {"Je postule": True, "J'écarte": False},
            "ecarte": {"J'écarte": True, "Je postule": False, "Préparer dossier": False},
            "dossier": {"Préparer dossier": True, "J'écarte": False},
            "annule": {"Je postule": False, "J'écarte": False, "Préparer dossier": False},
        }.get(body.action)
        if patch is None:
            raise HTTPException(400, "action inconnue")
        ctx.at.patch(ctx.offres, record_id, patch)
        erreurs: list[str] = []
        if body.action in ("postule", "ecarte"):
            n = len(ctx.report.errors)
            sync_decisions(ctx)          # Postulée + Candidatures, ou Écartée, tout de suite
            erreurs = ctx.report.errors[n:]   # ex. garde-fou « plus de 15 coches en attente »
        if body.action in ("postule", "dossier") and not rec["fields"].get("Dossier le") \
                and not JOBS.for_record(record_id):
            JOBS.start("dossier", lambda: _job_dossier(ctx, record_id, make_dossier), record_id)
        out = to_json(ctx, record(record_id), detail=True)
        if erreurs:
            out["erreur_sync"] = " ; ".join(erreurs)
        return out

    def _job_dossier(ctx: Context, record_id: str, make_dossier) -> str:
        item = make_dossier(ctx, ctx.at.get(ctx.offres, record_id))
        if item is None:
            raise RuntimeError(ctx.report.errors[-1] if ctx.report.errors else "dossier KO")
        return f"dossier prêt ({item['profile']}, {item['lang']})"

    @app.post("/api/offres/{record_id}/lettre/proposer")
    def proposer(record_id: str, body: Consigne) -> dict:
        from mp import letter as lettermod
        ctx = get_ctx()
        rec = record(record_id)
        f = rec["fields"]
        current = f.get("Lettre texte", "")
        if not current.strip():
            raise HTTPException(409, "pas encore de lettre pour cette offre : préparer le dossier d'abord")
        if not body.consigne.strip():
            raise HTTPException(400, "consigne vide")
        lang = _sel(f.get("Langue")) or "FR"
        letter = lettermod.rewrite_letter(
            ctx.claude, lang=lang, text=current, consigne=body.consigne, title=f.get("Poste", ""),
            employer=f.get("Employeur", ""), location=f.get("Lieu", ""), profile_md=ctx.profile_md,
            writing_rules=lettermod.load_writing_rules(ctx.s.writing_rules_dir, lang))
        return {"lettre": letter.lettre, "objections": letter.objections, "mots": lettermod.word_count(letter.lettre)}

    @app.put("/api/offres/{record_id}/lettre")
    def enregistrer(record_id: str, body: Texte) -> dict:
        ctx = get_ctx()
        rec = record(record_id)
        text = body.texte.strip()
        if len(text) < 200:
            raise HTTPException(400, "texte trop court pour une lettre")
        ctx.at.patch(ctx.offres, record_id, {"Lettre texte": text})
        rec["fields"]["Lettre texte"] = text
        job = JOBS.start("lettre", lambda: _letter_files(ctx, rec, text), record_id)
        return {"ok": True, "job": job}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict:
        j = JOBS.items.get(job_id)
        if not j:
            raise HTTPException(404, "travail inconnu")
        return j

    @app.post("/api/run")
    def run() -> dict:
        if JOBS.running("run"):
            raise HTTPException(409, "un run est déjà en cours")

        def do_run() -> str:
            import argparse

            from mp.cli import cmd_run
            args = argparse.Namespace(days=None, limit=80, max_dossiers=6, skip=[], label="appli", dry_run=False,
                                      verbose=False)
            rc = cmd_run(args)
            return "run terminé" + ("" if rc == 0 else " avec des erreurs (voir le digest)")

        return JOBS.start("run", do_run)

    @app.get("/api/sante")
    def sante() -> dict:
        ctx = get_ctx()
        s = ctx.s
        logs = sorted((s.out_dir / "logs").glob("mp_*.log")) if (s.out_dir / "logs").is_dir() else []
        last = datetime.fromtimestamp(logs[-1].stat().st_mtime).isoformat(timespec="minutes") if logs else ""
        counts = {}
        for key, formula in {
            "decider": TABS["decider"][0], "dossiers": TABS["dossiers"][0],
            "postulees_7j": "AND({Statut}='Postulée',IS_AFTER({Date postulé},DATEADD(TODAY(),-7,'days')))",
            "erreurs": "{Erreur}",
            "nouvelles_24h": "IS_AFTER({Date 1ère vue},DATEADD(TODAY(),-1,'days'))",
        }.items():
            try:
                counts[key] = len(ctx.at.list(ctx.offres, formula=formula, fields=["jobId"]))
            except Exception as e:  # noqa: BLE001
                counts[key] = None
                log.warning("santé %s : %s", key, e)
        return {"version": __version__, "dernier_run": last, "run_en_cours": JOBS.running("run"), "compteurs": counts,
                "modele": s.model_main}

    return app


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
