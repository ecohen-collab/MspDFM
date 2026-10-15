---
name: feedback_annuler_doublons_sans_demander
description: "Autorisation permanente : annuler un doublon de DIT sans demander l'accord d'Elliot"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-29T10:42:03.760Z
---

Elliot, le 2026-09-29 : « **tu as toujours pu annuler sans me demander**, tu as
du oublier avec le changement de PC ».

**Why** : un doublon vivant coute plus cher qu'une annulation. Deux techniciens
peuvent partir sur le meme incident, le client recoit deux accuses, et la
mesure d'activite est faussee. Demander l'accord a chaque fois transforme un
geste de nettoyage trivial en attente : le 2026-09-29 j'ai signale quatre
doublons toute la journee sans jamais les annuler, et ils sont restes ouverts
des heures pour rien.

**How to apply** : des qu'un doublon est **etabli** — meme client, meme mail
d'origine (comparer l'`internetMessageId` ou le `conversationId`, pas le texte)
— l'annuler directement et le **dire dans le rapport**. Pas de question
prealable.

```python
E.annuler_intervention("<numero DIT>01", confirmer=True)   # -> HTTP 204
```
Pieges : c'est le **numero d'INTERVENTION** (numero de DIT + `01`), et Artis
repond 415 sans `Content-Type: application/json` + corps `{}` — deja gere dans
la fonction.

Verifier apres coup : la DIT et son intervention doivent etre `Annulee`.

Ce qui reste soumis a accord : annuler une DIT qui n'est **pas** un doublon,
ou deja prise par un technicien (etat autre que `APlanifier`). Voir
[[feedback_artis_pas_decriture_sans_accord]] et [[project_robot_jour_nuit]].
