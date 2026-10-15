---
name: feedback-soc-sans-mco
description: Une alerte SOC sans mco@dfm.fr en copie se transfère à MCO puis se classe en 04 INFORMATIONS
metadata:
  type: feedback
---

Alerte SOC (`[SOC x <client>]`) :
- **mco@dfm.fr en copie** → à la corbeille, elle est déjà prise en charge ;
- **mco absent** → **la transférer à mco@dfm.fr**, puis classer le mail en
  `04 - INFORMATIONS`.

**Why:** Elliot, 2026-09-18. La version précédente s'abstenait (« rien ne dit
qu'elle a été prise en charge ») et laissait dormir une alerte de sécurité dans
une boîte que personne ne surveille. Ne pas savoir si quelqu'un l'a vue est
précisément la raison de la transmettre.

**How to apply:** `decider_seul()` dans `mail_dit.py`, première branche ; la clé
`apres` d'une décision « transferer » indique le dossier de destination finale
quand ce n'est pas `01 - DIT ARTIS`.
