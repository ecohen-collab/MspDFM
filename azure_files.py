# -*- coding: utf-8 -*-
"""Lire et ecrire les fichiers persistants de la chaine, sans Azure CLI.

Deux voies, dans cet ordre : le **relais du dashboard** (`RELAIS_URL` +
`RELAIS_CLE`), et a defaut une **SAS** sur le share (`AZ_FILES_*`). La
premiere existe parce que la seconde demande les cles du compte de stockage,
que le compte d'Elliot n'a pas le droit de lire.

Pourquoi ce module existe : la chaine mail -> DIT garde deux choses sur
`/data` (Azure Files) — le jeton Graph (`mail_tokens.json`) et le journal
(`mail_dit_journal.jsonl`). Sur le poste d'Elliot on y accede avec
`az storage file upload --auth-mode login`, donc avec SON identite. Dans une
session distante (routine Cloud) il n'y a ni `az`, ni `az login`, ni identite
managee : il n'y a que des variables d'environnement.

D'ou ce module : trois appels REST a l'API Azure Files, authentifies par une
**SAS de share** passee en variable. Rien a installer, `requests` suffit.

    AZ_FILES_ACCOUNT  dfmmspdashboardstor
    AZ_FILES_SHARE    msp-data
    AZ_FILES_SAS      sv=2024-...&ss=f&srt=sco&sp=rwdlc&se=...&sig=...

`disponible()` dit si ces trois-la sont posees. Quand elles ne le sont pas,
tout le reste du code continue comme avant, sur le disque local : c'est le cas
du poste d'Elliot, et il ne faut rien y changer.
"""
import os
import requests

# Version de l'API File Service. Celle-ci accepte `x-ms-write: update` et les
# SAS de service comme de compte ; ne pas la baisser sans verifier.
VERSION = "2023-11-03"
TIMEOUT = 60


def _conf():
    return (os.environ.get("AZ_FILES_ACCOUNT"),
            os.environ.get("AZ_FILES_SHARE"),
            os.environ.get("AZ_FILES_SAS"))


# ── le relais du dashboard ───────────────────────────────────────────────────
# La SAS suppose qu'on puisse en fabriquer une : il faut les cles du compte de
# stockage, et Elliot ne les a pas (verifie le 2026-10-14 : `AuthorizationFailed`
# sur `az storage account keys list` — ses roles portent sur l'ACR, la Container
# App et l'environnement, pas sur `dfmmspdashboardstor`).
#
# Le dashboard, lui, a `/data` monte. Il sert donc de relais : trois chemins
# `/api/relais/<fichier>`, exclus d'Easy Auth un par un, gardes par `RELAIS_CLE`.
# C'est ce chemin-la qu'utilise une routine Cloud ; la SAS reste accepte si un
# jour quelqu'un en fournit une.
#
#   RELAIS_URL   https://msp-dashboard.<...>.azurecontainerapps.io
#   RELAIS_CLE   la cle, secret de la Container App
def _relais():
    return (os.environ.get("RELAIS_URL"), os.environ.get("RELAIS_CLE"))


def disponible():
    """Vrai si un chemin de persistance est configure — relais ou SAS."""
    return all(_relais()) or all(_conf())


def _url(chemin):
    compte, share, sas = _conf()
    if not all((compte, share, sas)):
        raise RuntimeError("AZ_FILES_ACCOUNT / AZ_FILES_SHARE / AZ_FILES_SAS "
                           "ne sont pas toutes posees")
    return ("https://%s.file.core.windows.net/%s/%s?%s"
            % (compte, share, chemin.lstrip("/"), sas.lstrip("?")))


def _url_relais(chemin):
    base, _ = _relais()
    return "%s/api/relais/%s" % (base.rstrip("/"), chemin.lstrip("/"))


def lire(chemin):
    """Contenu du fichier en octets, ou None s'il n'existe pas.

    Un 404 n'est pas une erreur : au premier passage le fichier n'est pas
    encore la, et l'appelant doit pouvoir partir d'un contenu vide.
    """
    base, cle = _relais()
    if base and cle:
        r = requests.get(_url_relais(chemin), timeout=TIMEOUT,
                         headers={"X-Relais-Cle": cle})
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RuntimeError("relais, lecture %s : HTTP %s %s"
                               % (chemin, r.status_code, r.text[:200]))
        return r.content
    r = requests.get(_url(chemin), headers={"x-ms-version": VERSION},
                     timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    if r.status_code not in (200, 206):
        raise RuntimeError("lecture %s : HTTP %s %s"
                           % (chemin, r.status_code, r.text[:200]))
    return r.content


def ecrire(chemin, donnees):
    """Ecrit (ou remplace) le fichier. `donnees` : octets ou texte.

    Azure Files demande deux requetes — creer le fichier a la bonne taille,
    puis y poser le contenu. Le `Create File` remplace un fichier existant et
    remet sa taille a celle qu'on annonce : pas de residu de l'ancienne
    version, meme si elle etait plus longue.
    """
    if isinstance(donnees, str):
        donnees = donnees.encode("utf-8")
    base, cle = _relais()
    if base and cle:
        # POST et non PUT : le serveur du dashboard est un
        # `SimpleHTTPRequestHandler`, qui ne connait pas PUT et repond 501.
        r = requests.post(_url_relais(chemin), data=donnees, timeout=TIMEOUT,
                          headers={"X-Relais-Cle": cle,
                                   "Content-Type": "application/octet-stream"})
        if r.status_code != 200:
            raise RuntimeError("relais, ecriture %s : HTTP %s %s"
                               % (chemin, r.status_code, r.text[:200]))
        return True
    url = _url(chemin)
    r = requests.put(url, timeout=TIMEOUT, headers={
        "x-ms-version": VERSION,
        "x-ms-type": "file",
        "x-ms-content-length": str(len(donnees)),
        "Content-Length": "0"})
    if r.status_code not in (201, 200):
        raise RuntimeError("creation %s : HTTP %s %s"
                           % (chemin, r.status_code, r.text[:200]))
    if not donnees:                      # un fichier vide n'a pas de plage
        return True
    sep = "&" if "?" in url else "?"
    r = requests.put(url + sep + "comp=range", data=donnees, timeout=TIMEOUT,
                     headers={"x-ms-version": VERSION,
                              "x-ms-write": "update",
                              "Range": "bytes=0-%d" % (len(donnees) - 1),
                              "Content-Length": str(len(donnees))})
    if r.status_code not in (201, 200):
        raise RuntimeError("ecriture %s : HTTP %s %s"
                           % (chemin, r.status_code, r.text[:200]))
    return True


def essai():
    """Controle avant le premier passage : le stockage repond-il ?

    Par le relais on se contente de LIRE le drapeau : les trois fichiers sont
    une liste blanche, un fichier temoin n'y est pas, et on ne va pas ecrire
    dans le journal pour tester. Par SAS on fait un vrai aller-retour, le
    share acceptant n'importe quel nom.
    """
    base, cle = _relais()
    if base and cle:
        return lire("mail_dit_ecriture.txt") is not None
    temoin = "azure_files_test.txt"
    ecrire(temoin, "ok")
    return lire(temoin) == b"ok"


if __name__ == "__main__":
    import sys
    if not disponible():
        print("aucun stockage configure : il faut RELAIS_URL + RELAIS_CLE, "
              "ou AZ_FILES_ACCOUNT + AZ_FILES_SHARE + AZ_FILES_SAS")
        sys.exit(1)
    voie = "relais" if all(_relais()) else "SAS"
    print("stockage par %s :" % voie, "OK" if essai() else "KO")
