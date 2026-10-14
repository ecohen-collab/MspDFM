"""
Auth Graph deleguee pour lire la BAL support@dfm.fr (device code, client public
pre-consenti -> pas d'app registration a demander a Benjamin).

Usage :
    python mail_auth.py            -> demarre / termine la connexion
    python mail_auth.py --token    -> affiche juste si un jeton valide existe

Le refresh token est stocke dans ./data/mail_tokens.json (en prod : /data).
"""
import os, sys, json, time, pathlib, requests

TENANT     = os.environ.get("DFM_TENANT_ID", "9e2627a2-9070-4fc1-9d57-946ac4bfe416")
# Microsoft Graph Command Line Tools : client public first-party preautorise pour
# Graph (le client "Office" d3590ed6 ne l'est plus -> AADSTS65002).
CLIENT_ID  = os.environ.get("MAIL_CLIENT_ID", "14d82eec-204b-4c2f-b7e8-296a70dab67e")
# Mail.ReadWrite est demande depuis le 2026-09-09 pour un seul usage, valide par
# Elliot : DEPLACER le mail traite vers « 01 - DIT ARTIS SUPPORT » apres creation
# du DIT. Toujours PAS de Mail.Send : l'outil ne doit jamais ecrire au client.
# La lecture reste la seule operation effectuee tant que le deplacement n'est pas
# explicitement declenche (cf. deplacer() dans mail_dit.py).
SCOPES_LECTURE = ("https://graph.microsoft.com/Mail.ReadWrite offline_access")
# Mail.Send ajoute le 2026-09-14 a la demande d'Elliot : « je voudrais mettre en
# place une relance automatique, et qu'une reponse sur un ticket soit transferee
# au dernier intervenant de la DIT ». Sans lui, `createForward` prepare le
# message mais `send` repond 403 (verifie).
#
# ATTENTION, ce scope change la nature de l'outil : il devient capable
# d'ENVOYER du courrier au nom de support@dfm.fr. Rien ne part malgre tout sans
# le drapeau d'ecriture, comme toute action qui ecrit.
SCOPES     = ("https://graph.microsoft.com/Mail.ReadWrite "
              "https://graph.microsoft.com/Mail.Send offline_access")
# Le defaut est ancre sur CE fichier, pas sur le repertoire courant : un
# outil lance depuis .claude/skills/mail-dit/bin/ cherchait sinon un
# ./data inexistant, access_token() rendait None, et Graph repondait 401
# "token could not be read" — que lire_mail.py affichait en "mail
# introuvable" (2026-10-01). En production DATA_DIR est pose, ce defaut
# ne sert qu'en local.
DATA       = pathlib.Path(os.environ.get("DATA_DIR")
                          or (pathlib.Path(__file__).resolve().parent / "data"))
TOKENS     = DATA / "mail_tokens.json"
AUTH       = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0"


# Le jeton ne vit pas toujours sur un disque. Dans une session distante
# (routine Cloud) il n'y a ni `/data` monte, ni `az login` : le fichier est
# alors lu et ecrit sur Azure Files par SAS. Le refresh token tourne —
# Microsoft en rend un nouveau a chaque rafraichissement — donc il ne suffit
# pas de le deposer en variable d'environnement : il faut un endroit ou le
# REECRIRE, sinon la session distante perd l'acces des la premiere rotation.
#
# Sur le poste d'Elliot aucune de ces variables n'est posee : `_distant()` est
# faux et tout se passe comme avant, dans ./data.
_CACHE = {"jeton": None}


def _distant():
    try:
        import azure_files
        return azure_files.disponible()
    except Exception:                    # noqa: BLE001
        return False


def _save(d):
    _CACHE["jeton"] = d
    if _distant():
        import azure_files
        azure_files.ecrire("mail_tokens.json", json.dumps(d))
        return
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = str(TOKENS) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, TOKENS)


def _load():
    if _distant():
        # Un seul aller-retour par processus : `access_token()` est appele a
        # chaque requete Graph, et le jeton ne bouge qu'ici.
        if _CACHE["jeton"] is None:
            import azure_files
            brut = azure_files.lire("mail_tokens.json")
            try:
                _CACHE["jeton"] = json.loads(brut.decode("utf-8")) if brut else {}
            except Exception:            # noqa: BLE001
                _CACHE["jeton"] = {}
        return _CACHE["jeton"]
    try:
        return json.loads(TOKENS.read_text(encoding="utf-8")) if TOKENS.exists() else {}
    except Exception:
        return {}


def access_token():
    """Renvoie un access token, en rafraichissant si besoin. None si pas connecte."""
    t = _load()
    if not t.get("refresh_token"):
        return None
    if t.get("access_token") and t.get("exp", 0) > time.time() + 120:
        return t["access_token"]
    # Deux essais, dans cet ordre : avec Mail.Send, puis sans. Tant que le
    # consentement n'a pas ete redonne, le premier echoue — et sans ce repli
    # tout le moteur de mail s'arreterait le temps que quelqu'un se reconnecte.
    j = None
    for scope in (SCOPES, SCOPES_LECTURE):
        r = requests.post(f"{AUTH}/token", timeout=30, data={
            "client_id": CLIENT_ID, "grant_type": "refresh_token",
            "refresh_token": t["refresh_token"], "scope": scope})
        if r.status_code == 200:
            j = r.json()
            t["scope_obtenu"] = j.get("scope", scope)
            break
        if scope is SCOPES:
            continue
        print(f"refresh KO HTTP {r.status_code}: {r.text[:200]}")
    if j is None:
        return None
    t["access_token"] = j["access_token"]
    t["exp"] = time.time() + int(j.get("expires_in", 3600))
    if j.get("refresh_token"):
        t["refresh_token"] = j["refresh_token"]
    _save(t)
    return t["access_token"]


