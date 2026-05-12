# HANDOVER — MSP Dashboard DFM

> **À lire avant toute action.** Ce document récapitule l'état du projet pour reprendre la main.

---

## 1. Contexte

- **Utilisateur** : Elliot Cohen (`ecohen@dfm.fr`) — DFM, MSP français
- **But** : remplacer le fichier Excel `MCO - Console ALL.xlsm` (macros VBA) par un dashboard web
- **Principe** : pour chaque client *Contrat Services Informatique* sur Artis (ERP), comparer **quantités facturées** (Artis) vs **quantités déployées** (vendeurs) → afficher les **deltas** colorés
- **Pen test à venir** → secrets dans `.env`, jamais en dur dans le code

---

## 2. Architecture

```
[updater.py] (service Python)
    │
    ├─→ fetch APIs vendeurs en parallèle (ThreadPoolExecutor)
    │   ├─ Artis (ERP DFM, public via http://artis.dfm.fr)
    │   ├─ VEEAM Backup        — INTERNE DFM (backup.groupe-dfm.fr:1280) ⚠
    │   ├─ Sophos Central      — public (api.central.sophos.com)
    │   ├─ VADE Secure         — public
    │   ├─ SentinelOne         — public
    │   ├─ DATTO RMM           — public
    │   └─ M365 (TDSynnex)     — public (ion.tdsynnex.com)
    │
    ├─→ Cross-référence par CODE client Artis (extrait du nom vendeur)
    ├─→ Écrit msp_data.json
    └─→ Sert dashboard.html sur HTTP localhost:8080
```

---

## 3. Fichiers du projet

| Fichier | Rôle |
|---|---|
| `updater.py` | Script principal (fetch + cross-ref + serveur HTTP) |
| `dashboard.html` | Frontend (onglets par service + export Excel ExcelJS) |
| `.env` | Secrets — chmod 600 obligatoire |
| `tdsynnex_token.txt` | Refresh token TDSynnex (rotation à chaque auth) — chmod 600 |
| `mapping.json` | (optionnel) Overrides manuels client → tenant |
| `msp_data.json` | Données générées (cross-référence) |
| `artis_update.log` | Logs MAJ |
| `MCO - Console ALL.xlsm` | Fichier Excel source (mdp `/ryansv:434X273-`) |

---

## 4. Mode de fonctionnement

`mise_a_jour(with_rfc=True, rfc_source="excel"|"api")` :

- **rfc_source="excel"** : lit la feuille `Export RFC Détaillé` du `.xlsm` (~10s, 3245 lignes) — utilisé en lancement manuel
- **rfc_source="api"** : scan complet API Artis (4 niveaux contrat→msc→rfc→détail, ~10-15 min) — utilisé pour la MAJ planifiée 20h00

L'heure de MAJ vient de `HEURE_MAJ` dans `.env`.

---

## 5. Résultats matching obtenus (Windows, fonctionnels)

Matching par **code Artis extrait du nom vendeur** (regex `\b\d{3,6}\b`) au lieu de fuzzy match.

| Service | Matchés / 750 |
|---|---|
| VEEAM | 286 |
| VEEAM 365 | 206 |
| Sophos | 381 |
| SentinelOne | 142 |
| DATTO | 430 |
| VADE | 141 |
| M365 | 338 |
| Avec facturation Artis | 575 |
| Anomalies | 224 |

---

## 6. Formules billing Artis (sheet "Export RFC Détaillé")

Reproduites à l'identique des SUMIFS Excel :

- **DATTO** : SUMIFS(qty, RFC libellé LIKE `*infogérance*`)
- **M365** : SUMIFS(qty, MSC LIKE `* 365`) hors SAUVEGARDE
- **Sophos** : SUMIFS(qty, MSC LIKE `*Antivirus*`) + SUMIFS(qty, MSC LIKE `*firewall*`, RFC NOT LIKE `*SG*`)
- **VEEAM TB** : SUMIFS(TB extrait via regex `\d+To`, MSC=`SAUVEGARDE`, RFC NOT LIKE `*Microsoft 365*`)
- **VEEAM365** : COUNTIFS(MSC=`SAUVEGARDE`, RFC LIKE `*Microsoft 365*`)
- **VADE** : SUMIFS(qty, RFC LIKE `*antispam*`)
- **SOC** : SUMIFS(qty, RFC LIKE `*SOC*`)

---

## 7. Détails techniques importants (pour ne pas casser ce qui marche)

