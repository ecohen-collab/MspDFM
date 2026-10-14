# -*- coding: utf-8 -*-
"""Lire et ecrire un fichier du share Azure Files, par SAS, sans Azure CLI.

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


def disponible():
    """Vrai si les trois variables sont posees — donc si on est en distant."""
    return all(_conf())


def _url(chemin):
    compte, share, sas = _conf()
    if not all((compte, share, sas)):
        raise RuntimeError("AZ_FILES_ACCOUNT / AZ_FILES_SHARE / AZ_FILES_SAS "
                           "ne sont pas toutes posees")
    return ("https://%s.file.core.windows.net/%s/%s?%s"
            % (compte, share, chemin.lstrip("/"), sas.lstrip("?")))


def lire(chemin):
    """Contenu du fichier en octets, ou None s'il n'existe pas.

    Un 404 n'est pas une erreur : au premier passage le fichier n'est pas
    encore la, et l'appelant doit pouvoir partir d'un contenu vide.
    """
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
    """Aller-retour de controle : ecrit un petit fichier, le relit, l'oublie."""
    temoin = "azure_files_test.txt"
    ecrire(temoin, "ok")
    return lire(temoin) == b"ok"


if __name__ == "__main__":
    import sys
    if not disponible():
        print("variables absentes : AZ_FILES_ACCOUNT / AZ_FILES_SHARE / "
              "AZ_FILES_SAS")
        sys.exit(1)
    print("aller-retour :", "OK" if essai() else "KO")
