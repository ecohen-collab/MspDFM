# MSP Dashboard DFM — Déploiement Azure Container Apps

**Cible** : Azure Container Apps (sous PlateformeInterneDFM) avec Entra ID SSO.
**Auteur côté code** : Elliot Cohen (ecohen@dfm.fr)
**Référent infra** : Benjamin Ozon (bozon@dfm.fr) + DSI
**Dernière maj** : 2026-05-11 (refactor on-premise → Azure)

---

## 1. Architecture

```
              Utilisateur (navigateur, depuis n'importe où)
                                │
                                │ HTTPS + SSO Entra ID
                                ▼
              ┌──────────────────────────────┐
              │ Azure Container App          │
              │ msp-dashboard                │
              │                              │
              │ ─ Easy Auth (Entra ID)       │
              │   intercepte la requête,     │
              │   injecte X-MS-CLIENT-*      │
              │ ─ updater.py (Python 3.12)   │
              │ ─ frontend HTML/JS statique  │
              └────────┬─────────────────────┘
                       │ outbound vers vendeurs
                       ▼
        Artis, Sophos, VEEAM*, VADE, SentinelOne, DATTO, TDSynnex
        * VEEAM via peering DSI

   Volumes :
     /data       ─►  Azure Files (msp_data.json, *_history.json,
                                  snapshots/, tdsynnex_token.txt,
                                  cas_particuliers.json, …)
     env vars    ─►  Key Vault references (managed identity)
```

**Plus de Caddy / Authelia / VM `ialiot` / ZeroTier.** Tout ça est remplacé par Container Apps Easy Auth + Entra ID.

---

## 2. Rôles Entra ID

L'app registration "MSP Dashboard" expose 2 app roles :

| Role value | Pour qui |
|---|---|
| `msp_dashboard_user` | Tous les utilisateurs autorisés (~6 personnes) |
| `msp_dashboard_admin` | Elliot + DSI — donne accès aux actions sensibles (clôture DIT, suppression Sophos endpoints, refacturation) |

Convention : un user `admin` a aussi `user` dans ses claims. Le code teste uniquement la présence de `msp_dashboard_admin` pour le gating des actions sensibles.

---

## 3. Variables d'environnement attendues

### 3.1 Configuration

| Variable | Défaut | Rôle |
|---|---|---|
| `PORT` | `8080` | Port HTTP du serveur Python |
| `HEURE_MAJ` | `20` | Heure du cycle de MAJ planifié (0-23, 99 = jamais) |
| `DATA_DIR` | `.` en local, **`/data` en prod** | Répertoire des fichiers persistés |
| `DEV_MODE` | `0` | `1` = bypass Easy Auth en local, ne JAMAIS activer en prod |
| `TZ` | `Europe/Paris` | Fuseau horaire (déjà dans le Dockerfile) |

### 3.2 Secrets vendeurs (à injecter via Key Vault references)

| Variable | Source |
|---|---|
| `ARTIS_AUTH_URL`, `ARTIS_BASE_URL` | URLs Artis (publiques, peuvent être env vars normales) |
| `ARTIS_USERNAME`, `ARTIS_PASSWORD` | Compte service Artis |
| `VEEAM_BASE_URL`, `VEEAM_TOKEN` | VSPC |
| `SOPHOS_CLIENT_ID`, `SOPHOS_CLIENT_SECRET` | OAuth client_credentials |
| `VADE_CLIENT_ID`, `VADE_CLIENT_SECRET` | OAuth client_credentials |
| `S1_URL`, `S1_TOKEN` | SentinelOne (JWT, à renouveler) |
| `DATTO_URL`, `DATTO_API_KEY`, `DATTO_API_SECRET` | DATTO RMM |

**Format Key Vault reference** (DSI configure dans la définition Container App) :
```
@Microsoft.KeyVault(SecretUri=https://kv-msp-dashboard.vault.azure.net/secrets/ARTIS-PASSWORD/)
```

Le code Python utilise simplement `os.getenv("ARTIS_PASSWORD")` — aucun couplage Azure dans le code.

### 3.3 Fichier `tdsynnex_token.txt`

Refresh token rotatif TDSynnex. **Persisté sur Azure Files** (`/data/tdsynnex_token.txt`), pas dans Key Vault. Raison : le token est mis à jour par l'app à chaque cycle (rotation), ce qui ne marche pas avec un Key Vault (immuable côté app).

Premier déploiement : copier le `tdsynnex_token.txt` actuel sur le share Azure Files (cf. §8 migration).

---

## 4. Fichiers persistés sur `/data` (Azure Files)

Tous les fichiers qui changent à runtime vivent sous `/data` :

