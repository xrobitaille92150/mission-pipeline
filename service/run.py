#!/usr/bin/env python3
"""
run.py — orchestrateur unique du Mission Pipeline (launchd com.xrobitaille.missionrun, 06:15 / 18:15).

Séquence : JOE (ingest pont Gmail + enrichissement Veille 2) → BOB (triage emails →
Candidatures / A traiter / Email Triage) → MATT (CV + CL, scripts/run-dossiers.sh).

Principes (REBUILD_PLAN) :
  - chaque étape = sous-processus isolé ; un échec n'empêche pas les étapes suivantes ;
  - fin de run = digest email systématique (lib/notify.py) — plus JAMAIS d'échec silencieux ;
  - exit code 1 si au moins une étape KO (visible dans le log launchd).

Modes :
  python3 run.py             # prod : tout en écriture
  python3 run.py --dry-run   # JOE + BOB en dry-run, MATT sauté, digest [DRY-RUN]
"""
import datetime
import subprocess
import sys
import time
from pathlib import Path

SERVICE = Path(__file__).resolve().parent
PIPELINE = SERVICE.parent
PY = sys.executable

TAIL_LINES = 25


def run_step(name: str, cmd: list, timeout: int = 3600) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=SERVICE, capture_output=True, text=True, timeout=timeout)
        ok = p.returncode == 0
        out = (p.stdout or "")
        if (p.stderr or "").strip():
            out += "\n[stderr]\n" + p.stderr
    except subprocess.TimeoutExpired:
        ok, out = False, f"TIMEOUT après {timeout}s"
    except Exception as e:  # noqa: BLE001 — capté pour le digest, jamais silencieux
        ok, out = False, f"EXCEPTION lanceur : {e}"
    return {"name": name, "ok": ok, "secs": round(time.time() - t0), "out": out.strip()}


def tail(s: str, n: int = TAIL_LINES) -> str:
    return "\n".join(s.splitlines()[-n:])


def main():
    dry = "--dry-run" in sys.argv
    started = datetime.datetime.now()
    steps = []

    if dry:
        steps.append(run_step("JOE (dry-run)", [PY, "joe.py", "--dry-run"]))
        steps.append(run_step("BOB (dry-run)", [PY, "bob.py"]))
    else:
        steps.append(run_step("JOE", [PY, "joe.py", "all", "--write"]))
        steps.append(run_step("BOB", [PY, "bob.py", "--write"]))
        steps.append(run_step("MATT", ["/bin/bash", str(PIPELINE / "scripts" / "run-dossiers.sh")],
                              timeout=5400))

    nb_ko = sum(1 for s in steps if not s["ok"])
    statut = "OK" if nb_ko == 0 else f"{nb_ko} étape(s) KO"
    prefix = "[Mission Pipeline]" + (" [DRY-RUN]" if dry else "")
    subject = f"{prefix} run {started:%d/%m %H:%M} — {statut}"

    parts = [f"Run {'DRY-RUN ' if dry else ''}du {started:%d/%m/%Y %H:%M} — {statut}", ""]
    for s in steps:
        parts.append(f"── {s['name']} — {'✓ OK' if s['ok'] else '✗ ÉCHEC'} ({s['secs']}s) " + "─" * 20)
        parts.append(tail(s["out"]))
        parts.append("")
    text = "\n".join(parts)
    print(text)

    from lib.notify import send_digest
    sent = send_digest(subject, text)
    print(f"[digest] email {'envoyé' if sent else 'KO — fallback notification macOS'}")
    sys.exit(1 if nb_ko else 0)


if __name__ == "__main__":
    main()
