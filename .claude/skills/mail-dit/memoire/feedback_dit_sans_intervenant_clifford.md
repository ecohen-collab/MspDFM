---
name: feedback-dit-sans-intervenant-clifford
description: Réponse client sur un DIT sans intervenant affecté → transférer à Clifford YAP NEWK PIN et ranger le mail
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-16T09:40:14.743Z
---

Quand une réponse de client porte sur un DIT qui n'a **aucun intervenant
affecté** (ou dont l'intervenant n'a pas d'adresse joignable), le mail part
chez **Clifford YAP NEWK PIN — cyapnewkpin@dfm.fr**, puis il est rangé dans
`01 - DIT ARTIS SUPPORT`. On ne laisse jamais le mail en boîte de réception,
et on ne se contente pas d'écrire dans le commentaire du DIT.

**Why:** Elliot, 2026-09-16, après la relance « TRES URGENT » de NEURAXPHARM
sur le DIT 100174012 (cliente : 2 mails + 3 appels sans réponse). Un
commentaire ajouté à un ticket que personne ne suit ne réveille personne ;
l'objectif reste zéro mail en boîte de réception.

**How to apply:** `RELAIS` dans `mail_dit_ecriture.py` ; `dernier_intervenant()`
renvoie ce relais au lieu de `{"aucun": True}`, ce qui déclenche le transfert
automatique du suivi. Le mot du transfert change quand `relais` est vrai — ne
jamais annoncer à Clifford qu'il est « le dernier intervenant ».

Voir [[project_mail_to_dit_artis]] et [[feedback_bal_support_dfm]].
