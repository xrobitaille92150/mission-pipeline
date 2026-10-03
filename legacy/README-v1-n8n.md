# Mission Pipeline — Suivi de candidatures Gmail → Claude → Airtable

Automatisation n8n qui lit les emails Gmail liés à des candidatures, les classe avec
Claude, et tient à jour une base Airtable du suivi de candidatures.

- **Hébergement** : n8n auto-hébergé sur VPS Hostinger.
- **Workflows live** :
  - Backfill (manuel, semaine par semaine) — `Cc6Ngky4IS30Cj8h`
  - Run quotidien (cron `0 4 * * *`, **inactif** par défaut) — `BsXYMJdidg8tej9i`
- **Source de vérité** : l'instance n8n en ligne. Ce dépôt en est le **miroir versionné**
  (`backfill.workflow.json` / `daily.workflow.json` = export complet via API ;
  `nodes/*.js` = code lisible des nœuds Code).
- **État** : backfill en cours, traité **semaine par semaine** (déclencheur manuel).
  Daily à activer une fois le backfill terminé.

## Contexte projet (front-end / pilotage)

Ce dépôt est la **plomberie** : il alimente la base Airtable Mission Pipeline. Le **front
de pilotage** (dashboard, profil, sync LinkedIn, contexte stratégique) vit dans le projet
Cowork :

- **Projet Cowork « Candidatures »** : `/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/`
  - `CLAUDE.md` : instructions Claude (schéma Airtable, MCP, dashboard)
  - `PROJECT.md` : statut, KPIs
  - `artifacts/mission-pipeline.html` : dashboard live
- **Profil partagé** : `/Users/xavierrobitaille/Desktop/Claude/Projects/_shared/context.md`
  (parcours XRO, mission en cours, priorités, TJM cible, liste cabinets…)

**Contrat partagé entre les deux** : la base Airtable **Mission Pipeline**
(`apphTpnW5vu0OdnfC`). Toute mise à jour écrite par ce pipeline est lue par le dashboard
en temps réel.

## Édition à distance via l'API n8n

Plus de copier-coller : la clé API n8n est stockée dans `~/.config/mission-pipeline/n8n.env`
(jamais commitée, jamais collée dans un chat). Depuis une session Code :

```sh
set -a; source ~/.config/mission-pipeline/n8n.env; set +a
# GET un workflow
curl -sS -H "X-N8N-API-KEY: $N8N_API_KEY" "$N8N_URL/api/v1/workflows/Cc6Ngky4IS30Cj8h" | jq .
# PUT après patch (settings strict : ne garder que executionOrder)
curl -sS -X PUT -H "X-N8N-API-KEY: $N8N_API_KEY" -H "Content-Type: application/json" \
  --data @body.json "$N8N_URL/api/v1/workflows/Cc6Ngky4IS30Cj8h"
```

## Architecture (flux des nœuds)

```
Déclenchement manuel
  → Gmail (getAll, simple:false, q = fenêtre hebdo)
  → Filtrer & préparer (Code)            décode corps, règles LinkedIn, prompt Claude
  → Construire requêtes batch (Code)      1 requête Batch API pour tous les emails
  → Créer batch Claude (HTTP POST)        /v1/messages/batches
  → Statut batch (HTTP GET) → Batch terminé ? (IF)
        ├─ non → Attendre 30 s → (reboucle sur Statut batch)
        └─ oui → Récupérer résultats (HTTP GET results_url, format texte/JSONL)
  → Lister enregistrements (Airtable search, returnAll, tous champs)
  → Matcher & décider (Code)              rapproche email ↔ candidature, action: create/update/skip/review
  → A traiter ? (IF action=review)
        ├─ oui → Créer un enregistrement à checker (Airtable → table "A traiter")
        └─ non → Créer ? (IF action=create)
              ├─ oui → Créer enregistrement Airtable (Candidatures)
              └─ non → Mettre à jour ? (IF action=update)
                    ├─ oui → Mettre à jour Réponse (Candidatures)
                    └─ non → Ignoré (NoOp, action=skip)
```

Pourquoi la **Batch API** : le backfill traite beaucoup d'emails d'un coup (‑50 % de coût,
asynchrone). Pour une future version **temps réel** (1 email à la fois), repasser à un appel
`/v1/messages` synchrone.

## Taxonomie des statuts (champ « Réponse »)

Entonnoir, on n'avance que vers l'avant (rangs) :

`Néant (0) → Envoyé (1) → A/R (2) → Oui / Non (3)`

Une mise à jour n'a lieu que si le nouveau statut a un **rang strictement supérieur** à
l'existant (`rankOf` dans `matcher-decider.js`). Oui et Non sont terminaux et de même rang
(le premier arrivé gagne).

## Règles déterministes (priment sur Claude) — `filtrer-preparer.js`

