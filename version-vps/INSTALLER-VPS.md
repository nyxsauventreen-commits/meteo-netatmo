# Installer le mode en ligne sur un serveur

Le serveur fait tourner le même programme que le PC, en mode en ligne : il démarre ainsi dès que
`METEO_INVITE_CODE` est défini. Le PC reste utilisable en même temps, en mode local. Le site
tourne dans Docker avec l'image Python officielle et s'ouvre en HTTPS sur votre domaine. La
collecte continue quand le PC est éteint.

Dans ce guide :

- `votre-domaine.fr` est votre nom de domaine ;
- `utilisateur@adresse-du-serveur` est votre accès SSH, par exemple `ubuntu@203.0.113.10`.

**Plusieurs utilisateurs :** chacun a son compte et sa liste de stations, avec ses propres noms
et couleurs. Une station suivie par plusieurs personnes n'est collectée qu'une fois. Pour créer
un compte, il faut un code d'invitation, et chaque code ne sert qu'une fois.

**Pourquoi un tunnel Cloudflare :** il fournit le HTTPS sans ouvrir de port sur le serveur. Il
convient aussi quand un autre site occupe déjà les ports 80 et 443. Un proxy inverse avec HTTPS
(Caddy, Nginx, Traefik) peut le remplacer. Dans ce cas, n'exposez le port 8765 qu'à ce proxy.
Le site doit être servi en HTTPS : le cookie de session est marqué `Secure`.

**Ce qu'il faut :**

- un serveur Linux avec Docker, et éventuellement Portainer ;
- un domaine géré par Cloudflare ;
- un PC avec Python, `ssh` et `scp`. Windows 10 et 11 les ont déjà.

Les écrans de Cloudflare et de Netatmo peuvent changer : les noms de menus sont donnés à titre
indicatif.

## 1. Préparer le PC

Le programme de préparation part du PC. Il envoie au serveur vos stations et vos mesures, de
`data/meteo.db`. Le premier compte créé sur le site reçoit ces stations. Installez donc
d'abord le programme sur le PC et lancez-le au moins une fois, avec vos stations : voir le
[README](../README.md).

## 2. Mettre le domaine sur Cloudflare

