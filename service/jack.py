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

Verrou anti-chevauchement : /tmp/run-dossiers.lock (posé par run-dossiers.sh,
périmé 90 min). Si un MATT est en cours (run 06:15/18:15 ou run horaire long),
les étapes 1-3 (suppressions) sont sautées jusqu'au run horaire suivant — sinon
MATT PATCHe des records supprimés (403) et génère des CV pour rien. L'étape 4
reste protégée par le même lock côté shell.

Usage :
  python3 jack.py            # run réel
  python3 jack.py --dry-run  # compte sans supprimer ni générer
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import airtable as at  # noqa: E402
from lib import config as C  # noqa: E402

RUN_DOSSIERS = C.REPO / "scripts" / "run-dossiers.sh"
MATT_MAX = "12"  # dossiers max par run horaire — reste sous le verrou de 90 min
MATT_LOCK = Path("/tmp/run-dossiers.lock")  # même verrou que run-dossiers.sh
LOCK_STALE_S = 90 * 60  # au-delà, lock considéré périmé (aligné sur le .sh)


def matt_en_cours() -> bool:
    """True si un run MATT est en cours (lock présent et non périmé).

    Supprimer/dédupliquer des lignes Veille 2 pendant qu'un MATT tourne
    provoque des PATCH 403 sur records supprimés + CV générés pour rien
    (constaté le 28/07 : 16 × 403 sur le run 18:35-19:38). Dans ce cas,
    les purges/dédup sont reportées au run horaire suivant."""
    try:
        return MATT_LOCK.exists() and (time.time() - MATT_LOCK.stat().st_mtime) < LOCK_STALE_S
    except OSError:
        return False


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

    # 0. MATT en cours ? → aucune suppression pendant qu'il tourne (race → 403)
    if matt_en_cours():
        print("MATT en cours (lock run-dossiers) — purges/dédup reportées au "
              "prochain run horaire", flush=True)
    else:
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
