---
name: project-mail-modele-lecture
description: "Le moteur mail appelle Claude Haiku sur les mails qu'aucune règle ne tranche, et retient la réponse comme règle"
metadata: 
  node_type: memory
  type: project
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-17T08:52:21.927Z
---

Depuis le 2026-09-17, `mail_modele.py` donne au moteur de `mail_dit.py` la
lecture qui lui manque. Appelé **en dernier** dans le cycle, uniquement sur les
mails qu'aucune règle ne prend et qui ne citent aucun ticket.

**Why:** Elliot, 2026-09-17 — « le robot et toi c'est la même chose, vous devez
fonctionner de la même manière ». Mesure du jour : sur 233 propositions, 73
étaient bloquées sur « client non établi » et 73 sur « candidat_faible » ; ce
sont ces deux familles que je traitais à la main chaque matin.

**How to apply:**
- Modèle `claude-haiku-4-5`, outil forcé `decider` → {action: ranger|creer|
  laisser, dossier, client, confiance, pourquoi}. **Coût mesuré : 0,002 €/mail.**
- Plafond **10 €/mois** (`PLAFOND_EUR`, demandé par Elliot), vérifié avant
  chaque appel. Crédit épuisé, clé absente, erreur réseau → `avis()` rend
  `None` et le moteur continue exactement comme avant.
- **Le robot s'auto-remplit** : une réponse `ranger` écrit une règle apprise
  (expéditeur → dossier) dans `/data/mail_regles_apprises.json`, consultée
  avant tout appel. Le deuxième mail du même expéditeur est gratuit.
- Un mail n'est soumis qu'une fois (`/data/mail_modele_vus.json`), 5 lectures
  par cycle au maximum.
- Le modèle ne court-circuite aucun garde-fou : `creer` et `laisser` repassent
  par le chemin normal, seul `ranger` en confiance haute agit directement.
- Dépense visible dans `etat()["modele"]` : plafond, dépensé, restant, appels,
  erreurs, règles apprises.

Assouplit encore [[feedback_chatbot_cout_zero]], comme
[[project_moteur_tickets_365]] l'avait fait. Voir [[project_mail_to_dit_artis]].
