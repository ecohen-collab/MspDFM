# -*- coding: utf-8 -*-
"""Cree une ou plusieurs DIT depuis un fichier JSON de proprietes.

Remplace les scripts creer_lotNN.py jetables : meme enchainement a chaque fois,
apercu obligatoire avant ecriture, numero jamais devine.

    python creer_dit.py lot.json            # APERCU, n'ecrit rien
    python creer_dit.py lot.json --go       # cree pour de vrai

    --forcer-doublon   NE JAMAIS utiliser de ma propre initiative. Uniquement
                       sur demande explicite d'Elliot, apres lui avoir montre
                       pourquoi le verrou se trompe. Le verrou compare les 60
                       premiers caracteres du texte du client sur 24 h
                       glissantes : deux demandes de meme forme pour des
                       beneficiaires differents se ressemblent. Tout le reste
                       du chemin de creation reste identique — c'est justement
                       l'interet de passer par ici plutot que de POSTer a la
                       main (le 2026-09-30, un POST bricole a cote a produit
                       une DIT en nature 10 / typeDit 01 au lieu de 11 / 05).

Le JSON est un objet, ou une liste d'objets :

    [{
      "id": "CLIENT-SUJET-30092026",     identifiant externe, 20 car. utiles
      "client": "9599",
      "bien": "9599 - INFOGERANCE",      omettre si le client n'en a aucun
      "pid_interlocuteur": 158057,       omettre si expediteur interne @dfm.fr
      "objet": "Objet du mail du client",
      "de": "prenom.nom@client.fr",
      "de_nom": "Prenom NOM",
      "priorite": "03",                  03 personne bloquee / 02 une / 01 tous
      "symptome": "ADM-365-EXC-BAL",
      "uo": "HLN3",                      HLN3 si symptome ADM-*, HLI sinon
      "categorie": "hotline",
      "date_dit": "2026-09-30T12:23:13", date du mail en heure de Paris
      "commentaire": "texte du client MOT POUR MOT, signature exclue",
      "pieces_fichiers": ["C:/.../capture.png"]   optionnel, jointes apres creation
    }]

La cle s'appelle `pieces_fichiers` et NON `pieces` : `mail_dit_ecriture`
utilise deja `prop["pieces"]` pour les pieces du mail, sous forme de dicts, et
lui passer des chemins fait echouer la construction du commentaire.

Apres coup, poser le type de travail Hotline / Non facturable sur les numeros
rendus, dans UNE SEULE session web (voir type_travail.py).
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, r"C:\Users\ecohen\OneDrive - DFM\Bureau\Dashboard IA")
import mail_dit_ecriture as E             # noqa: E402

# Le journal alimente l'onglet << Journal mail vers DIT >> du dashboard.
# Import defensif : une creation ne doit jamais echouer parce que le
# journal manque.
try:
    import mail_dit_journal as J
except Exception:                          # noqa: BLE001
    J = None

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")


def _controles(prop):
    """Les verifications que j'oublie quand j'ecris le script a la main."""
    alertes = []
    sym = (prop.get("symptome") or "").upper()
    uo = (prop.get("uo") or "").upper()
    if sym.startswith("ADM-") and uo != "HLN3":
        alertes.append("symptome %s -> l'UO devrait etre HLN3, pas %s" % (sym, uo))
    if not sym.startswith("ADM-") and uo == "HLN3":
        alertes.append("symptome %s -> l'UO devrait etre HLI" % sym)
    if prop.get("priorite") not in ("01", "02", "03"):
        alertes.append("priorite %r inattendue (01 tous / 02 une / 03 aucune)"
                       % prop.get("priorite"))
    if not prop.get("bien"):
        alertes.append(
            "DIT SANS BIEN : verifier que le client a bien un contrat Info "
            "(artis.py bien_dit). S'il n'en a pas, NE PAS CREER — transferer le "
            "mail a cdamato@dfm.fr avec Clifford en copie, puis ranger en "
            "01 - DIT (regle Elliot du 2026-10-01, DIT 100175462 annulee).")
    de = (prop.get("de") or "").lower()
    if de.endswith("@dfm.fr") and prop.get("pid_interlocuteur"):
        alertes.append("expediteur interne @dfm.fr : la DIT doit partir SANS "
                       "demandeur")
    return alertes


def creer(prop, go):
    alertes = _controles(prop)
    for a in alertes:
        print("  ATTENTION     :", a)

    r = E.creer_dit(prop, confirmer=go)
    print("  bloquants      :", r.get("bloquants"))
    print("  avertissements :", r.get("avertissements"))
    corps = r.get("corps") or {}
    print("  corps          :", json.dumps(
        {k: v for k, v in corps.items() if k != "commentaire"},
        ensure_ascii=False))
    if not go:
        print("  commentaire    :\n" + (corps.get("commentaire") or "(vide)"))
        print("  APERCU — rien n'a ete ecrit")
        return None

    if r.get("erreur") == "doublon evite":
        print("  DOUBLON EVITE  : DIT %s couvre deja cet incident"
              % r.get("doublon_de"))
        print("  -> ne pas contourner. Signaler, laisser le mail en boite.")
        return None
    if not r.get("envoye"):
        print("  ERREUR         :", r.get("erreur"), str(r.get("http"))[:200])
        return None

    numero = str(r["numero"])
    print("  CREEE          :", numero)

    # Journaliser TOUT DE SUITE : si la pose d'UO ou d'une piece jointe
    # echoue plus bas, la DIT existe quand meme et doit figurer au journal.
    if J is not None:
        try:
            J.ajouter("creation",
                      mail_de=prop.get("de"),
                      mail_objet=prop.get("objet"),
                      mail_recu=prop.get("date_dit"),
                      client_code=prop.get("client"),
                      dit=numero,
                      detail="%s / %s / priorite %s" % (prop.get("symptome"),
                                                        prop.get("uo"),
                                                        prop.get("priorite")),
                      dossier="01 - DIT ARTIS SUPPORT",
                      par="claude-code")
        except Exception as exc:           # noqa: BLE001
            print("  JOURNAL        : non ecrit (%s)" % str(exc)[:80])

    # L'UO se pose sur le numero d'INTERVENTION = numero de DIT + "01".
    intervention = numero + "01"
    for _ in range(4):
        u = E.poser_uo(intervention, prop["uo"], confirmer=True)
        if u.get("envoye"):
            print("  UO %-5s     : posee sur %s" % (prop["uo"], intervention))
            break
        time.sleep(6)
    else:
        print("  UO %-5s     : ECHEC %s" % (prop["uo"],
                                            str(u.get("erreur"))[:140]))

    for chemin in prop.get("pieces_fichiers") or []:
        if not os.path.exists(chemin):
            print("  PJ introuvable :", chemin)
            continue
        with open(chemin, "rb") as f:
            data = f.read()
        ext = os.path.splitext(chemin)[1].lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".pdf": "application/pdf"}.get(
                    ext, "application/octet-stream")
        p = E.joindre_fichier(numero, os.path.basename(chemin), data, mime,
                              confirmer=True)
        print("  PJ             :", os.path.basename(chemin), "->",
              p.get("envoye"))

    return numero


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    go = "--go" in sys.argv
    forcer = "--forcer-doublon" in sys.argv
    # utf-8-sig : PowerShell ecrit un BOM que json.load refuse en utf-8 strict.
    with open(sys.argv[1], encoding="utf-8-sig") as f:
        lot = json.load(f)
    if isinstance(lot, dict):
        lot = [lot]

    if forcer:
        # Derogation accordee par Elliot sur un faux positif verifie. On
        # neutralise LE SEUL verrou concerne ; le corps de la DIT, ses defauts
        # (typeDit 05, nature 11, domaine INFO) et le registre restent ceux du
        # chemin normal.
        print("!! --forcer-doublon : verrou anti-doublon neutralise sur ce lot")
        E.dit_jumelle = lambda *a, **k: ""

    print("armee :", E.ecriture_armee(), "| GO :", go)
    if len(lot) > 2:
        print("ATTENTION : %d creations demandees, le garde-fou est de 2 par "
              "passage." % len(lot))
    print("=" * 70)

    numeros = []
    precedent_client = None
    for prop in lot:
        # Pas deux DIT pour le meme client a moins de 60 s.
        if go and precedent_client == prop.get("client"):
            print("\n... meme client, attente 65 s")
            time.sleep(65)
        elif go and precedent_client is not None:
            time.sleep(5)
        precedent_client = prop.get("client")

        print("\n--- client %s / %s" % (prop.get("client"),
                                        (prop.get("objet") or "")[:55]))
        n = creer(prop, go)
        if n:
            numeros.append(n)

    if numeros:
        print("\n" + "=" * 70)
        print("A FAIRE : type de travail Hotline / Non facturable, UNE session :")
        print("  python type_travail.py %s --go" % " ".join(numeros))


if __name__ == "__main__":
    main()