def connect():
    r = requests.post(f"{AUTH}/devicecode", timeout=30,
                      data={"client_id": CLIENT_ID, "scope": SCOPES})
    if r.status_code != 200:
        print(f"devicecode KO HTTP {r.status_code}: {r.text[:400]}")
        return False
    j = r.json()
    print("\n" + "=" * 62)
    print(f"  Ouvre : {j.get('verification_uri')}")
    print(f"  Code  : {j['user_code']}")
    # Le jeton appartient a la BOITE elle-meme : `/me` rend « Support DFM
    # <support@dfm.fr> ». C'est donc avec CE compte qu'il faut se connecter,
    # pas avec un compte personnel.
    print(f"  Connecte-toi avec support@dfm.fr")
    print(f"  Permissions demandees : Mail.ReadWrite + Mail.Send")
    print("=" * 62 + "\n")
    deadline = time.time() + int(j.get("expires_in", 900))
    interval = int(j.get("interval", 5))
    while time.time() < deadline:
        time.sleep(interval)
        p = requests.post(f"{AUTH}/token", timeout=30, data={
            "client_id": CLIENT_ID,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": j["device_code"]})
        if p.status_code == 200:
            k = p.json()
            _save({"refresh_token": k.get("refresh_token"),
                   "access_token": k.get("access_token"),
                   "exp": time.time() + int(k.get("expires_in", 3600)),
                   "scope": k.get("scope")})
            print("Connecte OK. Scopes accordes :")
            print("  " + (k.get("scope") or "?"))
            return True
        err = ""
        try: err = p.json().get("error", "")
        except Exception: pass
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5; continue
        print(f"echec : {err} — {p.text[:250]}")
        return False
    print("expire (timeout)")
    return False


DEVICE = DATA / "mail_device.json"


def start():
    """Demarre le device code et rend la main tout de suite (affiche le code)."""
    r = requests.post(f"{AUTH}/devicecode", timeout=30,
                      data={"client_id": CLIENT_ID, "scope": SCOPES})
    if r.status_code != 200:
        print(f"devicecode KO HTTP {r.status_code}: {r.text[:300]}")
        return False
    j = r.json()
    DATA.mkdir(parents=True, exist_ok=True)
    DEVICE.write_text(json.dumps({"device_code": j["device_code"],
                                  "interval": int(j.get("interval", 5)),
                                  "exp": time.time() + int(j.get("expires_in", 900))}),
                      encoding="utf-8")
    print(f"URL : {j.get('verification_uri')}")
    print(f"CODE: {j['user_code']}")
    return True


def poll(wait=0):
    """Interroge une fois (ou pendant `wait` secondes) la fin de la connexion."""
    if not DEVICE.exists():
        print("aucune connexion en cours"); return False
    d = json.loads(DEVICE.read_text(encoding="utf-8"))
    interval = int(d.get("interval", 5))
    deadline = time.time() + wait
    while True:
        p = requests.post(f"{AUTH}/token", timeout=30, data={
            "client_id": CLIENT_ID,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": d["device_code"]})
        if p.status_code == 200:
            k = p.json()
            _save({"refresh_token": k.get("refresh_token"),
                   "access_token": k.get("access_token"),
                   "exp": time.time() + int(k.get("expires_in", 3600)),
                   "scope": k.get("scope")})
            try: DEVICE.unlink()
            except Exception: pass
            print("CONNECTE OK")
            print("scopes: " + (k.get("scope") or "?"))
            return True
        err = ""
        try: err = p.json().get("error", "")
        except Exception: pass
        if err in ("authorization_pending", "slow_down"):
            if time.time() >= deadline:
                print("en attente de ta validation")
                return False
            time.sleep(interval + (5 if err == "slow_down" else 0))
            continue
        print(f"echec: {err} — {p.text[:250]}")
        return False


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--token" in a:
        print("jeton OK" if access_token() else "pas connecte")
    elif "--start" in a:
        start()
    elif "--poll" in a:
        w = 0
        for x in a:
            if x.startswith("--wait="): w = int(x.split("=")[1])
        poll(w)
    elif "--scopes" in a:
        # Quelles permissions le jeton porte-t-il REELLEMENT ? Azure rend les
        # scopes CONSENTIS, pas ceux demandes : c'est la seule source fiable.
        t = _load()
        access_token()
        t = _load()
        s = t.get("scope_obtenu") or t.get("scope") or ""
        print("scopes du jeton :")
        for x in sorted(s.split()):
            print("  " + x)
        print("\nMail.Send : %s" % ("OUI" if "Mail.Send" in s else "NON"))
    else:
        # `--forcer` : rouvrir une session MEME si le jeton actuel est valide.
        # Sans ca, impossible d'obtenir un nouveau consentement quand on ajoute
        # une permission — le script repondait « deja connecte » et s'arretait.
        if access_token() and "--forcer" not in a:
            t = _load()
            s = t.get("scope_obtenu") or t.get("scope") or ""
            print("deja connecte (jeton valide)")
            print("Mail.Send : %s" % ("OUI" if "Mail.Send" in s else "NON"))
            if "Mail.Send" not in s:
                print("\nPour ajouter la permission d'envoi :")
                print("    python mail_auth.py --forcer")
        else:
            connect()