1. Sur [cloudflare.com](https://www.cloudflare.com), choisissez **Connect a domain**, entrez
   `votre-domaine.fr` et prenez l'offre **Free**.
2. Gardez les enregistrements MX et TXT des e-mails. Supprimez les enregistrements A ou CNAME
   de l'adresse choisie pour le site, qui entreraient en conflit avec le tunnel.
3. Chez votre registraire, désactivez **DNSSEC** s'il est actif. Remplacez ensuite les
   serveurs DNS par les deux serveurs donnés par Cloudflare.
4. Attendez que Cloudflare indique le domaine comme actif.

## 3. Créer le tunnel

1. Dans Cloudflare, ouvrez **Networking**, puis **Tunnels**, et créez le tunnel `meteo`.
2. Choisissez **Docker** et copiez la commande affichée. N'exécutez pas cette commande.
   - Le **jeton** est la très longue suite de caractères après `--token`. Il commence souvent
     par `eyJ`. Ce n'est pas le « Tunnel ID », qui est plus court.
   - **Continue** reste grisé tant que le conteneur ne tourne pas : le tunnel est déjà
     enregistré.
3. Ouvrez le tunnel, puis l'onglet **Routes**, **Add route** et **Published application**.
   - Choisissez `votre-domaine.fr`, avec ou sans sous-domaine, par exemple `meteo`.
   - Comme **Service URL**, indiquez `http://meteo:8765`.

## 4. Créer une deuxième application Netatmo

Le PC et le serveur ne peuvent pas partager leurs jetons Netatmo. Les jetons changent à chaque
renouvellement, donc le PC et le serveur se déconnecteraient l'un l'autre.

1. Sur [dev.netatmo.com](https://dev.netatmo.com), dans « My Apps », créez une deuxième
   application, par exemple « Meteo serveur ».
2. Si un champ « redirect URI » est demandé, indiquez `http://localhost:8771/callback`.
3. Gardez son client ID et son client secret.

## 5. Lancer le programme de préparation

Dans un terminal, depuis le dossier du projet. Le programme du PC peut rester ouvert.

```bash
python version-vps/preparer_vps.py
```

Sous Windows, vous pouvez aussi double-cliquer sur `preparer-vps.bat`. Le programme fait
ceci :

1. Il demande le client ID et le client secret.
2. Il ouvre une page pour connecter Netatmo.
3. Il demande l'accès au serveur, sous la forme `utilisateur@adresse-du-serveur`.
4. Il crée `/opt/meteo-perso` sur le serveur, envoie le programme et vos données, puis règle
   les droits.

Le mot de passe du serveur peut être demandé plusieurs fois. En cas d'échec, relancez le
programme : il reprend sans redemander ce qui est déjà fait.

## 6. Démarrer les conteneurs

**Avec Portainer :**

1. Ouvrez **Stacks**, puis **Add stack**, et nommez-la `meteo-perso`.
2. Choisissez **Web editor** et collez le contenu de `version-vps/docker-compose.yml`.
3. Dans **Environment variables**, ajoutez :
   - `METEO_INVITE_CODE` : un code d'au moins 8 caractères. Il crée votre compte, puis devient
     votre premier code d'invitation, valable une fois.
   - `TUNNEL_TOKEN` : le jeton de l'étape 3.
4. Cliquez sur **Deploy the stack**.

**Sans Portainer :**

1. Copiez `docker-compose.yml` dans un dossier du serveur.
2. Dans ce même dossier, créez un fichier `.env` avec ces deux lignes :

   ```bash
   METEO_INVITE_CODE=votre-code
   TUNNEL_TOKEN=votre-jeton
   ```

3. Lancez `docker compose up -d`.

## 7. Créer votre compte en premier

Ouvrez `https://votre-domaine.fr`, cliquez sur **Créer un compte** et entrez le code
d'invitation. **Le tout premier compte reçoit les stations de `config.json`.** Les comptes
suivants démarrent avec une liste vide.

Quand tout fonctionne, supprimez le dossier `vps-data` du PC : il contient des secrets.

## En cas de problème

Regardez les journaux des conteneurs `meteo-perso` et `meteo-tunnel`. Dans Portainer, ils sont
dans **Containers**, puis **Logs**. En ligne de commande, utilisez `docker logs meteo-perso`.
Sans `METEO_INVITE_CODE`, `meteo-perso` refuse de démarrer.

**Mot de passe oublié :** ouvrez une console dans le conteneur `meteo-perso`, avec
`docker exec -it meteo-perso sh`, puis lancez :

```bash
python accounts.py mot-de-passe NOM NOUVEAU_MOT_DE_PASSE
```

**Connexion Netatmo perdue :**

1. Arrêtez les conteneurs.
2. Si vous avez des comptes à garder, copiez d'abord `/opt/meteo-perso/data/meteo.db` dans
   `vps-data`. Le programme de préparation remplace aussi la base du serveur.
3. Supprimez `vps-data/token.json` s'il existe encore.
4. Relancez le programme de préparation.

## Mettre à jour

Lancez `python version-vps/mettre_a_jour_vps.py`, ou double-cliquez sur
`mettre-a-jour-vps.bat` sous Windows. Le programme est envoyé et `meteo-perso` redémarre, sans
toucher aux données ni aux comptes. Si le redémarrage échoue, faites-le à la main.

## Gérer les utilisateurs

Le premier compte créé est l'administrateur. Dans le menu à son nom, **Administration**
ouvre sa page dédiée (`/admin`) :

- **État du serveur :** connexion Netatmo, dernière collecte, requêtes Netatmo de la dernière
  heure, taille de la base, place libre sur le disque.
- **Limites :**
  - stations au total ;
  - stations par personne, 8 au plus ;
  - minutes entre deux collectes ;
  - option Stations voisines autorisée ou non.

  Baisser une limite ne retire rien : elle bloque seulement les nouveaux ajouts.
- **Message pour tous :** affiché en haut de la page de chacun, pendant la durée choisie.
- **Options utilisées :** combien de personnes ont choisi chaque option, chaque thème et chaque
  période.
- **Utilisateurs :** dernière visite, qui a invité qui, le code et les stations de chacun.
  - On peut créer un nouveau code, donner un mot de passe temporaire ou supprimer un compte.
  - La case **Tout le monde peut inviter** décide si les codes des autres marchent. Sinon,
    seul celui de l'administrateur fonctionne.
- **Stations suivies :** chaque station, combien de personnes la suivent, et sa dernière mesure.
- **Journal des erreurs de collecte :** chaque nouveau problème, puis son retour à la normale.
  Les 200 dernières lignes sont gardées.
- **Sauvegarde :** télécharge une copie complète de la base (voir plus bas).
- **Stations abandonnées :** celles que plus personne ne suit. Elles ne sont plus collectées.
  « Supprimer ses données » efface leurs mesures pour libérer de la place.

Le premier compte est le **propriétaire** du site. Lui seul peut nommer d'autres
administrateurs (« Nommer admin ») ou leur retirer ce rôle. Les autres administrateurs ont la
même page. Ils ne peuvent ni toucher au compte du propriétaire, ni gérer un autre
administrateur.

Chaque personne trouve son code dans **Inviter quelqu'un**, dans le menu à son nom. Un code ne
sert qu'une fois : un nouveau le remplace dès qu'une personne s'inscrit avec.
`METEO_INVITE_CODE` ne sert plus qu'au démarrage : changer sa valeur ensuite ne change pas les
codes.

## Sauvegarder

Tout ce qui change se trouve dans `/opt/meteo-perso/data`, comptes compris. Copiez ce dossier
pour garder l'historique, les utilisateurs et la connexion Netatmo.

Plus simple : utilisez **Télécharger une sauvegarde de la base**, sur la page Administration.
Elle donne un fichier `meteo-AAAA-MM-JJ.db` avec les mesures, les comptes et les réglages, pris
sans arrêter le site. La connexion Netatmo (`token.json`) n'y est pas. Pour remettre la base en
place :

1. Arrêtez le conteneur `meteo-perso`.
2. Envoyez le fichier sur le serveur, dans `/opt/meteo-perso/data/`, sous le nom `meteo.db`, à
   la place de l'ancien. Supprimez `meteo.db-wal` et `meteo.db-shm` s'ils existent.
3. Lancez `sudo chown 1000:1000 /opt/meteo-perso/data/meteo.db`.
4. Redémarrez le conteneur.

## Limites

- 8 stations par utilisateur et 30 pour tout le serveur, pour protéger la collecte.
- Le programme demande peu de mémoire : 256 Mo au plus pour le site et 128 Mo pour le tunnel.
  Le disque utilisé est de l'ordre de quelques dizaines de Mo.
