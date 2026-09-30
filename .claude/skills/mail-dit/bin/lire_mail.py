# -*- coding: utf-8 -*-
"""Corps texte d'un mail, et inventaire de ses pieces jointes.

Le `summary` rendu par la recherche est TRONQUE : il ne sert qu'a trier
grossierement. Le commentaire d'une DIT se construit sur ce corps-ci.

    python lire_mail.py "<internetMessageId>"
    python lire_mail.py --id "<id Graph complet>"
    python lire_mail.py --id "<id>" --pieces DOSSIER   # telecharge les PJ utiles
"""
import base64
import html
import io
import os
import re
import sys

import requests

sys.path.insert(0, r"C:\Users\ecohen\OneDrive - DFM\Bureau\Dashboard IA")
import mail_auth                          # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")

GRAPH = "https://graph.microsoft.com/v1.0"


def _entetes():
    return {"Authorization": "Bearer %s" % mail_auth.access_token()}


def en_texte(html_ou_texte):
    """HTML -> texte lisible, en gardant les sauts de ligne du message."""
    c = html_ou_texte or ""
    c = re.sub(r"(?is)<(script|style).*?</\1>", "", c)
    c = re.sub(r"(?i)<br\s*/?>", "\n", c)
    c = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", c)
    c = re.sub(r"(?s)<[^>]+>", "", c)
    c = html.unescape(c).replace("\u00a0", " ")
    c = re.sub(r"[ \t]+", " ", c)
    return re.sub(r"\n{3,}", "\n\n", c).strip()


def par_message_id(internet_message_id):
    """Le mail depuis son internetMessageId (stable, contrairement a l'id)."""
    r = requests.get(GRAPH + "/me/messages", headers=_entetes(), timeout=60,
                     params={"$filter": "internetMessageId eq '%s'"
                                        % internet_message_id,
                             "$select": "id,subject,from,toRecipients,"
                                        "receivedDateTime,body,hasAttachments"})
    v = (r.json() or {}).get("value") or []
    return v[0] if v else None


def par_id(message_id):
    r = requests.get(GRAPH + "/me/messages/%s" % message_id, headers=_entetes(),
                     timeout=60,
                     params={"$select": "id,subject,from,toRecipients,"
                                        "receivedDateTime,body,hasAttachments"})
    return r.json() if r.status_code == 200 else None


def pieces(message_id, dossier=None, mini=5000):
    """Pieces jointes NON inline. Les signatures et bandeaux d'organisation
    sont inline : on les ignore. `dossier` -> ecrit les fichiers sur disque."""
    r = requests.get(GRAPH + "/me/messages/%s/attachments" % message_id,
                     headers=_entetes(), timeout=180)
    out = []
    for a in (r.json() or {}).get("value") or []:
        if a.get("isInline") or not a.get("contentBytes"):
            continue
        if (a.get("size") or 0) < mini:
            continue
        data = base64.b64decode(a["contentBytes"])
        chemin = None
        if dossier:
            os.makedirs(dossier, exist_ok=True)
            chemin = os.path.join(dossier, a["name"])
            with open(chemin, "wb") as f:
                f.write(data)
        out.append({"nom": a["name"], "type": a.get("contentType"),
                    "taille": len(data), "chemin": chemin})
    return out


def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    dossier = None
    if "--pieces" in args:
        i = args.index("--pieces")
        dossier = args[i + 1]
        args = args[:i] + args[i + 2:]
    if args[0] == "--id":
        msg = par_id(args[1])
    else:
        msg = par_message_id(args[0])
    if not msg:
        print("mail introuvable")
        return
    print("ID      :", msg["id"])
    print("DE      :", (msg.get("from") or {}).get("emailAddress", {}))
    print("RECU    :", msg.get("receivedDateTime"))
    print("SUJET   :", msg.get("subject"))
    print("=== CORPS ===")
    print(en_texte((msg.get("body") or {}).get("content")))
    if msg.get("hasAttachments"):
        print("=== PIECES UTILES ===")
        for p in pieces(msg["id"], dossier):
            print("   ", p["nom"], p["type"], p["taille"], p["chemin"] or "")


if __name__ == "__main__":
    _cli()
