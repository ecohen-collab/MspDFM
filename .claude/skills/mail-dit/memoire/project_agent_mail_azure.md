---
name: project-agent-mail-azure
description: "agent_mail_dit.py — Claude lui-même traite la BAL support depuis Azure, sans le PC d'Elliot (construit le 2026-10-02)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-02T08:47:50.600Z
---

`agent_mail_dit.py` fait le passage mail → DIT **dans le conteneur Azure**, en
appelant l'API Anthropic avec le skill `mail-dit` en consigne système et 17
outils (lire la boîte, Artis, créer, compléter, ranger, transférer). Ce n'est
pas le robot à règles de [[project-robot-jour-nuit]] : c'est une boucle d'outils
avec du jugement. Demandé par Elliot le 2026-10-02 : « je veux que ce soit TOI
sur Azure en auto sans mon PC ».

**Why:** les règles rangent bien mais ne lisent pas ; Elliot voulait éteindre son
PC sans que la qualité des tickets tombe.

**How to apply:**
- La consigne vit sur `/data/agent_consigne.md` (`.dockerignore` exclut
  `.claude/`) : **modifier le skill ne suffit pas**, il faut republier —
  `agent_mail_dit.py --consigne <fichier>` puis `az storage file upload`.
- Un passage coûte ~0,90 € sur Sonnet. `_boite_inchangee()` fait qu'on ne paie
  que si la boîte a bougé : sans lui, un mail laissé en boîte est repayé tous
  les quarts d'heure.
- Plafond mensuel `AGENT_PLAFOND_EUR` (30 € au départ — un arrêt, pas une
  décision budgétaire ; Elliot doit fixer le vrai chiffre).
- État visible sur `/data` : `agent_mail_dit_etat.json`,
  `_battement.json`, `_usage.json`. Les logs du conteneur ne se lisent pas
  depuis le poste (cf. [[feedback-az-logs-cp1252]]).
- Démarré par `updater.py` si `AGENT_MAIL=1`. `AGENT_MODE=observation` par
  défaut : rien n'est écrit.
