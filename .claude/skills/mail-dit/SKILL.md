---
name: mail-dit
description: Trier la boîte support@dfm.fr et créer les DIT Artis. À utiliser à chaque passage de surveillance de la BAL support, ou quand Elliot demande de traiter les mails, de créer une DIT depuis un mail, ou de compléter un ticket existant avec la suite d'un fil.
---

# Traitement de la boîte support@dfm.fr

Chaîne mail → DIT Artis. Un passage = lire l'Inbox, décider pour chaque mail,
créer ou compléter dans Artis, ranger le mail, rendre un rapport court.

## Déroulé d'un passage

1. **Lire l'Inbox** : `outlook_email_search`, `folderName: Inbox`, `order: newest`,
   `limit: 25`. Vide → « RAS », fin.
2. **Pour chaque mail, lire le corps complet.** Le `summary` de la recherche est
   tronqué : il ne sert qu'à trier grossièrement. Le corps se récupère par Graph
   (voir `bin/lire_mail.py`).
3. **Raisonner sur LE FIL**, pas sur le message isolé. Voir « Réponse à un
   ticket » ci-dessous : un numéro dans l'objet décide à lui seul.
4. **Décider** : créer une DIT · compléter un ticket existant · ranger sans DIT ·
   laisser en boîte si le client n'est pas identifiable.
5. **Ranger** le mail — et seulement après le succès de l'action Artis.
6. **Rapport** : créé / complété / rangé / laissé, une ligne de raison chacun.
   Rien d'autre : pas de synthèse, pas de tendance, pas de recommandation.

## Pas de contrat d'infogérance → pas de DIT, on transmet

**Avant toute création, vérifier que le client a un contrat Info.** Le test est
`bin/artis.py` → `bien_dit(code)` : s'il rend `None`, le client n'a **ni**
bien INFOGERANCE **ni** BAL EXCHANGE 365 dans sa fiche Artis — il n'est pas
sous contrat d'infogérance, et une demande informatique ne donne pas de ticket
chez nous.

Dans ce cas, et **seulement** dans ce cas :

1. **Transférer le mail** à `cdamato@dfm.fr`, avec **Clifford**
   (`cyapnewkpin@dfm.fr`) **en copie**. C'est la seule exception à l'interdit
   d'envoi : transfert interne, à ces deux destinataires, jamais ailleurs.
2. **Ranger le mail en `01 - DIT ARTIS SUPPORT`** après le transfert.
3. Le dire dans le rapport : client sans contrat Info, transmis à Céline
   d'Amato et Clifford.

Règle posée par Elliot le 2026-10-01 sur la DIT **100175462** (AREAL 29449,
dont les 4 biens sont un écran et des copieurs) : « ta créé ça sans contrat
info chez le client donc annule ». Le ticket a été annulé et le mail transmis.

Ne pas confondre avec le cas d'un client **sous contrat** dont l'identifiant de
bien a une forme inhabituelle : là, `bien_dit` rend bien une chaîne, et la DIT
se crée normalement.

**La règle ne vaut que pour les créations à venir.** Elliot, le 2026-10-01 :
« non, pas rétroactivement ». Une DIT déjà ouverte pour un client hors contrat
suit son cours — on la complète comme les autres quand le client écrit, on ne
l'annule pas et on ne transfère pas son fil.

## Ce qui fait une DIT

Domaine INFO uniquement. Les autres métiers de DFM se rangent sans ticket :

| Sujet | Destination |
|---|---|
| Sécurité, alarme, télésurveillance, contrôle d'accès, badgeuse, vidéo | BAL SECURITE |
| Téléphonie, standard, DECT, portabilité, fibre, 4G de secours | BAL TELECOM |
| Copieur, bourrage papier, nettoyage machine | BAL IMPRESSION |
| SOC, alerte automatique, rapport de sauvegarde, journal d'équipement, newsletter, facture | 04 - INFORMATIONS |
| Mail traité (DIT créée ou complétée), suivi de ticket | 01 - DIT ARTIS SUPPORT |

« Imprimante » n'est pas un mot-clé de filtrage : un problème de pilote ou de
file d'impression sur un poste reste de l'informatique. C'est l'intervention
demandée qui décide, pas le vocabulaire.

