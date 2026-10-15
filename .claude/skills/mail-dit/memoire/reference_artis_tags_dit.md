---
name: reference-artis-tags-dit
description: "Les Tags d'une DIT ne sont pas dans l'API Artis — ils vivent dans le select dt_tagsIt de l'ecran ccDit"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-06T12:32:25.318Z
---

Les **Tags** d'une DIT (le bandeau orange « En attente de validation client »,
« Demande validée », « Escalade vers N3 »…) sont **invisibles de l'API REST** :
absents de `GET /dit/{n}`, absents du corps de `POST /dit`, et il n'existe
aucune route `/tag` (swagger v2 vérifié le 2026-10-06).

Ils ne vivent que sur l'écran **`ccDit` en modification**, dans un
`<select multiple name="dt_tagsIt">` doublé d'un `<input hidden
name="dt_tagsIt_multi" value="true">`. Même couple `flush.action` /
`save.action` que `ita_ditProbleme` — voir [[reference-artis-formulaire-web]].

**Why:** on perd une demi-heure à chercher le champ dans l'API avant de
comprendre qu'il n'y est pas. Et le lecteur générique `champs_du_form` ne rend
que le **premier** `selected` d'un `<select>` : sur un multiple, il effacerait
silencieusement les autres tags.

**How to apply:** `artis_type_travail.poser_tag(numero, ajouter=…, retirer=…,
confirmer=True, web=w)` — codes des `<option value>`, pas les libellés, et les
tags non cités sont conservés. Les deux codes du circuit de validation client :
`VALIDATION` = « En attente de validation client », `VALIDATION CLIENT` =
« Demande validée ». Se glisse dans la **même session web** que
`poser_type_travail` : une connexion coûte un siège
([[feedback-artis-licence-sieges]]).
