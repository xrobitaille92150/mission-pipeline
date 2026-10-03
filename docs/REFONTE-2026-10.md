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

- **Un moteur** : le package Python `mp/` (16 modules + cockpit, 78 tests). Rien d'autre ne s'exécute.
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
  │                                        silence 14 j → Expirée
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

**Cockpit mobile (ajouté le 1er octobre, demande de Xavier : « un minimum de clics, depuis l'iPhone »).**
`mp app` sert sur le Mac une page web minimaliste (`mp/web/index.html`, FastAPI derrière), exposée à l'iPhone
par Tailscale Serve et ajoutée à l'écran d'accueil. Les trois clics deviennent trois boutons : **Je postule**,
**J'écarte**, **Préparer le dossier**, appliqués tout de suite (le run suivant n'a plus rien à faire). La lettre
se relit sur le téléphone, se copie, et se fait réécrire par Claude sur une consigne libre (« plus court »,
« insiste sur IFRS 17 ») ; valider régénère PDF et DOCX dans Airtable. Un bouton lance un run complet.
Choix de Xavier : auto-hébergement derrière Tailscale (pas d'app native, pas d'exposition Internet, secrets
sur le Mac). Pas d'authentification applicative : c'est le réseau Tailscale qui limite l'accès.

### 2.4 Scoring : ce qui change

| Avant (v1) | Après (v3) |
|---|---|
| 7 blocs pondérés, 7 appels ou un prompt de 7 sections | filtres durs déterministes puis **un** appel Claude Opus avec sortie JSON validée (Pydantic) |
| scores concentrés entre 58 et 72 | Claude note cinq dimensions sur des échelles ancrées (adéquation 40, séniorité 15, géographie 20, format 15, signaux 10) ; le programme additionne, plafonne (junior, langue, hors Europe, hors axe, adéquation < 15) et tranche : ≥ 70 Postuler, 50-69 Étudier, < 50 Écarter ; plafond 69 sans fiche de poste. Le premier dry-run avec un score global demandé au modèle avait reproduit le tassement (58-62) |
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

### 2.7 Parité avec l'ancien système (audit du 2 octobre)

Avant d'arrêter l'ancien pipeline, Xavier a demandé une comparaison fonction par fonction avec le code qui tourne
sur le Mac (branche `main`, inchangée hors PDF : JOE, BOB, MATT, JACK, `apply_tool.py`). Les manques ont été comblés
le même jour ; les écarts restants sont voulus.

| Fonction | Ancien | v3 |
|---|---|---|
| Lecture des alertes | JOE via le pont n8n « Gmail Bridge » | IMAP direct (`mp ingest`) |
| Fiche, mode, Easy Apply | JOE | `linkedin.fetch_jd` |
| Notation | Haiku, « Note rôle » / « Note critères » | Opus, cinq sous-scores, Pourquoi, Red flags (écart voulu) |
| Choix des dossiers | toute offre ≥ 50 | top 3 ≥ 60 par passage + coches de Xavier (écart voulu) |
| « Je postule » automatique | coché sur Easy Apply ≥ 50 | jamais (écart voulu : décision de Xavier) |
| CV + lettre | MATT : 3-6 retouches, faits durs, clauses géo | idem, CV complet fourni à Claude, profil choisi sur mots entiers, règles d'écriture en entier |
| Copie dans le Drive | MATT, `10_Work/Candidatures/dossiers/<date>/` | même dossier par défaut sur le Mac ; rattrapage depuis Airtable par le cockpit (`mp drive-sync`) |
| Dossier dans l'heure | JACK, toutes les heures | immédiat depuis le cockpit |
| Offres écartées / anciennes | supprimées par JACK | gardées : Écartée, Expirée (écart voulu) |
| Doublons employeur + poste | supprimés par JACK | rangés en « Doublon » (`mp dedup`, étape du run) ; une offre déjà décidée couvre ses réapparitions |
| Suivi des réponses | BOB | `mp track`, même entonnoir |
| Société non identifiée | BOB → table A traiter | idem (table A traiter, upsert par ID Email) |
| Offre trouvée ailleurs | `apply_tool.py` (UI locale) | cockpit « ＋ Ajouter une offre » et `mp dossier` |
| Email de fin de run | n8n « send digest » | SMTP direct |

Deux interférences observées tant que les deux systèmes tournent ensemble : JACK supprime des lignes que la v3 garde,
et les « Je postule » cochés automatiquement par l'ancien sont appliqués par la v3 (Postulée + Candidatures) au
premier passage de `sync`. D'où l'arrêt de l'ancien dès la parité atteinte.

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

## 4. Décisions de Xavier (1er octobre 2026)

| Question | Décision | Conséquence |
|---|---|---|
| Ordonnanceur | **GitHub Actions** ; **remplacé le 2 octobre par launchd sur le Mac** (option « Mixte » : CV et lettres par Claude Code avec les skills du compte, sur l'abonnement ; scoring et tri des emails sur l'API) | quatre secrets à saisir dans le dépôt ; automation Airtable pour le dossier dans la minute ; launchd reste disponible en secours |
| Schéma Airtable | **création automatique** | champs créés le jour même via le connecteur Airtable (même liste que `mp airtable-setup --apply`) ; `mp doctor` doit afficher « schéma complet » |
| `candidatures/` | **dossier supprimé** | 130 Mo retirés du dépôt courant ; les PDF restent dans l'historique git (réécriture possible plus tard) |
| Ancien pipeline | **n8n et launchd v2 arrêtés maintenant, VPS après** ; précisé le 2 octobre : **manques comblés d'abord, pause ensuite** | `deploy/decommission.sh` (réversible) après l'audit de parité (§ 2.7) ; résiliation Hostinger après une semaine de v3 stable |
| Emplacement du clone Mac | **hors Drive** : `~/Claude/Artifacts/mission-pipeline` | exception à la règle d'organisation du 15/07 ; un `.git` synchronisé par Drive Mirror se corrompt |
| Actifs dans le dépôt | **CV de base et règles d'écriture versionnés** (choix technique, non soumis) | indispensable pour Actions ; `MP_CV_BASE_DIR` permet de pointer vers le Drive en launchd |

