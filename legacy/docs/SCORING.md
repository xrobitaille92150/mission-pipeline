# Système de Scoring des Offres

Notation flexible et évolutive basée sur le barème Excel `Scoring_Offres_XRO.xlsx`.

---

## Architecture

3 composants créés (toujours référencés au fichier Excel, jamais en dur) :

### 1. `scripts/parse_scoring_bareme.py` — Parser barème

Lit le fichier Excel et extrait les 7 blocs de critères.

```bash
python3 scripts/parse_scoring_bareme.py
# Sortie : JSON structuré (blocs, critères, points, mots-clés)
```

**Input** : `~/Desktop/Claude/Projects/Candidatures/Scoring_Offres_XRO.xlsx`  
**Output** : JSON avec structure :
```json
{
  "titre": "...",
  "blocs": [
    {
      "numero": 1,
      "titre": "Fit domaine / Cluster",
      "criteres": [
        {"nom": "Cluster B", "points": 40, "mots_cles": "..."}
      ]
    }
  ]
}
```

### 2. `nodes/scoring-prompt.md` — Prompt Claude

Template pour évaluer une offre selon le barème.

**Input** : Barème (JSON) + Offre (titre, employeur, lieu, description JD)  
**Output** : Réponse JSON avec :
- `score_final` (0-100)
- `seuil_atteint` (booléen)
- `justification` (détail par bloc)
- `resume` (1 phrase)

### 3. `nodes/veille-scoring.js` — Nœud n8n (Code)

Orchestrateur : charge barème → assemble prompt → appelle Claude → parse réponse.

```javascript
// Input : items avec {poste, employeur, lieu, description, ...}
// Output : items enrichis avec {score, score_justification, score_resume}
```

---

## Intégration n8n (workflow Veille — notes IA)

### Ajouter le nœud dans n8n

1. Dans le workflow `Veille — notes IA` (id `zxIgXzu2d2S9d1vg`)
2. Après le nœud `Parser triage Veille` (celui qui retourne les items avec notes)
3. Ajouter nœud **Code** (Type: JavaScript)
4. Copier le contenu de `nodes/veille-scoring.js`
5. Connecter l'entrée du parser triage vers ce nœud
6. Paramètres :
   - Mode : `runOnceForEachItem`
   - Timeout : 30s (Claude peut être lent)

### Ajouter le champ Score dans Airtable

Table **Veille** (`tblrXH5Jiyg6w21lW`) :
- Nouveau champ : **Score** (type Number, 0-100)
- Field ID : à récupérer après création

### Mapper le score dans Airtable MàJ

Nœud `MàJ Veille` (après le nœud scoring) :
- `Score` = `{{ $json.score }}`
- `Justification Score` (Long Text optionnel) = `{{ JSON.stringify($json.score_justification) }}`

---

## Test dans Cowork (avant déploiement)

1. **Récupère une offre réelle** de Airtable Veille (avec JD complète)
2. **Teste le parser** :
   ```bash
   cd ~/Claude/Artifacts/mission-pipeline
   python3 scripts/parse_scoring_bareme.py | jq '.blocs[0]'
   ```
3. **Teste Claude manuellement** dans Cowork :
   - Copie le prompt depuis `nodes/scoring-prompt.md`
   - Remplace les placeholders (offre réelle + barème)
   - Appelle Claude Sonnet
   - Valide le format JSON retourné
4. **Ajuste le barème si nécessaire** :
   - Modifie `Scoring_Offres_XRO.xlsx`
   - Relance le test
   - Aucun code à touch

---

## Évolution du barème

**Le barème est maintenu UNIQUEMENT dans le Excel.**

Si tu veux changer les points, ajouter un critère, ou ajuster les plafonds :
1. Édite `Scoring_Offres_XRO.xlsx`
2. **C'est tout.** Aucun code à toucher.
3. Au prochain run du workflow (6h30), il lira la nouvelle version.

---

## Schéma du flux complet (6h30)

```
Veille — notes IA (workflow n8n)
  │
  ├─ Lister Veille (offres Haute/Moyenne sans note)
  │
  ├─ Fetch description JD (LinkedIn jobs-guest)
  │
  ├─ Claude Sonnet : 2 notes IA (rôle + critères)
  │
  ├─ Parser triage Veille
  │
  ├─ ★ Claude Sonnet : SCORE (nœud veille-scoring.js) [NOUVEAU]
  │   ├─ Parse barème Excel
  │   ├─ Analyse 7 blocs
  │   └─ Retourne Score (0-100) + justification
  │
  ├─ Chercher Veille (search par jobId)
  │
  ├─ Veille décider (create / update)
  │
  └─ MàJ Veille : écrire Score + Justification
```

---

## Points importants

- **JAMAIS coder les critères en dur** : le parser les lit toujours depuis Excel
- **Claude doit parser JSON** : la réponse doit être un objet JSON valide
- **Malus rédhibitoires** : certains critères (Junior, langue exclue, hors UE) → score ≤ 0 (plafonné à 0)
- **Conservateur** : en cas de doute, Xavier préfère un score bas (pas d'optimisme)
- **Thresholds** : le seuil actionnable est ≥ 50 (à confirmer ou ajuster dans Excel)

---

## Fichiers

- `scripts/parse_scoring_bareme.py` — Parser (à exécuter)
- `nodes/scoring-prompt.md` — Prompt Claude (template)
- `nodes/veille-scoring.js` — Nœud n8n (Code, runOnceForEachItem)
- `~/Desktop/.../Scoring_Offres_XRO.xlsx` — Source de vérité (Excel)
- Ce fichier — Documentation complète

---

## Prochaines étapes

1. ✅ Créer les 3 composants
2. ⏳ Tester dans Cowork (5-10 offres réelles)
3. ⏳ Ajuster barème si nécessaire (+ relancer tests)
4. ⏳ Ajouter champ Score dans Airtable Veille
5. ⏳ Déployer le nœud dans le workflow n8n
6. ⏳ Valider sur le premier run complet (6h30)
