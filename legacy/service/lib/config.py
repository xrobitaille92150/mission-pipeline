"""Config centrale du service Mission Pipeline (Mac). Secrets lus depuis ~/.config/mission-pipeline/*.env — jamais en dur."""
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "mission-pipeline"
REPO = Path(__file__).resolve().parents[2]          # .../pipeline
BAREME_FILE = REPO / "nodes" / "scoring-bareme-prompt.txt"

# --- Airtable ---
AIRTABLE_BASE = "apphTpnW5vu0OdnfC"
T_VEILLE2 = "tblrCyL6huHkUPZbF"
T_CANDIDATURES = "tblF3jpncEXA647ou"
T_A_TRAITER = "tblSeyppFhxU3i8Ev"
T_TRIAGE = "tblOkwh1UtFHQpyct"      # Email Triage (log de classification BOB)

# --- Modèles Claude (chaînes prouvées en prod le 01/07/2026) ---
MODEL_NOTES = "claude-sonnet-4-6"
MODEL_SCORE = "claude-haiku-4-5-20251001"

# --- Seuil actionnable (aligné barème) ---
SEUIL = 50

# --- Profil candidat (pour les notes) ---
PROFIL = (
    "Profil candidat : consultant senior independant, 25 ans d'experience, "
    "100% assurance & finance. Cherche mission freelance/contract/interim "
    "(CDI senior finance possible), full remote ou hybride privilegie, "
    "TJM >= 800 EUR/j. Ouvert a l'international en remote (EMEA, Amerique du Nord, APAC)."
)


def load_env(filename: str, var: str) -> str:
    """Lit une variable dans ~/.config/mission-pipeline/<filename>."""
    path = CONFIG_DIR / filename
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line.split("=", 1)[0] == var:
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError(f"{var} introuvable dans {path}")


def bareme() -> str:
    return BAREME_FILE.read_text(encoding="utf-8")[:6000]
