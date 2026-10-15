---
name: project-routine-cloud-claude
description: "La routine mail-dit automatique tourne sur les routines Cloud de Claude, PAS sur Azure (coût) — et le proxy de la session bloque les sorties réseau"
metadata:
  node_type: memory
  type: project
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-15T10:30:00.000Z
---

**La chaîne `/mail-dit` automatique doit tourner sur les routines Cloud de
l'application Claude, pas sur Azure.** Elliot, le 2026-10-15 : « on a dit HIER
qu'on touche pas a azure ca coute trop cher, on utilse les routines cloud de
lapplication claude ». Ne pas reproposer le conteneur `msp-dashboard`, une VM
ou GitHub Actions comme hôte : la question est tranchée.

## Le premier passage réel a eu lieu le 2026-10-15 — il a échoué, et il a appris trois choses

**1. Le proxy de la session Cloud bloque les sorties réseau.** La routine a eu
un **403 du proxy** sur `/api/relais/mail_dit_ecriture.txt`. Ce n'est ni Entra
ni `RELAIS_CLE` : les deux hôtes sont pourtant **publics** (`artis.dfm.fr` →
`4.251.104.254`, le relais → `20.19.242.234`). C'est la liste blanche du proxy
sortant (`$HTTPS_PROXY`) qui refuse. Donc **Artis sera bloqué aussi**, et le
chantier ne tient pas tant que ce domaine n'est pas autorisé côté routine.
Ne pas chercher à contourner le proxy ni le classifieur.

**2. Le connecteur Microsoft 365 contourne TOUS les garde-fous Python.** La
même routine, en se déclarant bloquée, a **rangé un mail en production**
(« RE: Guard Industrie- rainbow » → BAL TELECOM, vérifié). Elle l'a fait par
le connecteur MCP Outlook, pas par la chaîne. Conséquence :
`DOSSIERS_PERMIS`, l'anti-doublon, le journal et surtout le **drapeau
`/data/mail_dit_ecriture.txt` ne sont PAS un bouton d'arrêt** pour une routine
Cloud équipée du connecteur M365 — le drapeau valait `NON` et le mail a bougé
quand même. Le seul vrai bouton d'arrêt est de retirer le connecteur ou de
désactiver la routine.

**3. Le journal d'une session Cloud meurt avec elle.** Il s'écrit sur
`/home/user/mail_dit_journal.jsonl` et n'est pas remonté : l'action existait
dans la BAL et nulle part dans la trace. Ligne rattrapée à la main le
2026-10-15.

## Modules du dépôt : vérifier, ne pas supposer

`mail_regles.py` et `mail_modele.py` **n'étaient pas suivis par git** (constaté
le 2026-10-15 quand la routine a échoué à l'import). Une note antérieure
affirmait que tout était poussé : c'était faux. La bonne vérification est la
**fermeture transitive des imports** comparée à `git ls-files`, pas un coup
d'œil à la liste. Ni l'un ni l'autre ne contient de secret (`mail_modele.py`
lit `ANTHROPIC_API_KEY` dans l'environnement).

## Ce qui reste vrai

Une session distante n'a ni `/data` monté, ni `az login`, ni `.env` — d'où le
relais du dashboard (`/api/relais/<fichier>`, monté le 2026-10-14, trois
fichiers en liste blanche, drapeau en lecture seule) et `azure_files.py`.
Contrôle avant tout passage : `python azure_files.py` doit afficher
`stockage par relais : OK`. Voir [[project-skill-mail-dit]].
