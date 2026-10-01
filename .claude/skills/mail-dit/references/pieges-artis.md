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
  20 000. Le bon paramètre, celui du swagger, est **`chaineRecherchee`** :
  `GET /client?chaineRecherchee=SEINE ET MARNE CONSTRUCTION&maxResults=10`
  rend le client et lui seul. C'est la réponse aux clients absents de l'index
  figé — SMC77, ESBGI, Maison Arnaud Larher ont été retrouvés comme ça le
  2026-10-01, après avoir été « laissés en boîte, client non identifiable »
  pendant trois passages. **La recherche donne un candidat, elle ne tranche
  pas** : vérifier ensuite les interlocuteurs et les biens, comme toujours.
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

## Les outils de `bin/` se lancent depuis n'importe où — maintenant

`mail_auth`, `mail_dit` et `mail_dit_ecriture` cherchaient leur `./data` et leur
`.env` **relativement au répertoire courant**. Lancés depuis
`.claude/skills/mail-dit/bin/`, ils ne trouvaient rien, et chacun échouait à sa
façon, sans jamais nommer la cause :

| Outil | Symptôme observé | Cause réelle |
|---|---|---|
| `lire_mail.py` | « mail introuvable » | `access_token()` rendait `None` → Graph 401 `token could not be read` |
| `artis.py` | `RuntimeError: configuration Artis absente` | `.env` introuvable |
| `creer_dit.py` | `armee : False`, puis `ERREUR : None None` | le drapeau `data/mail_dit_ecriture.txt` invisible |

Les trois chemins sont désormais ancrés sur le fichier Python lui-même
(`Path(__file__).parent`), pas sur le `cwd`. `DATA_DIR` reste prioritaire : en
production rien ne change. Corrigé le 2026-10-01.

La leçon dépasse ces trois modules : **un chemin relatif dans un module importé
depuis ailleurs est un piège silencieux**. Si un outil de `bin/` se met à ne
rien trouver, vérifier d'abord d'où il croit lire.

## Le type de facturation s'écrit « Non facturable », avec une espace

`poser_type_travail(..., aussi={"cb_typeFactu": "..."})` lit le libellé du
`<select>`, pas le code stocké. Artis **affiche** `NonFacturable` quand on relit
la fiche, mais le formulaire attend `Non facturable`. Passer la forme collée
rend `facturation inconnue : NonFacturable (attendu Facturable/Non
facturable/Automatique)` — refus propre, avant écriture, mais le siège de
licence est consommé pour rien.

## Graph rend des 5xx par intermittence

`GET /me/messages/{id}` a rendu trois `504 UnknownError` au corps vide le
2026-10-01, sur une requête qui passait en direct l'instant d'après.
`lire_mail.py` les lisait comme un mail absent. Il réessaie maintenant quatre
fois sur tout code ≥ 500. Un 5xx n'est pas une réponse : c'est une absence de
réponse.

## Un arriéré de propositions n'est pas un arriéré de travail

Le 2026-10-01 à 18h00, à la seconde où le drapeau d'écriture a été posé sur
`/data`, le robot a créé **neuf DIT en soixante secondes**. Aucune ne
correspondait à un mail en boîte : c'étaient les propositions P-1390 à P-1399,
nées de mails du **29 septembre**, restées à l'état `nouveau` parce que
personne ne referme une proposition dont le mail a été traité ailleurs.

Huit étaient des doublons exacts — même client, même minute, même objet que des
tickets déjà ouverts. Elles ont été annulées. Une seule était légitime
(100176161, client 30428).

**L'anti-doublon ne protège pas de ça** : sa fenêtre est de 48 h, et ces mails
en avaient 50. Un garde-fou calibré sur « deux demandes identiques le même
jour » ne voit rien quand on rejoue l'avant-veille.

La règle, désormais dans `traiter_automatiquement` : **un mail sorti de la boîte
a reçu une issue**. Un collègue l'a traité dans Artis, ou il a été rangé sans
ticket. Seules les propositions dont le mail est **encore en boîte** s'exécutent
automatiquement.

Corollaire pour tout armement futur : **vider la file de propositions avant
d'armer**, ou s'attendre à ce qu'elle parte d'un bloc. Le drapeau n'est pas un
interrupteur neutre — c'est un déclencheur sur une file qui a pu s'accumuler
pendant des jours.

