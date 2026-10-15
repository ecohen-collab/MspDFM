---
name: project_robot_jour_nuit
description: "Depuis le 2026-09-29 : robot de prod arme en permanence, Claude prioritaire le jour via le battement, revue des creations du robot chaque matin"
metadata: 
  node_type: memory
  type: project
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-29T08:18:12.749Z
---

Arrangement decide par Elliot le **2026-09-29**, apres une nuit ou **56 mails**
se sont empiles dans `support@dfm.fr` sans que rien ne soit traite.

**Cause de l'incident** : la boucle `/loop` repose sur un cron `[session-only]`
(`CronList` l'affiche explicitement). Il ne se declenche que tant que la
session Claude Code tourne. Poste en veille = zero passage. Ce n'est pas un
plantage, c'est le support qui est inadapte a une surveillance 24/7.

**Le montage retenu** :
- `/data/mail_dit_ecriture.txt` = `OUI` → **le robot reste arme en permanence**.
- A **chaque passage**, j'ecris `/data/ia_presence.json` (`battement_ia`).
  Tant qu'il a moins de `PRESENCE_FRAICHE_MIN` = **40 min**, le robot me laisse
  la main (`mail_dit.py` ~2132). Ma session morte, il reprend seul en 40 min.
- **Le jour, c'est moi seul.** Nuances a connaitre : `DELAI_IA_MIN = 20` — meme
  battement frais, le robot reprend un mail que je n'ai pas marque `ia_vue` et
  qui a plus de 20 min. Pour un « moi seul » strict il faudrait relever cette
  constante (modif de code + deploiement, non faite).
- **Chaque matin**, relire ce que le robot a cree la nuit et corriger.

**Ecrire dans `/data`** : uniquement `az containerapp exec`, et il est
**rate-limite** — 7 appels en 4 min ont declenche un `429` avec
`retry-after: 600`. Un appel par passage de 15 min passe. Donc : **un seul exec
par passage, aucune sonde**. Il n'existe aucune route HTTP pour le battement, et
Elliot a interdit d'en creer une.

Deux pieges verifies le 2026-09-29 :
- `az exec --command` **decoupe sur les espaces**. Un one-liner Python doit
  n'en contenir aucun : utiliser `__import__('json')` plutot que `import json`.
  `base64` n'existe pas cote Windows, inutile d'encoder.
- Le drapeau doit etre ecrit **sans BOM**. `Set-Content -Encoding utf8` en pose
  un, et `ecriture_armee()` fait `read_text().strip().startswith("OUI")` — le
  BOM n'est pas retire par `strip()`, l'outil reste desarme en silence. Cote
  Windows : `[IO.File]::WriteAllText(..., (New-Object Text.UTF8Encoding $false))`.

Voir [[project_mail_to_dit_artis]], [[reference_maj_manuelle_prod]],
[[feedback_bal_support_dfm]].
