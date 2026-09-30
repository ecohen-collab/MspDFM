---
name: widget-dashboard
description: Ajouter ou modifier un outil/onglet du dashboard MSP (page HTML + section updater.py + routes /api + cache /data + entrée du rail). À charger avant d'écrire la moindre ligne d'un nouveau widget, d'une nouvelle route /api, ou d'un croisement entre une source vendeur et la facturation Artis.
---

# Ajouter un outil au dashboard

Un outil = **une page HTML autonome** + **une section dans `updater.py`** + **des routes `/api/<nom>/…`** +
**une entrée dans le rail d'`index.html`**. Pas de framework, pas de build : `http.server` sert les
fichiers du répertoire, le front est du JS vanilla.

## 1. La page

Partir de `rentabilite.html` : c'est le modèle le plus proche d'un widget d'analyse (mêmes variables
CSS, même en-tête, même table triable, même export). Copier son squelette, garder les classes
(`.metric`, `.table-wrap`, `.num`, `.num-dim`, `.tag`, `.banner`).

Deux lignes obligatoires, sinon la page s'affiche hors de la coquille :

```html
<script>/*dfm-shell-redirect*/(function(){try{if(window.self===window.top&&!/[?&]raw=1/.test(location.search)){location.replace('/index.html#<id>'+location.hash.replace('#','&'));}}catch(e){}})();</script>
```
et `<script src="/assistant.js" defer></script>` en fin de `<body>`.

Le rôle se lit avec `GET /api/whoami` → `is_admin`, `is_compta`, `role`. Masquer les actions
d'écriture aux non-admins **côté page ET côté serveur** (la garde client ne protège rien).

## 2. L'entrée du rail

Dans `index.html`, ajouter un item au bon groupe de `GROUPS` (Assistance / Facturation & marge /
Opérations / Infogérance M365 / Comptabilité) :

```js
{id:'azure', ico:LOGOS.azure, stack:['td','artis'], n:'Consommation Azure', u:'/azure.html',
 d:"Une phrase qui dit ce que l'outil montre.", t:['TD SYNNEX','Mensuel']},
```

L'`id` sert d'ancre (`#azure`) et doit correspondre au `dfm-shell-redirect` de la page. Un logo
s'ajoute dans `LOGOS` (fichier dans `logos/`, ou pastille SVG inline). `adm:1` réserve la carte aux
admins — à doubler d'un `ROLE_GATED_PAGES` dans `updater.py` pour un gating réel.

**Piège connu** (cf. refonte UI) : la coquille remonte les onglets de l'iframe via `harvest(doc)`,
appelé **avant** `injectChrome(doc)`. Ne pas toucher à cet ordre.

## 3. La section dans `updater.py`

Une section encadrée de `# ═══`, placée à côté des autres sources. Toujours dans cet ordre :

1. constantes (`<NOM>_FILE = DATA_DIR / "<nom>.json"`) ;
2. helpers privés `_<nom>_…` qui parlent à l'API vendeur et renvoient `(valeur, erreur)` ;
3. fonction de collecte `<nom>_collect(...)` qui écrit le cache ;
4. fonction de lecture `<nom>_load()` qui ne fait **aucun** appel réseau.

## 4. La règle du cache — la plus importante

**Une page ne doit jamais déclencher un appel vendeur à son chargement.** La collecte écrit un JSON
sur `DATA_DIR` (`/data`, volume Azure Files), la page lit ce fichier. Comme le dashboard avec
`msp_data.json`.

Pourquoi : les jetons rotatifs à usage unique (TD SYNNEX, Yooz) seraient consommés à chaque F5, les
API vendeur throttlent, et Artis facture des licences sur les connexions web.

Écriture atomique systématique :

```python
tmp = FICHIER.with_suffix(".tmp")
tmp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
tmp.replace(FICHIER)
```

Si le cache garde plusieurs périodes, les indexer par clé explicite (`periodes["LAST_MONTH"]`) et
inclure les dates dans la clé pour une plage libre (`CUSTOM:2026-07-15:2026-08-14`), sinon chaque
relevé écrase le précédent et la comparaison devient fausse.

## 5. Les routes

Dans `_api_dispatch`, un bloc par outil, placé avant le `return 404` final :

```python
if len(parts) >= 3 and parts[0] == "api" and parts[1] == "<nom>":
    sub = parts[2]
    try:
        if sub == "conso" and method == "GET":
            return self._send_json(200, <nom>_load())          # lecture : ouverte
        if sub == "collecte" and method == "POST":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            ...
        return self._send_json(404, {"error": "route <nom> inconnue"})
    except Exception as e:
        return self._send_json(500, {"error": _sans_token(e)})
```

