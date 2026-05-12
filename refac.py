"""
Génère refac_data.json à partir de msp_data.json (peuplé par updater.py)
+ prix.json (prix unitaires) + mapping_ssc.json (overrides manuels SSC).

Calcule les montants à refacturer par DFM aux SSC pour chaque service mutualisé.

Étape 2 : DATTO + SentinelOne uniquement.
"""

import json, os
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DOSSIER       = Path(__file__).parent
DATA_DIR      = Path(os.environ.get("DATA_DIR") or DOSSIER)
MSP_DATA      = DATA_DIR / "msp_data.json"
PRIX_FILE     = DOSSIER / "prix.json"            # config statique (in-image)
MAPPING_FILE  = DOSSIER / "mapping_ssc.json"     # config statique (in-image)
REFAC_DATA    = DATA_DIR / "refac_data.json"     # généré, persisté en runtime


def _empty_entite():
    return {
        "datto": 0.0, "m365": 0.0, "sophos": 0.0,
        "prosave": 0.0, "prosave365": 0.0, "vade": 0.0, "sentinelone": 0.0,
        "detail_datto": [], "detail_m365": [], "detail_sophos": [],
        "detail_prosave": [], "detail_prosave365": [], "detail_vade": [],
        "detail_sentinelone": [],
        "clients": set(),
    }


def _resolve_ssc(code, overrides, full_mapping):
    """Priorité : override manuel > Artis full mapping > 'Pas de SSC'."""
    if not code:
        return "Pas de SSC"
    code = str(code).strip().lstrip("0") or "0"
    return overrides.get(code) or full_mapping.get(code) or "Pas de SSC"


def _client_label(code, name):
    return f'{code} - {name}' if code else (name or '?')


