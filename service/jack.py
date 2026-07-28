"""JACK — agent horaire Veille 2 (redéfini le 28/07/2026).

Toutes les heures, scan RÉTROACTIF de toute la table Veille 2 :
  1. supprime les offres cochées « J'écarte » ;
  2. dédoublonne : même employeur + même poste (2 recherches LinkedIn peuvent
     renvoyer la même offre) → garde la meilleure ligne (CV > case cochée >
     score > ancienneté), supprime les autres ;
  3. supprime les lignes ignorées : aucune case cochée, pas de CV,
     « Date 1ère vue » > 7 jours ;
  4. lance la génération CV + CL (MATT / run_dossiers.py) pour toute offre
     « Préparer dossier » OU « Je postule » sans CV (plafond MATT_MAX par run) ;
  5. email uniquement en cas d'erreur (fail-loud, silencieux quand tout va bien).

Le verrou anti-chevauchement vit dans run-dossiers.sh (lock /tmp, périmé 90 min) :
si le run de 06:15/18:15 est en cours, l'étape 2 est simplement sautée jusqu'au
run horaire suivant.

Usage :
  python3 jack.py            # run réel
  python3 jack.py --dry-run  # compte sans supprimer ni générer
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import airtable as at  # noqa: E402
from lib import config as C  # noqa: E402

RUN_DOSSIERS = C.REPO / "scripts" / "run-dossiers.sh"
MATT_MAX = "12"  # dossiers max par run horaire — reste sous le verrou de 90 min


def purge_ecartees(dry: bool) -> tuple:
    """Supprime toutes les lignes J'écarte=1. Retourne (trouvées, supprimées)."""
    recs = at.list_records(C.T_VEILLE2, fields=["jobId"], formula="{J'écarte}=1")
    ids = [r["id"] for r in recs]
    if dry or not ids:
        return len(ids), 0
    return len(ids), at.delete(C.T_VEILLE2, ids)


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def dedoublonner(dry: bool) -> tuple:
    """Même employeur + même poste sur plusieurs lignes → garde la meilleure.
    Priorité de conservation : CV présent > case cochée (Préparer/Je postule) >
    Score le plus haut > Date 1ère vue la plus ancienne.
    Retourne (doublons trouvés, supprimés)."""
    recs = at.list_records(
        C.T_VEILLE2,
        fields=["Employeur", "Poste", "Score", "CV",
                "Préparer dossier", "Je postule", "Date 1ère vue"])
    groupes = {}
    for r in recs:
        f = r.get("fields", {})
        key = (_norm(f.get("Employeur")), _norm(f.get("Poste")))
        if key == ("", ""):
            continue
        groupes.setdefault(key, []).append(r)

    def rang(r):
        f = r.get("fields", {})
        d = f.get("Date 1ère vue") or "9999-12-31"
        return (bool(f.get("CV")),
                bool(f.get("Préparer dossier")) or bool(f.get("Je postule")),
                f.get("Score") or 0,
                d)  # date : la plus ancienne gagne (tri croissant sur d, cf. min)

    a_supprimer = []
    for rs in groupes.values():
        if len(rs) < 2:
            continue
        # meilleure ligne = max sur (cv, coché, score) puis, à égalité, la plus ancienne
        garder = sorted(rs, key=lambda r: (rang(r)[0], rang(r)[1], rang(r)[2]),
                        reverse=True)
        tete = [r for r in garder if (rang(r)[0], rang(r)[1], rang(r)[2])
                == (rang(garder[0])[0], rang(garder[0])[1], rang(garder[0])[2])]
        keep = min(tete, key=lambda r: rang(r)[3])
        a_supprimer.extend(r["id"] for r in rs if r["id"] != keep["id"])

    if dry or not a_supprimer:
        return len(a_supprimer), 0
    return len(a_supprimer), at.delete(C.T_VEILLE2, a_supprimer)


def purge_ignorees(dry: bool) -> tuple:
    """Supprime les lignes sans aucune case cochée, sans CV, vues il y a > 7 jours.
    Date 1ère vue vide = jamais supprimée (prudence). Retourne (trouvées, supprimées)."""
    formula = ("AND(NOT({Préparer dossier}),NOT({Je postule}),NOT({J'écarte}),"
               "{CV}='',IS_BEFORE({Date 1ère vue},DATEADD(TODAY(),-7,'days')))")
    recs = at.list_records(C.T_VEILLE2, fields=["jobId"], formula=formula)
    ids = [r["id"] for r in recs]
    if dry or not ids:
        return len(ids), 0
    return len(ids), at.delete(C.T_VEILLE2, ids)


def main():
    dry = "--dry-run" in sys.argv
    errors = []

    # 1. Purge des écartées (rétroactif, toute la table)
    try:
        found, deleted = purge_ecartees(dry)
        print(f"purge écartées — trouvées={found} supprimées={deleted}"
              f"{' (dry-run)' if dry else ''}", flush=True)
    except Exception as e:  # noqa: BLE001 — capté pour le digest, jamais silencieux
        errors.append(f"purge écartées KO : {e}")
        print(f"purge écartées KO : {e}", file=sys.stderr, flush=True)

    # 2. Dédoublonnage (même employeur + poste via 2 recherches différentes)
    try:
        found, deleted = dedoublonner(dry)
        print(f"doublons — trouvés={found} supprimés={deleted}"
              f"{' (dry-run)' if dry else ''}", flush=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"dédoublonnage KO : {e}")
        print(f"dédoublonnage KO : {e}", file=sys.stderr, flush=True)

    # 3. Purge des ignorées (aucune case, pas de CV, > 7 jours)
    try:
        found, deleted = purge_ignorees(dry)
        print(f"purge ignorées >7j — trouvées={found} supprimées={deleted}"
              f"{' (dry-run)' if dry else ''}", flush=True)
    except Exception as e:  # noqa: BLE001
        errors.append(f"purge ignorées KO : {e}")
        print(f"purge ignorées KO : {e}", file=sys.stderr, flush=True)

    # 4. Génération CV/CL (MATT) — Préparer dossier OU Je postule, CV vide
    if dry:
        pend = at.list_records(
            C.T_VEILLE2, fields=["Poste"],
            formula="AND(OR({Préparer dossier}=1,{Je postule}=1),{CV}='',NOT({J'écarte}=1))")
        print(f"dossiers en attente : {len(pend)} (dry-run, rien généré)")
    else:
        try:
            env = dict(os.environ, MATT_MAX=MATT_MAX)
            p = subprocess.run(["/bin/bash", str(RUN_DOSSIERS)], env=env,
                               timeout=80 * 60)
            if p.returncode != 0:
                errors.append(f"MATT (run-dossiers.sh) exit {p.returncode}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"MATT KO : {e}")

    # 5. Fail-loud : email seulement si erreur
    if errors:
        try:
            from lib.notify import send_digest
            send_digest("[JACK] run horaire KO", "\n".join(errors))
        except Exception as e:  # noqa: BLE001
            print(f"notify KO : {e}", file=sys.stderr)
        sys.exit(1)
    print("JACK OK")


if __name__ == "__main__":
    main()
