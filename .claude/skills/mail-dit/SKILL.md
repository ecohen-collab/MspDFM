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
6. **Finir le passage** : `python3 finir_passage.py --retard`. Une **seule**
   session web pour tout — le type de travail Hotline / Non facturable, les
   tags de validation et les accusés de réception. Le mode `--retard` prend
   les créations **du passage en cours ET celles qui traînent**, en relisant
   le journal de prod et le journal local : une DIT laissée en
   `Terrain` / `Automatique` ne se signale nulle part et part en facturation,
   où un dispatcher la planifie comme une intervention terrain. Trois DIT y
   ont échappé le 2026-10-15.
7. **Rapport** : créé / complété / rangé / laissé, une ligne de raison chacun.
   Rien d'autre : pas de synthèse, pas de tendance, pas de recommandation.
8. **Journaliser chaque action** — voir ci-dessous.
9. **Si une règle a changé pendant le passage, la publier** — depuis le poste
   d'Elliot seulement : `python publier.py --verifier` dit ce qui est écrit ici
   et que le Cloud n'a pas. Une règle qui reste sur le disque ne vaut que pour
   moi : la routine Cloud lit le **dépôt**, pas ce poste. Le 2026-10-15, deux
   références y dormaient depuis des jours — la priorité `01` sur les incidents
   de sécurité et les `PC-INC-*` en HLI — pendant que la routine créait des DIT
   sans elles. `publier.py` committe, pousse et republie la consigne sur
   `/data` en une fois.

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
`annulation`, `laisse`, `piece`, `confirmation`. Une action hors liste lève une erreur —
c'est voulu : une ligne au type inconnu serait invisible dans les filtres.

Le fichier est en **append** : jamais de réécriture, jamais de verrou. Deux
écrivains concurrents s'intercalent, ils ne s'effacent pas.

### Depuis le PC d'Elliot, le journal n'arrive PAS tout seul sur le dashboard

`DATA_DIR` n'existe pas sur le poste : `mail_dit_journal.FICHIER` retombe sur
**la racine du repo**, pas sur `/data`. L'onglet « Journal mail vers DIT » lit,
lui, `/data/mail_dit_journal.jsonl` sur Azure Files — donc tant qu'on ne pousse
rien, il affiche les lignes de l'agent Azure et **aucune** des miennes. Constaté
le 2026-10-06 : 939 lignes locales, 118 en prod, Elliot ne voyait « rien ».

**Fin de passage, après le type de travail** : fusionner et téléverser.

1. télécharger `/data/mail_dit_journal.jsonl` dans le scratchpad ;
2. fusionner avec le fichier local en dédoublonnant sur
   `(ts[:19], action, mail_de, mail_objet, dit, dossier)`, puis trier par `ts` —
   l'agent Azure écrit dans le même fichier, **ses lignes ne se perdent pas** ;
3. vérifier qu'aucune clé de la version prod n'a disparu de la fusion ;
4. téléverser par-dessus :

```powershell
& $az storage file upload --account-name dfmmspdashboardstor --share-name msp-data `
     --source "<scratchpad>\journal_fusion.jsonl" --path mail_dit_journal.jsonl `
     --auth-mode login --enable-file-backup-request-intent --only-show-errors
```

Jamais d'`upload` sans la fusion : le fichier de prod serait écrasé par le seul
local, et les passages de l'agent disparaîtraient.

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

### Le contrat peut être porté par une autre société du groupe

**`bien_dit` à `None` ne prouve pas l'absence de contrat : il prouve l'absence
de contrat *dans cette fiche*.** Un groupe peut faire porter l'infogérance de
toutes ses filiales par une seule entité, et c'est alors chez elle que vivent
les biens.

**CLINIQUE DES CHAMPS-ELYSEES — toutes les villes — est le cas connu.** Le
contrat est chez **ELITE CENTER (12930)**, dont les 474 biens comportent un
trio INFOGERANCE / BAL EXCHANGE 365 / PROTECT **par ville**, chacun sur son
propre site et préfixé du **code client de la clinique** :
`14288 - INFOGERANCE RENNES` (site 006), `10721 - INFOGERANCE LILLE` (016),
`5535-INFOGERANCE PARIS` (017), `12769 - INFOGERANCE NICE` (003),
`13573 - … BORDEAUX`, `14232 - … LYON`, `14287 - … TOURS`,
`14323 - … LA BAULE`, `15327 - … SEBASTOPOL`, `19002 - … MONTPELLIER`, plus
DIJON, MARSEILLE, CANNES, REUILLY, STRASBOURG, TOULOUSE, AIX, REIMS, CAEN,
LEVALLOIS et BIARRITZ.

Donc, pour ces clients : **on crée la DIT chez la clinique qui écrit, et on ne
transfère JAMAIS au commercial.** Elliot, le 2026-10-12, après que j'ai
transmis à `jktorza@dfm.fr` un mail de la clinique de Rennes : « c'est faux car
toutes les cliniques des champs elysées sont couvertes par ELITE CENTER qui lui
a bien un contrat ».

**Le bien posé est celui du porteur** — tranché par Elliot le 2026-10-12 entre
trois options : « DIT avec le bien d'ELITE CENTER ». Bien et site exacts, donc
contrat juste pour la facturation ; la contrepartie est connue et acceptée :
`identifiantFabricant` étant une clé globale et `POST /dit` suivant le **site
du bien** (le cas XILAM, plus bas), **le ticket s'affiche au nom d'ELITE
CENTER**, pas de la clinique qui écrit. Rien ne se corrige après création — ni
le bien, ni le site.

C'est automatique, et dans le code plutôt que dans cette consigne :

| Où | Quoi |
|---|---|
| `mail_dit_ecriture.CONTRAT_PORTE_PAR` | la table filiale → porteur, seule copie, lue aussi par le robot à règles |
| `mail_dit_ecriture.biens_portes(code)` | les biens du porteur **préfixés du code de la filiale** — sans ce filtre on poserait le bien d'une autre ville |
| `mail_dit_ecriture.bien_porte(code)` | `(identifiant, site, orgInt)`, modèles dans l'ordre habituel |
| `agent_mail_dit._bien_et_site` | passe par le porteur, et le `transferer` motif `sans_contrat` refuse toujours ces clients |
| `mail_dit.bien_informatique` | même règle pour le robot de nuit |

**Le code ne suffit pas à désigner la ville.** Chez ELITE CENTER, deux codes en
portent plusieurs : **10721** (LILLE, BORDEAUX, DIJON, MARSEILLE, STRASBOURG,
TOULOUSE, AIX, REIMS, CANNES, REUILLY) et **12769** (NICE, CAEN, LEVALLOIS).
La ville se lit donc dans la raison sociale de la filiale (« CLINIQUE DES
CHAMPS ELYSEES RENNES » → `14288 - INFOGERANCE RENNES`), et à défaut le premier
bien du modèle gagne. Quand plusieurs biens sont candidats, `creer_dit` l'écrit
dans ses `faits` (« VILLE A VERIFIER ») : **lire ce fait avant de créer**, car
après il est trop tard. Un mail de DIJON ou de MARSEILLE arrive sous le code
10721 et partira sur LILLE si personne ne regarde.

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
| `Cloturee` | **jamais de réouverture** : on commente et on transfère au dernier intervenant — voir ci-dessous |
| `Annulee` | seule exception : l'affaire a été retirée, une nouvelle DIT est légitime ; le dire dans le rapport |

