---
name: deployer
description: Déployer le dashboard MSP en production (Azure Container Apps), mettre à jour un secret, lire un fichier de /data, vérifier une révision, faire un rollback. À charger avant tout déploiement, toute commande az, tout git, ou avant d'exécuter python en local sur ce repo.
---

# Déployer le dashboard MSP

Prod = **Azure Container Apps**, resource group `GRP-RES-CONTENEURS`, app `msp-dashboard`,
ACR `dfmplateformeprocess`, storage `dfmmspdashboardstor` / share `msp-data` monté sur `/data`.
FQDN : `msp-dashboard.gentlesand-80781e11.francecentral.azurecontainerapps.io`.

Les mentions d'Authelia et de `10.127.15.239` dans `index.html` sont un **reliquat** de l'ancienne
VM Linux : ce n'est plus l'infra.

## Les binaires ne sont pas dans le PATH

Ils sont installés, mais le shell les ignore. Toujours les appeler par chemin complet :

```powershell
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"   # Python 3.12
$az = "$env:ProgramFiles\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"  # Azure CLI 2.90
$git = "$env:ProgramFiles\Git\cmd\git.exe"                        # Git 2.55
```

Pour un script qui les appelle par leur nom (`deploy.ps1`), préfixer le PATH :

```powershell
$env:PATH="$env:ProgramFiles\Microsoft SDKs\Azure\CLI2\wbin;$env:ProgramFiles\Git\cmd;$env:PATH"
```

Ne jamais conclure « l'outil n'est pas installé » sans avoir testé le chemin complet.

## Vérifier avant de pousser

```powershell
& $py -c "import ast; ast.parse(open('updater.py',encoding='utf-8').read()); print('OK')"
```

