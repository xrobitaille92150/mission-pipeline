"""Configuration centrale.

Ordre de résolution d'une variable : environnement du processus, puis tous les fichiers
`~/.config/mission-pipeline/*.env` (format KEY=VALUE, jamais commités). Les secrets ne sont
lus qu'ici. Sur GitHub Actions, les mêmes noms sont déclarés comme secrets du dépôt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG_DIR = Path.home() / ".config" / "mission-pipeline"

# --- Airtable : base « Mission Pipeline » ------------------------------------
AIRTABLE_BASE = "apphTpnW5vu0OdnfC"
T_OFFRES = "tblrCyL6huHkUPZbF"        # table historique « Veille 2 », réutilisée telle quelle
T_CANDIDATURES = "tblF3jpncEXA647ou"  # entonnoir de suivi (tous canaux)

# --- Gmail --------------------------------------------------------------------
LINKEDIN_ALERTS = "jobalerts-noreply@linkedin.com"
LINKEDIN_JOBS = "jobs-noreply@linkedin.com"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().split(" #")[0].strip().strip('"').strip("'")
            out[key.strip()] = value
    except OSError:
        pass
    return out


# Provenance de chaque variable (diagnostic `mp doctor`) : nom du fichier, ou « shell » si elle vient de
# l'environnement du processus. DUPLICATES liste les autres définitions trouvées et ignorées.
SOURCES: dict[str, str] = {}
DUPLICATES: dict[str, list[str]] = {}
SHELL = "environnement du shell (export dans ~/.zshrc ou ~/.zprofile)"
# Pour les secrets, les fichiers de ~/.config/mission-pipeline font foi : une vieille clé exportée par le shell
# ne doit pas masquer la clé à jour du fichier. Les autres variables (MP_*) gardent la règle habituelle
# « l'environnement prime », utile pour un essai ponctuel ou GitHub Actions (où aucun fichier n'existe).
SECRETS = ("ANTHROPIC_API_KEY", "AIRTABLE_PAT", "GMAIL_USER", "GMAIL_APP_PASSWORD")


# Fichiers canoniques (voir .env.example), lus avant les autres : un `airtable-codex.env` ou un `anthropic-old.env`
# ne peut pas passer devant `airtable.env` ou `anthropic.env`.
CANONICAL = ("anthropic.env", "airtable.env", "gmail.env", "n8n.env")


def _load_order(path: Path) -> tuple[int, str]:
    return (CANONICAL.index(path.name) if path.name in CANONICAL else len(CANONICAL), path.name)


def load_env_files(config_dir: Path = CONFIG_DIR) -> None:
    """Charge les *.env du dossier de config dans os.environ. Fichiers canoniques d'abord, puis les autres par
    ordre alphabétique ; premier trouvé gagnant. Secrets : le fichier prime sur le shell ; autres : le shell prime."""
    if not config_dir.is_dir():
        return
    for path in sorted(config_dir.glob("*.env"), key=_load_order):
        for key, value in _parse_env_file(path).items():
            if key in SOURCES:                                   # déjà fixée par un fichier précédent
                DUPLICATES.setdefault(key, []).append(path.name)
                continue
            in_shell = bool(os.environ.get(key))
            if in_shell and key not in SECRETS:
                SOURCES[key] = SHELL
                DUPLICATES.setdefault(key, []).append(path.name)
                continue
            if in_shell:
                DUPLICATES.setdefault(key, []).append("le shell")
            os.environ[key] = value
            SOURCES[key] = path.name


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _path(name: str, default: Path) -> Path:
    raw = env(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    airtable_pat: str | None
    gmail_user: str | None
    gmail_app_password: str | None
    digest_from: str
    digest_to: str
    model_main: str
    model_fast: str
    score_min: int
    auto_dossiers: int
    ingest_days: int
    label_done: str
    cv_base_dir: Path
    writing_rules_dir: Path
    out_dir: Path
    drive_dossiers_dir: Path | None
    profile_file: Path
    airtable_base: str
    t_offres: str
    t_candidatures: str

    def missing_secrets(self) -> list[str]:
        missing = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.airtable_pat:
            missing.append("AIRTABLE_PAT")
        if not self.gmail_user or not self.gmail_app_password:
            missing.append("GMAIL_USER / GMAIL_APP_PASSWORD")
        return missing


@lru_cache(maxsize=1)
def settings() -> Settings:
    load_env_files()
    drive = env("MP_DRIVE_DOSSIERS_DIR")
    return Settings(
        anthropic_api_key=env("ANTHROPIC_API_KEY"),
        airtable_pat=env("AIRTABLE_PAT"),
        gmail_user=env("GMAIL_USER"),
        gmail_app_password=(env("GMAIL_APP_PASSWORD") or "").replace(" ", "") or None,
        digest_from=env("DIGEST_FROM", "xro@xavier-robitaille.com") or "",
        digest_to=env("DIGEST_TO", "xrobitaille92150@gmail.com") or "",
        model_main=env("MP_MODEL_MAIN", "claude-opus-5-5") or "claude-opus-5-5",
        model_fast=env("MP_MODEL_FAST", "claude-haiku-4-5") or "claude-haiku-4-5",
        score_min=int(env("MP_SCORE_MIN", "60") or 60),
        auto_dossiers=int(env("MP_AUTO_DOSSIERS_PER_RUN", "3") or 3),
        ingest_days=int(env("MP_INGEST_DAYS", "2") or 2),
        label_done=env("MP_LABEL_DONE", "MissionPipeline") or "MissionPipeline",
        cv_base_dir=_path("MP_CV_BASE_DIR", REPO / "assets" / "cv_base"),
        writing_rules_dir=_path("MP_WRITING_RULES_DIR", REPO / "assets" / "writing_rules"),
        out_dir=_path("MP_OUT_DIR", REPO / "out"),
        drive_dossiers_dir=Path(drive).expanduser() if drive else None,
        profile_file=_path("MP_PROFILE_FILE", REPO / "mp" / "prompts" / "profile.md"),
        airtable_base=env("MP_AIRTABLE_BASE", AIRTABLE_BASE) or AIRTABLE_BASE,
        t_offres=env("MP_T_OFFRES", T_OFFRES) or T_OFFRES,
        t_candidatures=env("MP_T_CANDIDATURES", T_CANDIDATURES) or T_CANDIDATURES,
    )


def prompt(name: str) -> str:
    """Lit un fichier de prompt versionné (mp/prompts/<name>.md)."""
    return (REPO / "mp" / "prompts" / f"{name}.md").read_text(encoding="utf-8")