| Fichier | Rôle | Régénérable ? |
|---|---|---|
| `msp_data.json` | Dataset principal cross-référencé (~17 Mo) | Oui (1 cycle de ~25 min) |
| `refac_data.json` | Dataset refacturation | Oui (généré par refac.py) |
| `artis_data.json` | Dataset Artis brut | Oui |
| `tdsynnex_token.txt` | Refresh token TDSynnex | **Non** (rotaté, à backuper) |
| `cloture_history.json` | Journal clôtures DIT (audit) | **Non** |
| `nonfact_history.json` | Journal passage NonFacturable | **Non** |
| `sophos_tamper_history.json` | Journal actions Sophos tamper | **Non** |
| `sophos_delete_history.json` | Journal suppressions endpoints | **Non** |
| `cas_particuliers.json` | Notes manuelles par client | **Non** |
| `snapshots/dashboard/*.json` | Snapshots quotidiens msp_data | Optionnel |
| `snapshots/refac/*.json` | Snapshots quotidiens refac_data | Optionnel |
| `artis_update.log` | Logs applicatifs | Non bloquant (stdout aussi vers Log Analytics) |

**Politique de backup recommandée pour Azure Files** :
- Quotidienne, rétention 30 jours pour les datasets et snapshots
- Long terme pour les `*_history.json`, `cas_particuliers.json` et `tdsynnex_token.txt`

---

## 5. Modifications appliquées dans le code (vs version on-premise)

| Bloc | Avant | Maintenant |
|---|---|---|
| Auth | Authelia 2FA TOTP via Caddy `forward_auth`, header `X-User` | Easy Auth Entra ID, headers `X-MS-CLIENT-PRINCIPAL*`, rôles `msp_dashboard_admin/user` |
| Reverse proxy | Caddy local (`/etc/caddy/Caddyfile`) | Ingress natif Container Apps |
| Bind | `("", 8080)` (toutes interfaces) | `("0.0.0.0", 8080)` explicite |
| Persistance | Tout à la racine du projet (DOSSIER) | `DATA_DIR` env var, défaut DOSSIER en dev / `/data` en prod |
| Logs | `artis_update.log` uniquement | `logging` 2 handlers : stdout (Log Analytics) + fichier (`DATA_DIR/artis_update.log`) |
| Secrets | `.env` chmod 600 sur la VM | Key Vault references hydratées en env vars (Option A, zéro changement de code) |
| Dockerfile | Code monté en volume `:/app` | `COPY . /app` (image autonome) |
| Compose | 2 services (dashboard + authelia) + `network_mode: host` | 1 service, port mapping standard |

---

## 6. Test en local pendant le développement

Easy Auth ne peut pas tourner localement. Pour tester la logique de gating :

```bash
cd "Dashboard IA"
# Build + run (utilise docker-compose.override.yml qui injecte DEV_MODE=1)
docker compose up -d --build

# Vérif principal fake admin
curl -s http://localhost:8080/api/whoami
# → {"user":"dev-admin@local","upn":"dev-admin@local","name":"Dev Admin","oid":"",
#    "roles":["msp_dashboard_admin","msp_dashboard_user"],"role":"admin","is_admin":true,"dev_mode":true}

# Dump des headers Easy Auth (debug)
curl -s http://localhost:8080/api/_debug/whoami

# Test simulation d'un user non-admin (sans DEV_MODE)
docker compose run --rm -e DEV_MODE=0 dashboard \
  python -c "import updater; print('updater chargé OK, DEV_MODE off')"
```

Pour simuler un vrai header Easy Auth en local :
```python
import base64, json
payload = {
  "auth_typ": "aad",
  "claims": [
    {"typ": "name", "val": "Test Admin"},
    {"typ": "preferred_username", "val": "test-admin@dfm.fr"},
    {"typ": "roles", "val": "msp_dashboard_admin"},
    {"typ": "roles", "val": "msp_dashboard_user"},
  ],
}
header = base64.b64encode(json.dumps(payload).encode()).decode()
# puis : curl -H "X-MS-CLIENT-PRINCIPAL: $header" http://localhost:8080/api/whoami
```

---

## 7. Déploiement Azure

### 7.1 Pré-requis (DSI a déjà fait)

- Container App `msp-dashboard` provisionnée dans RG `GRP-RES-CONTENEURS`
- ACR `dfmplateformeprocess` avec namespace `msp-dashboard*`
- Key Vault `kv-msp-dashboard` peuplé avec les secrets vendeurs
- Azure Files share monté sur `/data`
- App registration Entra + 2 app roles + assignation des users
- RBAC Elliot : Contributor sur le Container App, AcrPush sur le repo, Secrets User sur le KV
- DNS interne pour le FQDN (`msp-dashboard.dfm.fr` ou autre)
- Cert TLS (managé par Container Apps)

### 7.2 Build & deploy (mes commandes)

