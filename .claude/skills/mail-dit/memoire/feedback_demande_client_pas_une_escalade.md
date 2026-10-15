---
name: feedback_demande_client_pas_une_escalade
description: "Une demande d'un client sur SON environnement est une DIT ordinaire, jamais une escalade a Benjamin — meme pour une elevation de droits"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-01T07:58:18.062Z
---

Elliot, le **2026-10-01**, sur la DIT **100175965** (SRP CONSTRUCTION, qui
demandait une elevation `Mail.ReadWrite` dans son Entra ID) : « quel rapport
Benjamin, c'est une demande d'un client sur son environnement, il faut juste
creer la DIT comme t'as fait et on va s'en occuper, donc rien a faire ».

**Why** : j'avais remonte la demande comme « decision humaine en attente »
parce qu'elle touchait a de l'administration sensible. C'est confondre deux
perimetres. Benjamin Ozon et la DSI DFM traitent **nos** ressources internes
(Azure, RBAC, app registrations DFM). Ce que le client demande sur **son**
tenant est le metier quotidien de l'infogerance : l'equipe hotline le fait.

**How to apply**

1. Le ticket cree **est** l'action attendue. Ne pas le signaler en plus comme
   bloque ou en attente d'arbitrage.
2. Une demande sensible chez le client (droits eleves, acces privilegie,
   delegation large) se traite comme les autres — la sensibilite se mentionne
   au rapport, elle ne change pas le circuit.
3. L'escalade a Benjamin reste pour nos ressources a nous : voir le CLAUDE.md
   du projet.

Voir [[project_skill_mail_dit]], [[feedback_commentaire_dit_sans_avis]].
