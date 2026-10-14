# -*- coding: utf-8 -*-
"""Remonter le journal local vers `/data`, sans Azure CLI.

Le journal (`mail_dit_journal.jsonl`) s'ecrit la ou tourne la chaine : sur le
poste d'Elliot, a la racine du repo ; dans une session distante, dans le
repertoire de travail. L'onglet « Journal mail vers DIT » du dashboard lit,
lui, `/data/mail_dit_journal.jsonl` sur Azure Files. Sans remontee, il
n'affiche rien de ce qui a ete fait — constate le 2026-10-06 : 939 lignes
locales, 118 en prod, et Elliot ne voyait « rien ».

Jusqu'ici la remontee passait par `az storage file upload --auth-mode login`,
donc par l'identite d'Elliot. Une session distante n'a pas d'identite : elle
a une SAS (voir `azure_files`). Ce module fait le meme travail avec, et il
s'utilise aussi bien depuis le poste.

**On fusionne toujours, on n'ecrase jamais.** Plusieurs ecrivains touchent ce
fichier — moi, le robot a regles, l'agent du conteneur. Un `upload` sans
fusion ferait disparaitre les lignes des autres.

    python journal_sync.py          # fusionne et televerse, affiche le compte
"""
import json
import mail_dit_journal as J
import azure_files

DISTANT = "mail_dit_journal.jsonl"

# Deux lignes sont la meme action si tout ceci coincide. La seconde precise de
# l'horodatage ne compte pas : deux ecrivains ne produisent pas la meme ligne
# a la meme seconde par hasard, mais un meme passage rejoue peut decaler les
# millisecondes.
def _cle(l):
    return (str(l.get("ts", ""))[:19], l.get("action"), l.get("mail_de"),
            l.get("mail_objet"), l.get("dit"), l.get("dossier"))


def _lignes(brut):
    out = []
    for ligne in (brut or "").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            out.append(json.loads(ligne))
        except Exception:                # noqa: BLE001
            continue                     # une ligne illisible ne bloque pas
    return out


def fusionner(prod, local):
    """Prod + local, dedoublonne, trie par horodatage. Prod gagne a egalite."""
    vues, sortie = set(), []
    for source in (prod, local):
        for l in source:
            c = _cle(l)
            if c in vues:
                continue
            vues.add(c)
            sortie.append(l)
    sortie.sort(key=lambda l: str(l.get("ts", "")))
    return sortie


def synchroniser(verbeux=True):
    """Fusionne le journal local avec celui de `/data` et le repose la-bas."""
    if not azure_files.disponible():
        raise RuntimeError(
            "AZ_FILES_ACCOUNT / AZ_FILES_SHARE / AZ_FILES_SAS ne sont pas "
            "posees : impossible de remonter le journal sans Azure CLI.")
    brut = azure_files.lire(DISTANT)
    prod = _lignes(brut.decode("utf-8", "replace") if brut else "")
    try:
        local = _lignes(J.FICHIER.read_text(encoding="utf-8"))
    except Exception:                    # noqa: BLE001
        local = []
    tout = fusionner(prod, local)

    # Garde-fou : une fusion ne doit JAMAIS perdre une ligne de la prod.
    perdues = {_cle(l) for l in prod} - {_cle(l) for l in tout}
    if perdues:
        raise RuntimeError("fusion incorrecte : %d ligne(s) de prod perdue(s)"
                           % len(perdues))

    azure_files.ecrire(DISTANT, "".join(
        json.dumps(l, ensure_ascii=False) + "\n" for l in tout))
    etat = {"prod": len(prod), "local": len(local), "fusion": len(tout),
            "perdues": 0}
    if verbeux:
        print("journal : prod %(prod)d, local %(local)d, fusion %(fusion)d, "
              "perdues %(perdues)d" % etat)
    return etat


if __name__ == "__main__":
    synchroniser()
