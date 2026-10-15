---
name: reference_artis_bien_pagination
description: "GET /client/{code}/bien est pagine a 20 par defaut — sans maxResults on conclut a tort qu'un client n'a pas de bien d'infogerance"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-29T10:48:34.247Z
---

`GET /client/{codeClient}/bien` accepte un parametre **`maxResults`** et en
renvoie **20 par defaut**. Sans lui, on lit la premiere page et rien d'autre.

Le 2026-09-29 j'ai conclu quatre fois « ce client n'a pas de bien
d'infogerance » alors qu'il en avait un, parce que la premiere page etait
remplie de materiel d'impression :

| Client | Biens reels | Bien attendu, invisible page 1 |
|--------|------------|--------------------------------|
| 20227 ALIGN AEROSPACE | 69 | `20227-INFOGERANCE` |
| 9599 MYMOBILITY       | 166 | `9599 - INFOGERANCE` |
| 0935 CDB              | 91 | `0935 - BAL EXCHANGE 365` |
| 30508 SRP CONSTRUCTION| 32 | `30508 - INFOGERANCE` |

Consequence : quatre DIT creees **sans bien**, et un rapport faux affirmant que
SRP CONSTRUCTION n'etait peut-etre pas sous contrat d'infogerance — il l'est.

**Toujours interroger `/client/{code}/bien?maxResults=5000`.** Le meme piege
vaut probablement pour les autres listes du meme style (interlocuteurs, sites,
DIT) : verifier le parametre dans `_artis_swagger.json` avant de conclure a
l'absence de quelque chose.

**Le libelle du bien n'a pas de forme fixe** : `20227-INFOGERANCE` (colle),
`9599 - INFOGERANCE` (espaces), `2349-INFOGERANCE`, `31239 - INFOGERANCE`.
Ne jamais le fabriquer par concatenation — le **lire** dans la liste.

Poser le bien apres coup sur une intervention :
`PATCH /it/{numeroIt}/bien/fabricant/{identifiantFabricant}` — encoder
l'identifiant (`urllib.parse.quote(bien, safe="")`, il contient des espaces),
`Content-Type: application/json` et corps `{}`. Repond 204.

Voir [[feedback_dit_bien_et_onboarding]] et [[project_mail_to_dit_artis]].
