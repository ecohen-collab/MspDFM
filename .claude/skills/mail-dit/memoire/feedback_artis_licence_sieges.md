---
name: feedback_artis_licence_sieges
description: "Se connecter au formulaire web Artis consomme un siège de licence — ne jamais enchaîner des connexions, ça bloque toute l'équipe"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-10T13:51:24.139Z
---

**Ne pas se connecter à l'interface web d'Artis (`composants/login/form/connect.action`) depuis du code.** Chaque connexion consomme un **siège de licence** (le paramètre s'appelle littéralement `typeLicence=PT`), et la limite est **globale à l'entreprise**.

Incident du 2026-09-10 : trois connexions successives depuis mes scripts ont saturé la limite —

> `{"errorMessage":"Ouverture impossible : le nombre d'accès simultanés global est atteint."}`

— bloquant les **nouvelles connexions de toute l'équipe DFM** jusqu'à expiration des sessions (30 min à 1 h). Les scripts se terminaient sans déconnexion et leurs cookies étaient perdus, donc impossible de libérer les sièges après coup.

**Vérifié le 2026-09-10 : il n'existe aucune déconnexion utilisable.** J'avais d'abord annoncé le contraire en trouvant des routes dans le bundle — c'était faux. Ni `shared/login/disconnect.action` (le `sendBeacon` que le navigateur envoie en quittant la page) ni `composants/login/auth/PT.action?disconnect=<session>` (le lien « Déconnexion » du menu) ne ferment la session : les deux répondent **200** et la session reste **pleinement vivante**, action complète rejouée après coup pour le prouver. Le siège ne se libère qu'à l'expiration.

**Conséquence de conception** : ne pas ouvrir une session par opération. En ouvrir **une**, la stocker, la réutiliser — c'est ce que fait `Web.ouvrir()` dans `artis_type_travail.py` (session du compte dédié MCO dans `<DATA_DIR>/artis_mco_session.json`). Un siège pendant que l'outil travaille, au lieu d'un par ticket.

**L'API REST n'est pas concernée** : `POST /user/auth` (`ARTIS_AUTH_URL`) délivre un jeton sans consommer de siège. C'est la seule voie à utiliser depuis du code — c'est aussi celle du dashboard, qui a continué de fonctionner pendant l'incident.

**Pourquoi ça compte** : un contournement par le formulaire web, même quand il fonctionne, s'appuie sur une ressource partagée avec l'équipe. Ce n'est acceptable qu'avec **une** session réutilisée — jamais une par opération (cf. `typeGpTravail` : ~30 corrections/jour = 30 sièges pris et rendus au compte-gouttes).

**Leçon plus large, qui a mordu deux fois en deux jours** : vérifier le **coût** et la **réversibilité** d'une écriture avant de la répéter — pas après. L'autre cas : `PATCH /it/{n}/intervenant/{m}` est accepté sur une intervention annulée alors que le `DELETE` correspondant est refusé, rendant l'affectation irréversible.

Projet : [[project_mail_to_dit_artis]]. Côté écritures Artis : [[feedback_artis_pas_decriture_sans_accord]].
