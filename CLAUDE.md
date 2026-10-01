# Mission Pipeline v3 — instructions pour les sessions Claude

**Dépôt** : `xrobitaille92150/mission-pipeline` (privé). Clone Mac : `/Users/xavierrobitaille/Claude/Artifacts/mission-pipeline/`.
**Rôle** : moteur technique des candidatures de Xavier (alertes LinkedIn Gmail → scoring → CV + lettre → suivi), piloté
depuis Airtable. Le suivi humain et le contexte utilisateur vivent dans le dossier Cowork `Candidatures/`.

Lire d'abord : `README.md` (usage) puis `docs/REFONTE-2026-10.md` (pourquoi, décisions, migration).

---

## Carte du code

| Fichier | Rôle | Testé par |
|---|---|---|
| `mp/cli.py` | commandes `mp run / ingest / sync / score / dossiers / dossier / track / digest / airtable-setup / doctor` | `test_pipeline_and_tracking` |
| `mp/pipeline.py` | ingest (Gmail → Offres), score (+ classement du jour), dossiers, sync_decisions | `test_scoring`, `test_pipeline_and_tracking` |
| `mp/gmail.py` | IMAP, parseur des digests LinkedIn (`parse_job_cards`), emails de statut (`parse_linkedin_status`), label de traitement, envoi SMTP | `test_gmail_parser` |
| `mp/linkedin.py` | fiche de poste via `jobs-guest`, jamais d'exception (`JobDescription.ok`) | — (réseau) |
| `mp/airtable.py` | client REST (upsert, patch, pièces jointes via `content.airtable.com`, Meta API) et **schéma attendu** (`OFFRES_FIELDS`) | double `FakeAirtable` |
| `mp/claude.py` | SDK `anthropic` : sorties JSON (schema), cache de prompt, repli serveur, comptage d'usage | double `FakeClaude` |
| `mp/scoring.py` | filtres durs (langue, junior, géo), `score_offer`, `rank_for_dossiers` | `test_scoring` |
| `mp/cv.py` | choix du profil, retouches python-docx run par run, garde-fous | `test_cv_and_letter` |
| `mp/letter.py` | lettre (HARD FACTS, fourchettes de mots, clauses géo), DOCX à en-tête | `test_cv_and_letter` |
| `mp/pdf.py` | LibreOffice headless | `test_cv_and_letter` (sauté sans soffice) |
| `mp/dossier.py` | `build_dossier` : fiche → profil → CV → lettre → PDF → Drive | — (intégration) |
| `mp/tracking.py` | index Candidatures, événements de statut, entonnoir sans retour arrière | `test_pipeline_and_tracking` |
| `mp/digest.py` | email de fin de run (texte + HTML) | — |
| `mp/models.py` | dataclasses + schémas Pydantic (`Scoring`, `CvEditPlan`, `Letter`, `EmailClass`) | `test_scoring` |
| `mp/config.py` | `settings()`, lecture de `~/.config/mission-pipeline/*.env`, ids Airtable | — |
| `mp/prompts/*.md` | profil candidat, méthode et barème de scoring, retouches CV, lettre FR/EN (HARD FACTS verbatim), tri des emails | — |

Ids Airtable : base `apphTpnW5vu0OdnfC`, Offres `tblrCyL6huHkUPZbF` (ex-« Veille 2 »), Candidatures `tblF3jpncEXA647ou`.
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
   est un reliquat à purger.
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
- **Changer le parseur Gmail** : coller un digest réel (anonymisé) dans `tests/test_gmail_parser.py`, faire passer.
- **Ajouter un champ Airtable** : `mp/airtable.py::OFFRES_FIELDS`, puis `mp airtable-setup --apply`.
- **Changer la lettre** : `cover_common.md` (invariants), `cover_fr.md` / `cover_en.md` (structure), `letter.py::geo_clauses`
  (clauses déterministes). Vérifier les fourchettes de mots dans `test_cv_and_letter`.
- **Débugger un run** : `out/logs/mp_YYYY-MM-DD.log` (Mac) ou artefact `logs-<run>` sur GitHub ; chaque ligne Airtable
  en erreur porte le détail dans le champ `Erreur`.

## Déploiement

- GitHub Actions : `.github/workflows/pipeline.yml` (06:30 / 18:30 Paris, `workflow_dispatch`, `repository_dispatch` type
  `dossier` depuis l'automation Airtable `deploy/airtable/automation_dossier.js`).
- launchd : `deploy/launchd/install.sh` (même horaire, logs dans `out/logs/`).
- Un seul des deux à la fois.

## Skills liés

- `skills/postuler/SKILL.md` : en session Claude, « postule à cette offre » → `mp dossier --url …`.
- Skills du compte (`cv-tailoring`, `cover-letter`, `voix-xavier`) : leur contenu validé est repris dans `mp/prompts/` ;
  en cas d'évolution d'un skill, répercuter dans le prompt correspondant et committer.

## État au 1er octobre 2026

- v3 écrite et testée (31 tests), **pas encore exécutée en production** : secrets, schéma Airtable et
  ordonnanceur à mettre en place selon `docs/REFONTE-2026-10.md` § 5.
- Décisions en attente (Xavier) : ordonnanceur (Actions vs launchd), création du schéma, purge de `candidatures/`,
  décommissionnement n8n / VPS / agents launchd v2.
- Legacy : `legacy/README.md` décrit ce qui a été conservé et pourquoi.
