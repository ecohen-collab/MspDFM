---
name: feedback_bal_support_dfm
description: "support@dfm.fr est la BAL de production de tout le support info — écritures autorisées seulement pour déplacer un mail traité, jamais d'envoi"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-09T10:19:33.598Z
---

`support@dfm.fr` est la **boîte mail de production de tout le support informatique DFM**, pas un bac à sable. Aucun envoi, suppression, règle ni réponse automatique sans accord explicite d'Elliot, demandé juste avant l'action.

**Ce qui est autorisé depuis le 2026-09-09** : déplacer un mail traité vers le dossier `01 - DIT ARTIS SUPPORT`, **et seulement après création réussie du DIT**. Le jeton porte donc `Mail.ReadWrite` — mais **jamais `Mail.Send`** : l'outil ne doit pas pouvoir écrire à un client. Ancien jeton lecture seule gardé sous `data/mail_tokens.json.lecture-seule`.

Deux verrous cumulatifs protègent toute écriture (cf. `mail_dit_ecriture.py`) : `confirmer=True` **et** le fichier `<DATA_DIR>/mail_dit_ecriture.txt` contenant `OUI`. Sans les deux, les fonctions renvoient une simulation sans émettre de requête. Supprimer ce fichier désarme l'outil.

**Structure des dossiers** (expliquée par Elliot, essentielle) :
- **boîte de réception** = demandes non triées, c'est **la file d'attente** (~27 mails, vidée en continu par les agents)
- **`01 - DIT ARTIS SUPPORT`** = mails informatiques **déjà saisis** dans Artis, nouveau DIT ou suivi d'un DIT existant (**20 729 mails**)
- autres métiers : ailleurs, hors périmètre

Ne **jamais** scanner l'archive dans un cycle temps réel : le delta Graph la renvoie 10 messages par page, soit 2 000+ requêtes et 7 minutes. L'information « ce mail a été traité » se déduit gratuitement : il a quitté la boîte de réception.

Le connecteur MCP « claude.ai Microsoft 365 » est authentifié **en tant que support@dfm.fr** (pas en délégation) — ses outils d'écriture (`outlook_send_mail`, `outlook_trash_thread`, `outlook_batch_delete_messages`, `outlook_set_vacation`, `outlook_create_filter`…) agissent en direct sur la prod. À ne pas appeler.

**Pourquoi** : une fausse manœuvre impacterait tout le support et des tickets clients réels. Elliot l'a rappelé explicitement au démarrage du projet le 2026-09-08.

Projet : [[project_mail_to_dit_artis]]. Côté Artis, même règle : [[feedback_artis_pas_decriture_sans_accord]].
