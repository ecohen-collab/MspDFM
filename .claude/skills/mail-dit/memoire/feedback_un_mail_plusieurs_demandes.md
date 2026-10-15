---
name: feedback-un-mail-plusieurs-demandes
description: "Le numero dans l'objet place la REPONSE du client, pas tout le contenu du mail : un refus clot, les demandes neuves prennent leur propre DIT"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-09T09:12:47.713Z
---

**L'unite n'est pas le mail, c'est la demande.** Un mail peut en porter zero,
une ou trois, et le numero dans l'objet ne dit que l'appartenance de la
**reponse** — pas celle de tout le contenu.

Elliot, le 2026-10-09, sur la DIT **100175753** (SFAF) : « la cliente a refuse,
puis a fait une nouvelle demande dans le meme mail ; l'IA a juste rajoute la
nouvelle demande en editant, mais il ne fallait absolument pas ici ».

Karine DOGNIN SAUZE faisait deux choses opposees dans un seul message : elle
**refusait** la demande d'acces de Mme Priouret (la conclusion du ticket) et
demandait **trois taches neuves** sur d'autres personnes (droits drive pour
Martine Leonard, retrait pour Valerie Mercier, rappel telephonique). Tout
colle en commentaire d'un ticket dont l'objet parle d'autre chose : le
technicien lit le refus, clot, et les trois demandes disparaissent — invisibles
dans toute file, et non facturables. Corrige par la DIT **100177064**.

**Why:** j'ai aiguille sur l'en-tete sans trier le contenu, en appliquant
« le numero decide seul » ([[feedback-reponse-ticket-jamais-nouveau]]) comme une
regle mecanique. Cette regle vise le client qui repond a SA demande ; elle
suppose que le mail parle du meme travail.

**How to apply:** devant un mail sur un fil de ticket, deux questions dans cet
ordre — **ferme-t-il la demande d'origine** (refus, abandon, « c'est resolu ») ?
alors le commentaire ne porte que cette conclusion ; **reste-t-il des demandes
etrangeres a l'objet du ticket** (autre beneficiaire, autre geste) ? alors
**DIT neuve** pour celles-la. Un client qui precise ou relance SA demande reste
un simple commentaire. Regle ecrite dans `.claude/skills/mail-dit/SKILL.md`,
section « Un mail, plusieurs demandes » — a republier sur `/data` pour l'agent
([[project-agent-mail-azure]], [[feedback-cause-puis-garde-fou]]).
