"""Structures de données partagées entre les étapes du pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


@dataclass
class Card:
    """Une offre extraite d'un email LinkedIn (alerte, recommandation, offre enregistrée)."""

    job_id: str
    url: str
    title: str
    employer: str
    location: str = ""
    mode: str = ""              # À distance / Hybride / Sur site / ""
    alert_name: str = ""        # nom de la recherche LinkedIn enregistrée (alertes)
    easy_apply: bool = False
    source: str = "Alerte"      # Alerte / Recommandation / Enregistrée / Manuelle
    email_id: str = ""
    email_date: str = ""        # YYYY-MM-DD


@dataclass
class Email:
    uid: bytes
    id: str                     # id Gmail (hex de X-GM-MSGID)
    subject: str
    from_name: str
    from_email: str
    date: str                   # YYYY-MM-DD
    plain: str
    html: str = ""
    labels: list[str] = field(default_factory=list)

    @property
    def domain(self) -> str:
        return self.from_email.split("@")[1] if "@" in self.from_email else ""


@dataclass
class JobDescription:
    ok: bool = False
    text: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    criteria: dict[str, str] = field(default_factory=dict)
    mode: str = ""
    easy_apply: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Scoring (sortie structurée de Claude, validée par Pydantic)
# ---------------------------------------------------------------------------

CLUSTER_LABELS = {
    "A": "A — Assurance/Passif",
    "B": "B — Investissement/Actif",
    "C": "C — Transformation/PMO",
    "HORS_AXE": "Hors-axe",
}
VERDICT_LABELS = {"POSTULER": "Postuler", "ETUDIER": "Étudier", "ECARTER": "Écarter"}
MODE_LABELS = {"remote": "À distance", "hybride": "Hybride", "sur_site": "Sur site", "non_precise": ""}
CONTRAT_LABELS = {
    "freelance": "Freelance", "cdi": "CDI", "interim": "Intérim", "cdd": "CDD", "non_precise": "Non précisé",
}
POSTURE_LABELS = {"projet": "Projet", "production": "Production", "mixte": "Mixte"}


# Barème v3 : Claude note cinq dimensions sur des échelles ancrées ; le total, les plafonds et le verdict
# sont calculés ici, jamais demandés au modèle (c'est ce qui évite les scores tassés entre 58 et 62).
SUBSCORES = {"fit": 40, "seniorite": 15, "geo": 20, "format_poste": 15, "signaux": 10}
SUBSCORE_LABELS = {"fit": "adéquation", "seniorite": "séniorité", "geo": "géographie", "format_poste": "format",
                   "signaux": "signaux"}


# Notes affichées dans le cockpit (« L'offre en bref », « Tes critères »), écrites dans les colonnes historiques
# « Note rôle » et « Note critères » de la table Offres (reprises de l'ancien système, à la demande de Xavier).
NOTE_CRITERES = ["Domaine", "Séniorité", "Contrat", "Rémunération", "Lieu et rythme", "Langue", "Secteur"]
STATUT_SYMBOLS = {"ok": "✓", "ecart": "✗", "inconnu": "?"}


class Critere(BaseModel):
    critere: str
    constat: str
    statut: str = Field(pattern="^(ok|ecart|inconnu)$")


def format_criteres(criteres: list[Critere]) -> str:
    """« ✓ Contrat : … » une ligne par critère (le symbole en tête distingue les notes v3 des anciennes)."""
    return "\n".join(f"{STATUT_SYMBOLS[c.statut]} {c.critere} : {c.constat.strip()}" for c in criteres)


class Notes(BaseModel):
    resume: str = ""
    criteres: list[Critere] = Field(default_factory=list)


NOTES_PROPERTIES: dict = {
    "resume": {"type": "string"},
    "criteres": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "critere": {"type": "string", "enum": NOTE_CRITERES},
                "constat": {"type": "string"},
                "statut": {"type": "string", "enum": ["ok", "ecart", "inconnu"]},
            },
            "required": ["critere", "constat", "statut"],
        },
    },
}
NOTES_SCHEMA: dict = {"type": "object", "additionalProperties": False, "properties": NOTES_PROPERTIES,
                      "required": ["resume", "criteres"]}


class Scoring(BaseModel):
    cluster: str = Field(pattern="^(A|B|C|HORS_AXE)$")
    posture: str = Field(pattern="^(projet|production|mixte)$")
    langue: str = Field(pattern="^(FR|EN|AUTRE)$")
    pays: str = ""
    europe: bool = True
    mode: str = Field(pattern="^(remote|hybride|sur_site|non_precise)$")
    contrat: str = Field(pattern="^(freelance|cdi|interim|cdd|non_precise)$")
    junior: bool = False
    fit: int | None = None
    seniorite: int | None = None
    geo: int | None = None
    format_poste: int | None = None
    signaux: int | None = None
    score: int = 0
    verdict: str = Field(default="ETUDIER", pattern="^(POSTULER|ETUDIER|ECARTER)$")
    pourquoi: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    profil_cv: str = Field(pattern="^(FinanceTransformation|AssetManagement|IFRS17SolvencyII)$")
    mots_cles: list[str] = Field(default_factory=list)
    resume: str = ""
    criteres: list[Critere] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _clamp(cls, v: int) -> int:
        return max(0, min(100, int(v)))

    @model_validator(mode="after")
    def _compute(self) -> Scoring:
        if self.fit is None:                      # ancien format (tests, exclusions) : score fourni tel quel
            return self
        total = 0
        for name, top in SUBSCORES.items():
            v = max(0, min(top, int(getattr(self, name) or 0)))
            setattr(self, name, v)
            total += v
        # plafonds déterministes
        if self.junior or self.langue == "AUTRE":
            total = min(total, 15)
        if not self.europe:
            total = min(total, 20)
        if self.cluster == "HORS_AXE":
            total = min(total, 40)
        if self.fit < 15:                         # sans adéquation métier, jamais au-dessus de « Écarter »
            total = min(total, 49)
        self.score = total
        self.verdict = "POSTULER" if total >= 70 else "ETUDIER" if total >= 50 else "ECARTER"
        return self

    @property
    def detail(self) -> str:
        if self.fit is None:
            return ""
        return "Détail : " + " · ".join(f"{SUBSCORE_LABELS[k]} {getattr(self, k)}/{top}" for k, top in SUBSCORES.items())

    @field_validator("pourquoi", "red_flags", "mots_cles")
    @classmethod
    def _strip(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]