```powershell
# Tag basé sur la date/heure
$tag = (Get-Date -Format "yyyyMMddHHmm")

# Build dans ACR (utilise le Dockerfile à la racine)
az acr build --registry dfmplateformeprocess --image "msp-dashboard:$tag" .

# Update l'image active du Container App
az containerapp update `
  --name msp-dashboard `
  --resource-group GRP-RES-CONTENEURS `
  --image "dfmplateformeprocess.azurecr.io/msp-dashboard:$tag"
```

Le rollout est progressif (zero-downtime). Vérifier les logs après deploy :
```bash
az containerapp logs show --name msp-dashboard --resource-group GRP-RES-CONTENEURS --follow
```

### 7.3 Healthcheck

Container Apps utilise `/index.html` (200 OK = healthy). Easy Auth bypasse les probes internes.

---

## 8. Migration depuis la VM `ialiot`

Quand prêt à basculer :

1. **Stopper l'ancienne** sur ialiot pour éviter la double rotation du refresh token TDSynnex :
   ```bash
   ssh elliot@ialiot "cd 'Dashboard IA' && docker compose down"
   ```

2. **Copier les fichiers data persistants vers Azure Files** (via `azcopy` ou portail) :
   ```
   msp_data.json
   refac_data.json
   artis_data.json
   tdsynnex_token.txt        ← critique
   cloture_history.json
   nonfact_history.json
   sophos_tamper_history.json
   sophos_delete_history.json
   cas_particuliers.json
   snapshots/                ← arborescence complète
   mapping_ssc.json          ← config manuelle (déjà dans l'image mais à synchroniser si édité depuis)
   ```

3. **Démarrer le Container App**, vérifier :
   - `/api/whoami` retourne le bon UPN + rôles
   - Le badge user en haut du dashboard affiche le nom Entra
   - Un cycle de MAJ démarre (vérif des logs)
   - Au moins une action sensible (clôture DIT en `?dry=1`) fonctionne pour un admin

4. **Validation sur quelques jours** puis désactiver `ialiot` définitivement.

---

## 9. Endpoints API exposés

Tous les endpoints sont derrière Easy Auth (Entra ID obligatoire).

| Méthode | Route | Rôle requis | Description |
|---|---|---|---|
| GET | `/api/whoami` | user | Identité + rôles du caller |
| GET | `/api/_debug/whoami` | user | Dump des headers Easy Auth (debug) |
| GET | `/api/snapshots/<kind>` | user | Liste snapshots (`kind` = dashboard / refac) |
| POST | `/api/snapshot/<kind>` | user | Crée un snapshot du fichier courant |
| DELETE | `/api/snapshot/<kind>/<id>` | user | Supprime un snapshot |
| GET | `/api/cas-particulier` | user | Map complète {client_code: [notes]} |
| GET | `/api/cas-particulier/<code>` | user | Notes d'un client |
| POST | `/api/cas-particulier/<code>` | user | Ajoute une note (`{texte}`, auteur = UPN) |
| DELETE | `/api/cas-particulier/<code>/<idx>` | user | Supprime une note |
| PATCH | `/api/it/<numero>/cloturer` | **admin** | Saisir CRIT IT (Artis, ?dry=1 par défaut) |
| PATCH | `/api/dit/<numero>/non-facturable` | **admin** | Passer DIT en NonFacturable (Artis) |
| PATCH | `/api/dit/<numero>/cloturer-dit` | **admin** | Clôturer DIT TravailTermine/NonTermine |
| GET | `/api/sophos/customer/<query>` | **admin** | Lookup tenant Sophos |
| GET | `/api/sophos/endpoints/lookup/<query>` | **admin** | Liste endpoints d'un tenant |
| POST | `/api/sophos/endpoints/delete` | **admin** | Suppression endpoints (avec confirm_code) |
| GET | `/<page>.html`, `/msp_data.json`, etc. | user | Frontend statique (HTML/JS dans image) + data files servis depuis DATA_DIR |

---

## 10. Hors scope (DSI gère)

- Provisionnement Azure (Container App, Storage, KV, app registration Entra)
- Accès VEEAM `backup.groupe-dfm.fr:1280` depuis Azure (peering VNet)
- DNS + certificat TLS
- Politique de backup Azure Files
- Monitoring / alerting (Log Analytics queries)
- Intégration sidebar PlateformeInterneDFM (déjà en place via app role `msp_dashboard_user`)

---

## 11. Contacts

- **Code applicatif** : Elliot Cohen — `ecohen@dfm.fr`
- **Design intégration / plateforme** : Benjamin Ozon — `bozon@dfm.fr`
- **Provisionnement Azure / RBAC** : DSI

Doc de référence côté plateforme : `docs/superpowers/specs/2026-05-11-msp-dashboard-integration-design.md` dans le repo PlateformeInterneDFM.
