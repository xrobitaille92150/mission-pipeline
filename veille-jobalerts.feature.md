# Feature — Veille / Opportunités (alertes LinkedIn `jobalerts-noreply`)

> **Statut : conçu, non démarré.** Proposition validée comme cadre ; 6 décisions en
> attente (voir bas). Sous-projet isolé dans une conversation dédiée.

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