- **stdout UTF-8 forcé** au début de `updater.py` (sinon plante sur `═ › ⚠`)
- **Conversion VEEAM** : `usedStorageQuota / 1099511627776` = TB (pas /1073741824 qui donne des GB)
- **Sophos endpoint count** :
  - Champ tenant : `apiHost` (singulier), pas `apiHosts.dataRegion`
  - Endpoint billing : `GET /partner/v1/billing/usage/{YYYY/MM}` (mois courant -1)
  - Match billing items : `assignedAccountIdentifier` n'a pas le code → on cherche le tenant par nom puis on prend son `artis_code` (extrait de `showAs`)
- **VADE** : Artis code dans le champ **`brand`** (pas `cpRef`!), state numérique `2 = active`
- **TDSynnex** : refresh token rotatif, écrit dans `tdsynnex_token.txt` à chaque auth (faire chmod 600)
- **Normalisation code Artis** : `str(int(code))` pour virer les zéros non-significatifs (`"0502"` → `"502"`)

---

## 8. État du déploiement Linux

### Fait
- VM Ubuntu 24.04 LTS sur Hyper-V
- IP réseau : la VM n'est PAS sur le LAN DFM (switch Hyper-V mal configuré). Utilise **ZeroTier** pour accès depuis le poste Windows
- User `ialiot` créé à l'install (sudoer)
- Code copié dans le dossier (chemin à confirmer — l'utilisateur a copié le dossier complet "Dashboard IA")
- Plan initial : user système `msp`, code dans `/opt/msp-dashboard/`. Si pas en place, à faire.

### À faire
1. Vérifier où sont les fichiers exactement (`pwd`, `ls`)
2. Si pas déjà fait : créer user `msp`, déplacer code dans `/opt/msp-dashboard/`, ownership `msp:msp`, chmod 600 sur `.env` et `tdsynnex_token.txt`
3. Créer venv Python `.venv` avec `requests urllib3 msoffcrypto-tool openpyxl`
4. Tester un lancement manuel et **diagnostiquer les sources qui ne marchent pas** :
   - **VEEAM va échouer** (serveur interne DFM non joignable depuis ZeroTier) → laisser tel quel, l'erreur est gérée
   - Vérifier que Artis, Sophos, DATTO, S1, VADE, M365 répondent bien
5. Créer service systemd `/etc/systemd/system/msp-dashboard.service` :
   ```ini
   [Unit]
   Description=MSP Dashboard Updater
   After=network.target

   [Service]
   Type=simple
   User=msp
   WorkingDirectory=/opt/msp-dashboard
   ExecStart=/opt/msp-dashboard/.venv/bin/python /opt/msp-dashboard/updater.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
6. Installer **Caddy** comme reverse proxy avec HTTPS auto + Basic Auth :
   ```
   msp-dashboard.dfm.local {
       tls internal
       basicauth /* {
           ecohen $2a$14$<hash>
       }
       reverse_proxy localhost:8080
   }
   ```
   Hash du mot de passe : `caddy hash-password`
7. UFW : `allow 22/tcp`, `allow 443/tcp`, deny 8080 (interne uniquement)
8. Régler définitivement le souci VEEAM : soit corriger Hyper-V (switch externe vers LAN DFM), soit garder ZeroTier en attendant

---

## 9. Notes importantes

- **`webbrowser.open()`** dans `main()` ne fait rien sur serveur headless — pas grave, ça crash pas
- **Le fichier `.xlsm`** : si copié sur la VM, le mode `rfc_source="excel"` fonctionnera. Sinon, basculer en `rfc_source="api"` au lancement manuel (le seul inconvénient : ça prend 10-15 min). Pour la MAJ planifiée 20h00, c'est déjà en mode API
- **L'Excel a un mot de passe**, mais il chiffre seulement les macros VBA, pas les données. `openpyxl` peut lire directement (le code essaie d'abord `load_workbook` direct, puis fallback `msoffcrypto`)
- **Frontend `dashboard.html`** charge `msp_data.json` via `fetch()`. Caddy le servira via `reverse_proxy localhost:8080`

---

## 10. Prochaines actions suggérées

1. Lire le log actuel : `tail -50 artis_update.log`
2. Identifier précisément quelles sources fonctionnent depuis la VM
3. Configurer le service systemd
4. Mettre Caddy + auth
5. Tester l'accès depuis un autre poste sur le LAN/ZeroTier

---

**FIN DU HANDOVER**
