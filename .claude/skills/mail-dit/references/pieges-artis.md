# Pièges Artis / Graph — erreurs déjà payées

Chaque ligne ici a coûté un incident réel. Les relire avant d'écrire du code.

## Authentification et lecture

- **Le jeton Artis passe en paramètre d'URL `token=`, pas en en-tête Bearer.**
  Un `Authorization: Bearer` renvoie `401` sans message utile.
  `mail_dit_ecriture._artis()` rend `(base_url, token)`.
- **`mail_dit._get(tok, url, params)` est le helper GRAPH, pas Artis.** Il attend
  une URL absolue. Pour Artis, faire la requête directement (voir `bin/artis.py`).
- **`/client/{code}/bien` est paginé à 20** : sans `maxResults=5000`, on conclut
  à tort qu'un client n'a pas d'infogérance. Quatre fausses conclusions le
  2026-09-29, dont un « client peut-être hors contrat » totalement faux.
- **`/client?libelle=X` ignore le filtre** et renvoie toute la base, plafonnée à
  20 000. Ne jamais chercher un client par ce biais.
- **`GET /affaire/{numero}` donne le client**, raison sociale comprise. C'est la
  seule façon fiable de rattacher un mail interne qui ne cite qu'un numéro
  d'affaire : le 2026-10-01, l'affaire 10009602 présentée comme « PPG
  ingénierie » était en réalité **PPN INGENIERIE 29347** — le nom écrit dans le
  mail était faux, le numéro non. `GET /commande/{numero}` existe aussi, et ne
  rend pas le même client : ce sont deux registres différents, ne pas les
  confondre.
- **Les dates en paramètre sont ISO seulement** (`2026-09-30`). Le format
  `JJ/MM/AAAA` renvoie `400`.
- **`/dit/{n}` n'expose ni le bien ni le demandeur** en lecture. Ne pas conclure
  d'une absence dans la réponse que le champ est vide dans Artis.
- **Artis peut répondre `503` sur l'authentification** (fenêtre de maintenance
  nocturne, constaté vers 23 h). Ce n'est pas un problème de jeton : réessayer
  plus tard, ne rien créer entre-temps.

## Écriture

- **Ne JAMAIS deviner un numéro de DIT avant sa création.** Le 2026-09-30, j'ai
  lancé le type de travail sur `100175860` en anticipant le mien, alors que la
  création a rendu `100175861` : le ticket d'un collègue a été modifié. Toujours
  enchaîner sur le `numero` rendu par `creer_dit()`.
- **L'UO se pose sur le numéro d'INTERVENTION** = numéro de DIT + `01`
  (`10017586101`), pas sur le numéro de DIT. Sinon `404`.
- **`annuler_intervention` exige `Content-Type: application/json` et un corps
  `{}`**, sinon `415`.
- **`creer_interlocuteur` rend l'identifiant en entier nu**, pas en objet JSON.
- **Une DIT en état `TravailNonTermine` peut refuser l'ouverture en
  modification** : `poser_commentaire` échoue avec « la DIT ne s'ouvre pas en
  modification ». Signaler, ranger le mail quand même.
- **Même refus quand une QUESTION est ouverte sur la DIT**, quel que soit son
  état : le fil d'Ariane affiche `Demande d'intervention (M) / Question (V)` et
  le commentaire ne passe pas (100175802, GCI, le 2026-10-01, pourtant
  `EnCours`). **Et l'échec laisse la session web coincée sur cet écran** : tout
  ce qui suit dans la même session échoue avec « impossible de repartir de
  l'accueil ». Donc **traiter en dernier** une DIT dont l'ajout est incertain,
  ou vérifier le résultat avant d'enchaîner.
- **ISO-8859-15** : `E._stockable(texte)` sur tout ce qui part dans Artis. Sans
  ça, les apostrophes typographiques et les tirets longs disparaissent en
  collant les mots voisins (constaté sur la DIT 100173591 : « s'il vous plait »
  arrivé « sil vous plait »).

## Sessions web (licences)

- Chaque `Web.ouvrir()` consomme **un siège de licence** partagé avec toute
  l'équipe. Une seule session par passage, réutilisée pour tous les appels
  (`poser_type_travail`, `poser_commentaire`).
- Erreur `connexion refusée : Ouverture impossible : le nombre d'accès
  simultanés global est atteint` = l'équipe occupe tous les sièges. Ne pas
  insister, reporter les ajouts au passage suivant, laisser les mails en boîte.

## PowerShell / fichiers

- **`Set-Content -Encoding utf8` écrit un BOM.** `json.load()` le refuse
  (`Unexpected UTF-8 BOM`) : lire en `utf-8-sig`. Même piège sur le fichier
  drapeau `/data/mail_dit_ecriture.txt`, où le BOM faisait échouer
  `ecriture_armee()` en silence — l'écrire avec
  `[IO.File]::WriteAllText(chemin, texte, (New-Object Text.UTF8Encoding $false))`.
- **`az containerapp exec --command` découpe sur les espaces** : une commande
  Python doit tenir sans espace (`__import__('json')`), sinon `SyntaxError`.
  Et pas plus d'un `exec` par passage : au-delà, `429` avec `retry-after: 600`.

## Microsoft Graph

- **Ne jamais reconstruire un identifiant de message par concaténation.**
  Deux incidents le même jour (2026-09-30) : un rangement groupé bâti sur
  `préfixe + suffixe` a envoyé un devis sécurité en 01 - DIT, puis un journal
  Vigor au même endroit, pendant que les mails visés restaient en boîte.
  Les suffixes se ressemblent (`…NXAAA=` / `…NYAAA=`) et rien ne signale
  l'erreur : le `move` réussit sur le mauvais message. Toujours reprendre
  l'`id` complet rendu par la recherche, et ranger **un message par appel**.
- **L'identifiant change à chaque déplacement** : un id lu avant un `move` n'est
  plus valide après.
- **`outlook_email_search` refuse `order` avec une requête plein texte.**
  Recherche par mots-clés → pas de tri ; tri → filtrer par dossier/expéditeur.
- **Pièces jointes** : filtrer sur `isInline`. Les images de signature et les
  bandeaux d'organisation (`org-int-img.png`) ne sont pas des pièces utiles.
  Une photo de 5 Mo envoyée par le client, si.
