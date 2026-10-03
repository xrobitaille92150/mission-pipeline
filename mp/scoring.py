"""Scoring des offres : filtres durs déterministes, puis un appel Claude à sortie structurée."""
from __future__ import annotations

import logging
import re
from datetime import date

from mp.claude import Claude
from mp.config import prompt
from mp.models import NOTES_SCHEMA, SCORING_SCHEMA, JobDescription, Notes, Scoring

log = logging.getLogger("mp.scoring")

NON_EUROPE = re.compile(
    r"\b(am[ée]rique du nord|north america|[ée]tats[- ]unis|united states|\bu\.?s\.?a?\b|canada|toronto|montr[ée]al"
    r"|new york|chicago|boston|houston|dallas|apac|asia|asie|singapou?r|hong kong|\binde\b|\bindia\b|bangalore"
    r"|mumbai|duba[iï]|emirates|[ée]mirats|qatar|riyad|saudi|australi[ae]|sydney|melbourne|japon|japan|tokyo"
    r"|chine|china|shanghai|br[ée]sil|brazil|mexi(que|co)|afrique du sud|south africa|johannesburg|lagos|nairobi"
    r"|latin america|am[ée]rique latine|middle east|moyen[- ]orient)\b",
    re.I,
)
JUNIOR = re.compile(
    r"\b(junior|stagiaire|stage|intern(ship)?|alternan(t|ce)|apprenti[e]?|graduate|trainee|werkstudent"
    r"|d[ée]butant|entry[- ]level|associate consultant|analyst\b)",
    re.I,
)

# Marqueurs de langue (comptés sur un texte entouré d'espaces, en minuscules).
_LANG_MARKERS = {
    "FR": [" le ", " la ", " les ", " des ", " une ", " et ", " pour ", " avec ", " vous ", " nous ", " dans ",
           " sur ", " du ", " est ", " que ", " qui ", " poste ", " entreprise ", " expérience ", " équipe ",
           " missions ", " vos ", " notre ", " au sein "],
    "EN": [" the ", " and ", " for ", " with ", " you ", " we ", " to ", " of ", " in ", " on ", " is ", " are ",
           " will ", " role ", " team ", " experience ", " skills ", " our ", " your ", " within ", " as "],
    "DE": [" und ", " für ", " mit ", " der ", " die ", " das ", " wir ", " sie ", " bei ", " nicht ", " ein ",
           " eine ", " ihre ", " unser ", " oder ", " sind ", " werden ", " aufgaben ", " kenntnisse "],
    "NL": [" en ", " het ", " voor ", " met ", " wij ", " een ", " van ", " bij ", " je ", " jij ", " onze ",
           " niet ", " werk ", " ervaring ", " functie "],
    "ES": [" y ", " para ", " con ", " el ", " las ", " los ", " nosotros ", " empresa ", " experiencia ",
           " puesto ", " nuestro ", " buscamos "],
    "IT": [" e ", " per ", " con ", " il ", " della ", " delle ", " siamo ", " azienda ", " esperienza ",
           " ricerchiamo ", " nostro "],
    "PT": [" e ", " para ", " com ", " nós ", " empresa ", " experiência ", " vaga ", " nosso ", " você "],
    "PL": [" i ", " dla ", " oraz ", " w ", " na ", " firma ", " doświadczenie ", " praca ", " nasz "],
}