**Une demande du client sur SON environnement est une DIT ordinaire**, même
quand elle touche à de l'administration sensible — une élévation de droits dans
son Entra ID, un accès privilégié, une délégation large. Ce n'est pas une
escalade à la DSI DFM : on crée le ticket, l'équipe s'en occupe. Elliot sur la
DIT 100175965 (SRP CONSTRUCTION, `Mail.ReadWrite`), le 2026-10-01 : « quel
rapport Benjamin, c'est une demande d'un client sur son environnement ». Les
escalades à Benjamin concernent nos ressources internes, pas celles des clients.

## Réponse à un ticket — jamais un nouveau ticket

**L'objet contient `[IT xxxxxxxxxx01]` ou `[DIT xxxxxxxxx]` → c'est une réponse
du client à SA demande.** Ce numéro décide seul de l'appartenance du mail.
L'état du ticket ne change rien à cette appartenance :

| État du ticket | Ce qu'on fait |
|---|---|
| `EnCours`, `TravailNonTermine` | ajout de commentaire sous bannière `E.entete_ajout` |
| `Cloturee` | **rouvrir** — `PATCH /dit/{numero}/reouvrir`, corps `{}`, `Content-Type: application/json` (204 → `TravailNonTermine`) — puis ajouter le commentaire |
| `Annulee` | seule exception : l'affaire a été retirée, une nouvelle DIT est légitime ; le dire dans le rapport |

Erreur du 2026-09-30 à ne pas refaire : mail `RE: [IT 10017547001]`, ticket
100175470 vu `Cloturee`, j'en ai créé un second (100175881) au lieu de rouvrir.
Réparation : annuler le doublon, rouvrir, y reporter le texte du client.

Un mail **au sujet neuf, sans numéro dans l'objet**, n'est pas une réponse —
même s'il ressemble à un incident déjà traité. Un symptôme qui se reproduit
après clôture donne un nouveau ticket.

Une **relance sans information nouvelle ne produit rien** : on range, c'est tout.

### Compléter ne suffit pas : prévenir l'intervenant

**Tout ajout du client sur une DIT en cours se double d'un transfert du mail au
dernier intervenant.** Elliot, le 2026-10-01, sur la DIT 100174590 (STEAMONE) :
« tu as bien ajouté l'info dans la DIT, par contre mon intervenant n'était pas
au courant ». Le commentaire ne notifie personne : le technicien a le ticket
ouvert depuis des jours et ne le relit pas.

1. `bin/artis.py intervenants <numero>` — le **dernier** de la liste est celui
   qui a la main (la dernière intervention, même `Planifiee`).
2. Son adresse dans Artis est `support@dfm.fr`, l'adresse de l'organisation :
   **résoudre son nom dans l'annuaire Microsoft** (`search_people`) pour
   obtenir l'adresse réelle. Ne jamais la reconstruire à partir du nom.
3. Transférer le mail du client, en disant en une phrase ce qui a été ajouté,
   sur quelle DIT, et ce que le client demande.
4. Le dire dans le rapport.

**Signature de tout transfert : `Service MCO - Mail automatique`.** Jamais le
nom d'Elliot : le mail part de `support@dfm.fr`, pas de lui, et celui qui le
reçoit doit voir tout de suite que c'est le robot qui écrit (demandé le
2026-10-01).

Pas d'intervenant du tout sur la DIT → c'est l'autre règle qui s'applique :
transfert à Clifford, « aucun intervenant, redispatche ».

## Champs d'une DIT

Voir `references/priorites-uo-biens.md` pour le détail. En résumé :

- `codeTypeDit` **05** (EMAIL CLIENT) · `codeNature` **11** (Hotline) · `etatDit` **EnCours**
- `dateDit` = date du mail, convertie UTC → heure de Paris
- **Priorité** : personne n'est bloqué **03** · une personne bloquée **02** ·
  toute la société bloquée ou arrêt de production **01**.
  Les codes sont inversés par rapport à l'intuition : raisonner en libellé.
- **UO** : `HLN3` si le symptôme commence par `ADM-`, `HLI` sinon.
  Posée sur le numéro d'**intervention** = numéro de DIT + `01`.
- **Bien** : `{code} - INFOGERANCE`, à défaut `{code} - BAL EXCHANGE 365`.
  Jamais vide — sauf client sans aucun des deux, et alors on le signale.
