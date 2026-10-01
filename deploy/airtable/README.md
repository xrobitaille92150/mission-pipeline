# Airtable — l'unique interface du pipeline

Base **Mission Pipeline** (`apphTpnW5vu0OdnfC`). Deux tables :

| Table | Id | Rôle |
|---|---|---|
| **Offres** (ex-« Veille 2 ») | `tblrCyL6huHkUPZbF` | Une ligne par offre LinkedIn vue dans Gmail. Scoring, dossier, décision. |
| **Candidatures** | `tblF3jpncEXA647ou` | Entonnoir de suivi, tous canaux (LinkedIn, cabinets, réseau). Alimentée par le pipeline et à la main. |

## 1. Créer les champs (une fois)

```bash
mp airtable-setup           # liste les champs manquants
mp airtable-setup --apply   # les crée (PAT avec le scope schema.bases:write)
```

**Fait le 1er octobre 2026** : les 26 champs d'Offres et les 2 de Candidatures ont été créés via le connecteur
Airtable ; `mp airtable-setup` doit répondre « 0 champ manquant ». La commande reste utile si un champ est
supprimé par erreur.

Les champs historiques sont réutilisés tels quels (`jobId`, `Employeur`, `Poste`, `Lieu`, `Mode`, `Score`,
`Easy Apply`, `URL`, `Date 1ère vue`, `Préparer dossier`, `Je postule`, `J'écarte`, `Recherche LI`).
Les champs ajoutés sont décrits dans `mp/airtable.py` (`OFFRES_FIELDS`, `CANDIDATURES_FIELDS`).

## 2. Les trois cases à cocher (tout le « clic » est là)

| Case | Effet au prochain run (ou dans la minute avec l'automation ci-dessous) |
|---|---|
| **Préparer dossier** | CV adapté + lettre générés, attachés à la ligne (`CV (fichiers)`, `Lettre (fichiers)`), texte de la lettre dans `Lettre texte`, `Statut` → *Dossier prêt*. Le pipeline la coche lui-même pour les meilleures offres du jour (`Rang du jour` 1 à 3). |
| **Je postule** | `Statut` → *Postulée*, `Date postulé`, `Réponse` → *Envoyé*, ligne créée dans Candidatures. Si le dossier n'existait pas encore, il est généré. |
| **J'écarte** | `Statut` → *Écartée*, plus jamais scorée ni proposée. |

Parcours minimal pour répondre à une offre : ouvrir la vue **À décider**, lire `Pourquoi` / `Red flags`,
télécharger le CV PDF attaché, copier `Lettre texte` dans le formulaire LinkedIn, cocher **Je postule**.
Trois clics, zéro saisie.

## 3. Vues conseillées (table Offres)

| Vue | Filtre | Tri / groupe |
|---|---|---|
| **À décider** | `Statut` ∈ {À étudier, Dossier prêt} et `J'écarte` décoché | tri `Rang du jour` ↑ puis `Score` ↓ |
| **Dossiers prêts** | `Dossier le` non vide et `Statut` ≠ Postulée | tri `Dossier le` ↓ |
| **Postulées** | `Statut` = Postulée | groupe `Réponse` |
| **Erreurs** | `Erreur` non vide | tri `Scoré le` ↓ |
| **Historique** | `Statut` ∈ {Écartée, Expirée} | tri `Dernière vue` ↓ |

Une interface Airtable (type *Record review*) sur la vue « À décider » donne la même chose sur mobile :
fiche à gauche, boutons à droite (les trois cases).

## 4. Automation « dossier dans la minute » (facultatif)

Le run planifié tourne deux fois par jour. Pour obtenir un dossier sans attendre :

1. Créer un jeton GitHub *fine-grained* limité au dépôt `mission-pipeline`, permission **Contents : Read and write**
   (c'est la permission requise par l'endpoint `repository_dispatch`).
2. Dans Airtable → Automations → **New automation** :
   - *Trigger* : When record matches conditions — table Offres — `Préparer dossier` is checked **ou** `Je postule`
     is checked, **et** `Dossier le` is empty.
   - *Action* : Run script — coller `automation_dossier.js`, déclarer les variables `recordId`, `employeur`,
     `poste` (depuis le trigger) et `token` (le jeton, en clair dans le champ de la variable).
3. Tester (bouton *Test*) : le run `mission-pipeline` apparaît dans l'onglet Actions du dépôt, le dossier est
   attaché à la ligne en deux à trois minutes.

Avec launchd sur le Mac au lieu de GitHub Actions, cette automation n'a pas d'équivalent : on attend le
run suivant ou on lance `mp dossiers` à la main.

## 5. Table Candidatures — ce que le pipeline y écrit

- **Création** quand `Je postule` est coché ou qu'un email LinkedIn « votre candidature a été envoyée » est lu.
- **Mise à jour** de `Réponse` (Envoyé → A/R → Oui / Non, jamais en arrière) et `Date réponse` depuis les
  emails LinkedIn (« vue par », « dernière nouvelle de ») et les emails de recruteurs classés par Claude.
- Clé de rapprochement : `Job ID` d'abord, puis société normalisée + mots du titre.
- Les lignes saisies à la main (cabinets, réseau) ne sont jamais écrasées : seuls `Réponse`, `Date réponse`
  et `Dernier email` bougent, et uniquement vers l'avant.
