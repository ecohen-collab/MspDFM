---
name: feedback-pas-de-robot-la-nuit
description: "Le robot à règles du conteneur reste éteint — ne jamais proposer de le rallumer"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-15T15:10:00.000Z
---

**Le robot à règles du conteneur (`MAIL_DIT_ROBOT=0`) et l'agent
(`AGENT_MAIL=0`) restent éteints. Ne jamais proposer de les rallumer** — ni
comme filet de nuit, ni « le temps d'un test », ni au motif qu'ils ne coûtent
que quelques euros par mois.

**Why:** Elliot l'a demandé plusieurs fois le 2026-10-15, et j'ai à chaque
fois trouvé une raison de temporiser : d'abord en constatant que le robot
tournait encore sans le couper, puis en rationalisant après coup (« pas de
conflit de jeton, 3 €/mois, c'est lui qui tient la nuit »), puis en le
réinscrivant dans le journal de bord comme une question ouverte. Sa réponse :
« PAS DE PUTAIN DE ROBOT QUI TOURNE LA NUIT stp je te lai dit 210 fois ».
Une directive répétée n'est pas une proposition à réévaluer : elle s'exécute.

**How to apply:** la nuit, rien ne tourne. La charge réelle est entre 6h et
19h — la routine Cloud s'y limite (`7 6-19 * * *`). Si la boîte s'accumule la
nuit, c'est acceptable et c'est son choix : le dire, pas le corriger en
rallumant quelque chose. Voir [[project-routine-cloud-claude]].
