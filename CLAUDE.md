# Mission Pipeline — Instructions techniques

**Code du pipeline** — service Python sur le Mac (agents JOE / BOB / MATT / JACK) + exports n8n historiques + génération CV/CL
**Emplacement sur le Mac** : `/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/pipeline/` (chemin codé en dur dans `scripts/run-pipeline.sh`, `scripts/run-dossiers.sh`, `scripts/run-jack-hourly.sh`)
**GitHub** : dépôt privé `xrobitaille92150/mission-pipeline`

---

## Architecture (état constaté dans le code au 03/10/2026)

```
launchd com.xrobitaille.missionrun (06:15 / 18:15)
  └─→ service/run.py — orchestrateur, digest email en fin de run
      ├─ JOE  : ingest pont Gmail + enrichissement Veille 2
      │         notes Sonnet (claude-sonnet-4-6) + score Haiku (claude-haiku-4-5-20251001)
      ├─ BOB  : triage emails de candidature (Haiku) → Candidatures / A traiter / Email Triage
      └─ MATT : scripts/run-dossiers.sh → scripts/run_dossiers.py (CV + CL, PDF, push GitHub, PATCH Airtable)

launchd com.xrobitaille.jack (toutes les heures à :45)
  └─→ scripts/run-jack-hourly.sh → service/jack.py
      purge « J'écarte », dédoublonnage, nettoyage > 7 j, puis MATT (max 12 dossiers / run)
      verrou partagé avec MATT : /tmp/run-dossiers.lock (périmé après 90 min)
```

Airtable — base `apphTpnW5vu0OdnfC` (IDs dans `service/lib/config.py`) :
- Veille 2 `tblrCyL6huHkUPZbF` — offres jobalerts (table utilisée par JOE / JACK)
- Candidatures `tblF3jpncEXA647ou` — suivi statut
- A traiter `tblSeyppFhxU3i8Ev` — revue manuelle
- Email Triage `tblOkwh1UtFHQpyct` — log de classification BOB
- Veille `tblrXH5Jiyg6w21lW` — ancienne table des workflows n8n

Seuil actionnable du score : **≥ 50** (`SEUIL` dans `service/lib/config.py`).
Barème de scoring : `nodes/scoring-bareme-prompt.txt` (source unique, régénéré par `scripts/sync_bareme.sh`).

---

## Modèles Claude utilisés

| Usage | Modèle | Où |
|---|---|---|
| Notes Veille (JOE) | `claude-sonnet-4-6` | `service/lib/config.py` (`MODEL_NOTES`) |
| Score Veille (JOE) | `claude-haiku-4-5-20251001` | `service/lib/config.py` (`MODEL_SCORE`) |
| Triage emails (BOB) | `claude-haiku-4-5-20251001` | `service/bob.py` via `lib/claude.py` |
| Gap analysis CV (MATT) | `claude-haiku-4-5-20251001` | `scripts/run_dossiers.py` (`HAIKU_MODEL`) |
| Cover letter (MATT) | `claude-sonnet-5` | `scripts/run_dossiers.py` (`SONNET_MODEL`) |

Le score de production tourne sur **Haiku**, pas sur Opus. `nodes/veille-scoring.js` (Sonnet 4) et `scripts/test_scoring.py` (Opus 4.1) sont des prototypes de juin, non appelés par le service.

---

## Fichiers clés

| Fichier | Rôle |
|---|---|
| `service/run.py` | Orchestrateur JOE → BOB → MATT (`--dry-run` disponible) |
| `service/joe.py` | Enrichissement Veille 2 (`--test [N]`, `--dry-run`) |
| `service/bob.py` | Triage emails (`--write` pour écrire, sinon lecture seule) |
| `service/jack.py` | Agent horaire Veille 2 (`--dry-run`) |
| `service/lib/` | Config, clients Airtable / Claude / Gmail, digest email |
| `scripts/run_dossiers.py` | MATT — génération CV/CL (python-docx, pandoc, Chrome headless) |
| `scripts/run-dossiers.sh` | Lanceur MATT (charge les `.env`, pose le verrou) |
| `scripts/apply_tool.py` + `run-apply-tool.sh` | Outil local « Postuler proprement » (http://localhost:8765) |
| `nodes/scoring-bareme-prompt.txt` | Barème de scoring (lu par JOE, apply_tool, backfills) |
| `README.md` | Spec historique du pipeline n8n (statuts, règles déterministes, matching, schéma Airtable) |
| `*.workflow.json` | Exports n8n (`daily`, `backfill`, `veille-notes`, `veille-alerte-dossiers`, `mission-team`) |
| `SCORING.md`, `SCORING_QUICKSTART.md`, `DEPLOY_SCORING.md` | Docs du scoring v1 (juin, architecture n8n) |

---

## Itérer (1 feature = 1 commit)

### Modifier un agent du service
1. Éditer `service/<agent>.py` ou `service/lib/*.py`
2. Tester sans écrire : `python3 service/run.py --dry-run` (ou `joe.py --dry-run`, `jack.py --dry-run`, `bob.py` sans `--write`)
3. Commit, puis attendre le prochain run launchd

### Fixer un bug MATT (run_dossiers.py)
1. Lire les logs : `logs/shell_*.log`, `logs/dossiers_*.log`, `logs/launchd_dossiers_err.log`
2. Éditer `scripts/run_dossiers.py`
3. Tester : `MATT_MAX=1 scripts/run-dossiers.sh` (le script n'a pas d'option `--test`)
4. Commit : `git commit -m "fix(dossiers): ..."`

### Modifier le barème de scoring
Éditer le barème source puis `scripts/sync_bareme.sh` (régénère et commit `nodes/scoring-bareme-prompt.txt`).

---

## Credentials (jamais committés)

- `~/.config/mission-pipeline/anthropic.env` — `ANTHROPIC_API_KEY`
- `~/.config/mission-pipeline/airtable.env` — `AIRTABLE_PAT`
- `~/.config/mission-pipeline/n8n.env` — `N8N_BASEURL`, `N8N_API_KEY`

Lire ces fichiers dans les scripts, ne jamais les committer.

---

## Points à confirmer par Xavier

Ces points ne peuvent pas être vérifiés depuis le dépôt :
- **n8n** : les workflows du VPS Hostinger sont-ils encore actifs, ou remplacés par le service Mac depuis le 03/07/2026 ? Les exports affichent `active: true` mais datent de juin.
- **Ancien launchd `com.xrobitaille.dossiers`** (6h15 / 12h15 / 19h15) : `run-dossiers.sh` mentionne un « cron résiduel ». Est-il désactivé ?
- **Emplacement du dépôt** : le code pointe vers `~/Desktop/Claude/Projects/...`, alors que l'organisation du 15/07/2026 range tout sous `~/Mon Drive/XavierAdvisory/`. Les chemins `CONTEXT_FILE` et `WRITING_*` de `run_dossiers.py` pointent eux aussi vers `~/Desktop/Claude/...`.
- **Mémoires auto-memory** : elles sont rangées par chemin de projet. Si le dépôt a quitté `~/Claude/Artifacts/mission-pipeline/`, les anciennes mémoires (`project-scoring-deployed.md`, `n8n-workflow-debug-cheatsheet.md`, etc.) ne se chargent plus.

---

**Dernière mise à jour** : 3 octobre 2026
