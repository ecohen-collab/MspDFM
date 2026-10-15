---
name: project_mail_to_dit_artis
description: Créer des DIT Artis depuis les mails de support@dfm.fr — en production depuis le 2026-09-10, surveillance active, validation humaine obligatoire avant création
metadata: 
  node_type: memory
  type: project
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-14T10:52:00.389Z
---

Créer automatiquement des DIT Artis depuis les mails de `support@dfm.fr`. **Doc complète : `MAIL_TO_DIT.md` (810 lignes) à la racine du repo** — chiffres, règles, pièges, historique des itérations. À lire en premier pour reprendre.

## État au 2026-09-11 — en production
Surveillance **active** en Azure (60 s en heures ouvrées, 10 min sinon), file de validation dans l'onglet « Mail support vers DIT ». Première DIT créée de bout en bout par la chaîne complète : **`100173591`** (STEAMONE), avec UO posée, type de travail corrigé et mail déplacé. Compte Artis dédié **MCO** (`enregistrePar` ne porte plus ELCOHEN) ; le dashboard est passé dessus après avoir vérifié qu'il voit autant qu'ELCOHEN.

**Le blocage `typeGpTravail` est levé** : l'API ne sait pas écrire ce champ (21 hypothèses au journal) mais le formulaire web, si — `artis_type_travail.py`, branché dans `traiter()`, corrige automatiquement après création. Voir [[reference_artis_formulaire_web]] et `NOS_DIT_CREES.md`.

**Rien ne part tout seul** : le cycle est en lecture seule, il remplit la file. La création exige une action humaine dans l'écran, plus les deux verrous d'écriture (`confirmer=True` + drapeau `mail_dit_ecriture.txt` contenant `OUI`).

### Décision d'Elliot (2026-09-11) : webhook Graph ECARTE
La cadence de 60 s en heures ouvrees suffit — « non non 60 ca suffit amplement ».
Ne pas reproposer. Le webhook aurait exige d'ouvrir une route NON AUTHENTIFIEE
sur la Container App (`excludedPaths` sur Easy Auth, qui est aujourd'hui en
`RedirectToLoginPage` sans aucune exclusion), sinon le handshake de validation
de Graph echoue et l'abonnement ne se cree meme pas. Il aurait aussi fallu
renouveler l'abonnement tous les 3 jours et garder le sondage en filet, la
livraison des notifications n'etant pas garantie. Gain reel : la latence, pas
la bande passante — on est a 1 ou 2 requetes Graph par minute.

### RENVERSEMENT (2026-09-14) : le demandeur manquant se cherche par le NOM, puis se cree
Elliot (P-0186) : « si le demandeur existe pas, cree-le ; regarde d'abord sur le
client dans Artis s'il y a le meme nom, sinon ajoute l'adresse comme nouveau
demandeur — et garde cette logique pour tous les cas futurs ».

Logique posee dans `assurer_demandeur()` / `trouver_demandeur()` :
1. adresse mail EXACTE chez ce client ;
2. sinon **le nom de famille**, le domaine mail pouvant differer — un comptable
   externe garde son nom, pas le domaine du client. Cas reel : le mail vient de
   `romain.broutier@bfpatrimoine.fr` et le client RAISE (20470) a bien
   `M. Romain BROUTIER` (id 179012, mail `@raise.co`) ;
3. deux homonymes -> on ne tranche pas, l'ecran demande ;
4. personne de ce nom -> **creation** du contact, avec deux refus nets : un
   expediteur automatique (`RE_NOREP`) et un nom d'affichage d'un seul mot ne
   creent jamais rien (mesure sur la file : les deux seuls candidats a la
   creation etaient « Sophos » et `firewall@crpce.com`).

Toute creation est tracee dans le registre (`demandeur_cree`) et s'affiche en
pastille « contact cree » dans l'historique des DIT — sa demande du 2026-09-11.