def detect_language(text: str) -> str:
    """FR / EN / AUTRE (DE, NL, ES, IT, PT, PL) — heuristique de mots fréquents ; EN par défaut."""
    t = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    if len(t) < 40:
        return "EN"
    scores = {lang: sum(t.count(m) for m in markers) for lang, markers in _LANG_MARKERS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "EN"
    # FR et EN partagent peu de marqueurs ; les autres langues doivent dominer nettement.
    if best in ("FR", "EN"):
        return best
    if scores[best] >= 1.5 * max(scores["FR"], scores["EN"]):
        return "AUTRE"
    return "FR" if scores["FR"] >= scores["EN"] else "EN"


def hard_filter(title: str, location: str, jd: JobDescription | None) -> str | None:
    """Raison d'exclusion déterministe, ou None. Volontairement limité aux cas certains."""
    if JUNIOR.search(title or "") and not re.search(r"senior|lead|head|director|manager|principal", title or "", re.I):
        return "niveau junior / analyst dans l'intitulé"
    loc = " ".join([location or "", (jd.location if jd else "") or ""])
    if NON_EUROPE.search(loc):
        return f"poste hors Europe ({location})"
    if jd and jd.ok and len(jd.text) > 300 and detect_language(jd.text) == "AUTRE":
        return "annonce rédigée dans une langue autre que FR / EN"
    return None


def _offer_block(title: str, employer: str, location: str, mode: str, source: str,
                 jd: JobDescription | None) -> str:
    lines = [f"Poste : {title or '(non renseigné)'}", f"Employeur : {employer or '(non renseigné)'}",
             f"Lieu : {location or '(non renseigné)'}", f"Mode (email) : {mode or 'non précisé'}",
             f"Source : {source}"]
    if jd and jd.criteria:
        lines.append("Critères LinkedIn : " + " | ".join(f"{k}: {v}" for k, v in jd.criteria.items()))
    if jd and jd.easy_apply:
        lines.append("Candidature simplifiée (Easy Apply) : oui")
    if jd and jd.ok:
        lines.append("\nFICHE DE POSTE :\n" + jd.text[:9000])
    else:
        lines.append("\nFICHE DE POSTE : non disponible" + (f" ({jd.error})" if jd and jd.error else ""))
    return "\n".join(lines)


def score_offer(claude: Claude, *, title: str, employer: str, location: str, mode: str, source: str,
                jd: JobDescription | None, effort: str = "medium") -> Scoring:
    system = [prompt("profile"), prompt("scoring") + "\n\n" + prompt("bareme") + "\n\n" + prompt("notes")]
    user = ("Évalue cette offre et rends le JSON demandé.\n\n"
            + _offer_block(title, employer, location, mode, source, jd))
    data = claude.json(system=system, user=user, schema=SCORING_SCHEMA, effort=effort, max_tokens=6000)
    s = Scoring.model_validate(data)
    if jd is None or not jd.ok:
        s.score = min(s.score, 69)
        if s.verdict == "POSTULER":
            s.verdict = "ETUDIER"
    return s


def notes_offer(claude: Claude, *, title: str, employer: str, location: str, mode: str, source: str,
                jd: JobDescription | None) -> Notes:
    """Résumé et critères seuls, sans re-noter l'offre (rattrapage des offres déjà notées : `mp notes`).
    Même consigne que dans la notation (prompts/notes.md)."""
    user = ("Rédige les notes demandées (résumé et critères) pour cette offre et rends le JSON.\n\n"
            + _offer_block(title, employer, location, mode, source, jd))
    data = claude.json(system=[prompt("profile"), prompt("notes")], user=user, schema=NOTES_SCHEMA,
                       effort="low", max_tokens=3000)
    return Notes.model_validate(data)


def excluded_scoring(reason: str) -> Scoring:
    return Scoring(cluster="HORS_AXE", posture="mixte", langue="AUTRE" if "langue" in reason else "EN",
                   pays="", mode="non_precise", contrat="non_precise", junior="junior" in reason,
                   score=0, verdict="ECARTER", pourquoi=[f"Exclu : {reason}"], red_flags=[reason],
                   profil_cv="FinanceTransformation", mots_cles=[])


def rank_for_dossiers(scored: list[tuple[str, Scoring]], score_min: int, max_auto: int) -> dict[str, int]:
    """Classe les offres POSTULER du run (score ≥ score_min) ; retourne {record_id: rang (1 = meilleur)}
    pour celles qui méritent un dossier automatique (max_auto)."""
    eligible = [(rid, s) for rid, s in scored if s.verdict == "POSTULER" and s.score >= score_min]
    eligible.sort(key=lambda x: -x[1].score)
    return {rid: i + 1 for i, (rid, _) in enumerate(eligible[:max_auto])}


def today() -> str:
    return date.today().isoformat()