`_sans_token(e)` est **obligatoire** sur tout message d'erreur : les exceptions `requests`
recopient l'URL complète, et les appels Artis portent le jeton dans la query string — sans ça il
s'affiche en clair à l'écran.

## 6. Tout ce qui dépasse la minute passe en tâche de fond

L'ingress coupe les requêtes longues : le navigateur reçoit une réponse vide et affiche
`Failed to execute 'json' on 'Response': Unexpected end of JSON input`, alors que le traitement a
parfois abouti côté serveur.

Utiliser `_azure_lancer("libellé", fn)` (mécanisme générique malgré son nom) + `GET /api/azure/etat`.
Une seule tâche à la fois, ce qui protège aussi les jetons rotatifs. Côté page :

```js
const r = await fetch(url, opts); const d = await r.json();
if(!d.ok && !d.lancee) throw new Error(d.error);
for(;;){ await new Promise(s=>setTimeout(s,2000));
  const e = await (await fetch('/api/azure/etat?t='+Date.now())).json();
  if(e.en_cours){ /* afficher e.secondes */ continue; }
  if(e.erreur) throw new Error(e.erreur);
  return e.resultat; }
```

## 7. Croiser une source vendeur avec Artis

- Le rattachement se fait par **code Artis extrait du nom vendeur** : `extract_artis_code(nom)`
  (« 12522 - VERKINDER » → `12522`). Il renvoie `None` si absent → prévoir la ligne « hors Artis ».
- Nom et organisation du client : `reconciliation[].nom` / `.org`, et `artis_full_mapping` pour
  les clients sans contrat informatique.
- Les lignes de facturation détaillées sont dans `reconciliation_par_contrat[].rfc_lines`
  (`{msc, rfc, qty, prix_unitaire, total, service, date_fin}`).
- **Avant de conclure qu'un service n'est pas facturé, vérifier `is_relevant_msc`** (`updater.py`,
  ~l. 1849) : ce filtre s'applique au **premier** appel Artis, donc une MSC hors liste n'existe
  nulle part — ni dans `rfc_lines`, ni dans `artis.*`, ni dans aucun écart, et sans message
  d'erreur. `/api/maj/reclassify` ne la ramène pas (il re-classe l'existant). Seul un cycle
  complet, ou un scan ciblé, va la chercher.
- Écarter les contrats inactifs avec `_statut_inactif()`, mais **garder** « Résiliée.Dans les
  délais » : le préavis est encore facturé.

## 8. Quand la détection est heuristique, le dire

Un libellé n'est jamais une preuve. Séparer ce qui est **certain** de ce qui est **à confirmer**
(cf. `_motif_azure` : `"azure"` quand le libellé nomme Azure, `"hebergement"` pour un
« serveur virtuel » qui peut être hébergé ailleurs), afficher les deux distinctement, et fournir
un écran de revue ligne à ligne pour qu'Elliot tranche. Ne jamais gonfler un total avec du
probable sans le signaler.

Même exigence sur les totaux : un rapport vendeur mélange souvent plusieurs natures. Le rapport
« Microsoft CSP Billing » contient les licences **et** la conso Azure ; seules les lignes portant un
compteur (`Meter Category`) sont de la consommation. Sans ce tri, le total passait de 18 439 € à
155 109 €. Quand un tri de ce genre est appliqué, l'afficher.

## 9. Avant de dire que c'est fini

- `python -c "import ast; ast.parse(open('updater.py',encoding='utf-8').read())"` puis un vrai
  `import updater` (attrape ce qu'`ast` ne voit pas).
- Tester les fonctions pures sur des données réelles : `msp_data.json` se télécharge depuis Azure
  Files (cf. skill `deployer`). Les formats FR et US cohabitent dans les rapports vendeur
  (`1 234,56 €` et `1,234.56`) — vérifier les deux.
- Contrôler la non-régression quand on touche à `_classify_rfc` : compter les lignes par service
  avant/après sur l'ensemble du fichier. Les autres services doivent être **strictement** inchangés.
- Déployer (skill `deployer`), vérifier la révision, et dire à Elliot ce qu'il doit cliquer.

## 10. Sobriété de l'interface

Un bouton principal qui fait le travail complet, le reste replié derrière un lien « outils ▾ ».
Retour d'Elliot, textuel : « tes en train de tout compliquer avec plein de boutons... fait juste
une maj que je vois tout ». Les écrans de diagnostic sont pour moi, pas pour lui.
