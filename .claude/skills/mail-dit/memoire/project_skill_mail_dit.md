---
name: project_skill_mail_dit
description: La chaine mail support -> DIT Artis vit dans la skill .claude/skills/mail-dit/ du repo Dashboard IA
metadata: 
  node_type: memory
  type: project
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-30T10:53:11.482Z
---

Construite le **2026-09-30** a la demande d'Elliot, dans le repo
`C:\Users\ecohen\OneDrive - DFM\Bureau\Dashboard IA\.claude\skills\mail-dit\`
— donc versionnee et partageable avec l'equipe.

```
SKILL.md                          deroule du passage, champs d'une DIT,
                                  reponse a un ticket, garde-fous
references/pieges-artis.md        auth en query param, pagination des biens,
                                  n° de DIT jamais devine, licences web,
                                  ids Graph jamais reconstruits, BOM PowerShell
references/priorites-uo-biens.md  codes inverses, symptomes, choix du bien
references/dossiers-bal.md        folderId + cas de tri recurrents
references/clients-hors-index.md  index fige au 10/09, methode SALAMANDRE/ADP
bin/artis.py                      client / dit / doublons / symptomes (lecture)
bin/lire_mail.py                  corps texte + pieces jointes utiles
bin/creer_dit.py                  JSON -> DIT + UO + PJ, apercu par defaut
```

**Why** : le prompt du `/loop` faisait 40 lignes rechargees toutes les 15 min et
n'etait modifiable qu'en relancant le job. Les scripts de creation etaient
reecrits a chaque lot (`creer_lot2.py` ... `creer_lot41.py`), ce qui a produit
des erreurs evitables : numero de DIT anticipe (ticket d'un collegue modifie),
ids Graph reconstruits par concatenation (mails ranges au mauvais endroit).

**How to apply**

- Le loop tourne sur `/mail-dit`, les interdits durs restant dans le prompt du
  cron : pas d'envoi de mail, max 2 creations, ne jamais contourner
  l'anti-doublon. Une consigne dans le prompt pese plus qu'un fichier charge.
- `creer_dit.py` controle tout seul ce que j'oubliais : symptome `ADM-*` sans
  UO HLN3, priorite hors 01/02/03, expediteur interne avec un demandeur,
  attente de 65 s entre deux DIT du meme client. Il enchaine sur le numero
  **rendu** par la creation et refuse d'ecrire sans `--go`.
- Tout piege nouveau se note dans `references/`, dans le meme mouvement que la
  decouverte — cf. [[feedback_actualiser_les_skills]].

Voir [[project_mail_to_dit_artis]], [[feedback_reponse_ticket_jamais_nouveau]].
