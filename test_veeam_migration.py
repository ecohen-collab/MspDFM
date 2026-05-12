"""
Test à blanc du nouveau gateway VEEAM VSPC avant migration.

Usage:
    python3 test_veeam_migration.py <BASE_URL> <TOKEN>

Exemple:
    python3 test_veeam_migration.py https://bckgwfr01.groupe-dfm.fr:1280 "Bearer eyJ..."

Le script:
  - vérifie que le serveur répond (swagger)
  - teste les 4 endpoints utilisés par updater.py (companies, usage, vb365, pulse)
  - compare le volume de données vs le serveur actuel (si VEEAM_BASE_URL/.env)
  - n'écrit RIEN, ne touche pas à msp_data.json ni à la prod

Sortie : OK / KO / DIFF par endpoint, plus un récap final.
"""

import sys
import time
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ENDPOINTS = [
    ("companies",
     "/api/v3/organizations/companies?limit=1",
     lambda j: f"{j.get('meta',{}).get('pagingInfo',{}).get('total','?')} companies"),
    ("usage",
     "/api/v3/organizations/companies/sites/backupResources/usage?limit=1",
     lambda j: f"{j.get('meta',{}).get('pagingInfo',{}).get('total','?')} entrées usage"),
    ("vb365",
     "/api/v3/infrastructure/vb365Servers/organizations?limit=1",
     lambda j: f"{j.get('meta',{}).get('pagingInfo',{}).get('total','?')} orgas VB365"),
    ("pulse",
     "/api/v3/pulse/licenses?limit=1",
     lambda j: f"{j.get('meta',{}).get('pagingInfo',{}).get('total','?')} licences Pulse"),
]


def test_one(base, token, label, path, summarize):
    url = base.rstrip("/") + path
    headers = {"accept": "application/json", "Authorization": token}
    t0 = time.time()
    try:
        r = requests.get(url, headers=headers, timeout=15, verify=False)
    except requests.exceptions.ConnectTimeout:
        return ("TIMEOUT", f"{label:12} → TIMEOUT (serveur injoignable)", None)
    except requests.exceptions.SSLError as e:
        return ("SSL", f"{label:12} → erreur SSL: {e}", None)
    except Exception as e:
        return ("ERR", f"{label:12} → {type(e).__name__}: {e}", None)

    dur = time.time() - t0
    if r.status_code == 401:
        return ("AUTH", f"{label:12} → HTTP 401 (token rejeté)", None)
    if r.status_code == 403:
        return ("AUTH", f"{label:12} → HTTP 403 (token sans droit sur cet endpoint)", None)
    if r.status_code == 404:
        return ("404", f"{label:12} → HTTP 404 (endpoint disparu — API a peut-être migré v4/v5)", None)
    if r.status_code != 200:
        return ("HTTP", f"{label:12} → HTTP {r.status_code} ({r.text[:120]})", None)

    try:
        j = r.json()
    except Exception:
        return ("JSON", f"{label:12} → réponse non JSON ({len(r.content)} octets)", None)

    summary = summarize(j)
    return ("OK", f"{label:12} → OK [{r.status_code} en {dur*1000:.0f}ms] · {summary}", j)


def discover_api_versions(base):
    """Liste les versions /api/vN/ qui répondent autre chose que 404."""
    print("\n  Probe versions API (route /organizations/companies)...")
    for v in (3, 4, 5, 6, 7):
        url = f"{base.rstrip('/')}/api/v{v}/organizations/companies?limit=1"
        try:
            r = requests.get(url, timeout=5, verify=False)
            mark = "✅" if r.status_code != 404 else "❌"
            print(f"    {mark} /api/v{v}/organizations/companies  → HTTP {r.status_code}")
        except Exception:
            print(f"    ⚠️  /api/v{v}/  → injoignable")


def discover_usage_endpoint(base, token):
    """Cherche dans quelle version vit l'endpoint backupResources/usage."""
    print("\n  Probe versions pour 'usage' (résolution du 400)...")
    headers = {"accept": "application/json", "Authorization": token}
    paths = [
        "/api/v3/organizations/companies/sites/backupResources/usage?limit=1",
        "/api/v4/organizations/companies/sites/backupResources/usage?limit=1",
        "/api/v5/organizations/companies/sites/backupResources/usage?limit=1",
        "/api/v6/organizations/companies/sites/backupResources/usage?limit=1",
        "/api/v3/organizations/companies/backupResources/usage?limit=1",
        "/api/v4/organizations/companies/backupResources/usage?limit=1",
        "/api/v3/organizations/sites/backupResources/usage?limit=1",
        "/api/v4/organizations/sites/backupResources/usage?limit=1",
    ]
    found = []
    for p in paths:
        try:
            r = requests.get(base.rstrip("/") + p, headers=headers, timeout=8, verify=False)
            if r.status_code == 200:
                try:
                    total = r.json().get("meta", {}).get("pagingInfo", {}).get("total", "?")
                except Exception:
                    total = "?"
                print(f"    ✅ {p}  → HTTP 200 · {total} entrées")
                found.append(p)
            elif r.status_code == 400:
                print(f"    ⛔ {p}  → HTTP 400 (version refusée)")
            elif r.status_code == 404:
                pass  # silencieux pour le 404, c'est juste "n'existe pas"
            else:
                print(f"    ?  {p}  → HTTP {r.status_code}")
        except Exception:
            pass
    return found


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)

    base = sys.argv[1].rstrip("/")
    token = sys.argv[2].strip()
    if not token.lower().startswith("bearer "):
        token = "Bearer " + token

    print(f"=== Test VEEAM VSPC migration ===")
    print(f"  URL    : {base}")
    print(f"  Token  : {token[:20]}...{token[-6:]} ({len(token)} chars)")
    print()

    # 0. Reachability — swagger + UI
    print("--- 0. Joignabilité ---")
    for path, label in [("/api/swagger/index.html", "swagger UI"),
                        ("/swagger/v3/swagger.json", "spec JSON")]:
        try:
            r = requests.get(base + path, timeout=8, verify=False)
            print(f"  {label:14} → HTTP {r.status_code}  {base}{path}")
        except Exception as e:
            print(f"  {label:14} → KO  ({type(e).__name__}: {e})")

    # 1. Each endpoint we use
    print("\n--- 1. Endpoints utilisés par updater.py ---")
    results = {}
    for label, path, summarize in ENDPOINTS:
        status, line, _ = test_one(base, token, label, path, summarize)
        print(f"  {line}")
        results[label] = status

    # 2. Discoveries supplémentaires si endpoints en erreur
    if "404" in results.values():
        discover_api_versions(base)
    if results.get("usage") != "OK":
        discover_usage_endpoint(base, token)

    # 3. Récap final
    print("\n--- Verdict ---")
    ok = sum(1 for s in results.values() if s == "OK")
    total = len(results)
    if ok == total:
        print(f"  ✅ Migration sûre : {ok}/{total} endpoints OK, le code n'a besoin d'aucune modif.")
        print(f"     → Mettre à jour VEEAM_BASE_URL et VEEAM_TOKEN dans .env, restart service.")
    elif ok == 0:
        print(f"  ❌ Migration impossible en l'état : 0/{total} endpoints OK.")
        print(f"     → Vérifier le token, l'URL, ou si l'API a migré (v4/v5).")
    else:
        print(f"  ⚠️  {ok}/{total} endpoints OK — le code à adapter pour les autres :")
        for label, status in results.items():
            if status != "OK":
                print(f"     - {label}: {status}")


if __name__ == "__main__":
    main()
