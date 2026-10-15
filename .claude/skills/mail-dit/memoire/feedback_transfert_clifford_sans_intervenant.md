---
name: feedback-transfert-clifford-sans-intervenant
description: Le mot du transfert a Clifford doit dire qu'il n'y a aucun intervenant et qu'il doit redispatcher
metadata:
  type: feedback
---

Quand une reponse client part chez Clifford faute d'intervenant, le message ne
doit PAS dire « tu es le dernier intervenant » : il doit dire qu'**aucun
intervenant n'est affecte** et que Clifford doit **redispatcher le ticket**.

**Why:** Elliot, 2026-09-25 : « la tu mets "tu es le dernier intervenant", il
faudrait lui dire qu'il n'y a aucun intervenant et qu'il doit redispatcher le
ticket ». Annoncer un role que la personne n'a pas lui fait perdre du temps.

**How to apply:** `_mot_du_transfert(dit, iv)` dans `mail_dit.py` distingue trois
cas — l'intervenant du ticket (il continue), un intervenant sans adresse mail
(Clifford fait suivre), et aucun intervenant (`sans_intervenant=True`, pose par
`mail_dit_ecriture`, Clifford redispatche). Voir
[[feedback-dit-sans-intervenant-clifford]].
