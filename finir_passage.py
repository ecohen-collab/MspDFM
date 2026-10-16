# -*- coding: utf-8 -*-
"""Finir un passage : type de travail, tags et accuses, en UNE session web.

Sans ce script, finir un passage demandait d'appeler `_poser_type_travail`,
une fonction PRIVEE qui exige un `ctx` interne qu'on ne sait pas fabriquer
depuis l'exterieur. Chacun reconstruisait donc la session a la main — et le
2026-10-15 une routine Cloud s'est fait refuser son script par le classifieur
d'actions, parce qu'il rebatissait `createForward`/`send` avec le jeton Graph
en clair. Trois DIT sont restees en Terrain / Automatique, sans accuse.

    python finir_passage.py lot_fin.json
    python finir_passage.py --retard        # rattrape ce qui traine

Le JSON est une liste. `numero` est le seul champ obligatoire :

    [{"numero": "100177822", "de": "a.duvivier@sodes-sa.com"},
     {"numero": "100177824", "de": "lm.jimenez@orange.fr", "tag": "validee"}]

`tag` vaut "attente", "validee", ou s'omet. `de` sert de destinataire de
secours a l'accuse quand Artis n'a pas encore indexe le demandeur.

UNE seule session web pour tout le lot : chaque connexion prend un siege de
licence, et Artis n'a pas de deconnexion qui le rende.
"""
import io
import json
import pathlib
import sys

RACINE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

import agent_mail_dit as A                 # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")


class _Ctx(object):
    """Le strict necessaire pour `_poser_type_travail` : il ne lit que
    `crees`, `tags_validation` et `actif`."""

    def __init__(self, crees):
        self.crees = crees
        self.tags_validation = []
        self.actif = True


def _journal():
    """Le journal de PROD quand le relais est la, le local sinon.

    Les creations d'une routine Cloud ne sont pas dans le journal du poste :
    c'est pour ca qu'on lit /data en priorite. Sans cette lecture, le
    rattrapage ne verrait que ses propres DIT.
    """
    lignes = []
    try:
        import azure_files
        if azure_files.disponible():
            brut = azure_files.lire("mail_dit_journal.jsonl")
            if brut:
                lignes += brut.decode("utf-8", "replace").splitlines()
    except Exception:                      # noqa: BLE001
        pass
    # ET le local : les creations du passage EN COURS y sont deja, alors
    # qu'elles n'arriveront en prod qu'a la fusion de fin. Sans cette seconde
    # source, `--retard` finirait les retardataires mais pas le passage du
    # jour -- et il faudrait une seconde session web, donc un siege de plus.
    f = RACINE / "mail_dit_journal.jsonl"
    if f.exists():
        lignes += io.open(f, encoding="utf-8").read().splitlines()
    return lignes


# Les DIT que `retard()` n'a pas pu relire — Artis injoignable, jeton refuse.
# Elles ne sont PAS « rien a rattraper » : on ne sait simplement pas. Sans ce
# compteur, une coupure reseau faisait conclure au script que tout etait fini,
# et une DIT restee en Terrain / Automatique serait partie en facturation sans
# que personne ne le voie (constate le 2026-10-15 : dix DIT illisibles d'affilee
# suivies de « rien a rattraper »).
ILLISIBLES = []


def retard(jours=1):
    """Les DIT creees recemment qui sont restees en Terrain / Automatique.

    Une DIT non finie ne se signale pas toute seule : elle part en
    facturation, et un dispatcher la planifie comme une intervention terrain.
    Le passage suivant ne la voit pas non plus — son `ctx.crees` est vide.
    D'ou ce rattrapage, qui relit le journal plutot que la memoire du passage.
    """
    import datetime
    import mail_dit_ecriture as E
    limite = (datetime.date.today()
              - datetime.timedelta(days=max(0, jours - 1))).isoformat()
    nums = []
    for l in _journal():
        l = l.strip()
        if not l:
            continue
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("action") != "creation":
            continue
        if (d.get("ts") or "")[:10] < limite:
            continue
        n = d.get("dit")
        if n and n not in [x["numero"] for x in nums]:
            nums.append({"numero": n, "de": d.get("mail_de") or "",
                         "client": d.get("client_code") or ""})
    a_finir = []
    del ILLISIBLES[:]
    for d in nums:
        f = E.relire_dit(d["numero"]) or {}
        if f.get("erreur"):
            print("  %s : illisible (%s)" % (d["numero"], str(f["erreur"])[:60]))
            ILLISIBLES.append(d["numero"])
            continue
        if f.get("type_travail") != "Hotline" or f.get("facturation") != "NonFacturable":
            # Le tag se DEDUIT du motif, comme a la creation : le rattrapage
            # ne doit pas poser un tag different de celui qu'on aurait mis.
            try:
                d["tag"] = E.tag_a_la_creation(d.get("client") or "",
                                               d.get("de") or "",
                                               f.get("commentaire") or "")
            except Exception:              # noqa: BLE001
                d["tag"] = None
            d["a_valider"] = d["tag"] == "attente"
            print("  %s : %s / %s — a finir (tag %s)"
                  % (d["numero"], f.get("type_travail"), f.get("facturation"),
                     d["tag"] or "aucun"))
            a_finir.append(d)
    return a_finir


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    if sys.argv[1] == "--retard":
        jours = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        print("recherche des DIT non finies sur %d jour(s)..." % jours)
        lot = retard(jours)
        if ILLISIBLES:
            # Ne JAMAIS annoncer « rien a rattraper » sur des DIT qu'on n'a pas
            # pu lire : l'absence de reponse d'Artis n'est pas une preuve que
            # le type de travail est pose. Code 1 pour que l'appelant le voie.
            print("ATTENTION : %d DIT illisible(s), etat inconnu — %s"
                  % (len(ILLISIBLES), ", ".join(ILLISIBLES)))
            print("relancer `finir_passage.py --retard` quand Artis repond.")
            if not lot:
                return 1
        elif not lot:
            print("rien a rattraper : toutes les DIT sont en Hotline / Non facturable")
            return 0
        print("%d DIT a finir" % len(lot))
    else:
        lot = json.loads(io.open(sys.argv[1], encoding="utf-8").read())
    if isinstance(lot, dict):
        lot = [lot]
    crees = []
    for d in lot:
        n = str(d.get("numero") or "").strip()
        if not n:
            print("REFUS : une entree sans numero")
            return 1
        crees.append({"numero": n, "client": d.get("client") or "",
                      "uo": d.get("uo") or "", "objet": d.get("objet") or "",
                      "de": d.get("de") or "",
                      "a_valider": d.get("tag") == "attente",
                      "tag": d.get("tag")})
    print("a finir :", ", ".join(c["numero"] for c in crees))
    res = A._poser_type_travail(_Ctx(crees))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    # Un echec se voit dans le code de sortie, pas seulement a l'ecran : une
    # DIT laissee en Terrain / Automatique part en facturation chez un
    # dispatcher qui la planifie.
    rate = (res.get("echecs") or []) + (res.get("confirmations_echecs") or [])
    rate += (res.get("tags") or {}).get("echecs") or []
    return 1 if (rate or not res.get("fait")) else 0


if __name__ == "__main__":
    sys.exit(main())
