# -*- coding: utf-8 -*-
"""Helpers de lecture Artis pour la chaine mail -> DIT.

Lecture seule : rien ici n'ecrit dans Artis. Les ecritures passent par
mail_dit_ecriture (creer_dit, poser_uo, joindre_fichier) et par
artis_type_travail (poser_type_travail, poser_commentaire).

Usage en ligne de commande :

    python artis.py chercher "SEINE ET MARNE"   # client absent de l'index
    python artis.py client 9599          # raison sociale, biens, interlocuteurs
    python artis.py dit 100175861        # etat, date, commentaire d'une DIT
    python artis.py intervenants 100174590   # qui prevenir apres un ajout
    python artis.py doublons 9599        # DIT du client sur 48 h
    python artis.py symptomes 365        # symptomes dont le code/libelle matche
"""
import io
import re
import sys

import requests

# La racine du repo, deduite de l'emplacement de CE fichier :
# <repo>/.claude/skills/mail-dit/bin/<ce fichier>. Le chemin etait code en
# dur sur le poste d'Elliot, donc ces helpers ne servaient a rien ailleurs --
# constate le 2026-10-15, une routine Cloud a du s'en passer et s'est
# arretee faute de pouvoir importer la chaine. `parents[4]` remonte
# bin -> mail-dit -> skills -> .claude -> racine du repo.
import pathlib
_RACINE = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_RACINE))
import mail_dit as M                      # noqa: E402
import mail_dit_ecriture as E             # noqa: E402

_SESSION = {}


def connexion():
    """(base_url, token). Le jeton part en PARAMETRE `token`, jamais en Bearer."""
    if "base" not in _SESSION:
        _SESSION["base"], _SESSION["token"] = E._artis()
    return _SESSION["base"], _SESSION["token"]


def get(chemin, **params):
    """GET Artis. Rend une liste, un dict, ou None si la requete echoue."""
    base, token = connexion()
    params["token"] = token
    r = requests.get(base + chemin, headers={"accept": "application/json"},
                     params=params, timeout=90)
    if r.status_code != 200:
        return None
    try:
        j = r.json()
    except ValueError:
        return None
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        return j.get("items") or j.get("dit") or j
    return j


def client(code):
    """Fiche client, ou None."""
    return get("/client/%s" % code)


def chercher_client(texte, maxi=10):
    """Clients dont le nom ou le code contient `texte`.

    `GET /client?chaineRecherchee=...` — le parametre du swagger, celui qui
    filtre vraiment (contrairement a `libelle`, qui rend toute la base).
    C'est la sortie de secours quand un client est absent de l'index fige :
    SMC77, ESBGI et Maison Arnaud Larher ont ete retrouves ainsi le 2026-10-01.

    Rend un candidat, pas une certitude : verifier interlocuteurs et biens.
    """
    out = []
    for c in get("/client", chaineRecherchee=texte, maxResults=maxi) or []:
        if isinstance(c, dict):
            out.append({"code": c.get("code"),
                        "nom": c.get("raisonSociale") or c.get("libelle")})
    return out


def biens(code):
    """TOUS les biens du client. maxResults obligatoire : l'API pagine a 20."""
    return get("/client/%s/bien" % code, maxResults=5000) or []


def bien_dit(code):
    """L'identifiantFabricant a poser dans la DIT, ou None si le client n'a ni
    INFOGERANCE ni BAL EXCHANGE 365 (cas SPAT 7968 : il faut alors creer sans
    bien et le signaler)."""
    liste = biens(code)
    for motif in ("INFOGERANCE", "BAL EXCHANGE 365", "EXCHANGE"):
        for b in liste:
            if motif in str(b.get("libelle") or "").upper():
                return b.get("identifiantFabricant")
    return None


def dits(code, debut, fin):
    """DIT du client sur une periode. Dates ISO uniquement (JJ/MM/AAAA -> 400)."""
    return get("/client/%s/dit" % code, dateDebut=debut, dateFin=fin,
               maxResults=200) or []


def dit(numero):
    """Une DIT par son numero. N'expose ni le bien ni le demandeur."""
    return get("/dit/%s" % numero)


