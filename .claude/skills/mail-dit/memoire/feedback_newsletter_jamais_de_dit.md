---
name: feedback-newsletter-jamais-de-dit
description: "Une newsletter ou une pub ne produit jamais de DIT, même si son domaine désigne un client"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-16T14:12:40.975Z
---

Un mail portant un lien de désinscription, avec un objet qui n'est pas une
réponse, n'est **jamais** une demande : pas de DIT, direction 04 INFORMATIONS.
Le domaine de l'expéditeur ne fait pas foi — il peut être enregistré sur la
fiche d'un client et déclencher « domaine unique ».

**Why:** Elliot, 2026-09-16 : « quel rapport, c'est de la pub, ça se voit quand
même ». Le DIT 100174085 était né d'une newsletter Koban parce que
`koban-crm.com` figurait sur la fiche du client 30223 AERTECH. DIT annulé.

**How to apply:** `envoi_de_masse()` dans `mail_regles.py`, appelée par
`classer()` et par la règle `envoi-de-masse` de `RANGEMENTS_AUTO`. Trois
conditions mesurées sur les 1181 mails de 14 jours : lien de désabonnement,
objet non-réponse, et **pas** un objet de supervision (sinon on casse les DIT
de type 07). Résultat : 3 mails attrapés, tous de vrais envois de masse, 0
demande perdue.

Voir [[project_mail_to_dit_artis]].