Emails LinkedIn (domaine `linkedin.com`), détectées sur sujet + aperçu :

| Motif | Statut forcé | Société |
|---|---|---|
| « your application was sent to XXX » / « candidature envoyée à XXX » | **Envoyé** | XXX |
| « your application was viewed by XXX » / « candidature consultée par XXX » | **A/R** | XXX |
| « Dernière nouvelle de XXX » | **Non** | XXX |
| Expéditeur `jobalerts-noreply@linkedin.com` | *ignoré* (mis de côté, feature à venir) | — |

Exclusions côté prompt (`recrutement=false`) : authentification/code, bienvenue/inscription
plateforme, invitation conférence/webinaire, newsletters, notifs bancaires, etc.

Denylist sociétés (faux positifs) dans `matcher-decider.js` : `DENY = {'indigoneo'}`.

## Rapprochement (matching) — `matcher-decider.js`

1. Nom de société extrait par Claude (normalisé : minuscules, sans accents, suffixes
   `sas/sarl/recruitment/...` retirés).
2. **Reverse-match** : un nom de candidature connue présent dans le texte de l'email
   (sujet/corps/expéditeur), le plus spécifique d'abord (longueur ≥ 4).
3. Domaine de l'expéditeur (dernier recours).

Routage : **si société identifiée de façon fiable** (règle forcée OU nom extrait par Claude)
→ Candidatures (create/update). **Sinon** → table « A traiter » (review) avec lien Gmail.

## Schéma Airtable

Base **Mission Pipeline** `apphTpnW5vu0OdnfC`.

- **Candidatures** `tblF3jpncEXA647ou` : Société, Poste, Lieu, Mode, URL, Date parue,
  Date postulé, Date réponse, Réponse (single-select : Néant/Envoyé/A/R/Oui/Non), Note.
  - Champs réels **à plat** dans la sortie n8n (pas de wrapper `fields`), noms **accentués**
    (`Société`, `Réponse`…). `getField` gère casse/accents.
  - Dates : `Date postulé` si statut Envoyé, sinon `Date réponse` (mapping ternaire `undefined`).
  - URL = lien Gmail **à la création uniquement** (pas à l'update).
- **A traiter** `tblSeyppFhxU3i8Ev` : Société, Poste, Réponse, Sujet, Expéditeur, Date,
  Note, Lien Gmail.

Credentials n8n : Gmail OAuth2 `wN9kkTh8npM257wD`, Anthropic (httpHeaderAuth `x-api-key`)
`tb4jH2LrqUFwnjrM`, Airtable PAT `bQLoL1mZbqEFDoKw`. Modèle : `claude-haiku-4-5-20251001`.

## Lancer / tester

Backfill, une fenêtre par run — changer le `q` du nœud Gmail :
```
after:2026/05/01 before:2026/05/08   (puis 05/08→05/15, 05/15→05/22, 05/22→05/29,
                                       05/29→06/05, 06/05→06/13)
```
Lancer manuellement, attendre la fin du batch (polling). Vérifier le ratio create/update/skip
et la table « A traiter ».

## Itérer (1 feature = 1 commit)

Boucle : décrire la règle → modifier le(s) nœud(s) (`nodes/*.js`) → tester sur une semaine →
déployer dans n8n → réexporter `candidatures.workflow.json` → commit.

Prochaine amélioration recommandée : **brancher le connecteur n8n (MCP)** sur l'instance
Hostinger (URL + clé API n8n) pour éditer/déployer directement, sans copier-coller.

## Pipeline dossiers (run_dossiers.py)

Script Python lancé par launchd à 6h, 12h, 19h. Lit les offres Veille avec
`Préparer dossier=true` + CV vide + J'écarte=false → génère CV adapté + Cover Letter → PDF → GitHub → Airtable PATCH.

```
scripts/run_dossiers.py      ← script principal
scripts/run-dossiers.sh      ← lanceur (chemin absolu python3 + pandoc)
logs/dossiers_*.log          ← un log par run
```

Profils CV (`select_cv` decision tree, fichiers dans `~/Desktop/.../CV de base/`) :
- `FinanceTransformation` (défaut) — `IFRS17SolvencyII` — `AssetManagement`

## Problèmes connus / TODO

- [ ] **BUG n8n** : nœud « Créer un enregistrement à checker » — `Poste` est mappé sur
  `{{ $json.societe }}` au lieu de `{{ $json.poste }}`.
- [ ] **« A traiter » pas en upsert** : opération `create` sans clé `ID Email` → doublons
  cross-run possibles. À passer en « Create or Update » matché sur `ID Email`.
- [ ] Matching dur résiduel → escalade possible vers matching délégué à Claude.
- [ ] Feature : version **temps réel** (Gmail Trigger + appel Claude synchrone).
