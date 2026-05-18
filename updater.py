"""
MSP Dashboard - Script de mise a jour
Les credentials sont dans .env - jamais dans le code source.
"""

import os, re, sys, json, time, logging, threading, webbrowser
import requests
from requests.adapters import HTTPAdapter
from urllib.parse import parse_qs, unquote
import urllib3

# Force UTF-8 on stdout (Windows defaults to cp1252 which can't encode ═ › etc.)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from datetime import datetime, date, timedelta
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOSSIER  = Path(__file__).parent                      # code source (statique, in-image)
# DATA_DIR : tous les fichiers de données qui changent à runtime (msp_data.json,
# *_history.json, snapshots/, tdsynnex_token.txt, cas_particuliers.json, logs).
# Défaut = DOSSIER (dev local). En prod Azure Container Apps : volume Azure Files monté sur /data.
DATA_DIR = Path(os.environ.get("DATA_DIR") or DOSSIER)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Chargement .env ────────────────────────────────────────────────────────────
def load_env():
    """Charge .env si présent (dev local). En prod Azure : env vars hydratées depuis Key Vault."""
    env_file = DOSSIER / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

def cfg(key, default=""):
    return os.environ.get(key, default).strip()

PORT       = int(cfg("PORT", "8080"))
HEURE_MAJ  = int(cfg("HEURE_MAJ", "20"))
DEV_MODE   = cfg("DEV_MODE", "0") == "1"
DATA_FILE  = DATA_DIR / "msp_data.json"
LOG_FILE   = DATA_DIR / "artis_update.log"

# ── Logging : stdout (Azure Log Analytics) + fichier (tail -f local) ──────────
_log_fmt = logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_logger = logging.getLogger("msp-dashboard")
_logger.setLevel(logging.INFO)
_logger.handlers.clear()
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setFormatter(_log_fmt)
_logger.addHandler(_stdout_handler)
try:
    _file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    _file_handler.setFormatter(_log_fmt)
    _logger.addHandler(_file_handler)
except Exception as _e:
    # Filesystem read-only ou indisponible : on continue avec stdout seul.
    print(f"WARN: file log indisponible ({LOG_FILE}): {_e}", flush=True)
_logger.propagate = False

def log(msg):
    _logger.info(msg)

# ── État du cycle MAJ exposé via /api/maj/status ──────────────────────────────
_maj_state = {"in_progress": False, "phase": "", "started_at": None, "ended_at": None}

def _phase(msg):
    _maj_state["phase"] = msg
    log(f"  › {msg}")

# ── Helpers ────────────────────────────────────────────────────────────────────

def normalize_code(code):
    """Normalize Artis client code: remove leading zeros, return as string."""
    try:
        return str(int(str(code).strip()))
    except Exception:
        return str(code).strip()

def extract_artis_code(name):
    """
    Extract Artis client code from a vendor entity name.
    Names embed the code like '12522 - VERKINDER COURTAGE' or '18128 - SCM...' or ' 10351 FESTIVAL...'
    Returns normalized code (no leading zeros) or None.
    """
    if not name:
        return None
    # First try: code is the very first word (DATTOdata pattern)
    m = re.match(r'^\s*(\d{3,6})\b', str(name))
    if m:
        return normalize_code(m.group(1))
    # Second try: find first standalone 3-6 digit sequence in name
    m = re.search(r'\b(\d{3,6})\b', str(name))
    return normalize_code(m.group(1)) if m else None

def extract_tb(libelle):
    """Extract TB quantity from RFC libelle, e.g. 'Basic 1To inclus' → 1."""
    m = re.search(r'(\d+)\s*To\b', str(libelle), re.IGNORECASE)
    return int(m.group(1)) if m else 0

# ═══════════════════════════════════════════════════════════════════════════════
# ARTIS
# ═══════════════════════════════════════════════════════════════════════════════

def artis_get_token():
    resp = requests.post(
        cfg("ARTIS_AUTH_URL"),
        headers={"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        data=f"userName={cfg('ARTIS_USERNAME')}&password={cfg('ARTIS_PASSWORD')}",
        timeout=30
    )
    resp.raise_for_status()
    d = resp.json()
    return d.get("token") or d.get("data", {}).get("token") or d.get("access_token")

def artis_get_dit(token, days=90, date_debut=None, date_fin=None):
    """
    Récupère les DIT (Demandes d'Intervention Technique) de la période donnée.
    Soit on passe `days` (default 90), soit on passe `date_debut` et `date_fin` (date ou str ISO).
    Retourne une liste {numero, date, etat, client_code, client_nom, nature, symptome,
                        commentaire, satisfaction, tps_resolution, interventions: [...]}
    """
    from datetime import timedelta
    base = cfg("ARTIS_BASE_URL")
    if not base or not token:
        return {"dit": [], "error": "credentials manquantes"}
    if date_fin is None:
        date_fin = date.today()
    if date_debut is None:
        date_debut = date_fin - timedelta(days=days)
    # accepter str "YYYY-MM-DD" ou date
    df = date_fin.isoformat() if hasattr(date_fin, "isoformat") else str(date_fin)
    dd = date_debut.isoformat() if hasattr(date_debut, "isoformat") else str(date_debut)
    url = (f"{base}/dit"
           f"?dateDebut={dd}"
           f"&dateFin={df}"
           f"&maxResults=20000"
           f"&token={token}")
    try:
        r = requests.get(url, headers={"accept": "application/json"}, timeout=120)
        if r.status_code != 200:
            return {"dit": [], "error": f"HTTP {r.status_code}"}
        body = r.json()
        raw_dits = body if isinstance(body, list) else body.get("data", [])

        def _str(v):
            """Récupère un libellé : si dict (CodeLib), retourne libelle ; sinon string telle quelle."""
            if isinstance(v, dict):
                return v.get("libelle") or v.get("code") or ""
            return v or ""

        def _personne_nom(p):
            if not isinstance(p, dict):
                return ""
            n = (p.get("nom") or "").strip()
            pr = (p.get("prenom") or "").strip()
            return (pr + " " + n).strip() if pr else n

        out = []
        for d in raw_dits:
            if not isinstance(d, dict):
                continue
            client = d.get("client") or {}
            tps    = d.get("tpsResolution") or {}
            tps_pec = d.get("tpsPrisEnCharge") or {}
            inters = []
            for it in d.get("inter", []) or []:
                if not isinstance(it, dict):
                    continue
                inters.append({
                    "numero":       it.get("numero", ""),
                    "date_debut":   it.get("dateDebut", ""),
                    "duree":        it.get("duree", 0),
                    "etat":         _str(it.get("etat")),
                    "commentaire":  (it.get("commentaire") or "")[:500],
                    "intervenant":  _personne_nom(it.get("intervenant")),
                    "domaine":      _str(it.get("domaine")),
                    "activite":     _str(it.get("activite")),
                    "priorite":     _str(it.get("priorite")),
                })
            out.append({
                "numero":          d.get("numero", ""),
                "date":            d.get("date", ""),
                "date_max_reponse": d.get("dateMaxiReponseContractuelle", ""),
                "client_code":     client.get("code", "") if isinstance(client, dict) else "",
                "client_nom":      client.get("libelle", "") if isinstance(client, dict) else "",
                "etat":            _str(d.get("etat")),
                "nature":          _str(d.get("nature")),
                "symptome":        _str(d.get("symptome")),
                "type_dit":        _str(d.get("typeDit")),
                "commentaire":     (d.get("commentaire") or "")[:500],
                "commentaire_cloture": (d.get("commentaireCloture") or "")[:500],
                "note_satisfaction": d.get("noteSatisfaction"),
                "commentaire_satisfaction": (d.get("commentaireSatisfaction") or "")[:300],
                "demandeur":       _personne_nom(d.get("demandeur")) or _personne_nom(d.get("enregistrePar")),
                "tps_resolution":     tps.get("tempsReel") if isinstance(tps, dict) else None,
                "tps_prise_en_charge": tps_pec.get("tempsReel") if isinstance(tps_pec, dict) else None,
                "interventions":   inters,
                "nb_interventions": len(inters),
            })
        return {"dit": out}
    except Exception as e:
        return {"dit": [], "error": str(e)}


def artis_get_non_productif(token, days=90, date_debut=None, date_fin=None):
    """
    Récupère les TNP (Temps Non Productif) sur la période donnée via /nonProductif.
    Catégories rencontrées : HT (Hotline), DEJ (Pause déjeuner), CP (Congés), CD (Course),
    AT (Audit technique), HOT-SUP-TERRAIN, HOT-SUP-REGIONS, FOR (Formation), RE (Réunion),
    STK (Gestion stocks), VGG (Véhicule), MAL (Maladie), VIM (Visite médicale)…
    Retourne {"np": [...]} avec entrées : intervenant, categorie_code, categorie_libelle,
    date_debut, date_fin, duree_h (calculée), libelle.
    """
    from datetime import timedelta, datetime
    base = cfg("ARTIS_BASE_URL")
    if not base or not token:
        return {"np": [], "error": "credentials manquantes"}
    if date_fin is None:
        date_fin = date.today()
    if date_debut is None:
        date_debut = date_fin - timedelta(days=days)
    df = date_fin.isoformat() if hasattr(date_fin, "isoformat") else str(date_fin)
    dd = date_debut.isoformat() if hasattr(date_debut, "isoformat") else str(date_debut)
    url = f"{base}/nonProductif?dateDebut={dd}&dateFin={df}&token={token}"
    try:
        r = requests.get(url, headers={"accept": "application/json"}, timeout=120)
        if r.status_code != 200:
            return {"np": [], "error": f"HTTP {r.status_code}"}
        body = r.json()
        raw = body if isinstance(body, list) else body.get("data", [])

        def _personne_nom(p):
            if not isinstance(p, dict):
                return ""
            n = (p.get("nom") or "").strip()
            pr = (p.get("prenom") or "").strip()
            return (pr + " " + n).strip() if pr else n

        def _duree_h(d_deb, d_fin):
            if not d_deb or not d_fin:
                return 0.0
            try:
                t1 = datetime.fromisoformat(d_deb.replace("Z", ""))
                t2 = datetime.fromisoformat(d_fin.replace("Z", ""))
                delta = (t2 - t1).total_seconds() / 3600.0
                return round(delta, 3) if delta > 0 else 0.0
            except Exception:
                return 0.0

        out = []
        for d in raw:
            if not isinstance(d, dict):
                continue
            cat = d.get("categorie") or {}
            out.append({
                "intervenant":       _personne_nom(d.get("intervenant")),
                "categorie_code":    cat.get("code", "") if isinstance(cat, dict) else "",
                "categorie_libelle": cat.get("libelle", "") if isinstance(cat, dict) else "",
                "date_debut":        d.get("dateDebut", ""),
                "date_fin":          d.get("dateFin", ""),
                "duree_h":           _duree_h(d.get("dateDebut"), d.get("dateFin")),
                "libelle":           (d.get("libelle") or "")[:300],
            })
        return {"np": out}
    except Exception as e:
        return {"np": [], "error": str(e)}


def artis_patch_saisir_crit(it_numero, body, dry_run=True):
    """
    PATCH /services/rest/v2/it/{numero}/saisirCrit pour clôturer/saisir le compte rendu.
    Champs obligatoires côté API : dateDebut, commentaireClient, lignes (au moins [{}]).
    En dry-run, n'envoie PAS la requête — retourne le payload qui aurait été envoyé.
    """
    base = cfg("ARTIS_BASE_URL")
    if not base:
        return {"ok": False, "error": "ARTIS_BASE_URL manquant"}
    # Forcer HTTPS : Artis renvoie 302 sur HTTP et requests dégrade PATCH→GET sur le suivi → 405
    base_v2 = base.replace("/rest/v1", "/rest/v2").replace("http://", "https://", 1)
    url = f"{base_v2}/it/{it_numero}/saisirCrit"

    if dry_run:
        return {"ok": True, "dry_run": True, "url": url, "payload": body}

    token = artis_get_token()
    if not token:
        return {"ok": False, "error": "token Artis indisponible"}
    try:
        r = requests.patch(
            url,
            params={"token": token},
            headers={"accept": "*/*", "content-type": "application/json"},
            data=json.dumps(body),
            timeout=30,
        )
        out = {"ok": r.ok, "status": r.status_code, "url": url}
        if r.status_code == 204 or not r.text:
            out["response"] = {"message": "Saisie CRIT enregistrée (HTTP 204 No Content)"}
        else:
            try:
                out["response"] = r.json()
            except Exception:
                out["response"] = r.text[:500]
        return out
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}


