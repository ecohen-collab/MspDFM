---
name: feedback_commentaire_dit_sans_avis
description: "Le commentaire d'une DIT porte la demande du client, jamais un avertissement ni un avis de ma part"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-28T13:43:13.391Z
---

Le commentaire d'une DIT contient **la demande du client, et rien d'autre**.
Pas de bloc « ATTENTION », pas de « Note : », pas de mise en garde, pas
d'analyse de risque, pas de recommandation sur la façon de traiter.
**On crée juste la demande.**

**Why:** Elliot, 2026-09-28, sur la DIT 100175592 (CFD IMMOBILIER, connexion
de la messagerie a ChatGPT) : « ta mis un avertissement, stp tu l'enleve et tu
fais plus ca, tu creer juste les demande ». J'avais ajoute un paragraphe
d'avertissement sur le consentement administrateur a une application tierce.
Meme defaut sur la DIT 100175583 (EBENISTERIE DAGORN), ou j'avais ajoute une
note sur la transmission de mots de passe par mail. Les deux commentaires ont
ete reecrits.

**How to apply:**
- Dans le commentaire : objet, texte du client, historique cite, annonce des
  pieces jointes, echeance donnee par le client. C'est tout.
- Ce qui me semble sensible ou risque se dit **a Elliot dans le rapport**, pas
  dans le ticket. Le garde-fou « signaler un mail sensible » reste valable — il
  s'adresse a lui, pas a l'agent qui traitera la DIT.
- Reecrire un commentaire apres coup : `artis_type_travail.poser_commentaire(
  numero, texte, confirmer=True, web=w)` — aucune route REST ne le permet, c'est
  le formulaire web (champ `ita_ditProbleme`), donc un siege de licence : grouper
  le lot dans une seule session.

Voir [[feedback_crit_format]] pour le format du CRIT de cloture.
