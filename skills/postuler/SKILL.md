---
name: postuler
description: Prépare le dossier de candidature (CV adapté + lettre) pour une offre LinkedIn à partir de son URL, de son jobId ou du texte collé, via le moteur Mission Pipeline v3 (`mp dossier`). À utiliser dès que Xavier colle une offre et dit « postule », « prépare le dossier », « CV et lettre pour ça », ou « même exercice avec cette offre ».
---

# /postuler — dossier de candidature en une commande

Ce skill ne rédige rien lui-même : il appelle `mp dossier`, qui applique les skills `cv-tailoring`,
`cover-letter` (HARD FACTS 1-10) et `voix-xavier` (registre cover text) de façon déterministe, attache les
fichiers dans Airtable et renvoie le texte à coller. Ne jamais produire un CV ou une lettre « à la main »
en parallèle : une seule source de vérité.

## Pré-requis

- Dépôt cloné : `/Users/xavierrobitaille/Claude/Artifacts/mission-pipeline` (variable `REPO` ci-dessous).
- `mp doctor` au vert (secrets dans `~/.config/mission-pipeline/*.env`, LibreOffice installé).

## Procédure

1. **Identifier l'offre.** Extraire l'URL LinkedIn (`https://www.linkedin.com/jobs/view/<jobId>/`) ou le jobId.
   Si Xavier a collé le texte de l'annonce, l'écrire dans un fichier temporaire du scratchpad.
2. **Lancer le moteur** (une seule commande, sans la réécrire) :
   ```bash
   cd "$REPO" && .venv/bin/mp dossier --url "<URL>"
   # LinkedIn bloqué (« fiche LinkedIn indisponible ») → repasser avec le texte collé :
   cd "$REPO" && .venv/bin/mp dossier --url "<URL>" --text /chemin/annonce.txt --title "<Poste>" --employer "<Société>"
   ```
3. **Restituer** à Xavier, dans cet ordre, sans reformuler la lettre :
   - la ligne `=== DOSSIER PRÊT ===` (profil CV, langue, nombre de retouches) ;
   - le lien de l'enregistrement Airtable (table Offres), où CV et lettre sont attachés en PDF + DOCX ;
   - le bloc `--- Texte de candidature ---` tel quel (c'est ce qu'il colle dans le formulaire) ;
   - le bloc `--- Objections à préparer ---` s'il existe.
4. **Quand Xavier dit « envoyé » / « postulé »** : cocher `Je postule` sur l'enregistrement (MCP Airtable,
   `update_records_for_table`, champ `Je postule` = true). Le prochain run crée la ligne Candidatures ;
   pour le faire tout de suite : `cd "$REPO" && .venv/bin/mp sync`.

## Ce que le skill ne fait pas

- Il ne modifie ni les prompts (`mp/prompts/*.md`) ni les CV de base (`assets/cv_base/`) : toute correction
  de fond passe par un commit dans le dépôt, pas par une retouche de session.
- Il ne retouche pas la lettre produite. Si Xavier demande un changement, le relancer avec l'indication
  (ex. `--title` plus précis) ou noter la demande comme évolution du prompt `cover_common.md`.