### On ne rouvre JAMAIS un ticket clôturé

**Rouvrir coûte cher : un ticket rouvert retourne dans la file d'un technicien
qui avait fini.** Sur une DIT `Cloturee`, quel que soit le contenu du mail :

1. **poser le commentaire** — la trace doit rester sur le ticket ;
2. **transférer le mail au dernier intervenant** ;
3. **ranger en `01 - DIT ARTIS SUPPORT`** et le dire dans le rapport.

C'est **lui** qui décide ensuite de rouvrir ou d'ouvrir un ticket neuf. Pas
nous : on ne voit ni ce qu'il a fait, ni ce qu'il a déjà répondu au téléphone.

Elliot a dû le dire deux fois le 2026-10-05, et la deuxième est la règle :

- sur la DIT **04216844** (VILLA NOAILLES) : « la personne a répondu au ticket,
  il l'a rouvert. Il ne faut pas rouvrir tous les tickets dont on a une
  réponse, sinon on va se retrouver avec 1 milliard de tickets ré-ouverts pour
  rien, alors que c'est réglé pour beaucoup » ;
- sur la DIT **100176087** (JEAN-LOUIS DENIOT), où j'avais rouvert parce que la
  cliente demandait, en plus de l'accès déjà ouvert, le droit de **créer** des
  dossiers et des fichiers — donc, selon la table d'alors, « il redemande le
  travail » : « **dans ces cas là transfert juste le mail au dernier
  intervenant, ne réouvre pas** ».

Il n'y a donc plus de cas « le travail est à refaire → rouvrir ». Une demande
qui ressemble à du travail neuf **ressemble** seulement : le technicien, lui,
sait si c'est un complément de son intervention ou une demande à part.

Le garde-fou est dans le code, pas seulement ici : `completer_dit` n'appelle
plus `PATCH /dit/{n}/reouvrir` et le paramètre `rouvrir` a disparu de son
schéma.

Erreur du 2026-09-30, toujours valable : mail `RE: [IT 10017547001]`, ticket
100175470 vu `Cloturee`, j'en ai créé un second (100175881). **Un doublon reste
pire qu'un silence** — si le travail est bien à refaire, on rouvre, on ne
recrée jamais.

Un mail **au sujet neuf, sans numéro dans l'objet**, n'est pas une réponse —
même s'il ressemble à un incident déjà traité. Un symptôme qui se reproduit
après clôture donne un nouveau ticket.

Une **relance sans information nouvelle ne produit rien** : on range, c'est tout.

### Un mail, plusieurs demandes : le numéro place la RÉPONSE, pas tout le mail

**Le numéro dans l'objet dit à quel ticket appartient la réponse du client. Il
ne dit pas que tout le contenu du mail y va.** L'unité n'est pas le mail, c'est
**la demande** : un mail peut en porter zéro, une, ou trois.

Le cas qui l'a montré, le 2026-10-09 sur la DIT **100175753** (SFAF). Karine
DOGNIN SAUZE répond sur le fil de validation et fait **deux choses opposées**
dans le même message :

1. « Je vous confirme que cette demande est refusée » — c'est la **conclusion**
   du ticket : l'accès de Mme Priouret à `presidence@sfaf.com` ne se fera pas ;
2. « Par ailleurs, pourriez-vous… » — trois tâches **neuves**, sur d'autres
   personnes (Martine Léonard, Valérie Mercier) et d'autres objets (droits sur
   les espaces drive SFAF-DG / SFAF-CA, rappel téléphonique).

J'ai tout collé en commentaire de 100175753. Elliot : « la cliente a refusé,
puis a fait une nouvelle demande dans le même mail ; l'IA a juste rajouté la
nouvelle demande en éditant, mais il ne fallait absolument pas ici ». Le ticket
porte un objet qui parle de la boîte de Mme Priouret : le technicien l'ouvre,
lit le refus, clôt — et emporte les trois demandes avec lui. Elles ne sont dans
aucun objet, aucune file, et ne sont pas facturables.

Donc, devant un mail sur un fil de ticket, **lire le contenu avant d'aiguiller**
et se poser deux questions dans cet ordre :

- **le mail ferme-t-il la demande d'origine ?** (refus de validation, « finalement
  non », « c'est résolu ») → le commentaire porte cette conclusion, et rien
  d'autre ne doit s'y ajouter ;
- **reste-t-il des demandes qui ne sont pas celle du ticket ?** (autres
  bénéficiaires, autre objet, autre geste) → **DIT neuve pour celles-là**, avec
  leur propre objet.

