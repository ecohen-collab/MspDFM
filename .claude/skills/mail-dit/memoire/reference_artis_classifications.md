---
name: reference_artis_classifications
description: "Le commercial attitré d'un client Artis est la classification COMMERCIAL INFORMATIQUE (COMINF), pas le vendeur de la dernière affaire"
metadata:
  type: reference
---

L'écran « Classifications » d'un client Artis porte **un commercial par métier** :

| Classification | `abrev` |
|---|---|
| COMMERCIAL INFORMATIQUE | `COMINF` ← **le nôtre** |
| COMMERCIAL IMPRESSION | `COMIMP` |
| COMMERCIAL TELEPHONIE | `COMTEL` |
| COMMERCIAL SECURITE | `COMSEC` |
| AGENCE | `AG_DFM` |

Elles sont dans l'objet client lui-même, champ **`categ`** (`GET /client/{code}`), sous la forme
`{abrev, nom, classification:{abrev, nom}}`. Il n'existe **aucune** route `/client/{code}/classifications`.

**Le piège corrigé le 2026-10-06** : le dashboard affichait comme « commercial » le `vendeur` de la
**dernière affaire**. Or celle-ci peut être une vente d'impression ou de téléphonie, ou dater.
SFEG (13136) affichait Sarah HADDAD alors que son commercial informatique est Mathieu GEOLLE ;
ALIGN AEROSPACE affichait Céline D'AMATO au lieu de Colin GALLOT.

**Deux contraintes à connaître :**

1. `categ` ne donne qu'un **matricule** (`MGEOLLE`), et aucun endpoint ne le traduit en personne
   (`/personnel`, `/vendeur`, `/collaborateur` → 404). La traduction se fait par un annuaire
   construit depuis les affaires, qui portent nom/prénom/mail en face du même matricule
   (`_ANNUAIRE_MATRICULE`). D'où l'ordre : **affaires d'abord, commerciaux ensuite**.
2. `GET /client?maxResults=99999` rend bien tous les `categ` d'un coup, mais c'est **59,5 Mo**
   pour 23 543 clients — de quoi faire tomber le conteneur. Et l'API **n'a aucune pagination** :
   `startIndex`, `offset`, `page`, `firstResult`, `start` sont tous ignorés, les 20 mêmes premiers
   reviennent. On lit donc client par client (`artis_get_commerciaux`, 40 en parallèle, ~1 s
   pour 3 clients).

`vendeur` est conservé dans la donnée (historique des ventes), mais l'affichage utilise
`commercial`. Livré en rev 0851. Voir [[reference_artis_swagger]].
