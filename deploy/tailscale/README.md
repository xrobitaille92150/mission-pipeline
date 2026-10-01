# Cockpit sur l'iPhone via Tailscale

Le cockpit (`mp app`) tourne sur le Mac, écoute uniquement en local (`127.0.0.1:8765`) et garde les secrets
(Airtable, Claude, Gmail) sur le Mac. Tailscale crée un réseau privé entre le Mac et l'iPhone ; **Tailscale
Serve** publie le cockpit en HTTPS sur ce réseau, et nulle part ailleurs. Aucun port ouvert sur Internet,
aucun mot de passe à gérer : seul un appareil connecté à ton réseau Tailscale peut atteindre la page.

## 1. Sur le Mac (une fois, 10 minutes)

1. Installer Tailscale : App Store → « Tailscale », ouvrir, se connecter (compte Google ou Apple).
   Dans le menu Tailscale (barre de menus) vérifier que le Mac est **Connected**.
2. Installer le cockpit comme service permanent, dans Terminal :

   ```bash
   cd /Users/xavierrobitaille/Claude/Artifacts/mission-pipeline
   git pull
   .venv/bin/pip install -e ".[dev]"
   zsh deploy/launchd/install-app.sh
   ```

   Attendu : `cockpit démarré : http://127.0.0.1:8765`.
3. Publier le cockpit sur le réseau Tailscale (HTTPS automatique), **sur le port 8443** :

   ```bash
   tailscale serve --bg --https=8443 8765
   tailscale serve status
   ```

   La deuxième commande affiche l'adresse, de la forme `https://<nom-du-mac>.<ton-tailnet>.ts.net:8443`
   (sur le Mac mini : `https://mac-mini-de-xavier.tail5f18a8.ts.net:8443`). C'est l'adresse à ouvrir sur
   l'iPhone. Si `tailscale` est introuvable dans Terminal, utiliser le chemin complet
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale` à la place du mot `tailscale`.
   Si Tailscale répond que HTTPS doit être activé, l'activer dans la console
   <https://login.tailscale.com/admin/dns> (« Enable HTTPS »), puis relancer la commande.

> **Pourquoi 8443 et pas l'adresse sans port.** Un Mac n'a qu'une adresse `ts.net`, et l'adresse sans port
> (HTTPS 443) ne peut mener qu'à une seule application. Sur le Mac mini, elle appartient au **cockpit
> LinkedIn** (service `com.xavieradvisory.cockpit`, port local 8766). `tailscale serve --bg 8765` sans
> `--https=8443` la reprend et le cockpit LinkedIn devient inaccessible. Pour la lui rendre :
> `tailscale serve --bg 8766`.

Pour arrêter l'exposition du seul cockpit Missions : `tailscale serve --https=8443 off` (jamais
`tailscale serve reset`, qui coupe aussi le cockpit LinkedIn). Pour arrêter le cockpit :
`zsh deploy/launchd/install-app.sh --remove`.

## 2. Sur l'iPhone (une fois, 3 minutes)

1. App Store → installer **Tailscale**, ouvrir, se connecter **avec le même compte** que sur le Mac,
   activer le VPN (bouton en haut). L'iPhone apparaît dans la liste des appareils, avec le Mac.
2. Safari → ouvrir l'adresse `https://<nom-du-mac>.<ton-tailnet>.ts.net:8443` affichée à l'étape 1.3
   (avec `:8443` à la fin).
3. Bouton **Partager** (carré avec flèche) → **Sur l'écran d'accueil** → Ajouter.
   L'icône « Missions » s'ouvre ensuite en plein écran, comme une app.

Le Mac doit être allumé (ou en veille avec réveil réseau) et Tailscale actif des deux côtés. Hors du Wi-Fi
de la maison, ça marche aussi : Tailscale passe par la 4G/5G.

## 3. Ce que fait le cockpit

| Onglet | Contenu | Actions |
|---|---|---|
| **À décider** | offres « À étudier » et « Dossier prêt », rang du jour puis score | ouvrir le détail, **Je postule**, **J'écarte**, **Préparer le dossier**, Annuler |
| **Dossiers** | offres dont le dossier (CV + lettre) est prêt | lire / copier la lettre, donner une **consigne à Claude** (« plus court », « insiste sur IFRS 17 »), valider la réécriture → nouveaux PDF/DOCX attachés dans Airtable |
| **Postulées** | candidatures envoyées, avec la réponse connue | ouvrir l'annonce, la ligne Airtable |
| **Santé** | dernier run, compteurs, erreurs | **▶ Run** lance un run complet depuis le téléphone |

« Je postule » et « J'écarte » sont appliqués tout de suite (statut, ligne Candidatures) : pas besoin
d'attendre le run suivant. « Je postule » sur une offre sans dossier lance la préparation du dossier en
arrière-plan ; le détail se rafraîchit tout seul quand il est prêt.

## 4. Dépannage

**Première chose à faire, une seule commande sur le Mac** (puis coller la sortie à Claude) :

```bash
cd /Users/xavierrobitaille/Claude/Artifacts/mission-pipeline && zsh deploy/tailscale/check.sh
```

Elle vérifie le service, la page en local, l'icône, la publication Tailscale et affiche les dernières erreurs.

**Écran vide et pas d'icône sur l'iPhone** : Safari n'a reçu aucune page, presque toujours parce que le
cockpit ne tourne pas sur le Mac (Tailscale renvoie alors une page vide). Une fois réparé, **supprimer
l'icône de l'écran d'accueil et la réajouter** : iOS garde l'icône vide capturée la première fois.
La toute première ouverture peut aussi prendre jusqu'à une minute, le temps que Tailscale obtienne le
certificat HTTPS.

| Symptôme | Cause probable | Remède |
|---|---|---|
| Safari : « impossible de se connecter » | VPN Tailscale coupé sur l'iPhone, ou Mac éteint | ouvrir l'app Tailscale sur l'iPhone, vérifier le Mac dans la liste |
| L'adresse sans port ouvre Missions au lieu du cockpit LinkedIn | publication faite sans `--https=8443` | `tailscale serve --bg 8766` puis `tailscale serve --bg --https=8443 8765` |
| Page blanche ou erreur 502 | le cockpit ne tourne pas sur le Mac | `curl http://127.0.0.1:8765/api/sante` sur le Mac ; sinon `zsh deploy/launchd/install-app.sh` |
| « erreur 500 » sur une action | Airtable ou Claude a refusé | `out/logs/app.err.log` sur le Mac, et le champ `Erreur` de la ligne Airtable |
| Les compteurs de Santé sont vides | AIRTABLE_PAT absent ou expiré | `.venv/bin/mp doctor` sur le Mac |

Mettre à jour le cockpit : `git pull` puis `launchctl kickstart -k gui/$(id -u)/com.xrobitaille.mp-app`.
