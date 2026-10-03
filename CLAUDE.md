# Mission Pipeline v3 — instructions pour les sessions Claude

**Dépôt** : `xrobitaille92150/mission-pipeline` (privé). Clone Mac : `/Users/xavierrobitaille/Claude/Artifacts/mission-pipeline/`, **hors Google Drive** (exception voulue par
Xavier le 1er octobre 2026 : un `.git` sous Drive Mirror se corrompt). Ne jamais cloner ni déplacer le dépôt sous `~/Mon Drive/`.
**Rôle** : moteur technique des candidatures de Xavier (alertes LinkedIn Gmail → scoring → CV + lettre → suivi), piloté
depuis Airtable. Le suivi humain et le contexte utilisateur vivent dans le dossier Cowork `Candidatures/`.

Lire d'abord : `README.md` (usage) puis `docs/REFONTE-2026-10.md` (pourquoi, décisions, migration).

---

## Carte du code

| Fichier | Rôle | Testé par |
|---|---|---|
| `mp/cli.py` | commandes `mp run / ingest / dedup / sync / score / notes / dossiers / dossier / track / digest / drive-sync / airtable-setup / doctor / app` | `test_pipeline_and_tracking` |
| `mp/pipeline.py` | ingest (Gmail → Offres), `dedupe` (Doublon, jamais de suppression), score / `score_one` (+ classement du jour), `refresh_notes` (résumé + critères, parution et mode de candidature des offres actives), dossiers / `make_dossier`, `add_offer` / `process_new_offer` (offre ajoutée à la main), sync_decisions | `test_scoring`, `test_pipeline_and_tracking` |
| `mp/gmail.py` | IMAP, parseur des digests LinkedIn (`parse_job_cards`), emails de statut (`parse_linkedin_status`), label de traitement, envoi SMTP | `test_gmail_parser` |
| `mp/linkedin.py` | fiche de poste via `jobs-guest`, jamais d'exception (`JobDescription.ok`) ; date de parution (« il y a N jours ») et mode de candidature (bouton `apply-link-onsite` = simplifiée, `offsite` = site employeur) | `test_linkedin` (marqueurs relevés le 3 octobre) |
| `mp/airtable.py` | client REST (upsert, patch, pièces jointes via `content.airtable.com`, Meta API) et **schéma attendu** (`OFFRES_FIELDS`) | double `FakeAirtable` |
| `mp/claude.py` | SDK `anthropic` : sorties JSON (schema), cache de prompt, repli serveur, comptage d'usage | double `FakeClaude` |
| `mp/scoring.py` | filtres durs (langue, junior, géo), `score_offer` (score + notes), `notes_offer` (notes seules), `rank_for_dossiers` | `test_scoring` |
| `mp/cv.py` | choix du profil, retouches python-docx run par run, garde-fous | `test_cv_and_letter` |
| `mp/claude_code.py` | `claude -p` en mode headless : dossier (retouches CV + lettre) et réécriture rédigés avec les skills du compte (`cv-tailoring`, `cover-letter`, `voix-xavier`) sur l'abonnement ; repli API | `test_claude_code` |
| `mp/letter.py` | lettre (HARD FACTS, fourchettes de mots, clauses géo), `rewrite_letter` sur consigne, DOCX à en-tête | `test_cv_and_letter`, `test_app` |
| `mp/pdf.py` | LibreOffice headless | `test_cv_and_letter` (sauté sans soffice) |
| `mp/dossier.py` | `build_dossier` : fiche → profil → CV → lettre → PDF → Drive | — (intégration) |
| `mp/tracking.py` | index Candidatures, événements de statut, entonnoir sans retour arrière, revue manuelle (table A traiter) si la société n'est pas identifiée | `test_pipeline_and_tracking` |
| `mp/digest.py` | email de fin de run (texte + HTML) | — |
| `mp/drive.py` | copie des dossiers vers le Drive du Mac depuis les pièces jointes Airtable (lancée par le cockpit toutes les 30 min) | `test_drive` |
| `mp/app.py` + `mp/web/` | cockpit mobile (FastAPI + page vanilla JS) : onglets, décisions immédiates, consigne → réécriture de la lettre, ajout d'offre (lien ou texte), run à distance | `test_app` |
| `mp/models.py` | dataclasses + schémas Pydantic (`Scoring`, `CvEditPlan`, `Letter`, `EmailClass`) | `test_scoring` |
| `mp/config.py` | `settings()`, lecture de `~/.config/mission-pipeline/*.env`, ids Airtable | — |
| `mp/prompts/*.md` | profil candidat, méthode et barème de scoring, notes du cockpit (`notes.md` : l'offre en bref + 7 critères), retouches CV, lettre FR/EN (HARD FACTS verbatim), tri des emails | — |

Ids Airtable : base `apphTpnW5vu0OdnfC`, Offres `tblrCyL6huHkUPZbF` (ex-« Veille 2 »), Candidatures `tblF3jpncEXA647ou`,
A traiter `tblSeyppFhxU3i8Ev` (réponses à revoir à la main).
Les champs sont adressés par **nom** (`typecast=True`), jamais par id de champ.

## Règles de travail

1. **Une seule implémentation.** Toute règle vit dans `mp/` et nulle part ailleurs. Ne jamais réintroduire un nœud
   n8n, un script dans `scripts/` ou un agent dans `service/` : `legacy/` est mort.
2. **1 feature = 1 commit**, avec ses tests. `python -m pytest` doit rester vert avant tout push.
3. **Les prompts sont du code.** Modifier `mp/prompts/*.md` dans le commit, pas en session. Les HARD FACTS 1-10 de
   `cover_common.md` (titre d'actuaire interdit, CNP, Coface, Clearwater, SCOR, Primexis, management, IA, statut,
   doctrine des écarts) sont validés par Xavier : les reprendre **verbatim**, jamais les reformuler.
4. **Jamais de secret dans git.** `ANTHROPIC_API_KEY`, `AIRTABLE_PAT`, `GMAIL_APP_PASSWORD` : fichiers
   `~/.config/mission-pipeline/*.env` ou secrets GitHub. Vérifier avec `git diff --cached` avant de committer.
5. **Pas de PDF dans git.** Les dossiers vont dans Airtable (pièces jointes) et dans `out/` (ignoré). `candidatures/`
   a été supprimé le 1er octobre 2026 (les anciens PDF restent dans l'historique git).
6. **Idempotence.** Tout ce qui écrit doit pouvoir être rejoué : upsert sur `jobId`, label Gmail, formules Airtable
   filtrant sur `Scoré le` / `Dossier le`.
7. **Le profil cible** (`mp/prompts/profile.md`) suit `00_Knowledge/context.md` (décision du 6 septembre 2026 :
   mission hybride en France, ≈ 1 000 €/j, clusters B/C prioritaires). Si Xavier change de cap, c'est ce fichier
   qui change, puis le barème.

## Itérer

```bash
.venv/bin/python -m pytest -q                   # avant et après
.venv/bin/mp doctor --offline                   # fichiers et binaires
.venv/bin/mp run --dry-run -v                   # répétition complète sans écriture (Gmail et Airtable en lecture)
.venv/bin/mp score --limit 5 --dry-run -v       # calibrer un prompt sur 5 offres réelles
.venv/bin/mp dossier --url <URL> --dry-run      # un dossier dans out/ sans toucher Airtable
```

- **Changer une règle de scoring** : `mp/prompts/scoring.md` ou `bareme.md` (jugement) ; `mp/scoring.py::hard_filter`
  (déterministe). Ajouter un cas dans `tests/test_scoring.py`.
- **Changer le résumé ou les critères du cockpit** : `mp/prompts/notes.md` (texte), `mp/models.py::NOTE_CRITERES`
  (liste et ordre des critères) ; `mp notes` régénère les offres actives dont les notes ne sont pas au format v3 et relit leur page
  LinkedIn quand `Publiée le` manque. Les tests coupent LinkedIn (`conftest._no_linkedin`) : injecter une page.
- **Changer le parseur Gmail** : coller un digest réel (anonymisé) dans `tests/test_gmail_parser.py`, faire passer.
- **Ajouter un champ Airtable** : `mp/airtable.py::OFFRES_FIELDS`, puis `mp airtable-setup --apply`.
- **Changer la lettre** : `cover_common.md` (invariants), `cover_fr.md` / `cover_en.md` (structure), `letter.py::geo_clauses`
  (clauses déterministes). Vérifier les fourchettes de mots dans `test_cv_and_letter`.
- **Débugger un run** : `out/logs/mp_YYYY-MM-DD.log` (Mac) ou artefact `logs-<run>` sur GitHub ; chaque ligne Airtable
  en erreur porte le détail dans le champ `Erreur`.
- **Changer le cockpit** : API dans `mp/app.py` (toute écriture passe par `pipeline` / `letter`, jamais par un
  appel Airtable ad hoc), page dans `mp/web/index.html` (sans framework, sans build). Tester avec
  `TestClient` et `FakeAirtable(..., formulas=True)` (évaluateur de formules dans `tests/conftest.py`).
  Le cockpit n'a pas d'authentification propre : il n'écoute que sur 127.0.0.1 et c'est Tailscale qui
  restreint l'accès. Ne jamais le lier à `0.0.0.0`.

## Déploiement

- **launchd sur le Mac** (décision du 2 octobre, option « Mixte ») : `deploy/launchd/install.sh` (06:30 / 18:30, logs
  dans `out/logs/`). CV et lettres par Claude Code (`claude -p`, abonnement, skills du compte synchronisées dans
  `~/.claude/skills/synced/`), scoring et tri des emails par l'API. `mp doctor` vérifie `claude`, la connexion claude.ai
  et les trois skills. Les tests forcent `MP_DOSSIER_ENGINE=api` : ne jamais appeler un vrai `claude` dans un test.
- GitHub Actions : `.github/workflows/pipeline.yml`, lancement manuel seulement (secours), dossiers par l'API.
  L'automation Airtable `deploy/airtable/automation_dossier.js` (repository_dispatch) n'est plus utilisée : le
  cockpit prépare les dossiers à la demande.
- Cockpit : `deploy/launchd/install-app.sh` (service `com.xrobitaille.mp-app`, KeepAlive) + `tailscale serve --bg --https=8443 8765`
  (`deploy/tailscale/README.md`). **Toujours `--https=8443`** : l'adresse sans port (HTTPS 443) du Mac mini
  appartient au cockpit LinkedIn (`com.xavieradvisory.cockpit`, port 8766) ; ne jamais lancer `tailscale serve reset`. Le cockpit tourne sur le Mac quel que soit l'ordonnanceur choisi.

## Parité avec l'ancien système (audit du 2 octobre 2026)

Avant l'arrêt de l'ancien pipeline, Xavier a demandé que la v3 reprenne ses fonctions. Repris : copie des dossiers
dans le Drive, doublons employeur + poste (rangés en Doublon au lieu d'être supprimés), file A traiter, ajout d'une
offre trouvée ailleurs (cockpit), personnalisation CV + lettre (profil choisi sur mots entiers, règles d'écriture
transmises en entier). Écarts voulus : pas de « Je postule » coché automatiquement, pas de dossier pour toute offre
≥ 50, aucune suppression de ligne, plus de PDF dans GitHub. Détail : `docs/REFONTE-2026-10.md` § 2.7.

## Skills liés

- `skills/postuler/SKILL.md` : en session Claude, « postule à cette offre » → `mp dossier --url …`.
- Skills du compte (`cv-tailoring`, `cover-letter`, `voix-xavier`) : sur le Mac, Claude Code les charge directement
  (`mp/claude_code.py`) : une évolution d'un skill s'applique aux dossiers sans toucher au dépôt. `mp/prompts/`
  (cv_edits, cover_*) ne sert plus qu'au repli API : y répercuter les changements importants des skills.

## État au 1er octobre 2026

- v3 écrite et testée (113 tests), cockpit mobile inclus, **pas encore exécutée en production** : secrets, schéma Airtable et
  ordonnanceur à mettre en place selon `docs/REFONTE-2026-10.md` § 5.
- Décisions de Xavier (1er octobre) : ordonnanceur GitHub Actions ; schéma créé automatiquement ; `candidatures/`
  supprimé ; n8n et agents launchd v2 arrêtés tout de suite (`deploy/decommission.sh`), VPS résilié après une
  semaine de v3 stable.
- Legacy : `legacy/README.md` décrit ce qui a été conservé et pourquoi.
