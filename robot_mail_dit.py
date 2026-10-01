# -*- coding: utf-8 -*-
"""Un passage de la chaine mail -> DIT, sans personne devant l'ecran.

C'est le point d'entree du Container Apps Job. Il ne decide rien lui-meme :
tout le jugement est deja dans `mail_dit.decider_seul` et
`mail_dit.traiter_automatiquement`, murís pendant des semaines. Ce fichier ne
fait que trois choses que la session interactive faisait a sa place :

1. **s'authentifier en applicatif** (`mail_bal_apponly`), parce qu'aucun
   humain ne passera par un device code a 3 h du matin ;
2. **lancer un passage** : relire la boite, creer les propositions, executer
   ce que les regles autorisent ;
3. **laisser une trace** : le journal `mail_dit_journal` et un battement, pour
   qu'un robot muet se voie.

Pourquoi un battement
---------------------
Un robot qui s'arrete ne previent pas. La boite s'est remplie de 45 mails le
2026-10-01 parce que le cron de session etait mort sans un bruit. Le job ecrit
donc son heure de passage sur /data a chaque execution : si le fichier
vieillit, c'est que plus rien ne tourne, et ca se voit depuis le dashboard.

Modes
-----
`ROBOT_MODE=observation` (defaut) : lit, propose, journalise, **n'ecrit ni
dans Artis ni dans la boite**. C'est le mode d'un premier deploiement, le
temps de comparer ce que le robot aurait fait avec ce qu'un humain fait.

`ROBOT_MODE=actif` : execute en plus les decisions que les regles autorisent.
L'ecriture reste soumise au drapeau `/data/mail_dit_ecriture.txt` — le robot
ne peut pas se l'accorder lui-meme.

Usage :
    python robot_mail_dit.py            # un passage, puis sortie
    python robot_mail_dit.py --boucle   # passage toutes les ROBOT_PERIODE s
"""
import datetime
import json
import os
import pathlib
import sys
import time

RACINE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(RACINE))

DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR") or RACINE)
BATTEMENT = DATA_DIR / "robot_mail_dit_battement.json"
MODE = (os.environ.get("ROBOT_MODE") or "observation").strip().lower()
PERIODE = int(os.environ.get("ROBOT_PERIODE") or 900)


def log(*a):
    quand = datetime.datetime.now().strftime("%H:%M:%S")
    print(quand, *a, flush=True)


def _battre(resume):
    """Ecrit l'heure du passage et son resume. Un fichier qui vieillit est une
    alerte : c'est le seul moyen de voir qu'un robot s'est tu."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = BATTEMENT.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            dict(resume, quand=datetime.datetime.now().astimezone()
                 .isoformat(timespec="seconds"), mode=MODE),
            ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(BATTEMENT)
    except Exception as exc:                       # noqa: BLE001
        log("battement non ecrit :", str(exc)[:120])


def passage():
    """Un passage complet. Rend un resume, ne leve jamais : le job doit
    survivre a une panne d'Artis ou de Graph et reessayer au tour suivant."""
    resume = {"ok": False}
    try:
        import mail_bal_apponly as APP
        APP.activer(log=log)

        import mail_dit as M
        import mail_dit_journal as J

        # Le re-tri d'abord : une regle corrigee doit nettoyer la file
        # existante, pas seulement filtrer les mails a venir.
        try:
            M.retrier()
        except Exception as exc:                   # noqa: BLE001
            log("retri impossible :", str(exc)[:140])

        nouvelles = M.cycle()
        compte = M.compteurs()
        log("propositions : %d nouvelle(s) | file : %s" % (nouvelles, compte))
        resume.update(nouvelles=nouvelles, file=compte)

        if MODE == "actif":
            import mail_dit_ecriture as E
            if not E.ecriture_armee():
                log("mode actif demande mais le drapeau d'ecriture est absent "
                    "— rien ne sera ecrit")
                resume["ecriture_armee"] = False
            else:
                faits = M.traiter_automatiquement(log=log)
                log("traitements executes :", faits)
                resume.update(traites=faits, ecriture_armee=True)
        else:
            log("mode observation : rien n'est ecrit")

        resume["journal"] = J.compte()
        resume["ok"] = True
    except Exception as exc:                       # noqa: BLE001
        log("PASSAGE EN ECHEC :", repr(exc)[:300])
        resume["erreur"] = repr(exc)[:300]
    _battre(resume)
    return resume


def main():
    log("robot mail -> DIT | mode %s | data %s" % (MODE, DATA_DIR))
    if "--boucle" in sys.argv:
        while True:
            passage()
            log("prochain passage dans %d s" % PERIODE)
            time.sleep(PERIODE)
    else:
        r = passage()
        sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
