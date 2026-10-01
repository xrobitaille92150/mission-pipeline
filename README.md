# Mission Pipeline v3

Des alertes LinkedIn reçues dans Gmail au dossier de candidature (CV adapté + lettre) attaché dans
Airtable, sans intervention. Il reste **trois clics** à faire par offre : lire, télécharger, cocher.

```
Gmail ─► mp ingest ─► Airtable « Offres » ─► mp score (Claude) ─► top 3 du jour ─► mp dossiers (CV + lettre, PDF)
                                   ▲                                                      │
                                   └──── mp sync (Je postule / J'écarte) ◄── Xavier coche ◄┘
                                   └──── mp track (emails de statut) ─► « Candidatures »
```

Un seul moteur (`mp/`), un seul ordonnanceur (GitHub Actions ou launchd), une seule interface (Airtable).
Le détail de la refonte, l'audit chiffré et le runbook de migration : [`docs/REFONTE-2026-10.md`](docs/REFONTE-2026-10.md).

## Répondre à une offre

1. Ouvrir la vue **À décider** (table Offres). La ligne de `Rang du jour` 1 est la meilleure offre du run ;
   `Pourquoi` et `Red flags` disent pourquoi.
2. Télécharger le PDF dans `CV (fichiers)` ; copier `Lettre texte` dans le formulaire LinkedIn.
3. Cocher **Je postule**. Statut, date, ligne Candidatures et suivi des réponses suivent tout seuls.

Offre trouvée ailleurs (réseau, site d'un cabinet) :

```bash
mp dossier --url https://www.linkedin.com/jobs/view/4444856066/
mp dossier --text annonce.txt --title "Head of Finance" --employer "Swiss Re"   # si LinkedIn bloque
```

En session Claude : le skill `/postuler` (`skills/postuler/SKILL.md`) fait la même chose.

## Installation

```bash
git clone git@github.com:xrobitaille92150/mission-pipeline.git && cd mission-pipeline
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
brew install --cask libreoffice            # Mac ; sur Linux : apt install libreoffice-writer
```

Secrets : dans `~/.config/mission-pipeline/*.env` (format `KEY=VALUE`, jamais commités) ou dans les
secrets du dépôt GitHub. Liste complète et valeurs par défaut : [`.env.example`](.env.example).

```bash
mp doctor                      # vérifie secrets, LibreOffice, CV de base, règles, Airtable, Gmail, Claude
mp airtable-setup --apply      # crée les champs Airtable manquants (une fois)
mp run --dry-run -v            # répétition sans écriture
mp run                         # run complet + digest par email
```

## Commandes

| Commande | Rôle |
|---|---|
| `mp run [--days N] [--limit N] [--max-dossiers N] [--skip …] [--label matin]` | ingest → sync → score → dossiers → track → digest |
| `mp ingest` | lit les digests LinkedIn des N derniers jours (label Gmail `MissionPipeline` posé sur les emails traités) |
| `mp sync` | applique `Je postule` / `J'écarte`, expire les offres silencieuses depuis 30 jours |
| `mp score [--rescore]` | filtres durs puis un appel Claude par offre ; coche `Préparer dossier` sur le top du jour |
| `mp dossiers` | CV + lettre pour les lignes cochées, attachés dans Airtable |
| `mp dossier --url … \| --job-id … \| --text …` | dossier à la demande |
| `mp track [--days N]` | emails de statut LinkedIn et recruteurs → `Réponse`, Candidatures |
| `mp digest` | renvoie le digest du dernier run |
| `mp airtable-setup [--apply]` | schéma Airtable |
| `mp doctor [--offline]` | diagnostic |

Options globales : `--dry-run` (n'écrit rien), `-v`.

## Réglages

| Variable | Défaut | Effet |
|---|---|---|
| `MP_MODEL_MAIN` / `MP_MODEL_FAST` | `claude-opus-5-5` / `claude-haiku-4-5` | scoring, CV, lettre / classification des emails |
| `MP_SCORE_MIN` | 60 | score minimal pour un dossier automatique |
| `MP_AUTO_DOSSIERS_PER_RUN` | 3 | dossiers générés sans clic, par run |
| `MP_INGEST_DAYS` | 2 | fenêtre Gmail |
| `MP_LABEL_DONE` | `MissionPipeline` | label Gmail des emails traités |
| `MP_CV_BASE_DIR` | `assets/cv_base` | 6 CV de base (3 profils × FR/EN) |
| `MP_WRITING_RULES_DIR` | `assets/writing_rules` | règles d'écriture FR / EN |
| `MP_DRIVE_DOSSIERS_DIR` | (vide) | copie des dossiers vers le miroir Drive, si renseigné |

## Ordonnancement

- **GitHub Actions** (recommandé) : [`.github/workflows/pipeline.yml`](.github/workflows/pipeline.yml),
  06:30 et 18:30 (Paris, été), lancement manuel, et `repository_dispatch` depuis l'automation Airtable pour
  un dossier dans la minute ([`deploy/airtable/`](deploy/airtable/README.md)).
- **launchd** (Mac) : `zsh deploy/launchd/install.sh`, mêmes horaires.
- **Arrêt de l'ancien pipeline** (n8n + agents launchd v2) : `zsh deploy/decommission.sh`.

## Structure du dépôt

```
mp/                 moteur (config, gmail, linkedin, airtable, claude, scoring, cv, letter, pdf, dossier,
                    tracking, digest, pipeline, context, cli)
mp/prompts/         prompts versionnés : profile, scoring, bareme, cv_edits, cover_common/fr/en, tracking
assets/cv_base/     CV_XRO_{EN,FR}_{FinanceTransformation,AssetManagement,IFRS17_SolvencyII}_v4.docx
assets/writing_rules/   WRITING RULES.md, REGLES-ECRITURE-FR.md
tests/              31 tests pytest (doubles Airtable / Claude en mémoire)
deploy/             launchd (Mac), automation Airtable
skills/postuler/    skill Claude « prépare le dossier pour cette offre »
docs/               REFONTE-2026-10.md
legacy/             n8n, agents Python de juillet, scripts : plus exécutés, gardés pour mémoire
out/                sorties locales (dossiers, logs), ignorées par git
```

## Tests

```bash
.venv/bin/python -m pytest          # 31 tests, < 10 s (le test PDF est sauté si LibreOffice est absent)
```

## Airtable

Base `apphTpnW5vu0OdnfC` : table **Offres** (`tblrCyL6huHkUPZbF`, ex-« Veille 2 ») et table
**Candidatures** (`tblF3jpncEXA647ou`). Champs, vues et automation : [`deploy/airtable/README.md`](deploy/airtable/README.md).
