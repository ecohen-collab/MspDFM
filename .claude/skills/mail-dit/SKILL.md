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
7. **Journaliser chaque action** — voir ci-dessous.

## Le journal : ce qui reste quand le terminal se ferme

Un rapport de passage vit dans un terminal et meurt avec lui. Le **journal**
(`mail_dit_journal.py`, à la racine du repo) écrit une ligne par action dans
`DATA_DIR/mail_dit_journal.jsonl`, que l'onglet **Journal mail vers DIT** du
dashboard affiche. Demandé par Elliot le 2026-10-01 : « un onglet unique avec
tout ce qui a été fait ; mail de x@y du 02/09 à 14h54 ──▸ DIT 100176099 créée ».

- **Les créations sont journalisées toutes seules** par `bin/creer_dit.py` :
  rien à faire.
- **Tout le reste est à écrire à la main**, dans le même passage :

```python
import mail_dit_journal as J
J.ajouter("complement", mail_de="x@y.fr", mail_objet="RE: [IT …]",
          dit="100175976", client_code="28964", client_nom="EDOLIX",
          detail="le client demande quand la coupure sera rétablie")
J.ajouter("transfert", mail_de="x@y.fr", destinataire="ckudlak@dfm.fr",
          dit="100175976", detail="prévenir l'intervenant")
J.ajouter("rangement", mail_de="x@y.fr", mail_objet="…",
          dossier="BAL TELECOM", detail="standard téléphonique")
J.ajouter("laisse", mail_de="x@y.fr", detail="corps vide, aucune demande")
```

Actions admises : `creation`, `complement`, `rangement`, `transfert`,
`annulation`, `laisse`, `piece`. Une action hors liste lève une erreur —
c'est voulu : une ligne au type inconnu serait invisible dans les filtres.

Le fichier est en **append** : jamais de réécriture, jamais de verrou. Deux
écrivains concurrents s'intercalent, ils ne s'effacent pas.

## Pas de contrat d'infogérance → pas de DIT, on transmet

**Avant toute création, vérifier que le client a un contrat Info.** Le test est
`bin/artis.py` → `bien_dit(code)` : s'il rend `None`, le client n'a **ni**
bien INFOGERANCE **ni** BAL EXCHANGE 365 dans sa fiche Artis — il n'est pas
sous contrat d'infogérance, et une demande informatique ne donne pas de ticket
chez nous.

Dans ce cas, et **seulement** dans ce cas :

1. **Transférer le mail au commercial informatique du client**, avec
   **Clifford** (`cyapnewkpin@dfm.fr`) **en copie**.
2. **Ranger le mail en `01 - DIT ARTIS SUPPORT`** après le transfert.
3. Le dire dans le rapport : client sans contrat Info, transmis à son
   commercial.

### Qui est le commercial informatique du client

Il est dans la fiche Artis, onglet **Classifications**, ligne
`COMMERCIAL INFORMATIQUE`. Par l'API : `GET /client/{code}` →
`categ[].classification.abrev == "COMINF"`, et l'`abrev` de la catégorie **est
le matricule** (`JKTORZA`, `SVOCI`, `CGALLOT`…). Elliot, le 2026-10-02 : « il
faudrait plutôt transférer au commercial qui est assigné au client ».

`mail_dit_ecriture.commercial_informatique(code)` fait le chemin complet :

- du matricule à l'adresse, `matricule.lower() + "@dfm.fr"` est la convention
  du tenant — mais elle n'est jamais **devinée** : elle est vérifiée dans
  l'annuaire Microsoft, et le compte doit être **actif**. Un commercial parti
  garde sa ligne dans Artis pendant des années (`FMEIRINHO`, `CNEBEL` sont
  désactivés) ;
- un client présent dans **plusieurs organisations internes** porte plusieurs
  `COMINF` (AREAL : `CGALLOT` pour DFM, `AELKAIM` pour MOO). On ne tranche
  pas : le premier est destinataire, les autres passent en copie ;
- **repli sur `cdamato@dfm.fr`** quand la fiche ne désigne personne de
  joignable — c'est le cas de POLYTRANS et d'AREAL, qui n'ont par ailleurs
  aucune affaire. Le dire dans le rapport quand ça arrive.

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
- **UO** : `HLI` pour tout ce qu'un N1/N2 fait seul — **reset de mot de passe
  compris**, malgré son symptôme `ADM-365-GES` ; `HLN3` pour ce qui demande la
  console d'administration (comptes, licences, délégations, MFA, onboarding).
  Le préfixe du symptôme ne décide pas : voir `references/priorites-uo-biens.md`.
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
- **Maximum 10 créations par passage.** Elliot a relevé la limite de 2 à 10 le
  2026-10-01, après avoir vu la boîte accumuler sept demandes traitables
  derrière le plafond : « on ne laisse RIEN en BAL ». Le plafond de 2 datait du
  temps où il validait chaque création ; l'anti-doublon interroge Artis
  lui-même et voit aussi les tickets saisis par les collègues, c'est lui qui
  protège désormais.
  Pas deux DIT pour le même client à moins de 60 s — ce délai-là reste.
