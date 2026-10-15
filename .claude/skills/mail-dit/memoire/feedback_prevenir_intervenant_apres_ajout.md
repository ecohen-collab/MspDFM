---
name: feedback_prevenir_intervenant_apres_ajout
description: "Ajouter le mail d'un client a une DIT en cours ne suffit pas : transferer aussi le mail au dernier intervenant"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-01T09:46:07.977Z
---

Elliot, le **2026-10-01**, sur la DIT **100174590** (STEAMONE, onboarding d'un
free-lance) : « tu as bien ajoute dans la DIT l'ajout d'un client suite a son
mail d'hier a 16h47, par contre mon intervenant n'etait pas au courant. Tu es
pas cense egalement transferer le mail au dernier intervenant pour lui dire que
t'as ajoute l'info a la DIT en cours, comme ca il sait ? »

**Why** : poser un commentaire ne notifie personne. Le technicien a le ticket
ouvert depuis des jours ; il ne le relit pas pour verifier qu'une ligne a ete
ajoutee. L'information arrive donc nulle part — c'est pire qu'un mail non
traite, parce que le ticket a l'air a jour.

**How to apply**

1. `artis.py intervenants <numero>` : le **dernier** de la liste a la main,
   meme si son intervention est seulement `Planifiee`.
2. Artis rend `support@dfm.fr` comme adresse de l'intervenant — c'est celle de
   l'organisation. Resoudre son nom dans l'annuaire Microsoft
   (`search_people`), jamais reconstruire l'adresse a partir du nom.
3. Transferer le mail du client avec une phrase : ce qui a ete ajoute, sur
   quelle DIT, ce que le client demande.
4. Le dire dans le rapport.

**Signature de TOUT transfert : `Service MCO - Mail automatique`**, jamais le
nom d'Elliot (demande du 2026-10-01, apres les premiers transferts signes a son
nom). Le mail part de support@dfm.fr : le destinataire doit voir que c'est le
robot qui ecrit, pas Elliot.

C'est la **troisieme exception** a l'interdit d'envoi, avec le transfert a
cdamato + Clifford (client sans contrat Info) et le transfert a Clifford seul
(le client attend une reponse humaine).

Voir [[project_skill_mail_dit]], [[feedback_pas_de_contrat_info_pas_de_dit]],
[[feedback_dit_sans_intervenant_clifford]].
