# MSP Dashboard DFM — Contexte pour Claude Code

## C'est quoi

App Python (`http.server` stdlib, pas de framework) + frontend HTML/JS vanilla qui croise les inventaires de tous nos vendeurs (Sophos, VEEAM, DATTO, SentinelOne, VADE, M365 via TDSynnex) avec la facturation Artis (ERP DFM). Sert à voir les **deltas** entre quantités facturées et quantités déployées par client.

Owner produit : **Elliot Cohen** (ecohen@dfm.fr).

## Où ça tourne (production)

- **Azure Container Apps** dans le tenant DFM, sous PlateformeInterneDFM
- **URL** : `https://msp-dashboard.gentlesand-80781e11.francecentral.azurecontainerapps.io` (ou `https://msp-dashboard.dfm.fr` si custom domain en place — vérifier avec `az containerapp show ... --query properties.configuration.ingress.fqdn`)
- **Auth** : Easy Auth Entra ID (headers `X-MS-CLIENT-PRINCIPAL*`), 2 rôles : `msp_dashboard_user` / `msp_dashboard_admin`
- **Ressources Azure** :

| Type | Nom |
|---|---|
| Resource group | `GRP-RES-CONTENEURS` |
| Container App | `msp-dashboard` |
| Container Apps Environment | `plateforme-process-env` (partagé) |
| ACR | `dfmplateformeprocess` |
| Storage Account | `dfmmspdashboardstor` |
| File Share | `msp-data` → monté sur `/data` |
| Subscription | `a619cbcf-13e3-4d19-ac1e-5a3ce6cc7886` (Production SRV 2024) |
| Tenant | `9e2627a2-9070-4fc1-9d57-946ac4bfe416` |
| Région | `francecentral` |

RBAC d'Elliot scopé sur ces 5 ressources uniquement (Contributor / AcrPush / Storage data contributor).

## Pour les opérations Azure (deploy, secrets, rollback, debug)

**Consulter `msp-dashboard-elliot-runbook.md` à la racine** — c'est exhaustif, écrit par Benjamin Ozon (DSI). Couvre :
- §2 Setup initial (`az login`, variables d'env à exporter)
- §3 Workflow standard de déploiement (`az acr build` + `az containerapp update`)
- §4 Gestion des secrets (Container App secrets, **pas Key Vault**)
- §5 Forcer une nouvelle révision sans changer l'image
- §6 Modifier les env vars
- §7 Rollback
- §8 Logs (CLI + KQL Log Analytics)
- §9 Lecture/écriture Azure Files
- §10 Debug (exec dans le container, YAML config courante)
- §11 Limites & escalades à Benjamin

**Avant d'agir sur Azure** : vérifier qu'`az` est installé et que la session est active (`az account show`). Si pas connecté → `az login --use-device-code`.

## Architecture applicative (pour comprendre le code)

Pour la conception, le métier (formules billing Artis, cross-référence par code Artis extrait des noms vendeurs, gestion du chef de projet via UO INFO-PROJ, etc.) :
- **`DEPLOYMENT_AZURE.md`** — architecture cible Azure, env vars attendues, endpoints API
- **`HANDOVER.md`** — handover initial on-premise, formules billing Artis détaillées
- **`RUNBOOK_DSI.md`** — historique du runbook envoyé à la DSI pour la migration (référence)

## Conventions de travail

- **Tag image** : `YYYYMMDDHHMM` (timestamp). Ex. `dfmplateformeprocess.azurecr.io/msp-dashboard:202605120930`
- **DEV_MODE=1** : **uniquement** en local (compose). Ne JAMAIS deployer avec DEV_MODE=1 en prod — c'est un bypass d'auth qui rend tout le monde admin.
- **Test local** : `docker compose up -d` charge auto le `docker-compose.override.yml` qui injecte DEV_MODE=1 + volume mount code + `./data:/data`.
- **Smoke test post-deploy** : `curl https://$FQDN/api/whoami` doit retourner `dev_mode: false`. Si `true` → rollback immédiat.
- **Cycle de MAJ** : 1× par jour à `HEURE_MAJ` (20h00 par défaut). Durée actuelle ~25 min (fetch détaillé de 4497 lignes RFC Artis + chef projet via UO).
- **Hot-reload des notes "cas particuliers"** : le frontend fait `fetch /api/cas-particulier` à chaque chargement de page, pas besoin d'attendre un cycle MAJ pour voir une nouvelle note.

## Ce qu'il NE faut PAS faire sans demander

- Modifier des ressources Azure **hors** de la liste ci-dessus (autres Container Apps, DB SQL plateforme, app registration Entra…)
- Toucher au RBAC ou aux app roles → escalade Benjamin
- Activer un cycle de MAJ en plein milieu de journée sans raison (ça tape lourd sur Artis ~25 min)
- Faire un POST/PATCH écrivant sur Artis ou Sophos sans accord explicite d'Elliot (cf. mémoire `feedback_artis_pas_decriture_sans_accord` et `feedback_sophos_pas_decriture_sans_accord`)
- Compter les firewalls Sophos comme du Sophos (cf. mémoire `feedback_sophos_firewall_exclu` — Sophos = antivirus uniquement)

## Contacts

- **Elliot Cohen** — `ecohen@dfm.fr` — owner code
- **Benjamin Ozon** — `bozon@dfm.fr` — intégration plateforme, escalades Azure / RBAC
