# MSP Dashboard — Runbook autonomie Elliot

**Destinataire** : Elliot Cohen (ecohen@dfm.fr) et son Claude Code (ou autre agent IA assistant)
**Date** : 2026-05-11
**Émetteur** : Benjamin Ozon (bozon@dfm.fr)
**Statut** : MSP Dashboard en production sur Azure Container Apps

---

## 1. Contexte essentiel pour ton agent IA

Tu travailles sur le repo `Dashboard IA/` (anciennement déployé sur la VM `ialiot`). Depuis le 2026-05-11, l'app est **hébergée sur Azure Container Apps** dans le tenant DFM, intégrée à PlateformeInterneDFM via Entra ID SSO. Tu disposes d'un périmètre RBAC scopé qui te permet de **faire évoluer ton app en autonomie** sans dépendre du DSI pour chaque déploiement.

### 1.1 Ressources Azure de ton app

Tout est dans le resource group `GRP-RES-CONTENEURS`, subscription `Production SRV 2024` (`a619cbcf-13e3-4d19-ac1e-5a3ce6cc7886`), tenant `9e2627a2-9070-4fc1-9d57-946ac4bfe416`, région `francecentral` :

| Ressource | Nom | Rôle |
|---|---|---|
| Container App | `msp-dashboard` | Ton app, ingress public + Easy Auth Entra |
| Container Apps Environment | `plateforme-process-env` | Partagé avec la plateforme |
| Container Registry (ACR) | `dfmplateformeprocess` | Tes images Docker (`msp-dashboard:*`) |
| Storage Account | `dfmmspdashboardstor` | Persistance Azure Files |
| File Share | `msp-data` (monté sur `/data` dans ton container) | `msp_data.json`, `*_history.json`, `snapshots/`, `tdsynnex_token.txt`, etc. |
| App registration Entra | `MSP Dashboard` | Audience de l'auth Easy Auth ; définit les rôles `msp_dashboard_user` / `_admin` |

### 1.2 Tes droits Azure RBAC (scopés à ces 5 ressources uniquement)

- `Contributor` sur le Container App `msp-dashboard` (manage l'app, les secrets, les env vars, les revisions, les logs)
- `AcrPush` sur l'ACR `dfmplateformeprocess` (push d'images)
- `Storage Blob Data Contributor` + `Storage File Data SMB Share Contributor` sur le Storage `dfmmspdashboardstor` (gérer les fichiers sur Azure Files)

> **Auth ACR du Container App** : le pull d'image utilise le **managed identity** (system-assigned) du Container App, qui a `AcrPull` sur l'ACR. Pas d'admin user, pas de password. Donc quand tu fais `az containerapp update --image ...`, le Container App s'authentifie automatiquement à l'ACR via son identité. Rien à configurer côté toi.

