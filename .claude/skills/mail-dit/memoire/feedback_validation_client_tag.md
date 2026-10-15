---
name: feedback-validation-client-tag
description: "Circuit de validation client — Tag a la creation puis Demande validee ; jamais retroactif, jamais sur un ticket cloture"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-06T12:38:46.525Z
---

Chez un client qui exige qu'un tiers de chez lui **valide** une demande de
droits ou d'accès, la DIT part avec le Tag **« En attente de validation
client »** (`VALIDATION`) à la création, et passe à **« Demande validée »**
(`VALIDATION CLIENT`) quand le valideur répond. Demandé par Elliot le
2026-10-06 sur la DIT 100176480 (accès SAGE PAIE chez MYMOBILITY).

Trois limites, dites le même jour :

- **jamais rétroactivement** : les tickets déjà ouverts ne se retaguent pas ;
- **jamais sur un ticket clôturé** : ni pose ni changement, c'est fini ;
- **aucune phrase de moi dans le commentaire**, pas même entre parenthèses
  pour dire qui a validé et quand : « transmettre le mail du client est
  suffisant ». Voir [[feedback-commentaire-dit-sans-avis]] et
  [[feedback-commentaire-texte-du-client]].

**Why:** le Tag est ce que le dispatcher lit pour savoir s'il peut lancer le
travail. Un tag posé à tort bloque une demande ; un tag oublié fait traiter
une demande non autorisée.

Et le mail du valideur se range dans **`BAL INFO/14 - VALIDATION CLIENT`**,
pas en 01 - DIT ARTIS SUPPORT (demandé le même jour) : c'est là que l'équipe va
chercher les accords. Clé de rangement `validation`, choisie automatiquement par
`completer_dit` — y compris sur un ticket clôturé, puisque ranger n'écrit rien
dans Artis.

**How to apply:** `mail_dit_ecriture.demande_a_valider(code, de, texte)` et
`validation_recue(code, de)` ; la table est `REGLES_CLIENT` + `VALIDEURS`
(MYMOBILITY 9599/9600 aujourd'hui), parce que le « Commentaire fiche client »
d'Artis n'est pas exposé par l'API. Le Tag lui-même se pose par le web :
[[reference-artis-tags-dit]].
