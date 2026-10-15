---
name: reference_yooz_resolution_client
description: "Yooz — résolution End User→client Artis : code préfixe + champ 'nom' + org fallback (résiliés en 404)"
metadata:
  node_type: memory
  type: reference
  originSessionId: 2c04e5d8-22b6-41b7-920b-e42f7521aa7e
  modified: 2026-09-25T08:15:25.474Z
---

`yooz_resolve_client` (updater.py) associe l'End User d'une facture TD SYNNEX à un client Artis + agence + org Yooz. Bugs trouvés et corrigés le 2026-09-24 (ticket « factures en erreur chargeur Yooz », ~97 factures TECH DATA en échec) :

1. **L'index de noms se construisait avec `it.get("libelle")` sur la reconciliation, mais le champ est `nom`** → AUCUN client de la reconciliation n'était indexé par nom, seuls ceux avec M365 (`customer_name`) l'étaient. D'où « Aucun client Artis correspondant » pour tout client **sans M365** (dont les résiliés). Fix : `nm = it.get("nom") or it.get("libelle")`.

2. **Le code Artis est en PRÉFIXE de l'End User** (« 17725 - ECOVALIS France ») — TD SYNNEX le préfixe toujours. On l'exploite désormais EN PREMIER (regex `^\s*0*(\d{3,6})\s*[-–]`), plus fiable que le nom. Org tirée de la reconciliation (`code_org`) ou du mapping complet `artis_full_mapping` (code→SSC).

3. **Clients RÉSILIÉS** (`actif=False` en reconciliation) : `GET /client/{code}` renvoie **HTTP 404** (« Le client n'a pas été trouvé ») → pas d'agence via live Artis. Mais l'org du dernier cycle est dans la reconciliation (`org='DFM GRAND EST'`…) → fallback `ARTIS_ORG_TO_AGENCE[org]` donne l'agence (TRO…). C'est fréquent : client résilié côté Artis mais encore facturé côté TDSynnex/M365.

**Résultat** : sur 97 factures en erreur, **73 se résolvent** après correctif ; restent 19 vrais « pas dans Artis » (code absent), 4 End User illisibles dans le PDF, 1 org inconnue.

4. **L'index ne couvrait que les clients à « Contrat Services Informatique »** (`artis_get_clients` filtre ce type) → un client existant dans Artis mais avec un autre contrat (impression, téléphonie…) remontait « aucun client » (ex. SEPT CEPAGES 30772, ACE CONSEIL 1755). Fix 2026-09-24 : `artis_get_clients` renvoie aussi `all_mapping` (code→{nom,org}, TOUS contrats), stocké dans `msp_data["artis_all_mapping"]`, et le resolver l'ajoute à son index (nom + code + org→agence). **Vérifier l'existence réelle via l'API `/contrat?maxResults=9999999` (9874 clients tous contrats), pas seulement l'index local.**

5. **`/contrat` n'est PAS la base clients complète** — il ne liste que les clients AVEC contrat (~9874). La vraie base = **`GET /client` = 23465 tiers** (clients même sans contrat, ex. AXEES 19984, LA SOLUTION AVOCATS 9041, TAMRISA 6321, DUONEXT 31400 — tous existants mais sans contrat informatique). Fix 2026-09-25 : quand le code en préfixe n'est pas dans l'index local, le resolver confirme en LIVE via `GET /client/{code}` (200 = existe, agence via sa catég AGENCES). **Le code préfixe FAIT FOI, avant tout match par nom** (un contains sur le nom visait un homonyme, ex. 9041 → 29388). Alias nom→code pour les factures sans code dont le nom diffère d'Artis : `YOOZ_CLIENT_ALIASES` (ex. WIZYA→31400).

**Piège méthodo** : ne PAS conclure « client absent » sans interroger **`/client/{code}` (base tiers)**, pas seulement `/contrat`. Vérifié : sur 9 « absents » supposés, 6 existaient bien en tiers. — le CSV d'erreurs du lot reflète l'état au moment de l'import (Artis pouvait être injoignable), pas la réalité des données. [[project_yooz_factures_tdsynnex]]
