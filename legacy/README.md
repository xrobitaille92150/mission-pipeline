# legacy/ — le pipeline de juin-juillet 2026, conservé pour mémoire

Tout ce qui est dans ce dossier **n'est plus exécuté**. Il a été remplacé le 1er octobre 2026 par
le moteur `mp/` (voir `../README.md` et `../docs/REFONTE-2026-10.md`). Ces fichiers restent dans le
dépôt pour retrouver une règle, une expression Airtable ou un prompt de l'époque ; ils peuvent être
supprimés sans risque une fois la v3 stabilisée.

| Dossier | Contenu | Génération |
|---|---|---|
| `n8n/` | Exports des 5 workflows n8n (daily, backfill, veille-notes, veille-alerte-dossiers, mission-team) et logique des nœuds (`nodes/*.js`, prompts de scoring) | v1 — juin 2026, VPS Hostinger |
| `service/` | Agents Python JOE (ingestion + enrichissement), BOB (triage email), JACK (purge horaire), `run.py` (orchestrateur), `lib/` | v2 — juillet 2026, launchd Mac |
| `scripts/` | `run_dossiers.py` (CV + lettre, pandoc + Chrome headless), `apply_tool.py` (UI web locale), expériences Managed Agents (`ma_*.py`), backfills, parseur du barème Excel | v1/v2 |
| `docs/` | `SCORING.md`, `SCORING_QUICKSTART.md`, `DEPLOY_SCORING.md`, `veille-jobalerts.feature.md` | v1 |
| `dashboard/` | Dashboard HTML (artifact Cowork) et ses versions | v1 |
| `scratch/` | Pages texte des CV de juin, base64, trois CV adaptés à la main (BearingPoint, Deloitte, EY) | juin 2026 |
| `README-v1-n8n.md` | Spécification détaillée du pipeline n8n (schéma Airtable, règles déterministes, triage, logs) | v1 |
| `CLAUDE-v2.md` | Instructions techniques de l'ancien dépôt (état au 24 juin) | v2 |

## Pourquoi la refonte

- Trois générations coexistaient (n8n, agents Python, Managed Agents) pour un même flux, sans
  source de vérité unique : une règle vivait dans un nœud n8n, sa copie dans `service/lib`, sa
  variante dans un prompt.
- Le scoring produisait un CV pour presque chaque offre scorée (324 CV pour 325 offres), sans seuil
  ni classement : le travail utile était noyé.
- Le dépôt GitHub servait d'hébergeur de PDF (`candidatures/`, 130 Mo, 858 commits « dossiers: … »)
  pour alimenter des liens Airtable ; la v3 attache les fichiers directement dans Airtable.
- Le suivi des réponses (BOB) était mort depuis le 17 juillet ; les candidatures envoyées depuis
  n'étaient plus tracées.

## Ce qui a été repris dans la v3

- Les champs historiques de la table Offres (ex-« Veille 2 ») : `jobId`, `Employeur`, `Poste`,
  `Lieu`, `Mode`, `Score`, `Easy Apply`, `URL`, `Date 1ère vue`, `Préparer dossier`, `Je postule`,
  `J'écarte`, `Recherche LI`.
- Le parseur des digests LinkedIn (`nodes/filtrer-preparer.js`) → `mp/gmail.py`, réécrit et testé.
- Les 7 blocs du barème de scoring → `mp/prompts/bareme.md`, condensés en un seul appel Claude.
- Les HARD FACTS de la lettre (skill `cover-letter` v2.4) → `mp/prompts/cover_common.md`, verbatim.
- La sélection du profil CV (arbre de décision `cv_profiles.md`) → `mp/cv.py::select_profile`.
