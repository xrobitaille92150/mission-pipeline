"""Digest email de fin de run : ce qu'il y a à décider, ce qui est prêt, ce qui a bougé, ce qui a cassé."""
from __future__ import annotations

import html
import logging

from mp.context import Context, RunReport

log = logging.getLogger("mp.digest")


def _esc(s) -> str:
    return html.escape(str(s or ""))


def decisions_line(d: dict) -> str:
    return (f"Décisions : {d.get('postulees', 0)} postulée(s), {d.get('ecartees', 0)} écartée(s), "
            f"{d.get('expirees', 0)} expirée(s).")


def render(report: RunReport, label: str = "") -> tuple[str, str, str]:
    """(sujet, texte, html)"""
    top = sorted((s for s in report.scored if s["verdict"] != "Écarter"), key=lambda s: -s["score"])[:10]
    n_post = sum(1 for s in report.scored if s["verdict"] == "Postuler")
    subject = (f"[Mission Pipeline]{' ' + label if label else ''} {len(report.dossiers)} dossier(s) prêt(s) · "
               f"{n_post} à postuler · {len(report.scored)} évaluée(s)"
               + (f" · {len(report.errors)} erreur(s)" if report.errors else ""))

    lines = [f"Run du {report.started:%d/%m/%Y %H:%M}", ""]
    h = [f"<h2 style='font-family:Georgia,serif;color:#0B1530'>Mission Pipeline — {report.started:%d/%m %H:%M}</h2>"]

    if report.dossiers:
        lines.append("DOSSIERS PRÊTS (CV + lettre dans Airtable) :")
        h.append("<h3>Dossiers prêts</h3><ul>")
        for d in report.dossiers:
            how = "skills" if d.get("engine") == "claude-code" else "API"
            lines.append(f"  • {d['employer']} — {d['title']} [{d['profile']} / {d['lang']} / {how}] → {d['airtable']}")
            h.append(f"<li><b>{_esc(d['employer'])}</b> — {_esc(d['title'])} "
                     f"<small>({_esc(d['profile'])}, {_esc(d['lang'])}, {how})</small> · "
                     f"<a href='{_esc(d['airtable'])}'>Airtable</a> · <a href='{_esc(d['url'])}'>LinkedIn</a></li>")
        h.append("</ul>")
        lines.append("")

    if top:
        lines.append("OFFRES DU JOUR :")
        h.append("<h3>Offres évaluées</h3><table cellpadding='6' style='border-collapse:collapse;font-size:14px'>"
                 "<tr style='background:#f0f0f0'><th>Score</th><th>Verdict</th><th>Offre</th><th>Pourquoi</th></tr>")
        for s in top:
            tag = f" ★{s['rank']}" if s.get("rank") else ""
            lines.append(f"  {s['score']:3d} {s['verdict']:8s}{tag} {s['employer']} — {s['title']}")
            for w in s["why"]:
                lines.append(f"        - {w}")
            why = "<br>".join(_esc(w) for w in s["why"])
            h.append(f"<tr><td align='center'><b>{s['score']}</b>{tag}</td><td>{_esc(s['verdict'])}</td>"
                     f"<td><b>{_esc(s['employer'])}</b><br>{_esc(s['title'])}<br>"
                     f"<a href='{_esc(s['airtable'])}'>Airtable</a> · <a href='{_esc(s['url'])}'>LinkedIn</a></td>"
                     f"<td style='font-size:12px'>{why}</td></tr>")
        h.append("</table>")
        lines.append("")

    if report.events:
        lines.append("SUIVI DES RÉPONSES :")
        h.append("<h3>Suivi des réponses</h3><ul>")
        labels = {"create": "nouvelle candidature", "update": "mise à jour", "skip": "déjà à jour",
                  "review": "à vérifier dans la table A traiter"}
        for ev in report.events:
            act = labels.get(ev["action"], ev["action"])
            lines.append(f"  • {ev['status']:6s} {ev['company'] or '(société ?)'} — {ev['title']} ({act})")
            h.append(f"<li><b>{_esc(ev['status'])}</b> {_esc(ev['company'] or '(société ?)')} — {_esc(ev['title'])} "
                     f"<small>({_esc(act)})</small></li>")
        h.append("</ul>")
        lines.append("")

    if report.ingest:
        i = report.ingest
        lines.append(f"Ingestion : {i.get('emails', 0)} email(s), {i.get('cartes', 0)} carte(s), "
                     f"{i.get('nouvelles', 0)} nouvelle(s) offre(s).")
        h.append(f"<p style='color:#666'>Ingestion : {i.get('emails', 0)} email(s), {i.get('cartes', 0)} carte(s), "
                 f"{i.get('nouvelles', 0)} nouvelle(s).</p>")
    if report.decisions:
        lines.append(decisions_line(report.decisions))
        h.append(f"<p style='color:#666'>{_esc(decisions_line(report.decisions))}</p>")
    if report.errors:
        lines.append("")
        lines.append("ERREURS :")
        h.append("<h3 style='color:#a32626'>Erreurs</h3><ul>")
        for e in report.errors:
            lines.append(f"  ✗ {e}")
            h.append(f"<li>{_esc(e)}</li>")
        h.append("</ul>")
    if report.notes:  # suivi des réponses, doublons, consommation Claude : dans les deux versions du digest
        lines.append("")
        lines.extend(f"  · {n}" for n in report.notes)
        h.append("<ul style='color:#666;font-size:12px'>" + "".join(f"<li>{_esc(n)}</li>" for n in report.notes)
                 + "</ul>")
    return subject, "\n".join(lines), "\n".join(h)


def send(ctx: Context, label: str = "", force: bool = False) -> bool:
    if not (force or ctx.report.has_content):
        log.info("digest : rien à signaler, pas d'email")
        return False
    subject, text, body = render(ctx.report, label)
    if ctx.dry_run:
        log.info("digest (dry-run) :\n%s", text)
        return False
    try:
        with ctx.gmail() as g:
            g.send(subject, text, html=body, sender=ctx.s.digest_from, to=ctx.s.digest_to)
        log.info("digest envoyé à %s", ctx.s.digest_to)
        return True
    except Exception as e:  # noqa: BLE001 — le digest ne doit jamais faire échouer le run
        log.error("digest non envoyé : %s\n%s", e, text)
        return False
