# -*- coding: utf-8 -*-
"""Copier dans le depot ce qu'Elliot m'a appris, pour que la routine l'ait.

Ma memoire vit dans `~/.claude/projects/.../memory/` : 97 fiches relues a
chaque session, et c'est la SEULE raison pour laquelle je ne redemande pas a
Elliot ce qu'il a deja tranche. Une routine Cloud ne clone que le depot : elle
n'en voyait aucune, redecouvrait tout a chaque passage, et devant le doute
s'abstenait -- « client non identifiable », « accord requis », 20 mails laisses.

Les fiches sont copiees comme fichiers CONSULTABLES, jamais charges : 113 Ko
s'ajouteraient a une consigne qui en fait deja 100. `assembler_consigne()` lit
une liste fixe, donc ce dossier ne la gonfle pas. La chaine y cherche quand
une regle ou une autorisation lui manque.

    python exporter_memoire.py          # copie, puis dit quoi publier
"""
import io
import os
import pathlib
import re
import shutil
import sys

RACINE = pathlib.Path(__file__).resolve().parent
SOURCE = pathlib.Path(os.path.expanduser(
    "~/.claude/projects/C--Users-ecohen-OneDrive---DFM-Bureau-Dashboard-IA/memory"))
CIBLE = RACINE / ".claude" / "skills" / "mail-dit" / "memoire"

# Ce qui concerne la chaine. Le reste (Yooz, VEEAM, TD SYNNEX, Sophos...) ne
# lui sert pas et alourdirait le depot pour rien.
MOTS = ("mail", "dit", "bal", "artis", "valid", "commentaire", "client",
        "intervenant", "newsletter", "telecom", "soc", "phishing", "doublon",
        "priorite", "bien", "contrat", "metier", "piece", "transfert",
        "signaler", "robot", "routine", "elliot")
# Un secret en clair ne part jamais dans le depot, meme prive.
SECRET = re.compile(r"password\s*[:=]|api[_-]?key\s*[:=]|secret\s*[:=]"
                    r"|token\s*[:=]\s*[A-Za-z0-9]{16}|sk-ant", re.I)


def main():
    if not SOURCE.exists():
        print("memoire introuvable : %s" % SOURCE)
        return 1
    CIBLE.mkdir(parents=True, exist_ok=True)
    for vieux in CIBLE.glob("*.md"):
        vieux.unlink()
    copiees, ecartees = 0, []
    for f in sorted(SOURCE.glob("*.md")):
        nom = f.name.lower()
        if nom != "memory.md" and not any(m in nom for m in MOTS):
            continue
        texte = io.open(f, encoding="utf-8").read()
        if SECRET.search(texte):
            ecartees.append(f.name)
            continue
        io.open(CIBLE / f.name, "w", encoding="utf-8", newline="\n").write(texte)
        copiees += 1
    octets = sum(p.stat().st_size for p in CIBLE.glob("*.md"))
    print("copiees : %d fiches (%d octets) -> %s"
          % (copiees, octets, CIBLE.relative_to(RACINE)))
    for e in ecartees:
        print("  ECARTEE (secret en clair) :", e)
    print()
    print("a publier :")
    print("  python publier.py \"<message>\" .claude/skills/mail-dit/memoire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
