# Scoring — Quick Start

Système de notation automatique des offres basé sur ton barème Excel.

---

## 1️⃣ Tester le prototype

```bash
cd ~/Claude/Artifacts/mission-pipeline

# Mode test (2 offres exemples)
python3 scripts/test_scoring.py

# Résultat attendu :
# - AXA Investment Accounting (Senior, SimCorp, IFRS, Remote) → 83/100 ✅ ACTIONNABLE
# - BNP Junior Analyst (Retail, présentiel, allemand) → 0/100 ❌ REJETÉ
```

---

## 2️⃣ Tester avec tes vraies offres

**Prérequis** : Les offres doivent avoir une **Description** remplie dans Airtable Veille (la JD complète du poste).

```bash
# Scorer les 5 premières offres Haute/Moyenne
python3 scripts/test_scoring.py live 5
```

---

## 3️⃣ Intégrer dans n8n (quand tu seras satisfait)

1. **Ajouter champ Score** dans Airtable Veille (type Number, 0-100)
2. **Ajouter nœud Code** dans le workflow `Veille — notes IA` (après Parser triage)
   - Copier le code de `nodes/veille-scoring.js`
   - Mode : `runOnceForEachItem`
3. **Mapper dans Airtable MàJ** : `Score` = `{{ $json.score }}`

---

## 4️⃣ Ajuster le barème

Tu veux modifier les points, ajouter un critère, ou changer les plafonds ?

1. **Édite le Excel** : `/Users/xavierrobitaille/Desktop/Claude/Projects/Candidatures/Scoring_Offres_XRO.xlsx`
2. **Sauvegarde**
3. **Relance le test** → le parser charge la nouvelle version automatiquement
4. **Aucun code à toucher** ✨

---

## 📊 Comprendre les résultats

**Score = somme des 7 blocs, plafonné [0, 100], seuil actionnable ≥ 50**

```
BLOC 1 : Cluster/domaine (0-40)
BLOC 2 : Outils & normes (bonus ≤ +20)
BLOC 3 : Séniorité (+20 / +5 / -100)
BLOC 4 : Mode travail (+10 / +7 / -5 à -100)
BLOC 5 : Géographie & langue (+10 → -100)
BLOC 6 : Structure (+10 → -10)
BLOC 7 : Red flags (malus -5 à -100)
```

Chaque bloc a une **justification détaillée** expliquant les points attribués.

---

## 🔴 Malus rédhibitoires (arrêt immédiat)

Ces critères → **score ≤ 0 (plafonné à 0)** :
- Junior explicite : -100
- Langue exclue (NL, DE, ES, PT) : -100
- Full on-site hors UE (UK, USA, etc.) : -100

---

## 📝 Exemple : Interpréter un score

**Offre** : « Senior PMO IFRS 17, Allianz, Paris, Hybride »

Score 65/100 :
```
BLOC 1 Cluster C (Transformation/IFRS17) : +40
BLOC 2 IFRS 17 + ERP : +7 (plafonné ≤20)
BLOC 3 Senior : +20
BLOC 4 Hybride : +7
BLOC 5 FR marché FR : +4
BLOC 6 Assureur (Allianz) : +10
BLOC 7 Aucun red flag : 0

Total : 40+7+20+7+4+10+0 = 88 (mais voir détail exact de ce qui s'additionne)
```

➡️ **65/100 = ACTIONNABLE** (≥ 50, proche du seuil mais acceptable)

---

## 🎯 Next Steps

- [ ] Test mode `live` avec 5-10 offres réelles
- [ ] Valide les scores (« ça me plaît ou pas ? »)
- [ ] Ajuste le barème si besoin (modifier Excel)
- [ ] Crée le champ Score dans Airtable
- [ ] Déploie le nœud dans n8n (Veille — notes IA, 6h30)
- [ ] Laisse tourner 1-2 semaines pour voir les patterns

---

## 📂 Fichiers clés

- `scripts/test_scoring.py` — Script de test
- `scripts/parse_scoring_bareme.py` — Parser barème
- `nodes/scoring-prompt.md` — Prompt Claude
- `nodes/veille-scoring.js` — Nœud n8n
- `~/Desktop/.../Scoring_Offres_XRO.xlsx` — Barème source
- `SCORING.md` — Doc complète

---

**Questions ?** Consulte `SCORING.md` pour la doc technique complète.
