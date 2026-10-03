# Notes pour Xavier : l'offre en bref et ses critères

Ces deux champs s'affichent dans le cockpit. Ils doivent permettre à Xavier de comprendre le poste et de décider
**sans ouvrir LinkedIn**. Rédige-les en français, même si l'annonce est en anglais, à partir de la seule fiche de
poste fournie : n'invente rien, ne déduis rien qui n'y figure pas.

## `resume` : l'offre en bref

3 à 5 phrases factuelles, dans cet ordre quand l'information existe :
- qui recrute (et le client final si c'est un cabinet, une ESN ou un placeur), secteur ;
- l'objet du poste ou de la mission, les missions clés, le périmètre (normes, outils, entités, livrables) ;
- le niveau attendu (intitulé, années d'expérience, management) ;
- le format : contrat, durée, date de démarrage, lieu et rythme (sur site / hybride / remote), rémunération ou TJM.

Phrases simples et précises, vocabulaire du métier (IFRS 17, ABOR, recette, PMO…), sans formule publicitaire ni
jugement : le jugement va dans `criteres`.

## `criteres` : en quoi l'offre correspond aux attentes de Xavier

Exactement sept entrées, dans cet ordre, une par critère. Les attentes de référence sont celles du profil
ci-dessus (sections « Ce qu'il cherche », « Clusters d'expertise », « Exclusions ») :

| `critere` | Ce qu'on compare |
|---|---|
| Domaine | le cœur du poste face aux clusters : B et C prioritaires, A secondaire, hors axe |
| Séniorité | le niveau attendu face à ses 25 ans d'expérience (junior, analyst ou grade nettement inférieur = écart) |
| Contrat | freelance, mission, intérim ou management de transition ; un CDI senior est envisageable |
| Rémunération | TJM ou salaire annoncé face à environ 1 000 €/jour ou 150 k€/an |
| Lieu et rythme | hybride en France d'abord (Paris / Île-de-France) ; autres pays européens retenus en second ; hors Europe exclu |
| Langue | français ou anglais uniquement |
| Secteur | secteurs cœur et exclusions du profil |

Pour chaque entrée :
- `constat` : une phrase courte (20 mots au plus) qui cite ce que dit l'annonce, puis la comparaison
  (ex. « CDI, grade Senior Consultant, 55 k€ de base : très en deçà de la cible »).
- `statut` : `ok` si l'offre correspond, `ecart` si elle s'en écarte, `inconnu` si l'annonce ne dit rien
  (constat : « non précisé dans l'annonce »). Un critère secondaire satisfait (cluster A, pays européen hors
  France, full remote, CDI senior) est `ok`, et le constat le précise.
