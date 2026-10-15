---
name: reference_artis_formulaire_web
description: "Piloter le formulaire web Artis depuis du code — les 3 pièges qui font perdre des heures (userEvent en minuscule, lien générique, select disabled)"
metadata: 
  node_type: memory
  type: reference
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-10T13:51:14.393Z
---

Quand l'API REST Artis ne sait pas écrire un champ (cas de `typeGpTravail`), le formulaire web le fait. Chemin complet, validé le 2026-09-10 : **`artis_type_travail.py`** dans le repo, et `NOS_DIT_CREES.md` pour le raisonnement.

Les trois découvertes non devinables :

1. **Les `userEvent` sont en minuscule initiale.** Le HTML appelle `validate_Evt_ccIt_Modifier_()`, mais cette fonction — définie dans `shared/js.action?portail=false&domaine=services&composant=<ccXxx>`, un fichier séparé du bundle — poste `evt_ccIt_Modifier_`. Avec la majuscule, le serveur répond **200 et ignore l'événement en silence**.
2. **Le lien générique** : champs cachés `actionRedirectLink` / `…Domaine` / `…Entree` / `…Id` + `userEvent=evt_genericLink` mènent directement à n'importe quel objet métier, dans le mode voulu. Les pages rendues en donnent des exemples réels : `actionRedirect_link(this, 'ccIt','services','modif','723701;924780')`. Les identifiants internes viennent de l'index SOLR (`services/solr/global-search/select` → `data_long_id_dit`, `idBo`). Ça évite toute la navigation écran par écran, qui dépend de l'état du chemin courant et casse tout le temps.
3. **Les champs d'une ligne de tableau dépliée** : `shared/arrays/expand/show.action` (arrayName + rowKey) puis `shared/ajax-container/load.action` rendent `expandable_<rowKey>_<widget>`. Artis peut les rendre **`disabled`** — le navigateur ne les enverrait pas, le serveur les accepte quand même.

Écriture : `shared/form/flush.action` puis `shared/form/save.action` → `returnCode: Enregistrer_OK`. **Ces deux appels exigent un champ `componentName` qui n'est pas dans le formulaire** : `ajaxFormParams` l'ajoute à la main, avec `cKey`, `cStatus` et `session`. Sans lui, les deux répondent la page « Erreur — ouvrir une nouvelle session » alors que le reste de la session marche parfaitement : le message oriente vers un faux problème et coûte une heure. **Ne jamais fabriquer le formulaire** : reposter celui que le serveur vient de rendre avec un seul champ changé (`save.action` couvre tout l'écran, y compris le type de facturation), puis vérifier par l'API REST. Corps encodé en **ISO-8859-15** (`accept-charset` du formulaire). `submit.action` est relatif : l'URL dépend du domaine du composant.

**Une saisie laissée en mode (M) bloque la session** : `removeContextList` refuse de dépiler et toute la navigation suivante part de travers. Sortir par `oK_Enregistrer` ou `evtSortieChemin`, sur tous les chemins d'erreur.

Le bundle JS s'obtient **sans authentification** : `resources/js/all-min.js` (7,5 Mo) — c'est là qu'on lit la mécanique.

Compte dédié : **MCO**, créé par Elliot le 2026-09-10, identifiants dans `<DATA_DIR>/artis_mco.json` (répertoire ignoré par git). **Le dashboard tourne sur MCO depuis le 2026-09-10** (secrets `artis-username` / `artis-password` de la Container App + `.env` local). La première mesure montrait un trou — MCO ne voyait pas l'organisation interne `ACC`, soit 436 clients CSI perdus sur 1 200 ; Elliot a élargi les droits et la seconde mesure donne un **écart nul** (1 200 clients de part et d'autre, 18 769 contrats contre 18 767, les 2 en plus étant des contrats « Services Intelligence Artificielle » de l'orgInt `MOO`). Vérifié avec le vrai code du dashboard : `artis_get_clients` 1 200, `artis_get_dit(14 j)` 2 495, `artis_get_non_productif` 586.

**Leçon de méthode** : mesurer les droits d'un compte de service en comparant les **volumes réels endpoint par endpoint** avec le compte de référence, pas en lisant une liste d'habilitations. Le trou ne se voyait que sur `/contrat?maxResults=9999999`, et il aurait été silencieux — aucune erreur, juste des clients en moins.

En revanche MCO suffit largement pour l'outil de correction : il lit n'importe quelle DIT par l'API REST et la retrouve dans l'index SOLR, y compris pour ces 436 clients — vérifié.

Coût : cette voie exige une session web, donc un siège de licence, et **aucune déconnexion ne marche** — voir [[feedback_artis_licence_sieges]]. Donc : une session réutilisée, pas une par opération. Écritures : [[feedback_artis_pas_decriture_sans_accord]]. Projet : [[project_mail_to_dit_artis]].
