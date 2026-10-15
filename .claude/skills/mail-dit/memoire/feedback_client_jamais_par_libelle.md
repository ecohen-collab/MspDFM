---
name: feedback_client_jamais_par_libelle
description: "Ne jamais choisir un code client sur la ressemblance du libelle — passer par index['domaines'] puis verifier interlocuteurs et biens dans Artis"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-29T08:37:27.207Z
---

Chercher un client avec `_libelles_clients()` et retenir celui dont le **nom
ressemble** au domaine de l'expediteur est un piege : le 2026-09-29, le mail
de `a.dobigny@mymobility.fr` m'a fait retenir **9600 « ST2S (JL INTERNATIONNAL
- MYMOBILITY) »**. Le bon client etait **9599 « JL INTERNATIONAL (ST2S -
MYMOBILTY) »** — deux fiches quasi homonymes, inversees.

**Why** : une DIT creee sur le mauvais client part chez le mauvais
interlocuteur, avec le mauvais contrat et la mauvaise facturation. Le libelle
ne prouve rien ; les deux fiches contenaient le mot MYMOBILITY.

**How to apply** — dans cet ordre, avant toute creation :
1. `M._index_clients()["domaines"][domaine]` donne les codes **ponderes** :
   `mymobility.fr -> [['9599', 248], ['9600', 14], ['9327', 1]]`. Le poids
   tranche — 248 contre 14.
2. `index["emails"][adresse_exacte]` donne en prime le `pid_interlocuteur`
   a passer directement a `creer_dit` (ici Aline DOBIGNY = 191912).
3. Confirmer dans Artis : `GET /client/{code}/interlocuteur` doit contenir des
   adresses du bon domaine, et `GET /client/{code}/bien` doit montrer un parc
   coherent. Sur 9600 : 22 interlocuteurs, **zero** en `@mymobility.fr`, et un
   seul bien `PROTECT-9600` — deux signaux qui suffisaient a ecarter la fiche.

Signal d'alerte a ne jamais ignorer : l'avertissement `aucun interlocuteur : le
demandeur du DIT restera vide` renvoye par `creer_dit` en simulation. Sur un
client d'infogerance qui ecrit tous les jours, c'est presque toujours que le
**client est faux**, pas que le contact manque.

**L'index se trompe aussi de PERSONNE, pas seulement de client.** Le 2026-09-29,
`index["emails"]["aurelie.valentin@croaif.org"]` renvoyait l'id **216030**, qui
est en realite **« Melie Courtois »** chez le meme client 7551. La bonne fiche
etait **128013 « AURELIE (Contact informatique sur place) VALENTIN »**.
`creer_dit` aurait rattache le ticket a la mauvaise personne sans rien signaler.
Donc : quand l'index renvoie un `id`, **verifier le nom** par
`GET /client/{code}/interlocuteur` avant de creer — et preferer un contact
marque « contact informatique » quand il existe.

Voir [[reference_index_client_fige]] (l'index est fige au 10/09, mais ses
ponderations restent justes) et [[project_mail_to_dit_artis]].
