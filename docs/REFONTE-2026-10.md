# Mission Pipeline v3 — refonte du 1er octobre 2026

> Objectif fixé par Xavier : « un minimum de clics pour répondre à une offre ». Résultat : **trois clics
> dans Airtable** (lire, télécharger, cocher), tout le reste est automatique. Ce document consigne l'audit
> de l'existant, la cible retenue, les décisions prises, celles qui restent à prendre et le runbook de
> migration.

---

## 1. Audit de l'existant (mesuré le 1er octobre 2026)

Chiffres relevés directement dans Airtable, Gmail et le dépôt git ce jour-là. Ils peuvent avoir bougé
depuis ; les ordres de grandeur suffisent au diagnostic.

### 1.1 Trois générations de code pour un seul flux

| Génération | Quand | Où | Composants | État au 1er octobre |
|---|---|---|---|---|
| v1 n8n | juin 2026 | VPS Hostinger | 5 workflows (daily, backfill, veille-notes, veille-alerte-dossiers, mission-team), 15 nœuds JS, scoring Opus en 7 blocs | Routines éteintes volontairement, mais une ingestion tournait encore deux fois par jour (04:15 et 16:15 UTC) et remplissait la table Veille 2 |
| v2 agents Python | juillet 2026 | Mac (launchd 06:15 / 18:15, purge horaire) | JOE (ingestion + enrichissement), BOB (triage email), JACK (purge), MATT = `run_dossiers.py` (CV + lettre), `run.py` | BOB mort depuis le 17 juillet ; dossiers générés en masse |
| v2 bis Managed Agents | juillet 2026 | API Anthropic | `ma_joe_*.py`, `ma_email_triage_*.py` | Expériences, jamais mises en production |
| Outil local | juillet 2026 | Mac | `apply_tool.py` (UI web locale) | Non utilisé |

Une même règle (par exemple le filtre junior, ou le parseur des cartes LinkedIn) existait en trois
exemplaires divergents : nœud n8n, module `service/lib`, prompt. Aucune n'était testée.

### 1.2 La donnée

| Mesure | Valeur | Lecture |
|---|---|---|
| Lignes dans Veille 2 (offres) | 717 | dont 392 jamais scorées |
| Offres scorées | 325 | dernier scoring le 1er septembre |
| Offres avec un CV généré | 324 | **un CV pour quasiment chaque offre scorée** : aucun seuil, aucun classement |
| Distribution des scores | concentrée sur 58 / 62 / 68 / 72 | le barème en 7 blocs produisait des scores moyens indifférenciés |
| Lignes dans Candidatures | 36 | dernier « Envoyé » le 17 juillet, alors que Gmail montre des candidatures LinkedIn envoyées en septembre |
| Lignes dans Email Triage | 3 258 | emails de toute nature (y compris non professionnels) copiés dans Airtable |
| Emails jobalerts LinkedIn | ≈ 20 par jour | 30 à 60 offres par jour |

### 1.3 Le dépôt

| Mesure | Valeur |
|---|---|
| Commits | 942, dont 858 « dossiers: … » (dépôts automatiques de PDF) |
| `candidatures/` | 130 Mo, 1 171 PDF |
| Rôle de GitHub | hébergeur de PDF pour alimenter des liens dans Airtable |

### 1.4 Diagnostic en quatre points

1. **Pas de source de vérité.** Trois implémentations, zéro test, des règles qui se contredisent.
2. **Surproduction.** Un dossier par offre scorée : le travail utile (2 à 3 offres par jour) est noyé
   dans 300 PDF.
3. **Suivi cassé.** Les réponses des recruteurs ne remontaient plus ; la table Candidatures était à jour
   à la main ou pas du tout.
4. **Outillage fragile.** pandoc + Chrome headless pour le PDF, chemins absolus Mac, GitHub comme CDN,
   secrets dispersés (n8n, launchd, scripts).

---

## 2. La cible

### 2.1 Principes