def artis_passer_dit_non_facturable(numero_dit, dry_run=True, pause_s=5, max_retries=3,
                                     intervenant_matricule="ELCOHEN", commentaire=None):
    """
    Bascule une DIT clôturée Facturable → NonFacturable via la séquence :
      1. PATCH /dit/{n}/reouvrir          → crée une nouvelle IT en APlanifier
      2. pause (gestion latence Artis)
      3. PATCH /it/{newIT}/intervenant/{matricule}  (optionnel)
      4. PATCH /it/{newIT}/saisirCrit avec lignes[0].typeFacturation=NonFacturable

    Retry intelligent : si étape 3 ou 4 retourne 404 (IT pas encore indexée),
    attente +pause_s et nouvelle tentative, jusqu'à max_retries.

    Si dry_run=True : retourne le payload prévisionnel, n'envoie rien.

    Retour : dict {ok, dry_run, dit, new_it, steps[], final_etat, type_facturation, error}
    """
    base = cfg("ARTIS_BASE_URL")
    if not base:
        return {"ok": False, "error": "ARTIS_BASE_URL manquant"}
    if not str(numero_dit).isdigit():
        return {"ok": False, "error": "numero DIT invalide"}

    base_v2 = base.replace("/rest/v1", "/rest/v2").replace("http://", "https://", 1)

    # Préparation du payload saisirCrit (format spécifique NonFacturable)
    now = datetime.now()
    iso_now = now.strftime("%Y-%m-%dT%H:%M:%S")
    iso_end = (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S")
    if not commentaire:
        commentaire = f"Passage en non facturable — validation E. Cohen {now.strftime('%Y-%m-%d %H:%M')}"

    saisir_body = {
        "dateDebut": iso_now,
        "dateFin":   iso_end,
        "commentaireClient": commentaire,
        "lignes": [{
            "prestation":      {"code": "INF"},
            "qteUtilisee":     0.01,
            "typeFacturation": "NonFacturable",
        }],
    }

    if dry_run:
        return {
            "ok": True, "dry_run": True,
            "dit": numero_dit,
            "intervenant": intervenant_matricule,
            "pause_s": pause_s,
            "max_retries": max_retries,
            "preview": {
                "step_1": f"PATCH {base_v2}/dit/{numero_dit}/reouvrir",
                "step_2": f"sleep {pause_s}s",
                "step_3_optional": f"PATCH {base_v2}/it/<NEW_IT>/intervenant/{intervenant_matricule}",
                "step_4": f"PATCH {base_v2}/it/<NEW_IT>/saisirCrit",
                "saisir_body": saisir_body,
            },
            "_message": "Dry-run — rien envoyé. Re-poster avec dry_run=False pour exécuter.",
        }

    token = artis_get_token()
    if not token:
        return {"ok": False, "error": "token Artis indisponible"}

    headers = {"accept": "*/*", "content-type": "application/json"}
    steps = []

    def _record(step_name, status, **extra):
        rec = {"step": step_name, "ts": datetime.now().strftime("%H:%M:%S"), "status": status}
        rec.update(extra)
        steps.append(rec)
        log(f"[NF DIT {numero_dit}] {step_name} → {status} {extra.get('detail') or ''}")

    # ── Étape 0 : lecture état actuel pour détecter un état "déjà réouvert" ──
    # Si la DIT n'est pas Cloturee/TravailTermine → soit retry après échec partiel
    # (on récupère l'IT existante et saute reouvrir), soit état non réouvrable.
    # États qui acceptent reouvrir côté Artis : Cloturee, TravailTermine
    # États "déjà ouverts" possibles après échec partiel : EnCours, APlanifier, TravailNonTermine
    url_dit = f"{base.replace('http://', 'https://', 1)}/dit/{numero_dit}"
    skip_reouvrir = False
    new_it = None
    etat_initial = None
    try:
        r0 = requests.get(url_dit, params={"token": token},
                          headers={"accept": "application/json"}, timeout=30)
        if r0.status_code == 200:
            d0 = r0.json()
            etat0 = d0.get("etat")
            if isinstance(etat0, dict): etat0 = etat0.get("code")
            etat_initial = etat0
            _record("etat_initial", "OK", detail=f"DIT en état: {etat0}")

            if etat0 not in ("Cloturee", "TravailTermine"):
                # DIT pas dans un état réouvrable — chercher une IT existante à reprendre
                inters0 = [it for it in (d0.get("inter") or []) if isinstance(it, dict)]
                # Trier par date début décroissante (la plus récente d'abord)
                inters0.sort(key=lambda it: it.get("dateDebut") or "", reverse=True)

                # Logger tous les états des inter[] pour debug
                etats_inters = [(it.get("numero"),
                                 (it.get("etat") or {}).get("code") if isinstance(it.get("etat"), dict) else it.get("etat"))
                                for it in inters0]
                _record("scan_inter", "OK", detail=f"{len(inters0)} interventions trouvées: {etats_inters}")

                # Stratégie : prendre la plus récente IT NON finalisée (pas Terminee/Cloturee/Annulee/AValider)
                FINALIZED = {"Terminee", "Cloturee", "Annulee", "AValider"}
                target = None
                for it in inters0:
                    et = (it.get("etat") or {}).get("code") if isinstance(it.get("etat"), dict) else it.get("etat")
                    if et and et not in FINALIZED:
                        target = it
                        break
                # Si rien trouvé, fallback : prendre la plus récente quel que soit l'état
                # (on tentera saisirCrit, Artis nous dira si pas possible)
                if not target and inters0:
                    target = inters0[0]

                if target:
                    new_it = target.get("numero")
                    target_etat = (target.get("etat") or {}).get("code", "?") if isinstance(target.get("etat"), dict) else target.get("etat")
                    skip_reouvrir = True
                    _record("resume", "OK",
                            detail=f"DIT en état {etat0}, reprise sur IT {new_it} (état={target_etat})",
                            new_it=new_it)
                else:
                    return {"ok": False, "dit": numero_dit, "steps": steps,
                            "etat_initial": etat0,
                            "error": f"DIT en état '{etat0}' — aucune intervention dans inter[]. Vérifie dans Artis Web."}
    except Exception as e:
        _record("etat_initial", "EXCEPTION", error=str(e))
        # On continue avec le workflow normal en espérant que ça passe

    # ── Étape 1 : reouvrir (sauté en mode resume) ──
    if not skip_reouvrir:
        url_reo = f"{base_v2}/dit/{numero_dit}/reouvrir"
        try:
            r = requests.patch(url_reo, params={"token": token}, headers=headers, timeout=60)
        except Exception as e:
            _record("reouvrir", "EXCEPTION", error=str(e))
            return {"ok": False, "dit": numero_dit, "steps": steps, "error": f"reouvrir: {e}"}
        if r.status_code != 204:
            body = (r.text or "")[:300]
            _record("reouvrir", r.status_code, detail=body)
            return {"ok": False, "dit": numero_dit, "steps": steps,
                    "error": f"reouvrir HTTP {r.status_code}: {body}"}
        _record("reouvrir", 204, detail="DIT réouverte, nouvelle IT créée")

    # ── Lecture DIT pour récupérer le numéro de la nouvelle IT (si pas en mode resume) ──
    # On retry une fois si la liste inter ne contient pas encore la nouvelle entry
    if not new_it:
        for attempt in range(max_retries):
            try:
                rd = requests.get(url_dit, params={"token": token},
                                  headers={"accept": "application/json"}, timeout=30)
            except Exception as e:
                _record("get_dit", "EXCEPTION", error=str(e), attempt=attempt + 1)
                time.sleep(pause_s)
                continue
            if rd.status_code == 200:
                try:
                    d = rd.json()
                    inters = d.get("inter") or []
                    # Chercher l'IT en état APlanifier (la dernière créée)
                    planif = [it for it in inters if isinstance(it, dict)
                              and isinstance(it.get("etat"), dict)
                              and it["etat"].get("code") == "APlanifier"]
                    if planif:
                        # La plus récente
                        planif.sort(key=lambda it: it.get("dateDebut") or "", reverse=True)
                        new_it = planif[0].get("numero")
                        break
                    # Sinon prendre l'IT la plus récente quoi qu'il arrive
                    if inters and not new_it:
                        sorted_inters = sorted([it for it in inters if isinstance(it, dict)],
                                              key=lambda it: it.get("dateDebut") or "", reverse=True)
                        if sorted_inters:
                            new_it = sorted_inters[0].get("numero")
                            break
                except Exception as e:
                    _record("get_dit", "PARSE_ERROR", error=str(e), attempt=attempt + 1)
            else:
                _record("get_dit", rd.status_code, attempt=attempt + 1)
            time.sleep(pause_s)

    if not new_it:
        return {"ok": False, "dit": numero_dit, "steps": steps,
                "error": f"impossible de trouver la nouvelle IT après {max_retries} tentatives"}
    _record("get_new_it", "OK", new_it=new_it)

    # ── Étape 2 : pause additionnelle pour stabilisation ──
    time.sleep(pause_s)
    _record("pause", "OK", duration_s=pause_s)

    # ── Étape 3 : set intervenant (optionnel, retry sur 404) ──
    if intervenant_matricule:
        url_int = f"{base_v2}/it/{new_it}/intervenant/{intervenant_matricule}"
        for attempt in range(max_retries):
            try:
                ri = requests.patch(url_int, params={"token": token}, headers=headers, timeout=60)
            except Exception as e:
                _record("intervenant", "EXCEPTION", error=str(e), attempt=attempt + 1)
                time.sleep(pause_s)
                continue
            if ri.status_code == 204:
                _record("intervenant", 204, detail=intervenant_matricule)
                break
            if ri.status_code == 404 and attempt < max_retries - 1:
                _record("intervenant", 404, attempt=attempt + 1, retry=True)
                time.sleep(pause_s)
                continue
            # Autre erreur ou dernier retry → on logue mais on continue (intervenant optionnel)
            _record("intervenant", ri.status_code, detail=(ri.text or "")[:200])
            break

    # ── Étape 4 : saisirCrit avec NonFacturable (retry sur 404) ──
    url_sc = f"{base_v2}/it/{new_it}/saisirCrit"
    saisir_ok = False
    for attempt in range(max_retries):
        try:
            rs = requests.patch(url_sc, params={"token": token}, headers=headers,
                                data=json.dumps(saisir_body), timeout=60)
        except Exception as e:
            _record("saisirCrit", "EXCEPTION", error=str(e), attempt=attempt + 1)
            time.sleep(pause_s)
            continue
        if rs.status_code == 204:
            _record("saisirCrit", 204, detail="NonFacturable enregistré")
            saisir_ok = True
            break
        if rs.status_code == 404 and attempt < max_retries - 1:
            _record("saisirCrit", 404, attempt=attempt + 1, retry=True)
            time.sleep(pause_s)
            continue
        _record("saisirCrit", rs.status_code, detail=(rs.text or "")[:300])
        break

    if not saisir_ok:
        return {"ok": False, "dit": numero_dit, "new_it": new_it, "steps": steps,
                "error": "saisirCrit a échoué après retries"}

    # ── Vérification finale : relire la DIT ──
    final_etat = None
    final_type = None
    try:
        rf = requests.get(url_dit, params={"token": token},
                          headers={"accept": "application/json"}, timeout=30)
        if rf.status_code == 200:
            d = rf.json()
            etat = d.get("etat") or {}
            final_etat = etat.get("code") if isinstance(etat, dict) else str(etat)
            # Trouver l'IT qu'on vient de saisir et lire son typeFacturation
            for it in d.get("inter") or []:
                if isinstance(it, dict) and str(it.get("numero")) == str(new_it):
                    lignes = it.get("lignes") or []
                    if lignes and isinstance(lignes[0], dict):
                        final_type = lignes[0].get("typeFacturation")
                    break
    except Exception:
        pass
    _record("final_check", "OK", etat=final_etat, type_facturation=final_type)

    # ── Persistance dans l'historique ──
    try:
        # Récupérer infos client depuis la DIT relue
        client_code = ""
        client_nom = ""
        try:
            cl = (d.get("client") if rf.status_code == 200 else None) or {}
            if isinstance(cl, dict):
                client_code = cl.get("code", "")
                client_nom  = cl.get("libelle", "")
        except Exception:
            pass
        entry = {
            "ts":              datetime.now().isoformat(timespec="seconds"),
            "dit":             str(numero_dit),
            "new_it":          str(new_it) if new_it else None,
            "client_code":     client_code,
            "client_nom":      client_nom,
            "commentaire":     commentaire,
            "intervenant":     intervenant_matricule,
            "final_etat":      final_etat,
            "type_facturation": final_type,
            "ok":              final_type == "NonFacturable",
        }
        history_file = DATA_DIR / "nonfact_history.json"
        try:
            existing = []
            if history_file.exists():
                with open(history_file, encoding="utf-8") as f:
                    existing = json.load(f) or []
            existing.append(entry)
            # Garder les 1000 dernières entrées max
            if len(existing) > 1000:
                existing = existing[-1000:]
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"[NF DIT {numero_dit}] persistance historique échouée: {e}")
    except Exception:
        pass

    return {
        "ok": True, "dry_run": False,
        "dit": numero_dit, "new_it": new_it,
        "steps": steps,
        "final_etat": final_etat,
        "type_facturation": final_type,
    }


