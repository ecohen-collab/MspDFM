---
name: feedback_joindre_les_pieces
description: "Une piece jointe utile s'ATTACHE a la DIT, elle ne se mentionne pas dans le commentaire"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-28T14:57:41.910Z
---

Quand le mail porte une piece jointe utile, il faut **l'attacher a la DIT**, pas
seulement l'annoncer dans le commentaire. L'agent qui ouvre le ticket doit
trouver le document dedans.

**Why:** remonte a Elliot le 2026-09-28 sur la DIT 100175612 (CENTRE EXPLORE
VISION) : « il n'a pas mis le document ci-joint ». J'avais ecrit « Piece jointe
au mail d'origine : ... .pdf » dans le commentaire et je m'etais arrete la.
Le PDF listait tout ce que le client attendait pour la passation — le ticket
etait inutilisable sans lui. Meme defaut sur 3 autres DIT du jour : la fiche
d'onboarding DPF (100175596) et les captures d'ecran de CFD (100175592),
DENIOT (100175623) et JACQUESON (100175613).

**How to apply:**
- `POST /documents/DIT/{numero}` en multipart (`files={"content": (nom, octets,
  type_mime)}`), repond **204**. C'est ce que fait
  `mail_dit_ecriture.joindre_fichier()`.
- Recuperer les octets par Graph :
  `GET /me/messages?$filter=internetMessageId eq '<...>'` puis
  `/messages/{id}/attachments` — `contentBytes` est en base64.
  `mail_auth.access_token()` rafraichit le jeton depuis `data/mail_tokens.json`.
- **Le tri reste celui de [[feedback_pieces_jointes_jugement]]** : on exclut les
  images de signature, on garde les fichiers et les captures que le texte
  annonce. Reperes utiles : un vrai fichier a `isInline: false` ; une signature
  Outlook s'appelle `image00x.png` et pese quelques Ko ; `ATT00001.png` est le
  nom que Gmail donne a son inline (meme taille d'un mail a l'autre = logo).
  Une capture d'ecran annoncee par le texte se joint **meme si** elle s'appelle
  `image002.png`.
- Attention : apres un deplacement de mail, l'identifiant Graph change. Retrouver
  le message par son `internetMessageId`, qui lui ne bouge pas.

Voir [[feedback_commentaire_dit_sans_avis]] pour ce que contient le commentaire.