Le cas ordinaire ne change pas : un client qui précise, relance ou complète
**sa** demande reste un commentaire sur son ticket. Ce qui déclenche la
scission, c'est un contenu qui parle d'**autre chose** que l'objet du ticket.

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
5. **Déposer une copie du mail dans `BAL INFO/16 - REPONSE DIT`** — c'est là
   que l'équipe suit les réponses des clients sans ouvrir Artis. Elliot, le
   2026-10-13 : « lorsque le client répond à une DIT et que tu transfères au
   dernier intervenant, faudrait aussi que tu transfères une copie du mail
   dans le dossier 16 - REPONSE DIT pour qu'on puisse le suivre ». C'est une
   **copie**, pas un détour : l'original continue vers `01 - DIT ARTIS
   SUPPORT` (ou `14 - VALIDATION CLIENT`). Elle se prend **avant** le
   rangement — `ranger_mail` déplace le message et son identifiant change.
   `mail_dit_ecriture.copier_mail(mail_id, "reponse_dit")` le fait, appelé
   par `completer_dit_direct` et par le robot de nuit ; la copie est posée
   même quand le transfert a échoué, puisque c'est là que le suivi manque le
   plus.

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
  compris**, malgré son symptôme `ADM-365-GES`, et **tout incident**, même s'il
  faut ouvrir une console pour le diagnostiquer ; `HLN3` pour le **geste
  d'administration** lui-même (créer ou supprimer un compte, licences,
  délégations, MFA, onboarding, offboarding). `PC-INC-*` et `PC-DEM-*` vont en
  HLI par défaut. Le préfixe du symptôme ne décide pas, la console non plus :
  voir `references/priorites-uo-biens.md`.
  Posée sur le numéro d'**intervention** = numéro de DIT + `01`.
- **Bien** : on balaye les **sites** du client, le **principal d'abord**, puis
  les autres ; dans chaque site on cherche **INFOGERANCE**, à défaut **BAL
  EXCHANGE 365**, à défaut **PROTECT** ; rien dans ce site → site suivant. Le
  premier qui répond gagne, **et c'est SON site qu'on pose** — jamais un site
  calculé à part. Règle d'Elliot, le 2026-10-09 : « tu mets toujours le bien du
  SITE principal (et évidemment toujours du même client) INFOGERANCE, si tu
  trouves pas BAL EXCHANGE, sinon PROTECT, et si toujours rien tu passes au
  site suivant ».

  Le tri porte sur **`modele.code`, exactement** — jamais sur « le libellé
  contient INFOG ». Constantia Jeanne d'Arc (17661) n'a que de la sécurité
  (centrales VERSO+, NVR, télésurveillance) plus un bien
  `INFOGERANCE **AUDIO**` : l'ancien filtre le déclarait sous contrat
  d'infogérance informatique. Il rend `None` désormais, c'est-à-dire « pas de
  contrat Info », et le mail part au commercial.

  Jamais vide — sauf client qui n'a aucun des trois, et alors on le signale.
  **Le champ ne se rattrape pas après coup** : il n'est ni dans `GET /dit/{n}`
  ni dans aucune route d'écriture hors création, et une DIT créée sans lui part
  avec le seul site, donc « Pas de bien » à l'écran. Elliot l'a relevé le
  2026-10-08 sur la DIT **100176820** (NMCG 30706, qui a pourtant
  `30706 - INFOGERANCE`) : « pourquoi tu as mis pas infogérance ? ». Le
  garde-fou est désormais dans le code — `creer_dit` appelle
  `_bien_du_client()` quand l'appelant ne passe pas de `bien`, et dit dans ses
  `faits` lequel il a posé, ou qu'il n'y en a aucun.

  **`identifiantFabricant` est une clé GLOBALE, pas une clé du client — et le
  site suit le bien.** C'est le piège le plus coûteux de `POST /dit` : on
  envoie `codeClient`, `codeSite` **et** `identifiantFabricantBien`, et Artis
  ne garde que le site du **bien**. Un identifiant qui appartient à un autre
  client emmène donc son site avec lui, en silence, et le ticket s'affiche au
  nom de ce client-là.

  Le 2026-10-07, **sept DIT** sont parties avec le bien `INFOGERANCE` — le
  libellé nu, sans préfixe — qui est l'identifiant du bien du client **14630
  XILAM**, contrat fermé depuis août 2024. Résultat : EKILIBRE, MOBILITE
  MUTUELLE, GALYO, TP 2000, MYMOBILITY (×2) et l'ACJE portent toutes le site
  « XILAM, 57 boulevard de la Villette, Paris 10e ». Elliot, le 2026-10-09 :
  « attend, comment il a fait pour mettre XILAM ? c'est même pas un site du
  client ?? ».

  La cause était dans nos propres outils : `client` rendait ses biens **par
  libellé** et le schéma de `creer_dit` disait « libelle exact rendu par
  l'outil client ». Le bon champ était `bien_pour_la_dit`, juste au-dessus.
  Trois garde-fous depuis :

  - l'outil `client` rend `identifiant | libellé`, plus jamais un libellé nu ;
  - **tout `bien` passé par l'appelant est vérifié contre la fiche du client**
    (`_bien_du_client(E, code, valide=…)`) ; s'il n'y figure pas il est jeté,
    remplacé par le bon, et le fait est écrit dans les `faits` ;
  - **après création, la DIT est relue** et le site posé est comparé à la
    **liste des sites du client** — pas à son libellé, un client ayant souvent
    des sites qui portent un autre nom (ARCOTEL 13392 a « HOTEL MAGDA »). Site
    étranger → on le dit tout de suite, car rien ne se corrige ensuite.

  Ni le site ni le bien ne sont réparables par l'API : `GET /dit/{n}` ne les
  écrit pas, et l'intervention n'expose que `annuler`, `planifier`, `debuter`,
  `terminer`. Un ticket déjà parti se corrige à l'écran, ou pas du tout.
- **Symptôme** introuvable → `PC-DEM-AUT` / `PC-INC-AUT`, et on le signale.
- **Onboarding** (arrivée d'un salarié) → `ADM-ON` + `HLN3`.
- Après création : type de travail **Hotline** + facturation **Non facturable**,
  par le formulaire web, une seule session pour tout le lot.

## La confirmation de réception au client

**Toute DIT créée part avec son accusé de réception au demandeur.** Elliot, le
2026-10-13 : « j'ai beaucoup de clients qui demandent si leur demande a bien
été prise en compte […] il faudrait aussi cocher cette option quand tu fais une
création », puis, devant un envoi manqué : « **faut TOUJOURS envoyer** ». Sur
les créations seulement — jamais sur un complément.

Rien de cela n'est dans l'API : `POST /dit` n'a aucun champ de confirmation.
C'est le bouton enveloppe de la **visu DIT** qui le fait, et
`artis_type_travail.envoyer_confirmation_reception(numero)` le rejoue — dans la
**même session web** que le type de travail et les tags, donc sans siège de
licence en plus. `agent_mail_dit._poser_type_travail` l'appelle pour chaque
création du passage, et la journalise sous l'action `confirmation`.

Trois choses se sont payées pour en arriver là :

- **le type d'échange** : celui du bouton (« Confirmation réception ») envoie
  un objet nu, `[DIT n] Confirmation de réception`. DFM en a un autre,
  « 'Confirmation de réception' avec mail SUPPORT » (`TYPE_CONFIRMATION_SUPPORT`),
  qui rend l'objet maison « DFM - Informatique - Demande d'intervention N -
  Confirmation de réception », celui auquel les clients répondent déjà. Il ne
  s'obtient pas par `echangeData` — le serveur n'y reconnaît que ses libellés
  internes et retombe sur « Sollicitation » — mais en le choisissant dans le
  select puis en appelant `mails/changeTypeEchange.action` ;
- **l'expéditeur** : `cbo_mailFrom` ne liste pas des boîtes mais les adresses
  du **compte Artis connecté**. Le compte MCO écrivait depuis
  `service.client@dfm.fr`, et trois essais (type SUPPORT, champ omis, adresse
  postée en clair) n'y ont rien changé : Elliot a mis `support@dfm.fr` comme
  adresse du collaborateur « Service MCO » le 2026-10-13, et c'est ce qui a
  réglé le problème. `EXPEDITEUR_ATTENDU` le vérifie à chaque envoi et **refuse
  de partir** si l'adresse n'est pas dans la liste — un client qui répondrait à
  `service.client` écrirait dans une boîte que personne ne relève ;
- **le destinataire** : `it_mailTo` est alimenté par un index d'interlocuteurs
  qu'Artis ne rafraîchit pas tout de suite, donc un contact **créé dans la
  minute** n'y est pas et la modale s'ouvre sans destinataire — alors que la
  DIT a bien son demandeur, que le corps nomme. Le select acceptant la saisie
  libre, `destinataire_si_absent` retombe sur l'adresse du mail d'origine.

## Le circuit de validation client — le Tag

Certains clients exigent qu'un tiers de chez eux **valide** avant qu'on touche
à quoi que ce soit : des droits, un accès, l'ouverture d'un site. Artis le
matérialise par un **Tag** sur la DIT, posé à la main jusqu'ici. Demandé par
Elliot le 2026-10-06 sur la DIT **100176480** (accès SAGE PAIE chez
MYMOBILITY, taguée par Shannon) : « tu peux à la création mettre le tag en
attente validation et ensuite quand on reçoit un mail de validation du client
changer le tag pour mettre Demande validée ? »

1. **À la création**, si le client a un valideur et que la demande porte sur un
   de ses motifs → Tag **`VALIDATION`** (« En attente de validation client »).
2. **Quand le valideur répond**, sur le fil du ticket → on pose son mail en
   commentaire, on prévient l'intervenant comme d'habitude, et le Tag passe à
   **`VALIDATION CLIENT`** (« Demande validée »).
3. **Et le mail se range en `BAL INFO/14 - VALIDATION CLIENT`**, pas en
   `01 - DIT ARTIS SUPPORT` : c'est là que l'équipe va chercher les accords.
   Elliot, le 2026-10-06 : « tu pourras ranger les mails de validation clients
   dans 14 - Validation clients au lieu de ARTIS DIT SUPPORT ». La clé de
   rangement est `validation` ; `completer_dit` la choisit tout seul quand
   l'expéditeur est le valideur du client, **y compris si le ticket est
   clôturé** — le rangement n'écrit rien dans Artis, seul le Tag s'arrête aux
   tickets ouverts.

Trois garde-fous, posés par Elliot le même jour :

- **on ne touche pas aux anciens tickets** — la règle ne vaut que pour les DIT
  créées à partir de maintenant, jamais rétroactivement ;
- **on ne touche pas à un ticket clôturé** — ni pour poser le Tag, ni pour le
  changer : c'est fini, on laisse ;
- **aucune phrase de nous dans le commentaire.** J'avais ajouté entre
  parenthèses « réponse de Luis JIMENEZ, dsi@mymobility.fr, le 05/10 à 18h38,
  à la demande de validation envoyée par Shannon COHEN » : « arrête de mettre
  ton commentaire entre parenthèse, **transmettre le mail du client est
  suffisant** ». Le texte du valideur, mot pour mot, et rien d'autre.

Le Tag **n'est pas dans l'API** : ni dans `GET /dit/{n}`, ni dans le corps de
`POST /dit`, et aucune route `/tag` n'existe. Il vit dans un
`<select multiple name="dt_tagsIt">` de l'écran `ccDit`, et se pose par
`artis_type_travail.poser_tag(numero, ajouter=…, retirer=…)` — **dans la même
session web que le type de travail**, donc sans siège de licence
supplémentaire. Les tags déjà posés sont conservés.

### Le circuit vaut pour TOUS les clients, pas seulement ceux qui ont un valideur

**Toute demande de droits, de délégation ou d'accès part avec le Tag « En
attente de validation client », quel que soit le client.** Elliot, le
2026-10-09 : « tout ce qui est demande de droit, délégation, accès sharepoint
etc. doit faire l'objet d'une validation, donc faut mettre le tag en attente de
validation comme pour JLI (**pour tous les clients**) et passer en demande
validée quand on reçoit la confirmation du client ».

Avant, le circuit ne regardait que les clients inscrits dans `REGLES_CLIENT` :
un client absent de la table était **invisible** pour tout le mécanisme, et sa
demande de droits partait sans tag. C'est ce qui est arrivé à la DIT
**100177064** (SFAF). Désormais c'est le **motif** qui déclenche, pas
l'appartenance à une table — `MOTIFS_A_VALIDER` dans `mail_dit_ecriture.py` :
droits, accès, habilitation, autorisation, délégation, SharePoint, OneDrive,
drive, boîte mail, partage, privilège, administrateur, MFA, **licence**.

**Une licence se paye : l'ajouter engage le client, donc elle se valide comme
un droit.** Elliot, le 2026-10-13, sur la DIT **100177444** (5 licences Kiosk
sur le tenant mymob.fr) : « demande d'ajout de licence : faut une validation
(dans le cas de JLI, préciser qu'il faut "demande validée" automatiquement si
c'est Luis JIMENEZ) ». Le motif rejoignant ceux du valideur, c'est automatique
dans les deux sens : la demande d'un tiers part « En attente de validation
client », celle du valideur lui-même part « Demande validée ».

La table ne sert plus qu'à nommer **qui** valide chez les clients qui ont
désigné quelqu'un, et elle change deux choses :

- un mail **du valideur lui-même** part en **« Demande validée » tout de
  suite** : il n'a personne à qui soumettre sa demande, c'est lui qui autorise.
  Elliot, le 2026-10-09 : « pour JLI, si jamais le demandeur c'est LUIS, mets
  directement le tag demande validée ». Avant, ce cas ne posait **aucun** tag
  et le ticket partait nu — l'équipe ne pouvait pas distinguer « autorisé » de
  « pas encore demandé » ;
- **sa réponse vaut accord sans qu'on lise le texte**. Chez un client qui n'a
  désigné personne, n'importe lequel de ses interlocuteurs peut confirmer,
  mais le texte doit alors porter un accord (`MARQUES_ACCORD`) — sinon une
  simple question ferait passer le tag en « Demande validée » et l'équipe
  lancerait un travail non autorisé.

**Un signalement de mail frauduleux ne passe jamais par le circuit de
validation.** Le client qui demande « est-ce que ce mail est un phishing ? »
ne demande aucun droit : il demande une analyse, et le tag « En attente de
validation client » met son ticket en attente d'un accord que personne n'a à
donner. Elliot, le 2026-10-14, sur la DIT **100177663** (FACTORHY, mail de
mise en demeure douteux, où le faux site affichait « Votre adresse IP doit
être autorisée avant l'accès ») : « phishing potentiel, il n'y avait pas
besoin de demande de validation ». Le tag a été retiré, et
`_SIGNALEMENT_FRAUDE` coupe désormais le circuit avant tout motif — frauduleux,
phishing, faux mail, usurpation, arnaque.

**Un accord se dit rarement avec les mots qu'on attend.** `MARQUES_ACCORD`
a dû s'élargir deux fois : « Oui, merci » (PRESENCE CONSEIL, DIT 100177359),
puis « **Vous pouvez traiter cette demande** » — Mohammed KASSAINI, ALIGN
AERO, le 2026-10-14 sur le fil de validation de la DIT **100176803** : aucune
marque ne l'a reconnu, le ticket est resté « En attente de validation client »
et le mail est parti en `01` au lieu de `14`. Les deux ont été repris à la
main. Devant un fil « Demande de validation », lire la réponse soi-même : si
elle autorise, le tag passe à « Demande validée » et le mail va en `14`, que
l'expression figure ou non dans la table.

**Un refus ne vaut jamais validation, même écrit par le valideur désigné.**
Sans ce garde-fou, la réponse de Karine DOGNIN SAUZE — « je vous confirme que
cette demande est refusée » — aurait fait passer la DIT 100175753 en « Demande
validée », c'est-à-dire exactement l'inverse de ce que la cliente demandait.

Clients ayant désigné un valideur aujourd'hui : **MYMOBILITY** (9599 et 9600),
dont la fiche porte « Pour demande de droits, ouverture de sites - Validation
par M. DUCLAUX ou M. JIMENEZ, email : dsi@mymobility.fr », **SFAF** (8280),
où Clifford adresse ses demandes de validation à Karine DOGNIN SAUZE, et
**FREITAS LEVAGE** (12390), dont la DG **Stéphanie FREITAS**
(`stephanie_freitas@freitas.fr`) décide seule : ses propres demandes partent
en « Demande validée », et **RUNIMMO** — les quatre sociétés du groupe sous
contrat Info (**14329**, **15042**, **15043**, **17038**) — où **Élodie
CORJON** (`e.corjon@runimmo.fr`) écrit et autorise. Elliot, le 2026-10-14, sur
la DIT **100177533** (mise en boîte partagée de `m.schuller@runimmo.re` sur
celle de `c.cassar@runimmo.re`, plus changement de mot de passe), partie « En
attente de validation client » : « ce ticket doit être validé à la création
100177533, le ticket a été fait directement par le valideur ». La fiche **20226
(« runimmo gestion »)** n'est pas du circuit : ni contrat Info, ni Élodie
CORJON dans ses interlocuteurs. Elliot, le 2026-10-13, sur la DIT **100177181**
(arrivée de Mme ANTUNES, création de boîte et trois sous-délégations) :
« il aurait dû arriver en validé, c'est le DG qui fait la demande ». Le
« Commentaire fiche client » d'Artis, où le client l'écrit, n'étant pas exposé
par l'API, cette table se remplit à la main quand un circuit se révèle.

## Le commentaire

L'objet du mail en première ligne, puis **le texte du client mot pour mot**.
Jamais de reformulation, même meilleure. Signature exclue. Historique du fil
conservé sous un séparateur. Mail en anglais : on le garde en anglais.
Normalisation ISO obligatoire (`E._stockable`) — Artis stocke en ISO-8859-15 et
supprime en silence ce qu'il ne sait pas encoder.

Aucun avis de ma part dans le ticket. Ce qui est sensible se dit à Elliot dans
le rapport, pas dans le commentaire.

**Pas de parenthèse explicative non plus — même factuelle.** Elliot, le
2026-10-06, sur la DIT 100176480 où j'avais ajouté sous le mot du valideur
« (réponse de Luis JIMENEZ, dsi@mymobility.fr, le 05/10 à 18h38, à la demande
de validation envoyée par Shannon COHEN le même jour à 18h03) » : « arrête de
mettre ton commentaire entre parenthèse, **transmettre le mail du client est
suffisant** ». Qui a écrit, quand, en réponse à quoi : la bannière d'ajout et
le mail transféré à l'intervenant le disent déjà. Le commentaire ne porte que
le texte du client.

**Un commentaire déjà posé ne se repasse pas au propre.** Elliot, le
2026-10-01 : « non, touche pas si clôturé ». Rouvrir une DIT close pour
reformuler un texte que le technicien a déjà lu coûte plus que ça ne rapporte —
la règle du mot pour mot vaut pour ce qu'on écrit, pas pour ce qui est écrit.

Une capture d'écran annoncée par le client (« voici le message qui s'affiche »)
se joint au ticket **et** se retranscrit en texte dans le commentaire : le
technicien lit le ticket, pas forcément les pièces.

## La session web Artis — ne jamais l'ouvrir soi-même

**Tu n'as aucune raison d'ouvrir une session web toi-même.** Un seul appel en
ouvre une, `agent_mail_dit._poser_type_travail(ctx)`, et il fait tout ce qui
en a besoin **dans la même** : le type de travail Hotline / Non facturable,
les tags de validation, et les accusés de réception. Appelle-le une fois, à la
fin du passage, avec toutes les créations du lot. Si tu te retrouves à écrire
`Web.ouvrir()`, `Web.connecter()` ou `session_dediee()` à la main, c'est que tu
as pris le mauvais chemin : reviens à `_poser_type_travail`.

### Pourquoi c'est à ce point verrouillé

**Chaque connexion consomme un siège de licence global à DFM, et ce siège ne se
rend pas.** Il n'existe aucune déconnexion utilisable : `disconnect.action` et
`PT.action?disconnect=<session>` répondent tous deux HTTP 200 et laissent la
session **pleinement vivante** — vérifié le 2026-09-10 en rejouant une action
complète après coup. Le siège se libère à l'expiration, pas avant. Donc une
session ouverte pour rien bloque un collègue pendant tout ce temps, et
enchaîner les connexions bloque l'équipe entière.

### « Erreur — ouvrir une nouvelle session » ne veut pas dire que la session est morte

**C'est le piège le plus coûteux du module, et il oriente vers un faux
problème.** `shared/form/flush.action` et `shared/form/save.action` attendent un
champ **`componentName`** qui ne figure pas dans le formulaire : la fonction
`ajaxFormParams` du bundle JS l'ajoute à la main, avec `cKey`, `cStatus` et
`session`. S'il manque, les deux répondent la page « Erreur — ouvrir une
nouvelle session » **alors que tout le reste de la session fonctionne
parfaitement**.

Devant ce message, donc : **ne te reconnecte pas**. Une reconnexion coûte un
siège de plus et ne répare rien, puisque le paramètre manquera encore. Les
fonctions du module passent déjà les bons champs — si tu vois ce message, c'est
que tu as reconstruit la requête à la main.

### Une saisie laissée ouverte bloque toute la navigation

Après une écriture, Artis reste **en mode saisie** tant qu'on n'en sort pas :
`userEvent=oK_Enregistrer` pour valider, `evtSortieChemin` pour abandonner.
Une saisie laissée en cours bloque ensuite **toute** la navigation de la
session — les écrans suivants répondent des erreurs qui n'ont aucun rapport.
Une session qui « ne répond plus » après une écriture réussie est presque
toujours une saisie non refermée, pas une session morte.

### La session se réutilise — par le relais, depuis le 2026-10-15

`Web.ouvrir()` relit la session du compte MCO dans
`<DATA_DIR>/artis_mco_session.json` et ne se reconnecte que si elle est morte.
Ce fichier est désormais le **quatrième du relais**, en lecture et en
écriture : tu reprends donc la session déjà ouverte au lieu d'en créer une, et
tu peux la renouveler quand elle meurt. `azure_files` s'en charge dès que
`RELAIS_URL` et `RELAIS_CLE` sont posées — tu n'as rien à faire de plus.

**Pourquoi ça compte.** Artis n'a pas de déconnexion utilisable : chaque siège
reste pris jusqu'à expiration. Tant que chaque passage ouvrait une session
neuve, les sièges s'accumulaient — et le 2026-10-15 un passage s'est vu
refuser la sienne, « nombre de sessions simultanées global atteint », laissant
deux DIT en `Terrain` / `Automatique`, sans tag ni accusé de réception. Une
licence saturée n'arrête pas que la chaîne : elle bloque toute l'équipe DFM.

La règle ne change pas pour autant : **une seule session par passage**, à la
fin, par `_poser_type_travail`. La différence est qu'elle ne coûte désormais
plus un siège de plus.

### Si la session échoue quand même

Ne boucle pas sur des reconnexions : chaque tentative prend un siège. Fais une
seule tentative, et si elle échoue, **dis-le dans le rapport** en nommant les
DIT concernées. Elles restent en `Terrain` / `Automatique` et sans accusé de
réception : c'est réparable à la main en une minute, alors qu'une licence
saturée arrête toute l'équipe.

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

  **Creuser le fil est une obligation, pas un réflexe optionnel.** Elliot, le
  2026-10-05, sur la DIT **100176371** (TECHNIFRUIT, mail de `cgallot`) : « tu
  n'as pas mis de demandeur car c'est une demande d'un DFM, par contre il a
  relayé une demande qui venait du client, donc il faut que tu creuses
  l'échange pour mettre le bon ». Trois endroits, dans cet ordre :

  1. le **corps entier** du mail, jamais les 1 000 premiers caractères — le
     bloc transféré vit sous la signature ;
  2. le **fil Graph** par `conversationId`, pas par objet :
     `GET /me/messages?$filter=conversationId eq '<id>'` ramène aussi les
     messages déjà rangés dans un autre dossier ;
  3. une **recherche** sur le nom du client dans toute la boîte
     (`$search="<client>"`) : le fil d'origine est souvent un autre objet.

  Seulement si les trois ne donnent rien : DIT sans demandeur, et on le dit
  dans le rapport **en précisant qu'on a cherché**.

  **Le pid explicite prime sur le garde-fou de l'expéditeur.** Le piège était
  dans le code : `creer_dit` jetait `pid_interlocuteur` dès que l'expéditeur
  finissait par `@dfm.fr`, y compris quand on lui passait **le contact client
  trouvé dans le fil**. Résultat, trois tickets du 2026-10-05 créés sans
  demandeur alors qu'on l'avait identifié (100176325 ARCOTEL, 100176326 ARM
  REPAR, 100176368 HYLA). Corrigé : le garde-fou ne supprime plus que le
  demandeur **déduit de l'expéditeur**, et le `pid_interlocuteur` passé
  explicitement est réappliqué après.

  **Le demandeur ne se rattrape PAS après coup** — vérifié le 2026-10-05.
  Aucune route REST ne l'écrit hors création (`POST /dit` seulement, pas de
  `PATCH /dit/{n}`), et sur l'écran `ccDit` le champ `itp_ditDemandeur` est une
  **loupe quickSearch** : y écrire le libellé (« M. Paul TROUSSEAU ») passe le
  `flush`/`save` avec `Enregistrer_OK` mais le champ **se relit vide**, le
  serveur ne résolvant pas le texte en identifiant. Le `data-fq-id` de l'input
  porte le pid, il n'est pas un champ de formulaire. Donc : **poser le bon
  demandeur à la création**, sinon c'est une correction manuelle dans Artis.
- **Une demande relayée par un collègue reste une demande client** : elle
  donne une DIT, elle ne se traite pas de la main à la main. Même quand on
  saurait la faire soi-même — sortir un historique de tickets, par exemple —
  le ticket doit exister, sinon le travail n'est ni tracé ni facturable.
- **Demandeur manquant** : chercher l'adresse exacte, puis le nom de famille.
  Créer le contact seulement si c'est une vraie personne — jamais un expéditeur
  automatique, jamais un nom d'un seul mot. Toute création de contact est
  signalée.
- **Contact trouvé mais sans adresse → on pose celle du mail.** Artis n'affiche
  pas l'adresse du demandeur depuis le message : il la lit dans la **fiche de
  l'interlocuteur**. Un contact saisi à la main sans adresse donne donc une DIT
  dont le demandeur n'a pas de mail — constaté le 2026-10-06 sur la DIT
  **100176656** (Alexandre Therrouin, CAB'S INDUSTRIES : fiche avec le seul
  téléphone). Elliot : « il y a un mail avec une demande et le mail du
  demandeur, **juste tu l'ajoutes, et s'il existe pas tu le crées** ».
  `E.assurer_mail_interlocuteur(code, pid, mail)` le fait par
  `PUT /client/{code}/interlocuteur/{id}`, et `creer_dit` l'appelle tout seul
  avant de créer. Trois limites tenues par le code :
  - on **complète**, on ne remplace jamais : une fiche qui porte déjà une
    adresse garde la sienne, même différente de celle du message ;
  - **jamais une adresse `@dfm.fr`** dans la fiche d'un client ;
  - le PUT remplace la fiche entière, donc on renvoie tout ce qu'on a lu —
    sinon le téléphone et la fonction disparaissent.
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

  **L'accusé de réception d'une DIT créée n'est PAS un de ces envois, et il
  part toujours.** Il ne sort pas de la boîte support : c'est **Artis** qui
  l'émet, depuis la visu DIT, dans la session web du type de travail — le
  mail ne passe ni par Graph ni par `support@dfm.fr` en tant qu'expéditeur
  que nous pilotons. Une consigne de passage qui interdit « tout envoi de
  mail sauf les trois transferts internes » vise les mails que la chaîne
  écrit elle-même, pas cet accusé-là.

  Le 2026-10-15, la routine Cloud a créé la DIT **100177760** (CMD TROYES)
  puis s'est abstenue d'envoyer l'accusé, en jugeant que le rappel des
  interdits primait sur le skill. Lecture compréhensible, mais fausse : la
  règle d'Elliot du 2026-10-13 est « **faut TOUJOURS envoyer** », et un
  client qui n'a pas d'accusé rappelle pour savoir si sa demande est prise
  en compte — c'est précisément ce que l'accusé évite. L'accusé a été envoyé
  à la main ensuite.
- **Une seule session web Artis par passage** : chaque connexion consomme un
  siège de licence et bloque l'équipe. Grouper tous les `poser_type_travail` et
  `poser_commentaire` dans la même session ouverte. **Cette session n'est pas
  optionnelle** : sans elle les DIT restent en `Terrain` / `Automatique`
  (voir `references/priorites-uo-biens.md`). Une consigne de passage qui
  interdit « de toucher au type de travail par le formulaire web » vise le coût
  des connexions répétées, pas le résultat : la bonne lecture est *une* session
  en fin de passage pour tout le lot.
- **Mail sensible** (demande de mot de passe ou d'identifiants, code d'accès,
  destinataire grand public en copie) → signaler à Elliot, ne pas agir.

  **Mais un mot de passe que le client écrit lui-même dans son mail se
  recopie dans le ticket, tel quel.** Elliot, le 2026-10-13, sur la DIT
  **02301289** (SOFITEC, arrivée de Ruben EGLIZEAUD) où j'avais écrit
  « Pwd : communiqué par le client dans son mail du 06/08, non recopié ici » :
  « quand le client écrit un mot de passe dans le mail, faut aussi le mettre
  sur le ticket ; ne fais pas le "communiqué par le client dans son mail du
  06/08, non recopié ici" ». Le technicien ouvre le ticket, pas la boîte
  support : un identifiant qui n'y est pas est un identifiant qu'il faut
  aller rechercher, ou redemander au client. Le ticket est l'outil de travail
  de l'équipe, pas un document public.

  Ce qui reste interdit n'est pas d'écrire un secret dans le ticket, c'est de
  le **faire circuler** : pas de mot de passe dans un transfert, pas de
  secret recopié dans un mail.

  **« Signaler » ne veut JAMAIS dire « laisser en boîte ».** Un mail jugé
  sensible suit le circuit normal — le client est identifié, la demande est
  lisible, donc **la DIT se crée** avec le texte du client et rien d'autre.
  Ce qui t'inquiète se dit à Elliot **dans le rapport**, pas en laissant le
  mail sur place et surtout pas dans le ticket. Le 2026-10-15, un passage a
  laissé le mail de Mister IA / GCI en boîte avec la mention « sensible, à
  examiner par Elliot » : il demandait un consentement administrateur Entra,
  c'est-à-dire une demande d'administration ordinaire, qui devait donner une
  DIT comme les autres. Elliot : « on s'en fout, tu crées juste la demande
  avec le texte ».

  **« Ne pas agir » veut dire ne rien envoyer, pas ne rien tracer.** Une
  demande de mot de passe venant d'un client identifié est une demande
  ordinaire : **on crée la DIT**, et le technicien vérifie l'identité du
  demandeur avant de donner quoi que ce soit. Elliot, le 2026-10-05, sur le
  mail de Bernard PINGREE (INFOPAC 15521, laissé trois passages en boîte) :
  « pour bernard bah si en fait tu créer une DIT pour le client, c'est le
  technicien qui se chargera de vérifier que tout est ok ». Ce qui est
  suspect — webmail grand public en copie, tiers invoqué sans adresse,
  « conversation » avec un collaborateur dont on n'a pas trace — se dit à
  Elliot **dans le rapport**, et au technicien hors du commentaire.
  **Rien ne reste en boîte, pas même un secret.** Un code d'accès à usage
  unique reçu sur `support@dfm.fr` pour le compte d'un tiers — les codes
  FortiCloud destinés à `sav@bcsystem.fr`, un lien de connexion Claude.ai,
  une récupération de compte Gandi, un code de compte Microsoft personnel —
  ne porte aucune demande client : il ne donne donc **ni DIT ni transfert**,
  mais il se **range en `04 - INFORMATIONS`** comme tout le reste, avec sa
  raison, et ce qui est suspect se dit à Elliot dans le rapport.

  Elliot, le 2026-10-14, devant huit mails laissés en boîte passage après
  passage avec leur raison écrite : « il reste des mails dans la BAL pk ta
  pas touché ni rangé ? ». Écrire la raison dans le rapport ne remplace pas
  le rangement — un code est périmé en quelques minutes, le garder sous les
  yeux de l'équipe ne protège rien et la boîte ne se vide jamais. « On ne
  laisse RIEN en BAL » n'a pas d'exception.

### Campagne d'hameçonnage annoncée par la DSI → corbeille

**C'est la seule suppression autorisée, et elle demande une alerte DSI
nommant la campagne.** Le 2026-10-05, Fabien RAMIERI (RSI) a prévenu que la
messagerie du **Groupe MYMOBILITY** était piratée : des mails partent de
**vraies** adresses `@mymobility.fr`, dont `c.herbin@mymobility.fr`, avec
l'objet « Céline HERBIN has shared a file with your for your review », un faux
« Request for Proposal » et un lien `www.mymobility.fr/Business%Proposal%…docx`.
Signature et logo sont authentiques. Consigne d'Elliot le même jour : « méfie
toi si on reçoit ce mail sur la boîte support, **n'ouvre aucun lien et mets-le
en corbeille** ».

Donc, pour un mail couvert par une alerte DSI de ce type :

1. **aucun lien ouvert, aucune pièce jointe ouverte** — ni pour « vérifier » ;
2. **déplacement en corbeille** : `POST /me/messages/{id}/move`, corps
   `{"destinationId": "deleteditems"}`. C'est un déplacement, pas un effacement :
   le mail reste récupérable ;
3. **ni réponse ni transfert**, pas même à un collègue — la DSI l'interdit
   explicitement ;
4. **journaliser** le rangement avec `dossier="Elements supprimes"` et la
   campagne en détail, puis le dire dans le rapport.

Hors alerte DSI nommée, rien ne se supprime : un mail douteux isolé se range en
`04 - INFORMATIONS` et se signale. Et l'agent Azure, lui, **ne peut toujours
pas supprimer** : `corbeille` reste hors de `DOSSIERS_PERMIS` et aucun outil ne
l'expose. Cette manœuvre est manuelle, sur demande d'Elliot.

## Références

- `references/pieges-artis.md` — API, authentification, erreurs déjà payées
- `references/priorites-uo-biens.md` — codes, symptômes, règles de choix
- `references/dossiers-bal.md` — folderId des dossiers de rangement
- `references/clients-hors-index.md` — résoudre un client absent de l'index

## Outils

- `bin/lire_mail.py` — corps texte d'un mail depuis son internetMessageId
- `bin/artis.py` — helpers REST (auth, GET, biens, anti-doublon, interlocuteurs)
- `bin/creer_dit.py` — crée une ou plusieurs DIT depuis un JSON de propriétés

## Le même passage depuis une session distante (routine Cloud)

### La voie : `agent_mail_dit`, jamais les outils Outlook du connecteur

**Tout le passage se fait par `agent_mail_dit.executer()`. N'utilise PAS les
outils `outlook_*` du connecteur Microsoft 365, ni `read_resource` pour lire
les mails.** Préambule, une fois, en tête de passage :

```python
import json, agent_mail_dit as A, mail_dit_ecriture as E
import mail_dit_journal as J, mail_dit as M
ctx = A.Contexte(actif=True)
print(json.dumps(A.executer('boite', {}, ctx, M, E, J), ensure_ascii=False))
```

Puis `lire_mail`, `resoudre_client`, `client`, `dit`, `creer_dit`,
`completer_dit`, `ranger`, `transferer`, `signaler` — **17 outils**, tous par
le même `A.executer(nom, args, ctx, M, E, J)`.

**N'appelle JAMAIS `mail_dit_ecriture` ni `mail_dit` en direct.** Leurs
fonctions prennent un `pid` de *proposition* — l'identifiant d'une analyse
faite par le robot à règles, qui n'existe pas pour toi. En les lisant tu
conclurais, à tort, que rien n'est possible. Les outils, eux, prennent un
**`mail_id`** :

| Ce que tu cherches dans le code | L'outil qui le fait |
|---|---|
| `E.completer_dit(pid, …)` | `completer_dit` · `mail_id`, `numero`, `commentaire` |
| `E.transferer_mail(pid, a=…)` | `transferer` · `mail_id`, `motif`, `mot` |
| `E.ranger_mail(prop, …)` | `ranger` · `mail_id`, `dossier`, `raison` |
| `E.creer_dit(prop, …)` | `creer_dit` · `mail_id`, `client`, `objet`, … |

`completer_dit` fait **à lui seul** le commentaire, le transfert au dernier
intervenant, la copie en `16 - REPONSE DIT` et le rangement — tu n'as pas à
les enchaîner toi-même. Et `transferer` prend un **motif**
(`intervenant` · `sans_contrat` · `relais_humain`), jamais une adresse : c'est
lui qui résout le destinataire et signe.

**Ne reconstruis JAMAIS un envoi Graph à la main.** Pas de `createForward`,
pas de `/send`, pas de jeton recopié dans un script. Non seulement
`transferer` le fait déjà en signant et en vérifiant le destinataire, mais un
script qui rebâtit ces appels **se fait refuser par le classifieur
d'actions** — c'est ce qui a coupé le passage du 2026-10-15 en deux, laissant
trois DIT en `Terrain` / `Automatique` et sept mails en boîte. Le classifieur
a raison de bloquer : un script qui fabrique des envois de mail avec un jeton
en clair ressemble exactement à un contournement de garde-fou.

### Finir le passage : `finir_passage.py`

Type de travail, tags et accusés de réception, en **une** session web, par un
script versionné du dépôt :

```bash
cat > /tmp/fin.json <<'EOF'
[{"numero": "100177822", "de": "a.duvivier@sodes-sa.com"},
 {"numero": "100177824", "de": "lm.jimenez@orange.fr", "tag": "attente"}]