def artis_cloturer_dit(numero_dit, commentaire=None, dry_run=True, force_finalize=False, pause_s=3):
    """
    PATCH /dit/{numeroDit}/cloturer — passe une DIT de TravailTermine → Cloturee.

    Si la DIT est en TravailNonTermine et force_finalize=True : finalise chaque IT
    non finalisée (saisirCrit minimaliste 0.01h) puis cloture la DIT.

    Retour : {ok, dry_run, dit, etat_initial, steps[], error}
    """
    base = cfg("ARTIS_BASE_URL")
    if not base:
        return {"ok": False, "error": "ARTIS_BASE_URL manquant"}
    if not str(numero_dit).isdigit():
        return {"ok": False, "error": "numero DIT invalide"}

    base_v2 = base.replace("/rest/v1", "/rest/v2").replace("http://", "https://", 1)
    base_https = base.replace("http://", "https://", 1)
    if not commentaire:
        commentaire = f"Clôture validée par E. Cohen — {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    if dry_run:
        return {
            "ok": True, "dry_run": True,
            "dit": numero_dit,
            "preview": {
                "step_check": f"GET {base_https}/dit/{numero_dit}",
                "step_finalize_iter (si TravailNonTermine + force_finalize)": f"PATCH {base_v2}/it/<IT>/saisirCrit (× chaque IT non finalisée)",
                "step_cloturer (si TravailTermine seulement)": f"PATCH {base_v2}/dit/{numero_dit}/cloturer?commentaire=...",
                "_note": "Pour TravailNonTermine, le saisirCrit final met la DIT en Cloturee automatiquement → /cloturer ignoré.",
                "commentaire": commentaire,
                "force_finalize": force_finalize,
            },
            "_message": "Dry-run — rien envoyé. Re-poster avec dry_run=False pour exécuter.",
        }

    token = artis_get_token()
    if not token:
        return {"ok": False, "error": "token Artis indisponible"}

    headers = {"accept": "*/*", "content-type": "application/json"}
    steps = []

    def _record(step_name, status, **extra):
        rec = {"step": step_name, "ts": datetime.now().strftime("%H:%M:%S"), "status": status}
        rec.update(extra)
        steps.append(rec)
        log(f"[CLOT DIT {numero_dit}] {step_name} → {status} {extra.get('detail') or ''}")

    # ── Étape 1 : lire l'état actuel ──
    try:
        r0 = requests.get(f"{base_https}/dit/{numero_dit}", params={"token": token},
                          headers={"accept": "application/json"}, timeout=30)
    except Exception as e:
        _record("read_dit", "EXCEPTION", error=str(e))
        return {"ok": False, "dit": numero_dit, "steps": steps, "error": f"lecture DIT: {e}"}
    if r0.status_code != 200:
        _record("read_dit", r0.status_code, detail=(r0.text or "")[:200])
        return {"ok": False, "dit": numero_dit, "steps": steps,
                "error": f"lecture DIT HTTP {r0.status_code}"}
    d0 = r0.json()
    etat0 = d0.get("etat")
    if isinstance(etat0, dict): etat0 = etat0.get("code")
    _record("read_dit", "OK", detail=f"état: {etat0}")

    if etat0 == "Cloturee":
        return {"ok": False, "dit": numero_dit, "etat_initial": etat0, "steps": steps,
                "error": "DIT déjà clôturée"}

    # ── Étape 2 : si TravailNonTermine, finaliser les IT non terminées (si autorisé) ──
    FINALIZED = {"Terminee", "Cloturee", "Annulee"}
    if etat0 == "TravailNonTermine":
        if not force_finalize:
            return {"ok": False, "dit": numero_dit, "etat_initial": etat0, "steps": steps,
                    "error": "DIT en TravailNonTermine — il y a des IT non finalisées. Active force_finalize=true pour les finaliser automatiquement."}
        # Finaliser chaque IT non finalisée
        # NB : it.get("etat") peut être un str (API v1) ou un dict {code,libelle}
        def _it_etat(it):
            e = it.get("etat")
            if isinstance(e, dict): return e.get("code")
            return e
        inters = d0.get("inter") or []
        to_finalize = [it for it in inters if isinstance(it, dict)
                       and _it_etat(it) not in FINALIZED]

        # Référence : date de fin de la dernière intervention (max dateDebut+duree)
        def _it_end_dt(it):
            dt = it.get("dateDebut")
            if not dt: return None
            try:
                d = datetime.fromisoformat(str(dt).replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                try: d = datetime.strptime(str(dt)[:19], "%Y-%m-%dT%H:%M:%S")
                except Exception: return None
            try: return d + timedelta(hours=float(it.get("duree") or 0))
            except Exception: return d
        end_dts = [t for t in (_it_end_dt(it) for it in inters if isinstance(it, dict)) if t is not None]
        ref_dt = max(end_dts) if end_dts else datetime.now()
        _record("finalize_scan", "OK",
                detail=f"{len(to_finalize)} IT à finaliser, daté au {ref_dt.strftime('%Y-%m-%d %H:%M')}")

        for idx, it in enumerate(to_finalize):
            it_num = it.get("numero")
            # +N secondes pour différencier les saisirCrit successifs
            base_dt = ref_dt + timedelta(seconds=idx)
            saisir_body = {
                "dateDebut": base_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "dateFin":   (base_dt + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S"),
                "commentaireClient": commentaire,
                "lignes": [{
                    "prestation":      {"code": "INF"},
                    "qteUtilisee":     0.01,
                    "typeFacturation": it.get("typeFacturation") or "Automatique",
                }],
            }
            try:
                rs = requests.patch(f"{base_v2}/it/{it_num}/saisirCrit",
                                    params={"token": token}, headers=headers,
                                    data=json.dumps(saisir_body), timeout=60)
                if rs.status_code == 204:
                    _record(f"finalize_it_{it_num}", 204, detail="finalisée")
                else:
                    _record(f"finalize_it_{it_num}", rs.status_code, detail=(rs.text or "")[:200])
                    return {"ok": False, "dit": numero_dit, "etat_initial": etat0, "steps": steps,
                            "error": f"finalize IT {it_num} HTTP {rs.status_code}"}
            except Exception as e:
                _record(f"finalize_it_{it_num}", "EXCEPTION", error=str(e))
                return {"ok": False, "dit": numero_dit, "etat_initial": etat0, "steps": steps,
                        "error": f"finalize IT {it_num}: {e}"}
        time.sleep(pause_s)
    elif etat0 != "TravailTermine":
        return {"ok": False, "dit": numero_dit, "etat_initial": etat0, "steps": steps,
                "error": f"DIT en état '{etat0}' — non clôturable. Doit être TravailTermine ou TravailNonTermine."}

    # ── Étape 3 : clôturer la DIT ──
    # Cas TravailNonTermine : le saisirCrit final met déjà la DIT en Cloturee
    # → on vérifie d'abord l'état, et on skip /cloturer si déjà Cloturee.
    final_etat = None
    skip_cloturer = False
    if etat0 == "TravailNonTermine":
        try:
            rc = requests.get(f"{base_https}/dit/{numero_dit}", params={"token": token},
                              headers={"accept": "application/json"}, timeout=30)
            if rc.status_code == 200:
                etc = rc.json().get("etat")
                etc_code = etc.get("code") if isinstance(etc, dict) else etc
                if etc_code == "Cloturee":
                    skip_cloturer = True
                    final_etat = etc_code
                    _record("post_finalize_check", "OK",
                            detail="DIT déjà Cloturee après saisirCrit — étape cloturer ignorée")
                else:
                    _record("post_finalize_check", "OK", detail=f"état: {etc_code}")
        except Exception as e:
            _record("post_finalize_check", "EXCEPTION", error=str(e))

    if not skip_cloturer:
        url_cl = f"{base_v2}/dit/{numero_dit}/cloturer"
        try:
            r = requests.patch(url_cl, params={"token": token, "commentaire": commentaire},
                               headers=headers, timeout=60)
        except Exception as e:
            _record("cloturer", "EXCEPTION", error=str(e))
            return {"ok": False, "dit": numero_dit, "etat_initial": etat0, "steps": steps,
                    "error": f"cloturer: {e}"}
        if r.status_code != 204:
            _record("cloturer", r.status_code, detail=(r.text or "")[:300])
            return {"ok": False, "dit": numero_dit, "etat_initial": etat0, "steps": steps,
                    "error": f"cloturer HTTP {r.status_code}: {(r.text or '')[:200]}"}
        _record("cloturer", 204, detail="DIT clôturée")

    # ── Étape 4 : vérification finale ──
    if final_etat is None:
        try:
            rf = requests.get(f"{base_https}/dit/{numero_dit}", params={"token": token},
                              headers={"accept": "application/json"}, timeout=30)
            if rf.status_code == 200:
                etat_f = rf.json().get("etat")
                final_etat = etat_f.get("code") if isinstance(etat_f, dict) else etat_f
        except Exception:
            pass
    _record("final_check", "OK", etat=final_etat)

    # ── Persistance dans l'historique ──
    try:
        client = d0.get("client") or {}
        entry = {
            "ts":              datetime.now().isoformat(timespec="seconds"),
            "dit":             str(numero_dit),
            "client_code":     client.get("code", "") if isinstance(client, dict) else "",
            "client_nom":      client.get("libelle", "") if isinstance(client, dict) else "",
            "etat_initial":    etat0,
            "final_etat":      final_etat,
            "force_finalize":  force_finalize,
            "commentaire":     commentaire,
            "ok":              final_etat == "Cloturee",
        }
        history_file = DATA_DIR / "cloture_history.json"
        existing = []
        if history_file.exists():
            with open(history_file, encoding="utf-8") as f:
                existing = json.load(f) or []
        existing.append(entry)
        if len(existing) > 1000:
            existing = existing[-1000:]
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[CLOT DIT {numero_dit}] persistance historique échouée: {e}")

    return {
        "ok": True, "dry_run": False,
        "dit": numero_dit,
        "etat_initial": etat0,
        "final_etat": final_etat,
        "steps": steps,
    }


def artis_get_clients(token):
    """
    Renvoie (clients_csi, full_mapping) où :
      - clients_csi    : liste filtrée 'Contrat Services Informatique' (utilisée pour reconciliation et appels RFC détaillés)
      - full_mapping   : dict {code_normalisé: ssc_libelle} sur TOUS les types de contrats (utilisé pour la refacturation pour avoir la SSC même quand le client n'a pas de CSI)
    """
    url = f"{cfg('ARTIS_BASE_URL')}/contrat?maxResults=9999999&token={token}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
        timeout=60
    )
    resp.raise_for_status()
    d = resp.json()
    raw = d if isinstance(d, list) else (d.get("data") or d.get("results") or d.get("contrats") or list(d.values()))

    clients = {}
    full_mapping = {}
    for c in raw:
        if c.get("ssp", {}).get("libelle") != "Contrat Services Informatique":
            continue
        code = c.get("client", {}).get("code", "")
        if not code:
            continue
        norm = normalize_code(code)
        ssc  = c.get("orgInt", {}).get("libelle", "") or ""
        if ssc and norm not in full_mapping:
            full_mapping[norm] = ssc

        if norm not in clients:
            clients[norm] = {
                "code": norm,
                "code_raw": code,
                "libelle": c.get("client", {}).get("libelle", ""),
                "org": ssc,
                "contrats": []
            }
        clients[norm]["contrats"].append({
            "code": c.get("code", ""),
            "statut": c.get("statut", ""),
        })
    return sorted(clients.values(), key=lambda x: x["libelle"]), full_mapping


def artis_get_chefs_projet(token, lookback_days=365):
    """
    Pour chaque client, identifie le 'Chef de projet informatique' en se basant sur
    les interventions avec UO code='INFO-PROJ' sur les `lookback_days` derniers jours.

    Stratégie : agrège le temps cumulé par (client_code, intervenant_matricule) puis
    prend l'intervenant avec le plus de temps cumulé. Tie-break = intervention la plus récente.

    Renvoie {code_norm: {nom, prenom, matricule, email, total_hours, last_date, nb_interventions}}.
    """
    base = cfg("ARTIS_BASE_URL")
    if not base or not token:
        return {}
    date_fin   = date.today()
    date_debut = date_fin - timedelta(days=lookback_days)
    url = (f"{base}/dit?dateDebut={date_debut.isoformat()}"
           f"&dateFin={date_fin.isoformat()}&maxResults=999999&token={token}")
    t0 = time.time()
    try:
        r = requests.get(url, headers={"accept": "application/json"}, timeout=300)
        if r.status_code != 200:
            log(f"    ⚠ Chef de projet: HTTP {r.status_code}")
            return {}
        dits = r.json() if isinstance(r.json(), list) else r.json().get("data", []) or []
    except Exception as e:
        log(f"    ⚠ Chef de projet: {e}")
        return {}

    # agrégation : {client_norm: {matricule: {total, last_date, intervenant}}}
    agg = {}
    for d in dits:
        client = (d.get("client") or {})
        ccode = normalize_code(client.get("code", "") or "")
        if not ccode:
            continue
        for it in (d.get("inter") or []):
            uo = it.get("uo") or {}
            if uo.get("code") != "INFO-PROJ":
                continue
            iv = it.get("intervenant") or {}
            mat = iv.get("matricule")
            if not mat:
                continue
            try:
                dur = float(it.get("duree") or 0)
            except (TypeError, ValueError):
                dur = 0.0
            dt = it.get("dateDebut", "") or ""
            entry = agg.setdefault(ccode, {}).setdefault(mat, {
                "total": 0.0,
                "last_date": "",
                "nb": 0,
                "intervenant": iv,
            })
            entry["total"] += dur
            entry["nb"]    += 1
            if dt > entry["last_date"]:
                entry["last_date"] = dt
                entry["intervenant"] = iv  # info la plus à jour

    # Pour chaque client : top intervenant
    result = {}
    for ccode, mats in agg.items():
        # tri : total desc, puis last_date desc
        best_mat, best = max(
            mats.items(),
            key=lambda kv: (kv[1]["total"], kv[1]["last_date"])
        )
        iv = best["intervenant"]
        mail = (iv.get("adresse") or {}).get("mail1") or ""
        result[ccode] = {
            "nom":              iv.get("nom", "") or "",
            "prenom":           iv.get("prenom", "") or "",
            "matricule":        iv.get("matricule", "") or "",
            "email":            mail,
            "total_hours":      round(best["total"], 2),
            "last_date":        best["last_date"][:10],
            "nb_interventions": best["nb"],
        }
    log(f"    › Chef de projet: {len(result)} clients identifiés (UO INFO-PROJ, {lookback_days}j) en {time.time()-t0:.1f}s")
    return result


def artis_get_affaires(token, clients):
    """
    Pour chaque client CSI, fetch /affaire?codeClient=X et garde la dernière
    affaire (par dateAffaire desc). Renvoie {code_norm: {gestionnaire, vendeur, num_affaire, date_affaire}}.

    gestionnaire/vendeur = {nom, prenom, matricule, email}
    """
    base = cfg("ARTIS_BASE_URL")
    if not base or not token:
        return {}

    def fetch(client):
        code = client["code"]
        try:
            r = requests.get(f"{base}/affaire?codeClient={client['code_raw']}&token={token}",
                             timeout=30)
            if r.status_code != 200:
                return code, None
            affs = r.json()
            if not isinstance(affs, list) or not affs:
                return code, None
            # Tri : dateAffaire desc ; en cas d'égalité, préfère etatApresVente=Encours
            affs_sorted = sorted(
                affs,
                key=lambda a: (
                    a.get("dateAffaire") or "",
                    1 if a.get("etatApresVente") == "Encours" else 0
                ),
                reverse=True
            )
            last = affs_sorted[0]
            def person(p):
                if not p: return None
                mail = (p.get("adresse") or {}).get("mail1") or ""
                return {
                    "nom":       p.get("nom", "") or "",
                    "prenom":    p.get("prenom", "") or "",
                    "matricule": p.get("matricule", "") or "",
                    "email":     mail,
                }
            return code, {
                "gestionnaire": person(last.get("gestionnaire")),
                "vendeur":      person(last.get("vendeur")),
                "num_affaire":  last.get("numero", ""),
                "date_affaire": last.get("dateAffaire", ""),
                "nb_affaires":  len(affs),
            }
        except Exception:
            return code, None

    t0 = time.time()
    result = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        for code, info in ex.map(fetch, clients):
            if info:
                result[code] = info
    log(f"    › Affaires: {len(result)}/{len(clients)} clients ont au moins une affaire ({time.time()-t0:.1f}s)")
    return result


def artis_get_rfc_details(token, clients):
    """
    Fetch billed quantities per service per Artis client via the RFC detail API.
    Returns dict: {client_code: {"datto":0,"m365":0,"sophos":0,"veeam_tb":0,"veeam365":0,"vade":0,"soc":0}}

    Excel formula mapping:
      DATTO   : SUMIFS(G, V=code, T LIKE "*infog*rance*")
      M365    : SUMIFS(G, V=code, S LIKE "* 365")
      SOPHOS  : SUMIFS(G, V=code, S LIKE "*Antivirus*")
               (les firewalls ne sont plus comptés depuis 2026-05-07)
      VEEAM   : SUMIFS(W, V=code, S="SAUVEGARDE")  — W = TB extracted from RFC libelle
      VEEAM365: COUNTIFS(V=code, S="SAUVEGARDE", T LIKE "*Microsoft 365*")
      VADE    : SUMIFS(G, V=code, T LIKE "*antispam*")
      SOC     : SUMIFS(G, V=code, T LIKE "*SOC*")
    """
    base = cfg('ARTIS_BASE_URL')
    hdr  = {"Authorization": f"Bearer {token}", "accept": "application/json"}

    t_start = time.time()
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=80, pool_maxsize=80, max_retries=0)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(hdr)

    # Map contrat_code → client_code
    contrat_client = {}
    for cl in clients:
        for ct in cl.get("contrats", []):
            if ct.get("code"):
                contrat_client[ct["code"]] = cl["code"]

    # Initialize results
    billed = {cl["code"]: {"datto": 0, "m365": 0, "sophos": 0,
                            "veeam_tb": 0, "veeam365": 0, "vade": 0, "soc": 0}
              for cl in clients}

    # Only fetch RFC lists for service-relevant MSC libelles
    def is_relevant_msc(lib):
        l = lib.lower()
        return any([
            "sauvegarde" in l,
            "365" in l,
            "antivirus" in l,
            "firewall" in l,
            "infog" in l,
            "maintenance" in l,
            "antispam" in l,
            "promail" in l,
            "cybersecurit" in l,
        ])

    # Classify an RFC line into services. Order = priorité (premier = primary_svc).
    def classify(msc_lib, rfc_lib):
        ml = msc_lib.lower()
        rl = rfc_lib.lower()
        svcs = []
        has_soc = bool(re.search(r"\bsoc\b", rl))

        # 1. VADE — "antispam" gagne contre "infogér" sur "Promail - AntiSpam infogéré"
        if "antispam" in ml or "antispam" in rl:
            svcs.append("vade")
        # 2. SOC avant sophos pour gagner sur "Antivirus poste + SOC"
        if has_soc:
            svcs.append("soc")
        # 3. Sophos — uniquement antivirus, plus les firewalls (décision 2026-05-07)
        if "antivirus" in ml or ("antivirus" in rl and not has_soc):
            svcs.append("sophos")
        # 4. VEEAM / M365
        if "sauvegarde" in ml:
            if "microsoft 365" in rl or "vbo 365" in rl:
                svcs.append("veeam365")
            else:
                svcs.append("veeam_tb")
        if "365" in ml and "sauvegarde" not in ml:
            svcs.append("m365")
        # 4b. M365 via RFC : "Gouvernance 365" / "Infogérance 365 (par licence)" sous MSC "Infogérance"
        # → c'est un service M365 par licence, PAS du RMM. Évite faux positif DATTO ci-dessous.
        is_365_rfc = "365" in rl and "sauvegarde" not in rl
        if is_365_rfc and "m365" not in svcs:
            svcs.append("m365")
        # 5. DATTO — "datto" explicite OU (MSC infog/maintenance + signal poste/serveur/infogér)
        #    Exclut les RFC 365/gouvernance qui matcheraient "serveur" ("absence de serveur physique")
        #    ou "infogér" ("Infogérance 365 par licence").
        is_infog_or_maint_msc = "infog" in ml or "maintenance" in ml
        is_gouvernance = "gouvernance" in rl
        if "datto" in rl:
            svcs.append("datto")
        elif is_infog_or_maint_msc and re.search(r"poste|serveur|infog[eé]r", rl) and not is_365_rfc and not is_gouvernance:
            svcs.append("datto")
        return svcs

    # Stage 1: MSC lists
    log(f"    › RFC: liste MSC ({len(contrat_client)} contrats)...")

    def fetch_msc(contrat_code):
        try:
            r = session.get(f"{base}/contrat/{contrat_code}/msc?token={token}", timeout=30)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return [(contrat_code, m.get("code", ""), m.get("libelle", ""))
                            for m in data
                            if m.get("statut") not in ("Fermé", "Résilié.Soldé")
                            and is_relevant_msc(m.get("libelle", ""))]
        except Exception:
            pass
        return []

    msc_triples = []
    with ThreadPoolExecutor(max_workers=80) as ex:
        futs = [ex.submit(fetch_msc, cc) for cc in contrat_client]
        for f in as_completed(futs):
            msc_triples.extend(f.result())

    log(f"    › RFC: {len(msc_triples)} MSC pertinents → liste RFC...")

    # Stage 2: RFC lists
    def fetch_rfcs(contrat_code, msc_code, msc_libelle):
        try:
            r = session.get(f"{base}/contrat/{contrat_code}/msc/{msc_code}/rfc?token={token}", timeout=30)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return [(contrat_code, msc_code, msc_libelle,
                             rfc.get("code", ""), rfc.get("libelle", ""))
                            for rfc in data]
        except Exception:
            pass
        return []

    rfc_items = []
    with ThreadPoolExecutor(max_workers=80) as ex:
        futs = [ex.submit(fetch_rfcs, *t) for t in msc_triples]
        for f in as_completed(futs):
            rfc_items.extend(f.result())

    log(f"    › RFC: {len(rfc_items)} RFC trouvés → détails quantités...")

    # Build per-contrat detailed line list (for client drill-down)
    rfc_lines_by_contrat = {}  # {contrat_code: [{msc, rfc_code, rfc, qty, service}, ...]}
    line_ref = {}              # {(contrat_code, rfc_code): index in rfc_lines_by_contrat[cc]}
    need_detail = []           # (contrat_code, msc_code, rfc_code, client_code, [services to fetch])

    for (cc, mc, ml, rc, rl) in rfc_items:
        client_code = contrat_client.get(cc)
        if not client_code or client_code not in billed:
            continue
        svcs = classify(ml, rl)
        primary_svc = svcs[0] if svcs else None

        qty = 0
        detail_svcs = []
        for svc in svcs:
            if svc == "veeam365":
                billed[client_code]["veeam365"] += 1
                qty = max(qty, 1)
            elif svc == "veeam_tb":
                tb = extract_tb(rl)
                billed[client_code]["veeam_tb"] += tb
                qty = max(qty, tb)
            else:
                detail_svcs.append(svc)

        line = {"msc": ml, "rfc_code": rc, "rfc": rl, "qty": qty,
                "service": primary_svc, "prix_unitaire": 0, "total": 0}
        rfc_lines_by_contrat.setdefault(cc, []).append(line)
        # On veut le prix pour TOUTES les lignes (pas seulement celles qui ont besoin du qty)
        line_ref[(cc, rc)] = (len(rfc_lines_by_contrat[cc]) - 1, detail_svcs)
        need_detail.append((cc, mc, rc, client_code, detail_svcs))

    log(f"    › RFC: {len(need_detail)} détails à récupérer (qté + prix)...")

    # Stage 3: RFC detail for quantity + price
    def fetch_detail(contrat_code, msc_code, rfc_code, client_code, svcs):
        try:
            r = session.get(
                f"{base}/contrat/{contrat_code}/msc/{msc_code}/rfc/{rfc_code}?token={token}",
                timeout=30
            )
            if r.status_code == 200:
                j = r.json()
                qty = 0
                if j.get("rfcmf"):
                    qty = float(j["rfcmf"].get("qteForfaitaire") or 0)
                elif j.get("rfcmfg"):
                    qty = float(j["rfcmfg"].get("grandeurAuForfait") or 0)
                # Prix unitaire : couts[0].uniformeOpts.coutFacture (€ facturé par unité)
                prix_u = 0.0
                couts = j.get("couts") or []
                if couts:
                    # Prendre rang=1 si présent, sinon le premier
                    cout = next((c for c in couts if c.get("rang") == 1), couts[0])
                    uo = cout.get("uniformeOpts") or {}
                    try:
                        prix_u = float(uo.get("coutFacture") or 0)
                    except (TypeError, ValueError):
                        prix_u = 0.0
                return contrat_code, rfc_code, client_code, svcs, qty, prix_u
        except Exception:
            pass
        return contrat_code, rfc_code, client_code, svcs, 0, 0.0

    with ThreadPoolExecutor(max_workers=80) as ex:
        futs = [ex.submit(fetch_detail, *t) for t in need_detail]
        for f in as_completed(futs):
            cc, rc, client_code, svcs, qty, prix_u = f.result()
            ref = line_ref.get((cc, rc))
            if not ref:
                continue
            idx, _ = ref
            line = rfc_lines_by_contrat[cc][idx]
            # Qty : seulement si on en avait besoin (services non-veeam) et qu'on l'a obtenu
            if qty and svcs and client_code in billed:
                for svc in svcs:
                    billed[client_code][svc] += qty
                line["qty"] = qty
            # Prix : pour toutes les lignes (sera 0 si non récupéré)
            line["prix_unitaire"] = prix_u
            line["total"] = (line.get("qty") or 0) * prix_u

    session.close()
    log(f"    › RFC API détaillée terminée en {time.time()-t_start:.1f}s")

    matched = sum(1 for v in billed.values() if any(v.values()))
    log(f"    › RFC OK — {matched} clients avec données de facturation")
    return billed, rfc_lines_by_contrat


# ═══════════════════════════════════════════════════════════════════════════════
# VEEAM
# ═══════════════════════════════════════════════════════════════════════════════

