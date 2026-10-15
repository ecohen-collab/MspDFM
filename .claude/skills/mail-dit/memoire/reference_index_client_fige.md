---
name: reference_index_client_fige
description: "L'index client du moteur mail est fige au 10/09/2026 et n'a pas tous les clients Artis — 2 sur 17 manquants sur un echantillon reel"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-28T15:18:05.353Z
---

`mail_dit._index_clients()` lit `<DATA_DIR>/mailcorpus/client_index_all.json`.
Cet index est **construit depuis le corpus de DIT**, pas depuis Artis, et il
n'est **jamais reconstruit** : en prod comme en local, le fichier fait
2 694 111 octets et date du **10 septembre 2026** (verifie le 2026-09-28,
`ls -l /data/mailcorpus` par `az containerapp exec`, taille identique des deux
cotes).

Contenu : 11 419 adresses, 2 328 domaines, 2 200 libelles clients.

**Un client absent de l'index est invisible au moteur**, quoi qu'il arrive :
ni par adresse, ni par domaine, ni par libelle. Mesure sur les 17 clients
reellement rencontres dans la boite le 2026-09-28 : **2 manquants**, soit 12 %.
- `5370` MASTER FINANCE (domaine `master-finance.eu` absent lui aussi)
- `17721` SPL AGENCE D'ATTRACTIVITE (domaine `attractivite52.fr` absent)

Les deux existent pourtant dans Artis, avec bien d'infogerance et
interlocuteurs. Tout mail venant d'eux tombe en « client non etabli ».

**Ce qui n'est PAS en cause : `mail_regles.trouver_client()`.** Elle cherche
bien l'**adresse exacte d'abord** (`index["emails"]`), puis les adresses citees
dans le corps d'un transfert, puis le destinataire, et seulement ensuite le
domaine — les domaines publics etant ecartes. L'ordre est correct ; c'est la
matiere qui manque.

Corollaire verifie : `benjamin.msihid@gmail.com` **est** dans `index["emails"]`.
Son mail du 25/09 (fin de contrat CENTRE EXPLORE VISION) a donc ete range en
`04 - INFORMATIONS` pour une autre raison que l'identification du client — ne
pas conclure trop vite a l'adresse grand public. Voir
[[project_mail_to_dit_artis]].

**Ce qu'il faudrait** : reconstruire l'index depuis Artis plutot que depuis le
corpus, ou au moins le regenerer regulierement. Non fait au 2026-09-28.

Recherche manuelle d'un client par son nom, en attendant :
`mail_dit._libelles_clients()` (2 200 entrees) — mais chercher **par code**
autant que par libelle, un client peut y figurer sans libelle exploitable.
Artis n'a pas d'endpoint de recherche : `/client?raisonSociale=...` ignore le
filtre et renvoie les 20 premiers clients.
