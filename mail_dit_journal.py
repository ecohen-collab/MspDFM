# -*- coding: utf-8 -*-
"""Journal des actions de la chaine mail -> DIT.

Une ligne = une action, ecrite au moment ou elle est faite. Le fichier est du
JSONL : on APPEND, on ne reecrit jamais. C'est ce qui permet au robot d'ecrire
pendant que le dashboard lit, sans verrou et sans perdre d'historique.

Elliot, le 2026-10-01 : « un onglet unique avec tout ce qui a ete fait, par
exemple : mail de xxx@xxx.fr du 02/09/2023 a 14h54 -----> DIT wxxxxx creee ;
comme ca ligne par ligne ». Les rapports de passage vivaient dans un terminal
et mouraient avec lui ; le journal leur donne un endroit ou rester.

    import mail_dit_journal as J
    J.ajouter("creation", mail_de="x@y.fr", mail_objet="...", dit="100176099")
    J.lire(limite=200)
"""
import json
import os
import pathlib
import datetime

DOSSIER = pathlib.Path(__file__).resolve().parent
DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR") or DOSSIER)
FICHIER = DATA_DIR / "mail_dit_journal.jsonl"

# Les seules actions admises. Une action hors liste serait invisible dans les
# filtres de la page : mieux vaut echouer ici que produire une ligne muette.
ACTIONS = (
    "creation",     # une DIT a ete creee
    "complement",   # un mail a ete ajoute au commentaire d'une DIT existante
    "rangement",    # le mail a ete classe sans DIT (telecom, securite, infos...)
    "transfert",    # le mail a ete transmis en interne (les 3 cas autorises)
    "annulation",   # une DIT a ete annulee
    "laisse",       # le mail reste en boite, avec la raison
    "piece",        # une piece jointe a ete versee sur une DIT
)


def _maintenant():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def ajouter(action, **champs):
    """Ajoute une ligne au journal. Rend la ligne ecrite.

    Champs usuels (tous optionnels, mais plus il y en a, plus la ligne parle) :
      mail_de, mail_objet, mail_recu, client_code, client_nom,
      dit, detail, dossier, destinataire, par
    """
    if action not in ACTIONS:
        raise ValueError("action inconnue : %s (attendu %s)"
                         % (action, "/".join(ACTIONS)))
    # `ts` explicite : seulement pour une reconstitution a posteriori, ou
    # l'heure de l'action est connue mais n'est pas l'heure d'ecriture.
    ligne = {"ts": champs.pop("ts", None) or _maintenant(), "action": action}
    for cle, val in champs.items():
        if val not in (None, ""):
            ligne[cle] = val
    ligne.setdefault("par", "robot")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # append : deux ecrivains concurrents n'ecrasent rien, au pire les lignes
    # s'intercalent. Une reecriture complete, elle, perdrait l'autre.
    with open(FICHIER, "a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    return ligne


def lire(limite=500, action=None, depuis=None, client=None):
    """Les dernieres lignes du journal, de la plus recente a la plus ancienne.

    `depuis` : borne basse sur `ts` (ISO, comparaison lexicale, ce qui marche
    parce que les horodatages sont tous au meme format).
    """
    if not FICHIER.exists():
        return []
    out = []
    with open(FICHIER, encoding="utf-8") as f:
        for brut in f:
            brut = brut.strip()
            if not brut:
                continue
            try:
                lig = json.loads(brut)
            except ValueError:
                # Une ligne tronquee (coupure en plein write) ne doit pas
                # rendre tout le journal illisible.
                continue
            if action and lig.get("action") != action:
                continue
            if depuis and (lig.get("ts") or "") < depuis:
                continue
            if client and client not in (str(lig.get("client_code") or ""),
                                         str(lig.get("client_nom") or "")):
                continue
            out.append(lig)
    out.reverse()
    return out[:limite] if limite else out


def compte(depuis=None):
    """Combien d'actions de chaque type, pour l'en-tete de la page."""
    res = {a: 0 for a in ACTIONS}
    for lig in lire(limite=0, depuis=depuis):
        a = lig.get("action")
        if a in res:
            res[a] += 1
    res["total"] = sum(res[a] for a in ACTIONS)
    return res
