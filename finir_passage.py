# -*- coding: utf-8 -*-
"""Finir un passage : type de travail, tags et accuses, en UNE session web.

Sans ce script, finir un passage demandait d'appeler `_poser_type_travail`,
une fonction PRIVEE qui exige un `ctx` interne qu'on ne sait pas fabriquer
depuis l'exterieur. Chacun reconstruisait donc la session a la main — et le
2026-10-15 une routine Cloud s'est fait refuser son script par le classifieur
d'actions, parce qu'il rebatissait `createForward`/`send` avec le jeton Graph
en clair. Trois DIT sont restees en Terrain / Automatique, sans accuse.

    python finir_passage.py lot_fin.json

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


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
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
