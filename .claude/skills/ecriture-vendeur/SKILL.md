---
name: ecriture-vendeur
description: Protocole obligatoire avant toute écriture sur une API vendeur ou métier — Artis, Sophos, Datto, SentinelOne, Yooz, TD SYNNEX, Vade, M365. À charger dès qu'une tâche implique un POST/PUT/PATCH/DELETE vers un de ces systèmes, une commande de licences, une modification de contrat, une action en lot, ou la manipulation d'un jeton d'API.
---

# Écrire chez un vendeur : le protocole

Tous ces systèmes sont en **production** et portent des données clients réelles. Une écriture peut
débiter de l'argent (commande TD SYNNEX), baisser la sécurité d'un parc entier (tamper protection
Sophos), ou modifier de la comptabilité (Yooz, Artis).

## La règle

**Aucune écriture sans accord explicite d'Elliot pour cette action précise.** Un accord donné une
fois ne couvre pas la fois suivante, et « tu peux voir ? » n'est pas un accord pour modifier.

En vigueur après incidents datés : Artis (DIT 2026-05-05), Sophos (2026-05-07), Datto (2026-05-19).

### UNE exception, et une seule : la chaîne mail → DIT

**Le skill `mail-dit` a son propre régime, et cette règle-ci ne s'y applique pas.** Un passage
`/mail-dit` crée des DIT, les complète, range et transfère **sans demander d'accord** — c'est
tout l'objet de la chaîne, et elle tourne en automatique plusieurs fois par jour.

L'accord d'Elliot n'y est pas donné mail par mail : il est **matérialisé par le drapeau
`/data/mail_dit_ecriture.txt`**. Tant qu'il dit `OUI`, la chaîne écrit ; dès qu'il dit autre
chose, plus rien ne part, et c'est le bouton d'arrêt. S'y ajoutent des garde-fous qui ne vivent
pas dans une consigne mais dans le code : 10 créations par passage, 60 s entre deux DIT du même
client, l'anti-doublon qui interroge Artis lui-même, et un transfert qui ne prend qu'un **motif**
— jamais une adresse libre.

Ne demande donc **jamais** d'autorisation pour une action du passage mail → DIT, et ne traite pas
la boîte `support@dfm.fr` comme lecture seule quand tu exécutes ce skill. Le 2026-10-15, un
passage a invoqué cette page pour ne rien faire du tout : 20 mails laissés, zéro DIT, zéro
rangement, alors que le drapeau disait `OUI`. Une règle de prudence qui empêche le travail prévu
n'est plus une prudence.

## Ce qu'on fait à la place, toujours dans cet ordre

1. **Diagnostiquer en lecture seule.** Tout ce qui se comprend par un GET se comprend sans écrire.
2. **Présenter ce qui serait modifié** : combien d'éléments, lesquels, montants, clients nommés.
3. **Demander** — en signalant ce qu'on ne peut pas vérifier soi-même.
4. **Exécuter** seulement après le feu vert, puis **rapporter** le résultat réel.

## Dans le code

- Paramètre `dry_run=True` **par défaut** sur toute fonction qui écrit. Le mode réel exige
  `dry_run=False` **explicite**, et pour les actions à portée large un `confirm=True` en plus
  (cf. `POST /api/sophos/tamper/global`, `POST /api/tdsynnex/order`).
- Le mode simulation renvoie l'appel exact qui **serait** envoyé (`preview_call`), pas un résumé
  vague.
- Journaliser avec `log()` : qui, quoi, combien, résultat HTTP. Et historiser sur `/data`
  (`sophos_tamper_history.json`, `yooz_history.json`, `tdsynnex_orders_audit.json`) — un fichier
  d'historique est ce qui permet, deux mois plus tard, de savoir ce qui s'est passé.
- **Historiser le résultat de chaque étape, pas seulement de la première.** L'import Yooz
  enchaînait création → dimensions → export SAGE → clôture, mais seul l'export était tracé : la
  clôture échouait silencieusement depuis deux mois et ~250 factures se sont empilées dans Yooz
  sans que personne ne le voie.
- Côté interface : confirmation nommant le client et l'effet réel, pas un « OK ? » générique.

## Jetons

- **TD SYNNEX et Yooz : refresh token ROTATIF à usage unique.** Chaque authentification consomme le
  jeton courant et en renvoie un nouveau qu'il faut persister immédiatement (écriture atomique).
  Conséquences : **une seule** authentification par traitement, l'access token circule en paramètre
  entre les fonctions, jamais deux traitements en parallèle, et **on ne « teste » pas l'auth en
  l'exécutant** — un test de trop casse la chaîne et il faut alors regénérer au portail.
- Un **5xx ou un 429** n'est pas un jeton mort : c'est la passerelle du vendeur. Réessayer, et ne
  surtout pas regénérer le jeton (incident TD SYNNEX 2026-09-22).
- **SentinelOne** : jeton valable 30 jours, lisible dans le JWT (`_s1_token_info()`).
- Ne jamais afficher un jeton : passer tout message d'erreur dans `_sans_token()`. Les exceptions
  `requests` recopient l'URL, et Artis met son token dans la query string.
- Si un jeton a circulé par un canal non maîtrisé, le dire et proposer de le regénérer.

## Spécificités par système