EOF
python3 finir_passage.py /tmp/fin.json
```

`tag` vaut `"attente"`, `"validee"`, ou s'omet. `de` sert de destinataire de
secours quand Artis n'a pas encore indexé le demandeur. Le script sort en
code 1 si une seule pose a échoué — une DIT restée en `Terrain` part en
facturation chez un dispatcher qui la planifie.

**En pratique, utilise `--retard` plutôt que le JSON** :

```bash
python3 finir_passage.py --retard        # le passage en cours + ce qui traîne
python3 finir_passage.py --retard 3      # en remontant trois jours
```

Il relit le journal — celui de `/data` **et** le local, car les créations du
passage en cours n'arrivent en prod qu'à la fusion finale — reprend les
créations récentes, relit chaque DIT dans Artis, et ne finit que celles qui
ne sont pas déjà en `Hotline` / `Non facturable`. Le tag se déduit du motif
par `tag_a_la_creation`, exactement comme à la création. Le JSON reste utile
pour un lot précis, hors passage.

C'est la **seule** façon de finir un passage. `_poser_type_travail` est privée
et exige un `ctx` interne : n'essaie pas de la reconstruire. Et une seule
session : ne lance pas `--retard` au début **et** à la fin, chaque connexion
prend un siège de licence.

Le 2026-10-15, un passage a lu `E.transferer_mail(pid)`, conclu « les
propositions ne contiennent pas ces mails, donc ça ne marche pas ici », et
laissé six mails en boîte — dont une urgence client. Les quatre outils
ci-dessus étaient disponibles et attendaient un `mail_id`. Et en fin de passage
`A._poser_type_travail(ctx)` puis `python journal_sync.py`.

Trois raisons, et chacune a coûté un passage le 2026-10-15 :

- **le quota Graph.** Les outils du connecteur passent par l'application de
  Claude (`07c030f6-…`), dont le quota est serré et partagé : huit rangements
  ont suffi à déclencher un `429 TooManyRequests` avec 62 s d'attente, et le
  passage s'est arrêté là. La chaîne Python, elle, passe par l'application
  **DFM** (`14d82eec-…`), quota distinct — aucun 429 en des centaines
  d'actions depuis le poste ;
- **les transferts.** « Pas d'outil d'envoi dans cette session » est faux :
  `transferer` existe, prend un **motif** et jamais une adresse, signe
  « Service MCO - Mail automatique » et refuse tout destinataire hors des
  trois autorisés. Sans lui, aucun complément n'est possible — et un passage
  entier s'est soldé par 0 création pour cette seule raison ;
- **le coût.** Un appel `boite` rend les 25 mails d'un coup ; un `lire_mail`
  rend le corps, les pièces et la date convertie. Lire la boîte mail par mail
  avec le connecteur, c'est une trentaine de tours d'outils au lieu de quatre,
  et chaque tour recharge tout le contexte.

Si un import manque : `python3 -m pip install -r requirements.txt` à la racine
du dépôt — avec `python3 -m pip`, car `/usr/bin/pip` installe pour un autre
interpréteur et le module reste introuvable (constaté le 2026-10-15).
L'environnement étant persistant, une seule fois suffit.

**Rien du raisonnement ne change** : c'est ce skill, les mêmes outils, les
mêmes garde-fous. Ce qui change, c'est qu'il n'y a ni `/data` monté, ni
`az login`, ni `.env` — une session distante n'a que des **variables
d'environnement** et le dépôt git.

Trois fichiers doivent pourtant survivre d'un passage à l'autre :

| Fichier | Pourquoi il doit persister |
|---|---|
| `mail_tokens.json` | le jeton Graph, lu **et réécrit** : le refresh token tourne à chaque rafraîchissement, le déposer en variable ne survivrait pas au premier passage |
| `mail_dit_journal.jsonl` | c'est lui que l'onglet du dashboard affiche |
| `mail_dit_ecriture.txt` | le drapeau — en lecture seule depuis l'extérieur |

### Le relais du dashboard, parce que la SAS n'était pas possible

La voie naturelle serait une **SAS** sur le share. Elle est fermée : une SAS
Azure Files se fabrique à partir des **clés du compte de stockage**, et le
compte d'Elliot n'a pas le droit de les lire — vérifié le 2026-10-14,
`AuthorizationFailed` sur `az storage account keys list`. Ses rôles portent
sur l'ACR, la Container App et l'environnement, **pas** sur
`dfmmspdashboardstor`. Demander la SAS à Benjamin restait possible ; Elliot a
préféré ne dépendre de personne.

Le dashboard, lui, a `/data` monté. Il sert donc de **relais** :
`GET|POST /api/relais/<fichier>` dans `updater.py`, gardé par `RELAIS_CLE`
(secret de Container App), avec :

- une **liste blanche** de trois noms — le nom reçu ne sert jamais à
  construire un chemin, sinon « ../ » suffirait à sortir de `/data` ;
- le **drapeau en lecture seule** : une clé qui fuite ne doit pas pouvoir
  *armer* la chaîne, seulement constater qu'elle l'est ;
- une **écriture atomique** (fichier temporaire puis `replace`) : un lecteur
  ne tombe jamais sur un journal à moitié écrit ;
- **POST, pas PUT** : le serveur est un `SimpleHTTPRequestHandler`, qui
  répond 501 à PUT.

Les trois chemins sont exclus d'Easy Auth **un par un** (jamais `/api/relais/*`) :
un nom hors liste reste donc derrière Entra, et répond 401 avant même
d'atteindre l'application. `azure_files.py` parle au relais quand
`RELAIS_URL` + `RELAIS_CLE` sont posées, et retombe sur la SAS si un jour on
en obtient une.

### Le bouton d'arrêt

`mail_dit_ecriture.txt` sur `/data` a un **droit de veto** : s'il existe et ne
dit pas « OUI », **rien ne s'écrit**, même si `MAIL_DIT_ECRITURE=OUI` est posé
sur la routine. C'est voulu : une variable d'environnement ne se retire pas
vite, un mot dans un fichier si — et au plus tard une minute après, le temps
du cache. Sans ce veto il n'y aurait pas de bouton d'arrêt du tout.

Donc pour **armer** la chaîne distante : poser `OUI` dans ce fichier. Pour
l'**arrêter** : y écrire autre chose. La variable ne sert qu'aux cas où le
fichier n'existe pas encore.

### Les variables de la routine

```
ARTIS_AUTH_URL  ARTIS_USERNAME  ARTIS_PASSWORD  ARTIS_BASE_URL
RELAIS_URL=https://msp-dashboard.<...>.azurecontainerapps.io
RELAIS_CLE=<secret relais-cle de la Container App>
MAIL_DIT_ECRITURE=OUI
```

Contrôle avant le premier passage : `python azure_files.py` doit afficher
`stockage par relais : OK`. S'il échoue, inutile d'aller plus loin — le jeton
ne sera pas lisible et rien ne s'écrira.

### Ne JAMAIS conclure du disque que le jeton manque

**`mail_tokens.json` n'est pas sur le disque d'une session distante, et il n'y
sera jamais.** C'est le principe même du relais : le fichier vit sur `/data`.
Un `ls`, un `Path.exists()` ou un « le fichier est absent » ne prouvent donc
**rien** — sinon que tu as regardé au mauvais endroit.

Le seul test qui vaut est **l'appel** :

```python
import mail_auth
tok = mail_auth.access_token()      # lit par le relais si RELAIS_* sont posees
print("jeton Graph :", "OK" if tok else "ABSENT")
```

`mail_auth._load()` bascule tout seul : `azure_files.disponible()` vrai — donc
`RELAIS_URL` **et** `RELAIS_CLE` posées — et il lit `/data` ; sinon il lit le
disque. Si `access_token()` rend `None` alors que le relais répond, le problème
est la **variable d'environnement vue par le processus Python**, pas le fichier :
vérifie que ton `python -c` hérite bien de `RELAIS_URL` et `RELAIS_CLE`.

Ce piège a coûté deux passages entiers le 2026-10-15. Dans le second, la
chaîne a correctement lu le drapeau par le relais, puis a déclaré le jeton
« absent » parce que le fichier n'était pas sur le disque — et s'est interdit
tout transfert, donc tout complément, donc toute création. **Six mails sont
restés en boîte, dont trois réponses clients urgentes**, alors que le jeton
était accessible depuis le début.

**Ne pas poser `DATA_DIR`** : il ferait chercher les fichiers sur un disque
qui ne survit pas à la session.

**Un seul rafraîchisseur à la fois.** Le jeton de `/data` est partagé avec le
conteneur et le robot à règles. Microsoft invalide l'ancien refresh token à
chaque rotation : si deux chaînes rafraîchissent en parallèle, l'une des deux
perd l'accès. Tant que la routine Cloud tourne, l'agent du conteneur doit
rester à l'arrêt — et inversement.

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
