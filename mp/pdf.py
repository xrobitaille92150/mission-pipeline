"""Conversion DOCX → PDF via LibreOffice (headless). Remplace le couple pandoc + Chrome de la v1/v2 :
la mise en page du CV est conservée (pandoc la perdait) et il n'y a plus de chemin absolu Mac."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CANDIDATES = [
    "soffice", "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice", "/usr/local/bin/soffice", "/usr/bin/soffice",
]


def soffice_binary() -> str | None:
    env = os.environ.get("MP_SOFFICE")
    if env and Path(env).exists():
        return env
    for c in CANDIDATES:
        found = shutil.which(c) if "/" not in c else (c if Path(c).exists() else None)
        if found:
            return found
    return None


def docx_to_pdf(docx: Path, outdir: Path | None = None, timeout: int = 180) -> Path:
    """Retourne le chemin du PDF. Lève RuntimeError si LibreOffice est absent ou échoue."""
    binary = soffice_binary()
    if not binary:
        raise RuntimeError("LibreOffice introuvable (installer LibreOffice ou définir MP_SOFFICE)")
    docx = Path(docx)
    outdir = Path(outdir) if outdir else docx.parent
    outdir.mkdir(parents=True, exist_ok=True)
    # Profil utilisateur jetable : permet des conversions en parallèle et évite les verrous.
    with tempfile.TemporaryDirectory(prefix="mp_lo_") as profile:
        cmd = [binary, "--headless", "--norestore", f"-env:UserInstallation=file://{profile}",
               "--convert-to", "pdf", "--outdir", str(outdir), str(docx)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    pdf = outdir / (docx.stem + ".pdf")
    if p.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"conversion PDF échouée ({p.returncode}) : {(p.stderr or p.stdout)[-400:]}")
    return pdf
