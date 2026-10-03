"""Claude Code en mode headless (`claude -p`) : dossiers rédigés avec les skills du compte claude.ai de Xavier
(cv-tailoring, cover-letter, voix-xavier), sur son abonnement et non sur l'API.

Décision de Xavier (2 octobre 2026, option « Mixte ») : CV et lettres passent par Claude Code sur le Mac, où les
skills du compte sont synchronisées par la connexion `/login` ; le scoring et le tri des emails restent sur l'API.
Le pipeline garde la main sur les fichiers : Claude rend un JSON (retouches du CV, lettre, objections), Python
applique les retouches au DOCX, produit les PDF et remplit Airtable comme avant.

Documentation : https://code.claude.com/docs/en/headless.md (claude -p, --output-format json, --json-schema,
--allowedTools, --permission-mode) et https://code.claude.com/docs/en/skills.md (skills du compte en mode -p).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("mp.claude_code")

SKILLS = ("cv-tailoring", "cover-letter", "voix-xavier")


class ClaudeCodeError(RuntimeError):
    pass


def binary() -> str | None:
    """Chemin de la commande `claude` : MP_CLAUDE_BIN, sinon le PATH, sinon les emplacements d'installation usuels
    (launchd n'a pas le PATH du terminal)."""
    explicit = os.environ.get("MP_CLAUDE_BIN")
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which("claude")
    if found:
        return found
    for p in (Path.home() / ".local" / "bin" / "claude", Path.home() / ".claude" / "local" / "claude",
              Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if p.exists():
            return str(p)
    return None


def synced_skills() -> list[str]:
    """Skills du compte claude.ai présentes localement (synchronisées par Claude Code dans ~/.claude/skills/synced/)."""
    root = Path.home() / ".claude" / "skills" / "synced"
    found = {p.parent.name for p in root.glob("*/*/SKILL.md")} if root.is_dir() else set()
    return sorted(s for s in SKILLS if s in found)


def engine(setting: str | None = None) -> str:
    """« claude-code » ou « api » selon MP_DOSSIER_ENGINE (auto par défaut : Claude Code s'il est installé, hors
    GitHub Actions où les skills du compte ne sont pas disponibles)."""
    choice = (setting or os.environ.get("MP_DOSSIER_ENGINE") or "auto").strip().lower()
    if choice in ("api", "claude-code"):
        return choice
    if os.environ.get("GITHUB_ACTIONS") == "true" or not binary():
        return "api"
    return "claude-code"


def _json_from_text(text: str) -> dict | None:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""
    try:
        data = json.loads(candidate)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_output(stdout: str) -> dict:
    """Extrait l'objet conforme au schéma de la sortie `--output-format json` de `claude -p`."""
    try:
        data = json.loads(stdout)
    except ValueError as e:
        raise ClaudeCodeError(f"sortie de claude illisible : {stdout[:300]}") from e
    if isinstance(data, list):                      # forme « liste de messages » : garder le résultat final
        results = [d for d in data if isinstance(d, dict) and d.get("type") == "result"]
        data = results[-1] if results else {}
    if not isinstance(data, dict):
        raise ClaudeCodeError("sortie de claude inattendue")
    if data.get("is_error") or (data.get("subtype") and data.get("subtype") != "success"):
        raise ClaudeCodeError(f"claude a échoué ({data.get('subtype') or 'erreur'}) : {str(data.get('result'))[:300]}")
    for key in ("structured_output", "structuredOutput", "output"):
        if isinstance(data.get(key), dict):
            return data[key]
    res = data.get("result")
    if isinstance(res, dict):
        return res
    parsed = _json_from_text(res) if isinstance(res, str) else None
    if parsed is None:
        raise ClaudeCodeError(f"pas de JSON dans la réponse de claude : {str(res)[:300]}")
    return parsed


QUERY = "Traite la demande fournie sur l'entrée standard et rends uniquement le JSON demandé."


def run_json(prompt: str, schema: dict, *, cwd: Path, timeout: int = 900, model: str | None = None,
             allowed_tools: tuple[str, ...] = ("Skill", "Read"), max_turns: int = 15, runner=subprocess.run) -> dict:
    """Lance `claude -p` (demande sur l'entrée standard), sortie JSON contrainte par `schema` (champ structured_output).
    Outils autorisés : charger une skill (« Skill ») et lire ses références sur le Drive (« Read ») ; tout le reste
    est refusé (`--permission-mode dontAsk`)."""
    exe = binary()
    if not exe:
        raise ClaudeCodeError("commande claude introuvable (installer Claude Code ou régler MP_CLAUDE_BIN)")
    cmd = [exe, "-p", QUERY, "--output-format", "json", "--json-schema", json.dumps(schema, ensure_ascii=False),
           "--allowedTools", ",".join(allowed_tools), "--permission-mode", "dontAsk", "--max-turns", str(max_turns)]
    model = model or os.environ.get("MP_CLAUDE_CODE_MODEL") or None
    if model:
        cmd += ["--model", model]
    env = dict(os.environ)
    # Une clé API ou un jeton passe AVANT la connexion claude.ai dans l'ordre d'authentification de Claude Code :
    # on les retire pour que le dossier soit fait sur l'abonnement, avec les skills du compte.
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    env.setdefault("CLAUDE_CODE_SYNC_SKILLS", "1")  # skills du compte à jour avant la réponse
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        proc = runner(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(cwd), env=env)
    except subprocess.TimeoutExpired as e:
        raise ClaudeCodeError(f"claude n'a pas répondu en {timeout} s") from e
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        raise ClaudeCodeError(f"claude a échoué (code {proc.returncode}) : {(proc.stderr or '')[:400]}")
    return parse_output(proc.stdout)


def auth_status(runner=subprocess.run) -> dict:
    """`claude auth status` : {"authMethod": "claude.ai" | "api_key" | "oauth_token" | "none", ...}."""
    exe = binary()
    if not exe:
        return {"authMethod": "absent"}
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    try:
        proc = runner([exe, "auth", "status"], capture_output=True, text=True, timeout=30, env=env)
        data = json.loads(proc.stdout or "{}")
        return data if isinstance(data, dict) else {"authMethod": "inconnu"}
    except (ValueError, OSError, subprocess.TimeoutExpired) as e:
        return {"authMethod": "inconnu", "erreur": str(e)[:200]}


def version(runner=subprocess.run) -> str:
    """`claude --version` → « 2.1.288 » (chaîne vide si la commande est absente ou muette)."""
    exe = binary()
    if not exe:
        return ""
    try:
        proc = runner([exe, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"\d+\.\d+\.\d+", proc.stdout or "")
    return m.group(0) if m else ""


# Commandes à donner à Xavier : sans la clé API du shell, qui ferait passer `claude` hors de l'abonnement et
# l'empêcherait de voir les skills du compte ; la synchronisation des skills n'est active qu'avec cette variable.
LOGIN_HINT = "lancer `env -u ANTHROPIC_API_KEY claude`, taper /login et choisir le compte claude.ai"
SYNC_HINT = ("mettre Claude Code à jour (`claude update`), puis lancer "
             "`env -u ANTHROPIC_API_KEY CLAUDE_CODE_SYNC_SKILLS=1 claude -p ok --max-turns 1`")


def workdir() -> Path:
    """Répertoire de travail neutre, hors du dépôt (sinon Claude Code chargerait le CLAUDE.md de développement)."""
    import tempfile
    return Path(tempfile.gettempdir()) / "mission-pipeline-claude"


# ---------------------------------------------------------------------------
# Dossier et réécriture de lettre avec les skills du compte
# ---------------------------------------------------------------------------

def _offer_block(title: str, employer: str, location: str, jd_text: str) -> str:
    return (f"OFFRE\nEmployeur : {employer}\nPoste : {title}\nLieu : {location or 'n.c.'}\n"
            f"Fiche de poste :\n{(jd_text or '(fiche non disponible : appuie-toi sur le titre et l’employeur)')[:12000]}")


def dossier_prompt(*, title: str, employer: str, location: str, jd_text: str, lang: str, profile: str, lang_cv: str,
                   cv_text: str, clauses: list[str], keywords: list[str], profile_md: str, words: tuple[int, int],
                   writing_rules: str) -> str:
    lo, hi = words
    langue = "français" if lang == "FR" else "anglais"
    langue_cv = "français" if lang_cv == "FR" else "anglais"
    extra = ""
    if clauses:
        extra += ("\n\nPRÉCISIONS À INTÉGRER À LA LETTRE (obligatoire, de façon fluide, jamais en liste) :\n"
                  + "\n".join(f"- {c}" for c in clauses))
    if keywords:
        extra += "\n\nMOTS-CLÉS DE L'ANNONCE (relevés au scoring) : " + ", ".join(keywords)
    return f"""Tu prépares le dossier de candidature de Xavier Robitaille pour l'offre ci-dessous. Exécution automatique :
personne ne lit tes messages intermédiaires, seul le JSON final compte.

1. Charge et applique les skills `cv-tailoring` (retouches du CV), `cover-letter` (texte de candidature : faits durs
   verbatim, standards FR/EN, doctrine des écarts, objections) et `voix-xavier` (registre cover text).
2. Tout ce dont tu as besoin est fourni ici : l'offre, le CV de base déjà choisi (profil {profile}, en {langue_cv}),
   le profil de Xavier et ses règles d'écriture. Ne cherche pas ces éléments sur le disque, ne va pas sur le web,
   ne crée aucun fichier : le pipeline produit le DOCX et le PDF à partir de ta réponse. Tu peux lire les
   références de `voix-xavier` sur le Drive si la skill le demande.
3. Rends uniquement le JSON demandé :
   - `edits` : 3 à 6 retouches chirurgicales du CV ; `old` est une sous-chaîne EXACTE (caractère pour caractère)
     d'un paragraphe du CV fourni, `new` est en {langue_cv}, de longueur comparable ; jamais de chiffre, d'outil, de
     client ni d'expérience inventés, jamais de date ni de montant modifiés ;
   - `gaps` : exigences réelles de l'offre que le CV ne couvre pas (0 à 4, une ligne chacune, en français) ;
   - `lettre` : le texte de candidature en {langue}, entre {lo} et {hi} mots, sans titre ni en-tête, clôture et
     signature conformes à la skill ;
   - `objections` : objections à préparer pour l'entretien, une ligne chacune (objection + réponse).{extra}

{_offer_block(title, employer, location, jd_text)}

CV DE BASE (texte, profil {profile}) :
{cv_text}

PROFIL DE XAVIER :
{profile_md}

RÈGLES D'ÉCRITURE ({langue}) :
{writing_rules or "(chargées par la skill)"}"""


def rewrite_prompt(*, title: str, employer: str, location: str, text: str, consigne: str, lang: str,
                   words: tuple[int, int], profile_md: str) -> str:
    lo, hi = words
    langue = "français" if lang == "FR" else "anglais"
    return f"""Tu réécris le texte de candidature de Xavier Robitaille selon SA consigne. Exécution automatique : seul le
JSON final compte. Charge et applique les skills `cover-letter` (faits durs verbatim, standards FR/EN) et
`voix-xavier` (registre cover text). Ne crée aucun fichier, ne va pas sur le web. Ne change que ce que la consigne
demande ; texte en {langue}, entre {lo} et {hi} mots ; rends `lettre` et `objections`.

CONSIGNE DE XAVIER : {consigne.strip()}

{_offer_block(title, employer, location, "")}

LETTRE ACTUELLE :
{text}

PROFIL DE XAVIER :
{profile_md}"""


def dossier_content(**kw):
    """Retouches du CV + lettre rédigées par Claude Code avec les skills. Lève ClaudeCodeError en cas d'échec."""
    from mp.letter import word_count
    from mp.models import DOSSIER_SCHEMA, CvEditPlan, Letter
    runner = kw.pop("runner", subprocess.run)
    data = run_json(dossier_prompt(**kw), DOSSIER_SCHEMA, cwd=workdir(), runner=runner)
    plan = CvEditPlan.model_validate({"edits": data.get("edits", []), "gaps": data.get("gaps", [])})
    letter = Letter.model_validate({"lettre": data.get("lettre", ""), "objections": data.get("objections", [])})
    if word_count(letter.lettre) < 120:
        raise ClaudeCodeError(f"lettre trop courte ({word_count(letter.lettre)} mots)")
    return plan, letter


def rewrite_content(**kw):
    from mp.letter import word_count
    from mp.models import LETTER_SCHEMA, Letter
    runner = kw.pop("runner", subprocess.run)
    data = run_json(rewrite_prompt(**kw), LETTER_SCHEMA, cwd=workdir(), runner=runner)
    letter = Letter.model_validate(data)
    if word_count(letter.lettre) < 100:
        raise ClaudeCodeError(f"réécriture trop courte ({word_count(letter.lettre)} mots)")
    return letter