---

## 5. Migration : runbook

### 5.1 Sur le Mac (15 minutes)

Le clone vit **hors du Drive** (décision du 1er octobre : un `.git` sous Google Drive Mirror se corrompt, la
synchronisation verrouille et réécrit les milliers de petits fichiers de l'index). C'est l'exception explicite à la
règle d'organisation ; les dossiers produits peuvent être copiés vers le Drive via `MP_DRIVE_DOSSIERS_DIR`.

```bash
REPO=/Users/xavierrobitaille/Claude/Artifacts/mission-pipeline
gh auth status || gh auth login --git-protocol https --web   # une fois ; gh configure aussi git (HTTPS, pas de clé SSH)
gh auth setup-git
[ -d "$REPO/.git" ] || gh repo clone xrobitaille92150/mission-pipeline "$REPO"
cd "$REPO"
git fetch origin && git checkout claude/mission-pipeline-refonte-m50exw   # puis main après merge
python3.11 -m venv .venv                 # Python 3.11 minimum ; le python3 par défaut du Mac est 3.10
.venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"
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

**launchd sur le Mac** (décision du 2 octobre) : `zsh deploy/launchd/install.sh` (06:30 / 18:30, logs dans `out/logs/`).
Prérequis pour les dossiers par Claude Code : la commande `claude` à jour (`claude update` ; la 2.1.193 du Mac ne
synchronisait pas les skills du compte) et connectée au compte claude.ai **sans la clé API du shell**
(`env -u ANTHROPIC_API_KEY claude`, puis `/login`), puis une première synchronisation des skills
(`env -u ANTHROPIC_API_KEY CLAUDE_CODE_SYNC_SKILLS=1 claude -p ok --max-turns 1`). `mp doctor` affiche la version, vérifie
la connexion et la présence des skills `cv-tailoring`, `cover-letter`, `voix-xavier`, et donne ces commandes si besoin. Documentation : https://code.claude.com/docs/en/headless.md et https://code.claude.com/docs/en/skills.md.

**GitHub Actions** : lancement manuel seulement, en secours (Settings → Secrets : `ANTHROPIC_API_KEY`, `AIRTABLE_PAT`,
`GMAIL_USER`, `GMAIL_APP_PASSWORD`). L'automation Airtable (`deploy/airtable/`) n'est plus nécessaire : le cockpit
prépare un dossier à la demande.

**Cockpit iPhone** (indépendant de l'ordonnanceur, tourne sur le Mac) : `zsh deploy/launchd/install-app.sh`
puis `tailscale serve --bg --https=8443 8765` (le port 8443 laisse l'adresse sans port au cockpit LinkedIn) ; sur l'iPhone, Tailscale + Safari → Sur l'écran d'accueil. Pas à pas :
`deploy/tailscale/README.md`.

### 5.4 Décommissionnement

```bash
zsh deploy/decommission.sh --liste      # ce qui serait mis en pause, sans rien toucher
zsh deploy/decommission.sh              # pause : workflows n8n de l'ancien pipeline + agents launchd v2
zsh deploy/decommission.sh --restaurer  # annule la pause, à l'identique
```

Le script ne désactive que les workflows n8n de l'ancien pipeline (les deux « Gmail Bridge ») et les agents
launchd v2 (`missionrun` 06:15/18:15, `jack` toutes les heures, qui supprimait des lignes d'Offres, `applytool`,
qui occupait le port 8765 du cockpit). `--liste` montre ce qui serait arrêté sans rien toucher. Rien n'est
supprimé : les agents sont renommés en `.plist.disabled`, les workflows désactivés, et tout est noté dans
`~/.config/mission-pipeline/ancien-pipeline-arrete.txt` ; `--restaurer` remet exactement ces éléments en service.
Puis, après une semaine de v3 stable : résilier le VPS Hostinger, **seulement si** les autres workflows qu'il
héberge (huit automatisations hors pipeline au 2 octobre) ne servent plus.

### 5.5 Rattrapage des 392 offres jamais scorées

Après `mp sync`, celles vues il y a plus de 14 jours (`MP_EXPIRE_DAYS`, décision de Xavier du 1er octobre) passent en
*Expirée* et ne seront jamais scorées. Les
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
- **Édition de la lettre** : depuis le cockpit (consigne à Claude, puis validation) ou dans le DOCX attaché ;
  une retouche faite dans Word n'est pas reportée dans `Lettre texte`.
- **Cockpit** : dépend du Mac allumé et de Tailscale actif des deux côtés ; un seul utilisateur, pas de
  verrou : deux actions simultanées sur la même offre se suivent sans se contredire mais sans avertir.

---

## 7. Vérification

- `python -m pytest` : 78 tests (parseur Gmail, scoring, retouches CV, lettre, PDF, suivi, CLI, config, cockpit).
- `mp doctor` : secrets, LibreOffice, CV de base, règles, schéma Airtable, Gmail, Claude.
- `mp run --dry-run -v` : exécution complète sans écriture.
- Logs : `out/logs/mp_YYYY-MM-DD.log` (Mac) ou artefact `logs-<run>` (Actions).
- Digest : un email par run utile, objet `[Mission Pipeline] matin N dossier(s) prêt(s) · …`.