def veeam_get_data():
    base  = cfg("VEEAM_BASE_URL")
    token = cfg("VEEAM_TOKEN")
    if not base or not token:
        return {"companies": [], "usage": {}, "veeam365": [], "error": "credentials manquantes"}
    headers = {"accept": "application/json", "Authorization": token}
    try:
        # Companies (paginé)
        companies, offset = [], 0
        while True:
            r = requests.get(f"{base}/api/v3/organizations/companies?limit=500&offset={offset}",
                             headers=headers, timeout=30, verify=False)
            if r.status_code != 200:
                break
            data = r.json().get("data", [])
            for c in data:
                name = c.get("name", "")
                companies.append({
                    "uid": c["instanceUid"],
                    "name": name,
                    "artis_code": extract_artis_code(name),
                })
            if len(data) < 500:
                break
            offset += 500

        # Usage stockage
        usage = {}
        r = requests.get(f"{base}/api/v3/organizations/companies/sites/backupResources/usage?limit=500",
                         headers=headers, timeout=30, verify=False)
        if r.status_code == 200:
            d = r.json()
            total = d.get("meta", {}).get("pagingInfo", {}).get("total", 0)
            for item in d.get("data", []):
                usage[item["companyUid"]] = round(item.get("usedStorageQuota", 0) / 1099511627776, 3)
            offset = 500
            while offset < total:
                r2 = requests.get(
                    f"{base}/api/v3/organizations/companies/sites/backupResources/usage?limit=500&offset={offset}",
                    headers=headers, timeout=30, verify=False)
                if r2.status_code != 200:
                    break
                for item in r2.json().get("data", []):
                    usage[item["companyUid"]] = round(item.get("usedStorageQuota", 0) / 1099511627776, 3)
                offset += 500

        # VEEAM 365
        r365 = requests.get(f"{base}/api/v3/infrastructure/vb365Servers/organizations?limit=500",
                            headers=headers, timeout=30, verify=False)
        veeam365 = []
        for o in (r365.json().get("data", []) if r365.status_code == 200 else []):
            name = o.get("name", "")
            veeam365.append({
                "uid": o.get("instanceUid"),
                "name": name,
                "type": o.get("type"),
                "artis_code": extract_artis_code(name),
            })

        # PULSE LICENSES — points consommés par company UID (paginé)
        points = {}
        offset = 0
        while True:
            rp = requests.get(f"{base}/api/v3/pulse/licenses?limit=500&offset={offset}",
                              headers=headers, timeout=30, verify=False)
            if rp.status_code != 200:
                break
            dp = rp.json()
            data_p = dp.get("data", [])
            for it in data_p:
                uid = it.get("assignedCompanyUid")
                if not uid:
                    continue
                pts = float(it.get("points") or 0)
                points[uid] = points.get(uid, 0) + pts
            total_p = dp.get("meta", {}).get("pagingInfo", {}).get("total", 0)
            if offset + 500 >= total_p or not data_p:
                break
            offset += 500

        return {"companies": companies, "usage": usage, "veeam365": veeam365, "points": points}
    except Exception as e:
        return {"companies": [], "usage": {}, "veeam365": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# SOPHOS
# ═══════════════════════════════════════════════════════════════════════════════

def sophos_get_data():
    cid  = cfg("SOPHOS_CLIENT_ID")
    csec = cfg("SOPHOS_CLIENT_SECRET")
    if not cid or not csec:
        return {"tenants": [], "error": "credentials manquantes"}
    try:
        r = requests.post(
            "https://id.sophos.com/api/v2/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": cid,
                  "client_secret": csec, "scope": "token"},
            timeout=30
        )
        if r.status_code != 200:
            return {"tenants": [], "error": f"auth HTTP {r.status_code}"}
        token = r.json().get("access_token")

        whoami = requests.get("https://api.central.sophos.com/whoami/v1",
                              headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if whoami.status_code != 200:
            return {"tenants": [], "error": f"whoami HTTP {whoami.status_code}"}
        info       = whoami.json()
        partner_id = info.get("id")
        global_url = info.get("apiHosts", {}).get("global", "https://api.central.sophos.com")

        tenants, page = [], 1
        while True:
            rt = requests.get(
                f"{global_url}/partner/v1/tenants?pageTotal=true&page={page}&pageSize=100",
                headers={"Authorization": f"Bearer {token}", "X-Partner-ID": partner_id},
                timeout=30
            )
            if rt.status_code != 200:
                break
            d = rt.json()
            for t in d.get("items", []):
                name    = t.get("name", "")
                show_as = t.get("showAs", "") or name
                # 'showAs' contient souvent le code Artis en préfixe alors que 'name' non
                tenants.append({
                    "id":          t.get("id"),
                    "name":        name,
                    "showAs":      show_as,
                    "status":      t.get("status", ""),
                    "dataRegion":  t.get("dataRegion", ""),
                    "apiUrl":      t.get("apiHost", "") or t.get("apiHosts", {}).get("dataRegion", ""),
                    "artis_code":  extract_artis_code(show_as) or extract_artis_code(name),
                })
            if page >= d.get("pages", {}).get("total", 1):
                break
            page += 1

        # Compteur d'endpoints via l'API billing/usage (une seule requête pour tous les tenants).
        # Format de période : "YYYY/MM". On prend le mois précédent (les billings sont
        # cloturés un peu après la fin du mois).
        from datetime import datetime as _dt
        now = _dt.now()
        m_prev = now.month - 1 or 12
        y_prev = now.year if now.month > 1 else now.year - 1
        periods = [f"{y_prev}/{m_prev:02d}"]
        # Fallback : mois encore avant
        m2 = m_prev - 1 or 12
        y2 = y_prev if m_prev > 1 else y_prev - 1
        periods.append(f"{y2}/{m2:02d}")

        # Index tenants par nom (lower) pour mapper les billing items → artis_code
        tenant_by_name = {(t.get("name") or "").lower(): t for t in tenants}

        billing_by_code = {}        # qty totale (endpoints) par code Artis
        billing_amount_by_code = {} # montant total (qty × msrp) par code Artis
        billing_items_by_code = {}  # détail par code Artis : [{product, qty, msrp, total}, ...]
        used_period = None
        for period in periods:
            try:
                rb = requests.get(
                    f"{global_url}/partner/v1/billing/usage/{period}?pageSize=1000",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Partner-ID":  partner_id,
                        "Accept":        "application/json",
                    },
                    timeout=30
                )
                if rb.status_code != 200:
                    continue
                items = rb.json().get("items", [])
                if not items:
                    continue
                for it in items:
                    identifier = (it.get("assignedAccountIdentifier") or "").strip()
                    if not identifier:
                        continue
                    # 1) Récupère le code via le tenant correspondant (nom = identifier)
                    code = None
                    t_match = tenant_by_name.get(identifier.lower())
                    if t_match:
                        code = t_match.get("artis_code")
                    # 2) Fallback : code extrait directement de l'identifier
                    if not code:
                        code = extract_artis_code(identifier)
                    if not code:
                        continue
                    qty = float(it.get("billableQuantity") or 0)
                    # Sophos peut renvoyer unitMsrp ou unitMSRP selon la version d'API
                    msrp = float(it.get("unitMsrp") or it.get("unitMSRP") or 0)
                    line_total = qty * msrp
                    billing_by_code[code] = billing_by_code.get(code, 0) + qty
                    billing_amount_by_code[code] = billing_amount_by_code.get(code, 0) + line_total
                    billing_items_by_code.setdefault(code, []).append({
                        "product": it.get("productName") or it.get("product") or "",
                        "sku":     it.get("sku") or "",
                        "qty":     qty,
                        "msrp":    msrp,
                        "total":   round(line_total, 4),
                    })
                if billing_by_code:
                    used_period = period
                    break
            except Exception:
                continue

        # Reporter les compteurs sur les tenants par artis_code
        for t in tenants:
            ac = t.get("artis_code")
            t["endpoints"]        = int(billing_by_code.get(ac, 0)) if ac else 0
            t["monthly_billing"]  = round(billing_amount_by_code.get(ac, 0), 4) if ac else 0
            t["billing_items"]    = billing_items_by_code.get(ac, []) if ac else []

        return {"tenants": tenants, "billing_period": used_period}
    except Exception as e:
        return {"tenants": [], "error": str(e)}


# ─── SOPHOS TAMPER PROTECTION ───────────────────────────────────────────────────

SOPHOS_TAMPER_HISTORY_FILE = DATA_DIR / "sophos_tamper_history.json"


def _sophos_partner_token():
    """Auth Partner — réutilise le client_credentials. Retourne (token, partner_id, global_url)."""
    cid  = cfg("SOPHOS_CLIENT_ID")
    csec = cfg("SOPHOS_CLIENT_SECRET")
    if not cid or not csec:
        return None, None, None, "credentials manquantes"
    r = requests.post(
        "https://id.sophos.com/api/v2/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": cid,
              "client_secret": csec, "scope": "token"},
        timeout=30
    )
    if r.status_code != 200:
        return None, None, None, f"auth HTTP {r.status_code}"
    token = r.json().get("access_token")
    whoami = requests.get("https://api.central.sophos.com/whoami/v1",
                          headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if whoami.status_code != 200:
        return token, None, None, f"whoami HTTP {whoami.status_code}"
    info = whoami.json()
    partner_id = info.get("id")
    global_url = info.get("apiHosts", {}).get("global", "https://api.central.sophos.com")
    return token, partner_id, global_url, None


def _sophos_all_tenants(token, partner_id, global_url):
    """Renvoie la liste complète des tenants (pagination automatique)."""
    out = []
    page = 1
    while True:
        rt = requests.get(
            f"{global_url}/partner/v1/tenants?pageTotal=true&page={page}&pageSize=100",
            headers={"Authorization": f"Bearer {token}", "X-Partner-ID": partner_id},
            timeout=30
        )
        if rt.status_code != 200: return out
        d = rt.json()
        for t in d.get("items", []):
            out.append(_sophos_tenant_obj(t))
        if page >= d.get("pages", {}).get("total", 1): break
        page += 1
    return out


def _sophos_resolve_tenant(token, partner_id, global_url, tenant_query):
    """
    Résout une saisie utilisateur (code Artis OU nom OU id) en {id, name, showAs, apiHost, dataRegion}.
    Renvoie None si introuvable. Cherche d'abord par id exact, puis par code Artis extrait du showAs/name.
    """
    for t in _sophos_all_tenants(token, partner_id, global_url):
        if tenant_query == t["id"]: return t
        code = extract_artis_code(t["showAs"]) or extract_artis_code(t["name"])
        if code and normalize_code(code) == normalize_code(tenant_query):
            return t
    return None


def _sophos_find_endpoint_owner(endpoint_id, tenants, token, max_workers=25):
    """
    Cherche en parallèle quel tenant possède l'endpoint_id donné.
    Retourne (tenant, endpoint_data) ou (None, None).
    Utilise GET /endpoint/v1/endpoints/{id} sur chaque tenant — celui qui répond 200 gagne.
    """
    from concurrent.futures import ThreadPoolExecutor
    targets = [t for t in tenants if t.get("apiHost")]
    found = {}
    def _probe(t):
        if found: return  # short-circuit léger
        try:
            r = requests.get(
                f"{t['apiHost']}/endpoint/v1/endpoints/{endpoint_id}",
                headers={"Authorization": f"Bearer {token}",
                         "X-Tenant-ID": t["id"], "Accept": "application/json"},
                timeout=10
            )
            if r.status_code == 200:
                d = r.json() or {}
                if d.get("id") == endpoint_id:
                    found["tenant"] = t
                    found["endpoint"] = d
        except Exception:
            pass
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_probe, targets))
    return found.get("tenant"), found.get("endpoint")


def _sophos_tenant_obj(t):
    return {
        "id":         t.get("id"),
        "name":       t.get("name", ""),
        "showAs":     t.get("showAs", "") or t.get("name", ""),
        "status":     t.get("status", ""),
        "dataRegion": t.get("dataRegion", ""),
        "apiHost":    t.get("apiHost", "") or t.get("apiHosts", {}).get("dataRegion", ""),
    }


def _sophos_count_endpoints(tenant, token):
    """
    Récupère le nombre total d'endpoints d'un tenant via 1 appel léger
    (pageSize=1&pageTotal=true → pages.items contient le total).
    Retourne (count, error_or_None). count = -1 si erreur.
    """
    if not tenant.get("apiHost"):
        return -1, "no apiHost"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   tenant["id"],
        "Accept":        "application/json",
    }
    try:
        r = requests.get(
            f"{tenant['apiHost']}/endpoint/v1/endpoints",
            headers=headers,
            params={"pageSize": 1, "pageTotal": "true"},
            timeout=20
        )
        if r.status_code != 200:
            return -1, f"HTTP {r.status_code}"
        d = r.json() or {}
        total = (d.get("pages") or {}).get("items")
        return int(total) if total is not None else -1, None
    except Exception as e:
        return -1, str(e)


def sophos_enrich_endpoint_counts(tenants, token, max_workers=15):
    """
    Pour chaque tenant ayant un apiHost, fetch le nombre total d'endpoints (parallèle).
    Modifie tenants in-place : ajoute tenant['endpoints_total'].
    Renvoie (nb_ok, nb_err).
    """
    from concurrent.futures import ThreadPoolExecutor
    targets = [t for t in tenants if t.get("apiHost")]
    nb_ok = 0; nb_err = 0
    def _job(t):
        c, err = _sophos_count_endpoints(t, token)
        if err is None and c >= 0:
            t["endpoints_total"] = c
            return True
        t["endpoints_total"] = None
        return False
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for ok in ex.map(_job, targets):
            if ok: nb_ok += 1
            else:  nb_err += 1
    return nb_ok, nb_err


