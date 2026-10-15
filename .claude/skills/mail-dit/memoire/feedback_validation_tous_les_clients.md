---
name: feedback-validation-tous-les-clients
description: "Toute demande de droits / delegation / acces part avec le tag En attente de validation, pour TOUS les clients ; un refus ne vaut jamais validation"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-09T09:34:05.976Z
---

**Le circuit de validation ne depend plus d'une table de clients : c'est le
MOTIF qui declenche.** Elliot, le 2026-10-09 : « tout ce qui est demande de
droit, delegation, acces sharepoint etc. doit faire l'objet d'une validation,
donc faut mettre le tag en attente de validation comme pour JLI (**pour tous
les clients**) et passer en demande validee quand on recoit la confirmation du
client ».

Avant, `demande_a_valider()` rendait False des que le client n'etait pas dans
`REGLES_CLIENT` : un client absent de la table etait invisible pour tout le
mecanisme. C'est ce qui a prive la DIT **100177064** (SFAF) de son tag.
Desormais `MOTIFS_A_VALIDER` (droits, acces, habilitation, autorisation,
delegation, SharePoint, OneDrive, drive, boite mail, partage, privilege,
administrateur, MFA) declenche pour tout le monde.

La table ne sert plus qu'a nommer **qui** valide chez les clients qui ont
designe quelqu'un — MYMOBILITY (9599/9600) et SFAF (8280) a ce jour :

- un mail **du valideur lui-meme** part en **« Demande validee » tout de
  suite** : il n'a personne a qui soumettre sa demande, c'est lui qui autorise
  (Elliot, 2026-10-09 : « pour JLI si jamais le demandeur c'est LUIS mets
  directement le tag demande validee ») ; avant, ce cas ne posait aucun tag et
  le ticket partait nu ;
- **sa reponse vaut accord sans lire le texte** ; chez un client sans valideur
  designe, n'importe quel interlocuteur peut confirmer, mais le texte doit
  porter un accord (`MARQUES_ACCORD`), sinon une simple question ferait passer
  le tag en « Demande validee ».

**Un refus ne vaut JAMAIS validation, meme du valideur designe.** Teste sur le
cas reel : « je vous confirme que cette demande est refusee » de Karine
DOGNIN SAUZE serait passe en « Demande validee » sans ce garde-fou.

**Why:** un tag pose a tort lance un travail que le client vient d'interdire ;
un tag oublie laisse l'equipe agir sans autorisation. Voir
[[feedback-validation-client-tag]] pour les trois limites d'origine (jamais
retroactif, jamais sur un ticket cloture, aucune phrase de moi dans le
commentaire).

**How to apply:** `demande_a_valider(code, de, texte)` et
`validation_recue(code, de, texte)` dans `mail_dit_ecriture.py` ; le tag se
pose par le web dans la session unique de fin de passage
([[reference-artis-tags-dit]]). Regle ecrite dans SKILL.md, a republier sur
`/data` pour l'agent ([[feedback-cause-puis-garde-fou]]).