Puis un vrai import (attrape les erreurs qu'`ast` ne voit pas), avec un DATA_DIR jetable et le
repo dans le PYTHONPATH :

```powershell
$env:DATA_DIR = "<scratchpad>\datatest"; $env:PYTHONPATH = (Get-Location).Path
& $py -c "import updater; print('import OK')"
```

`pip install -r requirements.txt` a déjà été fait sur ce poste.

## Déployer

```powershell
.\deploy.ps1
```

Build ACR + `containerapp update` + contrôle que l'image servie est bien celle construite + smoke.
Sortie attendue : `OK · msp-dashboard--00007xx (tag …) · ~100s · smoke HTTP 401`.

**`401` et `302` sont normaux** : Easy Auth refuse un appel anonyme, ce qui prouve que l'app répond.

### `acr build` : Permission denied sur un fichier bureautique

`az acr build` empaquette tout le contexte non exclu par `.dockerignore`. Un `.docx`/`.xlsx`
ouvert dans Word ou verrouillé par OneDrive fait échouer le build :

```
ERROR: [Errno 13] Permission denied: '.\Fiche_client_DFM (1) (1).docx'
```

Ces extensions sont désormais exclues (`*.docx *.doc *.xlsx *.xlsm *.xls *.eml *.msg *.pdf
*.csv *.har`). Si l'erreur revient sur un autre type, l'ajouter à `.dockerignore` plutôt que de
fermer le fichier : le document n'a rien à faire dans l'image.

### Jamais `-Commit`

`deploy.ps1 -Commit` fait `git add .` + `git push`. Or le dépôt est très désynchronisé (~150
fichiers non suivis) et `.gitignore` **ne couvre pas** `yooz_refresh.txt` (jeton Yooz en clair),
`mailcorpus_elliot.json`, les `.har`, ni les PDF de factures clients — contrairement à
`.dockerignore`, qui les exclut correctement de l'image. Un seul `-Commit` publierait tout ça dans
l'historique git.

Pour committer : fichier par fichier (`git add updater.py azure.html index.html`).

## Vérifier la révision

```powershell
& $az containerapp revision show --name msp-dashboard --resource-group GRP-RES-CONTENEURS `
   --revision <nom> --query "{sain:properties.healthState, etat:properties.runningState}" -o json 2>$null
```

Attendu : `Healthy` / `RunningAtMaxScale`. Terminer le `2>$null` : `az containerapp` écrit un
WARNING sur stderr à chaque appel (extension containerapp), ce qui devient une erreur terminante
sous `$ErrorActionPreference='Stop'`.

Rollback : `az containerapp ingress traffic set … --revision-weight <ancienne>=100`.

## Lancer `deploy.ps1` depuis PowerShell, pas depuis Bash

Appelé via `powershell -File .\deploy.ps1` depuis le shell Bash, le script échoue sur
`FAIL · containerapp update` alors que la commande équivalente passée à la main fonctionne
(constaté le 2026-10-01). Le `AzQuiet { ... 2>$null }` masque la cause réelle.

Si l'on est dans Bash : construire avec `deploy.ps1` puis, en cas d'échec de cette étape,
relever le tag construit et faire l'update directement —

```bash
AZ="/c/Program Files/Microsoft SDKs/Azure/CLI2/wbin/az.cmd"
"$AZ" acr repository show-tags --name dfmplateformeprocess \
      --repository msp-dashboard --orderby time_desc --top 3 -o tsv
"$AZ" containerapp update --name msp-dashboard --resource-group GRP-RES-CONTENEURS \
      --image dfmplateformeprocess.azurecr.io/msp-dashboard:<tag> \
      --query "properties.latestRevisionName" -o tsv
```

## Easy Auth : tout est fermé par défaut

```
globalValidation.unauthenticatedClientAction = RedirectToLoginPage
```

Toute requête sans session Entra est redirigée vers la page de connexion — **y compris les
appels d'API**. D'où le `401` sur une route pourtant publique dans le code : le refus vient
d'Easy Auth, pas de l'application. C'est aussi pourquoi un smoke test anonyme renvoie 401 ou
302 et que c'est le signe que tout va bien.

Pour exposer un chemin à un appelant sans compte DFM (partenaire, automate tiers), il faut
l'exclure explicitement :

```bash
"$AZ" containerapp auth update --name msp-dashboard --resource-group GRP-RES-CONTENEURS \
      --excluded-paths "/api/partenaire/soc"
```

**Ne jamais le faire de sa propre initiative.** Ouvrir un chemin, c'est le rendre accessible
à tout Internet : la seule protection restante est celle que porte le code (clé d'API,
journalisation, périmètre des données). Cela se décide avec Elliot, explicitement, chemin par
chemin — jamais un préfixe large type `/api/*`.

Vérifier l'état courant : `az containerapp auth show … -o json`.


## Secrets

Les credentials sont des **secrets de Container App** référencés par `secretRef`, pas des env vars
en clair, et pas Key Vault. Pour voir lesquels :

```powershell
& $az containerapp show --name msp-dashboard --resource-group GRP-RES-CONTENEURS `
   --query "properties.configuration.secrets[].name" -o json 2>$null
```

Mettre à jour puis **redémarrer** — sinon le changement ne prend pas :

```powershell
& $az containerapp secret set --name msp-dashboard --resource-group GRP-RES-CONTENEURS `
   --secrets "s1-token=$valeur" -o none
& $az containerapp revision restart --name msp-dashboard --resource-group GRP-RES-CONTENEURS `
   --revision <revision active>
```

**Avant de redémarrer**, vérifier qu'aucun cycle ne se déclenche au démarrage : `main()` ne lance
`mise_a_jour()` que si `msp_data.json` est **absent**, et `dernier_jour_maj.txt` sur `/data` évite
les double-cycles. Tant que ces deux fichiers sont là, un restart est sans effet de bord.

Quand c'est possible, préférer un dépôt de jeton **par l'application** (fichier sur `/data` qui
prime sur le secret, cf. `_s1_token`, `/api/s1/token`, `/api/tdsynnex/refresh-token`,
`/api/yooz/reconnect`) : pas d'Azure CLI, pas de redémarrage, et le jeton ne transite pas par
un autre canal.

## Lire ou écrire sur /data

Les données de prod (`msp_data.json`, `azure_conso.json`, `yooz_history.json`,
`sophos_tamper_history.json`, `tdsynnex_token.txt`…) vivent sur le share Azure Files. Pour
travailler sur du réel :

```powershell
& $az storage file download --account-name dfmmspdashboardstor --share-name msp-data `
   --path msp_data.json --dest "<scratchpad>\prod.json" --auth-mode login `
   --enable-file-backup-request-intent --only-show-errors
```

`--auth-mode login --enable-file-backup-request-intent` est nécessaire (RBAC, pas de clé de compte).
Toujours télécharger dans le scratchpad, jamais dans le repo.

## Les logs du conteneur sont illisibles depuis ce poste

`az containerapp logs show` **plante** sur le premier caractère hors cp1252 —
une flèche `→` suffit :

```
UnicodeEncodeError: 'charmap' codec can't encode character '→'
```

Le flux s'arrête là, sans rien dire : les lignes suivantes n'apparaissent pas,
et on croit que l'application n'a rien écrit. Constaté le 2026-10-02, sur
`log("mail→DIT : ...")`. `PYTHONIOENCODING=utf-8`, `chcp 65001` et
`[Console]::OutputEncoding` n'y changent rien — `az.cmd` relance son propre
Python.

Conséquence pratique : **un composant qui tourne en fond doit écrire son état
sur `/data`**, pas seulement dans les logs. C'est ce que font
`robot_mail_dit_battement.json` et `agent_mail_dit_etat.json` : un fichier se
télécharge (cf. « Lire ou écrire sur /data ») et se lit, lui.

Pour les logs eux-mêmes : KQL sur Log Analytics (`ContainerAppConsoleLogs_CL`,
runbook §8), ou écrire en ASCII dans les lignes de démarrage.

## `az containerapp exec` : ne pas insister

Passer du Python à travers PowerShell → cmd → az est un cauchemar de quoting. Pour agir sur la
prod, exposer plutôt une route dans l'application et la déclencher depuis l'interface : c'est
réutilisable, tracé, et Elliot peut le refaire seul.

## Ce qui reste bloqué côté RBAC

`az containerapp secret set` fonctionne. `az acr build` fonctionne (via `deploy.ps1`). En cas de
refus sur une autre ressource : le périmètre d'Elliot est limité aux 5 ressources du projet, et
toute demande RBAC ou app role Entra passe par **Benjamin Ozon** (bozon@dfm.fr).

## Cycle de MAJ

Automatique à `HEURE_MAJ` (20h), ~25 min. `POST /api/maj/run` (admin) le déclenche à la main, mais
il tape lourd sur Artis : ne pas le lancer en journée sans raison. Suivi : `GET /api/maj/status`.
Pour rafraîchir une seule source sans cycle complet, il existe des routes dédiées
(`/api/s1/refresh`, `refresh_m365_only`) et le bouton « ↻ Relire » du bandeau d'erreur.

Après réparation d'une source, penser à remettre `sources.<nom>.status` à `ok` : sinon le bandeau
du dashboard affiche l'erreur du dernier cycle jusqu'au soir suivant, alors que tout fonctionne.
