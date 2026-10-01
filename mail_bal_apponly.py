# -*- coding: utf-8 -*-
"""Bascule la chaine mail -> DIT en authentification APPLICATIVE.

Pourquoi ce module existe
-------------------------
Jusqu'ici la boite `support@dfm.fr` etait lue avec un jeton DELEGUE obtenu par
device code : quelqu'un se connecte une fois, un refresh token dort sur /data,
et tout le code parle a Graph via `/me/...`.

Ca marche tant qu'un humain est passe par la. Pour un robot qui tourne seul
dans un Container Apps Job, il faut un jeton APPLICATIF (client credentials) —
l'app registration `svc-mail-dit-support` posee par Benjamin le 2026-10-01,
restreinte a la seule boite support par une Application Access Policy Exchange
(verifie : `Test-ApplicationAccessPolicy` rend Accorde sur support@dfm.fr et
Refuse sur bozon@dfm.fr).

Et la, une difficulte : **`/me` n'existe pas en app-only**. Graph exige la
boite nommement, `/users/support@dfm.fr/...`. Or le moteur (`mail_dit.py`,
`mail_dit_ecriture.py`) porte 33 URL `/me/...`, parfois coupees sur deux
lignes.

Le choix fait ici
-----------------
Plutot que de reecrire ces 33 sites dans du code en production, on reecrit
l'URL **au moment de l'appel**, et seulement quand le mode applicatif est
explicitement active par le point d'entree du robot. Le moteur n'est pas
touche ; en mode delegue, rien de ce module ne s'execute.

C'est un detournement de `requests`, et il faut le savoir : si un jour le
moteur se met a viser une AUTRE boite que support, la reecriture la
redirigerait silencieusement. C'est pourquoi `activer()` journalise ce qu'il
fait et pourquoi la substitution est aussi etroite que possible — le seul
segment `/v1.0/me/`, rien d'autre.

Usage (point d'entree du job uniquement) :

    import mail_bal_apponly as APP
    APP.activer()          # leve si la configuration manque
"""
import os
import time

import requests

GRAPH_RACINE = "https://graph.microsoft.com/v1.0"
BAL = os.environ.get("MAILDIT_BAL", "support@dfm.fr")

_ME = GRAPH_RACINE + "/me/"
_USERS = "%s/users/%s/" % (GRAPH_RACINE, BAL)

_jeton = {"valeur": None, "expire": 0}
_actif = False


def configure():
    """Les trois valeurs sont-elles presentes ? Rend (tenant, client, secret)."""
    tenant = os.environ.get("MAILDIT_TENANT_ID")
    client = os.environ.get("MAILDIT_CLIENT_ID")
    secret = os.environ.get("MAILDIT_CLIENT_SECRET")
    return (tenant, client, secret) if (tenant and client and secret) else None


def jeton_app(forcer=False):
    """Jeton applicatif, mis en cache jusqu'a 60 s avant son expiration."""
    if not forcer and _jeton["valeur"] and time.time() < _jeton["expire"]:
        return _jeton["valeur"]
    conf = configure()
    if not conf:
        raise RuntimeError(
            "mode applicatif demande mais MAILDIT_TENANT_ID / MAILDIT_CLIENT_ID "
            "/ MAILDIT_CLIENT_SECRET manquent")
    tenant, client, secret = conf
    r = requests.post(
        "https://login.microsoftonline.com/%s/oauth2/v2.0/token" % tenant,
        data={"client_id": client, "client_secret": secret,
              "grant_type": "client_credentials",
              "scope": "https://graph.microsoft.com/.default"}, timeout=60)
    if r.status_code != 200:
        # Le corps porte le code AADSTS, seule chose utile pour diagnostiquer.
        raise RuntimeError("jeton applicatif refuse (HTTP %s) : %s"
                           % (r.status_code, r.text[:300]))
    j = r.json()
    _jeton["valeur"] = j["access_token"]
    _jeton["expire"] = time.time() + int(j.get("expires_in", 3600)) - 60
    return _jeton["valeur"]


def _reecrire(url):
    """`.../v1.0/me/X` -> `.../v1.0/users/support@dfm.fr/X`. Le reste intact."""
    if isinstance(url, str) and url.startswith(_ME):
        return _USERS + url[len(_ME):]
    return url


def activer(log=None):
    """Bascule le processus en app-only. Idempotent.

    Deux effets : `mail_auth.access_token` rend desormais le jeton applicatif,
    et toute URL Graph `/me/` emise par `requests` vise la boite nommement.
    """
    global _actif
    dire = log or (lambda *_a: None)
    if _actif:
        return True
    jeton_app()                      # echoue tout de suite si mal configure

    import mail_auth
    mail_auth.access_token = lambda *a, **k: jeton_app()

    original = requests.api.request

    def request(methode, url, **kw):
        return original(methode, _reecrire(url), **kw)

    requests.api.request = request
    # requests.get/post/... passent par requests.api.request, SAUF s'ils ont
    # ete importes directement : on recable aussi les raccourcis du module.
    for nom in ("get", "post", "put", "patch", "delete", "head", "options"):
        def fabrique(m=nom):
            def appel(url, **kw):
                return original(m, _reecrire(url), **kw)
            return appel
        setattr(requests, nom, fabrique())

    _actif = True
    dire("[app-only] actif : les URL Graph /me/ visent %s" % BAL)
    return True


def etat():
    conf = configure()
    return {"configure": bool(conf), "actif": _actif, "bal": BAL,
            "client_id": (conf[1] if conf else None)}