- **Expéditeur interne @dfm.fr** → ne JAMAIS créer de contact dans la fiche du
  client. DIT sans demandeur, et on le dit dans le rapport.
  **Mais d'abord, lire l'historique du fil** : un collègue qui transfère la
  demande d'un client n'est pas le demandeur, il est le facteur. Le demandeur
  est la personne du client citée plus bas dans le mail, et elle est souvent
  déjà dans la fiche Artis. Elliot, le 2026-10-01, sur le mail d'Abdourahmane
  relayant la CNCT : « il faut clairement créer un ticket CEPROC, le demandeur
  client est dans l'historique de mail, il faut pas le traiter directement ».
  La DIT sans demandeur est pour le cas où le fil ne nomme aucun client.
- **Une demande relayée par un collègue reste une demande client** : elle
  donne une DIT, elle ne se traite pas de la main à la main. Même quand on
  saurait la faire soi-même — sortir un historique de tickets, par exemple —
  le ticket doit exister, sinon le travail n'est ni tracé ni facturable.
- **Demandeur manquant** : chercher l'adresse exacte, puis le nom de famille.
  Créer le contact seulement si c'est une vraie personne — jamais un expéditeur
  automatique, jamais un nom d'un seul mot. Toute création de contact est
  signalée.
- **Jamais d'envoi de mail**, de réponse automatique, de règle, de suppression,
  de transfert externe. La BAL est en production. **Trois exceptions**, toutes
  des transferts internes, jamais ailleurs qu'à ces adresses :
  - **le dernier intervenant de la DIT**, chaque fois qu'on ajoute le mail d'un
    client à son ticket en cours (voir plus haut) ;
  - **le commercial informatique du client** + Clifford en copie quand le
    client n'a pas de contrat Info (voir plus haut) — à défaut `cdamato@dfm.fr` ;
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

## L'agent sur Azure — quand ce n'est plus moi qui passe

`agent_mail_dit.py` (racine du repo) fait ce passage **tout seul, dans le
conteneur**, sans le PC d'Elliot. Demandé le 2026-10-02 : « je veux que ce soit
TOI sur Azure en auto sans mon PC ». Ce n'est pas le robot à règles
(`robot_mail_dit.py` / `mail_dit.decider_seul`) : c'est une boucle d'outils,
avec ce fichier-ci en consigne système.

**La consigne de l'agent EST ce skill.** `assembler_consigne()` concatène
`SKILL.md` et les quatre références. Donc :

> Toute modification de ce skill doit être republiée sur `/data`, sinon l'agent
> continue avec l'ancienne règle :
>
> ```powershell
> & $py agent_mail_dit.py --consigne "<scratchpad>\agent_consigne.md"
> & $az storage file upload --account-name dfmmspdashboardstor --share-name msp-data `
>      --source "<scratchpad>\agent_consigne.md" --path agent_consigne.md `
>      --auth-mode login --enable-file-backup-request-intent --only-show-errors
> ```
>
> `.dockerignore` exclut `.claude/` : le skill n'est **pas** dans l'image, et
> l'agent lit `/data/agent_consigne.md`. Un déploiement seul ne suffit donc pas.

### Ce que l'agent ne peut pas faire, par construction

Les garde-fous sont en Python, pas dans la consigne — une consigne se contourne
par une tournure de phrase, pas par un `if` :

| Garde-fou | Où |
|---|---|
| 10 créations par passage | `Contexte.peut_creer` |
| 60 s entre deux DIT du même client | `Contexte.cadencer`, vraie attente |
| anti-doublon | `E.creer_dit`, inchangé, sans échappatoire |
| destinataire d'un transfert | `_transferer` : l'outil prend un **motif**, jamais une adresse |
| commercial d'un client sans contrat | `E.commercial_informatique` : lu dans Artis, vérifié dans l'annuaire |
| signature `Service MCO - Mail automatique` | ajoutée par `_transferer` |
| type de travail Hotline / Non facturable | `_poser_type_travail`, après le rapport, une seule session web |
| suppression de mail | aucun outil ne l'expose ; `corbeille` est hors de `DOSSIERS_PERMIS` |

### Modes et coût

`AGENT_MODE=observation` (défaut) : tout est lu, rien n'est écrit, `creer_dit`
passe quand même par l'anti-doublon. `AGENT_MODE=actif` : écrit, toujours sous
le drapeau `/data/mail_dit_ecriture.txt`.

**Un passage coûte environ 0,90 € sur Sonnet** (mesuré le 2026-10-02 : 7 mails,
31 tours, 196 s). D'où `_boite_inchangee()` : on ne paie que si la boîte a bougé
depuis le dernier passage. Sans ce garde-fou, un mail laissé en boîte serait
réanalysé — et repayé — tous les quarts d'heure.

Budget : `AGENT_PLAFOND_EUR`, compteur mensuel sur
`/data/agent_mail_dit_usage.json`. Plafond atteint → `disponible()` est faux,
l'agent s'arrête, le robot à règles continue seul.

### Le voir tourner

Les logs du conteneur sont illisibles depuis le poste d'Elliot (voir
`references/pieges-artis.md`). Trois fichiers sur `/data` disent tout :

| Fichier | Ce qu'il dit |
|---|---|
| `agent_mail_dit_etat.json` | ce qu'il a décidé au démarrage (clé, budget, consigne) |
| `agent_mail_dit_battement.json` | le dernier passage, avec son rapport complet |
| `agent_mail_dit_usage.json` | la dépense du mois, détail du cache |
