# Feature — Veille / Opportunités (alertes LinkedIn `jobalerts-noreply`)

> **Statut : v1 DÉPLOYÉE sur l'instance n8n (2026-06-16), active.**
> Décisions tranchées : branchement = redirection depuis « Filtrer & préparer »
> (Switch `kind`) ; table Airtable créée d'abord ; profil de triage condensé depuis
> `context.md`. 1ʳᵉ exécution = run planifié de 4h (ou « Execute workflow » manuel pour tester).
> Reste 4 décisions pour v2/v3 (notes auto/demande, skills CV/CL, promotion auto/manuelle, volume).

## Architecture déployée (branche Veille du workflow daily)

`Filtrer & préparer` (parse digest, tag `kind`) → **`Veille ?`** (IF sur `kind`) :
- `kind=candidature` → `Construire requêtes batch` … (flux existant inchangé) ;
- `kind=veille` → `Prépa triage Veille` (prompt) → `Triage Veille (Claude)` (HTTP haiku,
  `onError:continueRegularOutput`) → `Parser triage Veille` → `Chercher Veille` (search
  Airtable par `jobId`, `alwaysOutputData`) → `Veille décider` (create/update) →
  **`Veille créer ?`** (IF) → `Créer Veille` (Statut=À étudier, Date 1ère vue) / `MàJ Veille`
  (rafraîchit descriptif + Pertinence/Raison **uniquement** ; ne touche jamais Statut, Date,
  cases → triage manuel préservé sur les ré-apparitions de l'offre).

Workflow daily n8n : id `BsXYMJdidg8tej9i`. Mirrors repo : `nodes/veille-prepa-triage.js`,
`nodes/veille-parser-triage.js`, `nodes/veille-decider.js`, `nodes/filtrer-preparer.js`.
`daily.workflow.json` resynchronisé au live (27 nœuds).

**⚠️ Edge connu (pré-existant, aggravé)** : `Construire requêtes batch` lève une erreur si
0 email Candidature. Un jour avec **uniquement** des alertes jobalerts → la branche
Candidature erre (mais la branche Veille écrit quand même). À durcir si gênant.

## Avancement v1

- ✅ **Table Airtable « Veille »** créée — tableId `tblrXH5Jiyg6w21lW` (base `apphTpnW5vu0OdnfC`).
  Champ primaire/upsert `jobId` (`fldw7NH5gGRREOC4m`). Selects : Pertinence
  (Haute/Moyenne/Hors-cible), Statut (À étudier/Dossier prêt/Postulé/Écarté).
  Colonnes Note rôle/critères + CV + Cover letter posées (vides jusqu'à v2/v3).
- ✅ **Profil cible de triage** condensé → `nodes/veille-profil-cible.md` (réf. embarquée
  dans le prompt du nœud de triage Claude).
- ✅ **Parseur de digest** intégré à `nodes/filtrer-preparer.js` (remplace le `continue` :
  parse cartes → push items `kind:'veille'` ; items Candidatures tagués `kind:'candidature'`).
  **Validé sur 2 vrais mails** (2026-06-16) : 6 cartes chacun, footer/badges/lignes parasites
  exclus, jobId/Poste/Employeur/Lieu propres. Confirmé : **pas de description dans l'email**
  (préheader = teaser du 1ᵉʳ poste seulement) → la contrainte fiche tient, v2 = fetch requis.
  Structure réelle du digest : blocs séparés par lignes de tirets, `Poste / Employeur / Lieu /
  [badge] / "Voir l'offre : URL(/jobs/view/JOBID/)"`, en-tête « Votre alerte Emploi pour <nom> ».
- ✅ **Switch « Veille ? »** + **sous-chaîne Veille** (triage Claude + create/update Airtable)
  câblés dans `daily.workflow.json` et **poussés sur l'instance n8n** (HTTP 200, actif).
  Credentials Anthropic + Airtable rattachés et vérifiés côté live.
- ✅ **VALIDÉE en run réel (exec 132, 2026-06-16)** : 23 offres écrites dans Veille avec
  Pertinence (8 Haute / 3 Moyenne / 12 Hors-cible), Raison, Statut=À étudier, Date 1ère vue=date
  du mail. Tri pertinent (SimCorp, BNP Fund Accountant, ARCH Insurance PMO, Regulatory Reporting
  → Haute ; PM généralistes/e-commerce → Hors-cible).
  - 2 correctifs en cours de route : (a) **triage en 1 seul appel IA** pour TOUTES les offres
    (avant : 1 appel/carte → 23 appels → **429 rate-limit**, 13/23 vides) ; (b) **« Veille décider »
    en `.all()`** (avant : `$('...').item` après le nœud search → erreur n8n « Multiple matches »).
    Aussi : rubrique de tri assouplie (l'absence de mention « remote » ne pénalise plus),
    `retryOnFail` ajouté sur l'appel IA et le search Airtable.
  - Reste à voir au 2ᵉ run : la branche **MàJ** (offres ré-apparues → update sans doublon ni
    écrasement du Statut). Logique validée, pas encore exercée (table vide au 1ᵉʳ run).

## Vision (mots de Xavier)

Les recherches LinkedIn paramétrées génèrent des propositions reçues via
`jobalerts-noreply@linkedin.com` (actuellement **mises de côté** dans le nœud
« Filtrer & préparer » du pipeline principal). Objectif :
1. Trier pour écarter les offres hors cible (profil/expérience — projet « CV »).
2. Pour celles dans la cible : infos (employeur, lieu, modalités, URL) + note IA rôle +
   note IA critères recruteur + **CV sur-mesure** (skill existant) + **cover letter**
   (skill existant).
3. Case « Je postule » : si oui → copie dans **Candidatures** (suivi réponse par le
   pipeline existant) ; si non → la ligne reste (anti-doublon).
4. Dédup automatique des offres déjà reçues les jours précédents.

## Contrainte centrale

**L'email d'alerte ne contient PAS la description du poste** (juste titre, employeur,
lieu, parfois modalité, URL). Tout ce qui dépend du texte de l'annonce (note rôle, note
critères, CV/CL sur-mesure) nécessite de **récupérer la description** sur la page
LinkedIn → couche fragile / zone grise. Le socle (parse + dédup + triage grossier) est,
lui, 100 % fiable.

## Architecture (2 environnements, 4 composants)

1. **n8n (quotidien, auto)** — Gmail jobalerts → parse cartes (employeur/poste/lieu/URL/
   **jobId**) → dédup par jobId (upsert) → triage Claude (cible ? via titre+société+lieu vs
   profil) → [on-target] fetch description + 2 notes → table Airtable « Veille ».
2. **Airtable « Veille / Opportunités »** — surface de revue + cases.
3. **Claude Code/Cowork (à la demande)** — CV + cover letter via les skills (qui tournent
   dans Claude, pas n8n) pour les lignes sélectionnées → fichiers + liens dans Veille.
4. **n8n ou Claude** — case « Je postule » → promotion vers **Candidatures** (statut
   Envoyé) → suivi réponse par le pipeline email existant. **Referme la boucle** sur le
   système déjà construit.

## Faisabilité

**✅ Possible (robuste)** : parse digests en cartes ; dédup par jobId (résout les doublons
inter-jours) ; triage de pertinence ; dédup croisée avec Candidatures ; case « Je postule »
→ promotion ; non-postulé reste.

**⚠️ Compliqué mais faisable** : fetch description via endpoint public `jobs-guest`
(`https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<jobId>`) — fragile, zone grise
ToS, rate-limit IP VPS, couverture partielle ; notes IA rôle+critères (OK si description) ;
CV+CL sur-mesure (nécessite description + skills Claude → étape à la demande, pas sur tout).

**❌ Impossible/déconseillé** : scraping fiable des pages LinkedIn **authentifiées** (anti-bot,
ToS, risque blocage compte) ; description garantie 100 % ; skills CV/CL nativement dans n8n ;
jugement « dans la cible » fin sans la description.

## Table « Veille » envisagée

`jobId` (clé upsert) · Employeur · Poste · Lieu · Modalités · URL · Pertinence
(Haute/Moyenne/Hors-cible) · Raison triage · Note rôle · Note critères · Statut
(À étudier / Dossier prêt / Postulé / Écarté) · CV (lien) · Cover letter (lien) ·
Date 1ʳᵉ vue · ☐ Préparer dossier · ☐ Je postule

## 6 décisions en attente

1. Accepter le fetch description via `jobs-guest` (zone grise ToS) ? Sinon plan B = JD collé
   manuellement.
2. Notes rôle/critères en auto le matin, ou à la demande avec le dossier ?
3. Confirmer que les skills CV/CL tournent en Code/Cowork (pas API pure) ; où ranger les
   fichiers (un sous-dossier par offre ?).
4. OK pour condenser `context.md` en « profil cible » de triage (domaines, remote/hybride,
   séniorité, TJM, géo) ?
5. Volume estimé d'alertes/jour ?
6. Promotion vers Candidatures auto (case cochée) ou manuelle ?

## Phasage recommandé

- **v1 (sûr)** : ingestion + dédup + triage + table Veille. Sans description ni dossier.
- **v2** : + fetch description + 2 notes (si décision 1 = oui).
- **v3** : + CV/CL à la demande (skills) + promotion vers Candidatures.

## Pointeurs

- Profil complet : `/Users/xavierrobitaille/Desktop/Claude/Projects/_shared/context.md`
- Projet CV : projet Cowork « CV_Profiles » / dossier `Inputs/00 - Profil Xavier/CV XRO/`
- Pipeline principal : ce dépôt (`backfill.workflow.json`, `daily.workflow.json`, `nodes/`)
- Édition n8n à distance : `~/.config/mission-pipeline/n8n.env` + API n8n
- Le nœud « Filtrer & préparer » fait déjà `if (... === 'jobalerts-noreply@linkedin.com') continue;`
  → c'est le point de branchement (rediriger au lieu d'ignorer, OU workflow séparé dédié).
