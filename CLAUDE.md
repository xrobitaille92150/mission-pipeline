# Mission Pipeline — Instructions techniques

**Code du pipeline** — n8n workflows (Gmail → Airtable) + script Python (génération CV/CL)  
**Repo** : `/Users/xavierrobitaille/Claude/Artifacts/mission-pipeline/` (git, dépôt privé GitHub `xrobitaille92150/mission-pipeline`)  
**Suivi candidatures** : voir dossier Cowork `Candidatures/` pour le dashboard et le contexte utilisateur

---

## Architecture rapide

```
Gmail (LinkedIn, recruteurs, alertes jobalerts)
  │
  └─→ n8n (VPS Hostinger, cron 4h)
      ├─→ Claude Haiku (Batch API)
      └─→ Airtable base Mission Pipeline (apphTpnW5vu0OdnfC)
          ├── Candidatures (tblF3jpncEXA647ou) — suivi statut
          ├── A traiter (tblSeyppFhxU3i8Ev) — revue manuelle
          └── Veille (tblrXH5Jiyg6w21lW) — offres jobalerts
              │
              └─→ run_dossiers.py (launchd 6h/12h/19h)
                  ├─→ LinkedIn fetch
                  ├─→ CV adapté (python-docx)
                  ├─→ Cover letter
                  ├─→ PDF (pandoc + Chrome headless)
                  ├─→ GitHub push
                  └─→ Airtable PATCH
```

---

## Fichiers clés

| Fichier | Rôle |
|---|---|
| `README.md` | Spec détaillée du pipeline n8n (schéma Airtable, règles déterministes, triage, logs) |
| `daily.workflow.json` | Export n8n du workflow quotidien (source de vérité locale) |
| `backfill.workflow.json` | Export n8n du backfill (historique) |
| `nodes/*.js` | Logique détachée des nœuds n8n (matching, triage, parsing) |
| `scripts/run_dossiers.py` | Script Python — génération CV/CL via Claude Haiku |
| `scripts/run-dossiers.sh` | Lanceur shell (appelé par launchd, chemins absolus) |
| `logs/dossiers_*.log` | Logs d'exécution (un par run) |

---

## Mémoires auto-memory (Code)

Pour les bugs, flow, et state :
- **`project-dossiers-python.md`** — bugs launchd (chemin python3/pandoc), CV profils, séquence complète
- **`n8n-workflow-debug-cheatsheet.md`** — pannes n8n courantes et correctifs (à lire en premier en cas d'erreur)
- **`specs-n8n-gmail-airtable.md`** — field IDs, credentials, expressions exactes
- **`Cheatsheet débogage n8n`** — lookup rapide pour les erreurs

À consulter avant toute itération.

---

## Itérer (1 feature = 1 commit)

### Ajouter une règle n8n

1. **Décrire la règle** dans le README (avant/après)
2. **Modifier les nœuds** (`nodes/*.js` ou `daily.workflow.json`)
3. **Tester** sur une semaine (backfill ou run manuel)
4. **Déployer** sur n8n live via l'UI ou API (`~/.config/mission-pipeline/n8n.env`)
5. **Réexporter** le workflow (UI n8n → Export JSON)
6. **Commit** : `git add daily.workflow.json nodes/ README.md && git commit -m "..."`

### Fixer un bug run_dossiers.py

1. **Vérifier les logs** : `logs/dossiers_YYYY-MM-DD_*.log`
2. **Éditer le script** : `scripts/run_dossiers.py`
3. **Tester localement** : `python3 scripts/run_dossiers.py --test <jobId>`
4. **Commit** : `git commit -m "fix(dossiers): ..."`
5. **Relancer launchd** : `launchctl start com.xrobitaille.dossiers` ou attendre le prochain run planifié

---

## Credentials (jamais committés)

- `~/.config/mission-pipeline/anthropic.env` — `ANTHROPIC_API_KEY`
- `~/.config/mission-pipeline/airtable.env` — `AIRTABLE_PAT`
- `~/.config/mission-pipeline/n8n.env` — `N8N_BASEURL`, `N8N_API_KEY` (pour déploiement distant)

Lire ces fichiers dans les scripts, ne jamais les committer.

---

## État au 22 juin 2026

**n8n** (ingestion email)
- Run quotidien : actif, cron `0 4 * * *`
- Backfill : terminé
- Branche Veille : active (triage Claude, table Veille)
- ✅ Bugs corrigés : `undefined→null` date fields (22/06)

**run_dossiers.py** (génération dossiers)
- ✅ Opérationnel depuis le 19 juin 2026
- launchd actif : 6h / 12h / 19h
- ✅ Bugs corrigés : chemin absolu python3/pandoc (21-22/06), profil AO supprimé
- CV profils : FinanceTransformation, AssetManagement, IFRS17SolvencyII

Voir `project-dossiers-python.md` et `n8n-workflow-debug-cheatsheet.md` pour les bugs connus et leurs solutions.

---

## Dashboard & suivi

**Cowork Candidatures** (`/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/`)  
- `CLAUDE.md` — instructions techniques complètes
- `PROJECT.md` — vue d'ensemble, KPIs, architecture
- `artifacts/mission-pipeline.html` — dashboard live (Airtable, KPIs, alertes relance)

Le dossier Cowork est le point d'entrée pour les candidatures ; ce repo (Code) est le moteur technique.

---

## Premiers pas

**Debuguer une erreur n8n** → lire [`n8n-workflow-debug-cheatsheet.md`](../../../.claude/projects/-Users-xavierrobitaille-Claude-Artifacts-mission-pipeline/memory/n8n-workflow-debug-cheatsheet.md) (mémoire auto)

**Ajouter une règle n8n** → éditer `daily.workflow.json` ou `nodes/*.js`, tester, déployer

**Générer un dossier CV/CL manuellement** → `python3 scripts/run_dossiers.py` (local) ou attendre le prochain run launchd

**Vérifier les logs** → `tail -f logs/dossiers_*.log`

---

## Git & déploiement

```bash
# Local branches
git status
git log --oneline

# Push to GitHub
git push origin main

# Voir les derniers deployments n8n
curl -s -H "Authorization: Bearer $N8N_API_KEY" \
  https://mission-pipeline.fr/api/v1/workflows/<ID>/executions | jq
```

1 feature = 1 commit. Squash si nécessaire avant merge.

---

## Ressources connexes

- **Mémoires auto** : `/Users/xavierrobitaille/.claude/projects/-Users-xavierrobitaille-Claude-Artifacts-mission-pipeline/memory/`
- **GitHub** : `https://github.com/xrobitaille92150/mission-pipeline` (privé)
- **n8n live** : Hostinger VPS (credentials dans `~/.config/mission-pipeline/n8n.env`)
- **Airtable** : base `apphTpnW5vu0OdnfC` (Mission Pipeline)

---

**Dernière mise à jour** : 22 juin 2026