SCORING_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cluster": {"type": "string", "enum": ["A", "B", "C", "HORS_AXE"]},
        "posture": {"type": "string", "enum": ["projet", "production", "mixte"]},
        "langue": {"type": "string", "enum": ["FR", "EN", "AUTRE"]},
        "pays": {"type": "string"},
        "mode": {"type": "string", "enum": ["remote", "hybride", "sur_site", "non_precise"]},
        "contrat": {"type": "string", "enum": ["freelance", "cdi", "interim", "cdd", "non_precise"]},
        "junior": {"type": "boolean"},
        "europe": {"type": "boolean"},
        "fit": {"type": "integer"},
        "seniorite": {"type": "integer"},
        "geo": {"type": "integer"},
        "format_poste": {"type": "integer"},
        "signaux": {"type": "integer"},
        "pourquoi": {"type": "array", "items": {"type": "string"}},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "profil_cv": {
            "type": "string",
            "enum": ["FinanceTransformation", "AssetManagement", "IFRS17SolvencyII"],
        },
        "mots_cles": {"type": "array", "items": {"type": "string"}},
        **NOTES_PROPERTIES,
    },
    "required": [
        "cluster", "posture", "langue", "pays", "europe", "mode", "contrat", "junior",
        "fit", "seniorite", "geo", "format_poste", "signaux",
        "pourquoi", "red_flags", "profil_cv", "mots_cles", "resume", "criteres",
    ],
}


# ---------------------------------------------------------------------------
# Dossier (CV + lettre)
# ---------------------------------------------------------------------------


class CvEdit(BaseModel):
    old: str
    new: str


class CvEditPlan(BaseModel):
    edits: list[CvEdit] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


CV_EDITS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["old", "new"],
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["edits", "gaps"],
}


class Letter(BaseModel):
    lettre: str
    objections: list[str] = Field(default_factory=list)


LETTER_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lettre": {"type": "string"},
        "objections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["lettre", "objections"],
}


# Dossier complet rédigé en un appel (Claude Code + skills du compte) : retouches du CV et lettre.
DOSSIER_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "edits": CV_EDITS_SCHEMA["properties"]["edits"],
        "gaps": {"type": "array", "items": {"type": "string"}},
        "lettre": {"type": "string"},
        "objections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["edits", "gaps", "lettre", "objections"],
}


@dataclass
class DossierResult:
    job_id: str
    employer: str
    title: str
    lang: str
    profile: str
    cv_docx: str
    cv_pdf: str
    letter_docx: str
    letter_pdf: str
    letter_text: str
    objections: list[str]
    edits_applied: int
    edits_skipped: int
    gaps: list[str]
    engine: str = "api"          # « claude-code » : rédigé avec les skills du compte ; « api » : prompts du dépôt


# ---------------------------------------------------------------------------
# Suivi des réponses
# ---------------------------------------------------------------------------

RESPONSE_RANK = {"": 0, "Néant": 0, "Envoyé": 1, "A/R": 2, "Oui": 3, "Non": 3}


@dataclass
class StatusEvent:
    email_id: str
    date: str
    status: str                 # Envoyé / A/R / Oui / Non
    company: str = ""
    title: str = ""
    job_id: str = ""
    url: str = ""
    subject: str = ""
    source: str = "linkedin"    # linkedin (règle déterministe) / classif (Claude)
    confidence: str = "Haute"
    note: str = ""
    reliable: bool = True       # False : société devinée (nom d'expéditeur) ou confiance basse → revue manuelle
    sender: str = ""            # « Nom <adresse> », pour la table A traiter


class EmailClass(BaseModel):
    categorie: str = Field(pattern="^(Accusé de réception|Réponse positive|Refus|Autre)$")
    societe: str = ""
    poste: str = ""
    confiance: str = Field(pattern="^(Haute|Moyenne|Basse)$")
    justification: str = ""


EMAIL_CLASS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "categorie": {
            "type": "string",
            "enum": ["Accusé de réception", "Réponse positive", "Refus", "Autre"],
        },
        "societe": {"type": "string"},
        "poste": {"type": "string"},
        "confiance": {"type": "string", "enum": ["Haute", "Moyenne", "Basse"]},
        "justification": {"type": "string"},
    },
    "required": ["categorie", "societe", "poste", "confiance", "justification"],
}