- **Symptôme** introuvable → `PC-DEM-AUT` / `PC-INC-AUT`, et on le signale.
- **Onboarding** (arrivée d'un salarié) → `ADM-ON` + `HLN3`.
- Après création : type de travail **Hotline** + facturation **Non facturable**,
  par le formulaire web, une seule session pour tout le lot.

## Le commentaire

L'objet du mail en première ligne, puis **le texte du client mot pour mot**.
Jamais de reformulation, même meilleure. Signature exclue. Historique du fil
conservé sous un séparateur. Mail en anglais : on le garde en anglais.
Normalisation ISO obligatoire (`E._stockable`) — Artis stocke en ISO-8859-15 et
supprime en silence ce qu'il ne sait pas encoder.

Aucun avis de ma part dans le ticket. Ce qui est sensible se dit à Elliot dans
le rapport, pas dans le commentaire.

**Un commentaire déjà posé ne se repasse pas au propre.** Elliot, le
2026-10-01 : « non, touche pas si clôturé ». Rouvrir une DIT close pour
reformuler un texte que le technicien a déjà lu coûte plus que ça ne rapporte —
la règle du mot pour mot vaut pour ce qu'on écrit, pas pour ce qui est écrit.

Une capture d'écran annoncée par le client (« voici le message qui s'affiche »)
se joint au ticket **et** se retranscrit en texte dans le commentaire : le
technicien lit le ticket, pas forcément les pièces.

## Garde-fous — ne jamais contourner

- **Anti-doublon avant chaque création.** `creer_dit()` interroge Artis lui-même
  (`dit_jumelle`), qui voit aussi les tickets saisis à la main par les collègues.
  S'il refuse, c'est non : on signale, on ne force pas. Faux positif connu :
  deux demandes du même client, même phrase d'ouverture, bénéficiaires
  différents, à moins de 24 h — on attend l'expiration de la fenêtre.
- **Maximum 2 créations par passage.** Pas deux DIT pour le même client à moins
  de 60 s.
- **Expéditeur interne @dfm.fr** → ne JAMAIS créer de contact dans la fiche du
  client. DIT sans demandeur, et on le dit dans le rapport.
- **Demandeur manquant** : chercher l'adresse exacte, puis le nom de famille.
  Créer le contact seulement si c'est une vraie personne — jamais un expéditeur
  automatique, jamais un nom d'un seul mot. Toute création de contact est
  signalée.
- **Jamais d'envoi de mail**, de réponse automatique, de règle, de suppression,
  de transfert externe. La BAL est en production. **Trois exceptions**, toutes
  des transferts internes, jamais ailleurs qu'à ces adresses :
  - **le dernier intervenant de la DIT**, chaque fois qu'on ajoute le mail d'un
    client à son ticket en cours (voir plus haut) ;
  - `cdamato@dfm.fr` + Clifford en copie quand le client n'a pas de contrat
    Info (voir plus haut) ;
  - **Clifford seul** (`cyapnewkpin@dfm.fr`) quand le client attend une
    **réponse humaine directe** qu'aucun ticket ne peut produire — une
    confirmation, un arbitrage, un rappel téléphonique. Dire ce qu'il attend,
    et depuis quand. Autorisé par Elliot le 2026-10-01 sur les deux mails de
    Sandrine Barotte (MISE).

  Les trois se signent **`Service MCO - Mail automatique`**, jamais du nom
  d'Elliot : le mail part de `support@dfm.fr`, et celui qui le reçoit doit voir
  tout de suite que c'est le robot qui écrit.
- **Une seule session web Artis par passage** : chaque connexion consomme un
  siège de licence et bloque l'équipe. Grouper tous les `poser_type_travail` et
  `poser_commentaire` dans la même session ouverte. **Cette session n'est pas
  optionnelle** : sans elle les DIT restent en `Terrain` / `Automatique`
  (voir `references/priorites-uo-biens.md`). Une consigne de passage qui
  interdit « de toucher au type de travail par le formulaire web » vise le coût
  des connexions répétées, pas le résultat : la bonne lecture est *une* session
  en fin de passage pour tout le lot.
- **Mail sensible** (demande de mot de passe ou d'identifiants, code d'accès,
  destinataire grand public en copie) → signaler à Elliot, ne pas agir. Ne
  jamais recopier un secret dans un commentaire de DIT.

## Références

- `references/pieges-artis.md` — API, authentification, erreurs déjà payées
- `references/priorites-uo-biens.md` — codes, symptômes, règles de choix
- `references/dossiers-bal.md` — folderId des dossiers de rangement
- `references/clients-hors-index.md` — résoudre un client absent de l'index

## Outils

- `bin/lire_mail.py` — corps texte d'un mail depuis son internetMessageId
- `bin/artis.py` — helpers REST (auth, GET, biens, anti-doublon, interlocuteurs)
- `bin/creer_dit.py` — crée une ou plusieurs DIT depuis un JSON de propriétés
