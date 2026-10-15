---
name: feedback-metier-lire-le-corps
description: "Le métier se lit dans le CORPS du mail, et un client multi-métier n'est pas un client d'un autre métier"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-17T10:43:10.503Z
---

Trois règles données ensemble par Elliot le 2026-09-17, sur trois mails :

1. **Vidéosurveillance nommée dans le corps → sécurité.** « UFOLEP77 / Demande
   d'ip fixe » : l'objet ne dit rien, le corps parle de caméras de
   surveillance. Le vocabulaire sécurité n'était cherché que dans l'objet.
   ⚠ « caméra » **seul** reste informatique (webcam) — mesuré : le seul mail du
   corpus qui le contient est une vraie demande réseau.
2. **Un client multi-métier n'est pas un client d'un autre métier.** Steam-One
   avait été écarté deux fois comme sécurité ; son historique Artis est
   pourtant fourni en INFO. « Tu vois bien le nombre de DIT INFO. » Le
   garde-fou `metier_signale` ne vaut donc que si le client n'a **rien** en
   INFO.
3. **Un client dont TOUT l'historique est en téléphonie : on déplace, on ne
   bloque pas.** « RFG - RIBEIRO - CASQUE SANS FIL » — un casque sans fil chez
   un client 100 % TEL, c'est de la téléphonie. Avant, ces mails étaient
   seulement empêchés de devenir un ticket, donc ils s'accumulaient.

**Why:** « tu réfléchis et tu lis le corps du mail » — le sujet seul ne suffit
pas à décider du métier, et l'historique du client tranche mieux qu'un
signalement isolé.

**How to apply:** `RE_AUTRE_METIER_CORPS` (mail_regles.py) pour le point 1 ;
`_creation_sans_doute` pour le 2 ; `decider_seul`, étape « 3 bis », mappe
`domaine_client_hist` TEL/IMP/SEC vers le dossier du métier pour le 3.

Voir [[project_mail_to_dit_artis]] et [[project_mail_modele_lecture]].
