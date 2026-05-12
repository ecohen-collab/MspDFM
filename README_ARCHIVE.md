# MSP Dashboard DFM — Repo

**Cible de déploiement** : Azure Container Apps (sous PlateformeInterneDFM) avec Entra ID SSO.

## Ordre de lecture recommandé

1. **`DEPLOYMENT_AZURE.md`** — architecture, variables d'env, build/deploy, migration depuis ialiot. **Commence ici.**
2. `HANDOVER.md` — historique projet + architecture applicative + détails métier (formules billing).
3. `DOCKER_README.md` — procédure Docker locale (legacy on-premise, conservé pour référence).

## Ce qui est dans le repo

- ✅ Code Python (`updater.py`, `refac.py`) — refactor 2026-05-11 pour Container Apps (DATA_DIR, Easy Auth, logs stdout)
- ✅ Frontend HTML/JS (`*.html`)
- ✅ Configs Docker :
  - `Dockerfile` (image autonome, `COPY . /app`)
  - `docker-compose.yml` (prod-like, 1 service)
  - `docker-compose.override.yml` (dev local, DEV_MODE=1, volume mount code)
  - `.dockerignore`
- ✅ Configs métier : `mapping_ssc.json`, `prix.json`
- ✅ Documentation : `DEPLOYMENT_AZURE.md`, `HANDOVER.md`, `DOCKER_README.md`, `README_ARCHIVE.md`, `.env.example`
- ✅ Données runtime (générées) : `msp_data.json`, `refac_data.json`, `artis_data.json`, `*_history.json`, `cas_particuliers.json`, `snapshots/` — en prod, vivent sur `/data` (Azure Files)

## Ce qui N'EST PAS dans le repo (gérés ailleurs)

- ❌ **Secrets vendeurs** (`.env` locale) → **Azure Key Vault** en prod, hydratés en env vars du Container App via Key Vault references
- ❌ **`tdsynnex_token.txt`** → **Azure Files `/data`** en prod (rotaté à chaque cycle, ne peut pas vivre dans KV)
- ❌ **Authelia / Caddy** → **Easy Auth Container Apps** + Entra ID (l'auth n'est plus dans le repo)
- ❌ Le `.venv/` Python local (recréé dans le Dockerfile via `pip install`)

## Démarrage rapide (dev local)

```bash
cd "Dashboard IA"

# 1. Créer .env avec les credentials vendeurs (cf. .env.example)
cp .env.example .env
nano .env

# 2. Build + run (DEV_MODE=1 auto via override)
docker compose up -d --build

# 3. Tester
curl -s http://localhost:8080/api/whoami | jq
# → admin par défaut en local

# 4. Logs
docker compose logs -f dashboard

# 5. Inspecter les données générées
ls -la ./data/
```

## Déploiement Azure

Tout est dans `DEPLOYMENT_AZURE.md` §7. Résumé :

```powershell
$tag = (Get-Date -Format "yyyyMMddHHmm")
az acr build --registry dfmplateformeprocess --image "msp-dashboard:$tag" .
az containerapp update --name msp-dashboard --resource-group GRP-RES-CONTENEURS --image "dfmplateformeprocess.azurecr.io/msp-dashboard:$tag"
```

## Contact

Elliot Cohen — `ecohen@dfm.fr`
DSI / plateforme : Benjamin Ozon — `bozon@dfm.fr`