**Ce que tu ne peux PAS faire** (et qui n'est pas dans tes responsabilités) :
- Modifier d'autres Container Apps (notamment `plateforme-process-api` qui est le backend de la plateforme)
- Modifier la DB SQL de la plateforme
- Modifier les app roles Entra ou les assignations utilisateurs (rôles côté Entra)
- Modifier l'app registration Entra (redirect URIs, etc.)
- Modifier le code de la plateforme (`PlateformeInterneDFM`)

Toute opération de ces catégories nécessite une **escalade à Benjamin Ozon** (cf. §6).

### 1.3 Architecture mentale rapide

```
User → https://msp-dashboard.gentlesand-80781e11.francecentral.azurecontainerapps.io
         │
         │ Easy Auth (Entra ID) — login + headers X-MS-CLIENT-PRINCIPAL
         ▼
   Container App `msp-dashboard`
         │
         ├─ image: dfmplateformeprocess.azurecr.io/msp-dashboard:<tag>
         ├─ env vars: PORT=8080, DATA_DIR=/data, HEURE_MAJ=20, TZ=Europe/Paris
         │            ARTIS_*, SOPHOS_*, VEEAM_*, VADE_*, S1_*, DATTO_*  (← secretref)
         ├─ secrets:  artis-password, sophos-client-secret, etc.
         └─ volume:   /data → Azure Files share `msp-data`
```

Pour toute action côté Azure, tu utilises l'`az` CLI avec les commandes décrites ci-dessous.

---

## 2. Setup initial (une seule fois)

```bash
# 1. Login Azure
az login

# 2. Vérifier que tu es bien dans la bonne subscription
az account show --query "{subscriptionId:id, name:name, tenantId:tenantId}" -o table
# Doit afficher : a619cbcf-13e3-4d19-ac1e-5a3ce6cc7886 / Production SRV 2024

# Si plusieurs subs et que ce n'est pas la bonne :
# az account list -o table
# az account set --subscription a619cbcf-13e3-4d19-ac1e-5a3ce6cc7886

# 3. Variables d'environnement pratiques à exporter (zsh / bash)
export RG="GRP-RES-CONTENEURS"
export APP="msp-dashboard"
export ACR="dfmplateformeprocess"
export STORAGE="dfmmspdashboardstor"
export SHARE="msp-data"
export FQDN="msp-dashboard.gentlesand-80781e11.francecentral.azurecontainerapps.io"

# 4. Test d'accès — tu dois voir les détails du Container App sans erreur
az containerapp show --name $APP --resource-group $RG --query "{name:name, status:properties.runningStatus, fqdn:properties.configuration.ingress.fqdn}" -o table
```

Si la dernière commande te donne un 403 / Forbidden, contacte Benjamin — ton RBAC n'est pas en place.

---

## 3. Workflow standard : déployer une nouvelle version de l'app

C'est le workflow que tu utiliseras le plus souvent. Tu modifies du code dans ton repo, tu rebuilds, tu deploies.

```bash
# Depuis la racine de ton repo Dashboard IA (là où il y a le Dockerfile)
cd "Dashboard IA/"

# 1. Build et push dans ACR (~2-3 min, ACR fait le docker build côté Microsoft)
TAG=$(date +%Y%m%d%H%M)
az acr build --registry $ACR --image "msp-dashboard:$TAG" .

# 2. Mise à jour de l'image active du Container App
az containerapp update \
    --name $APP \
    --resource-group $RG \
    --image "$ACR.azurecr.io/msp-dashboard:$TAG"

# 3. Suivre le déploiement de la nouvelle révision en direct
az containerapp logs show --name $APP --resource-group $RG --follow --tail 30
```

Container Apps fait un **rollout progressif** (zero-downtime) : l'ancienne révision continue à servir le trafic jusqu'à ce que la nouvelle passe `Healthy`. Si la nouvelle plante au démarrage, l'ancienne reste active.

### 3.1 Vérifier post-deploy

```bash
# Quelle révision est active ? La nouvelle doit être en RunningAtMaxScale avec 100% traffic
az containerapp revision list --name $APP --resource-group $RG \
    --query "[].{name:name, active:properties.active, state:properties.runningState, traffic:properties.trafficWeight, created:properties.createdTime}" \
    -o table

# Test fonctionnel (depuis ton browser, authentifié sur la plateforme)
# https://msp-dashboard.gentlesand-80781e11.francecentral.azurecontainerapps.io/api/whoami
# → doit renvoyer ton identité avec roles peuplé
```

### 3.2 En cas de problème au démarrage de la nouvelle révision

```bash
# Voir le statut détaillé de la dernière révision
az containerapp show --name $APP --resource-group $RG --query "{latestRev:properties.latestRevisionName, latestReady:properties.latestReadyRevisionName, status:properties.runningStatus}" -o table

# Si latestRev != latestReady, la nouvelle révision n'est pas prête (probablement en cours ou en échec)
# Voir les logs de la révision en échec
az containerapp logs show --name $APP --resource-group $RG --revision <REV_NAME> --tail 100
```

Si la nouvelle révision est en échec, l'ancienne reçoit toujours le trafic — pas de panique. Tu peux soit fixer ton code et re-deployer, soit forcer un rollback (§7).

---

## 4. Gestion des secrets

Les **secrets vendeurs** (Artis, Sophos, VEEAM, VADE, SentinelOne, DATTO, TDSynnex) sont stockés en **Container App secrets** (pas de Key Vault — c'était une décision faite avec le DSI à cause d'une contrainte ABAC tenant). Ils sont chiffrés au repos, et mappés à des variables d'environnement consommées par `updater.py` via `os.getenv()`.

### 4.1 Lister les secrets (sans afficher les valeurs)

```bash
az containerapp secret list --name $APP --resource-group $RG -o table
```

### 4.2 Modifier ou ajouter un secret

```bash
# Update un secret existant (ex. rotation du token SentinelOne)
az containerapp secret set --name $APP --resource-group $RG \
    --secrets "s1-token=NEW_JWT_TOKEN_HERE"

# Ajouter un nouveau secret + le mapper sur une env var (en 2 commandes)
az containerapp secret set --name $APP --resource-group $RG \
    --secrets "nouveau-secret=valeur"
az containerapp update --name $APP --resource-group $RG \
    --set-env-vars "NOUVEAU_SECRET=secretref:nouveau-secret"
```

> Important : **modifier un secret ne redémarre PAS automatiquement le container**. Les containers utilisent la valeur capturée au démarrage. Pour qu'une nouvelle valeur de secret soit prise en compte, il faut **forcer une nouvelle révision** (§5).

### 4.3 Mapping nom de secret ↔ nom de variable d'environnement

Convention en place :
- Secret name : lowercase-with-dashes (ex. `artis-password`, `s1-token`)
- Env var name : UPPERCASE_WITH_UNDERSCORES (ex. `ARTIS_PASSWORD`, `S1_TOKEN`)
- Lien : `env: ARTIS_PASSWORD=secretref:artis-password`

Si tu ajoutes un nouveau secret, respecte cette convention.

---

## 5. Forcer une nouvelle révision (sans changer l'image)

Utile quand tu as :
- Modifié un secret et tu veux que le nouveau soit utilisé
- Modifié une env var et tu veux le redémarrage
- Un container qui semble bloqué et tu veux un fresh restart

```bash
# Pousse un suffix de révision arbitraire pour forcer une nouvelle révision
az containerapp update --name $APP --resource-group $RG --revision-suffix "manual$(date +%H%M)"
```

---

## 6. Modifier des variables d'environnement

```bash
# Modifier ou ajouter une env var non-secrète (ex. changer HEURE_MAJ pour tester)
az containerapp update --name $APP --resource-group $RG \
    --set-env-vars "HEURE_MAJ=14"

# Plusieurs en une seule commande
az containerapp update --name $APP --resource-group $RG \
    --set-env-vars "HEURE_MAJ=14" "LOG_LEVEL=DEBUG"
```

Pour les env vars qui pointent vers un secret, voir §4.2.

Pour voir toutes les env vars actives :

```bash
az containerapp show --name $APP --resource-group $RG --query "properties.template.containers[0].env" -o yaml
```

---

## 7. Rollback à une révision précédente

Container Apps garde les anciennes révisions (jusqu'à `maxInactiveRevisions: 100`). Tu peux pointer le trafic vers une révision plus ancienne sans rebuild.

```bash
# 1. Lister les révisions disponibles
az containerapp revision list --name $APP --resource-group $RG \
    --query "[].{name:name, image:properties.template.containers[0].image, created:properties.createdTime, active:properties.active}" \
    -o table

# 2. Activer une révision précédente et désactiver l'actuelle
az containerapp revision activate --name $APP --resource-group $RG --revision <OLD_REV_NAME>
az containerapp revision deactivate --name $APP --resource-group $RG --revision <CURRENT_REV_NAME>
```

> Note : le `activeRevisionsMode` du Container App est `Single` (une seule révision active à la fois). Donc activer une révision suppose de désactiver l'actuelle.

---

## 8. Voir les logs (live + historique)

### 8.1 Logs en direct

```bash
# Stream des logs en temps réel (Ctrl+C pour stopper)
az containerapp logs show --name $APP --resource-group $RG --follow --tail 50

# Logs d'une révision spécifique
az containerapp logs show --name $APP --resource-group $RG --revision <REV_NAME> --follow --tail 50
```

### 8.2 Logs historiques (Azure Log Analytics)

Le Container Apps Environment envoie automatiquement les logs vers Log Analytics (workspace lié à `plateforme-process-env`). Pour query sur l'historique :

Portail Azure → ouvrir le workspace Log Analytics du RG → **Logs** (KQL) :

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == "msp-dashboard"
| where TimeGenerated > ago(24h)
| project TimeGenerated, RevisionName_s, Log_s
| order by TimeGenerated desc
| take 200
```

Filtres utiles :
- `| where Log_s contains "ERROR"` — pour ne voir que les erreurs
- `| where Log_s startswith "F"` — pour le format de log de `updater.py` (préfixé `F`)

---

## 9. Travailler avec les données sur Azure Files

Le share `msp-data` est monté sur `/data` dans ton container. Il contient `msp_data.json`, `*_history.json`, `snapshots/`, `tdsynnex_token.txt`, `mapping_ssc.json`, `prix.json`, etc.

### 9.1 Voir le contenu

```bash
# Récupérer la storage key (tu as les droits)
STORAGE_KEY=$(az storage account keys list --account-name $STORAGE --resource-group $RG --query "[0].value" -o tsv)

# Lister à la racine du share
az storage file list --share-name $SHARE --account-name $STORAGE --account-key $STORAGE_KEY -o table

# Lister un sous-dossier
az storage file list --share-name $SHARE --account-name $STORAGE --account-key $STORAGE_KEY --path "snapshots/dashboard" -o table
```

### 9.2 Télécharger un fichier (debug local)

```bash
az storage file download \
    --share-name $SHARE \
    --account-name $STORAGE \
    --account-key $STORAGE_KEY \
    --path "msp_data.json" \
    --dest "./msp_data.json"
```

### 9.3 Uploader un fichier (ex. mise à jour manuelle de `mapping_ssc.json` ou `prix.json`)

```bash
az storage file upload \
    --share-name $SHARE \
    --account-name $STORAGE \
    --account-key $STORAGE_KEY \
    --source "./mapping_ssc.json" \
    --path "mapping_ssc.json"
```

### 9.4 Cas spécial : `tdsynnex_token.txt`

Ce fichier est **rotaté à chaque cycle de MAJ par l'app**. Si jamais l'auth TDSynnex casse (token corrompu, ou l'app a été stoppée pendant qu'elle réécrivait le fichier), tu peux re-uploader un token frais manuellement :

```bash
# 1. Récupérer un nouveau refresh token depuis la console TDSynnex / ION
# 2. Le sauver dans tdsynnex_token.txt local
# 3. Upload
az storage file upload --share-name $SHARE --account-name $STORAGE --account-key $STORAGE_KEY \
    --source "./tdsynnex_token.txt" --path "tdsynnex_token.txt"

# 4. Forcer une nouvelle révision pour redémarrer le cycle de MAJ avec le nouveau token (§5)
az containerapp update --name $APP --resource-group $RG --revision-suffix "newtdtoken$(date +%H%M)"
```

---

## 10. Debug avancé

### 10.1 Exec dans le container (shell interactif)

```bash
az containerapp exec --name $APP --resource-group $RG --command /bin/bash

# Dans le shell, tu peux :
#   - ls /data
#   - cat /data/artis_update.log
#   - python -c "import os; print(os.environ.get('ARTIS_USERNAME'))"  # vérifier qu'une env var est bien injectée
#   - curl http://localhost:8080/api/whoami  (sans Easy Auth ici puisque local)
```

### 10.2 Voir le YAML complet de l'app (config courante)

```bash
az containerapp show --name $APP --resource-group $RG -o yaml > current.yaml
```

Utile pour comprendre l'état complet (secrets list, env vars, volumeMounts, healthcheck, etc.) ou pour faire des updates complexes via `az containerapp update --yaml current.yaml`.

### 10.3 Status global rapide

```bash
az containerapp show --name $APP --resource-group $RG --query "{
    name:name,
    status:properties.runningStatus,
    latestRev:properties.latestRevisionName,
    latestReady:properties.latestReadyRevisionName,
    image:properties.template.containers[0].image,
    targetPort:properties.configuration.ingress.targetPort,
    fqdn:properties.configuration.ingress.fqdn
}" -o yaml
```

---

## 11. Limites et escalade

### 11.1 Ce que tu peux faire en autonomie

- ✅ Modifier ton code (`updater.py`, `*.html`, `requirements.txt`, `Dockerfile`, etc.)
- ✅ Builder et deployer une nouvelle image
- ✅ Gérer les Container App secrets (ajout, modif, rotation)
- ✅ Gérer les env vars (sauf si tu veux ajouter une nouvelle référence de secret KV → pas applicable ici, on n'utilise pas KV)
- ✅ Forcer des révisions, rollback
- ✅ Lire/écrire sur Azure Files
- ✅ Voir les logs et debug

### 11.2 Ce qui nécessite une escalade à **Benjamin Ozon (bozon@dfm.fr)**

- ❌ Ajouter un nouvel app role Entra (ex. `msp_dashboard_viewer`) — touche aux 2 app registrations
- ❌ Assigner un nouvel utilisateur à un app role (Enterprise apps → Users and groups)
- ❌ Changer la redirect URI Easy Auth (ex. nouveau FQDN custom)
- ❌ Ajouter / modifier l'entrée de menu côté sidebar plateforme
- ❌ Changer le binding du custom domain `msp-dashboard.dfm.fr`
- ❌ Modifier le code de la plateforme PlateformeInterneDFM
- ❌ Toute opération sur d'autres Container Apps ou la DB SQL plateforme

### 11.3 Ce qui nécessite la DSI (Benjamin transmet)

- Provisionnement de nouvelles ressources Azure
- Modification de RBAC scoped (élargir tes droits)
- Peering réseau (ex. accès LAN à VEEAM)
- Politique de backup, supervision, alerting

---

## 12. Conventions de travail avec ton agent IA

### 12.1 Avant chaque déploiement

- **Test local** : `docker compose up` avec `DEV_MODE=1` permet de tester sans Easy Auth, avec des fake headers (cf. ton `docker-compose.override.yml`).
- **Quick smoke test** post-deploy : `curl https://$FQDN/api/whoami` depuis un browser authentifié, vérifier que `dev_mode: false` (sinon tu as accidentellement deployé un `DEV_MODE=1`, ne JAMAIS faire ça en prod).

### 12.2 Conventions de commit / tag d'image

- Tag image : timestamp `YYYYMMDDHHMM` (déjà la convention)
- Tu peux additionnellement push un tag `latest` après validation : `az acr import` ou re-tag

### 12.3 Quand quelque chose ne va pas

1. Regarder les logs (§8)
2. Regarder le status des revisions (§3.1)
3. Tenter un rollback (§7)
4. Si pas de solution évidente en 15 min → escalader à Benjamin avec :
   - URL de l'erreur (capture)
   - Logs récents (`az containerapp logs show ... --tail 100`)
   - Liste des dernières modifs ce que tu as fait

---

## 13. Références

- **Spec d'intégration plateforme** : `docs/superpowers/specs/2026-05-11-msp-dashboard-integration-design.md` (dans le repo PlateformeInterneDFM)
- **Runbook DSI initial** : `docs/superpowers/plans/2026-05-11-msp-dashboard-integration.md`
- **Handoff initial qui t'a été envoyé pour la migration** : `docs/msp-dashboard-elliot-handoff.md`
- **Tes propres docs côté repo Dashboard IA** : `DEPLOYMENT_AZURE.md`, `RUNBOOK_DSI.md`

---

## 14. Contacts

- **Benjamin Ozon** — `bozon@dfm.fr` — Intégration plateforme, escalades, RBAC, gouvernance
- **DSI** — infrastructure Azure (via Benjamin)

Bon dev ! En cas de doute, demande à ton agent IA de consulter ce document, et si quelque chose n'est pas couvert ici, ne te lance pas dans des opérations Azure que tu ne maîtrises pas — escalade.
