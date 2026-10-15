---
name: project-gdap-mailbox-dashboard
description: POC dashboard mailboxes Exchange Online via GDAP — ticket envoyé à Benjamin 2026-05-25.
metadata: 
  node_type: memory
  type: project
  originSessionId: 2c04e5d8-22b6-41b7-920b-e42f7521aa7e
---

POC nouveau dashboard MSP : afficher l'état des BAL Exchange Online par client, via les relations GDAP du tenant DFM Partner Center.

**Why:** Donner à Elliot une vue rapide sur l'usage des mailboxes (stockage, dormance, règles forwarding suspectes, licences EXO assignées vs disponibles) à travers tous les clients qu'on administre, sans devoir se connecter à chaque tenant un par un.

**How to apply:**
- POC ciblant 1-2 clients pilotes d'abord, puis extension à tous les GDAP actifs.
- Le module Python tournera dans la Container App msp-dashboard existante (cycle MAJ daily 20h, comme les autres sources).
- App à créer : `MSP-Dashboard-GDAP-Mailbox` (tenant DFM), permissions Graph Application : `DelegatedAdminRelationship.Read.All`, `User.Read.All`, `MailboxSettings.Read.All`, `Reports.Read.All`, `Directory.Read.All`.
- Secret stocké en Container App secret `graph-gdap-secret`.
- Côté GDAP, vérifier que les rôles incluent au minimum `Exchange Recipient Administrator` sur les clients pilotes.

**Statut au 2026-05-25 :** ticket envoyé à Benjamin Ozon (DSI). En attente de :
1. Création de l'app + permissions + admin consent
2. Génération du client secret
3. Confirmation GDAP roles compatibles sur clients pilotes

**Next steps quand secrets dispos :**
- Code module `m365_gdap.py` (squelette à préparer en amont si Elliot le demande)
- Page HTML `gdap_mailboxes.html` (vue par client + drill-down par BAL)
- Carte sur index.html (admin-only initialement)

Voir [[feedback-datto-pas-decriture-sans-accord]] pour le pattern read-only par défaut sur les APIs externes.
