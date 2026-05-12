# Runbook déploiement MSP Dashboard — pour la DSI

**Destinataire** : Benjamin Ozon / DSI
**Date** : 2026-05-11
**Auteur côté code** : Elliot Cohen (ecohen@dfm.fr)

Tout est pré-adapté selon ton mail du 2026-05-11 (Easy Auth headers, `DATA_DIR=/data`, Dockerfile autonome, logs stdout, plus d'Authelia/Caddy). Tu reçois 3 choses :

1. **Cette archive** : code source prêt à `az acr build`
2. **Un fichier `.gpg`** (canal séparé, chiffré AES256) : secrets vendeurs à mettre en Key Vault
3. **Une seconde archive `data-*.tar.gz`** : données runtime à uploader sur Azure Files (`/data`)

---

## 1. Build & push de l'image dans ACR

```powershell
cd msp-dashboard    # racine du repo extrait
$tag = (Get-Date -Format "yyyyMMddHHmm")
az acr build --registry dfmplateformeprocess --image "msp-dashboard:$tag" .
```

Durée : ~2-3 min (image `python:3.12-slim` + pip install + COPY).

---

## 2. Populer Key Vault `kv-msp-dashboard`

Les secrets sont dans le bundle GPG joint. Déchiffrer :

```bash
gpg --decrypt secrets-msp-*.tar.gz.gpg | tar -xz
cd secrets-msp-dashboard/
cat LISEZ-MOI.txt
```

Le `dotenv-racine` contient les `KEY=VALUE` à mapper en KV. Mapping nom env var → nom KV (KV n'accepte pas `_`, remplacer par `-`) :

| Env var (lue par updater.py) | Nom secret KV |
|---|---|
| `ARTIS_AUTH_URL` | `ARTIS-AUTH-URL` |
| `ARTIS_BASE_URL` | `ARTIS-BASE-URL` |
| `ARTIS_USERNAME` | `ARTIS-USERNAME` |
| `ARTIS_PASSWORD` | `ARTIS-PASSWORD` |
| `VEEAM_BASE_URL` | `VEEAM-BASE-URL` |
| `VEEAM_TOKEN` | `VEEAM-TOKEN` |
| `SOPHOS_CLIENT_ID` | `SOPHOS-CLIENT-ID` |
| `SOPHOS_CLIENT_SECRET` | `SOPHOS-CLIENT-SECRET` |
| `VADE_CLIENT_ID` | `VADE-CLIENT-ID` |
| `VADE_CLIENT_SECRET` | `VADE-CLIENT-SECRET` |
| `S1_URL` | `S1-URL` |
| `S1_TOKEN` | `S1-TOKEN` |
| `DATTO_URL` | `DATTO-URL` |
| `DATTO_API_KEY` | `DATTO-API-KEY` |
| `DATTO_API_SECRET` | `DATTO-API-SECRET` |

Script PowerShell de population (à adapter avec ton compte) :

```powershell
$kv = "kv-msp-dashboard"
# Lire dotenv-racine et alimenter le KV
Get-Content "secrets-msp-dashboard\dotenv-racine" | ForEach-Object {
    if ($_ -match '^([A-Z_]+)=(.*)$') {
        $envName = $matches[1]
        $value   = $matches[2]
        $kvName  = $envName -replace '_','-'
        Write-Host "→ $kvName"
        az keyvault secret set --vault-name $kv --name $kvName --value $value | Out-Null
    }
}
```

---

## 3. Mapper les Key Vault references dans la définition Container App

Pour chaque secret du KV, créer une env var dans le Container App qui le référence :

```powershell
$rg = "GRP-RES-CONTENEURS"
$app = "msp-dashboard"
$kvUri = "https://kv-msp-dashboard.vault.azure.net"

$secrets = @(
    "ARTIS-AUTH-URL","ARTIS-BASE-URL","ARTIS-USERNAME","ARTIS-PASSWORD",
    "VEEAM-BASE-URL","VEEAM-TOKEN",
    "SOPHOS-CLIENT-ID","SOPHOS-CLIENT-SECRET",
    "VADE-CLIENT-ID","VADE-CLIENT-SECRET",
    "S1-URL","S1-TOKEN",
    "DATTO-URL","DATTO-API-KEY","DATTO-API-SECRET"
)

# 1. Définir les "secrets" du Container App (Key Vault refs)
$secretArgs = $secrets | ForEach-Object {
    $kvRef = "$kvUri/secrets/$_"
    "$($_.ToLower())=keyvaultref:$kvRef,identityref:system"
}
az containerapp secret set --name $app --resource-group $rg --secrets $secretArgs

# 2. Mapper chaque secret en env var du container
# Nom env var = nom original (underscore), value = secretref vers le secret du Container App
$envArgs = $secrets | ForEach-Object {
    $envName = $_ -replace '-','_'
    "$envName=secretref:$($_.ToLower())"
}
$envArgs += "PORT=8080"
$envArgs += "HEURE_MAJ=20"
$envArgs += "DATA_DIR=/data"
$envArgs += "TZ=Europe/Paris"

az containerapp update --name $app --resource-group $rg --set-env-vars $envArgs
```

**Pré-requis** : la **managed identity système** du Container App doit avoir le rôle "Key Vault Secrets User" sur `kv-msp-dashboard`.

---

## 4. Uploader les données runtime sur Azure Files

L'archive `data-YYYYMMDD-HHMMSS.tar.gz` contient ce qui doit aller sur `/data` :

```
msp_data.json
refac_data.json
artis_data.json
cas_particuliers.json
tdsynnex_token.txt              ← CRITIQUE (refresh token, ne peut PAS être en KV car rotaté)
cloture_history.json
nonfact_history.json (si présent)
sophos_tamper_history.json
sophos_delete_history.json
snapshots/                      ← arborescence dashboard/ + refac/
```

Upload via `azcopy` (à adapter avec ton SAS / login) :

```bash
tar -xzf data-*.tar.gz   # extrait dans ./data-*/
cd data-*/
azcopy copy "*" "https://<storage-account>.file.core.windows.net/<share>/?<SAS>" --recursive
```

**Permissions cibles sur Azure Files** :
- Le container du Container App tourne en UID 1000:1000 (cf. Dockerfile) → s'assurer que le mount accepte cet UID. Si le share est en lecture seule par défaut, passer en RW.

---

## 5. Déploiement de l'image

```powershell
az containerapp update `
  --name msp-dashboard `
  --resource-group GRP-RES-CONTENEURS `
  --image "dfmplateformeprocess.azurecr.io/msp-dashboard:$tag"
```

Le rollout est progressif (zero-downtime).

---

## 6. Vérification post-deploy

```powershell
# Logs en direct
az containerapp logs show --name msp-dashboard --resource-group GRP-RES-CONTENEURS --follow

# FQDN
az containerapp show --name msp-dashboard --resource-group GRP-RES-CONTENEURS `
  --query "properties.configuration.ingress.fqdn" -o tsv
```

Test depuis ton navigateur (connecté à Entra DFM) :

- `https://<fqdn>/dashboard.html` → doit afficher le dashboard avec ton nom Entra en haut à droite
- `https://<fqdn>/api/whoami` → doit retourner :
  ```json
  {
    "user": "bozon@dfm.fr",
    "upn": "bozon@dfm.fr",
    "name": "Benjamin Ozon",
    "oid": "...",
    "roles": ["msp_dashboard_admin", "msp_dashboard_user"],
    "role": "admin",
    "is_admin": true,
    "dev_mode": false
  }
  ```
- `https://<fqdn>/api/_debug/whoami` → dump complet des headers Easy Auth (utile si problème de décodage)

Si `dev_mode: true` → **danger**, ça veut dire que `DEV_MODE=1` traîne dans les env vars (à enlever immédiatement). En prod il doit être absent ou `0`.

---

## 7. Cycle de mise à jour

Premier cycle automatique au prochain `HEURE_MAJ` (20h par défaut). Pour forcer un cycle immédiat après déploiement :

```bash
# Solution 1 : restart du Container App (déclenche un cycle si pas de msp_data.json,
# sinon attend HEURE_MAJ. Comme on a uploadé msp_data.json, il attendra 20h.)

# Solution 2 : faire tomber msp_data.json à 0 bytes pour forcer un cycle initial
# (à faire AVANT le restart, via azcopy depuis ton poste)

# Solution 3 (recommandée) : laisser tourner jusqu'à 20h, vérifier le lendemain
```

---

## 8. Points qui peuvent foirer

| Symptôme | Cause probable |
|---|---|
| `api/whoami` retourne `null` user, `dev_mode: false` | Easy Auth pas activé en mode "Require authentication" → activer dans la config Container App |
| `403 Forbidden` sur les routes admin | L'user n'a pas le rôle `msp_dashboard_admin` dans son token Entra → vérifier app role assignment dans l'app registration |
| Cycle de MAJ qui plante en erreur 401 sur Artis/Sophos | Secret KV pas correctement référencé → vérifier `az containerapp show ... --query properties.template.containers[0].env` |
| `Permission denied` sur `/data/*.json` | UID du mount Azure Files != 1000 → ajuster côté Storage |
| VEEAM toujours en erreur | Peering pas en place vers `backup.groupe-dfm.fr:1280` — le code gère l'erreur proprement, colonnes VEEAM resteront vides |
| Logs vides côté Log Analytics | Vérifier que stdout est bien capturé (devrait être natif Container Apps) |

---

## 9. Contact

Elliot Cohen — `ecohen@dfm.fr` — disponible pour debug à distance pendant la migration.

Si problème bloquant : avant rollback, dump les logs (`az containerapp logs show`) et envoie-moi le retour de `/api/_debug/whoami`.
