"""Copie des dossiers (CV + lettre, PDF et DOCX) vers le miroir Google Drive du Mac.

`build_dossier` copie déjà chaque dossier produit sur le Mac. Cette synchronisation rattrape ceux produits
ailleurs (GitHub Actions) ou régénérés depuis le cockpit : elle relit les pièces jointes Airtable des offres
qui ont un dossier récent et télécharge celles qui manquent dans `<Drive>/<Dossier le>/`. Idempotente :
un fichier déjà présent avec la même taille n'est pas retéléchargé. Ne supprime jamais rien.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import requests

from mp.context import Context

log = logging.getLogger("mp.drive")

FIELDS = ("CV (fichiers)", "Lettre (fichiers)")


def _download(url: str, target: Path) -> None:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(target)


def sync(ctx: Context, days: int = 14, fetch=_download) -> dict:
    root = ctx.s.drive_dossiers_dir
    stats = {"dossier": str(root) if root else "", "offres": 0, "copies": 0, "deja": 0, "erreurs": 0}
    if not root:
        log.info("copie Drive : aucun dossier Drive sur cette machine (MP_DRIVE_DOSSIERS_DIR)")
        return stats
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    formula = f"AND({{Dossier le}},IS_AFTER({{Dossier le}},DATETIME_PARSE('{cutoff}','YYYY-MM-DD')))"
    recs = ctx.at.list(ctx.offres, formula=formula, fields=["Dossier le", "Employeur", *FIELDS])
    for rec in recs:
        f = rec["fields"]
        day = str(f.get("Dossier le") or date.today().isoformat())[:10]
        atts = [a for field in FIELDS for a in (f.get(field) or [])]
        if not atts:
            continue
        stats["offres"] += 1
        dest = root / day
        for a in atts:
            name = Path(a.get("filename") or "").name
            if not name or not a.get("url"):
                continue
            target = dest / name
            if target.exists() and (not a.get("size") or target.stat().st_size == a["size"]):
                stats["deja"] += 1
                continue
            try:
                dest.mkdir(parents=True, exist_ok=True)
                fetch(a["url"], target)
                stats["copies"] += 1
            except Exception as e:  # noqa: BLE001 — une pièce KO n'empêche pas les suivantes
                stats["erreurs"] += 1
                log.warning("copie Drive KO pour %s (%s) : %s", name, f.get("Employeur", "?"), e)
    log.info("copie Drive : %s", stats)
    return stats
