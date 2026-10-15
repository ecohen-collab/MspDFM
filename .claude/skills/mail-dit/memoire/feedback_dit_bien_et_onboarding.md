---
name: feedback-dit-bien-et-onboarding
description: "Bien d'une DIT = INFOGERANCE, à défaut BAL EXCHANGE 365 ; un onboarding va en N3"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-17T08:00:47.437Z
---

Toute DIT informatique porte un **bien** : `INFOGERANCE` du client, et **si le
client n'en a pas, `BAL EXCHANGE 365`**. Ne jamais laisser le bien vide.

Une demande d'**onboarding** (arrivée de collaborateurs : création d'adresses,
de sessions, d'accès) prend le symptôme `ADM-ON` et part en **N3** (UO `HLN3`),
pas en hotline N1 — le lien symptôme→UO est déjà dans `uo_pour()`, donc corriger
la détection du symptôme suffit.

**Why:** Elliot, 2026-09-17, sur les DIT 100174308 et 100173884 (client 30802
AX CONSULTING, qui n'a pas d'infogérance mais une BAL Exchange 365). Le motif
`ADM-ON` exigeait « arrivée » collé à « collaborateur » et ratait « l'arrivée de
deux nouvelles collaboratrices » — pluriel et mots intercalés.

**How to apply:** repli du bien dans `bien_informatique()` (`mail_dit.py`) ;
motif élargi dans `SYMPTOMES_REGLES` (`mail_regles.py`), mesuré sur les 1181
mails de 14 jours : 21 → 31 mails reconnus, 8 vrais onboardings gagnés, 2
tournures parlant d'arrivants sans en être une.

**Limite :** le symptôme d'une DIT existante ne se corrige ni par REST (aucune
route) ni par le formulaire web (champ « loupe » qui répond OK sans conserver).
Et le bien d'une intervention **déjà cloturée avec un CRIT** est refusé
(HTTP 500) : il faudrait rouvrir la DIT. Voir [[reference_artis_formulaire_web]]
et [[reference_artis_dit_non_facturable]].