def generer_refac():
    if not MSP_DATA.exists():
        print(f"[refac] {MSP_DATA} introuvable, lance d'abord updater.py")
        return None

    msp     = json.loads(MSP_DATA.read_text(encoding="utf-8"))
    prix    = json.loads(PRIX_FILE.read_text(encoding="utf-8"))
    overrides   = json.loads(MAPPING_FILE.read_text(encoding="utf-8")).get("overrides", {}) if MAPPING_FILE.exists() else {}
    raw         = msp.get("raw_sources", {})
    full_map    = msp.get("artis_full_mapping", {})
    if not raw:
        print("[refac] msp_data.json ne contient pas raw_sources — relance updater.py (MAJ complète)")
        return None

    par_ssc = defaultdict(_empty_entite)
    p_datto    = float(prix.get("datto_prix_par_appareil", 0))
    p_s1       = float(prix.get("sentinelone_prix_par_licence", 0))
    m365_skus  = prix.get("m365_prix_par_sku", {}) or {}
    sophos_skus= prix.get("sophos_prix_par_sku", {}) or {}
    p_m365_def = float(m365_skus.get("DEFAULT", 0))
    p_sophos_def = float(sophos_skus.get("DEFAULT", 0))

    def _price_sku(skus_dict, default, sku_name):
        if sku_name and sku_name in skus_dict:
            try: return float(skus_dict[sku_name])
            except: pass
        return default

    # ── DATTO ─────────────────────────────────────────────────────────────────
    for site in raw.get("datto", []):
        code = site.get("artis_code", "")
        ssc  = _resolve_ssc(code, overrides, full_map)
        qty  = int(site.get("total", 0) or 0)
        if qty <= 0:
            continue
        montant = qty * p_datto
        par_ssc[ssc]["datto"] += montant
        par_ssc[ssc]["detail_datto"].append({
            "client": _client_label(code, site.get("name", "")),
            "qty": qty,
            "prix": round(montant, 2),
        })
        if code:
            par_ssc[ssc]["clients"].add(code)

    # ── SentinelOne ───────────────────────────────────────────────────────────
    # SentinelOne sites peuvent être multiples par client (déjà groupés par artis_code en updater)
    s1_by_code = defaultdict(int)
    s1_names   = {}
    for site in raw.get("sentinelone", []):
        code = site.get("artis_code", "")
        s1_by_code[code] += int(site.get("activeLicenses", 0) or 0)
        s1_names[code]    = site.get("name", "") or s1_names.get(code, "")

    for code, qty in s1_by_code.items():
        if qty <= 0:
            continue
        ssc = _resolve_ssc(code, overrides, full_map)
        montant = qty * p_s1
        par_ssc[ssc]["sentinelone"] += montant
        par_ssc[ssc]["detail_sentinelone"].append({
            "client": _client_label(code, s1_names.get(code, "")),
            "qty": qty,
            "prix": round(montant, 2),
        })
        if code:
            par_ssc[ssc]["clients"].add(code)

    # ── M365 (TDSynnex) ───────────────────────────────────────────────────────
    # Détail par subscription (1 ligne par sub).
    # Prix mensuel = seller_cost / nb_mois_terme (= ce que DFM paie à TDSynnex / mois)
    # seller_cost = it.cost (cumul total sur la durée du terme : P1Y → 12, P3Y → 36, P1M → 1)
    import re as _re
    def _months(term):
        m = _re.match(r"P(\d+)([YM])", (term or "").upper())
        if not m: return 12
        n = int(m.group(1)); return n * 12 if m.group(2) == "Y" else n

    for sub in raw.get("m365", []):
        code = sub.get("artis_code", "")
        sc = float(sub.get("seller_cost", 0) or 0)
        nb_mois = _months(sub.get("billing_term"))
        prix_mensuel = sc / nb_mois if nb_mois > 0 else 0
        if prix_mensuel <= 0:
            continue
        ssc = _resolve_ssc(code, overrides, full_map)
        par_ssc[ssc]["m365"] += prix_mensuel
        par_ssc[ssc]["detail_m365"].append({
            "client":         _client_label(code, sub.get("customer_name", "")),
            "subscription":   sub.get("subscription") or "",
            "qty":            int(sub.get("licenses", 0) or 0),
            "billing_cycle":  sub.get("billing_cycle") or "",
            "billing_term":   sub.get("billing_term") or "",
            "seller_cost":    round(sc, 4),
            "customer_cost":  round(float(sub.get("customer_cost", 0) or 0), 4),
            "prix":           round(prix_mensuel, 2),
        })
        if code:
            par_ssc[ssc]["clients"].add(code)

    # ── Sophos ────────────────────────────────────────────────────────────────
    # Prix mensuel = somme(billableQuantity × unitMsrp) sur toutes les lignes du tenant.
    # Ces deux champs viennent de l'API billing/usage Sophos (period = mois précédent).
    for tenant in raw.get("sophos", []):
        code    = tenant.get("artis_code", "")
        qty     = int(tenant.get("endpoints", 0) or 0)
        montant = float(tenant.get("monthly_billing", 0) or 0)
        if montant <= 0 and qty <= 0:
            continue
        ssc = _resolve_ssc(code, overrides, full_map)
        par_ssc[ssc]["sophos"] += montant
        par_ssc[ssc]["detail_sophos"].append({
            "client": _client_label(code, tenant.get("name", "")),
            "qty":    qty,
            "prix":   round(montant, 2),
            "lignes": tenant.get("billing_items", []),
        })
        if code:
            par_ssc[ssc]["clients"].add(code)

    # ── PROSAVE (VEEAM stockage + points) ─────────────────────────────────────
    # Stockage : DFM paie 17 029,73€ pour 400 TB → produit en croix par client.
    # Points   : chaque point Pulse facturé 0,54€/mois.
    # On exclut complètement l'UID de l'infra interne DFM (4ec60958-…).
    veeam_companies = raw.get("veeam", [])
    veeam_usage     = raw.get("veeam_usage", {})
    veeam_points    = raw.get("veeam_points", {})
    storage_total   = float(prix.get("prosave_storage_total_mensuel", 0))
    storage_capa    = float(prix.get("prosave_storage_capacite_tb", 0)) or 1
    prix_par_tb     = storage_total / storage_capa
    prix_par_point  = float(prix.get("prosave_prix_par_point", 0))
    dfm_infra_uid   = (prix.get("prosave_dfm_infra_uid") or "").lower()

    veeam_by_code = defaultdict(lambda: {"tb": 0.0, "points": 0.0, "name": ""})
    for c in veeam_companies:
        uid  = (c.get("uid") or "").lower()
        if uid == dfm_infra_uid:
            continue  # infra interne DFM → exclue complètement
        code = c.get("artis_code", "")
        tb   = float(veeam_usage.get(c.get("uid", ""), 0) or 0)
        pts  = float(veeam_points.get(c.get("uid", ""), 0) or 0)
        if tb <= 0 and pts <= 0:
            continue
        veeam_by_code[code]["tb"]     += tb
        veeam_by_code[code]["points"] += pts
        if not veeam_by_code[code]["name"]:
            veeam_by_code[code]["name"] = c.get("name", "")

    for code, agg in veeam_by_code.items():
        ssc = _resolve_ssc(code, overrides, full_map)
        montant_storage = agg["tb"] * prix_par_tb
        montant_points  = agg["points"] * prix_par_point
        montant         = montant_storage + montant_points
        par_ssc[ssc]["prosave"] += montant
        par_ssc[ssc]["detail_prosave"].append({
            "client":         _client_label(code, agg["name"]),
            "qty":            round(agg["tb"], 3),       # affichage : TB
            "points":         round(agg["points"], 0),
            "prix_storage":   round(montant_storage, 2),
            "prix_points":    round(montant_points, 2),
            "prix":           round(montant, 2),
        })
        if code:
            par_ssc[ssc]["clients"].add(code)

    # ── PROSAVE M365 (VBO 365 — count) ────────────────────────────────────────
    # Coût ventilé au prorata du nombre d'orgs VBO 365 par client
    veeam365 = raw.get("veeam365", [])
    p365_total = float(prix.get("prosave365_total_mensuel", 0))

    v365_by_code = defaultdict(lambda: {"count": 0, "name": ""})
    for o in veeam365:
        code = o.get("artis_code", "")
        v365_by_code[code]["count"] += 1
        if not v365_by_code[code]["name"]:
            v365_by_code[code]["name"] = o.get("name", "")

    total_v365 = sum(d["count"] for d in v365_by_code.values())
    if total_v365 > 0 and p365_total > 0:
        for code, agg in v365_by_code.items():
            ssc = _resolve_ssc(code, overrides, full_map)
            montant = (agg["count"] / total_v365) * p365_total
            par_ssc[ssc]["prosave365"] += montant
            par_ssc[ssc]["detail_prosave365"].append({
                "client": _client_label(code, agg["name"]),
                "qty": agg["count"],
                "prix": round(montant, 2),
            })
            if code:
                par_ssc[ssc]["clients"].add(code)

    # ── VADE ──────────────────────────────────────────────────────────────────
    # 2 forfaits distincts ventilés au prorata des userCount :
    #   - DFM Rhône-Alpes : 3 354,64€ entre les clients de l'orga DFM RHONE-ALPES
    #   - DFM (forfait standard) : 1 057,53€ entre les clients de toutes les autres orgas
    vade_dfm     = float(prix.get("vade_dfm_mensuel", 0))
    vade_dfm_ra  = float(prix.get("vade_dfm_rhone_alpes_mensuel", 0))
    RA_NAME      = "DFM RHONE-ALPES"

    clients_vade = {}  # code → {"users", "name", "ssc"}
    for c in raw.get("vade", []):
        code = c.get("artis_code", "")
        u    = int(c.get("user_count", 0) or 0)
        if u <= 0:
            continue
        ssc = _resolve_ssc(code, overrides, full_map)
        e = clients_vade.setdefault(code, {"users": 0, "name": "", "ssc": ssc})
        e["users"] += u
        if not e["name"]:
            e["name"] = c.get("brand", "") or c.get("domain", "")

    users_ra      = sum(d["users"] for d in clients_vade.values() if d["ssc"] == RA_NAME)
    users_hors_ra = sum(d["users"] for d in clients_vade.values() if d["ssc"] != RA_NAME)

    for code, d in clients_vade.items():
        ssc = d["ssc"]
        users = d["users"]
        if ssc == RA_NAME:
            montant = (users / users_ra) * vade_dfm_ra if users_ra > 0 else 0
        else:
            montant = (users / users_hors_ra) * vade_dfm if users_hors_ra > 0 else 0
        if montant <= 0:
            continue
        par_ssc[ssc]["vade"] += montant
        par_ssc[ssc]["detail_vade"].append({
            "client": _client_label(code, d["name"]),
            "qty":    users,
            "prix":   round(montant, 2),
        })
        if code:
            par_ssc[ssc]["clients"].add(code)

    # ── Conversion ────────────────────────────────────────────────────────────
    entites = []
    for ssc, data in par_ssc.items():
        total = sum(data[k] for k in ["datto","m365","sophos","prosave","prosave365","vade","sentinelone"])
        entites.append({
            "nom": ssc,
            "datto": round(data["datto"], 2),
            "m365": round(data["m365"], 2),
            "sophos": round(data["sophos"], 2),
            "prosave": round(data["prosave"], 2),
            "prosave365": round(data["prosave365"], 2),
            "vade": round(data["vade"], 2),
            "sentinelone": round(data["sentinelone"], 2),
            "total": round(total, 2),
            "nb_clients": len(data["clients"]),
            "detail_datto":       sorted(data["detail_datto"],       key=lambda x: -x["prix"]),
            "detail_m365":        data["detail_m365"],
            "detail_sophos":      data["detail_sophos"],
            "detail_prosave":     data["detail_prosave"],
            "detail_prosave365":  data["detail_prosave365"],
            "detail_vade":        data["detail_vade"],
            "detail_sentinelone": sorted(data["detail_sentinelone"], key=lambda x: -x["prix"]),
        })
    entites.sort(key=lambda e: -e["total"])

    totaux = {k: round(sum(e[k] for e in entites), 2)
              for k in ["datto","m365","sophos","prosave","prosave365","vade","sentinelone"]}
    totaux["total"] = round(sum(totaux.values()), 2)

    # Vue plate par service (1 onglet par service côté front)
    lignes_par_service = {}
    for svc in ("datto","m365","sophos","prosave","prosave365","vade","sentinelone"):
        rows = []
        for e in entites:
            for d in e.get(f"detail_{svc}", []):
                rows.append({"org": e["nom"], **d})
        rows.sort(key=lambda r: -r.get("prix", 0))
        lignes_par_service[svc] = rows

    now = datetime.now()
    payload = {
        "updated_at":         now.isoformat(),
        "updated_at_display": now.strftime("%d/%m/%Y à %Hh%M"),
        "mois":               now.strftime("%Y/%m"),
        "services_actifs":    ["datto", "sentinelone", "m365", "sophos", "prosave", "prosave365", "vade"],
        "entites":            entites,
        "totaux":             totaux,
        "lignes_par_service": lignes_par_service,
    }

    REFAC_DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[refac] Saved {REFAC_DATA.name} ({len(entites)} entités, total {totaux['total']:.2f} €)")
    return payload


if __name__ == "__main__":
    generer_refac()
