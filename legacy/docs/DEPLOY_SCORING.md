# Déployer le Scoring dans n8n

Guide étape par étape pour intégrer le nœud de scoring dans le workflow `Veille — notes IA`.

---

## Étape 1 : Créer le champ Score dans Airtable

**Table** : Veille (`tblrXH5Jiyg6w21lW`)  
**Champ** : Score

1. Ouvre Airtable → Base Mission Pipeline → Table Veille
2. **Clique sur +** (ajouter un champ) à droite de la dernière colonne
3. **Nom** : `Score`
4. **Type** : `Number`
5. **Options** : Precision = 0 (pas de décimales)
6. **Valider**

→ Le champ Score est créé (note son Field ID pour plus tard si besoin)

---

## Étape 2 : Ajouter le nœud Code dans n8n

**Workflow** : Veille — notes IA (`zxIgXzu2d2S9d1vg`)

1. Ouvre n8n live → Workflows → `Veille — notes IA`
2. **Cherche le nœud** `Parser triage Veille` (celui qui retourne les items notés)
3. **Ajoute un nouveau nœud** après lui
4. **Type** : `Code` (JavaScript)
5. **Mode** : `runOnceForEachItem`
6. **Timeout** : 30s
7. **Code** : copie le contenu entier de [`nodes/veille-scoring.js`](nodes/veille-scoring.js)

---

## Étape 3 : Mapper le Score dans Airtable MàJ

**Nœud** : `MàJ Veille` (celui qui met à jour Airtable après la scoring)

1. Dans le nœud `MàJ Veille`, cherche les champs à mapper
2. **Ajoute le mapping** pour `Score` :
   - Field name : `Score`
   - Value : `{{ $json.score }}`
3. **(Optionnel)** Si tu veux aussi la justification détaillée :
   - Field name : `Score_Justification` (créer le champ en Long Text)
   - Value : `{{ JSON.stringify($json.score_justification) }}`

---

## Étape 4 : Tester le workflow

1. **Clique sur Execute** dans n8n (bouton Play)
2. **Observe le log** : 
   - Le nœud `Parser triage Veille` doit retourner les items
   - Le nœud `Scoring` doit ajouter `score`, `score_seuil_atteint`, `score_justification`
   - Le nœud `MàJ Veille` doit écrire les scores dans Airtable
3. **Vérifie dans Airtable** : la colonne Score doit avoir des valeurs (0-100)

---

## Étape 5 : Activer le workflow

1. **Toggle** `Active` en haut du workflow
2. Le workflow tournera à **6h30 chaque jour** (après le run quotidien et le fetch JD)

---

## 📋 Checklist de déploiement

- [ ] Champ `Score` créé dans Airtable Veille
- [ ] Nœud `Code` ajouté après `Parser triage Veille`
- [ ] Code copié depuis `nodes/veille-scoring.js`
- [ ] Mode `runOnceForEachItem` activé
- [ ] Timeout = 30s
- [ ] Mapping Score dans nœud `MàJ Veille`
- [ ] Test exécution OK (scores écrits dans Airtable)
- [ ] Workflow activé (toggle)

---

## 🚨 Troubleshooting

**Erreur : « ANTHROPIC_API_KEY not defined »**
- Le nœud Code essaie de lire `process.env.ANTHROPIC_API_KEY`
- Créer une Variable globale n8n : `ANTHROPIC_API_KEY` = (copier depuis `~/.config/mission-pipeline/anthropic.env`)

**Erreur : « Barème introuvable »**
- Le parser cherche `/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/Scoring_Offres_XRO.xlsx`
- Vérifier que le chemin est correct

**Le scoring ne tourne pas**
- Vérifier que `Veille — notes IA` est **Actif** (toggle en haut)
- Vérifier le log du workflow (Executions → voir la dernière exécution)

---

## 🎯 À partir de là

Chaque jour à **6h30** :
1. ✅ Run quotidien (6h00) — ingère mails, écrit dans Veille
2. ✅ Fetch JD (6h20) — récupère descriptions LinkedIn
3. ✅ Notes IA (6h25) — scores rôle + critères
4. **✅ Scoring global (6h30)** — score final 0-100 + justification

Les offres avec Score ≥ 50 sont **ACTIONNABLE**.

---

## Questions ?

Consulte :
- `SCORING_QUICKSTART.md` — comment utiliser
- `SCORING.md` — doc technique
- `nodes/veille-scoring.js` — code du nœud (si besoin de modifier)