### Ancienne decision du 2026-09-11 (remplacee par celle ci-dessus)
`POST /client/{codeClient}/interlocuteur` permettrait de créer le demandeur quand il n'existe pas — **43 %** des propositions à client identifié sont dans ce cas. Mesure faite avant de le construire : les 12 expéditeurs concernés étaient `support@`, `accueil@`, `exploitation@`, un relais `orangesmtp@orange.fr` et un robot d'alerte — **aucune personne**. Les créer aurait pollué les fiches clients de faux contacts. Et le garde-fou évident (« domaine déjà rattaché au client ») ne filtre rien : les 12 le passent.

Donc : exiger une **identité de personne** (nom d'affichage en deux parties, partie locale non générique) et **toujours une case à cocher** dans la file — jamais en automatique, parce que ça écrit dans la fiche du client. Le nom d'affichage de l'expéditeur (`de_nom`) n'était pas capté avant le 2026-09-11 ; il l'est désormais, de quoi mesurer le volume réellement légitime.

## Règles métier données par Elliot
- **On ne cree QUE des tickets informatiques — domaine `INFO`, JAMAIS un autre.** Dit deux fois par Elliot, la seconde apres m'avoir vu laisser `TEL` dans la liste autorisee. Impression (`HOTLINE`), Securite (`SEC`), Telephonie (`TEL`), `AUD`, `KM`... ont leurs propres equipes. Le piege : le domaine etait lu dans l'HISTORIQUE du client, et **1 151 clients sur 2 200** ont « Impression » comme domaine dominant — l'outil aurait donc mis la majorite des tickets chez la mauvaise equipe. Revele par la DIT 100173675 : creee en domaine Impression, elle n'avait meme pas de champ « type de travail » (il n'existe pas pour cette activite), d'ou l'echec de la correction automatique.
- **On ne cree QUE des tickets informatiques.** Securite, impression, autres metiers : d'autres equipes s'en chargent, on n'y touche jamais (2026-09-11). ATTENTION : ne PAS filtrer sur le vocabulaire de l'impression — mesure sur 408 DIT, « imprimante » apparait dans 15 tickets INFO contre 5 des autres domaines, et Elliot l'a confirme : « imprimante c'est de l'informatique parfois ». Seul le vocabulaire de la SECURITE est ecarte (camera, alarme, controle d'acces...), absent des 408 tickets donc sans risque.
- **Une relance sur un ticket deja cree ne produit rien** : le dispatcheur s'en charge manuellement. C'est le rapprochement fil <-> DIT qui tranche.
- **Raisonner sur le FIL, jamais sur le mail isole** : deux messages du meme fil peuvent recevoir des verdicts opposes selon qu'un ticket existe ou non. Le commentaire vient du PREMIER message client du fil, pas du rappel.
- **Priorité** : personne bloqué = `03` Niveau 1 · une personne = `02` Niveau 2 · toute la société = `01` Niveau 3. (L'équipe met `02` partout dans ~78 % des cas ; la règle changera la répartition, c'est voulu.)
- **UO** : `HLN3` pour les tâches d'administration (symptômes `ADM-*`), `HLI` sinon. L'UO dit **qui traite** le ticket (HLN3 = équipe niveau 3 dont Elliot fait partie), elle ne se déduit pas du mail.
- **typeDit `05` EMAIL CLIENT**, même si les agents saisissent souvent `01` par habitude.
- **Date du DIT = date du mail**, convertie UTC → heure locale.
- Puces de liste en tirets, pas en astérisques.
- Le mail traité doit être **déplacé dans `01 - DIT ARTIS SUPPORT`**, après création réussie du DIT seulement.

## EN AUTOMATIQUE depuis le 2026-09-15 au matin
Elliot : « vas-y commence le traitement automatique ». Actions armees :
`creer` (sous `_creation_sans_doute`), `ranger` les alertes, `ecarter` un
metier deja tranche 2 fois, `transferer` au collegue nomme.
**`completer` reste desarme** — ca ecrit dans le ticket de quelqu'un d'autre.

Tout est journalise dans `/data/mail_dit_auto.json`, visible par le bouton
« Journal automatique » : une ligne par decision, EXECUTEE OU REFUSEE, avec le
motif. Et chaque DIT de l'historique porte une zone d'AVIS (horodate, empile,
jamais ecrase) : c'est ce qu'Elliot ecrit pour que l'outil fasse mieux.

### Le
s trois lecons du premier passage (a ne pas re-apprendre)
1. **Rafale** : 24 decisions, 5 DIT en 25 secondes en vidant l'arriere. Le
   plafond de 12/heure ne l'a pas vu — il raisonne sur l'heure, pas sur la
   rafale. -> `CREATIONS_PAR_PASSAGE = 2`. Une erreur doit couter un ticket,
   pas cinq.
2. **Client d'un autre metier** : DIT 100173967 creee pour LL AVOCATS
   (« demande telephonie et copieur ») alors que ses deux tickets precedents
   sont en domaine Impression. -> `_creation_sans_doute` refuse desormais un
   client dont `domaine_client_hist` n'est pas INFO. Mesure : 1 proposition sur
   36 bloquee en plus.
3. **Relance dans un NOUVEAU fil** : 04216687 et 100173969 sont des escalades
   sur des demandes existantes, ouvertes dans un fil neuf — le garde-fou
   « le mail appartient a un fil » ne les voit pas. Trou connu, regle a ecrire.

### Piege Artis : annuler une intervention
`PATCH /it/{numeroIt}/annuler` repond **415 Unsupported Media Type** sans
`Content-Type: application/json` ET un corps `{}` — alors que l'URL ne prend
aucun parametre. Fonction `annuler_intervention()` dans mail_dit_ecriture.
Apres annulation, si le mail doit repartir au bon service : le REMETTRE dans la
boite de reception et le marquer NON LU (sinon personne ne le voit).

## FILETS DE SECURITE avant le passage en automatique (Elliot, 2026-09-14 au soir)
« Demain matin on te lance en automatique. » Consignes donnees telles quelles,
toutes implantees et deployees :

1. **Symptome introuvable -> creer quand meme, en `AUTRES`** (le code existe
   dans le referentiel INFO) et le SIGNALER dans l'historique des DIT. Une
   demande client ne doit pas se perdre parce que l'outil n'a pas su la ranger.
   Le symptome ne bloque donc plus la creation ; pastille « à choisir ».
2. **Demandeur introuvable -> creer quand meme et le dire** : pastille
   « sans demandeur » sur la ligne du registre. Voir [[reference_artis_formulaire_web]].
3. **AUCUN transfert externe**, jamais : `est_interne()` est le point de
   passage unique des deux chemins de transfert (bouton + suivi de ticket).
4. **Pas de creation en masse ni de boucle** — `_cadence_ok()`, trois verrous :
   - une proposition ne donne jamais deux DIT (sans limite de temps) ;
   - pas deux DIT pour le meme client a moins de 60 s ;
   - plafond de 12 DIT par heure, au-dela tout s'arrete.
   Verifie AVANT tous les autres controles : un emballement doit s'arreter net.

## Objectif final : l'outil decide seul (Elliot, 2026-09-14)
« Au final TU devras tout faire automatiquement, la je le fais manuellement
pour que tu apprennes a juger seul. » Son dispatch manuel est donc de la
matiere d'apprentissage, pas seulement une correction ponctuelle.

Deux instruments construits pour ca, tous deux en prod :
- **`GET /api/maildit/corpus`** — chaque proposition tranchee : ce que l'outil
  avait deduit face a ce que l'humain a decide. C'est la verite terrain contre
  laquelle toute regle candidate se mesure AVANT d'etre posee.
- **Mode ombre** (`decider_seul()` + `GET /api/maildit/ombre`, bouton dans
  l'ecran) — a chaque re-tri, l'outil ecrit ce qu'il FERAIT (`ranger`,
  `completer`, `ecarter`, `creer`, ou `attendre`) sans rien executer. On
  compare ensuite action par action. On n'allume l'automatisation que sur les
  familles ou le taux d'accord le justifie, jamais en bloc.
  Volontairement : le mode ombre ne juge QUE les propositions arrivees apres
  son activation, et ne touche pas celles marquees `confirme_humain` — sinon
  il connaitrait deja la reponse.

Etat des mesures au 2026-09-14 (echantillons encore petits, a refaire) :
- symptome : sur 12 DIT validees, la premiere suggestion est la bonne 2 fois,
  le bon symptome est dans le top 3 5 fois. **Trop bas pour automatiser** — le
  mode ombre s'abstient (`attendre`) quand aucune regle metier ne donne le
  symptome, et c'est le bon choix.
- perimetre (est-ce pour nous ?) : aucun vocabulaire ne separe la telephonie de
  l'informatique. L'apprentissage passe par les boutons « pas pour nous ».
- familles mecaniques (ranger une alerte, joindre les photos, regrouper,
  completer un DIT existant) : ce sont les premieres candidates, elles ne
  demandent aucun jugement.

## Regles ajoutees le 2026-09-14 (rapports de dispatch d'Elliot)
- **La signature ne va JAMAIS dans le commentaire** — dit trois fois (P-0165, P-0175, P-0182). Deux coupes : a la formule de politesse (`couper_signature`) et en fin de texte (`couper_pied`, qui exige un marqueur de signature avere avant de retirer quoi que ce soit). Mesure : 0 mail vide, rappel de classification inchange a 98,3 %.
- **L'historique cite reste dans le commentaire** (`historique_cite`) : sur un transfert, le probleme est dans le message transfere, pas dans le mot d'accompagnement. 32 % des propositions en ont un. Elliot veut en plus une **synthese** du fil — pas faisable sans modele de langue, a arbitrer.
- **Les photos incrustees sont des pieces jointes** : `hasAttachments` est a False quand la photo est dans le corps (cas d'un envoi depuis un telephone). On liste vraiment les pieces. Tri par le NOM : Outlook nomme `image001.png` ce qu'il incruste lui-meme, une photo garde son nom (`IMG_3274.jpeg`). Mesure : 1 290 images incrustees -> 27 retenues, aucune signature. Le commentaire annonce les photos jointes.
- **Regroupement** : meme expediteur + meme client + moins de 45 min + un mail sans texte = une seule demande, donc **une seule DIT** avec toutes les photos (P-0182/P-0183).
- **Le client ecrit dans le mail** : `Site: 14939 - ...` des tickets Datto (8/8 codes valides sur le corpus) et `Client COPRIMHA 29517` avec le nom intercale.
- **Une adresse grand public ne designe aucun client** : @gmail.com pointe sur 326 clients de l'index, @orange.fr sur 133 — prendre le plus frequent revient a tirer au sort (a impute ELEVIO GROUP a « NATAL BOULOGNE »). L'adresse exacte reste valable.
- **La telephonie ne se detecte PAS au vocabulaire** : « 3cx », « telephonie », « numero de telephone » touchent 21 mails du corpus, tous traites par l'equipe info (le seul « 3CX » est un offboarding). A la place, une pastille montre le metier habituel du client (`domaine_client_hist`) quand ce n'est pas l'informatique.
- **`retrier()` repare les propositions deja en file** quand une regle est ajoutee (client, historique, pieces, domaine du client) : Elliot ne doit pas revoir un defaut deja corrige.

## Mesures qui ont ECARTE une fonctionnalite (ne pas la reconstruire)
- **Identifier un client par son NOM dans l'objet** : mesure du 2026-09-11, inutile et dangereux. Le cas qui l'a motivee (« Client MAB FA 30 484 ») n'a PAS de client correspondant dans le referentiel — « MAB FA » est une abreviation commerciale, et « FA 30 484 » un numero de facture, pas un code client. Et 94 des 2 200 clients ont un libelle de 4 caracteres ou moins (VIC, RED, ADS, CBA...) : les chercher dans un objet produirait des faux positifs en serie. Ces mails restent « client a identifier », l'agent choisit.
- **Filtrer le vocabulaire de l'impression** : sur 408 DIT creees depuis cette boite, « imprimante » apparait dans 15 tickets INFO contre 5 des autres domaines. Confirme par Elliot : « imprimante c'est de l'informatique parfois ».
- **Elargir a 30 jours la fenetre de recherche du DIT d'un fil** : deux vraies demandes montaient alors a 0,50 et 0,57 de ressemblance avec d'anciens tickets, donc au-dessus du seuil. Fenetre courte (debut du fil - 1 jour), seuil 0,60.

## Pièges Artis à ne pas redécouvrir
- `POST /dit` ne pose ni l'UO ni le type de travail ; `PATCH /it/{n}/uo/{code}` marche mais **renvoie 404 dans la seconde qui suit la création** → prévoir une reprise.
- `etatDit: "EnCours"` évite l'état « SaisieExterne » (sinon aucune UO n'est attribuée).
- `GET /client/{code}/dit` exige `dateDebut` **et** `dateFin`.
- La **priorité et le domaine sont dans `inter[]`**, pas à la racine du DIT — je m'étais trompé en disant qu'ils n'étaient pas exposés.
- Numérotation par entité : `100…` DFM, `042…` AGB, `072…` AVBU, `034…` DNO, `005…` FOR, `126…` DO.
- Toujours passer l'`id` de l'interlocuteur, jamais nom+prénom (Artis créerait un doublon).
- **Ne jamais proposer une entité du groupe comme client** : `DFM, AGB, AVBU, DNO, DO, GPE` sont dans le référentiel client (AGB = « DFM PACA »).
- `enregistrePar` porte **MCO** depuis le 2026-09-10 (compte dédié créé par Elliot).
- **`POST /dit` accepte bien le `demandeur`** (`{"id": <id interlocuteur>}`) : c'est `GET /dit` qui ne le renvoie pas. J'en avais conclu à tort qu'il était ignoré — deuxième fois que l'absence d'un champ **en lecture** me fait conclure faux sur l'écriture, après la priorité. Vérifier dans l'interface.
- **Artis stocke en ISO-8859-15 et supprime sans prévenir** ce qu'il ne sait pas encoder, en collant les mots voisins : « s'il » (apostrophe typographique U+2019) devient « sil ». Normaliser la typographie avant tout envoi.
- Le commentaire d'un DIT est **réécrivable** après création, par le formulaire web (champ `ita_ditProbleme` de l'écran ccDit en modification) — aucune route REST ne le permet.
- **Incohérence** : affecter un intervenant sur une intervention annulée est accepté, le retirer est refusé.