- **Un moteur** : le package Python `mp/` (15 modules, 2 900 lignes, 31 tests). Rien d'autre ne s'exécute.
- **Un ordonnanceur** : GitHub Actions (recommandé) ou launchd sur le Mac. Pas les deux.
- **Une interface** : Airtable. Tout ce que Xavier fait se fait dans la vue « À décider » de la table Offres.
- **Fichiers dans Airtable**, pas dans git : CV et lettre attachés à la ligne (PDF + DOCX éditable).
- **Idempotence** : chaque email traité reçoit le label Gmail `MissionPipeline` ; chaque offre a un `jobId` ;
  rejouer un run ne crée jamais de doublon.
- **Tout est versionné et testé** : prompts (`mp/prompts/*.md`), CV de base, règles d'écriture, tests pytest.

### 2.2 Flux

```
Gmail (jobalerts-noreply@, jobs-noreply@)
  │  IMAP, label « MissionPipeline » sur les emails traités
  ▼
mp ingest ──► Offres (Airtable)            upsert sur jobId, Statut = Nouvelle, Dernière vue
  │
mp sync ────► décisions cochées            Je postule → Postulée + ligne Candidatures ; J'écarte → Écartée ;
  │                                        silence 30 j → Expirée
mp score ───► filtres durs (0 appel IA)    junior / hors Europe / langue ≠ FR-EN → Écartée
  │           + 1 appel Claude par offre   Score 0-100, Verdict IA, Cluster, Pourquoi, Red flags, Mots-clés, Profil CV
  │           + classement du jour         top 3 (score ≥ 60, verdict Postuler) → « Préparer dossier » coché, Rang du jour
  ▼
mp dossiers ► pour chaque ligne cochée     fiche LinkedIn (ou Description) → profil CV → retouches python-docx
  │                                        → lettre (HARD FACTS, règles FR/EN) → LibreOffice PDF → attachés à la ligne
mp track ───► emails de statut             « candidature envoyée / vue / dernière nouvelle » + emails recruteurs (Haiku)
  │                                        → Réponse (Envoyé → A/R → Oui/Non), Candidatures
  ▼
digest ─────► un email par run s'il y a quelque chose à dire (dossiers prêts, nouvelles offres, erreurs)
```

### 2.3 Les trois clics

| Clic | Où | Ce qui se passe |
|---|---|---|
| 1. Lire | vue **À décider**, ligne de rang 1 | `Pourquoi`, `Red flags`, `Objections`, lien LinkedIn |
| 2. Télécharger | pièce jointe `CV (fichiers)` | PDF prêt (DOCX à côté si retouche manuelle) |
| 3. Cocher | `Je postule` après avoir collé `Lettre texte` dans le formulaire | Statut, date, Candidatures, suivi des réponses : automatiques |

Une offre vue ailleurs que dans Gmail : `mp dossier --url <URL>` (ou le skill `/postuler` en session Claude).
Même résultat, même ligne Airtable.

### 2.4 Scoring : ce qui change

| Avant (v1) | Après (v3) |
|---|---|
| 7 blocs pondérés, 7 appels ou un prompt de 7 sections | filtres durs déterministes puis **un** appel Claude Opus avec sortie JSON validée (Pydantic) |
| scores concentrés entre 58 et 72 | calibration explicite : 85-100 rare, ≥ 70 Postuler, 50-69 Étudier, < 50 Écarter ; plafond 69 sans fiche de poste |
| CV pour toute offre scorée | **3 dossiers par run au maximum**, les mieux classées (`MP_AUTO_DOSSIERS_PER_RUN`, `MP_SCORE_MIN`) ; le reste attend une case cochée |
| profil cible de juin (full remote, hors Europe acceptés) | profil du 6 septembre : mission hybride en France, ≈ 1 000 €/j, clusters B/C prioritaires, A secondaire, full remote et hors Europe rétrogradés |

### 2.5 Dossier : ce qui change

| Avant | Après |
|---|---|
| CV EN seulement, FR par traduction ad hoc | 6 CV de base versionnés (`assets/cv_base/`, 3 profils × FR/EN), langue détectée sur l'annonce |
| retouches par regex sur le texte brut | plan de retouches JSON (ancien → nouveau) appliqué run par run dans le DOCX, mise en forme conservée, filtres de sécurité (longueur, mot « actuaire ») |
| lettre libre | HARD FACTS 1-10 du skill `cover-letter` v2.4 **verbatim** dans le prompt, fourchettes de mots (FR 190-280, EN 250-350) contrôlées avec une relance, clauses géographiques (IR35, CDI) déterministes |
| pandoc + Chrome headless | LibreOffice headless, un seul binaire, même rendu Mac / Linux |
| PDF dans git, lien dans Airtable | PDF + DOCX attachés à la ligne Airtable ; copie facultative vers le miroir Drive |