**Artis** — l'API REST est gratuite, mais **chaque connexion web consomme une licence** de siège :
enchaîner des logins bloque toute l'équipe. Le token a un cache de 10 min ; s'il est refusé (401),
en redemander un avec `force=True` et réessayer une fois. Pour piloter le formulaire web :
`userEvent` en minuscule, lien générique, attention aux `select` désactivés.

**Sophos** — antivirus **uniquement** : ne jamais compter les firewalls. « Déployé » = machines
actives sur 90 jours, pas le billing mensuel. La tamper protection a **deux niveaux** : un
interrupteur global de tenant et un état par machine ; quand le global est OFF, aucune machine
n'est protégée, quel que soit son état individuel.

**Datto** — le matching de composant doit être **strict** : un match approximatif a lancé
« Monitor » au lieu de « V6 Fonctionnel » (2026-05-19). Quickjob, UDF et déploiement = accord requis.

**Yooz** — un document encore verrouillé fait renvoyer 201 à `forceTask` **sans rien appliquer**.
Déverrouiller avant, et vérifier l'état après. Ne jamais supprimer un document : clôturer le
process le sort de la file sans toucher à SAGE. Avant de clôturer, s'assurer que l'export SAGE a
réussi — sinon la facture disparaît de la file sans être arrivée en compta.

**Vade** — le compte revendeur a été **migré chez Hornetsecurity** (constaté le 2026-10-02).
Conséquence : `POST /customer/v2/customers` répond `403 « you have been migrated into Hornet
Security Systems »`. La **lecture** v2 continue de fonctionner — d'où l'illusion d'un problème de
droits. Les créations passent désormais par :

```
POST /partner/v3/customers      ← « Creates a new customer for a migrated partner »
GET  /partner/v3/customers      ← la liste des clients DÉJÀ migrés (76 sur 191 au 2026-10-02)
```

Le corps n'a **rien** du v2 : champs en minuscules, `name` au lieu de `brand`, plus de
prénom/nom de contact, et trois champs devenus obligatoires — `domain` (domaine M365 du client),
`country` (ISO-2) et `reseller`. Ce dernier n'est écrit nulle part dans la configuration : il se
relève sur un client déjà migré (`reseller.id`), ce que fait `_vade_reseller_id()`.

Les deux listes **se recouvrent** (v3 = le sous-ensemble migré, 76 sur 191) et le format
d'identifiant ne les distingue pas — v3 mélange UUID et identifiants opaques. Avant de créer, chercher dans les deux (`_vade_find_customer` +
`_vade_find_customer_v3`), sinon on crée un doublon sans le voir. Les étapes suivantes
(licences, MSP, supervision) sont **encore sur v2** : leur équivalent migré n'est pas identifié.

**La licence s'appelle `subscription` en v3**, et il n'existe que deux routes dans tout l'espace
v3 (sondage GET exhaustif) : `customers` et `subscriptions`. La création d'essai se fait en
**deux temps** là où la v2 n'en demandait qu'un :

```
POST /partner/v3/subscriptions              -> {"id": 44123, "token": "…"}
PUT  /partner/v3/subscriptions/activation   <- {"token": "…"}   puis {"xVadeKey": "…"}
```

`duration` n'accepte que **`SUBSCRIPTION`** chez un partenaire migré — `TRIAL` renvoie
`400 Wrong duration for this subscription`. Il n'existe donc **plus de licence d'essai gratuite** :
tout abonnement créé est facturé dès son activation. Le dire à l'utilisateur AVANT le clic.

Le piège : le champ se nomme `tenantID` (deux majuscules), contre `tenantId` en v2. Mal
orthographié, il est **ignoré sans erreur** et l'abonnement part sur le mauvais tenant.
`product` 9 = Vade for M365, `environment` 2 = `o365eu`. Entête `X-Partner-Send-Mail: true`
pour que le client reçoive le mail d'activation.

Pour savoir si un chemin v3 existe, lire la **forme** du 404 : `404 page not found` en texte brut
= route non enregistrée ; `{"statusCode":404,…}` en JSON = route existante, ressource absente.

L'API ne publie aucun swagger (`/swagger.json`, `/openapi.json` → 404) et `OPTIONS` ne renvoie
pas d'en-tête `Allow` : le seul inventaire fiable des routes est le portail développeurs Vade.

**TD SYNNEX** — `POST /orders` débite réellement. Aperçu obligatoire, puis `confirm:true`. En
revanche `POST …/reports/{id}/reportDataCsv` ne fait que produire un rapport : ce n'est pas une
écriture métier.

**Mail support@dfm.fr** — boîte de production. Hors du skill `mail-dit`, lecture seule : aucune
action mail sans accord. **Pendant un passage `mail-dit`, au contraire, le rangement, la copie en
`16 - REPONSE DIT` et les trois transferts internes documentés font partie du travail** — ils
sont couverts par le drapeau `/data/mail_dit_ecriture.txt` (voir l'exception, plus haut).

## Quand on ne sait pas

Dire qu'on ne sait pas, et le vérifier. Ne pas affirmer qu'une API est en lecture seule sans
l'avoir contrôlé — ça a été affirmé à tort pour la tamper protection globale Sophos, alors que
`POST /endpoint/v1/settings/tamper-protection` existe. Vérifier dans la doc du vendeur ou son SDK
officiel, pas de mémoire.