## Leçons de méthode (elles ont mordu plusieurs fois)
1. **Poser un témoin** sur toute métrique de rapprochement : 80,6 % annoncés valaient 60 % réels.
2. **Remesurer après chaque changement de règle** avant d'annoncer un chiffre.
3. **Demander le sens métier des données avant de mesurer** : j'ai mesuré une précision sur un corpus qui était presque entièrement positif ; la bonne métrique était le rappel (98,9 %).
4. **Vérifier qu'une écriture est réversible avant de la lancer**, même sur une cible jugée inoffensive.
5. Les captures d'écran d'Elliot ont révélé ce qu'aucun test API ne pouvait voir (drapeau « Importée », secteur IT). **Demander une capture plutôt que multiplier les hypothèses.**
6. **Tester en écrivant pour de vrai, une fois, tôt.** Trois défauts n'ont été révélés que par la première création réelle en production : le bouton « Valider » qui n'écrivait pas, `_artis()` qui ne lisait que le fichier `.env` (absent du conteneur), et la demande tenant entièrement dans l'objet. Aucun test unitaire ne les voyait.
7. Le `conversationId` Outlook n'est pas un signal de doublon fiable : trois demandes distinctes peuvent partager un fil.

Piège machine : les heredocs Bash de ce poste **écrasent les antislashes** (`\\b` → backspace, `\\n` → vraie nouvelle ligne). Ne jamais écrire de regex ni de `\\n` Python via heredoc — passer par un fichier écrit avec l'outil Write.

Garde-fous : [[feedback_bal_support_dfm]] et [[feedback_artis_pas_decriture_sans_accord]].