### 2.6 Suivi : ce qui change

- Les emails LinkedIn de statut sont lus par règles (pas d'IA) : « envoyée », « vue par », « dernière nouvelle ».
- Les autres emails ne sont lus par Claude Haiku **que** s'ils mentionnent une société déjà présente dans
  Candidatures. Fini la copie de 3 258 emails dans Airtable.
- L'entonnoir ne recule jamais : Néant → Envoyé → A/R → Oui / Non.

---

## 3. Décisions prises

| Décision | Choix | Pourquoi |
|---|---|---|
| Langage | Python 3.11+, SDK `anthropic` 1.x, `python-docx`, `requests`, Pydantic | Un seul runtime, testable, pas de dépendance à n8n ni à un VPS |
| Modèles | `claude-opus-5-5` (scoring, retouches CV, lettre) avec repli serveur ; `claude-haiku-4-5` (classification des emails) | Qualité sur ce qui est lu par un recruteur ; coût minimal sur le tri |
| Sorties structurées | JSON Schema côté API + validation Pydantic | Plus de parsing fragile de texte libre |
| Cache de prompt | blocs système (profil, barème, règles d'écriture) mis en cache | Le profil et le barème ne changent pas d'une offre à l'autre |
| Table Offres | réutilisation de « Veille 2 » (`tblrCyL6huHkUPZbF`) avec ses champs historiques | Aucune migration de données ; les 717 lignes restent lisibles |
| Fiche de poste | LinkedIn `jobs-guest` avec replis ; champ `Description` modifiable à la main | LinkedIn bloque parfois (observé depuis le conteneur) ; l'humain peut coller l'annonce |
| Gmail | IMAP + mot de passe d'application, label de traitement | Pas d'OAuth à renouveler, pas de SDK Google ; idempotent |
| Secrets | `~/.config/mission-pipeline/*.env` sur Mac, secrets du dépôt sur Actions | Jamais dans git (règle du dépôt) |
| Legacy | déplacé dans `legacy/`, non supprimé | On garde l'historique des règles ; suppression possible plus tard |

## 4. Décisions à prendre (Xavier)

1. **Ordonnanceur** : GitHub Actions (le Mac peut être fermé, dossier dans la minute via l'automation
   Airtable, logs conservés 14 jours) ou launchd sur le Mac (pas de secret sur GitHub, copie Drive
   automatique, mais le Mac doit être allumé).
2. **Schéma Airtable** : laisser `mp airtable-setup --apply` créer les champs manquants (jusqu'à 26 sur Offres, 2 sur
   Candidatures ; liste dans `mp/airtable.py`), ou les créer à la main.
3. **Actifs dans le dépôt** : les 6 CV de base et les deux fichiers de règles d'écriture sont désormais
   versionnés dans `assets/` (dépôt privé). Indispensable pour GitHub Actions ; facultatif en launchd
   (`MP_CV_BASE_DIR` peut pointer vers le Drive).
4. **Purge de `candidatures/`** : 130 Mo de PDF et 858 commits sans valeur. Supprimer le dossier au
   prochain commit (simple) ou réécrire l'historique (gain de place, mais force-push).
5. **Décommissionnement** : désactiver définitivement les workflows n8n et résilier le VPS Hostinger ;
   retirer les agents launchd v2 (`com.xrobitaille.dossiers`, JACK horaire).

---

## 5. Migration : runbook

### 5.1 Sur le Mac (15 minutes)

```bash
cd /Users/xavierrobitaille/Claude/Artifacts/mission-pipeline
git fetch origin && git checkout claude/mission-pipeline-refonte-m50exw   # puis main après merge
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
brew install --cask libreoffice                                            # si absent

# Secrets (fichiers existants + deux nouveaux)
cat >> ~/.config/mission-pipeline/gmail.env <<'X'
GMAIL_USER=xrobitaille92150@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Google → Sécurité → Mots de passe d'application
X

.venv/bin/mp doctor                      # tout doit être OK sauf « schéma Airtable »
.venv/bin/mp airtable-setup --apply      # crée les champs manquants (PAT avec schema.bases:write)
.venv/bin/mp sync                        # expire les offres de juin-août jamais traitées (aucun appel IA)
.venv/bin/mp run --dry-run -v            # répétition : lit Gmail et Airtable, n'écrit rien
.venv/bin/mp run --label test            # premier run réel → digest par email
```

### 5.2 Vues Airtable (5 minutes)

Créer les vues « À décider », « Dossiers prêts », « Postulées », « Erreurs » décrites dans
`deploy/airtable/README.md`. Facultatif : une interface *Record review* sur « À décider » pour le mobile.

### 5.3 Ordonnanceur

**GitHub Actions** : Settings → Secrets and variables → Actions → ajouter `ANTHROPIC_API_KEY`,
`AIRTABLE_PAT`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`. Le workflow `.github/workflows/pipeline.yml` tourne
à 06:30 et 18:30 (heure de Paris en été). Lancer un premier run depuis l'onglet Actions (*Run workflow*).
Puis, pour les dossiers « dans la minute », l'automation Airtable (`deploy/airtable/`).

**launchd** : `zsh deploy/launchd/install.sh` (06:30 / 18:30, logs dans `out/logs/`).

### 5.4 Décommissionnement

```bash
launchctl bootout gui/$(id -u)/com.xrobitaille.dossiers 2>/dev/null
launchctl bootout gui/$(id -u)/com.xrobitaille.jack 2>/dev/null       # nom à vérifier dans ~/Library/LaunchAgents
ls ~/Library/LaunchAgents | grep -i xrobitaille
```

Sur n8n : désactiver les 5 workflows (ils le sont déjà, sauf l'ingestion : à vérifier), puis résilier le VPS.

### 5.5 Rattrapage des 392 offres jamais scorées

Après `mp sync`, celles vues il y a plus de 30 jours passent en *Expirée* et ne seront jamais scorées. Les
autres (septembre) sont scorées par lots de 80 par run (`--limit`). Pour forcer : `mp score --limit 400`
(un appel Opus par offre).

---

## 6. Limites connues

- **LinkedIn `jobs-guest`** peut refuser la requête (bloqué depuis le conteneur de développement, derrière un
  proxy ; la v1 obtenait les fiches depuis le VPS et le Mac). Replis : champ `Description` d'Airtable, option `--text`, scoring plafonné à 69 sans
  fiche. L'erreur est inscrite dans le champ `Erreur` de la ligne.
- **Mot de passe d'application Gmail** : à régénérer si le compte Google change de mot de passe.
- **Coût** : un appel Opus par offre scorée, trois par dossier (retouches CV, lettre, relance éventuelle).
  Le cache de prompt réduit la part fixe. Pas de chiffre en euros ici : le relever sur la console
  Anthropic après une semaine.
- **GitHub Actions** : les crons peuvent partir avec quelques minutes de retard ; LibreOffice s'installe
  à chaque run (≈ 1 min).
- **Données personnelles dans le dépôt** : CV de base et dossiers générés (dans `out/`, ignoré par git ;
  les logs téléversés comme artefacts Actions ne contiennent pas les lettres). Le dépôt est privé.
- **Pas d'interface d'édition de la lettre** : le DOCX attaché s'édite dans Word ; le texte collé dans le
  formulaire vient de `Lettre texte`.

---

## 7. Vérification

- `python -m pytest` : 31 tests (parseur Gmail, scoring, retouches CV, lettre, PDF, suivi, CLI).
- `mp doctor` : secrets, LibreOffice, CV de base, règles, schéma Airtable, Gmail, Claude.
- `mp run --dry-run -v` : exécution complète sans écriture.
- Logs : `out/logs/mp_YYYY-MM-DD.log` (Mac) ou artefact `logs-<run>` (Actions).
- Digest : un email par run utile, objet `[Mission Pipeline] matin N dossier(s) prêt(s) · …`.
