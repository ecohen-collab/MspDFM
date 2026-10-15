# -*- coding: utf-8 -*-
"""Publier une correction vers TOUS ceux qui lisent la chaine mail -> DIT.

Une regle ou un garde-fou ne vit pas a un seul endroit, et c'est la source
des oublis. Quatre consommateurs, trois chemins differents :

  | qui                     | lit quoi                        | chemin          |
  |-------------------------|---------------------------------|-----------------|
  | moi, sur le poste       | .claude/skills/.../SKILL.md     | le disque       |
  | la routine Cloud        | le depot git de son env         | git pull        |
  | l'agent du conteneur    | /data/agent_consigne.md         | --consigne + az |
  | le robot a regles       | le CODE (mail_regles, mail_dit) | deploy.ps1      |

`.dockerignore` exclut `.claude/`, donc le skill n'est PAS dans l'image : un
deploiement seul ne met jamais la consigne de l'agent a jour. Et
l'environnement de la routine Cloud est persistant : il garde le depot clone
et ne le rafraichit pas tout seul -- constate le 2026-10-15, cinq correctifs
pousses entre 11h04 et 12h36 et toujours pas pris a 12h36.

    python publier.py "message de commit" fichier1 [fichier2 ...]

Jamais `git add .` : ce depot porte des jetons en clair et des donnees
clients que `.gitignore` ne couvre pas (cf. feedback_git_add_dangereux).
Les fichiers se nomment un par un, et le script montre ce qui part.
"""
import io, os, ast, sys, subprocess, pathlib

RACINE = pathlib.Path(__file__).resolve().parent
GIT = r"C:\Program Files\Git\cmd\git.exe"
AZ = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
PY_EXE = sys.executable
SKILL = ".claude/skills/mail-dit/SKILL.md"
SKILL_DIR = ".claude/skills/mail-dit/"


def _run(args, **kw):
    return subprocess.run(args, cwd=str(RACINE), capture_output=True,
                          text=True, **kw)


def main():
    if len(sys.argv) < 3:
        print(__doc__.strip())
        return 2
    message, fichiers = sys.argv[1], sys.argv[2:]

    # 1. rien de casse ne part
    for f in fichiers:
        if f.endswith(".py"):
            try:
                ast.parse(io.open(RACINE / f, encoding="utf-8").read())
            except SyntaxError as e:
                print("REFUS : %s ne compile pas (ligne %s)" % (f, e.lineno))
                return 1
            print("  ast OK   %s" % f)
        elif not (RACINE / f).exists():
            print("REFUS : %s n'existe pas" % f)
            return 1

    # 2. git, fichier par fichier
    r = _run([GIT, "add"] + list(fichiers))
    if r.returncode:
        print("REFUS : git add —", r.stderr.strip()[:200]); return 1
    print("\n--- ce qui part (et rien d'autre) ---")
    print(_run([GIT, "diff", "--cached", "--stat"]).stdout.rstrip())
    r = _run([GIT, "commit", "-q", "-m", message])
    if r.returncode and "nothing to commit" not in (r.stdout + r.stderr):
        print("REFUS : git commit —", (r.stdout + r.stderr).strip()[:200]); return 1
    r = _run([GIT, "push", "-q", "origin", "main"])
    if r.returncode:
        print("REFUS : git push —", r.stderr.strip()[:200]); return 1
    sha = _run([GIT, "log", "--oneline", "-1"]).stdout.strip()
    print("\npousse : %s" % sha)

    # 3. la consigne de l'agent, seulement si le skill a bouge
    if any(f.startswith(SKILL_DIR) for f in fichiers):
        dest = RACINE / "data" / "agent_consigne.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = _run([PY_EXE, "agent_mail_dit.py", "--consigne", str(dest)],
                 env=dict(os.environ, PYTHONPATH=str(RACINE),
                          PYTHONIOENCODING="utf-8"))
        if r.returncode:
            print("consigne : ECHEC —", (r.stdout + r.stderr).strip()[:200])
            return 1
        r = _run([AZ, "storage", "file", "upload",
                  "--account-name", "dfmmspdashboardstor",
                  "--share-name", "msp-data", "--source", str(dest),
                  "--path", "agent_consigne.md", "--auth-mode", "login",
                  "--enable-file-backup-request-intent", "--only-show-errors"])
        if r.returncode:
            print("consigne : ECHEC televersement —", r.stderr.strip()[:200])
            return 1
        print("consigne : republiee sur /data (%d octets)" % dest.stat().st_size)

    # 4. ce que le script NE peut pas faire a ta place
    print("\nRESTE A FAIRE :")
    print("  - la routine Cloud ne prendra ce commit qu'apres un `git pull`")
    print("    dans son environnement (ajoute-le a ses Instructions)")
    if any(not f.startswith(".claude/") for f in fichiers):
        print("  - le conteneur tourne encore sur l'ancienne image :")
        print(r"    .\deploy.ps1  (necessaire pour le robot a regles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