def intervenants(numero):
    """Les interventions d'une DIT, de la plus ancienne a la plus recente.

    Sert a savoir QUI prevenir quand on ajoute le mail d'un client a un ticket
    en cours : le dernier de la liste est l'intervenant en charge.

    L'adresse rendue par Artis est celle de l'organisation (support@dfm.fr),
    pas celle de la personne : resoudre le nom dans l'annuaire Microsoft
    (search_people) pour obtenir son adresse reelle.
    """
    d = dit(numero) or {}
    out = []
    for it in d.get("inter") or []:
        iv = it.get("intervenant") or {}
        out.append({"intervention": it.get("numero"),
                    "debut": it.get("dateDebut"),
                    "etat": it.get("etat"),
                    "nom": iv.get("nom"), "prenom": iv.get("prenom"),
                    "matricule": iv.get("matricule")})
    return out


def client_du_dit(numero):
    """(code, libelle) du client porteur d'une DIT — sert a identifier un client
    dont le domaine est absent de l'index fige au 10/09."""
    d = dit(numero) or {}
    c = d.get("client") or {}
    return c.get("code"), c.get("raisonSociale") or c.get("libelle")


def resoudre(adresse, domaine=None):
    """Pistes de resolution d'un client depuis une adresse mail.

    Rend {'exact': [...], 'domaine': [...]} sans jamais trancher : la decision
    se prend apres verification dans Artis (raison sociale, interlocuteurs,
    biens). Ne JAMAIS resoudre par similarite de libelle.
    """
    idx = M._index_clients()
    libelles = M._libelles_clients()
    adresse = (adresse or "").strip().lower()
    if domaine is None and "@" in adresse:
        domaine = adresse.split("@", 1)[1]
    exact = (idx.get("emails") or {}).get(adresse) or []
    parDomaine = [
        {"client": c, "libelle": libelles.get(c), "poids": n}
        for c, n in ((idx.get("domaines") or {}).get(domaine) or [])
    ]
    return {"exact": exact, "domaine": parDomaine}


def symptomes(motif=""):
    """Symptomes officiels dont le code ou le libelle contient `motif`."""
    out = []
    for s in M.symptomes_officiels():
        code = s if isinstance(s, str) else (s.get("code") or "")
        lib = "" if isinstance(s, str) else (s.get("libelle") or "")
        if not motif or re.search(motif, code + " " + lib, re.I):
            out.append((code, lib))
    return out


def _cli():
    # Le reencodage de stdout se fait ICI et pas a l'import : un module qui
    # remplace le stdout de son appelant casse le wrapper deja pose par celui-ci
    # (« I/O operation on closed file »).
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        return
    quoi = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if quoi == "client":
        c = client(arg) or {}
        print("raison sociale :", c.get("raisonSociale"))
        print("bien pour DIT  :", bien_dit(arg))
        print("--- biens infogerance / exchange")
        for b in biens(arg):
            lib = str(b.get("libelle") or "").upper()
            if "INFOGERANCE" in lib or "EXCHANGE" in lib:
                print("   ", b.get("identifiantFabricant"), "|", b.get("libelle"))
        print("--- interlocuteurs")
        for i in E.interlocuteurs_artis(arg):
            print("   ", i.get("id"), "|", i.get("nom"), i.get("prenom"),
                  "|", i.get("mail") or i.get("email"))
    elif quoi == "dit":
        d = dit(arg) or {}
        print("numero :", d.get("numero"), "| etat :", d.get("etat"),
              "| date :", d.get("date"))
        c = d.get("client") or {}
        print("client :", c.get("code"), c.get("raisonSociale") or c.get("libelle"))
        print("--- commentaire")
        print(d.get("commentaire"))
    elif quoi == "chercher":
        for c in chercher_client(arg):
            print("  ", c["code"], "|", c["nom"])
    elif quoi == "intervenants":
        for i in intervenants(arg):
            print("  ", i["intervention"], "|", i["debut"], "|", i["etat"],
                  "|", i["nom"], i["prenom"], "(", i["matricule"], ")")
    elif quoi == "doublons":
        import datetime
        j = datetime.date.today()
        deb = (j - datetime.timedelta(days=2)).isoformat()
        fin = (j + datetime.timedelta(days=1)).isoformat()
        for x in dits(arg, deb, fin):
            print(x.get("numero"), x.get("date"), x.get("etat"), "|",
                  str(x.get("commentaire") or "")[:80].replace("\n", " "))
    elif quoi == "symptomes":
        for code, lib in symptomes(arg):
            print("  ", code, "|", lib)
    else:
        print(__doc__)


if __name__ == "__main__":
    _cli()