def _sophos_list_endpoints(tenant, token):
    """
    Liste tous les endpoints d'un tenant via GET /endpoint/v1/endpoints (paginé).
    Retourne (list_of_endpoints, error_or_None).
    Chaque endpoint: {id, hostname, type, os, tamperProtectionEnabled, lastSeenAt, ...}
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   tenant["id"],
        "Accept":        "application/json",
    }
    base = f"{tenant['apiHost']}/endpoint/v1/endpoints"
    items = []
    next_key = None
    page = 0
    while True:
        params = {"pageSize": 500}
        if next_key: params["pageFromKey"] = next_key
        try:
            r = requests.get(base, headers=headers, params=params, timeout=60)
        except Exception as e:
            return items, f"GET endpoints exception: {e}"
        if r.status_code != 200:
            return items, f"GET endpoints HTTP {r.status_code}: {(r.text or '')[:300]}"
        d = r.json() or {}
        for e in d.get("items", []) or []:
            items.append({
                "id":        e.get("id"),
                "hostname":  e.get("hostname"),
                "type":      e.get("type"),
                "os":        ((e.get("os") or {}).get("platform") or "")
                              + (" " + ((e.get("os") or {}).get("name") or ""))
                              if (e.get("os") or {}) else "",
                "tamperEnabled":   e.get("tamperProtectionEnabled"),
                "lastSeenAt":      e.get("lastSeenAt"),
            })
        next_key = (d.get("pages") or {}).get("nextKey")
        if not next_key: break
        page += 1
        if page > 50: break  # safety: 50 pages × 500 = 25000 endpoints max
    return items, None


def _sophos_endpoint_tamper(tenant, token, endpoint_id, method, body=None):
    """Appelle GET ou POST /endpoint/v1/endpoints/{id}/tamper-protection. Retourne (status, json|text)."""
    url = f"{tenant['apiHost']}/endpoint/v1/endpoints/{endpoint_id}/tamper-protection"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   tenant["id"],
        "Accept":        "application/json",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        r = requests.request(method, url, headers=headers,
                             data=json.dumps(body) if body is not None else None,
                             timeout=30)
        try: payload = r.json()
        except Exception: payload = (r.text or "")[:300]
        return r.status_code, payload
    except Exception as e:
        return None, str(e)


def _sophos_persist_tamper(entry):
    try:
        existing = []
        if SOPHOS_TAMPER_HISTORY_FILE.exists():
            with open(SOPHOS_TAMPER_HISTORY_FILE, encoding="utf-8") as f:
                existing = json.load(f) or []
        existing.append(entry)
        if len(existing) > 5000:
            existing = existing[-5000:]
        with open(SOPHOS_TAMPER_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[SOPHOS TAMPER] persistance échouée: {e}")


def sophos_tamper_inspect(tenant_query, with_passwords=True):
    """
    Read-only : résout le tenant + liste ses endpoints avec leur état tamper et password.
    Retourne {ok, tenant, endpoints[], summary{total, enabled, disabled}}
    Si with_passwords=True : fetch parallèle du password de chaque endpoint (×10 workers).
    """
    token, partner_id, global_url, err = _sophos_partner_token()
    if err: return {"ok": False, "error": err}
    tenant = _sophos_resolve_tenant(token, partner_id, global_url, tenant_query)
    if not tenant:
        return {"ok": False, "error": f"tenant introuvable pour '{tenant_query}'"}
    if not tenant.get("apiHost"):
        return {"ok": False, "error": "tenant sans apiHost (probablement résilié)"}
    endpoints, ep_err = _sophos_list_endpoints(tenant, token)
    if ep_err and not endpoints:
        return {"ok": False, "tenant": tenant, "error": ep_err}

    if with_passwords and endpoints:
        from concurrent.futures import ThreadPoolExecutor
        def _try_get_pwd(ep):
            s, p = _sophos_endpoint_tamper(tenant, token, ep["id"], "GET")
            if s == 200 and isinstance(p, dict):
                ep["password"] = p.get("password")
                if p.get("enabled") is not None:
                    ep["tamperEnabled"] = p.get("enabled")
                ep.pop("password_error", None)
                return True
            # 4xx définitif (sauf 429) → erreur définitive
            if isinstance(s, int) and 400 <= s < 500 and s != 429:
                ep["password_error"] = f"HTTP {s}"
                return False
            ep["password_error"] = f"HTTP {s}"
            return False

        # Pass 1 : parallèle 4 workers (limite la pression sur l'API)
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(_try_get_pwd, endpoints))

        # Pass 2 : retry séquentiel pour TOUS ceux sans password (même ceux en erreur transient)
        # avec petit délai pour laisser passer un éventuel rate-limit
        missing = [e for e in endpoints if not e.get("password")
                   and not (e.get("password_error", "").startswith("HTTP 4")
                            and "HTTP 429" not in e.get("password_error", ""))]
        if missing:
            time.sleep(1.5)
            for ep in missing:
                if _try_get_pwd(ep):
                    continue
                time.sleep(0.4)
                _try_get_pwd(ep)  # 3e essai après délai supplémentaire

    enabled  = sum(1 for e in endpoints if e.get("tamperEnabled") is True)
    disabled = sum(1 for e in endpoints if e.get("tamperEnabled") is False)
    return {
        "ok":        True,
        "tenant":    tenant,
        "endpoints": endpoints,
        "summary":   {"total": len(endpoints), "enabled": enabled, "disabled": disabled},
        "warning":   ep_err,
    }


def _sophos_tamper_apply(tenant_query, enabled_target, dry_run, user):
    """
    Itère tous les endpoints du tenant et POST /endpoint/v1/endpoints/{id}/tamper-protection
    avec {enabled: "true"|"false"}. Retourne {ok, tenant, results[], summary, dry_run}.
    Persiste UNIQUEMENT les opérations OK (1 ligne par endpoint) dans sophos_tamper_history.json.
    """
    token, partner_id, global_url, err = _sophos_partner_token()
    if err: return {"ok": False, "error": err}
    tenant = _sophos_resolve_tenant(token, partner_id, global_url, tenant_query)
    if not tenant:
        return {"ok": False, "error": f"tenant introuvable pour '{tenant_query}'"}
    if not tenant.get("apiHost"):
        return {"ok": False, "error": "tenant sans apiHost (probablement résilié)"}

    endpoints, ep_err = _sophos_list_endpoints(tenant, token)
    if not endpoints:
        return {"ok": False, "tenant": tenant,
                "error": ep_err or "aucun endpoint listé pour ce tenant"}

    target_str = "true" if enabled_target else "false"
    action_lbl = "enable" if enabled_target else "disable"

    # On ne touche que les endpoints qui ne sont pas déjà dans l'état cible
    to_change = [e for e in endpoints if e.get("tamperEnabled") is not enabled_target]

    if dry_run:
        return {
            "ok": True, "dry_run": True, "tenant": tenant,
            "summary": {
                "total":     len(endpoints),
                "to_change": len(to_change),
                "skipped":   len(endpoints) - len(to_change),
            },
            "endpoints_preview": [
                {"id": e["id"], "hostname": e.get("hostname"), "type": e.get("type"),
                 "tamperEnabled": e.get("tamperEnabled"), "will_change": e in to_change}
                for e in endpoints
            ],
            "preview_call": f"POST {tenant['apiHost']}/endpoint/v1/endpoints/<id>/tamper-protection {{\"enabled\":\"{target_str}\"}}",
            "_message": "Dry-run — rien envoyé."
        }

    results = []
    nb_ok = 0
    nb_ko = 0
    nb_skip = 0
    for ep in endpoints:
        if ep.get("tamperEnabled") is enabled_target:
            nb_skip += 1
            results.append({
                "endpoint_id":      ep["id"],
                "hostname":         ep.get("hostname"),
                "type":             ep.get("type"),
                "os":               ep.get("os"),
                "enabled_before":   ep.get("tamperEnabled"),
                "enabled_after":    ep.get("tamperEnabled"),
                "skipped":          True,
                "reason":           "déjà dans l'état cible",
                "ok":               True,
            })
            continue

        # Pour disable : capturer le password via GET avant le POST
        # (le POST ne renvoie pas le password dans sa réponse)
        password = None
        if not enabled_target:  # disable
            sg, pg = _sophos_endpoint_tamper(tenant, token, ep["id"], "GET")
            if sg == 200 and isinstance(pg, dict):
                password = pg.get("password")

        s1, p1 = _sophos_endpoint_tamper(tenant, token, ep["id"], "POST", {"enabled": target_str})
        # Sophos renvoie 200 OU 201 (Created) pour un succès
        if s1 in (200, 201) and isinstance(p1, dict):
            # Si le POST a quand même renvoyé un password (cas où l'API change), prendre celui-là
            if p1.get("password"): password = p1.get("password")
            # Fallback : si toujours pas de password après POST, re-tenter un GET
            if not password and not enabled_target:
                sg2, pg2 = _sophos_endpoint_tamper(tenant, token, ep["id"], "GET")
                if sg2 == 200 and isinstance(pg2, dict):
                    password = pg2.get("password")
            enabled_after = p1.get("enabled")
            res = {
                "endpoint_id":    ep["id"],
                "hostname":       ep.get("hostname"),
                "type":           ep.get("type"),
                "os":             ep.get("os"),
                "enabled_before": ep.get("tamperEnabled"),
                "enabled_after":  enabled_after,
                "password":       password,
                "ok":             True,
            }
            # Persistance : 1 ligne par machine, uniquement les DISABLE réussis
            # (les enable ne sont pas tracés en historique)
            if action_lbl == "disable":
                entry = {
                    "ts":            datetime.now().isoformat(timespec="seconds"),
                    "user":          user,
                    "tenant_id":     tenant["id"],
                    "tenant_name":   tenant["showAs"],
                    "data_region":   tenant.get("dataRegion", ""),
                    "endpoint_id":   ep["id"],
                    "hostname":      ep.get("hostname"),
                    "type":          ep.get("type"),
                    "os":            ep.get("os"),
                    "action":        action_lbl,
                    "enabled_before": ep.get("tamperEnabled"),
                    "enabled_after": enabled_after,
                    "password":      password,
                }
                _sophos_persist_tamper(entry)
            nb_ok += 1
            results.append(res)
        else:
            nb_ko += 1
            results.append({
                "endpoint_id":    ep["id"],
                "hostname":       ep.get("hostname"),
                "type":           ep.get("type"),
                "os":             ep.get("os"),
                "enabled_before": ep.get("tamperEnabled"),
                "ok":             False,
                "error":          f"HTTP {s1}: {p1}",
            })

    log(f"[SOPHOS TAMPER {tenant['id']}] {action_lbl} by {user} — {nb_ok} OK / {nb_ko} KO / {nb_skip} skip sur {len(endpoints)} endpoints")
    return {
        "ok":      nb_ok > 0 or (nb_ko == 0 and nb_skip > 0),
        "tenant":  tenant,
        "summary": {"total": len(endpoints), "ok": nb_ok, "ko": nb_ko, "skipped": nb_skip},
        "results": results,
    }


def sophos_tamper_disable(tenant_query, dry_run=True, user="?"):
    return _sophos_tamper_apply(tenant_query, False, dry_run, user)


def sophos_tamper_enable(tenant_query, dry_run=True, user="?"):
    return _sophos_tamper_apply(tenant_query, True, dry_run, user)


# ─── SOPHOS CUSTOMER LOOKUP ─────────────────────────────────────────────────────

def sophos_customer_lookup(query):
    """
    Lookup complet d'un client Sophos à partir de :
      - tenant ID (UUID) ou code Artis ou nom
      - OU endpoint ID (UUID) → recherche brute-force du tenant propriétaire (parallèle ~30s)
    Retourne {ok, tenant, endpoints_total, artis_client, found_via_endpoint?, endpoint?}.
    """
    token, partner_id, global_url, err = _sophos_partner_token()
    if err: return {"ok": False, "error": err}

    tenant = _sophos_resolve_tenant(token, partner_id, global_url, query)
    found_endpoint = None

    # Si pas trouvé comme tenant ET que ça ressemble à un UUID, essayer comme endpoint ID
    if not tenant:
        import re as _re
        if _re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", query):
            log(f"[CUSTOMER LOOKUP] {query} pas un tenant ID — recherche endpoint ID brute-force...")
            tenants = _sophos_all_tenants(token, partner_id, global_url)
            tenant, found_endpoint = _sophos_find_endpoint_owner(query, tenants, token)
            if not tenant:
                return {"ok": False, "error": f"ni tenant ni endpoint trouvé pour '{query}'"}
        else:
            return {"ok": False, "error": f"tenant introuvable pour '{query}'"}

    # Compte total d'endpoints (live)
    count, count_err = -1, None
    if tenant.get("apiHost"):
        count, count_err = _sophos_count_endpoints(tenant, token)

    # Cross-ref avec msp_data.json (Artis client + autres services + billing items capturés au 20h)
    artis_client = None
    billing_items = []
    monthly_billing = 0
    sophos_billed = 0
    try:
        if DATA_FILE.exists():
            with open(DATA_FILE, encoding="utf-8") as f:
                d = json.load(f)
            for r in d.get("reconciliation", []):
                s = r.get("sophos") or {}
                if not s: continue
                # match par tenant_id (préféré) ou par artis_code
                if s.get("tenant_id") == tenant["id"] or s.get("artis_code") == r.get("code"):
                    if s.get("tenant_id") == tenant["id"]:
                        artis_client = {
                            "code":        r.get("code"),
                            "nom":         r.get("nom"),
                            "org":         r.get("org"),
                            "nb_contrats": r.get("nb_contrats"),
                            "datto":       r.get("datto"),
                            "veeam":       r.get("veeam"),
                            "veeam365":    r.get("veeam365"),
                            "vade":        r.get("vade"),
                            "m365":        r.get("m365"),
                            "sentinelone": r.get("sentinelone"),
                            "artis_billed": r.get("artis"),
                        }
                        sophos_billed = s.get("endpoints", 0)
                        break
    except Exception:
        pass

    # Fetch billing items live depuis sophos_get_data (cher si on relance tout — alternative : depuis msp_data.json si stocké)
    # Pour l'instant on n'inclut pas les billing items live (trop lourd, peut être ajouté plus tard)

    return {
        "ok":              True,
        "tenant":          tenant,
        "endpoints_total": count if count >= 0 else None,
        "endpoints_total_error": count_err,
        "sophos_billed":   sophos_billed,
        "artis_client":    artis_client,
        "found_via_endpoint": found_endpoint is not None,
        "endpoint":        found_endpoint,
    }


# ─── SOPHOS DELETE ENDPOINTS ────────────────────────────────────────────────────

SOPHOS_DELETE_HISTORY_FILE = DATA_DIR / "sophos_delete_history.json"


def _sophos_persist_delete(entry):
    try:
        existing = []
        if SOPHOS_DELETE_HISTORY_FILE.exists():
            with open(SOPHOS_DELETE_HISTORY_FILE, encoding="utf-8") as f:
                existing = json.load(f) or []
        existing.append(entry)
        if len(existing) > 10000:
            existing = existing[-10000:]
        with open(SOPHOS_DELETE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"[SOPHOS DELETE] persistance échouée: {e}")


def sophos_endpoints_lookup(tenant_query):
    """
    Lookup tenant + liste de ses endpoints (sans password — pour vue suppression).
    Plus rapide que sophos_tamper_inspect (pas de GET tamper-protection par endpoint).
    """
    token, partner_id, global_url, err = _sophos_partner_token()
    if err: return {"ok": False, "error": err}
    tenant = _sophos_resolve_tenant(token, partner_id, global_url, tenant_query)
    if not tenant:
        return {"ok": False, "error": f"tenant introuvable pour '{tenant_query}'"}
    if not tenant.get("apiHost"):
        return {"ok": False, "error": "tenant sans apiHost (probablement résilié)"}
    endpoints, ep_err = _sophos_list_endpoints(tenant, token)
    if ep_err and not endpoints:
        return {"ok": False, "tenant": tenant, "error": ep_err}
    return {
        "ok":        True,
        "tenant":    tenant,
        "endpoints": endpoints,
        "summary":   {"total": len(endpoints)},
    }


def sophos_endpoints_delete(tenant_query, endpoint_ids, dry_run=True, user="?"):
    """
    DELETE /endpoint/v1/endpoints/{id} sur chaque ID. Persiste 1 entrée par succès.
    Retourne {ok, tenant, results[], summary}.

    SAFETY : exige le code tenant correspondant ; on resolve le tenant + on vérifie
    que chaque endpoint_id existe bien sur ce tenant avant de le supprimer.
    """
    if not isinstance(endpoint_ids, list) or not endpoint_ids:
        return {"ok": False, "error": "endpoint_ids manquant"}

    token, partner_id, global_url, err = _sophos_partner_token()
    if err: return {"ok": False, "error": err}
    tenant = _sophos_resolve_tenant(token, partner_id, global_url, tenant_query)
    if not tenant:
        return {"ok": False, "error": f"tenant introuvable pour '{tenant_query}'"}
    if not tenant.get("apiHost"):
        return {"ok": False, "error": "tenant sans apiHost"}

    # Récupère la liste live pour valider que chaque endpoint_id appartient bien à ce tenant
    endpoints, _ = _sophos_list_endpoints(tenant, token)
    by_id = {e["id"]: e for e in endpoints if e.get("id")}
    invalid = [eid for eid in endpoint_ids if eid not in by_id]
    if invalid:
        return {"ok": False, "tenant": tenant,
                "error": f"endpoint_id(s) inconnu(s) pour ce tenant: {invalid[:5]}{'...' if len(invalid)>5 else ''}"}

    if dry_run:
        return {
            "ok": True, "dry_run": True, "tenant": tenant,
            "summary": {"to_delete": len(endpoint_ids), "total": len(endpoints)},
            "endpoints_preview": [{
                "id":         eid,
                "hostname":   by_id[eid].get("hostname"),
                "type":       by_id[eid].get("type"),
                "os":         by_id[eid].get("os"),
                "lastSeenAt": by_id[eid].get("lastSeenAt"),
            } for eid in endpoint_ids],
            "preview_call": f"DELETE {tenant['apiHost']}/endpoint/v1/endpoints/<id>",
            "_message": "Dry-run — rien envoyé."
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   tenant["id"],
        "Accept":        "application/json",
    }
    base = f"{tenant['apiHost']}/endpoint/v1/endpoints"
    results = []
    nb_ok = 0; nb_ko = 0
    for eid in endpoint_ids:
        ep = by_id[eid]
        try:
            r = requests.delete(f"{base}/{eid}", headers=headers, timeout=30)
            ok = r.status_code in (200, 202, 204)
            if ok:
                nb_ok += 1
                # Snapshot de l'endpoint AVANT suppression dans l'historique
                entry = {
                    "ts":           datetime.now().isoformat(timespec="seconds"),
                    "user":         user,
                    "tenant_id":    tenant["id"],
                    "tenant_name":  tenant["showAs"],
                    "data_region":  tenant.get("dataRegion", ""),
                    "endpoint_id":  eid,
                    "hostname":     ep.get("hostname"),
                    "type":         ep.get("type"),
                    "os":           ep.get("os"),
                    "lastSeenAt":   ep.get("lastSeenAt"),
                    "tamper_state": ep.get("tamperEnabled"),
                    "http_status":  r.status_code,
                }
                _sophos_persist_delete(entry)
                results.append({"endpoint_id": eid, "hostname": ep.get("hostname"),
                                "os": ep.get("os"), "type": ep.get("type"),
                                "ok": True, "status": r.status_code})
            else:
                nb_ko += 1
                results.append({"endpoint_id": eid, "hostname": ep.get("hostname"),
                                "os": ep.get("os"), "type": ep.get("type"),
                                "ok": False,
                                "error": f"HTTP {r.status_code}: {(r.text or '')[:200]}"})
        except Exception as e:
            nb_ko += 1
            results.append({"endpoint_id": eid, "hostname": ep.get("hostname"),
                            "os": ep.get("os"), "type": ep.get("type"),
                            "ok": False, "error": f"exception: {e}"})

    log(f"[SOPHOS DELETE {tenant['id']}] {user} → {nb_ok} OK / {nb_ko} KO sur {len(endpoint_ids)}")
    return {
        "ok":      nb_ok > 0,
        "tenant":  tenant,
        "summary": {"requested": len(endpoint_ids), "ok": nb_ok, "ko": nb_ko},
        "results": results,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VADE
# ═══════════════════════════════════════════════════════════════════════════════

def vade_get_data():
    cid  = cfg("VADE_CLIENT_ID")
    csec = cfg("VADE_CLIENT_SECRET")
    if not cid or not csec:
        return {"customers": [], "error": "credentials manquantes"}
    try:
        r = requests.post(
            "https://api.vadesecure.com/oauth2/v2/token",
            data={"grant_type": "client_credentials", "client_id": cid,
                  "scope": "partner.reseller", "client_secret": csec},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30
        )
        r.raise_for_status()
        token = r.json().get("access_token")

        rc = requests.get("https://api.vadesecure.com/customer/v2/customers",
                          headers={"Authorization": f"Bearer {token}"}, timeout=60)
        rc.raise_for_status()
        d   = rc.json()
        raw = d if isinstance(d, list) else d.get("customers", d.get("data", []))

        customers = []
        for c in raw:
            lics   = c.get("licenses", [])
            # state 2 = active, others = inactive
            active = [l for l in lics if l.get("state") == 2]
            ends   = [l["endDate"] for l in active if l.get("endDate")]
            user_count = sum(int(l.get("userCount") or 0) for l in active)
            brand = c.get("brand", "")
            cp_ref = c.get("cpRef", "").strip()
            customers.append({
                "id":              c.get("id", ""),
                "domain":          c.get("domain", ""),
                "brand":           brand,
                "cpRef":           cp_ref,
                # Artis code is embedded in 'brand' field (e.g. "0011 - EMMANUELLE DE")
                "artis_code":      extract_artis_code(brand) or (normalize_code(cp_ref) if cp_ref else None),
                "mail":            c.get("mail", ""),
                "licenses_active": len(active),
                "licenses_total":  len(lics),
                "user_count":      user_count,
                "end_date_ts":     min(ends) if ends else 0,
            })
        return {"customers": customers}
    except Exception as e:
        return {"customers": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# SENTINELONE
# ═══════════════════════════════════════════════════════════════════════════════

def s1_get_data():
    base  = cfg("S1_URL")
    token = cfg("S1_TOKEN")
    if not base or not token:
        return {"sites": [], "error": "credentials manquantes"}
    try:
        headers = {"Authorization": f"ApiToken {token}", "Accept": "application/json"}
        sites, cursor = [], None
        while True:
            url = f"{base}/web/api/v2.1/sites?limit=200"
            if cursor:
                url += f"&cursor={cursor}"
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 401:
                return {"sites": [], "error": "token expire — mettre a jour S1_TOKEN dans .env"}
            if r.status_code != 200:
                return {"sites": [], "error": f"HTTP {r.status_code}"}
            d = r.json()
            for s in d.get("data", {}).get("sites", []):
                name = s.get("name", "")
                sites.append({
                    "id":             s.get("id"),
                    "name":           name,
                    "accountName":    s.get("accountName"),
                    "activeLicenses": s.get("activeLicenses", 0),
                    "state":          s.get("state", ""),
                    "artis_code":     extract_artis_code(name),
                })
            cursor = d.get("pagination", {}).get("nextCursor")
            if not cursor:
                break
        return {"sites": sites}
    except Exception as e:
        return {"sites": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# MICROSOFT 365 (via TDSynnex / ION)
# ═══════════════════════════════════════════════════════════════════════════════

TDSYNNEX_TOKEN_FILE = DATA_DIR / "tdsynnex_token.txt"
TDSYNNEX_AUTH_URL   = "https://ion.tdsynnex.com/oauth/token"
TDSYNNEX_API_URL    = "https://ion.tdsynnex.com/api/v3/accounts/26582/subscriptions"

def _tdsynnex_load_refresh():
    if TDSYNNEX_TOKEN_FILE.exists():
        return TDSYNNEX_TOKEN_FILE.read_text(encoding="utf-8").strip()
    # Fallback : .env
    return cfg("TDSYNNEX_REFRESH_TOKEN", "").strip()

def _tdsynnex_save_refresh(token):
    """Le refresh token est rotation : on l'écrit après chaque auth réussie."""
    TDSYNNEX_TOKEN_FILE.write_text(token + "\n", encoding="utf-8")

def m365_get_data():
    refresh = _tdsynnex_load_refresh()
    if not refresh:
        return {"subscriptions": [], "error": "TDSynnex refresh token manquant (tdsynnex_token.txt)"}
    try:
        # Étape 1 : access token via refresh token (qui est tourné par TDSynnex)
        ra = requests.post(
            TDSYNNEX_AUTH_URL,
            headers={"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            data=f"grant_type=refresh_token&refresh_token={refresh}",
            timeout=30
        )
        if ra.status_code != 200:
            return {"subscriptions": [], "error": f"auth TDSynnex HTTP {ra.status_code} (refresh token expiré ?)"}
        j = ra.json()
        access_token = j.get("access_token", "")
        new_refresh  = j.get("refresh_token", "")
        if new_refresh:
            _tdsynnex_save_refresh(new_refresh)
        if not access_token:
            return {"subscriptions": [], "error": "access_token manquant dans la réponse"}

        # Étape 2 : pagination subscriptions
        headers = {"Authorization": f"Bearer {access_token}", "accept": "application/json"}
        all_items = []
        offset, limit = 0, 1000
        for _ in range(100):
            url = f"{TDSYNNEX_API_URL}?pagination.limit={limit}&pagination.offset={offset}"
            r = requests.get(url, headers=headers, timeout=60)
            if r.status_code != 200:
                break
            d = r.json()
            items = d.get("items") or d.get("data") or (d if isinstance(d, list) else [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < limit:
                break
            offset += len(items)

        # Étape 3 : filtrage + agrégation par client (artis_code extrait de customerName)
        subs = []
        for it in all_items:
            status = (it.get("subscriptionStatus") or "").lower()
            end    = str(it.get("subscriptionEndDate") or "")[:10]
            start  = str(it.get("subscriptionStartDate") or "")[:10]
            if status not in ("active", "working", "paused"):
                continue
            if end == "9999-12-31":
                continue
            cust_name = it.get("customerName", "") or ""
            try:
                lic = int(it.get("subscriptionTotalLicenses") or 0)
            except (TypeError, ValueError):
                lic = 0
            try:
                cust_cost = float(it.get("customerCost") or 0)
            except (TypeError, ValueError):
                cust_cost = 0.0
            try:
                seller_cost = float(it.get("cost") or 0)
            except (TypeError, ValueError):
                seller_cost = 0.0
            subs.append({
                "customer_id":     it.get("customerId", ""),
                "customer_name":   cust_name,
                "artis_code":      extract_artis_code(cust_name),
                "subscription":    it.get("subscriptionName", ""),
                "licenses":        lic,
                "status":          it.get("subscriptionStatus", ""),
                "start_date":      start,
                "end_date":        end,
                "seller_cost":     seller_cost,    # = it.cost  (cumul/stock, peut être total durée)
                "customer_cost":   cust_cost,      # = it.customerCost (vrai mensuel client)
                "billing_cycle":   it.get("subscriptionBillingCycle", "") or "",
                "billing_term":    it.get("subscriptionBillingTerm", "") or "",
            })
        return {"subscriptions": subs}
    except Exception as e:
        return {"subscriptions": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# DATTO
# ═══════════════════════════════════════════════════════════════════════════════

def datto_get_data():
    base   = cfg("DATTO_URL")
    apikey = cfg("DATTO_API_KEY")
    apisec = cfg("DATTO_API_SECRET")
    if not base or not apikey or not apisec:
        return {"sites": [], "error": "credentials manquantes"}
    try:
        r_auth = requests.post(
            f"{base}/auth/oauth/token",
            auth=("public-client", "public"),
            data={"grant_type": "password", "username": apikey, "password": apisec},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30
        )
        r_auth.raise_for_status()
        access_token = r_auth.json().get("access_token", "")
        if not access_token:
            return {"sites": [], "error": "token DATTO vide"}

        headers = {"Authorization": f"Bearer {access_token}"}
        cutoff  = time.time() - 90 * 86400   # 90 jours
        sites   = {}
        url     = f"{base}/api/v2/account/devices"
        while url:
            r = requests.get(url, headers=headers, timeout=60)
            if r.status_code != 200:
                break
            d = r.json()
            for dev in d.get("devices", []):
                cat  = (dev.get("deviceType") or {}).get("category", "")
                ls   = dev.get("lastSeen", 0)
                ls_ts = (ls / 1000) if isinstance(ls, (int, float)) and ls > 0 else 0
                if cat not in ("Server", "Desktop", "Laptop") or ls_ts < cutoff:
                    continue
                site_name = dev.get("siteName", "")
                if site_name not in sites:
                    sites[site_name] = {
                        "name":       site_name,
                        "artis_code": extract_artis_code(site_name),
                        "servers":    0,
                        "desktops":   0,
                        "laptops":    0,
                        "devices":    [],
                    }
                if cat == "Server":
                    sites[site_name]["servers"] += 1
                elif cat == "Desktop":
                    sites[site_name]["desktops"] += 1
                else:
                    sites[site_name]["laptops"] += 1
                sites[site_name]["devices"].append({
                    "hostname":    dev.get("hostname", "") or dev.get("description", ""),
                    "category":    cat,
                    "os":          dev.get("operatingSystem", ""),
                    "user":        dev.get("lastLoggedInUser", ""),
                    "lastSeen":    ls_ts,
                    "ip":          dev.get("intIpAddress") or dev.get("extIpAddress", ""),
                    "domain":      dev.get("domain", ""),
                    "warrantyEnd": dev.get("warrantyDate", ""),
                })
            url = d.get("pageDetails", {}).get("nextPageUrl") or None

        for s in sites.values():
            s["total"] = s["servers"] + s["desktops"] + s["laptops"]
        return {"sites": list(sites.values())}
    except Exception as e:
        return {"sites": [], "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-REFERENCE
# ═══════════════════════════════════════════════════════════════════════════════

MAPPING_FILE = DATA_DIR / "mapping.json"

def load_mapping():
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def fuzzy_find(name, index, threshold=0.72):
    best_key, best_score = None, 0
    name_l = name.lower()
    for k in index:
        s = SequenceMatcher(None, name_l, k).ratio()
        if s > best_score:
            best_score, best_key = s, k
    return index.get(best_key) if best_score >= threshold else None

def cross_reference(artis_clients, billed, veeam, sophos, vade, s1, datto, m365=None, affaires=None, chefs_projet=None):
    mapping = load_mapping()
    affaires = affaires or {}
    chefs_projet = chefs_projet or {}

    # Build code-based indexes (primary) and name-based indexes (fallback)
    # VEEAM companies
    veeam_by_code = {}
    veeam_by_name = {}
    for c in veeam.get("companies", []):
        if c.get("artis_code"):
            veeam_by_code[c["artis_code"]] = c
        veeam_by_name[c["name"].lower()] = c
    usage_map = veeam.get("usage", {})

    # VEEAM365
    v365_by_code = {}
    v365_by_name = {}
    for o in veeam.get("veeam365", []):
        if o.get("artis_code"):
            v365_by_code[o["artis_code"]] = o
        v365_by_name[o["name"].lower()] = o

    # Sophos tenants
    soph_by_code = {}
    soph_by_name = {}
    for t in sophos.get("tenants", []):
        if t.get("artis_code"):
            soph_by_code[t["artis_code"]] = t
        soph_by_name[t["name"].lower()] = t

    # SentinelOne — group by artis_code
    s1_by_code = {}
    s1_by_name = {}
    for site in s1.get("sites", []):
        if site.get("artis_code"):
            s1_by_code.setdefault(site["artis_code"], []).append(site)
        s1_by_name.setdefault(site["name"].lower(), []).append(site)

    # DATTO
    datto_by_code = {}
    datto_by_name = {}
    for s in datto.get("sites", []):
        if s.get("artis_code"):
            datto_by_code[s["artis_code"]] = s
        datto_by_name[s["name"].lower()] = s

    # VADE — cpRef IS the normalized Artis code
    vade_by_code = {}
    for c in vade.get("customers", []):
        if c.get("artis_code"):
            vade_by_code[c["artis_code"]] = c

    # M365 — agrégation par artis_code (somme des licences toutes souscriptions confondues)
    m365_by_code = {}
    for s in (m365 or {}).get("subscriptions", []):
        ac = s.get("artis_code")
        if not ac:
            continue
        e = m365_by_code.setdefault(ac, {"licenses": 0, "subs": []})
        e["licenses"] += int(s.get("licenses") or 0)
        e["subs"].append(s.get("subscription"))

    result = []
    for client in artis_clients:
        code  = client["code"]        # normalized
        nom   = client["libelle"]
        nom_l = nom.lower()
        m     = mapping.get(code, {})

        def resolve_code_or_fuzzy(by_code, by_name, override=None):
            """Try code match first, then name fuzzy."""
            # Code match
            mc = by_code.get(code)
            if mc:
                return mc
            # Manual mapping override
            if override and override in m:
                return by_name.get(m[override].lower()) or fuzzy_find(m[override], by_name)
            # Fuzzy name
            return fuzzy_find(nom_l, by_name)

        def resolve_code_or_fuzzy_multi(by_code, by_name, override=None):
            """Like above but for multi-value (SentinelOne)."""
            mc = by_code.get(code)
            if mc:
                return mc
            if override and override in m:
                k = m[override].lower()
                return by_name.get(k) or fuzzy_find(k, by_name)
            return fuzzy_find(nom_l, by_name)

        # ── VEEAM ──
        vc = resolve_code_or_fuzzy(veeam_by_code, veeam_by_name, "veeam")
        veeam_status = None
        if vc:
            uid = vc["uid"]
            tb  = usage_map.get(uid, 0)
            veeam_status = {"name": vc["name"], "usedTB": tb, "artis_code": vc.get("artis_code")}

        # ── VEEAM 365 ──
        v365 = resolve_code_or_fuzzy(v365_by_code, v365_by_name, "veeam365")
        v365_status = {"name": v365["name"], "type": v365.get("type", ""), "artis_code": v365.get("artis_code")} if v365 else None

        # ── Sophos ──
        sc = resolve_code_or_fuzzy(soph_by_code, soph_by_name, "sophos")
        sophos_status = None
        if sc:
            sophos_status = {
                "name":            sc["name"],
                "status":          sc.get("status", ""),
                "endpoints":       sc.get("endpoints", 0),
                "endpoints_total": sc.get("endpoints_total"),
                "tenant_id":       sc.get("id"),
                "artis_code":      sc.get("artis_code"),
            }

        # ── SentinelOne ──
        s1_match = resolve_code_or_fuzzy_multi(s1_by_code, s1_by_name, "s1")
        s1_status = None
        if s1_match:
            total_lic = sum(s.get("activeLicenses", 0) for s in s1_match)
            s1_status = {
                "account":        s1_match[0]["accountName"],
                "sites":          len(s1_match),
                "activeLicenses": total_lic,
                "artis_code":     s1_match[0].get("artis_code"),
            }

        # ── DATTO ──
        dc = resolve_code_or_fuzzy(datto_by_code, datto_by_name, "datto")
        datto_status = None
        if dc:
            datto_status = {
                "name":       dc["name"],
                "total":      dc["total"],
                "servers":    dc["servers"],
                "desktops":   dc.get("desktops", 0),
                "laptops":    dc.get("laptops", 0),
                "artis_code": dc.get("artis_code"),
            }

        # ── VADE — cpRef = normalized Artis code ──
        vc2 = vade_by_code.get(code)
        if not vc2:
            vade_domain = m.get("vade_domain", "")
            if vade_domain:
                for cv in vade.get("customers", []):
                    if cv.get("domain", "").lower() == vade_domain.lower():
                        vc2 = cv
                        break
        vade_status = None
        if vc2:
            vade_status = {
                "domain":          vc2.get("domain", ""),
                "brand":           vc2.get("brand", ""),
                "cpRef":           vc2.get("cpRef", ""),
                "licenses_active": vc2["licenses_active"],
                "user_count":      vc2.get("user_count", 0),
                "artis_code":      vc2.get("artis_code"),
            }

        # ── M365 (TDSynnex) ──
        m365_e = m365_by_code.get(code)
        m365_status = None
        if m365_e:
            m365_status = {
                "licenses":      m365_e["licenses"],
                "subscriptions": len(m365_e["subs"]),
                "artis_code":    code,
            }

        # ── Artis billed quantities (from RFC details) ──
        b = billed.get(code, {})

        # ── Chef de projet : UO INFO-PROJ d'abord, fallback gestionnaire d'affaire ──
        aff_info = affaires.get(code) or {}
        chef_uo  = chefs_projet.get(code)
        if chef_uo:
            chef_projet = {
                "nom":      chef_uo["nom"],
                "prenom":   chef_uo["prenom"],
                "matricule": chef_uo["matricule"],
                "email":    chef_uo["email"],
                "source":   "uo",
                "total_hours":      chef_uo["total_hours"],
                "last_date":        chef_uo["last_date"],
                "nb_interventions": chef_uo["nb_interventions"],
            }
        elif aff_info.get("gestionnaire"):
            g = aff_info["gestionnaire"]
            chef_projet = {**g, "source": "affaire"}
        else:
            chef_projet = None

        result.append({
            "code":        code,
            "nom":         nom,
            "org":         client.get("org", ""),
            "nb_contrats": len(client.get("contrats", [])),
            # Vendor real data
            "veeam":       veeam_status,
            "veeam365":    v365_status,
            "sophos":      sophos_status,
            "sentinelone": s1_status,
            "datto":       datto_status,
            "vade":        vade_status,
            "m365":        m365_status,
            # Chef de projet (UO INFO-PROJ ou affaire fallback) + commercial (vendeur affaire)
            "chef_projet": chef_projet,
            "vendeur":     aff_info.get("vendeur"),
            "affaire": {
                "num":   aff_info.get("num_affaire", ""),
                "date":  aff_info.get("date_affaire", ""),
                "total": aff_info.get("nb_affaires", 0),
            } if aff_info else None,
            # Artis billed quantities
            "artis": {
                "datto":     b.get("datto", 0),
                "m365":      b.get("m365", 0),
                "sophos":    b.get("sophos", 0),
                "veeam_tb":  b.get("veeam_tb", 0),
                "veeam365":  b.get("veeam365", 0),
                "vade":      b.get("vade", 0),
                "soc":       b.get("soc", 0),
            },
        })

    return result


def expand_per_contrat(reconciliation, artis_clients, rfc_lines_by_contrat=None):
    rfc_lines_by_contrat = rfc_lines_by_contrat or {}
    contrats_by_client = {cl["code"]: cl.get("contrats", []) for cl in artis_clients}
    out = []
    for c in reconciliation:
        contrats = contrats_by_client.get(c["code"], [])
        if not contrats:
            entry = dict(c)
            entry["contrat_code"] = ""
            entry["contrat_statut"] = ""
            entry["rfc_lines"] = []
            out.append(entry)
            continue
        for ct in contrats:
            entry = dict(c)
            ct_code = ct.get("code", "")
            entry["contrat_code"] = ct_code
            entry["contrat_statut"] = ct.get("statut", "")
            entry["rfc_lines"] = rfc_lines_by_contrat.get(ct_code, [])
            out.append(entry)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# MISE A JOUR PRINCIPALE
# ═══════════════════════════════════════════════════════════════════════════════

def mise_a_jour(with_rfc=True, rfc_source="api"):
    """
    with_rfc   : True = peuple les billed quantities, False = conserve l'existant
    rfc_source : "api" = scan complet RFC API Artis (~10-15 min) — seule source supportée
                 (le mode "excel" a été retiré le 2026-05-07 sur demande user)
    """
    _maj_state["in_progress"] = True
    _maj_state["started_at"]  = datetime.now().isoformat()
    _maj_state["ended_at"]    = None
    _maj_state["phase"]       = "Démarrage"
    log("═══ Début mise à jour MSP ═══")
    now     = datetime.now()
    payload = {
        "updated_at":         now.isoformat(),
        "updated_at_display": now.strftime("%d/%m/%Y à %Hh%M"),
        "sources":            {},
        "reconciliation":     [],
        "stats":              {},
    }

    # Artis clients
    artis_clients = []
    artis_full_mapping = {}
    billed        = {}
    rfc_lines_by_contrat = {}
    artis_dit = []
    artis_np  = []
    artis_affaires = {}
    chefs_projet = {}
    try:
        _phase("Artis — clients")
        token         = artis_get_token()
        artis_clients, artis_full_mapping = artis_get_clients(token)
        payload["sources"]["artis"] = {"count": len(artis_clients), "status": "ok",
                                        "full_mapping_count": len(artis_full_mapping)}
        log(f"  › Artis OK — {len(artis_clients)} clients CSI + {len(artis_full_mapping)} dans mapping SSC complet")

        # DIT (Demandes d'Intervention Technique) — 90 derniers jours
        _phase("Artis — DIT (90j)")
        dit_res = artis_get_dit(token, days=90)
        if dit_res.get("error"):
            log(f"  ⚠ DIT: {dit_res['error']}")
            payload["sources"]["dit"] = {"status": "error", "error": dit_res["error"]}
        else:
            # Cross-ref : on garde uniquement les DIT des clients IT (présents dans artis_clients)
            it_codes = {c.get("code") for c in artis_clients if c.get("code")}
            artis_dit = [d for d in dit_res["dit"] if d.get("client_code") in it_codes]
            log(f"  › DIT OK — {len(artis_dit)}/{len(dit_res['dit'])} DIT clients IT")
            payload["sources"]["dit"] = {"status": "ok", "count": len(artis_dit), "total_received": len(dit_res["dit"])}

        # TNP (Temps Non Productif) — 90 derniers jours
        _phase("Artis — TNP (90j)")
        np_res = artis_get_non_productif(token, days=90)
        if np_res.get("error"):
            log(f"  ⚠ TNP: {np_res['error']}")
            payload["sources"]["non_productif"] = {"status": "error", "error": np_res["error"]}
            artis_np = []
        else:
            artis_np = np_res["np"]
            log(f"  › TNP OK — {len(artis_np)} entrées")
            payload["sources"]["non_productif"] = {"status": "ok", "count": len(artis_np)}

        # Affaires (vendeur = commercial, gestionnaire = fallback chef projet)
        _phase("Artis — affaires")
        artis_affaires = artis_get_affaires(token, artis_clients)
        payload["sources"]["affaires"] = {"status": "ok", "count": len(artis_affaires)}

        # Chef de projet "réel" via UO INFO-PROJ sur 12 mois
        _phase("Artis — chef de projet")
        chefs_projet = artis_get_chefs_projet(token, lookback_days=365)
        payload["sources"]["chef_projet"] = {"status": "ok", "count": len(chefs_projet)}

        if with_rfc:
            _phase("RFC Artis (long, ~10 min)")
            billed, rfc_lines_by_contrat = artis_get_rfc_details(token, artis_clients)
            with_billing = sum(1 for v in billed.values() if any(v.values()))
            log(f"  › RFC OK — {with_billing} clients avec facturation")
        else:
            # Conserver les billed et rfc_lines du précédent msp_data.json
            if DATA_FILE.exists():
                try:
                    with open(DATA_FILE, encoding="utf-8") as f:
                        prev = json.load(f)
                    for r in prev.get("reconciliation", []):
                        a = r.get("artis", {})
                        if any(a.values()):
                            billed[r["code"]] = {k: a.get(k, 0) for k in
                                ("datto","m365","sophos","veeam_tb","veeam365","vade","soc")}
                    for r in prev.get("reconciliation_par_contrat", []):
                        ct = r.get("contrat_code")
                        if ct and r.get("rfc_lines"):
                            rfc_lines_by_contrat[ct] = r["rfc_lines"]
                    log(f"  › RFC skip — billed conservé pour {len(billed)} clients, rfc_lines pour {len(rfc_lines_by_contrat)} contrats")
                except Exception as e:
                    log(f"  ⚠ RFC skip — impossible de relire le précédent: {e}")
            else:
                log("  › RFC skip — pas de données antérieures")
    except Exception as e:
        log(f"  ✗ Artis ERREUR: {e}")
        payload["sources"]["artis"] = {"count": 0, "status": "error", "error": str(e)}

    # Sources externes en parallèle
    _phase("Sources externes (VEEAM, Sophos, VADE, S1, Datto, M365)")
    fetchers = {
        "veeam":       veeam_get_data,
        "sophos":      sophos_get_data,
        "vade":        vade_get_data,
        "sentinelone": s1_get_data,
        "datto":       datto_get_data,
        "m365":        m365_get_data,
    }
    results = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {name: ex.submit(fn) for name, fn in fetchers.items()}
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=180)
                err = results[name].get("error")
                if err:
                    log(f"  ⚠ {name}: {err}")
                    payload["sources"][name] = {"status": "error", "error": err}
                else:
                    counts = {k: len(v) for k, v in results[name].items() if isinstance(v, list)}
                    log(f"  › {name} OK — {counts}")
                    payload["sources"][name] = {"status": "ok", **counts}
            except Exception as e:
                results[name] = {}
                payload["sources"][name] = {"status": "error", "error": str(e)}
                log(f"  ✗ {name} ERREUR: {e}")

    # Enrichissement Sophos : compte total d'endpoints par tenant (parallèle, ~30s pour 800 tenants)
    sophos_res = results.get("sophos", {}) or {}
    sophos_tenants = sophos_res.get("tenants") or []
    if sophos_tenants:
        try:
            tok_partner, _, _, err_p = _sophos_partner_token()
            if not err_p and tok_partner:
                t0 = time.time()
                ok_n, err_n = sophos_enrich_endpoint_counts(sophos_tenants, tok_partner, max_workers=15)
                log(f"  › Sophos endpoint counts : {ok_n} OK / {err_n} KO en {time.time()-t0:.1f}s")
        except Exception as e:
            log(f"  ⚠ Sophos endpoint counts : {e}")

    # Cross-reference
    if artis_clients:
        _phase("Cross-référencement")
        recon = cross_reference(
            artis_clients,
            billed,
            results.get("veeam",       {}),
            results.get("sophos",      {}),
            results.get("vade",        {}),
            results.get("sentinelone", {}),
            results.get("datto",       {}),
            results.get("m365",        {}),
            affaires=artis_affaires,
            chefs_projet=chefs_projet,
        )
        payload["reconciliation"] = recon
        payload["reconciliation_par_contrat"] = expand_per_contrat(recon, artis_clients, rfc_lines_by_contrat)
        payload["artis_full_mapping"] = artis_full_mapping
        # Sources brutes pour la refacturation (mapping SSC sur tous les clients facturables)
        payload["raw_sources"] = {
            "datto":       results.get("datto",       {}).get("sites", []),
            "m365":        results.get("m365",        {}).get("subscriptions", []),
            "sophos":      results.get("sophos",      {}).get("tenants", []),
            "veeam":       results.get("veeam",       {}).get("companies", []),
            "veeam365":    results.get("veeam",       {}).get("veeam365", []),
            "veeam_usage": results.get("veeam",       {}).get("usage", {}),
            "veeam_points": results.get("veeam",      {}).get("points", {}),
            "vade":        results.get("vade",        {}).get("customers", []),
            "sentinelone": results.get("sentinelone", {}).get("sites", []),
            "dit":         artis_dit,
            "non_productif": artis_np,
        }
        total = len(recon)
        payload["stats"] = {
            "artis_total":    total,
            "veeam_matched":  sum(1 for r in recon if r["veeam"]),
            "v365_matched":   sum(1 for r in recon if r["veeam365"]),
            "sophos_matched": sum(1 for r in recon if r["sophos"]),
            "s1_matched":     sum(1 for r in recon if r["sentinelone"]),
            "datto_matched":  sum(1 for r in recon if r["datto"]),
            "vade_matched":   sum(1 for r in recon if r["vade"]),
            "m365_matched":   sum(1 for r in recon if r.get("m365")),
            "with_billing":   sum(1 for r in recon if any(r["artis"].values())),
            "anomalies":      sum(1 for r in recon if not any([r["veeam"], r["sophos"], r["sentinelone"], r["datto"]])),
        }
        log(f"  › Stats: {payload['stats']}")

    # Cas particuliers (notes manuelles datées par client)
    payload["cas_particuliers"] = load_cas_particuliers()

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log(f"  › Sauvegardé dans {DATA_FILE}")

    try:
        import refac
        refac.generer_refac()
    except Exception as e:
        log(f"  ⚠ Génération refac échouée : {e}")

    _maj_state["in_progress"] = False
    _maj_state["phase"]       = ""
    _maj_state["ended_at"]    = datetime.now().isoformat()
    log("═══ Mise à jour terminée ═══\n")


# ═══════════════════════════════════════════════════════════════════════════════
# SERVEUR HTTP SECURISE
# ═══════════════════════════════════════════════════════════════════════════════

BLOCKED_PATHS = {".env", "mapping.json", "updater.py", "artis_update.log", "tdsynnex_token.txt", "nonfact_history.json", "cloture_history.json", "sophos_tamper_history.json", "sophos_delete_history.json", "cas_particuliers.json"}
BLOCKED_EXT   = {".py", ".log", ".bat", ".env", ".xlsm", ".txt"}

# Fichiers de données servis dynamiquement depuis DATA_DIR (frontend les fetch).
DATA_FILES_SERVED = {"msp_data.json", "refac_data.json", "artis_data.json"}

SNAPSHOTS_DIR = DATA_DIR / "snapshots"
SNAPSHOT_KINDS = {"dashboard": "msp_data.json", "refac": "refac_data.json"}
SNAPSHOT_ID_RE = re.compile(r"^[0-9TZ_-]+$")

# Auth Entra ID (Azure Container Apps Easy Auth) — rôle = role value de l'app registration
ROLE_USER  = "msp_dashboard_user"
ROLE_ADMIN = "msp_dashboard_admin"

def _decode_easy_auth_principal(raw_b64):
    """Décode le header X-MS-CLIENT-PRINCIPAL (Base64(JSON claims)) injecté par Easy Auth."""
    if not raw_b64:
        return None
    try:
        import base64
        payload = json.loads(base64.b64decode(raw_b64))
        claims = payload.get("claims") or []
        roles  = [c["val"] for c in claims if c.get("typ") == "roles"]
        name   = next((c["val"] for c in claims if c.get("typ") == "name"), None)
        upn    = next((c["val"] for c in claims if c.get("typ") == "preferred_username"), None)
        return {"name": name, "upn": upn, "roles": roles}
    except Exception:
        return None

def _fake_dev_principal():
    """Principal admin de dev local (uniquement si DEV_MODE=1). Ne JAMAIS l'utiliser en prod."""
    return {"name": "Dev Admin", "upn": "dev-admin@local", "roles": [ROLE_ADMIN, ROLE_USER]}

def _snapshot_dir(kind):
    p = SNAPSHOTS_DIR / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Cas particuliers (notes datées par client) ─────────────────────────────────
CAS_PARTICULIERS_FILE = DATA_DIR / "cas_particuliers.json"
_CAS_LOCK = threading.Lock()

def load_cas_particuliers():
    """Renvoie {client_code: [{date, auteur, texte}, ...]} (entrées récentes en premier)."""
    if not CAS_PARTICULIERS_FILE.exists():
        return {}
    try:
        with open(CAS_PARTICULIERS_FILE, encoding="utf-8") as f:
            d = json.load(f)
        # filtre les clés _comment / _* et garde uniquement les listes
        return {k: v for k, v in d.items() if not k.startswith("_") and isinstance(v, list)}
    except Exception:
        return {}

def save_cas_particuliers(data):
    """Écrit le fichier en conservant le _comment éventuel. Atomique via .tmp + rename."""
    full = {"_comment": "Notes datées par client. Modifiable via le dashboard. Format: { client_code: [ {date, auteur, texte}, ... ] }"}
    if CAS_PARTICULIERS_FILE.exists():
        try:
            with open(CAS_PARTICULIERS_FILE, encoding="utf-8") as f:
                existing = json.load(f)
            for k, v in existing.items():
                if k.startswith("_"):
                    full[k] = v
        except Exception:
            pass
    full.update(data)
    tmp = CAS_PARTICULIERS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    tmp.replace(CAS_PARTICULIERS_FILE)

def append_cas_particulier(code, texte, auteur):
    """Append une note pour un client. Renvoie la note ajoutée."""
    code = (code or "").strip()
    texte = (texte or "").strip()
    if not code or not texte:
        raise ValueError("code et texte obligatoires")
    note = {
        "date":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "auteur": auteur or "?",
        "texte":  texte,
    }
    with _CAS_LOCK:
        d = load_cas_particuliers()
        d.setdefault(code, []).insert(0, note)  # plus récent en premier
        save_cas_particuliers(d)
    return note

def delete_cas_particulier(code, idx):
    """Supprime la note à l'index idx pour le client code. Renvoie la note supprimée."""
    code = (code or "").strip()
    if not code:
        raise ValueError("code obligatoire")
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        raise ValueError("index invalide")
    with _CAS_LOCK:
        d = load_cas_particuliers()
        lst = d.get(code) or []
        if idx < 0 or idx >= len(lst):
            raise IndexError("index hors limites")
        removed = lst.pop(idx)
        if not lst:
            d.pop(code, None)  # nettoie la clé si plus de notes
        save_cas_particuliers(d)
    return removed


def _list_snapshots(kind):
    p = _snapshot_dir(kind)
    items = []
    for f in sorted(p.glob("*.json"), reverse=True):
        try:
            st = f.stat()
            items.append({"id": f.stem, "size": st.st_size, "mtime": st.st_mtime})
        except Exception:
            continue
    return items

class SecureHandler(SimpleHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _principal(self):
        """Identité Entra extraite des headers Easy Auth. Fallback en DEV_MODE."""
        p = _decode_easy_auth_principal(self.headers.get("X-MS-CLIENT-PRINCIPAL"))
        if p:
            # Compléter avec les headers individuels (plus directs)
            p["upn"]  = p["upn"]  or (self.headers.get("X-MS-CLIENT-PRINCIPAL-NAME") or "").strip()
            p["oid"]  = (self.headers.get("X-MS-CLIENT-PRINCIPAL-ID") or "").strip()
            return p
        if DEV_MODE:
            return _fake_dev_principal()
        return None

    def _user(self):
        """Compat ascendante : renvoie l'identifiant principal (UPN ou name) — utilisé dans les logs."""
        p = self._principal() or {}
        return (p.get("upn") or p.get("name") or "").strip()

    def _is_admin(self):
        p = self._principal() or {}
        return ROLE_ADMIN in (p.get("roles") or [])

    def _api_dispatch(self, method, path):
        # /api/whoami                              GET → current user + role
        # /api/snapshots/<kind>                    GET → list
        # /api/snapshot/<kind>                     POST → create from current live data
        # /api/snapshot/<kind>/<id>                DELETE → remove (admin only)
        # /api/it/<numero>/cloturer                PATCH → saisir compte rendu IT (admin, dry-run par défaut via ?dry=1)
        parts = path.strip("/").split("/")
        # /api/maj/status                          GET → état du cycle MAJ (pour barre de progression UI)
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "maj" and parts[2] == "status" and method == "GET":
            return self._send_json(200, _maj_state)
        if len(parts) == 2 and parts[0] == "api" and parts[1] == "whoami" and method == "GET":
            p = self._principal() or {}
            roles = p.get("roles") or []
            is_admin = ROLE_ADMIN in roles
            return self._send_json(200, {
                "user":     self._user(),         # compat ascendante (UPN ou name)
                "upn":      p.get("upn", ""),
                "name":     p.get("name", ""),
                "oid":      p.get("oid", ""),
                "roles":    roles,
                "role":     "admin" if is_admin else "user",
                "is_admin": is_admin,
                "dev_mode": DEV_MODE,
            })
        # /api/_debug/whoami : dump headers Easy Auth pour debug staging Azure.
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "_debug" and parts[2] == "whoami" and method == "GET":
            return self._send_json(200, {
                "headers_easy_auth": {
                    "X-MS-CLIENT-PRINCIPAL":      bool(self.headers.get("X-MS-CLIENT-PRINCIPAL")),
                    "X-MS-CLIENT-PRINCIPAL-NAME": self.headers.get("X-MS-CLIENT-PRINCIPAL-NAME"),
                    "X-MS-CLIENT-PRINCIPAL-ID":   self.headers.get("X-MS-CLIENT-PRINCIPAL-ID"),
                    "X-MS-CLIENT-PRINCIPAL-IDP":  self.headers.get("X-MS-CLIENT-PRINCIPAL-IDP"),
                },
                "principal_decoded": self._principal(),
                "dev_mode": DEV_MODE,
            })
        # /api/cas-particulier              GET → toute la map (lecture fraîche, pas en cache)
        # /api/cas-particulier/<code>       GET → notes du client
        # /api/cas-particulier/<code>       POST → ajoute une note (body {texte}, auteur = X-User)
        if len(parts) >= 2 and parts[0] == "api" and parts[1] == "cas-particulier":
            if method == "GET" and len(parts) == 2:
                return self._send_json(200, load_cas_particuliers())
            if method == "GET" and len(parts) == 3:
                code = parts[2].strip()
                return self._send_json(200, {"code": code, "notes": load_cas_particuliers().get(code, [])})
            if method == "POST" and len(parts) == 3:
                code = parts[2].strip()
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw or b"{}")
                except Exception as e:
                    return self._send_json(400, {"error": f"body invalide: {e}"})
                texte = (body.get("texte") or "").strip()
                if not texte:
                    return self._send_json(400, {"error": "champ 'texte' obligatoire"})
                if len(texte) > 2000:
                    return self._send_json(400, {"error": "texte trop long (max 2000 caractères)"})
                try:
                    note = append_cas_particulier(code, texte, self._user())
                except Exception as e:
                    return self._send_json(500, {"error": str(e)})
                log(f"[CAS PARTIC {code}] user={self._user()} → {texte[:60]}")
                return self._send_json(200, {"ok": True, "note": note})
            # /api/cas-particulier/<code>/<idx>  DELETE → supprime la note
            if method == "DELETE" and len(parts) == 4:
                code = parts[2].strip()
                try:
                    removed = delete_cas_particulier(code, parts[3])
                except (IndexError, ValueError) as e:
                    return self._send_json(400, {"error": str(e)})
                except Exception as e:
                    return self._send_json(500, {"error": str(e)})
                log(f"[CAS PARTIC DEL {code}#{parts[3]}] user={self._user()} → {removed.get('texte','')[:60]}")
                return self._send_json(200, {"ok": True, "removed": removed})
            return self._send_json(404, {"error": "not found"})
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "snapshots" and method == "GET":
            kind = parts[2]
            if kind not in SNAPSHOT_KINDS:
                return self._send_json(404, {"error": "unknown kind"})
            return self._send_json(200, {"snapshots": _list_snapshots(kind)})
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "snapshot" and method == "POST":
            kind = parts[2]
            if kind not in SNAPSHOT_KINDS:
                return self._send_json(404, {"error": "unknown kind"})
            src = DATA_DIR / SNAPSHOT_KINDS[kind]
            if not src.exists():
                return self._send_json(409, {"error": f"{src.name} introuvable"})
            sid = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            dst = _snapshot_dir(kind) / f"{sid}.json"
            try:
                dst.write_bytes(src.read_bytes())
                return self._send_json(200, {"ok": True, "id": sid, "size": dst.stat().st_size})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "snapshot" and method == "DELETE":
            # NB: pas de gating admin pour l'instant — ajouter `if not self._is_admin(): return self._send_json(403, ...)` quand on voudra restreindre
            kind, sid = parts[2], parts[3]
            if kind not in SNAPSHOT_KINDS or not SNAPSHOT_ID_RE.match(sid):
                return self._send_json(400, {"error": "bad request"})
            f = _snapshot_dir(kind) / f"{sid}.json"
            if not f.exists():
                return self._send_json(404, {"error": "not found"})
            try:
                f.unlink()
                return self._send_json(200, {"ok": True})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/it/<numero>/cloturer  PATCH (admin only)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "it" and parts[3] == "cloturer" and method == "PATCH":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            it_num = parts[2]
            if not it_num.isdigit():
                return self._send_json(400, {"error": "numero IT invalide"})
            qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            dry = qs.get("dry", ["1"])[0] != "0"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw or b"{}")
            except Exception as e:
                return self._send_json(400, {"error": f"body invalide: {e}"})
            for required in ("dateDebut", "commentaireClient", "lignes"):
                if required not in body:
                    return self._send_json(400, {"error": f"champ obligatoire manquant: {required}"})
            if not isinstance(body["lignes"], list) or len(body["lignes"]) < 1:
                body["lignes"] = [{}]
            res = artis_patch_saisir_crit(it_num, body, dry_run=dry)
            log(f"[CLOTURE IT {it_num}] user={self._user()} dry={dry} → {res.get('status') or res.get('ok')}")
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/dit/<numero>/non-facturable  PATCH (admin only) — passe une DIT en NonFacturable
        # Query : ?dry=1 (défaut) | ?dry=0
        # Body optionnel : {intervenant, pause_s, max_retries, commentaire}
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "dit" and parts[3] == "non-facturable" and method == "PATCH":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            num_dit = parts[2]
            if not num_dit.isdigit():
                return self._send_json(400, {"error": "numero DIT invalide"})
            qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            dry = qs.get("dry", ["1"])[0] != "0"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw or b"{}")
            except Exception as e:
                return self._send_json(400, {"error": f"body invalide: {e}"})
            try:
                pause_s = int(body.get("pause_s", 5))
                max_retries = int(body.get("max_retries", 3))
            except Exception:
                pause_s, max_retries = 5, 3
            # intervenant par défaut = None (skip l'étape qui cause des timeouts)
            # Pour le forcer : passer body.intervenant = "ELCOHEN" explicitement
            intervenant = body.get("intervenant") or None
            commentaire = body.get("commentaire") or None
            res = artis_passer_dit_non_facturable(
                num_dit, dry_run=dry,
                pause_s=pause_s, max_retries=max_retries,
                intervenant_matricule=intervenant,
                commentaire=commentaire,
            )
            log(f"[NF DIT {num_dit}] user={self._user()} dry={dry} → ok={res.get('ok')} new_it={res.get('new_it')}")
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/dit/<numero>/cloturer-dit  PATCH (admin only) — clôture une DIT TravailTermine/TravailNonTermine
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "dit" and parts[3] == "cloturer-dit" and method == "PATCH":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            num = parts[2]
            if not num.isdigit():
                return self._send_json(400, {"error": "numero DIT invalide"})
            qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            dry = qs.get("dry", ["1"])[0] != "0"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw or b"{}")
            except Exception as e:
                return self._send_json(400, {"error": f"body invalide: {e}"})
            commentaire = body.get("commentaire") or None
            force_finalize = bool(body.get("force_finalize", False))
            res = artis_cloturer_dit(num, commentaire=commentaire, dry_run=dry,
                                      force_finalize=force_finalize)
            log(f"[CLOT DIT {num}] user={self._user()} dry={dry} force={force_finalize} → ok={res.get('ok')}")
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/sophos/customer/<query>  GET (admin) — lookup complet d'un tenant (tenant + artis + counts)
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "customer" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            q = unquote(parts[3]).strip()
            if not q: return self._send_json(400, {"error": "query vide"})
            res = sophos_customer_lookup(q)
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/sophos/endpoints/lookup/<query>  GET (admin) — liste endpoints d'un tenant pour suppression
        if len(parts) == 5 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "endpoints" and parts[3] == "lookup" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            q = unquote(parts[4]).strip()
            if not q: return self._send_json(400, {"error": "query vide"})
            res = sophos_endpoints_lookup(q)
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/sophos/endpoints/delete  POST (admin) — body {tenant, endpoint_ids: [...], confirm_code}
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "endpoints" and parts[3] == "delete" and method == "POST":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception as e:
                return self._send_json(400, {"error": f"body invalide: {e}"})
            q = (body.get("tenant") or "").strip()
            ids = body.get("endpoint_ids") or []
            confirm = (body.get("confirm_code") or "").strip()
            if not q or not ids:
                return self._send_json(400, {"error": "tenant + endpoint_ids requis"})
            qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            dry = qs.get("dry", ["1"])[0] != "0"
            # Anti-erreur : exiger que confirm_code corresponde au tenant (code Artis OU id Sophos OU query)
            if not dry:
                # Résolution pour comparer aussi à showAs/code Artis/id
                _tok, _pid, _gurl, _err = _sophos_partner_token()
                _tenant = _sophos_resolve_tenant(_tok, _pid, _gurl, q) if (not _err) else None
                accepted = {q}
                if _tenant:
                    accepted.add(_tenant.get("id") or "")
                    import re as _re
                    _m = _re.match(r"^(\d+)\b", _tenant.get("showAs", "") or "")
                    if _m: accepted.add(_m.group(1))
                if confirm not in accepted:
                    return self._send_json(400, {
                        "error": "confirm_code ne correspond pas au tenant (attendu : " + " ou ".join(sorted(accepted)) + ")"
                    })
            res = sophos_endpoints_delete(q, ids, dry_run=dry, user=self._user())
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/sophos/delete/history  GET (admin) — historique persistant des suppressions
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "delete" and parts[3] == "history" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            try:
                items = []
                if SOPHOS_DELETE_HISTORY_FILE.exists():
                    with open(SOPHOS_DELETE_HISTORY_FILE, encoding="utf-8") as f:
                        items = json.load(f) or []
                items_sorted = sorted(items, key=lambda e: e.get("ts", ""), reverse=True)
                return self._send_json(200, {"ok": True, "count": len(items_sorted), "items": items_sorted})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/sophos/tenants  GET (admin) — liste des tenants Sophos (autocomplete)
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "tenants" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            try:
                tenants = []
                if DATA_FILE.exists():
                    with open(DATA_FILE, encoding="utf-8") as f:
                        d = json.load(f)
                    for r in d.get("reconciliation", []):
                        s = r.get("sophos") or {}
                        if not s: continue
                        tenants.append({
                            "code":            r.get("code", ""),
                            "client":          r.get("nom", ""),
                            "name":            s.get("name", ""),
                            "endpoints":       s.get("endpoints", 0),
                            "endpoints_total": s.get("endpoints_total"),
                            "status":          s.get("status", ""),
                        })
                tenants.sort(key=lambda t: t.get("client", "").lower())
                return self._send_json(200, {"ok": True, "count": len(tenants), "items": tenants})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/sophos/tamper/inspect/<query>  GET (admin) — état tamper d'un tenant
        if len(parts) == 5 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "tamper" and parts[3] == "inspect" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            q = unquote(parts[4]).strip()
            if not q:
                return self._send_json(400, {"error": "query vide"})
            res = sophos_tamper_inspect(q)
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/sophos/tamper/disable  POST (admin) — body {tenant: <code|id|name>, dry?: bool}
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "tamper" and parts[3] == "disable" and method == "POST":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception as e:
                return self._send_json(400, {"error": f"body invalide: {e}"})
            q = (body.get("tenant") or "").strip()
            if not q: return self._send_json(400, {"error": "tenant manquant"})
            qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            dry = qs.get("dry", ["1"])[0] != "0"
            res = sophos_tamper_disable(q, dry_run=dry, user=self._user())
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/sophos/tamper/enable  POST (admin) — réactive
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "tamper" and parts[3] == "enable" and method == "POST":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception as e:
                return self._send_json(400, {"error": f"body invalide: {e}"})
            q = (body.get("tenant") or "").strip()
            if not q: return self._send_json(400, {"error": "tenant manquant"})
            qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            dry = qs.get("dry", ["1"])[0] != "0"
            res = sophos_tamper_enable(q, dry_run=dry, user=self._user())
            return self._send_json(200 if res.get("ok") else 502, res)
        # /api/sophos/tamper/history  GET (admin) — historique persistant
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "sophos" \
           and parts[2] == "tamper" and parts[3] == "history" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            try:
                items = []
                if SOPHOS_TAMPER_HISTORY_FILE.exists():
                    with open(SOPHOS_TAMPER_HISTORY_FILE, encoding="utf-8") as f:
                        items = json.load(f) or []
                items_sorted = sorted(items, key=lambda e: e.get("ts", ""), reverse=True)
                return self._send_json(200, {"ok": True, "count": len(items_sorted), "items": items_sorted})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/dit/cloturer/history  GET (admin only) — historique des clôtures DIT
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "dit" \
           and parts[2] == "cloturer" and parts[3] == "history" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            history_file = DATA_DIR / "cloture_history.json"
            try:
                if history_file.exists():
                    with open(history_file, encoding="utf-8") as f:
                        items = json.load(f) or []
                else:
                    items = []
                items_sorted = sorted(items, key=lambda e: e.get("ts", ""), reverse=True)
                return self._send_json(200, {"ok": True, "count": len(items_sorted), "items": items_sorted})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/dit/<numero>/inspect  GET (admin only) — détail live d'une DIT pour pré-visualisation
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "dit" and parts[3] == "inspect" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            num = parts[2]
            if not num.isdigit():
                return self._send_json(400, {"error": "numero DIT invalide"})
            try:
                token = artis_get_token()
                if not token:
                    return self._send_json(502, {"error": "token Artis indisponible"})
                base = cfg("ARTIS_BASE_URL").replace("http://", "https://", 1)
                r = requests.get(f"{base}/dit/{num}", params={"token": token},
                                 headers={"accept": "application/json"}, timeout=20)
                if r.status_code != 200:
                    return self._send_json(r.status_code, {"error": f"Artis HTTP {r.status_code}", "body": (r.text or "")[:400]})
                d = r.json()
                # Synthèse pour l'aperçu
                client = d.get("client") or {}
                etat = d.get("etat") or {}
                if isinstance(etat, dict): etat_code = etat.get("code"); etat_lib = etat.get("libelle")
                else: etat_code = etat; etat_lib = etat
                inters = []
                for it in d.get("inter") or []:
                    if not isinstance(it, dict): continue
                    et = it.get("etat") or {}
                    et_code = et.get("code") if isinstance(et, dict) else et
                    intv = it.get("intervenant") or {}
                    inters.append({
                        "numero":  it.get("numero"),
                        "etat":    et_code,
                        "date":    it.get("dateDebut"),
                        "duree_h": it.get("duree"),
                        "type_facturation": it.get("typeFacturation"),
                        "intervenant": ((intv.get("prenom") or "") + " " + (intv.get("nom") or "")).strip() if isinstance(intv, dict) else "",
                        "domaine": (it.get("domaine") or {}).get("libelle") if isinstance(it.get("domaine"), dict) else it.get("domaine"),
                    })
                return self._send_json(200, {
                    "ok": True,
                    "numero":      d.get("numero"),
                    "date":        d.get("date"),
                    "client_code": client.get("code") if isinstance(client, dict) else "",
                    "client_nom":  client.get("libelle") if isinstance(client, dict) else "",
                    "etat":        etat_code,
                    "etat_libelle": etat_lib,
                    "nature":      (d.get("nature") or {}).get("libelle") if isinstance(d.get("nature"), dict) else d.get("nature"),
                    "type_dit":    (d.get("typeDit") or {}).get("libelle") if isinstance(d.get("typeDit"), dict) else d.get("typeDit"),
                    "commentaire": (d.get("commentaire") or "")[:500],
                    "commentaire_cloture": (d.get("commentaireCloture") or "")[:500],
                    "nb_interventions": len(inters),
                    "interventions": inters,
                })
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/dit/non-facturable/history  GET (admin only) — historique des passages NonFacturable
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "dit" \
           and parts[2] == "non-facturable" and parts[3] == "history" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            history_file = DATA_DIR / "nonfact_history.json"
            try:
                if history_file.exists():
                    with open(history_file, encoding="utf-8") as f:
                        items = json.load(f) or []
                else:
                    items = []
                items_sorted = sorted(items, key=lambda e: e.get("ts", ""), reverse=True)
                return self._send_json(200, {"ok": True, "count": len(items_sorted), "items": items_sorted})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/dit/fetch?from=YYYY-MM-DD&to=YYYY-MM-DD  GET — fetch DIT et TNP sur une plage custom (admin only, ne touche pas msp_data.json)
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "dit" and parts[2] == "fetch" and method == "GET":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            df = qs.get("from", [None])[0]
            dt = qs.get("to",   [None])[0]
            if not df or not dt:
                return self._send_json(400, {"error": "params 'from' et 'to' requis (YYYY-MM-DD)"})
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                it_codes = {c.get("code") for c in data.get("reconciliation", []) if c.get("code")}
                token = artis_get_token()
                if not token:
                    return self._send_json(502, {"error": "token Artis indisponible"})
                res = artis_get_dit(token, date_debut=df, date_fin=dt)
                if res.get("error"):
                    return self._send_json(502, {"error": res["error"]})
                all_dit = res.get("dit", [])
                filtered = [d for d in all_dit if d.get("client_code") in it_codes]
                np_res = artis_get_non_productif(token, date_debut=df, date_fin=dt)
                np_data = [] if np_res.get("error") else np_res.get("np", [])
                log(f"[DIT FETCH] user={self._user()} {df}→{dt} → {len(filtered)}/{len(all_dit)} DIT + {len(np_data)} TNP")
                return self._send_json(200, {"ok": True, "dit": filtered, "non_productif": np_data,
                                              "count": len(filtered), "total_received": len(all_dit),
                                              "np_count": len(np_data), "from": df, "to": dt})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        # /api/dit/refresh  POST (admin only) — refetch live des DIT et TNP, patch msp_data.json en place
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "dit" and parts[2] == "refresh" and method == "POST":
            if not self._is_admin():
                return self._send_json(403, {"error": "admin uniquement"})
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                it_codes = {c.get("code") for c in data.get("reconciliation", []) if c.get("code")}
                token = artis_get_token()
                if not token:
                    return self._send_json(502, {"error": "token Artis indisponible"})
                res = artis_get_dit(token, days=90)
                if res.get("error"):
                    return self._send_json(502, {"error": res["error"]})
                all_dit = res.get("dit", [])
                filtered = [d for d in all_dit if d.get("client_code") in it_codes]
                data.setdefault("raw_sources", {})["dit"] = filtered
                data.setdefault("sources", {})["dit"] = {
                    "status": "ok", "count": len(filtered), "total_received": len(all_dit)
                }
                # TNP en parallèle
                np_res = artis_get_non_productif(token, days=90)
                np_count = 0
                if not np_res.get("error"):
                    data["raw_sources"]["non_productif"] = np_res.get("np", [])
                    np_count = len(np_res.get("np", []))
                    data["sources"]["non_productif"] = {"status": "ok", "count": np_count}
                else:
                    data["sources"]["non_productif"] = {"status": "error", "error": np_res["error"]}
                with open(DATA_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                log(f"[DIT REFRESH] user={self._user()} → {len(filtered)}/{len(all_dit)} DIT + {np_count} TNP")
                return self._send_json(200, {"ok": True, "count": len(filtered), "total_received": len(all_dit), "np_count": np_count})
            except Exception as e:
                return self._send_json(500, {"error": str(e)})
        return self._send_json(404, {"error": "not found"})

    def do_GET(self):
        raw = self.path.split("?")[0]
        path = raw.lstrip("/")
        if path.startswith("api/"):
            return self._api_dispatch("GET", path)
        if path == "" or path.endswith("/"):
            self.send_response(302)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return
        name = Path(path).name
        ext  = Path(path).suffix.lower()
        if name in BLOCKED_PATHS or ext in BLOCKED_EXT or name.startswith("."):
            self.send_error(403, "Forbidden")
            return
        # Data files dynamiques : servis depuis DATA_DIR (volume Azure Files en prod).
        # Le code statique (HTML/JS/CSS) reste dans le cwd (DOSSIER, dans l'image).
        if name in DATA_FILES_SERVED:
            return self._serve_data_file(name)
        super().do_GET()

    def _serve_data_file(self, name):
        """Sert un fichier de données depuis DATA_DIR avec headers anti-cache."""
        target = DATA_DIR / name
        if not target.exists():
            return self.send_error(404, "Not Found")
        try:
            data = target.read_bytes()
        except Exception as e:
            return self._send_json(500, {"error": f"read failed: {e}"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = self.path.split("?")[0].lstrip("/")
        if path.startswith("api/"):
            return self._api_dispatch("POST", path)
        self.send_error(404, "Not Found")

    def do_DELETE(self):
        path = self.path.split("?")[0].lstrip("/")
        if path.startswith("api/"):
            return self._api_dispatch("DELETE", path)
        self.send_error(404, "Not Found")

    def do_PATCH(self):
        path = self.path.split("?")[0].lstrip("/")
        if path.startswith("api/"):
            return self._api_dispatch("PATCH", path)
        self.send_error(404, "Not Found")

    def list_directory(self, path):
        self.send_error(403, "Forbidden")
        return None

    def log_message(self, *args):
        pass

def start_server():
    # cwd = DOSSIER pour servir les HTML/JS statiques (in-image).
    # Les fichiers data dynamiques (msp_data.json, etc.) sont servis depuis DATA_DIR
    # via _serve_data_file (cf. do_GET).
    os.chdir(DOSSIER)
    # Bind explicite 0.0.0.0 : Container Apps ingress doit pouvoir taper le port.
    with HTTPServer(("0.0.0.0", PORT), SecureHandler) as httpd:
        log(f"HTTP server bind 0.0.0.0:{PORT} (cwd={DOSSIER}, DATA_DIR={DATA_DIR})")
        httpd.serve_forever()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    log("MSP Dashboard Updater démarré")
    log(f"Mise à jour automatique à {HEURE_MAJ}h00 chaque soir")
    log(f"Dashboard : http://localhost:{PORT}/dashboard.html")
    log("(Ctrl+C pour arrêter)\n")

    threading.Thread(target=start_server, daemon=True).start()

    # Pas de MAJ au démarrage : on sert le msp_data.json existant (du dernier 20h).
    # Si le fichier n'existe pas (premier lancement), MAJ API complète obligatoire.
    if not DATA_FILE.exists():
        log("  › Pas de msp_data.json — MAJ API initiale (~11min)...")
        mise_a_jour(with_rfc=True, rfc_source="api")
    else:
        log(f"  › msp_data.json existant servi — prochaine MAJ à {HEURE_MAJ}h00")

    dernier_jour_maj = None  # MAJ planifiée pas encore faite aujourd'hui

    while True:
        now = datetime.now()
        if now.hour == HEURE_MAJ and date.today() != dernier_jour_maj:
            # MAJ planifiée du soir : scan RFC API complet (rafraîchit les quantités)
            mise_a_jour(with_rfc=True, rfc_source="api")
            dernier_jour_maj = date.today()
        time.sleep(60)

if __name__ == "__main__":
    main()
