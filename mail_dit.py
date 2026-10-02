# -*- coding: utf-8 -*-
"""
Moteur « mail support -> proposition de DIT Artis ».

Surveille en quasi temps reel les mails de support@dfm.fr et produit des
PROPOSITIONS de DIT. **N'ecrit rien** : ni dans la boite mail (GET seulement),
ni dans Artis (aucun POST tant qu'Elliot ne l'a pas explicitement autorise).

Pourquoi du delta et pas un cycle quotidien : les demandes doivent etre traitees
vite. Graph expose des requetes `delta` qui ne renvoient que ce qui a change
depuis le dernier appel — quelques centaines d'octets quand rien n'a bouge. Un
sondage a la minute coute donc ~1440 requetes minuscules par jour, soit 0,1 % du
budget d'etranglement de Graph, et la classification elle-meme est locale
(regex + dictionnaire), donc gratuite.

Pourquoi surveiller DEUX dossiers : la boite de reception est videe en continu
par les agents (9 mails restants sur 14 jours). Un mail classe entre deux
sondages serait invisible si on ne regardait que l'Inbox. On suit donc aussi
« 01 - DIT ARTIS SUPPORT », et on dedoublonne sur `internetMessageId`, stable
lors d'un deplacement (contrairement a l'`id` du message).

Perimetre impose par Elliot (2026-09-08) : ces deux dossiers, rien d'autre.
"""
import os, json, re, time, threading, datetime, pathlib, requests

from mail_auth import access_token
from mail_regles import (RE_ALERTE_EQUIPEMENT, corps_de_journal,
                         classer, trouver_client, body_utile, deviner_domaine,
                         deviner_priorite, categorie, CATEGORIES, addr,
                         RE_CODECLI, INTERNE, deviner_symptome,
                         historique_cite, code_client_du_texte,
                         RE_SUPERVISION, destinataire_interne, RE_DIT,
                         images_du_corps, sans_html, images_du_html,
                         image_de_contenu, RE_AUTRE_METIER,
                         RE_AUTRE_METIER_CORPS, RE_ADRESSE, envoi_de_masse,
                         RE_REPONSE)

GRAPH = "https://graph.microsoft.com/v1.0"
# Separateur du fil cite dans le commentaire du DIT. Sert aussi de
# marque : un commentaire qui le contient deja n'est pas complete deux fois.
# Elliot, 2026-09-22, en lisant le commentaire de la DIT 100174835 : « c'est
# beaucoup trop brouillon, tout est colle, c'est illisible, faudrait bien
# separer l'historique et la signature ». Une ligne de tirets pleine largeur
# se voit, meme quand l'ecran d'Artis mange les lignes vides — ce qu'il fait.
SEPARATEUR_HISTO = ("=" * 58 + chr(10)
                    + "HISTORIQUE DU MAIL (messages precedents)" + chr(10)
                    + "=" * 58)
# « RE: », « TR: », « Fwd: » — a retirer avant de comparer deux objets.
RE_PREFIXE_REPONSE = re.compile(r"^\s*((re|tr|fw|fwd|rep)\s*:\s*)+", re.I)
# En prod DATA_DIR=/data (Azure Files). En local, ./data comme les autres
# scripts mail, pour retrouver le meme jeton et le meme index client.
# Ancre sur CE fichier, pas sur le repertoire courant : les outils de
# .claude/skills/mail-dit/bin/ tournent depuis leur propre dossier, ou un
# "./data" relatif n'existe pas. Le drapeau d'ecriture devenait alors
# invisible et toute creation echouait sans dire pourquoi (2026-10-01).
DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR")
                        or (pathlib.Path(__file__).resolve().parent / "data"))
PROPS_FILE = DATA_DIR / "mail_dit_props.json"
NUMEROS_FILE = DATA_DIR / "mail_dit_numeros.json"   # numeros de DIT connus
COTATION_REFS = DATA_DIR / "cotation_refs.json"     # {clients:[{code,nom}]}
# Appris depuis 7 284 DIT / 2 200 clients (cf. _mail_learn2.py) :
CLIENT_ATTRS  = DATA_DIR / "mailcorpus" / "client_attrs.json"
CLIENT_SYMPT  = DATA_DIR / "mailcorpus" / "client_symptomes.json"
# Registre des DIT creees par l'outil, et journal des essais techniques.
REGISTRE_FILE = DATA_DIR / "mail_dit_registre.json"
ESSAIS_FILE   = DATA_DIR / "mail_dit_essais.json"
# Un seul dossier est surveille en temps reel : la boite de reception, qui EST
# la file d'attente (27 mails). Le dossier des traites contient 20 729 mails et
# n'a servi qu'a l'apprentissage hors ligne -- le scanner a chaque cycle coutait
# plus de 2 000 requetes pour une information qu'on deduit autrement : un mail
# qui a quitte la boite de reception a ete traite par un humain.
DOSSIER_ENTREE = "Inbox"
DOSSIER_TRAITE = "01 - DIT ARTIS SUPPORT"   # apprentissage seulement, hors cycle
DOSSIERS = [DOSSIER_ENTREE]

SELECT = ("id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
          "conversationId,internetMessageId,hasAttachments,importance,bodyPreview,body")

# UO : HLN3 pour les taches d'administration, HLI sinon. Ce n'est PAS un
# attribut du client -- l'UO dit qui traite le ticket (HLN3 = equipe niveau 3,
# HLI = hotline niveau 1), donc elle depend du genre de demande. Elle se deduit
# du symptome, choisi par l'agent : voir uo_pour().
# On ne cree QUE des tickets informatiques. Elliot l'a dit deux fois, la
# seconde apres m'avoir vu laisser passer TEL : « JAMAIS on ne cree de ticket
# AUTRE que informatique ». Tous les autres domaines Artis (HOTLINE =
# Impression, SEC = Securite, TEL = Telephonie, AUD, KM...) ont leurs propres
# equipes, qui font leurs propres tickets.
#
# Consequence : aucun DIT cree par cet outil ne portera un autre domaine, ni
# depuis l'historique du client, ni depuis les mots-cles du mail.
DOMAINES_NOTRES = ("INFO",)

# La carte COMPLETE des domaines Artis vers la boite du metier qui les traite.
# Source : `/parametrage/domaines` (TEL Telephonie, HOTLINE Impression, CNX
# Connexion, FAX, GF Grand Format, INFO Informatique, SEC Securite, OPE
# Operateur). Elle etait jusqu'ici reduite a TEL/IMP/SEC, ecrits a la main :
# tout client d'un domaine absent de la carte se retrouvait BLOQUE a la
# creation sans etre range nulle part, donc invisible en boite. Constate le
# 2026-09-21 sur CONTROLE TECHNIQUE DE LA ZINSEL (domaine OPE, 60 clients
# concernes) : « GROS SOUCIS MAILS TRES URGENT » y dormait depuis son arrivee.
DOSSIER_DU_DOMAINE = {
    "TEL": "telecom", "OPE": "telecom", "CNX": "telecom", "FAX": "telecom",
    "HOTLINE": "impression", "IMP": "impression", "IMPRESSION": "impression",
    "GF": "impression", "VPIMP": "impression",
    "SEC": "securite", "SEC-VE": "securite",
}
# Variantes du domaine informatique : elles ne sont PAS un autre metier.
DOMAINES_INFORMATIQUES = ("INFO", "INF-VP")

# Les seules natures que produit cet outil : 11 Hotline partout, et 70 Avant
# Vente pour une demande de devis. PLUS de 10 Maintenance/Curatif : Elliot,
# 2026-09-15, « c'est TOUJOURS HOTLINE ». Les 24 autres natures d'Artis
# (Installation, Livraison, Demenagement, Formation, Preventif...) relevent
# d'un autre processus que la reception d'un mail de support.
NATURES_NOTRES = ("11", "70")

UO_ADMIN   = "HLN3"
UO_HOTLINE = "HLI"


def uo_pour(symptome):
    """UO d'apres le symptome. Les codes ADM-* sont des taches d'administration
    (Exchange, comptes 365, on/offboarding) : niveau 3."""
    return UO_ADMIN if (symptome or "").upper().startswith("ADM") else UO_HOTLINE


# Defauts arretes avec Elliot (cf. MAIL_TO_DIT.md). Le type et la nature
# dependent de la categorie de la demande : voir CATEGORIES dans mail_regles.
#   hotline     -> 05 EMAIL CLIENT / 11 Hotline
#   avant_vente -> 05 EMAIL CLIENT / 70 Avant Vente     (demandes de devis)
#   supervision -> 07 SUPERVISION  / 10 Curatif         (alertes RMM, monitoring)
PRIORITE = "03"     # Standard pour TOUT : aucune verite terrain disponible sur
                    # la priorite (GET /dit ne la renvoie pas), donc on ne devine
                    # pas et l'agent ajuste dans la file.

_lock = threading.Lock()
_etat = {"actif": False, "dernier": None, "erreur": None, "cycles": 0, "vus": 0}


# ═════════════════════════════════════════════════════════ stockage

def _tmp_a_moi(fichier):
    """Un nom de fichier temporaire propre a CE processus et a ce fil.

    Un nom fixe est partage : deux ecrivains — le cycle de l'application et un
    script lance a cote — s'ecrasent l'un l'autre, et le `os.replace` du
    premier fait disparaitre le fichier que le second allait renommer
    (incident du 2026-09-25). Au passage, on balaie les temporaires qu'une
    execution interrompue aurait laisses derriere elle.
    """
    base = str(fichier)
    try:
        dossier = pathlib.Path(base).parent
        nom = pathlib.Path(base).name
        limite = time.time() - 3600
        for vieux in dossier.glob(nom + ".*.tmp"):
            try:
                if vieux.stat().st_mtime < limite:
                    vieux.unlink()
            except Exception:
                pass
    except Exception:
        pass
    return "%s.%s.%s.tmp" % (base, os.getpid(), threading.get_ident())



def _fichier_secours():
    return pathlib.Path(str(PROPS_FILE) + ".bak")


def _charger():
    """Les propositions. Ne repart JAMAIS de zero si le fichier existe.

    INCIDENT DU 2026-09-14 : cette fonction avalait toute exception et rendait
    une structure vide. Deux ecritures concurrentes (le cycle de l'application
    et un script de traitement par lot lance a cote) ont rendu le JSON
    illisible ; la structure vide a ete REENREGISTREE par-dessus, et
    141 propositions ont ete perdues avec le journal des decisions.

    Desormais : si le fichier existe mais ne se lit pas, on tente la copie de
    secours, et a defaut on LEVE une exception. Un cycle en erreur se voit et
    se repare ; un fichier ecrase, non.
    """
    if not PROPS_FILE.exists():
        # ABSENT n'est pas VIDE. Le 2026-09-17, la file est repartie a P-0001
        # avec 364 propositions perdues : sur un partage SMB (Azure Files),
        # `os.replace` n'est pas atomique, et deux ecrivains — le cycle et un
        # script lance a cote — laissent une fenetre ou le fichier n'existe
        # plus. La branche « pas de fichier » repartait alors de zero et
        # ecrasait tout au premier enregistrement. On regarde donc TOUJOURS la
        # copie de secours avant de conclure a un premier demarrage.
        secours = _fichier_secours()
        if secours.exists():
            try:
                d = json.loads(secours.read_text(encoding="utf-8"))
                if d.get("props"):
                    return _completer_etat(d)
            except Exception:
                pass
        return {"delta": {}, "props": [], "_next": 1, "convs": {}}
    d = None
    try:
        d = json.loads(PROPS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        # Cas PRECIS et reparable : une ecriture plus courte a laisse la queue
        # de la precedente, le JSON est valide jusqu'a un point puis suivi de
        # debris (« Extra data »). Le prefixe porte toutes les propositions ;
        # le reste n'est qu'un fragment de la table des fils, qui se
        # reconstruit seule. On le recupere au lieu de s'arreter : le
        # 2026-09-23, ce defaut a fige le moteur pendant que cinq mails
        # s'accumulaient en boite.
        if "Extra data" in str(e):
            try:
                brut = PROPS_FILE.read_text(encoding="utf-8")
                partiel, fin = json.JSONDecoder().raw_decode(brut)
                if partiel.get("props"):
                    (_etat.get("_log") or (lambda *_a: None))(
                        "[mail-dit] fichier des propositions repare : %d "
                        "caracteres de debris apres la fin du JSON, %d "
                        "propositions intactes"
                        % (len(brut) - fin, len(partiel["props"])))
                    return _completer_etat(partiel)
            except Exception:
                pass
        secours = _fichier_secours()
        if secours.exists():
            try:
                d = json.loads(secours.read_text(encoding="utf-8"))
            except Exception:
                d = None
        if d is None:
            raise RuntimeError(
                "fichier des propositions illisible (%s) et aucune sauvegarde "
                "exploitable : on refuse de repartir de zero, ce qui "
                "l'ecraserait" % str(e)[:120])
    return _completer_etat(d)


def _completer_etat(d):
    """Les valeurs par defaut d'une structure de propositions relue."""
    d.setdefault("delta", {})
    d.setdefault("props", [])
    d.setdefault("_next", len(d["props"]) + 1)
    d.setdefault("convs", {})
    # Le compteur ne RECULE jamais. Demande d'Elliot le 2026-09-14, apres que
    # la file reconstruite a recommence a P-0001 : « on remet pas les memes
    # chiffres en boucle ». Un numero deja prononce ne doit jamais designer un
    # autre mail — c'est la reference dont il se sert pour me parler.
    d["_next"] = max(d.get("_next", 1), d.get("_next_max", 1))
    return d


def _sauver(d, forcer=False):
    """Enregistre les propositions, en gardant la version precedente.

    Garde-fou : on refuse d'ecrire une file VIDE par-dessus une file remplie
    sans `forcer`. C'est exactement ce qui a detruit 141 propositions le
    2026-09-14, et aucune situation normale ne le justifie.
    """
    if not forcer and PROPS_FILE.exists():
        try:
            avant = json.loads(PROPS_FILE.read_text(encoding="utf-8"))
        except Exception:
            avant = {}
        n_avant, n_apres = len(avant.get("props") or []), len(d.get("props") or [])
        if not d.get("props") and n_avant > 3:
            raise RuntimeError(
                "refus d'ecrire une file vide par-dessus %d propositions "
                "existantes" % n_avant)
        # VIDE n'etait pas la seule facon de tout perdre : le 2026-09-17 la
        # file est passee de 364 a 5 propositions d'un coup, ce que le
        # garde-fou precedent laissait passer. Une file qui fond de plus de
        # moitie ne vient jamais d'un cycle normal — la purge d'anciennete,
        # elle, retire quelques lignes a la fois.
        if n_avant > 20 and n_apres < n_avant // 2:
            raise RuntimeError(
                "refus d'ecrire %d propositions par-dessus %d : chute "
                "anormale" % (n_apres, n_avant))
    # marque haute du compteur : elle survit meme si les propositions sont
    # perdues, donc les numeros ne recommencent jamais a zero
    d["_next_max"] = max(d.get("_next_max", 1), d.get("_next", 1))
    # On serialise UNE FOIS, en memoire : le meme texte servira au fichier, a
    # la verification et a la sauvegarde. Ecrire deux fois, c'est risquer deux
    # contenus differents.
    texte = json.dumps(d, ensure_ascii=False)
    # UN FICHIER TEMPORAIRE PAR ECRIVAIN. Le nom etait fixe, donc partage : le
    # cycle de l'application et un script lance a cote ecrivaient le MEME
    # « .tmp », et le `os.replace` du premier faisait disparaitre le fichier que
    # le second s'appretait a renommer. Le 2026-09-25, deux DIT ont bien ete
    # creees (100175421 et 07221083) mais leurs propositions n'ont pas pu etre
    # enregistrees : « No such file or directory: mail_dit_props.json.tmp ».
    # Le verrou `_lock` ne protege que l'interieur d'un processus ; il ne dit
    # rien de l'autre.
    tmp = _tmp_a_moi(PROPS_FILE)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(texte)
        f.flush()
        os.fsync(f.fileno())          # sur un partage SMB, sans ca le
                                      # remplacement peut porter sur un
                                      # fichier encore incomplet
    # Copie de secours de la version PRECEDENTE — et seulement si elle est
    # LISIBLE. Le 2026-09-23 la sauvegarde a ete ecrasee par une copie du
    # fichier deja abime : il ne restait alors plus rien d'exploitable, et le
    # moteur s'est arrete net (a juste titre) pendant que les mails
    # s'accumulaient.
    try:
        if PROPS_FILE.exists():
            ancien = PROPS_FILE.read_text(encoding="utf-8")
            json.loads(ancien)        # leve si l'ancien est deja abime
            import shutil
            shutil.copyfile(str(PROPS_FILE), str(_fichier_secours()))
    except Exception:
        pass
    os.replace(tmp, PROPS_FILE)
    # ON RELIT. `os.replace` n'est pas atomique sur Azure Files : une ecriture
    # PLUS COURTE que la precedente peut laisser la queue de l'ancienne, et le
    # fichier devient illisible (« Extra data »). C'est arrive le 2026-09-23 :
    # 774 caracteres d'une table `convs` plus longue trainaient apres la fin du
    # JSON. On verifie donc, et on recommence une fois avant d'abandonner.
    try:
        json.loads(PROPS_FILE.read_text(encoding="utf-8"))
    except Exception:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(texte)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PROPS_FILE)
        json.loads(PROPS_FILE.read_text(encoding="utf-8"))


def propositions(etat=None, limite=500, traites=False):
    """Liste des propositions, les plus recentes d'abord.

    `traites=False` (defaut) exclut les mails marques `deja_traite`, qui
    viennent du dossier des mails DEJA saisis dans Artis (amorcage historique) :
    ils ne sont pas a traiter et gonflaient les compteurs.
    """
    with _lock:
        d = _charger()
    p = [x for x in d["props"] if bool(x.get("deja_traite")) == bool(traites)]
    if etat:
        p = [x for x in p if x.get("etat") == etat]
    return sorted(p, key=lambda x: x.get("recu") or "", reverse=True)[:limite]


def compteurs():
    """Compteurs de la FILE REELLE. Les mails deja saisis dans Artis sont
    comptes a part : ils ne sont pas du travail a faire."""
    with _lock:
        d = _charger()
    c = {"nouveau": 0, "valide": 0, "ecarte": 0, "total": 0, "deja_traites": 0}
    for p in d["props"]:
        if p.get("deja_traite"):
            c["deja_traites"] += 1
            continue
        c[p.get("etat", "nouveau")] = c.get(p.get("etat", "nouveau"), 0) + 1
        c["total"] += 1
    return c


def statuer(pid, etat, user="", note="", dit_numero=""):
    """Marque une proposition validee ou ecartee. N'ecrit RIEN dans Artis."""
    if etat not in ("nouveau", "valide", "ecarte"):
        return None
    with _lock:
        d = _charger()
        for p in d["props"]:
            if p.get("id") == pid:
                if p.get("etat") != etat:
                    p.setdefault("journal", []).append({
                        "quand": datetime.datetime.now().isoformat(
                            timespec="seconds"),
                        "qui": user, "champ": "etat",
                        "avant": p.get("etat"), "apres": etat,
                        "note": note or ""})
                p["etat"] = etat
                p["traite_par"] = user
                p["traite_le"] = datetime.datetime.now().isoformat(timespec="seconds")
                if note:
                    p["note"] = note
                if dit_numero:
                    p["dit_numero"] = dit_numero
                _sauver(d)
                return p
    return None


# Les metiers qui ne sont pas le notre. L'agent les designe d'un bouton dans la
# file ; on n'essaie plus de les deviner au vocabulaire, ca ne marche pas
# (mesure du 2026-09-14 : 21 mails « telephonie » du corpus, 21 traites par
# l'equipe informatique).
METIERS_AUTRES = {
    "telephonie": "téléphonie",
    "securite":   "sécurité",
    "impression": "impression",
    "commerce":   "commerce / ADV",
    # Le terrain, lui, EST de l'informatique : ce n'est pas un autre métier,
    # c'est une autre équipe. Une intervention sur site ne se traite pas à la
    # hotline, elle part chez le responsable terrain (Elliot, 2026-09-14,
    # P-0215 : « c'est Régis Chevot le destinataire, c'est le responsable
    # terrain »).
    "terrain":    "informatique terrain",
    "autre":      "autre métier",
}

# À qui va ce qui n'est pas pour la hotline, quand on le sait. Sert à proposer
# le transfert : rien n'est envoyé sans que quelqu'un clique.
RESPONSABLES = {
    "terrain": "rchevot@dfm.fr",
}

HORS_PERIMETRE = DATA_DIR / "mail_dit_hors_perimetre.json"
_lock_hp = threading.Lock()


def _hp_charger():
    try:
        if HORS_PERIMETRE.exists():
            return json.loads(HORS_PERIMETRE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"clients": {}, "expediteurs": {}, "journal": []}


def marquer_metier(pid, metier, user=""):
    """L'agent dit : « ce mail n'est pas pour nous, c'est de la <metier> ».

    Elliot (2026-09-14) : « ajoute la possibilite de flagger directement le
    ticket comme telephonie ou securite, comme ca pas besoin de te dire a
    chaque fois, et tu lis ce qui a ete fait pour apprendre ».

    Deux effets :
      1. la proposition sort de la file, motif ecrit et horodate ;
      2. le couple (client, expediteur) -> metier est COMPTE. Au prochain mail
         du meme expediteur ou du meme client, la proposition arrive deja
         signalee — c'est le seul apprentissage honnete ici, puisqu'aucun
         vocabulaire ne separe ces demandes.

    On compte, on signale, on n'ecarte pas tout seul : un client de telephonie
    peut avoir un vrai probleme informatique.
    """
    if metier not in METIERS_AUTRES:
        return None
    p = statuer(pid, "ecarte", user,
                "%s : autre métier, signalé par %s"
                % (METIERS_AUTRES[metier], user or "un agent"))
    if not p:
        return None
    journal_auto_corriger(pid, resultat="écarté à la main : %s"
                          % METIERS_AUTRES[metier])
    with _lock_hp:
        d = _hp_charger()
        for cle, valeur in (("clients", p.get("client")),
                            ("expediteurs", (p.get("de") or "").lower())):
            if not valeur:
                continue
            d.setdefault(cle, {}).setdefault(valeur, {})
            d[cle][valeur][metier] = d[cle][valeur].get(metier, 0) + 1
        d.setdefault("journal", []).append({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "qui": user, "proposition": pid, "metier": metier,
            "client": p.get("client"), "de": p.get("de"),
            "objet": (p.get("objet") or "")[:120]})
        d["journal"] = d["journal"][-500:]
        HORS_PERIMETRE.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    return p


def signal_metier(client, de):
    """Ce que les agents ont deja dit de ce client / cet expediteur.

    Rend {"metier": "telephonie", "n": 3, "source": "cet expéditeur"} ou None.
    L'expediteur prime : il est plus precis que le client.
    """
    d = _hp_charger()
    for cle, valeur, source in (("expediteurs", (de or "").lower(), "cet expéditeur"),
                                ("clients", client or "", "ce client")):
        compte = (d.get(cle) or {}).get(valeur) or {}
        if compte:
            metier, n = max(compte.items(), key=lambda kv: kv[1])
            return {"metier": metier, "libelle": METIERS_AUTRES.get(metier, metier),
                    "n": n, "source": source}
    return None


def corriger(pid, champs, user=""):
    """Corrige les champs proposes (client, domaine, priorite, commentaire...)."""
    autorises = {"client", "client_lib", "pid_interlocuteur", "domaine",
                 "priorite", "nature", "type_dit", "commentaire", "note",
                 "symptome", "org_int", "site", "bien", "uo", "gp_travail",
                 "type_facturation",
                 # mis a vide par l'agent quand il a corrige le champ dans Artis
                 "a_corriger_dans_artis",
                 # Ce que le modele a lu. Sans ces deux-la dans la liste, la
                 # correction etait acceptee puis SILENCIEUSEMENT jetee : le
                 # 2026-09-17, le modele identifiait bien les demandes et les
                 # clients, et pas une seule ne franchissait le garde-fou de
                 # confiance. Une liste blanche qui ne dit pas ce qu'elle
                 # refuse est un piege.
                 "tier_modele", "pourquoi_modele"}
    with _lock:
        d = _charger()
        for p in d["props"]:
            if p.get("id") == pid:
                # Photo de rattrapage : une proposition creee avant cette
                # fonctionnalite n'en a pas. On la prend AVANT d'appliquer la
                # correction, sinon elle enregistrerait la valeur corrigee.
                if not p.get("propose"):
                    p["propose"] = {k2: p.get(k2, "") for k2 in
                                    ("client", "symptome", "uo", "priorite",
                                     "domaine", "nature", "type_dit", "tier")}
                    p["propose"]["_reconstitue"] = True
                refuses = [k for k in (champs or {}) if k not in autorises]
                if refuses:
                    # Un champ refusé doit SE VOIR. Silencieux, il fait croire
                    # à une correction appliquée (incident du 2026-09-17).
                    p.setdefault("journal", []).append({
                        "quand": datetime.datetime.now().isoformat(
                            timespec="seconds"),
                        "qui": user, "champ": "(refusés)",
                        "avant": None, "apres": ", ".join(refuses)})
                for k, v in (champs or {}).items():
                    if k in autorises:
                        if p.get(k) != v:
                            p.setdefault("journal", []).append({
                                "quand": datetime.datetime.now().isoformat(
                                    timespec="seconds"),
                                "qui": user, "champ": k,
                                "avant": p.get(k), "apres": v})
                        p[k] = v
                # le symptome commande l'UO : si l'agent change l'un sans
                # toucher l'autre, on aligne l'UO sur la convention
                if "symptome" in (champs or {}) and "uo" not in (champs or {}):
                    p["uo"] = uo_pour(p.get("symptome"))
                p["corrige_par"] = user
                p["corrige_le"] = datetime.datetime.now().isoformat(timespec="seconds")
                _sauver(d)
                return p
    return None


def apprentissage():
    """Ce que les agents ont change par rapport a ce que l'outil proposait.

    C'est la seule source honnete pour ameliorer les regles : pas ce que je
    crois, mais ce qu'un humain a corrige. Rend, par champ, le nombre de fois
    ou la proposition a ete gardee ou changee, et le detail des couples
    (propose -> retenu) pour en tirer des regles.
    """
    import collections
    with _lock:
        d = _charger()
    hp = _hp_charger()
    hors = {
        "par_metier": dict(collections.Counter(
            x.get("metier") for x in (hp.get("journal") or []))),
        "clients": len(hp.get("clients") or {}),
        "expediteurs": len(hp.get("expediteurs") or {}),
        "derniers": list(reversed((hp.get("journal") or [])[-25:])),
    }
    champs = ("client", "symptome", "uo", "priorite", "domaine", "nature",
              "type_dit")
    garde = collections.Counter()
    change = collections.Counter()
    couples = collections.defaultdict(collections.Counter)
    decisions = collections.Counter()
    traites = 0
    for p in d["props"]:
        if p.get("etat") not in ("valide", "ecarte"):
            continue
        traites += 1
        decisions["%s/%s" % (p.get("tier") or "?", p.get("etat"))] += 1
        ref = p.get("propose") or {}
        if not ref:
            continue
        for k in champs:
            avant, apres = ref.get(k), p.get(k)
            if avant in (None, "") and apres in (None, ""):
                continue
            if avant == apres:
                garde[k] += 1
            else:
                change[k] += 1
                couples[k]["%s -> %s" % (avant or "(vide)", apres or "(vide)")] += 1
    return {
        "propositions_traitees": traites,
        "decisions": dict(decisions),
        "par_champ": {k: {"garde": garde[k], "change": change[k],
                          "taux_garde": (round(100.0 * garde[k] / (garde[k] + change[k]))
                                         if (garde[k] + change[k]) else None),
                          "corrections": couples[k].most_common(12)}
                      for k in champs if (garde[k] or change[k])},
        # Ce que les agents ont declare « pas pour nous » : c'est la matiere
        # d'apprentissage la plus fiable dont on dispose sur le perimetre.
        "hors_perimetre": hors,
    }


def dits_proches(prop, jours=3, maxi=12):
    """Les DIT du client autour de la date du mail, triées par ressemblance.

    Même lecture que l'écran « ce DIT existe-t-il déjà ? ». Factorisée ici pour
    que le mode ombre et l'écran raisonnent sur exactement la même chose.
    """
    code = (prop or {}).get("client")
    if not code:
        return []
    try:
        import requests as _rq
        import mail_dit_ecriture as _E
        base, tok = _E._artis()
        jour = datetime.date.fromisoformat((prop.get("recu") or "")[:10])
        r = _rq.get("%s/client/%s/dit" % (base, code),
                    params={"token": tok,
                            "dateDebut": (jour - datetime.timedelta(days=jours)).isoformat(),
                            "dateFin": (jour + datetime.timedelta(days=jours)).isoformat(),
                            "maxResults": 200},
                    headers={"accept": "application/json"}, timeout=30)
        if r.status_code != 200:
            return []
        out = []
        objet = _empreinte(RE_PREFIXE_REPONSE.sub("", prop.get("objet") or ""))
        # Le nom de la personne concernee est dans l'objet OU dans le corps :
        # les DIT saisies a la main commencent par « Bonjour, » et ne portent
        # le nom que plus bas. Chercher dans la seule premiere ligne ne
        # trouvait donc jamais rien (constate le 2026-09-17 sur le client 9599).
        gens_prop = _noms_propres((prop.get("objet") or "") + "\n"
                                  + (prop.get("commentaire") or "")[:1500])
        for d in (r.json() or []):
            com = d.get("commentaire") or ""
            # Le CORPS d'une reponse ne ressemble pas a la demande d'origine
            # (mesure sur les cas signales le 2026-09-15 : 28 % au mieux). Nos
            # DIT, elles, commencent par l'objet du mail — on compare donc
            # aussi les objets, et on garde le meilleur des deux signaux.
            # L'OBJET N'EST PAS TOUJOURS SUR LA PREMIERE LIGNE. Un ticket
            # saisi a la main commence par « Bonjour, » et ne met l'objet
            # qu'ensuite : ne lire que la premiere ligne rendait la
            # ressemblance nulle. Le 2026-09-25, la relance de MPL n'a pas
            # vu la DIT 100175412 (« Bonjour , / MPL - Depart Arnaud ») et
            # a cree un doublon. On lit donc les premieres lignes utiles.
            _tete = [x.strip() for x in (com or "").split(chr(10)) if x.strip()][:3]
            prem = _empreinte(" ".join(_tete)[:200])
            # Un objet trop court sature ce rapport : « Pb Mails du 16/09 »
            # se reduit a deux mots, dont l'un suffit a afficher 100 % face a
            # n'importe quelle DIT parlant de mails (constate le 2026-09-17 sur
            # le client 19045, ou une demande neuve a ete prise pour un
            # doublon). En dessous de quatre mots significatifs, l'objet ne
            # prouve rien et on s'en remet au corps.
            n_min = min(len(objet), len(prem)) if (objet and prem) else 0
            if n_min >= 4:
                r_objet = len(objet & prem) / float(n_min)
            elif n_min == 3:
                # Trois mots significatifs, mais TOUS communs : « REDEMARAGE
                # SERVEUR SAGE » ne se retrouve pas par hasard dans deux
                # demandes distinctes. Exiger quatre mots des DEUX cotes
                # rendait le signal nul des que le ticket d'origine avait un
                # objet court — c'est ainsi que la relance HOLDING HBL du
                # 2026-09-21 n'a pas vu la DIT 100174492 (5 % de ressemblance)
                # et a cree un doublon. Un recouvrement PARTIEL, lui, ne prouve
                # toujours rien : « Depart collaborateur Dupont » contre
                # « Depart collaborateur Martin » reste a zero.
                r_objet = 1.0 if len(objet & prem) == n_min else 0.0
            else:
                r_objet = 0.0
            r_corps = similarite(prop.get("commentaire"), com)
            score = max(r_corps, r_objet)
            # Arrivees et departs : le client reprend le MEME modele de mail et
            # ne change que le nom. « Depart collaborateur / suppression compte
            # SEGA DIARRA » ressemble a 90 % a celui de RUBEN CIVIS — ce n'est
            # pas un doublon, c'est une autre personne. Quand les deux textes
            # nomment quelqu'un et que ce n'est pas le meme, la ressemblance
            # redescend sous le seuil de doublon.
            gens_dit = _noms_propres(com[:1500])
            distincts = bool(gens_prop and gens_dit and not (gens_prop & gens_dit))
            if distincts:
                score = min(score, 0.4)
            out.append({"numero": d.get("numero"), "date": d.get("date"),
                        "personnes_differentes": distincts,
                        "etat": d.get("etat"),
                        "type": ((d.get("typeDit") or {}).get("libelle") or "").strip(),
                        "commentaire": com[:400],
                        "ressemblance_corps": r_corps,
                        "ressemblance_objet": round(r_objet, 2),
                        "ressemblance": score})
        out.sort(key=lambda x: -x["ressemblance"])
        return out[:maxi]
    except Exception:
        return []


# ─────────────────────────────────────────────────────── mode ombre
# L'outil décide sur chaque mail et ENREGISTRE ce qu'il aurait fait, sans rien
# faire. On compare ensuite à ce qu'Elliot a réellement décidé : c'est la seule
# façon de savoir, famille par famille, si l'automatisation peut être allumée.
# Demandé par Elliot le 2026-09-14 : « au final TU devras tout faire
# automatiquement, là je le fais manuellement pour que tu apprennes à juger ».

# Objet qui est un identifiant d'équipement + une version de firmware : c'est
# une notification d'appareil, jamais une phrase écrite par quelqu'un.
RE_EQUIPEMENT = re.compile(r"\bSFOS\s?\d|\bXGS\d{2,}|MR-\d+-Build\d+", re.I)

# Objet d'une alerte du SOC : « [SOC x 30542 - CLIPPERTON] - Application ... »
RE_ALERTE_SOC = re.compile(r"\[\s*SOC\s*x\s*\d+", re.I)
BAL_MCO = "mco@dfm.fr"
BAL_SUPPORT = "support@dfm.fr"

# Version du moteur d'ombre. Tant qu'une proposition n'est pas tranchée, son
# avis est refait si le moteur a changé : sinon on mesurerait un moteur qui
# n'existe plus. Un avis posé sur une proposition DÉJÀ tranchée n'est jamais
# réécrit — ce serait réécrire l'histoire.
OMBRE_VERSION = 6

FAMILLES_ALERTE = [
    ("firewall", re.compile(
        r"pare[- ]?feu|firewall|\bxgs\d|\bsfos\b|connexion perdue entre le pare", re.I)),
    # IDSBOX est un boîtier de SAUVEGARDE : ses « system checks report » vont
    # dans 06 - SAUVEGARDE. Ils tombaient dans le fourre-tout « informations »
    # faute de mot-clé (Elliot, 2026-09-14 : « on l'a déjà vu précédemment ce
    # cas »).
    ("sauvegarde", re.compile(
        r"sauvegarde|\bbackup\b|veeam|datto\s+backup|rapport de sauvegarde"
        r"|idsbox|\bnakivo\b|\bacronis\b|synology.{0,20}backup", re.I)),
    ("antivirus", re.compile(
        r"antivirus|sentinelone|\bav\b\s|malware|virus détecté|threat detected", re.I)),
    ("antispam", re.compile(r"antispam|quarantaine|spam report|vade", re.I)),
    ("rmm", re.compile(r"@rmm\.datto|datto rmm|monitor automatically disabled", re.I)),
]


ETATS_OUVERTS = ("EnCours", "APlanifier", "Planifiee", "TravailNonTermine",
                 "SaisieExterne", "Nouveau")


# Les boites des AUTRES metiers. Un fil qui y est deja range n'a pas a revenir
# chez nous message apres message.
METIERS_DU_FIL = ("telecom", "impression", "securite")
_CACHE_DOSSIERS_METIER = {}


def dossier_metier_du_fil(prop, tok=None):
    """Ce fil a-t-il DEJA ete range dans la boite d'un autre metier ?

    Une reponse dans un fil ne repete pas le probleme : « Boulogne
    Billancourt », trois mots, apres une demande de deblocage de numeros
    speciaux. Le mail isole ne dit plus rien de la telephonie, et le moteur en
    a fait une DIT informatique (100174862, le 2026-09-22) alors que le premier
    message du meme fil etait parti en BAL TELECOM une demi-heure plus tot.

    Le fil, lui, ne ment pas : si un message precedent est chez un metier, la
    suite y va aussi. Rend le nom du dossier (cle de `E.DOSSIERS`) ou None.
    """
    fil = (prop or {}).get("conversation")
    if not fil:
        return None
    try:
        import requests as _rq
        import mail_dit_ecriture as _E          # import tardif : evite le cycle
        tok = tok or access_token()
        for metier in METIERS_DU_FIL:
            fid = _CACHE_DOSSIERS_METIER.get(metier)
            if fid is None:
                fid = _E.id_dossier(tok, _E.DOSSIERS[metier])
                _CACHE_DOSSIERS_METIER[metier] = fid or ""
            if not fid:
                continue
            r = _rq.get("%s/me/mailFolders/%s/messages" % (GRAPH, fid),
                        headers={"Authorization": "Bearer " + tok},
                        params={"$filter": "conversationId eq '%s'" % fil,
                                "$select": "id", "$top": "1"}, timeout=40)
            if r.status_code == 200 and (r.json() or {}).get("value"):
                return metier
    except Exception:
        return None
    return None


def dit_du_meme_fil(prop):
    """La DIT deja ouverte pour CE fil de discussion, ou None.

    La ressemblance de texte est un indice ; le fil, lui, est une preuve. Une
    relance (« RE: ... ») porte un nouveau message_id mais le meme
    conversationId que la demande d'origine — si ce fil a deja donne un ticket
    et qu'il est encore ouvert, la relance le COMPLETE, elle n'en ouvre pas un
    second.

    Defaut constate le 2026-09-21 : HOLDING HBL relance son serveur SAGE, la
    DIT 100174492 est ouverte depuis le vendredi, et la comparaison de textes
    ne la voyait pas (5 %) parce que le corps d'une relance ne ressemble pas a
    la demande initiale et que son objet n'avait que trois mots significatifs.
    Resultat : la DIT 100174638, doublon exact.

    Rend (numero, etat) ou (None, None). On ne rend QUE des tickets ouverts :
    completer une DIT close ne sert personne, le mail suivra le chemin normal.
    """
    fil = (prop or {}).get("conversation")
    if not fil:
        return None, None
    lignes = [l for l in registre() if l.get("conversation") == fil]
    if not lignes:
        return None, None
    try:
        import requests as _rq
        import mail_dit_ecriture as _E          # import tardif : evite le cycle
        base, tok = _E._artis()
    except Exception:
        return None, None
    for l in lignes:                            # le plus recent d'abord
        num = l.get("dit")
        if not num:
            continue
        try:
            r = _rq.get("%s/dit/%s" % (base, num), params={"token": tok},
                        headers={"accept": "application/json"}, timeout=30)
            if r.status_code != 200:
                continue
            d = r.json() or {}
            if isinstance(d, list):
                d = d[0] if d else {}
            etat = d.get("etat") or ""
            if etat in ETATS_OUVERTS:
                return num, etat
        except Exception:
            continue
    return None, None


def dit_pour_les_pieces(prop, jours=25):
    """À quel ticket ouvert ces photos répondent-elles ?

    Règle d'Elliot, 2026-09-18 : « c'est sûrement une photo suite à une
    ouverture de DIT, il faut rajouter la photo dans la DIT qui correspond ;
    s'il n'y en a qu'une seule ouverte c'est celle-là, sinon essaie de voir si
    dans les DIT ouvertes on parle de photo ou d'une demande qui y ressemble ».

    Rend (numero, pourquoi) ou (None, pourquoi) — on ne devine jamais entre
    deux tickets qui se valent.
    """
    proches = [d for d in dits_proches(prop, jours=jours, maxi=40)
               if (d.get("etat") or "") in ETATS_OUVERTS]
    if not proches:
        return None, "aucun ticket ouvert chez ce client"
    if len(proches) == 1:
        return proches[0]["numero"], "seul ticket ouvert du client"
    # Plusieurs : celui qui PARLE de photos, puis celui qui ressemble le plus.
    parle = [d for d in proches
             if re.search(r"(photo|capture|image|ci[- ]?joint|pi[èe]ce jointe)",
                          d.get("commentaire") or "", re.I)]
    if len(parle) == 1:
        return parle[0]["numero"], "seul ticket ouvert qui parle de photos"
    candidats = parle or proches
    candidats.sort(key=lambda d: -d["ressemblance"])
    if candidats[0]["ressemblance"] >= 0.45 and (
            len(candidats) == 1
            or candidats[0]["ressemblance"] - candidats[1]["ressemblance"] >= 0.15):
        return candidats[0]["numero"], ("ressemblance %.0f %% avec ce ticket"
                                        % (100 * candidats[0]["ressemblance"]))
    return None, ("%d tickets ouverts se valent : à un humain de choisir"
                  % len(candidats))


def decider_seul(prop):
    """Ce que l'outil ferait s'il décidait tout seul. N'ÉCRIT RIEN.

    Rend {"action", "cible", "pourquoi", "confiance"} où action vaut :
      ranger    — c'est une alerte automatique, elle va dans son dossier
      completer — une DIT du client reprend déjà ce texte
      ecarter   — un autre métier, déjà tranché pour ce client/expéditeur
      creer     — une demande client, prête à devenir une DIT
      attendre  — je ne sais pas : à un humain de trancher
    `attendre` est une abstention assumée, pas une erreur : mieux vaut rendre
    la main que deviner.
    """
    objet = (prop.get("objet") or "")
    corps = (prop.get("commentaire") or "")[:2000]
    texte = objet + "\n" + corps

    # LA TELEPHONIE NE DONNE JAMAIS DE TICKET. Elliot, 2026-09-25 : « on ne
    # fait aucune creation pour le telecom, donc tu deplaces juste dans les
    # bonnes BAL ». L'equipe telecom ouvre ses propres affaires ; notre role
    # s'arrete a poser le mail devant elle. Cette regle passe AVANT toutes
    # les autres : un mail de telephonie ne doit pas pouvoir devenir une DIT
    # par un autre chemin.
    if (RE_TELEPHONIE.search(texte)
            or metier_du_destinataire(prop.get('destinataires') or [],
                                      expediteur=prop.get('de'),
                                      texte=texte)[0] == 'telecom'):
        return {"action": "ranger", "cible": "telecom",
                "pourquoi": "telephonie : aucune creation, le mail va en BAL TELECOM",
                "confiance": "haute"}

    # 0. Alerte SOC déjà prise en charge. Elliot, deux fois (P-0174 puis
    #    P-0197) : « c'est une alerte SOC, à mettre à la corbeille SI ET
    #    SEULEMENT SI mco@dfm.fr est en copie ou en destinataire ». La
    #    condition est vérifiée sur les destinataires enregistrés, jamais
    #    supposée : sans mco, l'alerte n'a pas été prise en charge et on n'y
    #    touche pas.
    if RE_ALERTE_SOC.search(objet):
        if any(BAL_MCO in (x or "").lower() for x in (prop.get("destinataires") or [])):
            return {"action": "ranger", "cible": "corbeille",
                    "pourquoi": "alerte SOC avec %s en copie : déjà prise en "
                                "charge par la sécurité" % BAL_MCO,
                    "confiance": "haute"}
        # Pas de MCO en copie : l'alerte ne lui est jamais parvenue. On la lui
        # TRANSMET, puis elle se classe — Elliot, 2026-09-18 : « il suffit de
        # le transférer à MCO et de le classer dans INFORMATIONS ». Attendre,
        # comme le faisait la version précédente, laissait une alerte de
        # sécurité dormir dans une boîte que personne ne surveille.
        return {"action": "transferer", "cible": BAL_MCO,
                "apres": "informations",
                "pourquoi": "alerte SOC sans %s en copie : on la lui transmet, "
                            "puis classement en informations" % BAL_MCO,
                "confiance": "haute"}

    # 1. Alertes automatiques : aucune demande humaine derrière, elles se
    #    rangent. C'est la famille la plus mécanique, donc la première
    #    candidate à l'automatisation.
    if (prop.get("categorie") == "supervision"
            or RE_SUPERVISION.search((prop.get("de") or "") + "\n" + objet)
            # Un objet qui EST un nom d'équipement et une version de firmware
            # (« FW_LYON_01_XGS108_SFOS 22.0.1 MR-1-Build490 ») ne peut pas
            # être une demande humaine. Repéré par le mode ombre lui-même, qui
            # proposait de créer une DIT pour deux alertes firewall.
            or RE_EQUIPEMENT.search(objet)):
        for dossier, rx in FAMILLES_ALERTE:
            if rx.search(texte):
                return {"action": "ranger", "cible": dossier,
                        "pourquoi": "alerte automatique de type %s" % dossier,
                        "confiance": "haute"}
        return {"action": "ranger", "cible": "informations",
                "pourquoi": "alerte automatique sans famille reconnue",
                "confiance": "basse"}

    # 2. Le mail s'adresse nommément à un collègue : il n'est pas pour le
    #    support, il est pour lui. On le transfère, on ne ticketise pas.
    di = prop.get("destinataire_interne") or {}
    if di.get("certitude") == "haute":
        return {"action": "transferer", "cible": di.get("adresse"),
                "pourquoi": di.get("indice"), "confiance": "haute"}

    # 3. Métier déjà tranché pour ce client ou cet expéditeur.
    sig = prop.get("metier_signale") or {}
    # Même exception que dans `_creation_sans_doute` : un client MULTI-MÉTIER
    # n'est pas un client d'un autre métier. Steam-One, écarté deux fois comme
    # sécurité, a un historique informatique fourni — sa demande d'accès à
    # distance était écartée dès la décision, avant même les garde-fous
    # (Elliot, 2026-09-17 puis 18).
    if (sig.get("n", 0) >= 2
            and (prop.get("domaine_client_hist") or "").upper()
            not in DOMAINES_INFORMATIQUES):
        return {"action": "ecarter", "cible": sig.get("metier"),
                "pourquoi": "%s mails de %s déjà écartés comme %s"
                            % (sig["n"], sig.get("source"), sig.get("libelle")),
                "confiance": "moyenne"}

    # 1 bis. Le fil est déjà chez un autre métier : la suite y retourne. C'est
    #    une PREUVE, pas un indice — un message précédent du même échange est
    #    physiquement rangé dans leur boîte.
    _metier_fil = dossier_metier_du_fil(prop)
    if _metier_fil:
        return {"action": "ranger", "cible": _metier_fil,
                "pourquoi": "ce fil est déjà rangé en %s" % _metier_fil,
                "confiance": "haute"}

    # 1 ter. Un mail ENVOYE par un collègue DFM A un client : la boîte n'en a
    #    qu'une copie. Ce n'est pas une demande qui arrive, c'est une réponse
    #    qui part — le technicien est déjà dessus, il donne son numéro direct.
    #    Le discriminant est net : le support ne figure PAS parmi les
    #    destinataires. Quand un collègue écrit AU support pour faire ouvrir un
    #    ticket, support@dfm.fr est en destinataire, et cette règle ne le
    #    touche pas. Constaté le 2026-09-21 : « problème de connexion VPN »,
    #    de Pascal OLIVIER vers metroneurope.fr, dormait en boîte — ni
    #    ticketisable (client non établi) ni rangé.
    dests = [x for x in (prop.get("destinataires") or []) if x]
    if (dests and (prop.get("de") or "").lower().endswith("@dfm.fr")
            and not any(BAL_SUPPORT in x for x in dests)
            and not all(x.endswith("@dfm.fr") for x in dests)):
        return {"action": "ranger", "cible": "traite",
                "pourquoi": "mail envoyé par un collègue DFM à un client, le "
                            "support n'en a qu'une copie",
                "confiance": "haute"}

    # 2 bis. Des pièces sans une phrase : ce sont des photos ENVOYÉES EN
    #    RÉPONSE à une demande. Elles rejoignent le ticket concerné.
    if (prop.get("client") and (prop.get("pieces") or prop.get("photos"))
            and corps_muet(prop.get("commentaire"))):
        num, pq = dit_pour_les_pieces(prop)
        if num:
            return {"action": "completer", "cible": num,
                    # Puis le mail part chez celui qui traite le ticket : les
                    # photos ne servent à rien si personne n'apprend qu'elles
                    # sont arrivées. Règle d'Elliot, 2026-09-18 : « tu ajoutes
                    # la photo à la DIT, et ensuite tu transfères le mail au
                    # dernier intervenant de la DIT ».
                    "puis_transferer": True,
                    "pourquoi": "photos envoyées par le client — %s" % pq,
                    "confiance": "haute"}
        # Aucun ticket ne s'impose. Si l'expediteur est des NOTRES, c'est une
        # reponse qui PART chez le client — un technicien qui envoie un QR code
        # avec le support en copie pour la trace. Rien a ticketiser : le mail
        # continue son chemin et se rangera plus bas. Le 2026-09-23, « En Qr
        # code » (Anton Curpen vers john-taylor.com) est reste bloque ici,
        # parce que deux tickets ouverts « se valaient » — une question qui ne
        # se posait meme pas.
        if not est_interne(prop.get("de") or ""):
            return {"action": "attendre", "cible": None,
                    "pourquoi": "photos sans texte, et %s" % pq,
                    "confiance": "nulle"}
        # Expediteur interne : le mail se range, point. Le laisser continuer
        # menait a un refus de creation (« ni le corps ni l'objet ne portent
        # de demande lisible ») qui, lui, ne rangeait rien — le mail revenait
        # a chaque passage.
        return {"action": "ranger", "cible": "traite",
                "pourquoi": "reponse partie chez le client, support en copie "
                            "pour la trace (%s)" % pq,
                "confiance": "haute"}

    # 2 ter. Le fil a DÉJÀ donné un ticket, encore ouvert : la preuve prime
    #    sur l'indice. On complète sans passer par la ressemblance de textes,
    #    qui ne reconnaît pas une relance (son corps ne répète pas la demande).
    num_fil, etat_fil = dit_du_meme_fil(prop)
    if num_fil:
        return {"action": "completer", "cible": num_fil,
                "pourquoi": "même fil de discussion que le DIT %s (%s)"
                            % (num_fil, etat_fil),
                "confiance": "haute"}

    # 2 quater. Ni le corps ni l'objet ne portent de demande, et il n'y a
    #    aucune pièce : « test » / « TEST ». Quelle que soit la reponse aux
    #    questions suivantes — quel client, quel symptôme — ce mail ne peut
    #    donner aucun ticket. Le laisser en attente d'un humain qui n'aura rien
    #    de plus à en dire, c'est le garder en boîte pour rien : il se range,
    #    lisible, en 01 DIT ARTIS. Constaté le 2026-09-21 sur un essai d'envoi
    #    d'une cliente de PARC ESPACE.
    if (corps_muet(prop.get("commentaire")) and corps_muet(prop.get("objet"))
            and not (prop.get("pieces") or prop.get("photos"))):
        return {"action": "ranger", "cible": "traite",
                "pourquoi": "ni le corps ni l'objet ne portent de demande : "
                            "rien à ouvrir",
                "confiance": "haute"}

    # 3. Une DIT du client reprend déjà ce texte : compléter, pas dupliquer.
    #    FENÊTRE LARGE, et c'est le point clé : la version précédente ne
    #    regardait que ±3 jours. La relance « Re: Licence Office » du 18/09
    #    citait mot pour mot la demande du 9, dont le ticket 100173456 existait
    #    — neuf jours plus tôt, donc invisible. Un client relance au bout d'une
    #    semaine ou deux, pas au bout de trois jours (Elliot, 2026-09-18 :
    #    « c'était évident, le contenu, l'objet, l'historique »).
    proches = dits_proches(prop, jours=30)
    # Même règle qu'au pont du modèle : à ressemblance comparable, un ticket
    # OUVERT passe avant un ticket annulé ou clôturé.
    proches.sort(key=lambda d: (0 if (d.get("etat") or "") in ETATS_OUVERTS
                                else 1, -d["ressemblance"]))
    if proches and proches[0]["ressemblance"] >= 0.55:
        return {"action": "completer", "cible": proches[0]["numero"],
                "pourquoi": "ressemblance %.0f %% avec le DIT %s"
                            % (100 * proches[0]["ressemblance"],
                               proches[0]["numero"]),
                "confiance": "haute" if proches[0]["ressemblance"] >= 0.7 else "moyenne"}

    # 3 bis. Client dont TOUT l'historique est dans un autre métier : le mail
    #    ne reste pas en attente, il part chez ce métier. Elliot, 2026-09-17,
    #    sur « RFG - RIBEIRO - CASQUE SANS FIL » : « c'est bien de la
    #    téléphonie, faut le déplacer ». Jusque-là ces mails étaient seulement
    #    EMPÊCHÉS de devenir un ticket, donc ils s'accumulaient.
    dossier_metier = DOSSIER_DU_DOMAINE.get(
        (prop.get("domaine_client_hist") or "").upper())
    # L'HISTORIQUE d'un client ne tranche que lorsque le mail, lui, ne dit
    # rien. Le 2026-09-22, « URGENT création de compte M365 » (MARC FOUJOLS,
    # client dont tous les tickets sont en domaine TEL) est parti en BAL
    # TELECOM : le classement regardait le client, jamais la demande. C'est la
    # même faute que les règles apprises sur un expéditeur — classer sur qui
    # écrit plutôt que sur ce qui est écrit. Le corps parle informatique : il
    # l'emporte.
    if dossier_metier:
        _dom_txt, _force_txt = deviner_domaine(texte)
        if _force_txt and _dom_txt in DOMAINES_INFORMATIQUES:
            dossier_metier = None
    if dossier_metier:
        return {"action": "ranger", "cible": dossier_metier,
                "pourquoi": "tout l'historique de ce client est en domaine %s"
                            % prop.get("domaine_client_hist"),
                "confiance": "haute"}

    # 4. Demande client. On ne crée que si l'essentiel est établi : le client
    #    sans ambiguïté ET un symptôme venu d'une règle métier, pas d'un
    #    classement de fréquence.
    if not prop.get("client") or prop.get("doute_client"):
        return {"action": "attendre", "cible": None,
                "pourquoi": "client non établi", "confiance": "nulle"}
    # Le symptôme ne bloque PLUS la prise de position. Il l'a fait un temps, et
    # la mesure a montré l'impasse : l'outil s'abstenait sur 30 propositions
    # sur 32, donc n'apprenait rien. « Faut-il créer une DIT pour ce client ? »
    # et « quel symptôme ? » sont deux jugements de difficulté différente — les
    # mesurer ensemble revient à n'en mesurer aucun. On tranche donc le
    # premier, et le second est suivi à part (`symptome` ci-dessous).
    return {"action": "creer", "cible": None,
            "symptome": prop.get("symptome") or None,
            "pourquoi": "demande client, client %s%s"
                        % (prop.get("client"),
                           (", symptôme %s par règle métier" % prop["symptome"])
                           if prop.get("symptome") else
                           ", symptôme à choisir (aucune règle ne le donne)"),
            "confiance": ("haute" if (prop.get("tier") == "candidat_fort"
                                      and prop.get("symptome"))
                          else "moyenne" if prop.get("tier") == "candidat_fort"
                          else "basse")}


# ═══════════════════════════════════════════ traitement automatique
# Elliot, le 2026-09-15 : « vas-y commence le traitement automatique ».
#
# Ce qui part seul, et à quelles conditions. Rien n'est laissé au hasard :
# chaque action a sa propre porte, et « attendre » n'en franchit aucune.
AUTO_TRAITEMENT = True
AUTO_ACTIONS = {
    "ranger":    True,    # alertes : aucun jugement, la famille est reconnue
    "ecarter":   True,    # métier déjà tranché 2 fois pour ce client
    "transferer": True,   # mail adressé nommément à un collègue
    # Armé le 2026-09-15 : « fais tout en automatique, de toute façon j'ai
    # l'historique dans le journal traité ». Compléter écrit dans le ticket de
    # quelqu'un d'autre, mais le texte est AJOUTÉ, jamais substitué.
    "completer": True,
    "creer":     True,    # sous conditions strictes, cf. _creation_sans_doute
}
JOURNAL_AUTO_ACTIONS = DATA_DIR / "mail_dit_auto.json"

# Combien de DIT au MAXIMUM par passage. Le premier passage en a cree 5 en
# 25 secondes en vidant l'arriere : c'est exactement la « creation en masse »
# qu'Elliot voulait eviter. Le plafond horaire (12) ne l'avait pas vu, il
# raisonne sur l'heure, pas sur la rafale. Une erreur doit couter un ticket,
# pas cinq.
# Relevé de 2 à 4 le 2026-09-18 : le plafond visait les rafales du premier
# jour, où l'outil avait créé 5 DIT en 25 secondes en vidant l'arriéré. Depuis,
# le plafond horaire (50) et le délai entre deux DIT d'un même client tiennent
# la cadence, et 2 par passage faisait traîner un retard sur plusieurs cycles.
CREATIONS_PAR_PASSAGE = 4


def _creation_sans_doute(p):
    """« Au moindre doute tu ne crées rien » — la consigne, en code.

    On ne crée tout seul que si TOUT est établi : le client sans ambiguïté, un
    interlocuteur connu, un vrai texte de demande, un fil qui n'a pas déjà son
    ticket, et une confiance haute sur la classification. Tout le reste
    retourne dans la file, où un humain tranche.
    """
    # Le modèle peut relever la confiance : c'est le second motif de blocage
    # (73 propositions le 2026-09-17, dont 14 étaient de vraies demandes que je
    # créais à la main chaque matin). Un `TR:` ou un `RE:` est déclassé par
    # prudence alors que la demande est dans le fil — le modèle, lui, le lit.
    if p.get("tier") != "candidat_fort" and p.get("tier_modele") != "candidat_fort":
        # Exception mesurée : un collègue qui écrit AU support pour un client
        # ne poursuit pas un échange, il ouvre une demande. Le déclassement
        # « transfert interne DFM » s'applique à tout mail parti d'une adresse
        # @dfm.fr, ce qui est juste pour une conversation entre nous, faux dès
        # que le support est le destinataire et que le client est identifié.
        # Le 2026-09-22, « URGENT création de compte M365 » de Solène VOCI,
        # adressé à support@dfm.fr avec la cliente en copie, est resté bloqué
        # pour cette seule raison. Le garde-fou du doublon (même fil, 30 jours)
        # reste devant : ce n'est pas une porte ouverte aux relances.
        dests = [x for x in (p.get("destinataires") or []) if x]
        interne_vers_support = (
            (p.get("de") or "").lower().endswith("@dfm.fr")
            and any(BAL_SUPPORT in x for x in dests)
            and p.get("client") and not p.get("doute_client")
            and "transfert interne DFM" in (p.get("motif") or "")
            and not dit_du_meme_fil(p)[0])
        if not interne_vers_support:
            return "confiance de classification insuffisante (%s)" % p.get("tier")
    if not p.get("client") or p.get("doute_client"):
        return "client non établi"
    # PAS de blocage sur le demandeur. C'etait un ajout de ma part, contraire a
    # la consigne d'Elliot du 2026-09-14 : « si tu ne trouves pas le demandeur,
    # cree-le ; et si MALGRE tout tu ne trouves rien, TU CREES LA DIT QUAND
    # MEME, SANS DEMANDEUR ». Pire, le blocage s'appliquait AVANT que
    # `assurer_demandeur` ait eu la moindre chance de chercher ou de creer le
    # contact — P-0272 a ete refusee alors que l'adresse de Romuald HAMON
    # etait dans le mail et que le client a 97 interlocuteurs.
    if p.get("categorie") != "hotline":
        return "catégorie %s : hors du cas courant" % p.get("categorie")
    # « Le mail appartient à un fil » n'est PLUS un motif de refus. Ce
    # garde-fou datait d'avant le contrôle de doublon sérieux : il refusait
    # toute suite de conversation par peur d'un second ticket, alors que
    # `dits_proches` sur 30 jours répond à la vraie question — existe-t-il déjà
    # un ticket ? Résultat mesuré : la demande de VPN de Mediascore, réclamée
    # depuis des semaines dans un fil, n'a jamais pu devenir un ticket
    # (Elliot, 2026-09-18). Un fil sans ticket, c'est une demande sans ticket.
    if (p.get("fil_messages") or 1) > 1 and not p.get("client"):
        return "mail dans un fil, et client non établi"
    # Un corps vide n'est un obstacle que si l'OBJET ne dit rien non plus.
    # « PAS DE CEGID AU SECOURS ON EST TS BLOQUE » (IML GROUP, 2026-09-17) :
    # tout est dans le sujet, le corps ne contient qu'une signature — et le
    # ticket est reste bloque alors que le client entier ne travaillait plus.
    muet = corps_muet(p.get("commentaire"))
    if muet and corps_muet(p.get("objet")):
        return "ni le corps ni l'objet ne portent de demande lisible"
    # Des PHOTOS sans une phrase, ce n'est pas une demande : c'est une RÉPONSE
    # à une demande — presque toujours « envoyez-moi des photos », faite par
    # téléphone ou depuis une autre boîte. Elliot, 2026-09-17, sur le DIT
    # 100174363 : « je pense qu'un tech a demandé d'envoyer des photos et du
    # coup ça a créé ce ticket ». Ce mail-là ne portait que trois photos et
    # « Envoyé à partir de Outlook pour iOS », avec pour objet un nom et un
    # site. Ces photos doivent rejoindre un ticket, pas en ouvrir un.
    if muet and (p.get("pieces") or p.get("photos")):
        return ("mail sans texte, seulement des pièces : à rattacher à une "
                "demande existante, pas à ticketiser")
    # Client dont TOUT l'historique Artis est d'un autre metier : la DIT
    # 100173967 (LL AVOCATS, « telephonie et copieur ») est nee comme ca, chez
    # un client dont les deux tickets precedents sont en domaine Impression.
    # On ne l'interdit pas a l'humain — seulement a l'automatique.
    if dossier_metier_du_fil(p):
        return ("ce fil est déjà rangé dans la boîte d'un autre métier")
    dom = (p.get("domaine_client_hist") or "").upper()
    # On ne bloque que si l'on sait OU envoyer le mail : un domaine qu'aucune
    # boite metier ne reclame n'a aucune issue, et bloquer sans issue revient a
    # perdre le mail. Les variantes du domaine informatique ne bloquent jamais.
    if dom and dom not in DOMAINES_INFORMATIQUES and dom in DOSSIER_DU_DOMAINE:
        # Même réserve qu'à la décision : un mail qui parle informatique n'est
        # pas retenu par l'historique de son client.
        _dom_txt, _force_txt = deviner_domaine(
            (p.get("objet") or "") + chr(10) + (p.get("commentaire") or "")[:2000])
        if not (_force_txt and _dom_txt in DOMAINES_INFORMATIQUES):
            return ("l'historique de ce client est en domaine %s, pas "
                    "informatique" % dom)
    # Un client MULTI-MÉTIER n'est pas un client d'un autre métier. Elliot,
    # 2026-09-17, sur Steam-One : « c'est bien de l'info, c'est un client
    # multi-métier, tu vois bien le nombre de DIT INFO ». Deux mails écartés à
    # la main ne pèsent rien face à un historique informatique fourni — ce
    # garde-fou ne vaut que pour les clients qui n'ont RIEN chez nous en INFO.
    if (p.get("metier_signale")
            and (p.get("domaine_client_hist") or "").upper()
            not in DOMAINES_INFORMATIQUES):
        # Ce signal vient d'AUTRES mails du meme client, jamais de celui-ci.
        # Quand le modele a lu CE texte et conclu a une demande de support en
        # confiance haute, c'est lui qui a raison : il a vu la demande, le
        # signal ne voit qu'un antecedent. On garde le blocage si le texte
        # parle lui-meme de l'autre metier — la, les deux concordent.
        # 2026-09-24, BAYVET : « Acces extranet », creation d'un compte Artis
        # pour un nouveau comptable, bloquee comme telephonie et restee en
        # boite toute la journee.
        _d_txt, _f_txt = deviner_domaine(
            (p.get("objet") or "") + chr(10) + (p.get("commentaire") or "")[:2000])
        _texte_confirme = bool(_f_txt) and _d_txt not in DOMAINES_INFORMATIQUES
        if p.get("tier_modele") != "candidat_fort" or _texte_confirme:
            return "ce client a déjà été écarté comme %s" % (
                p["metier_signale"].get("libelle"))
    # « Adressé à un collègue » empêche de ticketiser ce qui doit être
    # transmis. Mais un collègue qui écrit AU SUPPORT pour un client fait
    # l'inverse : il demande une intervention. « IT URGENT SCI FONCIERE HEMA
    # SVP » (2026-09-17) est resté bloqué pour cette raison. Quand le modèle a
    # lu le mail et conclu à une demande client, en confiance haute, c'est lui
    # qui a raison — il a vu le texte, ce garde-fou ne voit que des adresses.
    if p.get("destinataire_interne") and p.get("tier_modele") != "candidat_fort":
        return "le mail semble adressé à un collègue"
    return ""


APPELS_MODELE_PAR_PASSAGE = 5


_RE_MOT_CLIENT = re.compile(r"[A-Za-zÀ-ÿ0-9]{4,}")


def clients_candidats(m, maxi=10):
    """Les clients dont le NOM ressemble au domaine ou à l'objet du mail.

    La détection d'origine ne connaît que l'adresse exacte, le domaine déjà
    enregistré, et le code écrit en clair. `com-cwl.com` ne lui disait rien,
    alors que le client 30869 s'appelle CWL — et ce seul motif bloquait
    73 propositions le 2026-09-17. On ne tranche pas ici : on propose une
    liste courte, et c'est le modèle qui nomme, ou personne.
    """
    exp = addr(m)
    dom = exp.split("@")[-1].lower()
    racine = dom.rsplit(".", 1)[0] if "." in dom else dom
    mots = set()
    for bout in re.split(r"[.\-_]", racine):
        if len(bout) >= 4 and bout not in ("mail", "info", "contact", "group",
                                           "groupe", "france", "cabinet"):
            mots.add(bout)
    for w in _RE_MOT_CLIENT.findall(m.get("subject") or ""):
        if len(w) >= 4:
            mots.add(w.lower())
    if not mots:
        return []
    out = []
    for code, nom in _libelles_clients().items():
        n = (nom or "").lower()
        if not n:
            continue
        score = sum(1 for w in mots if w in n)
        if score:
            out.append((score, len(n), code, nom))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [(c, n) for _s, _l, c, n in out[:maxi]]


def symptomes_pour_modele(maxi=26):
    """Les symptômes informatiques réellement employés, du plus fréquent au
    moins fréquent : [(code, libelle, nombre)].

    On ne propose au modèle que ce que l'équipe utilise vraiment — un
    catalogue complet l'inviterait à choisir des codes que personne ne met.
    """
    s = _symptomes()
    lib = s.get("libelles") or {}
    out = []
    for x in (s.get("global") or []):
        code = x.get("code") or ""
        if not code.startswith(("PC-", "ADM-", "SRV-", "NET-")):
            continue                      # les autres domaines ont leur equipe
        out.append((code, lib.get(code, ""), x.get("n") or 0))
        if len(out) >= maxi:
            break
    return out


def symptome_par_modele(prop, log=None):
    """Le symptôme lu dans le mail, quand aucune règle ne l'a donné.

    C'est le point où la qualité des tickets se joue : sans règle, la création
    retombe sur « PC/MAC - Autres », et Elliot le voit sur chaque ticket.
    """
    try:
        import mail_modele as MM
    except Exception:
        return None
    if prop.get("symptome"):
        return None
    d = MM.avis_symptome(prop.get("objet"), prop.get("commentaire"),
                         symptomes_pour_modele(), log=log)
    return d


def _creer_depuis_modele(m, avis, log=None):
    """Crée le DIT d'un mail que les règles n'avaient pas retenu comme demande.

    Le modèle a lu, nommé le client et jugé en confiance haute. On ne lui fait
    pas confiance pour autant : la demande passe par `verifier` (champs
    obligatoires), par le contrôle de doublon et par la cadence, exactement
    comme une proposition ordinaire. Rend True si un ticket est né.
    """
    dire = log or (lambda *_a: None)
    import mail_dit_ecriture as E
    code = avis["client"]
    lib = _libelles_clients()
    a = _attrs_clients().get(code) or {}
    corps = body_utile((m.get("body") or {}).get("content") or "")
    histo = historique_cite((m.get("body") or {}).get("content") or "") or ""
    prop = {
        "id": "MODELE-%s" % (m.get("internetMessageId") or "")[:24],
        "client": code, "client_lib": lib.get(code, ""),
        "site": a.get("site"), "bien": a.get("bien") or bien_informatique(code),
        "org_int": a.get("orgInt") or "DFM",
        "objet": m.get("subject"), "recu": m.get("receivedDateTime"),
        "mail_id": m.get("id"), "message_id": m.get("internetMessageId"),
        # Le fil : c'est lui qui relie une relance au ticket deja ouvert.
        "conversation": m.get("conversationId"),
        "de": addr(m),
        "de_nom": ((m.get("from") or {}).get("emailAddress") or {}).get("name"),
        "commentaire": (corps + ("\n\n" + SEPARATEUR_HISTO + "\n" + histo
                                 if histo else "")).strip(),
        "pourquoi_modele": (avis.get("pourquoi") or "")[:200],
    }
    sym = deviner_symptome(prop["commentaire"] + " " + (prop["objet"] or ""), "")
    if isinstance(sym, tuple) and sym[0]:
        prop["symptome"] = sym[0]
    prop["uo"] = uo_pour(prop.get("symptome"))

    # Même garde-fou que pour les propositions : des pièces sans une phrase
    # répondent à une demande, elles n'en ouvrent pas une.
    if corps_muet(prop["commentaire"]) and (m.get("hasAttachments")):
        dire("[mail-modele] %s : que des pieces, aucune phrase — on ne cree pas"
             % (m.get("subject") or "")[:40])
        return False
    # Le fil a deja donne un ticket encore ouvert : c'est une PREUVE, pas une
    # ressemblance. On la place avant la comparaison de textes, qui ne sait pas
    # reconnaitre une relance (2026-09-21, HOLDING HBL / serveur SAGE).
    metier_fil = dossier_metier_du_fil(prop)
    if metier_fil:
        dire("[mail-modele] %s : fil deja range en %s, on suit"
             % ((m.get("subject") or "")[:40], metier_fil))
        res = E.ranger_mail(m["id"], metier_fil, confirmer=True)
        pourquoi = "ce fil est deja range en %s" % metier_fil
        _journal_auto({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": prop["message_id"], "proposition": "",
            "action": "ranger", "cible": metier_fil,
            "objet": (prop["objet"] or "")[:140], "de": prop["de"],
            "client": code, "execute": bool(res.get("envoye")),
            "resultat": res.get("destination") or res.get("erreur"),
            "pourquoi": pourquoi, "raison": pourquoi})
        return bool(res.get("envoye"))

    num_fil, etat_fil = dit_du_meme_fil(prop)
    if num_fil:
        dire("[mail-modele] %s : meme fil que le DIT %s (%s), on complete"
             % ((m.get("subject") or "")[:40], num_fil, etat_fil))
        res = E.completer_dit_direct(num_fil, prop, log=dire)
        pourquoi = "meme fil que le DIT %s (%s)" % (num_fil, etat_fil)
        _journal_auto({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": prop["message_id"], "proposition": "",
            "action": "completer", "cible": num_fil,
            "objet": (prop["objet"] or "")[:140], "de": prop["de"],
            "client": code, "dit": num_fil, "execute": bool(res.get("ok")),
            "resultat": res.get("resume") or "",
            "pourquoi": pourquoi, "raison": pourquoi})
        return bool(res.get("ok"))

    # Meme fenetre large que `decider_seul` : une relance arrive une ou deux
    # semaines apres la demande, pas trois jours apres.
    proches = [d for d in dits_proches(prop, jours=30)
               if d["ressemblance"] >= 0.7]
    # Un ticket OUVERT l'emporte sur un ticket annulé ou clôturé à
    # ressemblance égale : compléter une DIT annulée ne sert personne. Le
    # 2026-09-18, « Sanaz Courdkhalag Laptop Set-up » visait le DIT 100173943
    # (Annulée, 93 %) alors que le 100174354 (EnCours, 93 %) l'attendait.
    proches.sort(key=lambda d: (0 if (d.get("etat") or "") in ETATS_OUVERTS
                                else 1, -d["ressemblance"]))
    if proches:
        # Doublon : on ne crée pas, mais on ne laisse pas le mail en boîte non
        # plus. Il rejoint le ticket existant — c'est la suite de la même
        # demande. Sans cela, « RE: configuration d'un ordinateur » restait
        # indéfiniment, refusé à la création et pris en charge par personne.
        num = proches[0]["numero"]
        dire("[mail-modele] %s : suite du DIT %s, on complete"
             % ((m.get("subject") or "")[:40], num))
        res = E.completer_dit_direct(num, prop, log=dire)
        pourquoi = "suite du DIT %s (%s)" % (num, (avis.get("pourquoi") or "")[:120])
        _journal_auto({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": prop["message_id"], "proposition": "",
            "action": "completer", "cible": num,
            "objet": (prop["objet"] or "")[:140], "de": prop["de"],
            "client": code, "dit": num, "execute": bool(res.get("ok")),
            "resultat": res.get("resume") or "",
            "pourquoi": pourquoi, "raison": pourquoi})
        return bool(res.get("ok"))
    # LE DEMANDEUR. Le chemin des propositions le cherche (`assurer_demandeur`),
    # celui du modele ne le faisait PAS : il batit sa demande en memoire et
    # allait droit a la creation. Tout ticket ne par ce pont sortait donc sans
    # demandeur, meme quand le contact etait ecrit noir sur blanc dans
    # l'echange. Elliot, 2026-09-23, sur la DIT 100174566 : « il n'a pas repere
    # le contact alors qu'il est dans les echanges (mouchot) ».
    if prop.get("client") and not prop.get("pid_interlocuteur"):
        adr, nom_aff = (prop.get("de") or ""), (prop.get("de_nom") or "")
        # Jamais un collegue : meme regle que pour les propositions.
        if est_interne(adr):
            adr, nom_aff = "", ""
        t = None
        try:
            if adr or nom_aff:
                t = E.trouver_demandeur(prop["client"], nom_aff, adr)
        except Exception:
            t = None
        if not (t and t.get("id")):
            try:
                t = E.demandeur_du_texte(
                    prop["client"],
                    (prop.get("objet") or "") + chr(10)
                    + (prop.get("commentaire") or "")[:2000])
            except Exception:
                t = None
        if t and t.get("id"):
            prop["pid_interlocuteur"] = t["id"]
            dire("[mail-modele] demandeur trouve : %s" % (t.get("comment") or t["id"]))

    bloquants, _avertis = E.verifier(prop)
    if bloquants:
        dire("[mail-modele] %s : %s" % ((m.get("subject") or "")[:40], bloquants))
        return False

    res = E.creer_dit(prop, confirmer=True)
    numero = res.get("numero")
    pourquoi = "modèle : %s" % (avis.get("pourquoi") or "")[:170]
    # Le ticket existe deja chez ce client pour ce meme texte : on ne le
    # recree pas, et surtout on ne laisse pas le mail en boite pour autant.
    if res.get("doublon_de"):
        _journal_auto({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": prop["message_id"], "proposition": "",
            "action": "creer", "objet": (prop["objet"] or "")[:140],
            "de": prop["de"], "client": code, "execute": True,
            "resultat": "DIT %s existe deja pour cette demande" % res["doublon_de"],
            "dit": res["doublon_de"], "pourquoi": pourquoi, "raison": pourquoi})
        E.ranger_mail(m["id"], "traite", confirmer=True)
        dire("[mail-modele] doublon evite : DIT %s couvre deja %s"
             % (res["doublon_de"], (m.get("subject") or "")[:40]))
        return True
    if not res.get("envoye"):
        _journal_auto({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": prop["message_id"], "proposition": "",
            "action": "creer", "objet": (prop["objet"] or "")[:140],
            "de": prop["de"], "client": code, "execute": False,
            "resultat": (res.get("erreur") or "création refusée")[:140],
            "pourquoi": pourquoi, "raison": pourquoi})
        return False
    try:
        E.joindre_pieces(numero, prop, True)
    except Exception:
        pass
    # La MEME finition que sur le chemin des propositions : type de travail
    # « Hotline » et facturation « Non facturable ». Sans cet appel, les
    # tickets nes du modele sortaient en « Terrain / Automatique » — un ecart
    # invisible a l'oeil nu mais qui touche la facturation (2026-09-23).
    try:
        constat, _tt = E.finaliser_dit(numero, prop, True)
        if (constat.get("facturation") or "") != "NonFacturable":
            dire("[mail-modele] DIT %s : facturation restee %s, a corriger"
                 % (numero, constat.get("facturation") or "inconnue"))
    except Exception as e:
        dire("[mail-modele] finition impossible sur %s : %s" % (numero, str(e)[:110]))
    mail = E.ranger_mail(m["id"], "traite", confirmer=True)
    dire("[mail-modele] DIT %s creee pour %s" % (numero, code))
    _journal_auto({
        "quand": datetime.datetime.now().isoformat(timespec="seconds"),
        "message_id": prop["message_id"], "proposition": "",
        "action": "creer", "cible": numero, "objet": (prop["objet"] or "")[:140],
        "de": prop["de"], "client": code, "dit": numero, "execute": True,
        "resultat": "DIT %s créée%s" % (numero, "" if mail.get("envoye")
                                        else " (mail non déplacé)"),
        "pourquoi": pourquoi, "raison": pourquoi})
    return True


# Une regle apprise vaut pour un EXPEDITEUR entier : elle n'est donc legitime
# que si cet expediteur envoie toujours la meme chose — autrement dit une
# machine. Une PERSONNE ecrit un jour sur ses factures et le lendemain pour un
# depannage : lui coller un dossier revient a mal classer tout ce qu'elle
# enverra ensuite. Constate le 2026-09-21 : une question de facturation d'AQC a
# fait naitre la regle « m.bernadin@qualiteconstruction.com -> devis », et onze
# minutes plus tard sa relance « Merci de contacter Monsieur DAUCE, on ne l'a
# toujours pas rappele » partait en 03 DEVIS, invisible du support.
_RE_BAL_MACHINE = re.compile(
    r"^(no[-_.]?reply|ne[-_.]?pas[-_.]?repondre|donotreply|do[-_.]?not[-_.]?reply"
    r"|mailer[-_.]?daemon|postmaster|alert|alerte|alarme|alarm|monitor|monitoring"
    r"|supervision|noc|backup|sauvegarde|scan|copieur|notification|notifications"
    r"|info[-_.]?sys|system|root|admin|robot|bot|auto|service[-_.]?desk)\b",
    re.I)
# Un compte de machine porte souvent une longue suite de chiffres :
# « scanbcs202301@bcsystemit.fr », « FW-AMFD94-01 ».
_RE_LOCAL_CHIFFRE = re.compile(r"\d{4,}")


# Ce que le modele dit LUI-MEME d'un mail automatique. C'est le signe le plus
# sur dont on dispose sans relire le mail : il a lu le texte, et il ecrit
# pourquoi. « Notification automatique d'une sauvegarde », « message
# automatique de non-remise », « alerte automatique d'un firewall », « mail de
# prospection commerciale ».
_RE_MOTIF_MACHINE = re.compile(
    r"automatiqu|notification|notifie|rapport (?:de|d')|alerte|alarme"
    r"|newsletter|prospection|publicit|mailing|no[- ]?reply|non[- ]remise"
    r"|mailer[- ]?daemon|accus[ée] de r[ée]ception|monitoring|supervision",
    re.I)


def expediteur_automatique(m, pourquoi=""):
    """Peut-on retenir une regle pour CET expediteur ?

    Oui seulement si ses mails sont ecrits par une machine. Deux preuves, et
    une seule suffit : l'adresse est une boite de service, ou le motif du
    classement decrit lui-meme un message automatique.

    Par defaut on repond NON. Se tromper de ce cote coute une lecture du
    modele ; se tromper de l'autre fait disparaitre les mails d'une personne
    pendant des mois. Le 2026-09-21, une question de facturation d'AQC avait
    fait naitre « m.bernadin@qualiteconstruction.com -> devis » et, onze
    minutes plus tard, sa relance « on ne l'a toujours pas rappele » partait
    en 03 DEVIS, invisible du support.
    """
    adresse = addr(m) or ""
    local = adresse.split("@")[0]
    if _RE_BAL_MACHINE.match(local) or _RE_LOCAL_CHIFFRE.search(local):
        return True
    return bool(_RE_MOTIF_MACHINE.search(pourquoi or ""))


def _avis_modele_sur_restes(msgs, log=None):
    """Ce que les règles n'ont pas su trancher, on le fait LIRE.

    Elliot, 2026-09-17 : « le robot et toi c'est la même chose, vous devez
    fonctionner de la même manière ». Le moteur ne connaît que ses motifs ; ce
    passage lui donne la lecture qui lui manque, sur les seuls mails qu'aucune
    règle ne prend et qu'aucun ticket n'attend.

    Trois économies, dans l'ordre :
      1. une règle APPRISE sur l'expéditeur répond gratuitement ;
      2. un mail déjà soumis n'est jamais resoumis ;
      3. au plus `APPELS_MODELE_PAR_PASSAGE` lectures par cycle.
    Et si le modèle n'est pas là (crédit épuisé, clé absente), tout continue
    comme avant : `avis()` rend None.
    """
    dire = log or (lambda *_a: None)
    try:
        import mail_modele as MM
        import mail_dit_ecriture as E
    except Exception:
        return 0
    if not E.ecriture_armee():
        return 0

    num_dits = _num_dits()
    lib = _libelles_clients()
    faits = 0
    crees_modele = [0]          # compteur partage, borne par CREATIONS_PAR_PASSAGE
    with _lock:
        _d = _charger()
        _mids = {x.get("message_id") for x in _d["props"] if x.get("message_id")}

    def p_existe(mid):
        return mid in _mids

    for m in msgs:
        if faits >= APPELS_MODELE_PAR_PASSAGE:
            break
        mid = m.get("internetMessageId")
        exp = addr(m)
        subj = m.get("subject") or ""
        if not mid or not exp:
            continue
        # Une règle écrite passe avant : ce mail est déjà pris en charge.
        if any((not r.get("expediteur") or exp == r["expediteur"])
               and (r["quand"](m) if r.get("quand") else r["objet"].match(subj))
               for r in RANGEMENTS_AUTO):
            continue
        # Un mail qui cite un ticket relève du suivi, pas du classement.
        if RE_DIT.search(subj):
            continue

        appris = MM.regle_apprise(exp)
        # Une regle apprise sur une PERSONNE ne vaut rien : on la laisse dans
        # le fichier (elle y est datee et lisible) mais on ne s'en sert pas,
        # et le mail repart par la lecture normale.
        if appris and not expediteur_automatique(m, appris.get("pourquoi")):
            appris = None
        avis = None
        if appris:
            avis = {"action": "ranger", "dossier": appris["dossier"],
                    "confiance": "haute",
                    "pourquoi": "règle apprise : " + (appris.get("pourquoi") or ""),
                    "cout_eur": 0.0, "appris": True}
        else:
            # Un avis deja rendu se REJOUE, gratuitement : c'est ce qui permet
            # de corriger la façon de s'en servir sans repayer la lecture.
            avis = MM.avis_memorise(mid)
            if avis:
                avis = dict(avis, cout_eur=0.0, rejoue=True)
            elif MM.deja_vu(mid) or not MM.disponible():
                continue
            else:
                # TOUT l'echange, pas le dernier message. Elliot, 2026-09-17 :
                # « il n'y a pas de ticket a creer, faut bien lire tout
                # l'historique d'echange ». Cinq mails d'affilee etaient des
                # suites de fil — un rendez-vous confirme depuis huit jours, un
                # Mac parti en reparation en aout, une relance sur un copieur —
                # et le dernier message, seul, ne disait rien de tout cela.
                brut = (m.get("body") or {}).get("content") or ""
                corps = body_utile(brut)
                histo = historique_cite(brut) or ""
                if histo:
                    corps = (corps + "\n\n--- messages precedents du meme "
                             "echange ---\n" + histo[:3000])
                client, _pid, _meth = trouver_client(m, _index_clients())
                possibles = ([(client, lib.get(client, ""))] if client
                             else clients_candidats(m))
                avis = MM.avis(subj, corps, exp,
                               ", ".join(((r.get("emailAddress") or {}).get("address")
                                          or "") for r in (m.get("toRecipients") or [])),
                               possibles, log=dire)
                MM.marquer_vu(mid)
                if avis:
                    MM.memoriser_avis(mid, avis)
                faits += 1
        if not avis:
            continue

        action = avis.get("action")
        # Le modèle écrit parfois le DOSSIER à la place de l'action
        # (« traite », « terrain »). C'est sans ambiguïté : on le remet à sa
        # place plutôt que de perdre une décision juste sur une question de
        # forme.
        if action not in ("ranger", "creer", "laisser") and action in E.DOSSIERS:
            avis = dict(avis, dossier=avis.get("dossier") or action)
            action = "ranger"
        # « laisser » en CONFIANCE HAUTE n'est pas une hésitation : c'est un
        # constat — échange entre tiers, relance interne sans demande. Ces
        # mails n'ont aucune issue et s'accumulaient (4 sur 11 le
        # 2026-09-17). Ils partent en 01 DIT ARTIS, d'où ils restent lisibles.
        # En confiance basse ou moyenne, en revanche, on laisse vraiment :
        # c'est le doute, et le doute appartient à un humain.
        if action == "laisser" and avis.get("confiance") == "haute":
            avis = dict(avis, dossier="traite")
            action = "ranger"
        pourquoi = "modèle : %s" % (avis.get("pourquoi") or "")[:180]
        if action == "creer":
            # Le modèle a identifié le client que la détection n'a pas su
            # établir — c'est le premier motif de blocage (73 propositions
            # mesurées le 2026-09-17). On le POSE sur la proposition et on
            # rouvre le traitement automatique : la création elle-même
            # repassera par tous ses garde-fous, le modèle ne crée rien.
            code = (avis.get("client") or "").strip()
            # `etat()` ne porte PAS de clé « propositions » — c'est l'API qui
            # les ajoute à côté. Chercher là revenait à ne rien trouver
            # jamais, et toute cette branche ne s'appliquait pas (constaté le
            # 2026-09-17 : le modèle nommait les clients, rien n'en sortait).
            with _lock:
                d = _charger()
                p = next((dict(x) for x in d["props"]
                          if x.get("message_id") == mid), None)
            if p:
                champs = {}
                if code in lib and not p.get("client"):
                    a = (_attrs_clients().get(code) or {})
                    champs = {"client": code, "client_lib": lib.get(code, ""),
                              "site": a.get("site"),
                              "bien": a.get("bien") or bien_informatique(code),
                              "org_int": a.get("orgInt") or "DFM"}
                # Le modèle doute souvent du CLIENT, jamais de la demande —
                # et le client, les règles le tiennent déjà par une adresse
                # exacte. Lui demander de confirmer ce qu'on sait déjà revenait
                # à perdre son verdict : le 2026-09-21, « TR: ton PC »
                # (Packoffice en panne, numéro de portable de l'utilisatrice
                # dans le mail) est resté en boîte parce que le modèle avait
                # répondu « créer, confiance moyenne, client non identifié »
                # alors que l'adresse de l'expéditeur désignait SDVO sans
                # ambiguïté. Quand le client est établi par adresse exacte, la
                # confiance moyenne du modèle porte sur la seule question qui
                # reste — est-ce une demande — et cela suffit.
                client_certain = (p.get("methode_client") == "email exact"
                                  and p.get("client")
                                  and not p.get("doute_client"))
                assez = (avis.get("confiance") == "haute"
                         or (avis.get("confiance") == "moyenne" and client_certain))
                # Une seule fois : sans cette condition, un avis rejoué à
                # chaque cycle effacerait sans fin la décision d'ombre et la
                # marque d'essai, et la proposition tournerait en boucle.
                if assez and not p.get("tier_modele"):
                    champs["tier_modele"] = "candidat_fort"
                    champs["pourquoi_modele"] = (avis.get("pourquoi") or "")[:200]
                if champs:
                    corriger(p["id"], {k: v for k, v in champs.items() if v},
                             "modèle")
                    with _lock:
                        d = _charger()
                        for x in d["props"]:
                            if x.get("id") == p["id"]:
                                x.pop("auto_tente", None)
                                x.pop("auto_version", None)
                                # La décision d'ombre avait été prise SANS le
                                # client ni la confiance : elle est périmée.
                                # L'effacer la fait recalculer au cycle
                                # suivant, sinon la proposition resterait sur
                                # « attendre » pour toujours.
                                x.pop("ombre", None)
                        _sauver(d)
                    dire("[mail-modele] %s : client %s, confiance %s (%s)"
                         % (p["id"], code, avis.get("confiance"),
                            (avis.get("pourquoi") or "")[:50]))
        # Demande reconnue, client nommé, mais AUCUNE proposition pour la
        # porter : le mail n'avait pas été retenu comme candidat par les
        # règles. Sans ce pont, ces mails restaient en boîte indéfiniment —
        # trois des sept restants le 2026-09-17. On bâtit la demande ici, et
        # elle traverse ensuite les mêmes contrôles que toutes les autres :
        # doublon, champs obligatoires, cadence.
        if (action == "creer" and avis.get("confiance") == "haute"
                and (avis.get("client") or "") in lib and not p_existe(mid)
                and crees_modele[0] < CREATIONS_PAR_PASSAGE):
            fait = _creer_depuis_modele(m, avis, dire)
            crees_modele[0] += 1 if fait else 0
            if fait:
                continue

        if action != "ranger" or avis.get("confiance") == "basse":
            # « creer » et « laisser » repassent par le chemin normal : le
            # modèle ne court-circuite jamais les garde-fous de création.
            # On écrit CE QUE LE MODÈLE A DIT, pas « attendre » : lire
            # « attendre » sur un mail que le modèle a reconnu comme une
            # demande donne l'impression que rien n'a été compris.
            _journal_auto({
                "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                "message_id": mid, "proposition": "",
                "action": action or "attendre",
                "objet": subj[:140], "de": exp, "execute": False,
                "resultat": ("reconnu comme une demande, création au prochain "
                             "cycle" if action == "creer" else ""),
                "pourquoi": pourquoi, "raison": pourquoi,
                "cout_eur": avis.get("cout_eur")})
            continue

        dossier = avis.get("dossier")
        if dossier not in E.DOSSIERS:
            continue
        res = E.ranger_mail(m["id"], dossier, confirmer=True)
        if (res.get("envoye") and not avis.get("appris")
                and expediteur_automatique(m, avis.get("pourquoi"))):
            # Le robot vient d'apprendre : le prochain mail de cet expéditeur
            # ne coûtera rien. Uniquement sur une MACHINE — cf.
            # `expediteur_automatique`.
            MM.apprendre(exp, dossier, avis.get("pourquoi"))
        dire("[mail-modele] %s -> %s (%s)" % (subj[:50], dossier,
                                              (avis.get("pourquoi") or "")[:60]))
        _journal_auto({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": mid, "proposition": "", "action": "ranger",
            "cible": dossier, "objet": subj[:140], "de": exp,
            "execute": bool(res.get("envoye")),
            "resultat": res.get("destination") or res.get("erreur"),
            "pourquoi": pourquoi, "raison": pourquoi,
            "cout_eur": avis.get("cout_eur")})
    return faits


def _journal_auto(ligne=None):
    """Journal de TOUT ce que l'outil a fait seul. Consigne d'Elliot :
    « CONSIGNE et ENREGISTRE TOUT CE QUI A ÉTÉ CRÉÉ à partir de maintenant ».

    Depuis le 2026-09-15 il contient AUSSI un ligne par mail de la boite, meme
    sans action : c'est la liste de travail. Une action retrouve donc la ligne
    d'attente du meme mail et la complete, au lieu d'en empiler une seconde.
    """
    try:
        j = (json.loads(JOURNAL_AUTO_ACTIONS.read_text(encoding="utf-8"))
             if JOURNAL_AUTO_ACTIONS.exists() else [])
    except Exception:
        j = []
    if ligne is None:
        return list(reversed(j))
    mid = ligne.get("message_id")
    fusion = None
    if mid:
        # UNE ligne par mail. On reprend la derniere ligne de ce message,
        # executee ou non : Elliot veut lire « ce qui a ete fait », pas une
        # pile de tentatives. Avant cette correction, « Formatage PC »
        # apparaissait deux fois — une ligne « creer FAIT » et une ligne
        # « attendre » — et le meme mail annoncait deux dossiers differents.
        for x in reversed(j):
            if x.get("message_id") == mid:
                fusion = x
                break
    if fusion is not None:
        # Une ligne deja executee ne se laisse pas effacer par une ligne qui,
        # elle, n'a rien fait : le compte rendu d'une action vaut plus qu'une
        # intention.
        if fusion.get("execute") and not ligne.get("execute"):
            ligne = {k: v for k, v in ligne.items()
                     if k not in ("action", "cible", "execute", "resultat",
                                  "dit", "raison", "pourquoi")}
        avis = fusion.get("avis")
        fusion.update(ligne)
        if avis:
            fusion["avis"] = avis      # l'avis d'un agent ne se perd jamais
    else:
        j.append(ligne)
    try:
        JOURNAL_AUTO_ACTIONS.write_text(json.dumps(j[-2000:], ensure_ascii=False,
                                                   indent=1), encoding="utf-8")
    except Exception:
        pass
    return j


def journaliser_boite(msgs, log=None):
    """Une ligne de journal pour CHAQUE mail de la boite de reception.

    Elliot (2026-09-15) : « prends toutes les demandes dans la BAL, mets-les
    dans le journal automatique, et moi je te mets ce qu'il fallait faire ».

    Le journal cesse donc d'etre le seul carnet des actions executees : il
    devient la liste de travail de la BOITE. Un mail qui n'a rien declenche y
    figure avec la raison — c'est justement celui-la dont il faut parler.
    """
    dire = log or (lambda *_a: None)
    try:
        j = (json.loads(JOURNAL_AUTO_ACTIONS.read_text(encoding="utf-8"))
             if JOURNAL_AUTO_ACTIONS.exists() else [])
    except Exception:
        return 0
    connus = {x.get("message_id") for x in j if x.get("message_id")}
    # Le mail est-il ENCORE dans la boite ? C'est la seule mesure qui compte :
    # l'objectif d'Elliot est zero mail en boite de reception. Une ligne dont
    # le mail est parti est une ligne finie, quoi qu'il se soit passe.
    presents = {m.get("internetMessageId") for m in msgs}
    maintenant = datetime.datetime.now().isoformat(timespec="seconds")
    for x in j:
        mid = x.get("message_id")
        if not mid:
            # Sans identifiant de message, la ligne ne peut JAMAIS être
            # rapprochée de la boîte : la laisser « non traitée » la fait
            # traîner indéfiniment dans l'onglet, comme ces six lignes du
            # 15/09 qu'Elliot voyait encore le 17. Passé la journée, on la
            # considère close.
            if (x.get("quand") or "")[:10] < datetime.date.today().isoformat():
                x.setdefault("dans_la_boite", False)
                x.setdefault("sorti_le", x.get("quand") or maintenant)
            continue
        if mid in presents:
            x["dans_la_boite"] = True
            x.pop("sorti_le", None)
        else:
            # ABSENT DE LA BOÎTE = SORTI, sans condition. La version
            # précédente ne le notait que pour les lignes déjà passées par
            # `dans_la_boite = True` : une ligne écrite par une ACTION (elles
            # ne passent pas par ici) gardait le drapeau vide et restait
            # « non traitée » pour toujours. Elliot, 2026-09-17 : « j'ai
            # beaucoup de mails d'il y a longtemps » — c'étaient six lignes
            # du 15/09 dont le mail avait quitté la boîte depuis deux jours.
            x["dans_la_boite"] = False
            x.setdefault("sorti_le", maintenant)
    with _lock:
        d = _charger()
    props = {p.get("message_id"): p for p in d["props"]}
    num_dits = _num_dits()

    ajoutees = 0
    for m in msgs:
        mid = m.get("internetMessageId")
        if not mid or mid in connus:
            continue
        p = props.get(mid) or {}
        o = p.get("ombre") or {}
        verdict, motif = classer(m, num_dits)
        # Ce que l'outil a decide, ou a defaut pourquoi il n'a rien decide.
        if p.get("dit_numero"):
            action, raison = "creer", ""
        elif o.get("action") and o["action"] != "attendre":
            action, raison = o["action"], o.get("pourquoi") or ""
        else:
            action = "attendre"
            raison = (o.get("pourquoi") or "%s : %s" % (verdict, motif))
        j.append({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": mid,
            "proposition": p.get("id") or "",
            "action": action,
            "cible": o.get("cible"),
            "pourquoi": o.get("pourquoi") or motif,
            "objet": (m.get("subject") or "")[:140],
            "de": addr(m),
            "client": p.get("client"),
            "verdict": verdict,
            "execute": bool(p.get("dit_numero")),
            "dit": p.get("dit_numero") or None,
            "raison": raison,
            "dans_la_boite": True,
        })
        ajoutees += 1
    # On reecrit meme sans ajout : les sorties de boite viennent d'etre marquees.
    try:
        JOURNAL_AUTO_ACTIONS.write_text(json.dumps(j[-2000:], ensure_ascii=False,
                                                   indent=1), encoding="utf-8")
        if ajoutees:
            dire("[mail-dit] journal : %d mail(s) de la boite ajoutes" % ajoutees)
    except Exception:
        return 0
    return ajoutees


def journal_auto_corriger(pid, dit=None, resultat=""):
    """Met a jour une decision REFUSEE quand la suite lui donne tort.

    Elliot (2026-09-15) : « sur le journal automatique je le vois encore en non
    FAIT, tu peux corriger le journal en live quand tu fais des corrections ».
    On ne REECRIT pas la decision d'origine — elle reste, avec son motif, c'est
    le dossier de ce qui s'est passe. On lui ajoute sa suite : ce qui a ete
    fait ensuite, quand, et par quoi.
    """
    if not pid:
        return False
    try:
        j = (json.loads(JOURNAL_AUTO_ACTIONS.read_text(encoding="utf-8"))
             if JOURNAL_AUTO_ACTIONS.exists() else [])
    except Exception:
        return False
    change = False
    for x in reversed(j):
        if x.get("proposition") != pid or x.get("execute") or x.get("corrige_le"):
            continue
        x["corrige_le"] = datetime.datetime.now().isoformat(timespec="seconds")
        x["raison_initiale"] = x.get("raison")
        x["suite"] = resultat or ("DIT %s creee apres correction" % dit if dit
                                  else "traite depuis")
        if dit:
            x["dit"] = dit
        change = True
        break
    if change:
        try:
            JOURNAL_AUTO_ACTIONS.write_text(json.dumps(j[-2000:],
                                                       ensure_ascii=False,
                                                       indent=1), encoding="utf-8")
        except Exception:
            return False
    return change


def journal_auto_avis(proposition, quand, avis, qui="", message_id=""):
    """L'avis d'Elliot sur une decision automatique, garde AVEC la decision.

    Il les veut la et pas ailleurs (2026-09-15) : c'est l'endroit ou l'on voit
    ce que l'outil a decide, donc l'endroit ou l'on dit s'il a eu tort. Vaut
    aussi pour les REFUS — savoir qu'un refus etait injustifie vaut autant que
    savoir qu'une creation l'etait.
    """
    # Un mail de la boite n'a pas forcement de proposition : c'est alors son
    # identifiant de message qui le designe. Exiger `proposition` faisait
    # repondre 400 sur toutes ces lignes (signale par Elliot le 2026-09-15).
    if not ((proposition or message_id) and (avis or "").strip()):
        return False
    try:
        j = (json.loads(JOURNAL_AUTO_ACTIONS.read_text(encoding="utf-8"))
             if JOURNAL_AUTO_ACTIONS.exists() else [])
    except Exception:
        return False
    for x in j:
        if message_id:
            if x.get("message_id") != message_id:
                continue
        elif x.get("proposition") != proposition or not proposition:
            continue
        elif quand and x.get("quand") != quand:
            continue
        x.setdefault("avis", []).append({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "qui": qui or "agent", "texte": avis.strip()})
        try:
            JOURNAL_AUTO_ACTIONS.write_text(json.dumps(j[-2000:],
                                                       ensure_ascii=False,
                                                       indent=1), encoding="utf-8")
        except Exception:
            return False
        return True
    return False



# ═══════════════════════════════════ qui decide : l'IA d'abord, le script apres
# Elliot, 2026-09-22 puis 2026-09-25 : « c'est TOI, une IA via cle API, qui
# analyses et traites ; le script est la pour REMPLACER l'IA quand il n'y a
# plus de credit » — « c'est toi qui traites tout, c'est ca le but d'avoir un
# traitement reflechi et pas fige dans la roche ».
#
# Le traitement automatique du conteneur passait AVANT l'IA, tout simplement
# parce qu'il tourne toutes les 60 secondes : quoi que l'IA decide ensuite, le
# ticket etait deja cree. On lui apprend donc a ceder le pas.
#
# Le signal est un BATTEMENT DE COEUR depose par l'IA a chaque passage. Tant
# qu'il est frais, le script laisse a l'IA le temps de regarder ; des qu'il
# vieillit — session fermee, PC eteint, plus de credit — le script reprend la
# main immediatement et rien ne reste en souffrance.
PRESENCE_FICHIER = DATA_DIR / "ia_presence.json"
PRESENCE_FRAICHE_MIN = 40      # au-dela, on considere l'IA absente
DELAI_IA_MIN = 20              # temps laisse a l'IA sur un mail qu'elle n'a pas vu



def _minutes_depuis(quand):
    """Age en minutes d'une date ISO ou Graph, ou un tres grand nombre si on
    ne sait pas la lire : un mail dont on ignore l'age ne doit pas etre
    retenu indefiniment."""
    if not quand:
        return 10 ** 6
    t = str(quand).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(t)
    except Exception:
        return 10 ** 6
    maintenant = datetime.datetime.now(d.tzinfo) if d.tzinfo else datetime.datetime.now()
    return (maintenant - d).total_seconds() / 60.0



def battement_ia(qui="boucle"):
    """L'IA signale qu'elle est aux commandes. Appele a chaque passage."""
    try:
        PRESENCE_FICHIER.write_text(json.dumps({
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "qui": qui}), encoding="utf-8")
        return True
    except Exception:
        return False


def ia_presente():
    """L'IA a-t-elle donne signe de vie recemment ? (minutes, ou None)"""
    try:
        d = json.loads(PRESENCE_FICHIER.read_text(encoding="utf-8"))
        q = datetime.datetime.fromisoformat(d["quand"])
    except Exception:
        return None
    return (datetime.datetime.now() - q).total_seconds() / 60.0



def traiter_automatiquement(log=None, dans_la_boite=None):
    """Exécute les décisions du mode ombre sur les propositions en attente.

    Une proposition à la fois, jamais en rafale : les garde-fous de cadence de
    `mail_dit_ecriture` s'appliquent en plus, et la moindre erreur arrête le
    passage plutôt que d'enchaîner.
    """
    dire = log or (lambda *_a: None)
    if not AUTO_TRAITEMENT:
        return 0
    try:
        import mail_dit_ecriture as E
        if not E.ecriture_armee():
            return 0
    except Exception:
        return 0

    with _lock:
        d = _charger()
        # « Pas encore tentée » OU « tentée sous des règles qui ont change
        # depuis » : les corrections apportées aux règles doivent profiter aux
        # mails déjà refusés, sinon chaque règle ne vaut que pour l'avenir et
        # la boîte garde son retard.
        attente = [dict(x) for x in d["props"]
                   if x.get("etat") == "nouveau" and not x.get("dit_numero")
                   # Trois cas ouvrent un nouveau regard : jamais tentee, les
                   # regles ont change, ou — et c'est le plus simple — LE MAIL
                   # EST ENCORE EN BOITE. Ce dernier manquait : une proposition
                   # tentee sous les regles courantes n'etait plus jamais
                   # reexaminee, meme si son mail n'avait recu aucune issue.
                   # P-1035 est restee ainsi une heure en boite le 2026-09-22
                   # alors que son ticket existait deja. Un mail en boite est
                   # un mail sans issue : il merite un regard a chaque passage.
                   and (not x.get("auto_tente")
                        or (dans_la_boite is not None
                            and x.get("message_id") in dans_la_boite)
                        or x.get("auto_version") != REGLES_VERSION)
                   # ET LE MAIL DOIT ENCORE ETRE EN BOITE. Un mail sorti de la
                   # boite a RECU UNE ISSUE : un collegue l'a traite dans Artis,
                   # ou il a ete range sans ticket. Sa proposition, elle, reste
                   # « nouveau » pour toujours — personne ne la referme.
                   # Le 2026-10-01, a la seconde ou le drapeau d'ecriture a ete
                   # pose, neuf propositions du 29 septembre (P-1390 a P-1399)
                   # sont parties d'un coup : huit DIT en doublon exact, meme
                   # client, meme minute, meme objet que les tickets deja
                   # ouverts. L'anti-doublon n'a rien vu — sa fenetre est de
                   # 48 h, et ces mails avaient deux jours.
                   # Un arriere de propositions n'est pas un arriere de travail.
                   and (dans_la_boite is None
                        or x.get("message_id") in dans_la_boite)
                   and (x.get("ombre") or {}).get("action")
                   and not (x.get("ombre") or {}).get("retro")]
    # L'IA PASSE DEVANT. Si elle donne signe de vie, on lui laisse
    # DELAI_IA_MIN minutes sur chaque mail avant d'agir a sa place : c'est
    # elle qui lit, le script n'est que le filet. Passe ce delai — ou des
    # que le battement vieillit — le script reprend tout, immediatement.
    _age_ia = ia_presente()
    if _age_ia is not None and _age_ia <= PRESENCE_FRAICHE_MIN:
        _tot = len(attente)
        attente = [x for x in attente
                   if x.get('ia_vue') or _minutes_depuis(x.get('recu')) >= DELAI_IA_MIN]
        if len(attente) < _tot:
            dire('[mail-dit] %d proposition(s) laissee(s) a l IA '
                 '(battement il y a %.0f min)' % (_tot - len(attente), _age_ia))

    faits = 0
    creees = 0
    for p in attente:
        o = p.get("ombre") or {}
        # Une decision d'ombre est posee UNE FOIS et jamais reecrite : c'est ce
        # qui rend la mesure honnete (elle date d'avant celle de l'humain).
        # Mais l'EXECUTION, elle, doit obeir aux regles d'aujourd'hui. Sans ce
        # rejugement, chaque correction apportee au moteur ne valait que pour
        # les mails a venir, et un mail deja refuse restait refuse pour
        # toujours : le 2026-09-21, « GROS SOUCIS MAILS TRES URGENT » est reste
        # en boite alors que la regle qui le debloquait etait deployee depuis
        # dix minutes. Le critere est « le mail est ENCORE en boite » : c'est
        # la definition meme d'un mail bloque. Le critere precedent — « deja
        # tentee » — laissait de cote les propositions dont l'ombre disait
        # « attendre » : n'etant jamais tentees, elles n'etaient jamais
        # rejugees, et restaient en boite pour toujours (le mail « test » de
        # PARC ESPACE, 2026-09-21).
        if (dans_la_boite is None
                or p.get("message_id") in dans_la_boite):
            try:
                o2 = decider_seul(p)
                if o2.get("action") and o2.get("action") != o.get("action"):
                    dire("[mail-dit] %s : les regles ont change, %s -> %s"
                         % (p.get("id"), o.get("action"), o2.get("action")))
                o = o2 or o
            except Exception as e:
                dire("[mail-dit] %s : rejugement impossible (%s)"
                     % (p.get("id"), str(e)[:100]))
        action = o.get("action")
        if not AUTO_ACTIONS.get(action):
            continue
        ligne = {"quand": datetime.datetime.now().isoformat(timespec="seconds"),
                 "message_id": p.get("message_id"),
                 "proposition": p.get("id"), "action": action,
                 "cible": o.get("cible"), "pourquoi": o.get("pourquoi"),
                 "objet": (p.get("objet") or "")[:120],
                 "client": p.get("client"), "de": p.get("de")}
        try:
            if action == "creer":
                if creees >= CREATIONS_PAR_PASSAGE:
                    dire("[mail-dit] auto : plafond de %d creations par passage "
                         "atteint, le reste attend le cycle suivant"
                         % CREATIONS_PAR_PASSAGE)
                    break
                # Ce mail a-t-il DEJA donne un ticket ? Le registre le sait, et
                # il fait foi. Sans ce controle, la proposition repassait a
                # chaque cycle, le garde-fou de cadence la refusait, et le mail
                # restait en boite indefiniment — un ticket existait pourtant.
                # Constate le 2026-09-22 sur P-1035 / DIT 100174874 : le mail
                # a tourne une heure en boite apres la creation de son ticket.
                # Un refus doit toujours ranger le mail quelque part.
                deja = next((l for l in registre()
                             if l.get("message_id")
                             and l["message_id"] == p.get("message_id")
                             and l.get("dit") and not l.get("annulee")), None)
                if deja:
                    import mail_dit_ecriture as _E2
                    if not p.get("dit_numero"):
                        statuer(p["id"], "valide", "outil",
                                "DIT %s deja creee pour ce mail" % deja["dit"],
                                dit_numero=deja["dit"])
                    mv = _E2.deplacer_mail(par_id(p["id"]), True)
                    ligne.update({"execute": bool(mv.get("envoye")),
                                  "dit": deja["dit"], "cible": deja["dit"],
                                  "resultat": "DIT %s deja creee ; mail range"
                                              % deja["dit"],
                                  "raison": "deja ticketise"})
                    _journal_auto(ligne)
                    dire("[mail-dit] auto : %s deja ticketise (DIT %s), mail range"
                         % (p["id"], deja["dit"]))
                    _marquer_auto(p["id"], "deja ticketise")
                    continue
                empeche = _creation_sans_doute(p)
                if empeche:
                    _marquer_auto(p["id"], "creation ecartee : " + empeche)
                    ligne.update({"execute": False, "raison": empeche})
                    _journal_auto(ligne)
                    dire("[mail-dit] auto : %s pas de creation — %s"
                         % (p["id"], empeche))
                    continue
                res = E.traiter(p["id"], confirmer=True, deplacer=True)
                dit = (res.get("dit") or {})
                ligne.update({"execute": bool(dit.get("envoye")),
                              "dit": dit.get("numero"),
                              # La colonne « cible » du journal doit dire CE
                              # QUI A ETE FAIT : pour une création, c'est le
                              # numéro du ticket, pas une case vide.
                              "cible": dit.get("numero"),
                              "resultat": ("DIT %s créée" % dit.get("numero"))
                                          if dit.get("envoye")
                                          else (dit.get("erreur") or "")[:120],
                              "raison": (dit.get("erreur") or "")[:200],
                              "bloquants": dit.get("bloquants")})
                if dit.get("envoye"):
                    creees += 1
            elif action == "ranger":
                res = E.ranger_mail(p, o.get("cible"), confirmer=True)
                ligne.update({"execute": bool(res.get("envoye")),
                              "resultat": res.get("destination")
                                          or (res.get("erreur") or "")[:120],
                              "raison": (res.get("erreur") or "")[:200]})
                if res.get("envoye"):
                    statuer(p["id"], "ecarte", "outil (auto)",
                            "alerte rangee dans %s" % res.get("destination"))
            elif action == "completer":
                # Cette branche N'EXISTAIT PAS : `AUTO_ACTIONS["completer"]`
                # était à True depuis le 15/09, et l'action tombait dans le
                # `else: continue` en silence. Tous les mails décidés
                # « compléter » restaient en boîte sans que rien ne le dise.
                res = E.completer_dit(p["id"], o.get("cible"), confirmer=True,
                                      deplacer=not o.get("puis_transferer"))
                com_ok = bool((res.get("commentaire") or {}).get("ok"))
                pj = res.get("pieces_jointes") or {}
                ligne.update({"execute": com_ok or bool(pj.get("envoye")),
                              "dit": o.get("cible"),
                              "resultat": ("texte déjà présent dans le DIT %s ; "
                                           "mail rangé" % o.get("cible")
                                           if res.get("deja") else
                                           "ajouté au DIT %s%s"
                                           % (o.get("cible"),
                                              "" if com_ok
                                              else " (pièces seules : le "
                                                   "commentaire est verrouillé)")),
                              "raison": ((res.get("commentaire") or {})
                                         .get("erreur") or "")[:200]})
                # Puis le mail part chez celui qui suit le ticket.
                if ligne["execute"] and o.get("puis_transferer"):
                    iv = E.dernier_intervenant(o.get("cible"))
                    if iv.get("mail") and est_interne(iv["mail"]):
                        t = E.transferer_mail(
                            p["id"], iv["mail"],
                            "Bonjour,\n\nLe client a envoyé des photos, je les "
                            "ai ajoutées au ticket %s dont tu es le dernier "
                            "intervenant.\n" % o.get("cible"),
                            confirmer=True, deplacer=True)
                        ligne["resultat"] += " ; transmis à %s" % iv["mail"]
                        if not t.get("envoye"):
                            ligne["resultat"] += " (transfert : %s)" % (
                                t.get("erreur") or "échec")
            elif action == "ecarter":
                statuer(p["id"], "ecarte", "outil (auto)", o.get("pourquoi") or "")
                ligne["execute"] = True
            elif action == "transferer":
                res = E.transferer_mail(p["id"], o.get("cible"), confirmer=True,
                                        deplacer=not o.get("apres"))
                # `apres` : le dossier où le mail doit finir quand ce n'est pas
                # « 01 - DIT ARTIS ». Une alerte SOC transmise à MCO se range
                # en informations, pas avec les tickets.
                if res.get("envoye") and o.get("apres"):
                    res["mail"] = E.ranger_mail(par_id(p["id"]), o["apres"],
                                                confirmer=True)
                ligne.update({"execute": bool(res.get("envoye")),
                              "resultat": ("transféré à %s" % res.get("a"))
                                          if res.get("envoye")
                                          else (res.get("erreur")
                                                or res.get("remarque") or "")[:120],
                              "raison": (res.get("erreur")
                                         or res.get("remarque") or "")[:200]})
            else:
                continue
        except Exception as e:
            ligne.update({"execute": False, "raison": "exception : %s" % str(e)[:200]})
        # Le mail n'existe plus : quelqu'un l'a classé à la main. La
        # proposition n'a plus d'objet, et sans cette sortie elle réessayait à
        # chaque passage — quatre lignes « ErrorItemNotFound » polluaient tous
        # les cycles du 18/09 sans jamais rien changer.
        if "ErrorItemNotFound" in (ligne.get("raison") or ""):
            statuer(p["id"], "ecarte", "outil (auto)",
                    "le mail n'est plus dans la boîte : traité à la main")
            ligne["raison"] = "mail déjà sorti de la boîte, proposition close"
        _marquer_auto(p["id"], "%s -> %s" % (action, "ok" if ligne.get("execute")
                                             else ligne.get("raison", "echec")))
        _journal_auto(ligne)
        faits += 1
        dire("[mail-dit] auto : %s %s %s %s" % (p["id"], action,
                                                o.get("cible") or "",
                                                "OK" if ligne.get("execute")
                                                else "ECHEC " + str(ligne.get("raison"))[:80]))
    return faits


# Version des RÈGLES de traitement (rangements automatiques, symptômes,
# détection client…). À incrémenter dès qu'une règle change : les propositions
# déjà refusées repassent alors une fois devant les nouvelles règles.
#
# Sans ce compteur, `auto_tente` était définitif : Elliot, 2026-09-17 — « quand
# je te demande, tu classes, pourquoi cette fois ça s'est bloqué ? ». Une
# dizaine de règles déployées ce jour-là n'auraient jamais revu les mails
# refusés la veille.
REGLES_VERSION = 5          # 21/09 : completer range le mail meme sans rien a ajouter


def _marquer_auto(pid, quoi):
    """Une proposition n'est tentée qu'UNE FOIS **par version de règles**.

    C'est le verrou anti-boucle : quel que soit le résultat, on ne repasse pas
    dessus au cycle suivant. Mais si les règles changent, la proposition
    mérite un second regard — une seule fois de plus, sous la nouvelle version.
    """
    with _lock:
        d = _charger()
        for p in d["props"]:
            if p.get("id") == pid:
                p["auto_tente"] = datetime.datetime.now().isoformat(
                    timespec="seconds")
                p["auto_version"] = REGLES_VERSION
                p["auto_resultat"] = quoi
        _sauver(d)


def _poser_ombres(ids=None, retro=False):
    """Écrit la décision d'ombre des propositions qui viennent de naître.

    `retro=True` : rattrapage sur les propositions déjà en file au moment où le
    mode ombre a été activé. Elles sont marquées comme telles et comptées à
    part — une opinion écrite après coup n'a pas la même valeur qu'une opinion
    écrite avant de voir la décision humaine, et le tableau ne doit pas les
    mélanger.

    Les appels réseau (Artis, pour chercher un DIT ressemblant) sont faits
    HORS du verrou : le tenir pendant une requête bloquerait l'écran.
    """
    with _lock:
        d = _charger()
        def a_refaire(p):
            o = p.get("ombre")
            if not o:
                return True
            # Moteur changé et proposition pas encore tranchée : on refait.
            return (o.get("v") != OMBRE_VERSION and p.get("etat") == "nouveau")

        vises = [dict(p) for p in d["props"]
                 if a_refaire(p)
                 and (ids is None or p.get("id") in set(ids))
                 and (ids is not None or p.get("etat") == "nouveau")]
    if not vises:
        return 0
    decisions = {}
    for p in vises:
        try:
            o = decider_seul(p)
        except Exception as e:
            o = {"action": "attendre", "cible": None, "confiance": "nulle",
                 "pourquoi": "erreur : %s" % str(e)[:100]}
        o["quand"] = datetime.datetime.now().isoformat(timespec="seconds")
        o["v"] = OMBRE_VERSION
        # `retro` ne veut pas dire « avis refait », mais « avis dont la valeur
        # de preuve est douteuse » : la proposition avait DEJA ete ouverte par
        # un agent quand l'avis a ete ecrit, donc l'outil a pu profiter d'une
        # consigne recue entre-temps. Un avis refait sur une proposition que
        # personne n'a encore regardee, lui, precede bien la decision humaine :
        # il compte.
        if p.get("confirme_humain"):
            o["retro"] = True
        decisions[p["id"]] = o
    with _lock:
        d = _charger()
        for p in d["props"]:
            if p.get("id") not in decisions:
                continue
            # Jamais réécrire l'avis d'une proposition déjà tranchée.
            if p.get("ombre") and p.get("etat") != "nouveau":
                continue
            p["ombre"] = decisions[p["id"]]
        _sauver(d)
    return len(decisions)


def _action_reelle(p, metier_par_prop):
    """Ce qu'un humain a FAIT de cette proposition, dans le vocabulaire du
    mode ombre."""
    note = (p.get("note") or "").lower()
    if "transf" in note:
        return "transferer", None
    if p.get("dit_numero"):
        if any(x in note for x in ("ajouté au dit", "ajoute au dit", "déjà cré",
                                   "deja cre", "rattach", "regroupée", "regroupee",
                                   "jointes à la dit", "même demande", "meme demande")):
            return "completer", p.get("dit_numero")
        return "creer", p.get("dit_numero")
    m = metier_par_prop.get(p.get("id"))
    if m:
        return "ecarter", m
    if p.get("range_par_outil"):
        return "ranger", p.get("range_par_outil")
    return "ecarter", None


def bilan_ombre():
    """Accord entre ce que l'outil aurait fait et ce qu'un humain a fait.

    Lu famille par famille : on n'allume l'automatisation que là où l'accord
    est prouvé, pas en bloc.
    """
    import collections
    with _lock:
        d = _charger()
    hp = _hp_charger()
    metier_par_prop = {x.get("proposition"): x.get("metier")
                       for x in (hp.get("journal") or [])}
    par_action = collections.defaultdict(
        lambda: {"n": 0, "accord": 0, "desaccords": []})
    abstentions = 0
    suivies = en_attente = sans_ombre = retros = 0
    for p in d["props"]:
        o = p.get("ombre")
        if o:
            suivies += 1
            if o.get("retro"):
                retros += 1
            if p.get("etat") == "nouveau":
                en_attente += 1
        elif p.get("etat") == "nouveau":
            sans_ombre += 1
        # Une opinion écrite APRÈS coup ne prouve rien : on ne la compte pas.
        if not o or o.get("retro") or p.get("etat") not in ("valide", "ecarte"):
            continue
        if o["action"] == "attendre":
            abstentions += 1
            continue
        reel, cible = _action_reelle(p, metier_par_prop)
        s = par_action[o["action"]]
        s["n"] += 1
        # Pour « ranger », la famille compte aussi : se tromper de dossier,
        # c'est se tromper.
        bon = (reel == o["action"]) and (
            o["action"] != "ranger" or str(cible or "").endswith(str(o.get("cible") or "")))
        if bon:
            s["accord"] += 1
        elif len(s["desaccords"]) < 12:
            s["desaccords"].append({
                "id": p.get("id"), "objet": (p.get("objet") or "")[:70],
                "propose": "%s %s" % (o["action"], o.get("cible") or ""),
                "fait": "%s %s" % (reel, cible or ""),
                "pourquoi": o.get("pourquoi"), "note": (p.get("note") or "")[:120]})
    return {
        "abstentions": abstentions,
        "suivies": suivies,
        "en_attente": en_attente,
        "sans_ombre": sans_ombre,
        "retrospectives": retros,
        "par_action": {k: {"n": v["n"], "accord": v["accord"],
                           "taux": (round(100.0 * v["accord"] / v["n"])
                                    if v["n"] else None),
                           "desaccords": v["desaccords"]}
                       for k, v in par_action.items()},
    }


def corpus_decisions(limite=5000):
    """Le corpus d'apprentissage : un mail, ce que l'outil proposait, ce qu'un
    humain a décidé.

    Objectif d'Elliot (2026-09-14) : « au final TU devras tout faire
    automatiquement, là je le fais manuellement pour que tu apprennes à juger
    seul ». Pour juger seul il faut pouvoir SE MESURER, et pour se mesurer il
    faut une vérité terrain. C'est ce que rend cette fonction : chaque
    proposition tranchée, avec ses caractéristiques d'entrée et la décision.

    Toute règle candidate se teste contre ce corpus avant d'être posée — c'est
    la seule façon de savoir si elle gagne ou si elle perd des demandes.
    """
    with _lock:
        d = _charger()
    hp = _hp_charger()
    metier_par_prop = {x.get("proposition"): x.get("metier")
                       for x in (hp.get("journal") or [])}
    out = []
    for p in d["props"]:
        if p.get("etat") not in ("valide", "ecarte"):
            continue
        ref = p.get("propose") or {}
        out.append({
            "id": p.get("id"),
            "recu": p.get("recu"),
            "de": p.get("de"),
            "domaine_expediteur": (p.get("de") or "").split("@")[-1],
            "objet": p.get("objet"),
            "corps": (p.get("commentaire") or "")[:4000],
            "pieces": [x.get("nom") for x in (p.get("pieces") or [])],
            "fil_messages": p.get("fil_messages"),
            # ce que l'outil avait deduit
            "tier": p.get("tier"), "motif": p.get("motif"),
            "categorie": p.get("categorie"),
            "client_propose": ref.get("client", p.get("client")),
            "methode_client": p.get("methode_client"),
            "symptome_propose": ref.get("symptome"),
            "domaine_client_hist": p.get("domaine_client_hist"),
            # ce qu'un humain a retenu
            "decision": p.get("etat"),
            "metier_hors_perimetre": metier_par_prop.get(p.get("id")),
            "client_retenu": p.get("client"),
            "symptome_retenu": p.get("symptome"),
            "dit_numero": p.get("dit_numero"),
            "note": p.get("note"),
            "traite_par": p.get("traite_par"),
            "traite_le": p.get("traite_le"),
            "champs_corriges": sorted({j.get("champ")
                                       for j in (p.get("journal") or [])
                                       if j.get("champ")}),
        })
    out.sort(key=lambda x: x.get("traite_le") or "")
    return out[-limite:]


def similarite(a, b):
    """Ressemblance 0..1 entre deux textes, normalises comme dans l'outil de
    mesure. Sert a dire « ce DIT reprend le mail » sans que l'agent relise tout."""
    import difflib
    from mail_regles import norm
    x, y = norm(a)[:600], norm(b)[:600]
    if not x or not y:
        return 0.0
    sm = difflib.SequenceMatcher(None, x, y)
    if sm.quick_ratio() < 0.3:
        return round(sm.quick_ratio(), 3)
    return round(sm.ratio(), 3)


def par_id(pid):
    with _lock:
        d = _charger()
    for p in d["props"]:
        if p.get("id") == pid:
            return p
    return None


def statuer_lot(pids, etat, user="", note=""):
    """Validation/rejet groupe : une seule ecriture du fichier pour tout le lot,
    au lieu d'une par proposition."""
    if etat not in ("nouveau", "valide", "ecarte"):
        return 0
    quand = datetime.datetime.now().isoformat(timespec="seconds")
    vises = set(pids or [])
    n = 0
    with _lock:
        d = _charger()
        for p in d["props"]:
            if p.get("id") in vises:
                p["etat"] = etat
                p["traite_par"] = user
                p["traite_le"] = quand
                if note:
                    p["note"] = note
                n += 1
        if n:
            _sauver(d)
    return n


def interlocuteurs_index(code):
    """Interlocuteurs connus d'un client, depuis l'index local (sans appel
    reseau). Sert de secours si Artis ne repond pas."""
    idx = _index_clients()
    out = []
    for email, rows in (idx.get("emails") or {}).items():
        for r in rows:
            if r.get("client") == code:
                out.append({"id": r.get("id"), "nom": r.get("nom"), "mail": email})
    for pid, p in (idx.get("personnes") or {}).items():
        if p.get("client") == code and not any(str(o["id"]) == str(pid) for o in out):
            out.append({"id": int(pid) if str(pid).isdigit() else pid,
                        "nom": p.get("nom"), "mail": ""})
    vus, uniq = set(), []
    for o in out:
        if o["id"] in vus:
            continue
        vus.add(o["id"])
        uniq.append(o)
    return sorted(uniq, key=lambda o: (o.get("nom") or "").upper())


# ═════════════════════════════════════════════════════════ registre des DIT
def registre():
    """[{dit, intervention, client, client_lib, sujet, cree_le, proposition,
    symptome, uo_voulue, variante, note}] — le plus recent d'abord."""
    try:
        if REGISTRE_FILE.exists():
            d = json.loads(REGISTRE_FILE.read_text(encoding="utf-8"))
            return sorted(d.get("dits", []),
                          key=lambda x: x.get("cree_le") or "", reverse=True)
    except Exception:
        pass
    return []


def registre_ajouter(ligne):
    """Enregistre une DIT creee. Appele par mail_dit_ecriture apres succes."""
    with _lock:
        try:
            d = (json.loads(REGISTRE_FILE.read_text(encoding="utf-8"))
                 if REGISTRE_FILE.exists() else {"dits": []})
        except Exception:
            d = {"dits": []}
        d.setdefault("dits", [])
        # une DIT n'est enregistree qu'une fois
        if not any(x.get("dit") == ligne.get("dit") for x in d["dits"]):
            ligne.setdefault("cree_le",
                             datetime.datetime.now().isoformat(timespec="seconds"))
            d["dits"].append(ligne)
            tmp = _tmp_a_moi(REGISTRE_FILE)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, REGISTRE_FILE)
    return ligne


def registre_annoter(dit, note, avis=None, qui=""):
    """Note libre d'une ligne du registre, et AVIS de l'agent sur ce que
    l'outil a fait.

    Elliot (2026-09-15) : « je veux pouvoir ajouter un commentaire à chaque DIT
    que tu as faite automatiquement pour te donner mon avis, que tu
    ENREGISTRERAS pour mieux faire les prochaines ». L'avis est donc horodaté
    et conservé en liste : c'est de la matière d'apprentissage, on n'écrase pas
    l'historique d'un jugement par le suivant.
    """
    with _lock:
        try:
            d = json.loads(REGISTRE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
        for x in d.get("dits", []):
            if x.get("dit") == dit:
                if note or avis is None:
                    x["note"] = note
                if avis:
                    x.setdefault("avis", []).append({
                        "quand": datetime.datetime.now().isoformat(
                            timespec="seconds"),
                        "qui": qui or "agent", "texte": avis})
                tmp = _tmp_a_moi(REGISTRE_FILE)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=1)
                os.replace(tmp, REGISTRE_FILE)
                return x
    return None


def essais():
    """Journal des hypotheses testees sur typeGpTravail : ce qui a ete essaye,
    quand, et avec quel resultat. Evite de refaire deux fois le meme essai."""
    try:
        if ESSAIS_FILE.exists():
            d = json.loads(ESSAIS_FILE.read_text(encoding="utf-8"))
            return d.get("essais", [])
    except Exception:
        pass
    return []


def essai_ajouter(hypothese, methode, resultat, dit="", concluant=False):
    with _lock:
        try:
            d = (json.loads(ESSAIS_FILE.read_text(encoding="utf-8"))
                 if ESSAIS_FILE.exists() else {"essais": []})
        except Exception:
            d = {"essais": []}
        d.setdefault("essais", [])
        d["essais"].append({
            "n": len(d["essais"]) + 1,
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "hypothese": hypothese, "methode": methode,
            "resultat": resultat, "dit": dit, "concluant": bool(concluant)})
        tmp = _tmp_a_moi(ESSAIS_FILE)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ESSAIS_FILE)
    return True


# ═════════════════════════════════════════════════════════ lecture Graph
def _get(tok, url, params=None):
    """GET uniquement. Toute autre methode est interdite sur cette boite."""
    r = requests.get(url, params=params, timeout=60, headers={
        "Authorization": "Bearer " + tok,
        "Prefer": 'outlook.body-content-type="text"'})
    if r.status_code == 429:
        time.sleep(int(r.headers.get("Retry-After", 5)))
        return _get(tok, url, params)
    return r


def _id_boite(tok):
    """Identifiant de la boite de reception. Une requete, pas d'arborescence a
    parcourir : c'est le seul dossier dont le cycle a besoin."""
    r = _get(tok, GRAPH + "/me/mailFolders/inbox",
             {"$select": "id,displayName,totalItemCount"})
    if r.status_code != 200:
        raise RuntimeError("boite de reception inaccessible : HTTP %s" % r.status_code)
    j = r.json()
    return j["id"], j.get("totalItemCount")


def _boite(tok, fid):
    """Contenu COMPLET de la boite de reception. Elle est petite (quelques
    dizaines de mails) parce que les agents la vident en continu : on la lit
    entierement a chaque cycle, ce qui est plus simple et plus sur qu'un delta,
    et permet de voir ce qui en est SORTI."""
    out = []
    url = "%s/me/mailFolders/%s/messages?$select=%s&$top=100" % (GRAPH, fid, SELECT)
    while url:
        r = _get(tok, url)
        if r.status_code != 200:
            raise RuntimeError("boite HTTP %s : %s" % (r.status_code, r.text[:200]))
        j = r.json()
        for m in j.get("value", []):
            m["_folder"] = DOSSIER_ENTREE
            out.append(m)
        url = j.get("@odata.nextLink")
    return out


# ═════════════════════════════════════════════════════════ proposition
def ecrire_numeros(numeros):
    """Memorise les numeros de DIT existants (appele par le cycle quotidien
    d'updater.py, qui possede deja l'auth Artis). Sans ce fichier, le signal
    « numero de DIT cite en clair dans l'objet » reste inactif -- il valait
    68 doublons evites sur 14 jours, donc il compte."""
    try:
        tmp = _tmp_a_moi(NUMEROS_FILE)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(set(str(n) for n in numeros if n)), f)
        os.replace(tmp, NUMEROS_FILE)
        return True
    except Exception:
        return False


def _num_dits():
    """Les numeros de DIT connus, fichier quotidien + ceux crees depuis.

    Le fichier n'est rafraichi qu'au cycle de 20 h. Une DIT creee le matin y
    manquait donc toute la journee, et la reponse du client sur ce ticket
    n'etait pas reconnue comme un suivi : elle restait en boite (constate le
    2026-09-17 avec « RE: [IT 10017436801] », ticket cree une heure plus tot).
    """
    out = set()
    try:
        if NUMEROS_FILE.exists():
            out = set(json.loads(NUMEROS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    try:
        for x in registre():
            if x.get("dit"):
                out.add(str(x["dit"]))
    except Exception:
        pass
    return out


def _libelles_clients():
    """{code: nom}. Deux sources fusionnees : l'index client (construit depuis
    le corpus de DIT) et cotation_refs.json que le dashboard maintient deja.
    L'une ou l'autre peut manquer selon l'environnement."""
    out = {}
    try:
        if COTATION_REFS.exists():
            d = json.loads(COTATION_REFS.read_text(encoding="utf-8"))
            for c in (d.get("clients") or []):
                if c.get("code"):
                    out[c["code"]] = c.get("nom", "")
    except Exception:
        pass
    try:
        out.update({k: v for k, v in (_index_clients().get("libelles") or {}).items()
                    if v and not out.get(k)})
    except Exception:
        pass
    return out


def _charger_json(f, defaut):
    try:
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return defaut


def _attrs_clients():
    """{codeClient: {nature, type_dit, orgInt, site, bien, uo, gp, fact,
    domaine, priorite}} — la valeur majoritaire dans l'historique du client.
    Dominance mesuree : 90 a 99 % selon le champ, donc bien plus fiable qu'une
    deduction depuis le texte du mail."""
    return _charger_json(CLIENT_ATTRS, {})


def _symptomes():
    """{par_client: {code: [{code,n}]}, global: [...], libelles: {code: nom}}"""
    return _charger_json(CLIENT_SYMPT, {"par_client": {}, "global": [],
                                        "libelles": {}})


_CACHE_SYMPT_OFF = {"liste": None}


def symptomes_officiels(domaine="INFO"):
    """La liste Artis des symptomes du domaine, avec leurs libelles complets.

    Source : `/parametrage/scau/{domaine}/symptomes` (80 entrees pour INFO).
    Bien meilleure que celle deduite de l'historique, qui melange les domaines
    et n'a que les libelles vus passer.
    """
    if _CACHE_SYMPT_OFF["liste"] is not None:
        return _CACHE_SYMPT_OFF["liste"]
    out = []
    try:
        import requests as _rq
        import mail_dit_ecriture as _E          # import tardif : evite le cycle
        base, tok = _E._artis()
        r = _rq.get("%s/parametrage/scau/%s/symptomes" % (base, domaine),
                    params={"token": tok}, headers={"accept": "application/json"},
                    timeout=60)
        if r.status_code == 200:
            out = [{"code": x.get("code"), "libelle": x.get("libelle")}
                   for x in (r.json() or []) if x.get("code")]
    except Exception:
        out = []
    _CACHE_SYMPT_OFF["liste"] = out
    return out


def suggestions_symptome(code_client, sym, n=5):
    """Les n symptomes les plus probables pour ce client, du plus au moins
    frequent, completes par le top global. Mesure : le bon symptome est dans
    les 5 premiers dans 71 % des cas. On ne devine pas : l'agent choisit."""
    lib = sym.get("libelles") or {}
    out, vus = [], set()
    for x in (sym.get("par_client") or {}).get(code_client or "", []):
        if x["code"] not in vus:
            vus.add(x["code"])
            out.append({"code": x["code"], "libelle": lib.get(x["code"], ""),
                        "n": x.get("n"), "source": "historique du client"})
    for x in (sym.get("global") or []):
        if len(out) >= n:
            break
        if x["code"] not in vus:
            vus.add(x["code"])
            out.append({"code": x["code"], "libelle": lib.get(x["code"], ""),
                        "n": x.get("n"), "source": "fréquent chez tous"})
    return out[:n]


def _date_locale(iso_utc):
    """Graph rend l'heure en UTC, Artis attend l'heure locale : sans conversion
    le DIT afficherait deux heures de moins que le mail."""
    if not iso_utc:
        return None
    try:
        t = datetime.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return t.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


_CACHE_CSI = {"codes": None}


def clients_informatiques():
    """Les codes clients ayant un « Contrat Services Informatique ».

    Source : `artis_full_mapping` de msp_data.json, que le cycle de MAJ
    construit chaque nuit a partir des seuls contrats informatiques. On ne
    refait donc pas l'appel lourd a /contrat.

    Vide si le fichier manque : dans ce cas on ne filtre rien plutot que de
    tout rejeter.
    """
    if _CACHE_CSI["codes"] is not None:
        return _CACHE_CSI["codes"]
    codes = set()
    for nom in ("msp_data.json",):
        f = DATA_DIR / nom
        if f.exists():
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                codes = set((d.get("artis_full_mapping") or {}).keys())
            except Exception:
                codes = set()
    _CACHE_CSI["codes"] = codes
    return codes


_CACHE_NOMS = {"liste": None}


def _normal_nom(txt):
    """Un nom reduit a ses lettres, chiffres et espaces simples, en majuscules.
    Sert a comparer « MAB FA » a « Client Jdiffusion - MAB FA »."""
    import unicodedata
    t = unicodedata.normalize("NFD", txt or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = "".join(c if c.isalnum() else " " for c in t.upper())
    return " ".join(t.split())


def client_par_nom(texte, libelles):
    """Le client dont le NOM est ecrit dans ce texte, ou (None, "").

    Le nom du client est souvent la seule identification disponible : un
    collegue ecrit « Client Jdiffusion - MAB FA », sans code ni adresse. On ne
    cherchait alors rien, et le mail restait sans client donc sans issue
    (2026-09-23, demande urgente d'acces pour un technicien).

    Trois garde-fous, parce qu'un nom court se retrouve partout :
      - au moins 6 caracteres significatifs, ou deux mots ;
      - le nom doit apparaitre ENTIER, borne par autre chose que des lettres ;
      - un seul client doit correspondre, sinon on ne tranche pas.
    Le plus long nom l'emporte : « MAB FA » plutot que « MAB ».
    """
    t = " " + _normal_nom(texte) + " "
    if len(t) < 8:
        return None, ""
    liste = _CACHE_NOMS["liste"]
    if liste is None:
        liste = []
        for code, nom in (libelles or {}).items():
            n = _normal_nom(nom)
            if len(n.replace(" ", "")) < 6 and len(n.split()) < 2:
                continue
            if len(n) < 5:
                continue
            liste.append((code, n))
        _CACHE_NOMS["liste"] = liste
    trouves = [(code, n) for code, n in liste if (" " + n + " ") in t]
    if not trouves:
        return None, ""
    trouves.sort(key=lambda x: -len(x[1]))
    meilleur = trouves[0]
    # Ex aequo de meme longueur : deux clients portent le meme nom, on
    # s'abstient plutot que de tirer au sort.
    if sum(1 for _c, n in trouves if len(n) == len(meilleur[1])) > 1:
        return None, ""
    return meilleur[0], "nom du client ecrit dans le mail (%s)" % meilleur[1]


def _index_clients():
    for nom in ("client_index_all.json", "client_index_email.json"):
        f = DATA_DIR / "mailcorpus" / nom
        if f.exists():
            try:
                idx = json.loads(f.read_text(encoding="utf-8"))
                # Les comptes ayant un contrat informatique : c'est ce qui
                # departage deux comptes d'un meme client (impression vs
                # informatique).
                idx["csi"] = clients_informatiques()
                # NE JAMAIS appeler `_libelles_clients()` ici : cette fonction
                # lit elle-meme `_index_clients()`, et l'ajout d'un appel dans
                # ce sens cree une RECURSION INFINIE. Le 2026-09-22 elle a fait
                # tomber le conteneur en boucle (exit 137, OOM, sept fois).
                # L'index porte deja sa propre cle `libelles` quand le corpus
                # l'a construite : elle suffit a departager deux clients qui
                # partagent un domaine, et son absence n'est pas grave.
                idx.setdefault("libelles", {})
                return idx
            except Exception:
                pass
    return {"emails": {}, "domaines": {}, "personnes": {}, "csi": set()}


def _libelle_client(libelles, code):
    return libelles.get(code, "")


# ═══════════════════════════════════════════════════════ fil de discussion
# Noms que MET OUTLOOK LUI-MEME sur les images d'une signature ou d'un corps
# HTML : image001.png, logo.jpg, sign.gif... Une photo prise au telephone garde
# son nom d'origine (IMG_3274.jpeg, Capture d'ecran..., img-<guid>).
RE_IMG_GENERIQUE = re.compile(
    r"^(image|ol|oledata|logo|logos|sign|signature|bandeau|banniere|banner"
    r"|icone?|pastille|facebook|linkedin|instagram|twitter|youtube)"
    r"[-_ ]?\d*\.(png|jpe?g|gif|bmp|emz|wmz|svg)$", re.I)

# Le mot est parfois au MILIEU du nom : « ACP_MargauxNinet-Signature.png »,
# joint par erreur au DIT 100174300 le 2026-09-16 parce que le motif ci-dessus
# est ancre en debut de nom. Aucun corpus ne permet de mesurer ce cas (les
# mails archives ne portent pas le detail des pieces jointes), mais une image
# dont le nom contient « signature », « logo » ou « bandeau » n'est pas la
# photo d'une panne.
RE_NOM_SIGNATURE = re.compile(
    r"(signature|logo|bandeau|banniere|banner|footer|pied[-_ ]?de[-_ ]?page)",
    re.I)

# Le texte annonce-t-il une piece ? Une photo utile se presente presque
# toujours (« ci-joint », « voici la capture », « photo ci-dessous ») ; une
# signature, jamais.
RE_ANNONCE_PIECE = re.compile(
    r"(ci[- ]?joint|ci[- ]?joints?|ci[- ]?dessous|en\s+pi[èe]ce[s]?\s+jointe"
    r"|pi[èe]ce[s]?\s+jointe|voici\s+(la|le|les|une|un)\b"
    r"|capture[s]?\s+d.[ée]cran|copie\s+d.[ée]cran|screenshot"
    r"|vous\s+(trouverez|joins|envoie)|je\s+vous\s+(joins|envoie|transmets)"
    r"|photo[s]?\b|image[s]?\s+(jointe|ci)|\bscan\b)", re.I)

# Un GIF est une animation de signature, jamais une photo de panne.
TYPES_PHOTO = ("image/jpeg", "image/jpg", "image/png", "image/heic",
               "image/heif", "image/webp")
TAILLE_PHOTO_MINI = 40 * 1024     # en dessous : vignette, pas une photo


SIGNATURES_FILE = DATA_DIR / "mail_dit_signatures.json"
SEUIL_SIGNATURE = 3          # vue dans 3 fils differents = signature
TAILLE_IMAGE_MINI = 10 * 1024


def _signatures():
    try:
        if SIGNATURES_FILE.exists():
            return json.loads(SIGNATURES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"tailles": {}}


def apprendre_signatures(observations):
    """Apprend quelles images sont des logos, en les voyant REVENIR.

    Une capture d'ecran est unique ; un logo de signature reapparait dans tous
    les fils du meme expediteur. On compte donc, par TAILLE exacte, le nombre
    de conversations distinctes ou l'image apparait. Mesure sur 160 mails :
    12 tailles reviennent dans 3 fils ou plus, dont une dans 11 — et ce sont
    bien des logos. Le filtre retire 91 images sur 237.

    `observations` : iterable de (taille, conversationId).
    """
    d = _signatures()
    t = d.setdefault("tailles", {})
    change = False
    for taille, conv in observations:
        if not taille or not conv:
            continue
        cle = str(taille)
        vus = t.setdefault(cle, [])
        if conv not in vus:
            vus.append(conv)
            del vus[20:]          # inutile d'en garder plus
            change = True
    if change:
        try:
            SIGNATURES_FILE.write_text(json.dumps(d, ensure_ascii=False),
                                       encoding="utf-8")
        except Exception:
            pass
    return d


def _est_signature_recurrente(taille):
    return len((_signatures().get("tailles") or {}).get(str(taille), [])) >= SEUIL_SIGNATURE


def _est_photo(a):
    """Une image incrustee qui porte de l'information, pas un logo.

    Mesure sur 250 mails (1 290 images incrustees) : le nom suffit a separer
    les deux familles. Outlook nomme `image001.png` ce qu'il incruste lui-meme
    (signatures, bandeaux) ; ce que l'expediteur a pris en photo garde son nom
    (`IMG_3274.jpeg`, `Capture d'ecran...`). Le seuil de taille elimine le
    reste des vignettes. 1 290 -> 27 retenues, aucune signature parmi elles.
    """
    t = (a.get("contentType") or "").lower().split(";")[0].strip()
    nom = (a.get("name") or "").strip()
    return (t in TYPES_PHOTO and (a.get("size") or 0) >= TAILLE_PHOTO_MINI
            and not RE_IMG_GENERIQUE.match(nom)
            and not RE_NOM_SIGNATURE.search(nom))


def corps_muet(corps):
    """Le corps ne porte aucune phrase : il n'y a que l'objet et des images.

    Meme test que celui qui decide du commentaire du DIT, pour que les deux
    decisions ne divergent pas.
    """
    try:
        import mail_dit_ecriture as _E      # import tardif : evite le cycle
        lignes = _E._corps_utile(corps)
        return not lignes or not any(_E._ressemble_a_une_phrase(l)
                                     for l in lignes)
    except Exception:
        return not (corps or "").strip()


def pieces_du_fil(tok, fil, taille_max=20 * 1024 * 1024):
    """Les pièces de TOUT le fil, pas seulement du dernier message.

    Elliot (2026-09-14, P-0225) : « dans l'historique de discussion il y a une
    image jointe dans le corps du mail comme l'indique le message du client,
    mais elle n'est pas mise en PJ ». La capture était bien là — dans le
    message du 10/09, pas dans la réponse du 14/09 que nous lisions. Le
    document Word du premier message du fil manquait pour la même raison.

    Dédoublonnage sur (nom, taille) : une image citée est ré-incrustée à
    l'identique dans chaque réponse.
    """
    out, vus = [], set()
    for m in (fil or []):
        corps = sans_html((m.get("body") or {}).get("content")
                          or m.get("bodyPreview") or "")
        try:
            p = pieces_du_mail(tok, m.get("id"), taille_max,
                               sans_texte=corps_muet(body_utile(corps)),
                               noms_corps=images_du_corps(corps),
                               conversation=m.get("conversationId"))
        except Exception:
            p = []
        for x in p:
            cle = (x.get("nom"), x.get("taille"))
            if cle in vus:
                continue
            vus.add(cle)
            x = dict(x)
            x["mail_id"] = m.get("id")
            x["du_fil"] = (m.get("receivedDateTime") or "")[:16].replace("T", " ")
            out.append(x)
    return out


def _corps_html(tok, mail_id):
    """Le corps en HTML, quel que soit le format natif du message.

    Sans l'en-tete `Prefer`, Graph rend parfois la version TEXTE — et la
    conversion perd des images : sur le mail de la DIT 100173882, le marqueur
    de la capture d'ecran n'apparaissait pas du tout dans le texte.
    """
    try:
        r = requests.get("%s/me/messages/%s" % (GRAPH, mail_id),
                         headers={"Authorization": "Bearer " + tok,
                                  "Prefer": 'outlook.body-content-type="html"'},
                         params={"$select": "body"}, timeout=40)
        if r.status_code == 200:
            return ((r.json() or {}).get("body") or {}).get("content") or ""
    except Exception:
        pass
    return ""


def pieces_du_mail(tok, mail_id, taille_max=20 * 1024 * 1024, sans_texte=False,
                   noms_corps=None, conversation=None):
    """Les pieces a joindre au DIT : ce que l'expediteur a attache, plus les
    PHOTOS incrustees dans le corps.

    `isInline` distingue ce que l'expediteur a attache de ce qui est incruste
    dans le corps. Longtemps on jetait tout l'incruste, pour ne pas remplir la
    GED d'Artis de logos de signature. Elliot (2026-09-14, P-0182/P-0183) :
    « la cliente a mis des photos pour expliquer son probleme, il faut donc
    creer la DIT et ajouter les photos en PJ ». Ces photos etaient incrustees,
    et le mail n'etait meme pas marque `hasAttachments`.

    On garde donc les images incrustees qui sont des photos (cf. `_est_photo`).
    `sans_texte=True` — le corps ne dit rien — leve le filtre sur le nom : quand
    l'image EST le message, meme une capture nommee `image001.png` compte.
    """
    if not mail_id:
        return []
    # `contentId` n'existe que sur le type fileAttachment : sans le cast,
    # Graph repond 400. C'est pourtant la SEULE cle fiable pour relier une
    # piece a son <img src="cid:..."> — cinq captures d'ecran collees dans un
    # meme mail s'appellent toutes « image.png » (P-0214).
    r = _get(tok, "%s/me/messages/%s/attachments" % (GRAPH, mail_id),
             {"$select": "id,name,contentType,size,isInline,"
                         "microsoft.graph.fileAttachment/contentId"})
    if r.status_code != 200:
        return []
    pieces = r.json().get("value") or []
    # Le texte alternatif tranche ce que ni la taille ni le nom ne tranchent :
    # « Laugier - signature mail » se declare lui-meme.
    alts = {}
    _texte_annonce_piece = True
    # Une taille d'image qui revient PLUSIEURS FOIS dans le MEME message est un
    # logo de signature recopie par le fil cite : chaque reponse rembarque
    # celui de son auteur. Deux captures d'ecran distinctes ne pesent jamais le
    # meme nombre exact d'octets. Constate le 2026-09-21 sur HOLDING HBL : la
    # relance du serveur SAGE a colle douze images a la DIT — 50 407 o cinq
    # fois, 11 837 o quatre fois — alors que le mail ne portait AUCUNE piece
    # (`hasAttachments` etait faux).
    _repetees = set()
    _vues = {}
    for x in pieces:
        if x.get("isInline") and x.get("size"):
            _vues[x["size"]] = _vues.get(x["size"], 0) + 1
    _repetees = {t for t, n in _vues.items() if n > 1}
    if any(x.get("isInline") for x in pieces):
        html = _corps_html(tok, mail_id)
        alts = images_du_html(html)
        _texte_annonce_piece = bool(RE_ANNONCE_PIECE.search(sans_html(html or "")))
        if conversation:
            apprendre_signatures((x.get("size"), conversation) for x in pieces
                                 if x.get("isInline"))
    out = []
    for a in pieces:
        if (a.get("size") or 0) > taille_max:
            continue
        inline = bool(a.get("isInline"))
        if inline:
            t = (a.get("contentType") or "").lower().split(";")[0].strip()
            grosse = (a.get("size") or 0) >= TAILLE_PHOTO_MINI
            # Citée dans le corps AVANT la formule de politesse : c'est une
            # capture que le client commente, pas un logo de signature. Ni la
            # taille ni le nom ne le disaient — celle de P-0225 pèse 5,9 ko et
            # s'appelle image001.png.
            nom = (a.get("name") or "").lower()
            cid = (a.get("contentId") or "").strip().lower()
            citee = nom in (noms_corps or set()) or cid in (noms_corps or set())
            alt = alts.get(cid, alts.get(nom))
            # Image portant du CONTENU : declaree dans le HTML, sans `alt` de
            # signature, d'une taille credible, et dont la taille exacte ne
            # revient pas d'un fil a l'autre (ce qui trahit un logo).
            contenu = (alt is not None and image_de_contenu(nom, alt)
                       and t in TYPES_PHOTO
                       and (a.get("size") or 0) >= TAILLE_IMAGE_MINI
                       and not _est_signature_recurrente(a.get("size")))
            muet = sans_texte and t in TYPES_PHOTO and grosse
            if not (citee or contenu or _est_photo(a) or muet):
                continue
            # Dernier mot a la recurrence : une image dont la taille EXACTE
            # revient d'un fil a l'autre est un logo, meme citee dans le corps.
            # Seule exception : un mail sans texte, ou l'image EST le message.
            if not muet and _est_signature_recurrente(a.get("size")):
                continue
            # Le nom le dit lui-meme, ou qu'il soit dans la chaine.
            if RE_NOM_SIGNATURE.search(a.get("name") or ""):
                continue
            # Deja pesee plus haut : la meme taille exacte, plusieurs fois dans
            # ce message, ne peut etre qu'un logo recopie par le fil.
            if (a.get("size") or 0) in _repetees:
                continue
            # Dernier filtre, et le plus parlant : une image incrustee PETITE,
            # que le texte n'annonce pas, dans un mail qui a par ailleurs un
            # vrai message — c'est une signature. Elliot, 2026-09-17, sur le
            # DIT 100174112 : « il fallait mettre la PJ mais c'est la
            # signature que t'as mise ». Ce mail ne portait qu'une image de
            # 26 ko, citee par son cid mais annoncee nulle part, dans une
            # demande d'adresse mail pour un apprenti.
            # Les photos de P-0182/P-0183 etaient ANNONCEES (« ci-joint les
            # photos ») : la regle ne les touche pas.
            if not muet and not grosse and not _texte_annonce_piece:
                continue
        out.append({"id": a.get("id"), "nom": a.get("name"),
                    "type": a.get("contentType"), "taille": a.get("size"),
                    "incrustee": inline})
    return out


def contenu_piece(tok, mail_id, piece_id):
    """Le contenu binaire d'une piece jointe.

    Deux formes de pieces, et une seule porte `contentBytes` :

    * `fileAttachment` — un fichier, encode en base64 dans la reponse ;
    * `itemAttachment` — un MAIL transfere en piece jointe. Graph ne rend
      alors aucun `contentBytes`, et le code d'origine repondait None : la
      piece devenait « contenu illisible » et ne partait pas dans la GED.
      Constate le 2026-10-02 sur le rapport Veeam d'OPTIM WAFER (DIT
      04216913), que le client annoncait pourtant par « le message d'erreur
      ci-joint ». Son contenu s'obtient sur `/$value`, en MIME brut.
    """
    import base64
    r = _get(tok, "%s/me/messages/%s/attachments/%s" % (GRAPH, mail_id, piece_id))
    if r.status_code != 200:
        return None
    j = r.json() or {}
    b64 = j.get("contentBytes")
    if b64:
        return base64.b64decode(b64)
    if "itemAttachment" in (j.get("@odata.type") or ""):
        v = _get(tok, "%s/me/messages/%s/attachments/%s/$value"
                 % (GRAPH, mail_id, piece_id))
        if v.status_code == 200 and v.content:
            return v.content
    return None


def fil_de_discussion(tok, conversation_id, limite=25):
    """Les messages d'un fil, dans toute la boite, du plus ancien au plus recent.

    Les messages precedents d'un fil sont ranges dans « 01 - DIT ARTIS SUPPORT »
    des qu'un agent les a traites : on interroge donc la boite entiere, pas la
    seule boite de reception.
    """
    if not conversation_id:
        return []
    vus, out = set(), []
    for fid in _dossiers_du_fil(tok):
        r = _get(tok, "%s/me/mailFolders/%s/messages" % (GRAPH, fid),
                 {"$filter": "conversationId eq '%s'" % conversation_id,
                  "$select": SELECT, "$top": str(limite)})
        if r.status_code != 200:
            continue
        for m in (r.json().get("value") or []):
            if m.get("id") not in vus:
                vus.add(m.get("id"))
                out.append(m)
    return sorted(out, key=lambda m: m.get("receivedDateTime") or "")


_CACHE_DOSSIERS = []


def _dossiers_du_fil(tok):
    """Boite de reception + dossier des mails traites. Resolu une seule fois."""
    if _CACHE_DOSSIERS:
        return _CACHE_DOSSIERS
    ids = []
    try:
        ids.append(_id_boite(tok)[0])
    except Exception:
        pass
    try:
        import mail_dit_ecriture as _E          # import tardif : evite le cycle
        fid = _E._id_dossier_traite(tok)
        if fid:
            ids.append(fid)
    except Exception:
        pass
    _CACHE_DOSSIERS.extend(ids)
    return ids


def premier_message_client(msgs):
    """Le premier message du fil qui vienne de l'EXTERIEUR.

    C'est la que la demande est ecrite. Sur un rappel (« vous n'avez pas
    repondu »), le mail courant ne dit rien d'exploitable — le precedent, si.
    """
    for m in msgs:
        a = addr(m)
        if a and a.split("@")[-1].lower() not in INTERNE:
            return m
    return msgs[0] if msgs else None


_MOTS = re.compile(r"[0-9a-zA-ZÀ-ÿ]{4,}")


def _empreinte(txt):
    """Les mots significatifs d'un texte, en minuscules, sans doublon."""
    return {w.lower() for w in _MOTS.findall(txt or "")}


# Mots tout en majuscules qui ne designent personne : ils ne doivent pas
# passer pour un nom propre et separer deux demandes identiques.
_PAS_UN_NOM = {
    "URGENT", "TRES", "RE", "TR", "FW", "FWD", "REF", "DIT", "IT", "OU", "PC",
    "MAC", "VPN", "RDS", "SVP", "STP", "MDP", "TVA", "SARL", "SAS", "SASU",
    "SCI", "SCP", "SA", "ASAP", "NEW", "CC", "AD", "DFM", "HS", "PJ", "CR",
    "NAS", "VM", "SSO", "MFA", "OK", "KO", "ERP", "CRM", "GED", "ADSL", "SIM",
    "WIFI", "LAN", "WAN", "DNS", "DHCP", "VPN", "RGPD", "BAL", "SAV", "MAJ",
    "INFO", "SOS", "PDF", "USB", "SSD", "HDD", "RAM", "CPU", "IP", "URL",
    # Tires du corpus : tout mot capitale present dans au moins 3 DIT
    # differentes est un intitule, pas quelqu'un. Mesure du 2026-09-17 sur les
    # 408 DIT detaillees — la liste est courte parce qu'un nom de personne,
    # par construction, ne se repete pas.
    "INFORMATIQUE", "INSTALLATION", "KPAX", "LES", "MAIL", "RDV",
    # Meme famille, ajoutes a vue : ce sont des intitules de demande, et ils
    # apparaissent en tete des mails types d'arrivee et de depart.
    "CLIENT", "INTERNE", "SUPPORT", "SERVICE", "COMPTE", "USER", "DEPART",
    "ARRIVEE", "POSTE", "SERVEUR", "PACK", "IPBX", "INSTALLER", "GOURMET",
}
# DEUX mots capitales consecutifs au moins : un prenom suivi d'un nom. Un seul
# mot ne suffit pas — mesure sur le corpus : « ESD » et « LT- » separaient
# alors deux DIT qui n'avaient rien a voir avec des personnes.
_RE_NOM_PERSONNE = re.compile(
    r"\b([A-ZÀ-Þ][A-ZÀ-Þ'\-]+(?:\s+[A-ZÀ-Þ][A-ZÀ-Þ'\-]+)+)\b")


def _noms_propres(txt):
    """Les noms de personne ecrits en majuscules dans un texte.

    Sert a distinguer deux demandes baties sur le meme modele : chez les
    clients qui envoient leurs arrivees et departs avec un texte type, seul le
    NOM change, et c'est justement lui qui fait la difference.
    """
    out = set()
    for groupe in _RE_NOM_PERSONNE.findall(txt or ""):
        # Les particules courtes (DA, DE, LE, VAN…) font partie du nom mais ne
        # le designent pas : « MAITE DA COSTA-MOREIRA » doit rendre MAITE et
        # COSTA-MOREIRA. Sans cela, la particule coupait la suite en deux mots
        # isoles et le nom passait inapercu (2026-09-17, DIT 100173083).
        mots = [w for w in groupe.split()
                if len(w) >= 3 and w not in _PAS_UN_NOM]
        if len(mots) >= 2:
            out.update(mots)
    return out


def _ressemblance(a, b):
    """Part des mots du texte le plus court qu'on retrouve dans l'autre.

    Le recouvrement plutot que Jaccard : le commentaire d'une DIT est souvent
    une reprise PARTIELLE du mail (l'agent coupe la signature, reformule la
    fin). Jaccard punirait cette asymetrie, le recouvrement non.
    """
    ea, eb = _empreinte(a), _empreinte(b)
    if not ea or not eb:
        return 0.0
    return len(ea & eb) / float(min(len(ea), len(eb)))


def dit_du_fil(code_client, msgs, seuil=0.60):
    """La DIT deja creee pour ce fil, s'il y en a une.

    Rend (numero, score) ou (None, meilleur_score). Rend (None, None) si Artis
    n'a pas pu etre interroge — l'inconnu ne doit pas passer pour une absence,
    sinon on proposerait un doublon a chaque incident reseau.
    """
    if not code_client or not msgs:
        return None, None
    origine = premier_message_client(msgs) or msgs[0]
    texte = ((origine.get("body") or {}).get("content")
             or origine.get("bodyPreview") or "")
    texte = body_utile(texte)
    debut = origine.get("receivedDateTime")
    try:
        import datetime as _dt
        import requests as _rq
        import mail_dit_ecriture as _E          # import tardif : evite le cycle
        base, tok = _E._artis()
        d0 = _dt.datetime.fromisoformat(str(debut)[:19].replace("Z", ""))
        d0 = (d0 - _dt.timedelta(days=1)).date().isoformat()
        d1 = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
        r = _rq.get("%s/client/%s/dit" % (base, code_client),
                    params={"token": tok, "dateDebut": d0, "dateFin": d1},
                    headers={"accept": "application/json"}, timeout=40)
        if r.status_code != 200:
            return None, None
        dits = r.json() or []
    except Exception:
        return None, None

    meilleur, score_max = None, 0.0
    for d in dits:
        if not isinstance(d, dict):
            continue
        # UN TICKET CLOS N'EST PAS UN FIL EN COURS. Le 2026-09-28, la
        # demande de Christine Di Meglio (Optim Wafer, client 30910) —
        # le scan de l'imprimante vers son PC ne marche plus depuis
        # qu'elle a change son mot de passe — a ete refusee en
        # « suivi », ressemblance 1.00 avec la DIT 04214950... cloturee
        # depuis le 4 mai. C'est le meme incident qui REVIENT, donc une
        # demande neuve ; sans ce filtre le mail restait en boite sans
        # issue. La regle existait deja pour un numero de DIT cite en
        # clair ; elle manquait ici, ou le rapprochement se fait par
        # ressemblance.
        etat = d.get("etat") or etat_si_clos(d.get("numero") or "")
        if str(etat).lower().startswith(("clot", "annul", "term", "solde")):
            continue
        s = _ressemblance(texte, d.get("commentaire") or "")
        if s > score_max:
            meilleur, score_max = d.get("numero"), s
    return (meilleur if score_max >= seuil else None), round(score_max, 3)


def dit_existe_pour(code_client, depuis, marge_jours=1):
    """Une DIT a-t-elle ete creee pour ce client depuis `depuis` ?

    Rend (True/False, liste des numeros). En cas d'erreur d'appel on rend
    `None` : l'inconnu ne doit pas se faire passer pour une absence, sinon on
    proposerait un doublon a chaque incident reseau.
    """
    if not code_client or not depuis:
        return None, []
    try:
        import datetime as _dt
        import requests as _rq
        import mail_dit_ecriture as _E          # import tardif : evite le cycle
        base, tok = _E._artis()
        d0 = _dt.datetime.fromisoformat(str(depuis)[:19].replace("Z", ""))
        d0 = (d0 - _dt.timedelta(days=marge_jours)).date().isoformat()
        d1 = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
        r = _rq.get("%s/client/%s/dit" % (base, code_client),
                    params={"token": tok, "dateDebut": d0, "dateFin": d1},
                    headers={"accept": "application/json"}, timeout=40)
        if r.status_code != 200:
            return None, []
        dits = r.json() or []
        nums = [d.get("numero") for d in dits if isinstance(d, dict)]
        return (len(nums) > 0), nums
    except Exception:
        return None, []


_CACHE_BIENS = {}


def bien_informatique(code_client):
    """Le bien « INFOGERANCE » du client, ou None s'il n'en a pas.

    Un ticket informatique ne doit jamais porter un copieur. Les clients qui
    n'ont pas de bien d'infogerance (ceux qui ne sont chez nous que pour
    l'impression, par exemple) n'en auront donc aucun : Artis se contente du
    couple client + site.
    """
    if not code_client:
        return None
    if code_client in _CACHE_BIENS:
        return _CACHE_BIENS[code_client]
    trouve = None
    try:
        import requests as _rq
        import mail_dit_ecriture as _E          # import tardif : evite le cycle
        base, tok = _E._artis()
        # `maxResults` est INDISPENSABLE : sans lui l'API ne rend que les 20
        # premiers biens. Le client 29667 en a 72, le 9599 en a 166 — et leur
        # bien d'infogerance etait au-dela, d'ou un « aucun bien » faux qui a
        # fait creer un ticket sans bien (constate par Elliot le 2026-09-11).
        r = _rq.get("%s/client/%s/bien" % (base, code_client),
                    params={"token": tok, "maxResults": "9999"},
                    headers={"accept": "application/json"}, timeout=90)
        if r.status_code == 200:
            biens = r.json() or []
            for b in biens:
                texte = "%s %s" % (b.get("identifiantFabricant") or "",
                                   b.get("libelle") or "")
                if "INFOG" in texte.upper():
                    trouve = b.get("identifiantFabricant")
                    break
            # Pas d'infogerance : le client n'est pas forcement sans bien
            # informatique pour autant. Elliot, 2026-09-17 (DIT 100174308 et
            # 100173884, client 30802) : « Infogerance — si pas infogerance,
            # prendre BAL EXCHANGE 365 ». Sans ce repli, ses tickets partaient
            # sans aucun bien.
            if not trouve:
                for b in biens:
                    texte = "%s %s" % (b.get("identifiantFabricant") or "",
                                       b.get("libelle") or "")
                    if "BAL EXCHANGE 365" in texte.upper():
                        trouve = b.get("identifiantFabricant")
                        break
    except Exception:
        trouve = None
    _CACHE_BIENS[code_client] = trouve
    return trouve


_ETATS_DIT = {}


def etat_si_clos(numero):
    """Etat du ticket s'il n'est PLUS ouvert, "" sinon (ou en cas de doute).

    Sert a `classer` : un numero de DIT dans l'objet ne vaut « suivi » que si
    le ticket vit encore. Le resultat est garde en memoire le temps du
    processus — une passe croise plusieurs fois les memes numeros, et Artis
    n'a pas a etre interroge pour chacun.

    En cas de doute — Artis muet, numero inconnu — on renvoie "" : le
    comportement d'avant s'applique, on ne transforme pas un vrai suivi en
    demande neuve sur une panne de lecture.
    """
    numero = str(numero or "")
    if numero in _ETATS_DIT:
        return _ETATS_DIT[numero]

    def _lire(n):
        try:
            import mail_dit_ecriture as _E
            return (_E.relire_dit(n) or {}).get("etat") or ""
        except Exception:
            return ""

    # Les objets de mail portent souvent le numero d'INTERVENTION — la DIT
    # suivie de deux chiffres : « [IT 10015112703] » designe la DIT 100151127,
    # « [IT 0421680401] » la DIT 04216804. Interroger Artis sur le numero
    # complet ne renvoie rien, et le ticket passait alors pour vivant.
    # On retire le SUFFIXE D'INTERVENTION, pas un nombre fixe de chiffres : les
    # DIT anciennes en comptent huit, les recentes neuf.
    etat = _lire(numero)
    if not etat and len(numero) > 8:
        etat = _lire(numero[:-2])
    v = "" if (not etat or etat in ETATS_OUVERTS) else etat
    _ETATS_DIT[numero] = v
    return v




# Un client qui RELANCE. Ces phrases ne demandent rien de neuf : elles disent
# qu'une demande precedente est restee sans reponse. Elles sont donc la preuve
# qu'un mail a ete perdu — et le comble, c'est qu'elles etaient perdues a leur
# tour, declassees en « candidat faible » pour un simple « RE: » dans l'objet.
# Le 2026-09-25, MPL : « Pourriez-vous prendre en consideration ma demande dont
# je n'ai pas recu de ticket ? » ; OPQIBI : « Je n'ai eu aucun retour ? ».
RE_RELANCE = re.compile(
    r"(pas\s+(re[cç]u|eu)\s+(de\s+)?(ticket|retour|r[ée]ponse|nouvelle)"
    r"|aucun(e)?\s+(retour|r[ée]ponse|nouvelle|suite)"
    r"|sans\s+(retour|r[ée]ponse)\s+de\s+votre\s+part"
    r"|je\s+me\s+permets\s+de\s+relancer"
    r"|je\s+reviens\s+vers\s+vous"
    r"|toujours\s+(pas|sans)\s+(de\s+)?(retour|r[ée]ponse|nouvelle)"
    r"|prendre\s+en\s+consid[ée]ration\s+ma\s+demande)", re.I)


def batir(m, index, num_dits=None, libelles=None, attrs=None, sym=None,
          tok=None):
    """Transforme un mail en proposition, ou None s'il ne doit pas en produire.

    `tok` (jeton Graph) active la lecture du FIL : sans lui on ne regarde que le
    mail isole, ce qui suffit pour une demande neuve mais pas pour un rappel.
    """
    verdict, motif = classer(m, num_dits, etat_dit=etat_si_clos)
    if not verdict.startswith("candidat"):
        return None, verdict, motif
    if m.get("_dit_clos"):
        motif = "%s (DIT %s %s : le fil est repris, la demande est neuve)" % (
            motif, m["_dit_clos"][0], m["_dit_clos"][1])
        # Le declassement en « candidat faible » ne tenait qu'a un signal de
        # suite : objet de reponse, fil deja vu. Le ticket cite etant clos, ce
        # signal est REFUTE — il n'y a plus de suite a poursuivre, et laisser
        # la proposition en confiance basse revient a ne rien faire du mail.
        # C'est ce qui restait entre DIRECT IMPRESSION et son ticket apres que
        # la regle du ticket clos ait, elle, bien fonctionne.
        if verdict == "candidat_faible" and any(
                x in motif for x in ("suite probable", "objet de reponse",
                                     "fil deja vu")):
            verdict = "candidat_fort"
            motif += " ; declassement leve, le signal de suite est refute"

    brut = (m.get("body") or {}).get("content") or m.get("bodyPreview") or ""
    corps = body_utile(brut)
    # Ce qui est CITE sous la demande : sur un transfert, le probleme y est.
    histo = historique_cite(brut)
    subj = m.get("subject") or ""

    # ── le fil, quand on peut le lire ────────────────────────────────────
    # Un mail qui repond a un fil pose deux questions : le ticket existe-t-il
    # deja, et ou est ecrite la demande ? Les deux se repondent en lisant le
    # fil, pas le mail.
    fil, origine = [], None
    if tok and m.get("conversationId"):
        try:
            fil = fil_de_discussion(tok, m["conversationId"])
        except Exception:
            fil = []
        if len(fil) > 1:
            origine = premier_message_client(fil)

    # 1) code client ecrit en clair dans l'objet : le signal le plus fiable
    client = pid = None
    methode = ""
    libelles = libelles or {}
    # Un code client ecrit en clair est le signal le plus fiable. On le cherche
    # d'abord dans l'objet, puis dans le corps ("...sur le site TD Batiment
    # client 4260..."). Garde-fou : le code doit exister dans le referentiel,
    # sinon on confond avec un numero de serie ou une reference interne.
    client, methode = code_client_du_texte(subj, corps, libelles)
    if not client:
        client, pid, methode = trouver_client(m, index)
    # Dernier recours : le NOM du client, ecrit dans l'objet ou en tete du
    # corps. C'est frequent sur un transfert interne, ou l'adresse ne designe
    # qu'un collegue.
    if not client:
        client, methode = client_par_nom(subj + chr(10) + corps[:400], libelles)

    # Le ticket de ce fil existe-t-il deja dans Artis ? On ne le demande
    # qu'une fois le client connu — sans client, la question n'a pas de sens.
    dit_deja, score_fil = (None, None)
    if client and len(fil) > 1:
        dit_deja, score_fil = dit_du_fil(client, fil)
        if dit_deja:
            return None, "suivi", ("DIT %s existe deja pour ce fil "
                                   "(ressemblance %s)" % (dit_deja, score_fil))

    # Pas de ticket : la demande est dans le message d'ORIGINE du fil, pas dans
    # le rappel qu'on vient de recevoir.
    if origine is not None and origine.get("id") != m.get("id"):
        texte_origine = body_utile((origine.get("body") or {}).get("content")
                                   or origine.get("bodyPreview") or "")
        # On ne remplace la demande par le message d'origine QUE si le mail
        # courant ne dit rien lui-meme. La regle precedente prenait le plus
        # LONG des deux, et jetait donc l'apport du dernier message : le
        # second mail de FEDOM n'ajoutait qu'une ligne — « Ajout : il faut
        # egalement lui donner acces au repertoire TECHNIQUE » — et c'est
        # exactement elle qui a disparu de la DIT 100173837.
        if texte_origine and corps_muet(corps):
            corps = texte_origine
            histo = historique_cite((origine.get("body") or {}).get("content")
                                    or origine.get("bodyPreview") or "")
        elif texte_origine and texte_origine[:120] not in (histo or ""):
            # Le mail courant ne cite pas l'original : on le met en historique,
            # sinon la demande d'origine est perdue pour l'agent.
            histo = ((histo + "\n\n") if histo else "") + texte_origine

    # Les pieces : une requete Graph par proposition retenue, pas par mail lu.
    # Indispensable ici — `hasAttachments` ment sur les images incrustees.
    pieces = []
    if tok:
        try:
            # Tout le fil : la piece que le client commente est souvent dans un
            # message PRECEDENT (P-0225). A defaut de fil, le mail seul.
            pieces = (pieces_du_fil(tok, fil) if len(fil) > 1 else
                      pieces_du_mail(tok, m.get("id"),
                                     sans_texte=corps_muet(corps),
                                     noms_corps=images_du_corps(sans_html(brut))))
        except Exception:
            pieces = []

    doute = methode.startswith("domaine ambigu")
    # …sauf quand le NOM du client est écrit dans le domaine. « domaine ambigu
    # (2 clients) » bloquait tout ce qui venait de cheplapharm.fr alors que le
    # client retenu s'appelle CHEPLAPHARM FRANCE : l'ambiguïté était levée par
    # la seule lecture des deux chaînes (Elliot, 2026-09-18, sur la demande de
    # libération de quarantaine restée en boîte).
    if doute and client:
        racine = (addr(m).split("@")[-1] or "").lower().rsplit(".", 1)[0]
        racine = re.split(r"[.\-_]", racine)[-1] if racine else ""
        nom = (_libelle_client(libelles, client) or "").lower()
        if len(racine) >= 5 and racine in nom:
            doute = False
            methode += " — levé : le nom du client est dans le domaine"
    # Decision d'Elliot : on propose quand meme, mais le doute doit etre VISIBLE.
    # Mesure : precision 36,8 % contre 73,6 % sur un client sur - deux fois moins fiable.
    dom_possibles = []
    if doute:
        d = addr(m).split("@")[-1]
        dom_possibles = [c for c, _ in (index["domaines"].get(d) or [])][:6]

    sym_code, sym_pourquoi, sym_part = deviner_symptome(subj, corps)

    cat = categorie(m)
    codes = CATEGORIES[cat]
    attrs = attrs if attrs is not None else {}
    sym = sym if sym is not None else {"par_client": {}, "global": [], "libelles": {}}

    # Dix champs sont des attributs du client : on les LIT dans son historique.
    # Le repli sur une deduction depuis le texte ne sert que pour un client
    # inconnu ou sans historique.
    a = attrs.get(client or "", {})
    # L'historique du client ne peut proposer qu'un domaine QUI EST LE NOTRE.
    # Un client dont les tickets passes sont tous « Impression » ferait sinon
    # creer une intervention d'impression pour une demande informatique — ce
    # qui l'envoie chez la mauvaise equipe et supprime meme le champ « type de
    # travail », qui n'existe pas pour cette activite (DIT 100173675).
    if a.get("domaine") in DOMAINES_NOTRES:
        domaine, force, dom_src = a["domaine"], 1, "historique du client"
    else:
        domaine, f2 = deviner_domaine(subj + " " + corps)
        force, dom_src = f2, ("mots-clés du mail" if f2 else "défaut")
    # Verrou final : le devinage par mots-cles peut lui aussi sortir un domaine
    # qui n'est pas le notre (CNX « Connexion » notamment, absent des 408 DIT
    # creees depuis cette boite). On retombe sur INFO plutot que d'envoyer le
    # ticket a une equipe qui ne l'attend pas.
    if domaine not in DOMAINES_NOTRES:
        domaine, force, dom_src = "INFO", 0, "défaut : seul l'informatique nous concerne"


    # typeDit : Elliot a tranche 05 EMAIL CLIENT pour le canal mail, meme si la
    # moitie des agents saisissent 01 APPEL CLIENT par habitude. La categorie
    # supervision impose 07.
    type_dit = codes["type_dit"]
    # La nature ne se LIT plus nulle part : elle decoule de la categorie, point.
    # Elliot, 2026-09-15 : « ARRETE de mettre la nature maintenance curatif,
    # c'est TOUJOURS HOTLINE ». L'historique du client avait impose « 10
    # Maintenance / Curatif » a la DIT 100173969 (PROTAIS VIGIL), exactement
    # comme il avait impose « 90 Preventif » le 2026-09-11 : cette source est
    # mauvaise deux fois sur deux, on la supprime.
    nature = codes["nature"]
    # La priorite vient du CONTENU (ampleur du blocage), pas de
    # l'historique du client : regle metier d'Elliot du 2026-09-09.
    priorite, prio_pourquoi = deviner_priorite(subj + " " + corps)

    # UNE RELANCE EST UNE PREUVE, PAS UN DOUTE. Arrive ici, aucun ticket
    # n'existe pour ce fil — la recherche vient d'echouer plus haut. Si en
    # plus le client ecrit qu'il n'a eu ni retour ni ticket, la demande
    # d'origine s'est bien perdue : la declasser une seconde fois pour un
    # « RE: » dans l'objet, c'est la perdre deux fois. 2026-09-25, MPL :
    # « ma demande dont je n'ai pas recu de ticket ».
    if (verdict == "candidat_faible" and client
            and RE_RELANCE.search(corps[:1200])):
        verdict = "candidat_fort"
        motif += " ; relance du client, aucun ticket pour ce fil"

    return {
        "mail_id": m.get("id"),
        "message_id": m.get("internetMessageId"),
        "conversation": m.get("conversationId"),
        "dossier": m.get("_folder"),
        "recu": m.get("receivedDateTime"),
        # Date a poser sur le DIT : celle du MAIL, pas celle de la
        # creation -- le ticket doit refleter le moment de la demande.
        "date_dit": _date_locale(m.get("receivedDateTime")),
        "de": addr(m),
        # Destinataires : la regle « alerte SOC a la corbeille » depend de la
        # presence de mco@dfm.fr en To ou Cc. Sans les stocker, la condition
        # ne peut pas etre verifiee apres coup.
        "destinataires": [((x.get("emailAddress") or {}).get("address") or "").lower()
                          for x in ((m.get("toRecipients") or [])
                                    + (m.get("ccRecipients") or []))],
        # Le nom affiche de l'expediteur : sert a nommer le demandeur
        # dans le commentaire quand le mail n'a pas de corps.
        "de_nom": (((m.get("from") or {}).get("emailAddress") or {})
                   .get("name") or "").strip(),
        "objet": subj,
        # corps nettoye = commentaire du DIT, suivi du fil cite quand il y en
        # a un : Elliot (P-0170) « le coeur du probleme s'y trouve ».
        "commentaire": (corps + "\n\n" + SEPARATEUR_HISTO + "\n" + histo
                        if histo else corps),
        "historique": histo,
        # `hasAttachments` ne suffit pas : Microsoft le laisse a False quand la
        # piece est INCRUSTEE dans le corps, ce qui est le cas des photos
        # envoyees depuis un telephone (P-0182 et P-0183). On liste donc
        # vraiment les pieces, et c'est cette liste qui fait foi.
        "pieces": pieces,
        "piece_jointe": bool(pieces) or bool(m.get("hasAttachments")),
        "tier": verdict,
        "motif": motif,
        # Trace du croisement avec Artis : utile a l'ecran, et indispensable
        # pour comprendre apres coup pourquoi une proposition a survecu.
        "fil_messages": len(fil),
        "fil_score_dit": score_fil,
        "client": client,
        "client_lib": _libelle_client(libelles, client) if client else "",
        "pid_interlocuteur": pid,
        "methode_client": methode,
        "doute_client": doute,
        "clients_possibles": dom_possibles,
        "categorie": cat,
        "type_dit": type_dit,
        "nature": nature,
        "priorite": priorite,
        "priorite_pourquoi": prio_pourquoi,
        "domaine": domaine,
        "domaine_sur": force > 0,
        "domaine_source": dom_src,
        # lus dans l'historique du client -- a ne pas deviner
        "org_int": a.get("orgInt"),
        "site": a.get("site"),
        # JAMAIS le bien de l'historique tel quel : chez un client qui a des
        # copieurs, ce serait un copieur (DIT 100173686). On prend le bien
        # d'infogerance, ou aucun.
        "bien": (a.get("bien") if "INFOG" in (a.get("bien") or "").upper()
                 else bien_informatique(client)),
        # L'UO n'est pas lue dans l'historique : elle depend du symptome, que
        # l'agent choisit. Reste vide jusque-la.
        "uo": "",
        # gp_travail est recalcule par Artis a la creation : on le garde pour
        # information mais il n'est pas pilotable.
        "gp_travail": a.get("gp"),
        "type_facturation": a.get("fact"),
        "attrs_connus": bool(a),
        # Le domaine MAJORITAIRE des tickets passes de ce client. On ne s'en
        # sert pas pour decider (il est ecrase par le verrou « INFO seul »),
        # mais un client dont tout l'historique est de la telephonie ou de
        # l'impression merite un coup d'oeil avant de creer une DIT info.
        # Elliot (P-0179) : « j'ai trouve car le client est QUE telephonie ».
        # Mesure : aucun mot-cle (3cx, telephonie, numero de telephone) ne
        # separe ces demandes — les 21 mails qu'ils touchent dans le corpus
        # ont TOUS ete traites par l'equipe info. Donc on signale, on ne filtre pas.
        "domaine_client_hist": a.get("domaine") or "",
        # Ce que les agents ont deja tranche pour ce client / cet expediteur.
        "metier_signale": signal_metier(client, addr(m)),
        # Le mail s'adresse-t-il a un collegue plutot qu'au support ?
        "destinataire_interne": destinataire_interne(
            corps + "\n" + (histo or ""), addr(m)),
        # le symptome ne se predit pas : on propose, l'agent choisit
        # Symptome pre-rempli quand une regle metier s'applique. Il reste
        # modifiable, et l'UO en decoule (ADM-* -> HLN3).
        "symptome": sym_code or "",
        "symptome_pourquoi": sym_pourquoi,
        "symptome_part": sym_part,
        "symptome_libelle": (sym.get("libelles") or {}).get(sym_code or "", ""),
        # Photo de ce que l'outil a propose. Ne doit JAMAIS etre modifiee :
        # c'est la reference contre laquelle on mesure les corrections des
        # agents, donc la matiere premiere de l'apprentissage.
        "propose": {"client": client or "", "symptome": sym_code or "",
                    "uo": uo_pour(sym_code) if sym_code else "",
                    "priorite": priorite, "domaine": domaine,
                    "nature": nature, "type_dit": type_dit,
                    "tier": verdict},
        "symptomes_suggeres": suggestions_symptome(client, sym, 5),
        "etat": "nouveau",
    }, verdict, motif


# ═════════════════════════════════════════════════════════ cycle
def _mail_approx(p):
    """Reconstruit un mail a partir d'une proposition, pour rejouer le tri.

    Le corps brut n'est pas conserve — on garde le corps nettoye, qui suffit :
    les marqueurs qui comptent (plateformes de support tierces) y survivent.
    """
    return {"subject": p.get("objet") or "",
            "from": {"emailAddress": {"address": p.get("de") or "",
                                      "name": p.get("de_nom") or ""}},
            "body": {"content": p.get("commentaire") or ""},
            "bodyPreview": (p.get("commentaire") or "")[:400],
            "conversationId": p.get("conversation") or "",
            "internetMessageId": p.get("message_id") or p.get("id"),
            "receivedDateTime": p.get("recu") or ""}


def _hors_perimetre(p):
    """Les champs d'une proposition qui sortent de notre perimetre, avec la
    valeur a leur donner. Rend une liste de (champ, valeur)."""
    out = []
    if p.get("domaine") not in DOMAINES_NOTRES:
        out.append(("domaine", "INFO"))
    if p.get("nature") not in NATURES_NOTRES:
        out.append(("nature", CATEGORIES.get(p.get("categorie") or "hotline",
                                             CATEGORIES["hotline"])["nature"]))
    if p.get("client") and "INFOG" not in (p.get("bien") or "").upper():
        b = bien_informatique(p["client"])
        if b and b != p.get("bien"):
            out.append(("bien", b))
    return out


FENETRE_REGROUPEMENT = 45 * 60      # secondes


def _regrouper(en_attente):
    """Marque `regroupe_avec` sur les propositions qui sont UNE seule demande.

    Elliot (2026-09-14, P-0182 et P-0183) : « concernent la meme demande, la
    cliente a mis des photos pour expliquer son probleme ». Un expediteur qui
    envoie ses photos une par une produit autant de mails — et autant de
    propositions — pour un seul probleme, et donc un seul DIT.

    Critere volontairement etroit : meme expediteur, meme client, moins de
    45 minutes d'ecart, et au moins un des deux mails n'a pas de texte (c'est
    ce qui distingue « la suite de mes photos » d'une seconde vraie demande).
    Le regroupement est PROPOSE, pas impose : l'ecran le montre et l'agent
    decoche s'il n'est pas d'accord.
    """
    par_exp = {}
    for p in en_attente:
        cle = ((p.get("de") or "").lower(), p.get("client") or "")
        if cle[0]:
            par_exp.setdefault(cle, []).append(p)
    change = []
    for lot in par_exp.values():
        if len(lot) < 2:
            continue
        lot.sort(key=lambda p: p.get("recu") or "")
        grappe = [lot[0]]
        for p in lot[1:]:
            if _ecart(grappe[-1].get("recu"), p.get("recu")) <= FENETRE_REGROUPEMENT:
                grappe.append(p)
            else:
                change += _poser_grappe(grappe)
                grappe = [p]
        change += _poser_grappe(grappe)
    return change


def _ecart(a, b):
    try:
        fa = datetime.datetime.fromisoformat((a or "").replace("Z", "+00:00"))
        fb = datetime.datetime.fromisoformat((b or "").replace("Z", "+00:00"))
        return abs((fb - fa).total_seconds())
    except Exception:
        return 10 ** 9


def _poser_grappe(grappe):
    """Ecrit `regroupe_avec` sur chaque membre d'une grappe d'au moins deux."""
    if len(grappe) < 2:
        for p in grappe:
            p.pop("regroupe_avec", None)
        return []
    # Au moins un mail muet : sinon ce sont deux demandes distinctes.
    if not any(corps_muet(p.get("commentaire")) for p in grappe):
        return []
    ids = [p.get("id") for p in grappe]
    change = []
    for p in grappe:
        autres = [i for i in ids if i != p.get("id")]
        if p.get("regroupe_avec") != autres:
            p["regroupe_avec"] = autres
            change.append(p.get("id"))
    return change


def retrier(log=None):
    """Ecarte les propositions en attente que les regles ne retiendraient plus.

    Appelee a chaque cycle : quand une regle est corrigee, la file se nettoie
    d'elle-meme au tour suivant, sans intervention ni reconstruction.

    Prudence deliberee : on ECARTE seulement. Si le nouveau verdict reste un
    candidat, on ne retouche pas son niveau de confiance — un `candidat_fort`
    qui deviendrait `faible` n'a pas a bouger sous les yeux de l'agent. Et rien
    de ce qu'un humain a valide, ecarte ou fait creer n'est touche.
    """
    dire = log or (lambda *_a: None)
    num_dits = _num_dits()
    ecartes, enrichies, repares, ombres = [], [], [], []
    with _lock:
        d = _charger()
        en_attente = [x for x in d["props"]
                      if x.get("etat") == "nouveau"
                      and not x.get("dit_numero")
                      and not x.get("deja_traite")
                      and not x.get("confirme_humain")]
        libelles_sym = (_symptomes().get("libelles") or {})
        attrs_cli = _attrs_clients()
        libelles_cli = _libelles_clients()
        for p in en_attente:
            # Champ ajoute apres coup : les propositions deja en file doivent
            # l'avoir aussi, sinon le signal « client telephonie » n'apparait
            # que sur les mails a venir.
            # Le client ECRIT dans le mail : une regle ajoutee depuis (« Site:
            # 14939 - ... » des tickets Datto, le nom intercale dans « Client
            # COPRIMHA 29517 ») doit profiter aux propositions deja en file.
            # Sans ca, Elliot revoit le meme defaut sur des mails deja la.
            if not p.get("client"):
                c, comment = code_client_du_texte(p.get("objet"),
                                                  p.get("commentaire"),
                                                  libelles_cli)
                if not c:
                    # Puis le NOM du client, ecrit en toutes lettres. Une regle
                    # ajoutee apres coup doit profiter aux propositions deja en
                    # file : sinon le mail deja arrive reste bloque pendant que
                    # seuls les suivants en beneficient (2026-09-23, « Client
                    # Jdiffusion - MAB FA »).
                    c, comment = client_par_nom(
                        (p.get("objet") or "") + chr(10)
                        + (p.get("commentaire") or "")[:400], libelles_cli)
                if c:
                    p["client"] = c
                    p["client_lib"] = libelles_cli.get(c, "")
                    p["methode_client"] = comment
                    p["doute_client"] = False
                    p["attrs_connus"] = bool(attrs_cli.get(c))
                    repares.append("%s/client" % p.get("id"))
            # Le signal « ce client, c'est de la telephonie » doit apparaitre
            # des que l'agent l'a pose, y compris sur ce qui attend deja.
            sig = signal_metier(p.get("client"), p.get("de"))
            if sig != p.get("metier_signale"):
                p["metier_signale"] = sig
                if sig:
                    repares.append("%s/metier_signale" % p.get("id"))
            # Mode ombre : on note ce qu'on FERAIT, une fois, sans rien faire.
            # Posé au re-tri et jamais réécrit : la décision doit être datée
            # d'avant celle de l'humain pour que la comparaison ait un sens.
            if "ombre" not in p:
                try:
                    o = decider_seul(p)
                    o["quand"] = datetime.datetime.now().isoformat(
                        timespec="seconds")
                    p["ombre"] = o
                    ombres.append(p.get("id"))
                except Exception as e:
                    p["ombre"] = {"action": "attendre", "cible": None,
                                  "pourquoi": "erreur : %s" % str(e)[:80],
                                  "confiance": "nulle"}
                    ombres.append(p.get("id"))
            if "destinataire_interne" not in p:
                p["destinataire_interne"] = destinataire_interne(
                    p.get("commentaire") or "", p.get("de") or "")
                if p["destinataire_interne"]:
                    repares.append("%s/destinataire_interne" % p.get("id"))
            if "domaine_client_hist" not in p:
                p["domaine_client_hist"] = (
                    attrs_cli.get(p.get("client") or "", {}).get("domaine") or "")
                repares.append("%s/domaine_client_hist" % p.get("id"))
            verdict, motif = classer(_mail_approx(p), num_dits)
            if verdict.startswith("candidat"):
                # Une regle de symptome ajoutee depuis doit profiter aussi aux
                # propositions deja en file : sinon l'agent continue de voir un
                # champ vide sur des cas desormais couverts.
                if not p.get("symptome"):
                    c, pourquoi, part = deviner_symptome(p.get("objet"),
                                                         p.get("commentaire"))
                    if c:
                        p["symptome"] = c
                        p["symptome_pourquoi"] = pourquoi
                        p["symptome_part"] = part
                        p["symptome_libelle"] = libelles_sym.get(c, "")
                        p["uo"] = uo_pour(c)
                        enrichies.append(p.get("id"))
                # Champs hors perimetre : une regle corrigee depuis doit
                # profiter aussi aux propositions deja en file.
                for champ, valeur in _hors_perimetre(p):
                    p.setdefault("journal", []).append({
                        "quand": datetime.datetime.now().isoformat(
                            timespec="seconds"),
                        "qui": "outil (re-tri)", "champ": champ,
                        "avant": p.get(champ), "apres": valeur})
                    p[champ] = valeur
                    # la photo suit : c'est l'outil qui se corrige, pas l'agent
                    if isinstance(p.get("propose"), dict):
                        p["propose"][champ] = valeur
                    repares.append("%s/%s" % (p.get("id"), champ))
                continue
            p["etat"] = "ecarte"
            p["traite_par"] = "re-tri"
            p["traite_le"] = datetime.datetime.now().isoformat(timespec="seconds")
            p["note"] = "ecarte au re-tri : %s (%s)" % (verdict, motif)
            ecartes.append((p.get("id"), verdict, p.get("objet") or ""))
        # Regroupement des mails qui ne font qu'une demande (photos en rafale).
        restants = [x for x in en_attente if x.get("etat") == "nouveau"]
        groupes = _regrouper(restants)
        if ecartes or enrichies or repares or groupes or ombres:
            _sauver(d)
        if ombres:
            dire("[mail-dit] mode ombre : decision enregistree sur %d "
                 "proposition(s)" % len(ombres))
        if groupes:
            dire("[mail-dit] regroupement propose sur %d proposition(s) : %s"
                 % (len(groupes), ", ".join(groupes[:8])))
    for pid, verdict, objet in ecartes:
        dire("[mail-dit] re-tri : %s -> %s  %s" % (pid, verdict, objet[:60]))
    if enrichies:
        dire("[mail-dit] symptome propose sur %d proposition(s) : %s"
             % (len(enrichies), ", ".join(enrichies[:8])))
    if repares:
        dire("[mail-dit] %d champ(s) hors perimetre corrige(s) : %s"
             % (len(repares), ", ".join(repares[:10])))
    return len(ecartes)


def completer_historique(tok, log=None, limite=25):
    """Ajoute l'historique cite aux propositions deja en file.

    Sans ca, la correction demandee par Elliot (P-0170 : « il manque
    l'historique de la conversation mail ») ne profiterait qu'aux mails a
    venir, et il faudrait retraiter a la main ce qui attend deja.

    On relit le corps depuis Graph, une fois par proposition : la cle
    `historique` est ensuite posee meme vide, pour ne pas y revenir.
    """
    dire = log or (lambda *_a: None)
    with _lock:
        d = _charger()
        a_faire = [p for p in d["props"]
                   if p.get("etat") == "nouveau"
                   and ("historique" not in p or "pieces" not in p
                        or "destinataires" not in p)][:limite]
    if not a_faire:
        return 0
    trouves, pieces_par_prop, dests = {}, {}, {}
    for p in a_faire:
        histo = ""
        try:
            r = _get(tok, "%s/me/messages/%s" % (GRAPH, p.get("mail_id")),
                     {"$select": "body,bodyPreview,toRecipients,ccRecipients"})
            if r.status_code == 200:
                j = r.json()
                histo = historique_cite((j.get("body") or {}).get("content")
                                        or j.get("bodyPreview") or "")
                dests[p.get("id")] = [
                    ((x.get("emailAddress") or {}).get("address") or "").lower()
                    for x in ((j.get("toRecipients") or [])
                              + (j.get("ccRecipients") or []))]
        except Exception:
            histo = ""
        trouves[p.get("id")] = histo
        # Les pieces : `hasAttachments` ment sur les photos incrustees, il faut
        # les demander. Une requete de plus par proposition, une seule fois.
        try:
            fil = (fil_de_discussion(tok, p["conversation"])
                   if p.get("conversation") else [])
            pieces_par_prop[p.get("id")] = (
                pieces_du_fil(tok, fil) if len(fil) > 1 else
                pieces_du_mail(tok, p.get("mail_id"),
                               sans_texte=corps_muet(p.get("commentaire"))))
        except Exception:
            pieces_par_prop[p.get("id")] = []
    n = 0
    with _lock:
        d = _charger()
        for p in d["props"]:
            if p.get("id") in trouves and "historique" not in p:
                p["historique"] = trouves[p["id"]]
            if p.get("id") in dests and "destinataires" not in p:
                p["destinataires"] = dests[p["id"]]
            if p.get("id") in pieces_par_prop and "pieces" not in p:
                p["pieces"] = pieces_par_prop[p["id"]]
                if p["pieces"]:
                    p["piece_jointe"] = True
            if p.get("etat") != "nouveau":
                continue
            histo = p.get("historique") or ""
            corps = (p.get("commentaire") or "")
            if not histo or not corps or SEPARATEUR_HISTO in corps:
                continue
            # Un commentaire retouche a la main reste celui de l'agent.
            if any(j.get("champ") == "commentaire"
                   for j in (p.get("journal") or [])):
                continue
            p["commentaire"] = corps + "\n\n" + SEPARATEUR_HISTO + "\n" + histo
            if isinstance(p.get("propose"), dict):
                p["propose"]["commentaire"] = p["commentaire"]
            n += 1
        _sauver(d)
    if n:
        dire("[mail-dit] historique du mail ajoute a %d proposition(s)" % n)
    return n


# ───────────────────────────────────────────── rangement automatique
# Les SEULES familles qu'Elliot a explicitement autorisées à partir sans lui.
# Chaque entrée exige l'expéditeur ET l'objet : un motif large rangerait des
# mails que personne n'a vus. Tout rangement est journalisé.
# Referents des autres equipes : quand un mail leur est ADRESSE, il est pour
# eux. Elliot (2026-09-15) : « quand le destinataire c'est Fabien Peverelly
# c'est de la securite, Cyril Moreton par exemple de la telephonie ».
REFERENTS = {
    "fpeverelly@dfm.fr": "securite",
    # L'EQUIPE TELECOM, nommee par Elliot le 2026-09-22 : « s'il y a ces
    # personnes dans les destinataires, c'est a tres forte chance de la
    # telephonie ». Le destinataire est le signal le plus sur dont on dispose —
    # il ne depend pas des mots employes, donc il ne rate pas un « telephone
    # rouge » ou un « trunk » absent de nos listes.
    "cmoreton@dfm.fr":   "telecom",   # Cyril MORETON
    "lakossou@dfm.fr":   "telecom",   # Luther AKOSSOU
    "yelouardi@dfm.fr":  "telecom",   # Yacine EL OUARDI
    "shireche@dfm.fr":   "telecom",   # Samir HIRECHE
    "ckudlak@dfm.fr":    "telecom",   # Cedric KUDLAK
    "acurpen@dfm.fr":    "telecom",   # Anton CURPEN
    "mdesa@dfm.fr":      "telecom",   # Maximilien DE SA
    "itito@dfm.fr":      "telecom",   # Innocent TITO
    "anoah@dfm.fr":      "telecom",   # Augustin NOAH
    # Le CAT est le service impression. Elliot, 2026-09-17, sur le DIT
    # 100174392 (traceur CANON) : « annule et transfere au CAT, comme tu
    # devrais faire ».
    "cat@dfm.fr":        "impression",
    # Serge BARCELO, technicien hotline impression — nomme comme tel par le CAT
    # dans le dossier BSA (4048) le 2026-09-24. Sans lui dans cette table, sa
    # propre demande d'aide au support repartait chez son service.
    "sbarcelo@dfm.fr":   "impression",
}
# A qui transferer, metier par metier, quand le mail demande une action. Le
# rangement seul ne suffit pas : personne ne surveille une boite en continu.
RELAIS_METIER = {"impression": "cat@dfm.fr"}

# La phrase EXACTE de nos transferts entre services. Elle sert deux fois :
# a ecrire le mot du transfert, et a RECONNAITRE dans un fil qu'on l'a deja
# fait — sans quoi le meme dossier repart en boucle d'un service a l'autre.
PHRASE_RELAIS = "Ce mail relève de votre service"

# Vocabulaire de la TELEPHONIE. Il n'a pas servi jusqu'ici a ECARTER un mail —
# mesure du 2026-09-14 : les mots seuls se trompent trop souvent. Il sert ici a
# le RANGER dans la boite de l'equipe concernee, ou une erreur se corrige d'un
# glisser-deposer au lieu de perdre une demande.
RE_TELEPHONIE = re.compile(
    r"(\b3cx\b|t[ée]l[ée]phonie|standard\s+t[ée]l[ée]phonique|code\s+rio|\brio\b"
    r"|poste\s+t[ée]l[ée]phonique|portabilit[ée]|\bsda\b|combin[ée]\b"
    r"|yealink|gigaset|alcatel\s+oxo|op[ée]rateur\s+t[ée]l[ée]phonique"
    # Linkus est le softphone Yeastar : un « probleme de connexion Linkus »
    # est de la telephonie. Range a la main deux fois (les 16 et 17/09) sans
    # que la regle soit ecrite — c'est exactement ce qui fait revenir un mail.
    r"|\blinkus\b|yeastar|\bipbx\b|pr[ée]d[ée]croch|messagerie\s+vocale"
    r"|ligne\s+mobile|forfait\s+mobile|renvoi\s+d.appel"
    # Vocabulaire de l'exploitation telecom, absent jusqu'au 2026-09-22 : le
    # fil « Parc telephonique 5532 ICONOMEDIA » (trunk, numeros a porter) est
    # parti en 01 DIT ARTIS parce qu'aucun de ces mots n'etait reconnu.
    # « Ligne telephonique directe : 01 43 96 … » est une SIGNATURE, pas une
    # demande : on exclut la forme, pas le mot.
    r"|parc\s+t[ée]l[ée]phonique|ligne\s+t[ée]l[ée]phonique(?!\s+directe)"
    r"|\btrunk\b"
    # PAS de « telephonique » tout seul : ce mot vit dans la signature de la
    # moitie des mails (« ligne telephonique directe », « standard »), et il
    # faisait basculer en telecom un « parametrage acces VPN ». Mesure du
    # 2026-09-22 sur 200 mails reels : 15 faux positifs avec, 4 vrais sans.
    r"|\bsip\b|\btoip\b|\bvoip\b|num[ée]ros?\s+(manquants?|[àa]\s+porter"
    r"|port[ée]s?))", re.I)


def echange_interne_traite(m, texte, libelles=None, attrs=None):
    """Un echange ENTRE COLLEGUES sur une demande qui a deja son ticket.

    Elliot (2026-09-15), a propos du fil « DUFERMONT // 15101 // demande
    d'intervention » : « DIT deja creee, echange en interne, juste a deplacer
    vers 01 DIT ARTIS SUPPORT — apprends et reproduis dans les cas futurs ».

    Cadrage retenu, plus etroit que la consigne litterale et valide par lui :
    on n'agit QUE si l'objet porte un code client du referentiel ET que ce
    client a une DIT ouverte ces derniers jours. Sans cette double condition,
    la regle emporterait aussi « RE: Courrier de reclamation » ou
    « RE: 11521 SEF POWER », dont certains attendent peut-etre une action.
    """
    exp = addr(m)
    if not exp or exp.split("@")[-1] not in INTERNE:
        return None                       # ce n'est pas un echange interne
    objet = m.get("subject") or ""
    if RE_DIT.search(objet):
        return None                       # le suivi de ticket s'en charge
    # Le code client n'est pas toujours introduit par le mot « client » :
    # « DUFERMONT // 15101 // demande d'intervention ». On accepte donc un
    # nombre NU de l'objet, a condition qu'il existe au referentiel — et la
    # vraie garantie vient de la suite : il faut aussi une DIT ouverte.
    lib = libelles or _libelles_clients()
    code, _ = code_client_du_texte(objet, "", lib)
    if not code:
        code = next((x for x in re.findall(r"\b\d{4,8}\b", objet) if x in lib), None)
    if not code:
        return None
    try:
        proches = dits_proches({"client": code, "recu": m.get("receivedDateTime"),
                                "objet": objet, "commentaire": texte}, jours=10)
    except Exception:
        return None
    ouvertes = [d for d in proches if d.get("etat") not in ("Annulee",)]
    if not ouvertes:
        return None
    return {"client": code, "dit": ouvertes[0]["numero"],
            "pourquoi": "echange interne sur le client %s, DIT %s existante"
                        % (code, ouvertes[0]["numero"])}


# Vocabulaire de l'IMPRESSION. Il n'existait pas : j'ai rangé des copieurs à la
# main cinq fois en deux jours sans jamais l'écrire, et le traceur CANON du
# DIT 100174392 est passé en ticket informatique faute de ce motif.
# ⚠ « imprimante » SEUL n'y est pas : brancher une imprimante sur un poste est
# de l'informatique — Artis a deux symptômes pour ça, PC-DEM-IMP et PC-INC-IMP.
# On ne retient que la MACHINE et ses consommables. Mesure sur les 1181 mails
# de 14 jours : 29 mails, environ deux par jour, presque tous de vraies
# affaires d'impression.
RE_IMPRESSION = re.compile(
    r"(copieur|photocopieu|traceur|multifonction"
    r"|toner|cartouche|bourrage|tambour|agrafage|agrafeuse"
    r"|noir\s+et\s+blanc\s+par\s+d[ée]faut|recto[- ]verso\s+par\s+d[ée]faut"
    r"|relev[ée]\s+(de\s+)?compteur|compteurs?\s+(copie|impression)"
    r"|canon\s+(image|ir|tm)|konica|ricoh|xerox\s+altalink|kyocera)", re.I)


# Le LIEN internet (fibre, ligne, débit, opérateur) appartient à la téléphonie ;
# le RÉSEAU LOCAL (LAN, wifi, borne, switch, brassage) reste informatique.
# Elliot, 2026-09-18, sur le DIT 100173803 « Lenteur sur la fibre à Nantes » :
# « c'est un ticket téléphonie ».
RE_LIEN_INTERNET = re.compile(
    r"(\bfibre\b|\badsl\b|\bsdsl\b|\bvdsl\b|\bfai\b"
    r"|(lien|ligne|acc[èe]s)\s+internet"
    r"|d[ée]bit\s+(internet|descendant|montant|faible|insuffisant)"
    r"|coupure\s+(internet|de\s+la\s+ligne|r[ée]seau\s+op[ée]rateur)"
    r"|op[ée]rateur\s+(internet|t[ée]l[ée]com)|box\s+(4g|5g|internet))", re.I)
# Une PANNE d'acces internet declaree : ce n'est plus une mention au passage,
# c'est le sujet du mail. « Le site de Cannes Siege et Valbonne n'ont plus
# d'acces a internet » (JOHN TAYLOR, 2026-09-22) n'etait attrape par rien :
# l'objet « Pb internet Cannes siege - Valbonne » ne contient aucun des mots
# exiges (« lien/ligne/acces internet »), et le corps ecrit « acces A
# internet », avec la preposition. Le mail est devenu une DIT informatique
# alors que l'operateur a repondu dans le fil : « une panne generale impacte
# plusieurs operateurs ».
RE_PANNE_INTERNET = re.compile(
    r"((plus|pas|perte|coupure|panne|probl[èe]me|pb|souci)\s+"
    r"(g[ée]n[ée]ral[e]?\s+)?(d[eu']\s*|de\s+l[ae']\s*)?"
    r"(acc[èe]s\s+(à\s+|a\s+)?)?(internet|connexion\s+internet|r[ée]seau\s+op[ée]rateur)"
    r"|internet\s+(hs|ko|down|coup[ée]|ne\s+(fonctionne|marche)\s+plus)"
    r"|(n[e']\s*(ont|a)\s+plus|sans)\s+(d[eu']\s*)?(acc[èe]s\s+(à\s+|a\s+)?)?internet"
    r"|(secours|bascule|relais)\s+4g|4g\s+(de\s+secours|n[e']a\s+pas\s+pris))", re.I)
RE_RESEAU_LOCAL = re.compile(
    r"(\blan\b|wifi|wi-fi|borne|switch|\bvlan\b|c[âa]blage"
    r"|prise\s+r[ée]seau|brassage)", re.I)


def _texte_parle_metier(texte, metier):
    """Le TEXTE lui-meme releve-t-il de ce metier ?

    Elliot : « le metier se lit dans le corps ». C'est ce qui separe deux
    situations que l'adresse seule confond — un metier qui nous demande de
    l'aide sur un sujet informatique, et un metier qui nous transmet une
    affaire qui est bien la sienne.
    """
    t = texte or ""
    if not t:
        return False
    if metier == "telecom":
        return bool(RE_TELEPHONIE.search(t))
    if metier == "securite":
        return bool(RE_AUTRE_METIER.search(t) or RE_AUTRE_METIER_CORPS.search(t))
    return False


def metier_du_destinataire(dest, expediteur=None, texte=None):
    """Le metier designe par les DESTINATAIRES, ou None.

    Gratuit, exact, et independant des mots employes : c'est le signal
    qu'Elliot donne comme le plus sur. Il passe donc avant la lecture par le
    modele, qui coute une requete.

    UNE RESERVE : si c'est CE METIER QUI ECRIT, le mail ne lui appartient pas
    forcement — il peut nous demander de l'aide. Le 2026-09-24 sur le dossier
    BSA (4048), le CAT a ecrit au support pour un poste que son technicien
    n'arrivait pas a prendre en main ; le support lui a renvoye le mail, le CAT
    l'a renvoye au support, et le client a attendu.

    MAIS LA RESERVE TOMBE QUAND LE TEXTE PARLE DU METIER. Le 2026-09-25, une
    fiche d'intervention « DFM - Telephonie », envoyee par un referent telecom
    a un autre referent telecom pour un client qui n'a plus de telephonie, a
    ete privee de son signal et rangee chez les interventions informatiques.
    L'expediteur dit d'ou vient le mail ; le corps dit de quoi il parle.
    """
    joint = dest if isinstance(dest, str) else " ".join(dest or [])
    joint = joint.lower()
    exp = (expediteur or "").lower().strip()
    for adresse, ou in REFERENTS.items():
        if adresse not in joint:
            continue
        if (exp and REFERENTS.get(exp) == ou
                and not _texte_parle_metier(texte, ou)):
            continue
        return ou, "destinataire %s" % adresse
    return None, ""


def modele_disponible():
    """Le modele peut-il repondre ? (cle presente ET budget non epuise)

    Sert a savoir si l'on est en marche NORMALE (l'IA lit) ou en SECOURS (les
    regles prennent le relais). Elliot, 2026-09-22 : « le script est la pour
    remplacer l'IA si plus de credit ».
    """
    try:
        import mail_modele as MM
        return bool(MM.disponible())
    except Exception:
        return False


def metier_par_modele(m, texte, log=None):
    """A quel METIER ce mail appartient-il ? Question posee au modele, pas a
    une liste de mots.

    Elliot, 2026-09-22 : « ok la tu comprends mais POURQUOI de base t'as pas
    compris ? ». Parce que le moteur ne comprend pas : il compare des mots a
    une liste ecrite a la main, et chaque mot absent est un mail mal range —
    « telephone rouge », « trunk », « parc telephonique », « licence 3CX » ont
    tous du etre ajoutes APRES coup. Le modele, lui, lit. Il etait consulte en
    DERNIER, sur ce que les regles aveugles n'avaient pas deja avale.

    On l'interroge donc AVANT de classer un echange interne ou d'ouvrir un
    ticket, et seulement quand aucune regle n'a reconnu le metier : le cout
    reste celui d'une lecture (0,002 EUR) sur les seuls mails douteux.

    Rend « telecom », « impression », « securite » ou None.
    """
    dire = log or (lambda *_a: None)
    try:
        import mail_modele as MM
    except Exception:
        return None
    mid = m.get("internetMessageId")
    avis = MM.avis_memorise(mid) if mid else None
    if not avis:
        if not MM.disponible() or (mid and MM.deja_vu(mid)):
            return None
        brut = (m.get("body") or {}).get("content") or ""
        corps = body_utile(brut)
        histo = historique_cite(brut) or ""
        if histo:
            corps = (corps + chr(10) * 2 + "--- messages precedents ---"
                     + chr(10) + histo[:2000])
        client, _pid, _meth = trouver_client(m, _index_clients())
        lib = _libelles_clients()
        possibles = ([(client, lib.get(client, ""))] if client
                     else clients_candidats(m))
        avis = MM.avis(m.get("subject") or "", corps, addr(m),
                       ", ".join(((r.get("emailAddress") or {}).get("address") or "")
                                 for r in (m.get("toRecipients") or [])),
                       possibles, log=dire)
        if mid:
            MM.marquer_vu(mid)
            if avis:
                MM.memoriser_avis(mid, avis)
    if not avis:
        return None
    dossier = (avis.get("dossier") or "").strip()
    if avis.get("action") == "ranger" and dossier:
        import mail_dit_ecriture as _E3
        # L'IA NOURRIT LE SCRIPT : chaque lecture verse son vocabulaire dans
        # la base de secours, pour que les regles soient le moins mauvaises
        # possible le jour ou le credit manque (Elliot, 2026-09-22).
        try:
            MM.apprendre_vocabulaire(
                (m.get("subject") or "") + chr(10) + (texte or ""), dossier)
        except Exception:
            pass
        if dossier in METIERS_DU_FIL:
            return dossier
        # TOUT dossier valide compte, pas seulement les trois metiers. Le
        # 2026-09-23, le modele a lu une alerte de firmware Sophos et repondu
        # « ranger, informations, confiance haute » — verdict juste, et jete,
        # parce qu'on ne regardait que telecom/impression/securite. Le mail a
        # donc poursuivi sa route et est devenu la DIT 100175054, un ticket ne
        # d'une notification d'editeur. Le modele decide : on l'ecoute.
        # En confiance HAUTE seulement pour les autres dossiers : ranger a la
        # corbeille ou en « traite » ferme une porte, ca se merite.
        if dossier in _E3.DOSSIERS and avis.get("confiance") == "haute":
            return dossier
    return None


def _metier_du_mail(m, texte, dest):
    """« securite », « telecom », ou None — pour ranger dans la BAL du metier.

    Le DESTINATAIRE prime sur le vocabulaire : c'est le signal qu'Elliot a
    donne, et le plus sur. Un mail adresse au responsable maintenance parle de
    securite, quels que soient les mots employes.
    """
    objet = m.get("subject") or ""
    # Un mail qui cite un ticket appartient au SUIVI, pas au rangement metier :
    # il doit partir chez le dernier intervenant. Sans ce garde-fou, une
    # reponse sur « [IT 10017241902] » ou le coordinateur technique est en
    # copie serait rangee en telephonie au lieu d'etre transmise.
    if RE_DIT.search(objet):
        return None, ""
    for adresse, ou in REFERENTS.items():
        if adresse in dest:
            return ou, "destinataire %s" % adresse
    if RE_AUTRE_METIER.search(RE_ADRESSE.sub(" ", objet)):
        return "securite", "vocabulaire securite dans l'objet"
    if RE_AUTRE_METIER_CORPS.search(texte):
        return "securite", "acces / domotique"
    if RE_TELEPHONIE.search(texte):
        return "telecom", "vocabulaire telephonie"
    # Le lien internet doit être le SUJET du mail, pas une mention au passage :
    # « Problème connexion imprimante/PC » qui évoque « le boîtier fibre
    # Orange » reste une demande informatique. On l'exige donc dans l'objet, et
    # on s'abstient dès que le réseau local est de la partie.
    if (RE_LIEN_INTERNET.search(objet)
            and not RE_RESEAU_LOCAL.search(texte + " " + objet)):
        return "telecom", "lien internet (fibre, ligne, operateur)"
    # Une panne d'acces declaree vaut dans le CORPS aussi : dire « le site n'a
    # plus d'acces a internet » n'est pas une mention au passage, c'est la
    # demande elle-meme. Le reseau local garde son droit de veto.
    if (RE_PANNE_INTERNET.search(objet + " " + texte)
            and not RE_RESEAU_LOCAL.search(texte + " " + objet)):
        return "telecom", "panne d'acces internet (lien operateur)"
    if RE_IMPRESSION.search(texte):
        return "impression", "vocabulaire impression"
    return None, ""


RANGEMENTS_AUTO = [
    {
        # Newsletters et campagnes commerciales. Elles ne portent aucune
        # demande : on les sort de la boite sans les jeter, parce que toutes
        # ne sont pas de la publicite pure (annonce d'un editeur, changement
        # de conditions). Elliot, 2026-09-16, DIT 100174085 ne d'une
        # newsletter Koban : « quel rapport, c'est de la pub ».
        "nom": "envoi-de-masse",
        "expediteur": None,
        "objet": None,
        "quand": lambda m: envoi_de_masse(m),
        "dossier": "informations",
        "autorise": "Elliot, 2026-09-16",
    },
    # ── robots dont AUCUN mail n'est une demande du support ────────────────
    # Le 16/09 j'ai range ces expediteurs a la main, un par un, sans en faire
    # de regle : le lendemain matin ils etaient tous revenus (24 mails en une
    # nuit, dont 5 Cyclop et 2 Avideon). Une correction qui n'est pas ecrite
    # dans le code n'est pas une correction.
    # `objet` reste large : c'est l'ADRESSE qui identifie ces envois, jamais le
    # sujet.
    {
        # Systeme QSL d'Avideon : interventions de videosurveillance.
        "nom": "avideon-qsl",
        "expediteur": "qsl@avideon.fr",
        "objet": re.compile(r".", re.S),
        "dossier": "securite",
        "autorise": "range ainsi le 2026-09-16, a corriger si besoin",
    },
    {
        # Les alertes de la GED Zeendoc (echeances fournisseurs, factures a
        # payer, reglements) arrivent par la boite relayee d'un client. Ce ne
        # sont ni des demandes ni des incidents : personne n'y repond, elles
        # se classent. L'equipe les range en 03 - DEVIS — huit fois de suite
        # du 17 au 23 septembre 2026, a la main. Notre moteur, lui, en a
        # envoye une en 04 - INFORMATIONS et une A LA CORBEILLE le 25 : deux
        # lectures du meme mail, dont une qui le perd. D'ou cette regle.
        "nom": "zeendoc-alerte-ged",
        "expediteur": "mailer@zeendoc.com",
        "objet": re.compile(r"^\s*Zeendoc\s*-", re.I),
        "dossier": "BAL INFO/03 - DEVIS",
        "autorise": "pratique de l'equipe, mesuree le 2026-09-27",
    },
    {
        # Supervision des liens Internet OVH : « [OVH-FAI] [DOWN] Monitoring de
        # votre ligne ... ». A elle seule, cette famille represente 26 des 73
        # mails que l'automatisme refusait faute de client identifiable
        # (mesure du 2026-09-17) — c'est le premier poste de la boite.
        "nom": "ovh-monitoring-ligne",
        "expediteur": "support@services.ovhcloud.com",
        "objet": re.compile(r"^\s*\[OVH-FAI\]\s*\[(UP|DOWN)\]", re.I),
        # Le hall d'entree d'une BAL n'est pas un dossier : y deposer un mail,
        # c'est le rendre invisible a l'equipe qui devrait le lire. Les
        # telecoms rangent eux-memes ces supervisions en « 05 - OPERATEUR »
        # (verifie le 2026-09-27 : les OVH-FAI du 25 y sont, classes a la
        # main). Les notres s'empilaient depuis le 25 a la racine de
        # BAL TELECOM — 10 mails repris ce jour-la.
        "dossier": "BAL TELECOM/05 - OPERATEUR",
        "autorise": "reproduit le 2026-09-17 ; sous-dossier corrige le 2026-09-27",
    },
    {
        # L'ASTREINTE RESTE AVEC L'ASTREINTE. Le prestataire de permanence
        # releve la demande, un technicien DFM y repond : les deux doivent
        # se retrouver au meme endroit. Le 2026-09-26, la demande pour
        # Carrefour City Palaiseau est allee en 08 - ASTREINTE et la reponse
        # de l'astreinte en BAL SECURITE — parce qu'elle parlait d'un PC de
        # videosurveillance. Le sujet du mail prime ici sur son vocabulaire :
        # un fil d'astreinte coupe en deux ne raconte plus rien.
        # L'objet seul suffit, sans expediteur : la demande vient du
        # prestataire, les reponses de chez nous.
        "nom": "astreinte-dfm",
        "objet": re.compile(r"^\s*(re|tr|fw|fwd)?\s*:?\s*.{0,40}astreinte\s+dfm", re.I),
        "dossier": "BAL TELECOM/08 - ASTREINTE",
        "autorise": "range ainsi depuis 2024 ; fil reunifie le 2026-09-26",
    },
    {
        # Les journaux d'equipement reseau — routeurs DrayTek/Vigor, sondes,
        # pare-feux — vont au FIREWALL, toujours. Laisses a la lecture du
        # modele, deux alertes identiques du meme Vigor sont parties dans
        # deux dossiers differents le 2026-09-25 : « WAN1 Disconnected » en
        # FIREWALL, « WAN1 Reconnected » en INFORMATIONS. Une meme source
        # merite une meme destination, sinon personne ne peut s'y fier.
        "nom": "journal-equipement-reseau",
        "quand": lambda m: bool(
            RE_ALERTE_EQUIPEMENT.search(m.get("subject") or "")
            or corps_de_journal(((m.get("body") or {}).get("content")
                                 or m.get("bodyPreview") or "")[:3000])),
        "objet": re.compile(r".", re.S),
        "dossier": "firewall",
        "autorise": "regroupement des alertes d equipement (2026-09-25)",
    },
    {
        # Les alarmes INEO adressees a sav-abc@dfm.fr sont celles d'ABC
        # Securite. L'equipe les range a la main dans son sous-dossier —
        # 146 y sont deja — pendant que la regle generale les laissait
        # s'empiler dans BAL SECURITE, le dossier PARENT. Six pour la
        # seule journee du 2026-09-25. Un dossier parent n'est pas une
        # destination : c'est la ou les mails se perdent.
        "nom": "ineo-telesurveillance-abc",
        "expediteur": "mailauto@ineots.com",
        "destinataire": "sav-abc@dfm.fr",
        "objet": re.compile(r".", re.S),
        "dossier": "BAL SECURITE/14- SAV ABC",
        "autorise": "range ainsi par l equipe, 146 precedents (2026-09-25)",
    },
    {
        # Telesurveillance INEO : « ITS, site: 4297 ».
        "nom": "ineo-telesurveillance",
        "expediteur": "mailauto@ineots.com",
        "objet": re.compile(r".", re.S),
        "dossier": "securite",
        "autorise": "range ainsi le 2026-09-17, a corriger si besoin",
    },
    {
        # TOUT ce qui arrive sur sav-abc@dfm.fr appartient a l'equipe
        # ABC Securite et va dans SON dossier. Pas seulement les alarmes
        # automatiques : le courrier humain aussi — les mails de
        # Jean-Philippe Bernardi des 24 et 26 septembre 2026 y sont,
        # ranges a la main. Sans cette regle, le modele lit « securite »
        # et depose a la RACINE de BAL SECURITE, ou plus personne ne
        # regarde : le 2026-09-27 il a ainsi repris une demande TRES
        # URGENTE que je venais de classer correctement. L'adresse
        # destinataire est une preuve, la lecture du corps une opinion.
        "nom": "sav-abc-tout-courrier",
        "destinataire": "sav-abc@dfm.fr",
        "objet": re.compile(r".", re.S),
        "dossier": "BAL SECURITE/14- SAV ABC",
        "autorise": "pratique de l equipe, mesuree le 2026-09-27",
    },
    {
        # Cyclop adresse a sav-abc@dfm.fr, comme INEO : c'est la meme
        # telesurveillance, le meme client final, le meme sous-dossier. La
        # lecon ecrite pour INEO le 2026-09-25 n'avait pas ete tiree ici —
        # 70 alarmes Cyclop rangees par nous dans BAL SECURITE, le dossier
        # PARENT, quand l'equipe les mettait en 14- SAV ABC. La regle
        # generale ci-dessous garde sa place pour les autres destinataires.
        "nom": "cyclop-telesurveillance-abc",
        "expediteur": "cyclop-securite@cyclop.fr",
        "destinataire": "sav-abc@dfm.fr",
        "objet": re.compile(r".", re.S),
        "dossier": "BAL SECURITE/14- SAV ABC",
        "autorise": "range ainsi par l equipe ; corrige le 2026-09-27",
    },
    {
        # Alerte du NAS QNAP d'IMPRIM 2000, tous les soirs vers 21h20 :
        # « Finished Backup job with errors ». Treize soirs de suite en
        # 06 - SAUVEGARDE (mesure du 2026-09-27). Le mail part aussi a
        # Laurent Laborde et a MCO, qui l'ont donc deja sous les yeux ;
        # notre exemplaire se range.
        "nom": "qnap-alerte-nas",
        "expediteur": "orangesmtp@orange.fr",
        "objet": re.compile(r"^\s*ALERTE\s+NAS\b", re.I),
        "dossier": "sauvegarde",
        "autorise": "pratique de l equipe, mesuree le 2026-09-27",
    },
    {
        # Rapports Iperius Backup de BCSystem, plusieurs par jour et
        # toujours « terminee avec des erreurs » : quinze de suite ranges
        # en 06 - SAUVEGARDE (mesure du 2026-09-27). Le modele les lit un
        # par un et cherche un client dans un journal de sauvegarde qui
        # n'en nomme aucun : il en a classe un a 20h06 et laisse le
        # suivant en boite deux minutes plus tard, faute de « client
        # etabli ». Une sauvegarde se range, elle ne s'identifie pas.
        "nom": "iperius-rapport-sauvegarde",
        "expediteur": "scanbcs202301@bcsystemit.fr",
        "objet": re.compile(r"^\s*Sauvegarde\s+termin", re.I),
        "dossier": "sauvegarde",
        "autorise": "pratique de l equipe, mesuree le 2026-09-27",
    },
    {
        # Rapport quotidien de l'IDSBOX, l'appliance de sauvegarde de
        # GRAHAL. C'est une sauvegarde, et l'equipe le rangeait en
        # 06 - SAUVEGARDE treize jours de suite. Depuis que nous lisons
        # la boite, le modele le relit chaque matin et l'a envoye trois
        # fois en 04 - INFORMATIONS (25, 26 et 27 septembre 2026). Un
        # rapport qui revient tous les jours a la meme heure ne se
        # redecide pas tous les jours.
        "nom": "idsbox-rapport-quotidien",
        "expediteur": "support@proserve.fr",
        "objet": re.compile(r"^\s*\[idsbox\b", re.I),
        "dossier": "sauvegarde",
        "autorise": "pratique de l equipe, mesuree le 2026-09-27",
    },
    {
        # Rapport hebdomadaire SecuReporter de Zyxel : un bilan de securite
        # du routeur, a lire, jamais a traiter. Douze precedents depuis 2023
        # rangent en 04 - INFORMATIONS ; un seul a derape en BAL IMPRESSION
        # le 2026-08-02, lu par le modele. Une regle nommee coute moins cher
        # qu'une lecture hebdomadaire qui se trompe une fois sur six.
        "nom": "zyxel-secureporter",
        "expediteur": "do-not-reply@secureporter.cloudcnm.zyxel.com",
        "objet": re.compile(r".", re.S),
        "dossier": "informations",
        "autorise": "pratique constante depuis 2023, mesuree le 2026-09-27",
    },
    {
        # Telesurveillance Cyclop : « Anomalie chez un de vos clients ».
        "nom": "cyclop-telesurveillance",
        "expediteur": "cyclop-securite@cyclop.fr",
        "objet": re.compile(r".", re.S),
        "dossier": "securite",
        "autorise": "range ainsi le 2026-09-16, a corriger si besoin",
    },
    {
        # Centre de telesurveillance : absence de test d'un site. Adresse
        # a sav-abc@dfm.fr, comme INEO et Cyclop : onze mails mesures le
        # 2026-09-27, tous a cette adresse, dont sept ranges a la main en
        # 14- SAV ABC. Les trois qui trainent a la racine de BAL SECURITE
        # sont les notres, depuis le 25.
        "nom": "telesure-alarme",
        "expediteur": "no.reply@telesure.fr",
        "destinataire": "sav-abc@dfm.fr",
        "objet": re.compile(r".", re.S),
        "dossier": "BAL SECURITE/14- SAV ABC",
        "autorise": "pratique de l equipe ; corrige le 2026-09-27",
    },
    {
        # IPBX : tentatives d'enregistrement d'extension bloquees.
        "nom": "ipbx-extension-bloquee",
        "expediteur": "no-reply@txp.fr",
        "objet": re.compile(r".", re.S),
        "dossier": "telecom",
        "autorise": "range ainsi le 2026-09-16, a corriger si besoin",
    },
    {
        # Omnissa Workspace ONE : avancement des mises a jour SaaS.
        "nom": "omnissa-maj",
        "expediteur": "no_reply@omnissa.com",
        "objet": re.compile(r".", re.S),
        "dossier": "informations",
        "autorise": "reproduit le 2026-09-17, a corriger si besoin",
    },
    {
        # UniFi : « Network has been automatically updated ».
        "nom": "unifi-maj",
        "expediteur": "no-reply@notifications.ui.com",
        "objet": re.compile(r".", re.S),
        "dossier": "informations",
        "autorise": "reproduit le 2026-09-17, a corriger si besoin",
    },
    {
        # Facture Microsoft : comptabilite, pas support.
        "nom": "microsoft-facture",
        "expediteur": "microsoft-noreply@microsoft.com",
        "objet": re.compile(r".*facture\s+microsoft\s+est\s+pr[êe]te", re.I),
        "dossier": "informations",
        "autorise": "reproduit le 2026-09-17, a corriger si besoin",
    },
    {
        # Alertes de la console antivirus. Elles nomment le client, mais elles
        # appartiennent a l'equipe qui tient la console, pas a la hotline —
        # meme logique que les alertes firewall rangees en 07 le 2026-09-15.
        "nom": "sophos-central-alerte",
        "expediteur": "do-not-reply@central.sophos.com",
        "objet": re.compile(r"^\s*\[.*\]\s*alerte?\s+pour\s+sophos\s+central", re.I),
        "dossier": "antivirus",
        "autorise": "reproduit le 2026-09-17, a corriger si besoin",
    },
    {
        # Alertes des boitiers NAS (QNAP, Synology, Hybrid Backup Sync). Elliot,
        # 2026-09-16 : « alerte NAS tu connais, a deplacer dans 06 SAUVEGARDE ».
        "nom": "alerte-nas",
        "expediteur": None,
        "objet": re.compile(
            r"^\s*((re|tr|fw|fwd)\s*:\s*)*(alerte?\s+nas\b|\[?(qnap|synology)\]?"
            r"|.*\bnas\b.*(alerte|warning|error|failed))", re.I),
        "dossier": "sauvegarde",
        "autorise": "Elliot, 2026-09-16",
    },
    {
        # Accuse de reception renvoye par Artis, ou notre propre copie
        # sortante : le ticket existe, l'objet le dit. Rien a decider.
        "nom": "accuse-reception-dit",
        "expediteur": None,
        "objet": re.compile(
            r"^\s*((re|tr|très|fw|fwd)\s*:\s*)*\[?(DIT|IT)\s*\d{8,11}\]?"
            r"\s*(P-\d+)?\s*[-:]?\s*Confirmation\s+(de\s+)?"
            r"(r[ée]ception|demande)", re.I),
        "dossier": "traite",
        "autorise": "Elliot, 2026-09-15",
    },
    {
        # NOTRE propre copie sortante : objet reduit au numero de ticket. On
        # exige l'expediteur support@dfm.fr — sans ca, la regle attraperait
        # aussi « Tr: [IT 10014795801] » envoye par un CLIENT, qui doit partir
        # chez le dernier intervenant, pas etre range comme traite.
        "nom": "copie-sortante-support",
        "expediteur": "support@dfm.fr",
        "objet": re.compile(
            r"^\s*((re|tr|fw|fwd)\s*:\s*)*\[?(DIT|IT)\s*\d{8,11}\]?\s*$", re.I),
        "dossier": "traite",
        "autorise": "Elliot, 2026-09-15",
    },
    {
        # Notifications Microsoft sur les relations d'administration deleguee.
        "nom": "microsoft-relation-admin",
        "expediteur": None,
        "objet": re.compile(
            r"^\s*((re|tr|fw|fwd)\s*:\s*)*(the customer approved your granular"
            r"|you have a granular admin relationship"
            r"|granular admin relationship (request|with))", re.I),
        "dossier": "informations",
        "autorise": "Elliot, 2026-09-15",
    },
    {
        # Une absence du bureau n'apprend rien a personne et encombre la file.
        # Elliot (2026-09-14) : « mets les reponses automatiques recues dans la
        # corbeille ». Pas d'expediteur impose ici : il change a chaque fois,
        # mais l'objet est sans ambiguite — la locution est en tete de sujet.
        "nom": "reponse-automatique-absence",
        "expediteur": None,
        "objet": re.compile(
            r"^\s*(r[ée]ponse\s+automatique|automatic\s+reply|out\s+of\s+office"
            r"|absence\s+du\s+bureau)\b", re.I),
        "dossier": "corbeille",
        "autorise": "Elliot, 2026-09-14",
    },
    {
        # Sophos Central annonce l'activation d'une fonction sur un firewall :
        # information d'equipement, aucune demande derriere. Elliot, 2026-09-15
        # (P-0279, P-0280) : « pas de ticket a creer, mail a deplacer dans
        # 07 - FIREWALL ».
        "nom": "sophos-firewall-information",
        "expediteur": None,
        "objet": re.compile(
            r"^\s*(enabled\s+management\s+from\s+sophos\s+central"
            r"|firewall\s+reporting\s+turned\s+(on|off)"
            r"|sophos\s+firewall.{0,40}(registered|activated))", re.I),
        "dossier": "firewall",
        "autorise": "Elliot, 2026-09-15",
    },
    {
        "nom": "defender-nouvelles-vulnerabilites",
        "expediteur": "defender-noreply@microsoft.com",
        "objet": re.compile(
            r"^\s*new vulnerabilities notification from microsoft defender", re.I),
        "dossier": "informations",
        "autorise": "Elliot, 2026-09-14",
    },
]

JOURNAL_AUTO = DATA_DIR / "mail_dit_rangements.json"



# Un ticket ouvert depuis le portail Datto RMM n'est pas une alerte : c'est une
# DEMANDE d'utilisateur, relayee par l'agent installe sur son poste. Elle porte
# son site (« Site: 30004 - CLIMAT SYSTEMS »), son titre et sa description. Le
# 2026-09-25, « probleme sur application ici le programme se ferme tout seul »
# a ete range en INFORMATIONS parce que l'expediteur ressemble a un robot : la
# demande y serait restee sans reponse. Le corps, lui, ne trompe pas.
RE_TICKET_RMM = re.compile(
    r"ticket\s+(number|title)\s*:|description\s*:\s*\S", re.I)


def demande_relayee_par_rmm(m):
    """Vrai si ce mail EST une demande d'utilisateur remontee par le RMM."""
    exp = (addr(m) or "").lower()
    if "rmm.datto.com" not in exp and "@datto" not in exp:
        return False
    corps = ((m.get("body") or {}).get("content")
             or m.get("bodyPreview") or "")[:3000]
    return bool(re.search(r"description\s*:\s*\S", corps, re.I)
                and re.search(r"\bsite\s*:\s*\d{3,8}\b", corps, re.I))



def _regle_de_rangement(m, subj, exp):
    """La regle nommee qui s'applique a ce mail, ou None.

    Les regles de RANGEMENTS_AUTO designent un expediteur, parfois un
    destinataire, et une destination : elles sont exactes et reproductibles.
    On s'en sert AVANT la lecture par le modele, qui devine.
    """
    dests = " ".join(
        ((x.get("emailAddress") or {}).get("address") or "").lower()
        for x in ((m.get("toRecipients") or []) + (m.get("ccRecipients") or [])))
    for regle in RANGEMENTS_AUTO:
        if regle.get("expediteur") and exp != regle["expediteur"]:
            continue
        if (regle.get("destinataire")
                and regle["destinataire"].lower() not in dests):
            continue
        if regle.get("quand"):
            try:
                if not regle["quand"](m):
                    continue
            except Exception:
                continue
        elif not regle["objet"].match(subj):
            continue
        return regle
    return None


def _ranger_automatiquement(tok, msgs, log=None):
    """Range les familles autorisées, sans passer par la file de validation.

    Ces mails ne produisent même pas de proposition : ils sont classés
    `ignore` (expéditeur automatique) et resteraient donc indéfiniment dans la
    boîte. C'est exactement ce qu'Elliot a demandé d'automatiser en premier.

    Trois garde-fous : le drapeau d'écriture doit être armé, l'expéditeur ET
    l'objet doivent correspondre, et chaque déplacement est journalisé avec la
    règle qui l'a décidé.

    Rend les IDENTIFIANTS des mails deplaces, pour que l'appelant les retire de
    sa liste. Cette variable avait ete initialisee dans la MAUVAISE fonction le
    2026-09-18 : le rangement marchait, mais la fonction levait un NameError a
    la fin, l'exception partait au log, et les mails ranges restaient dans la
    liste du cycle — exactement le defaut qui avait cree le DIT 100174371.
    """
    dire = log or (lambda *_a: None)
    deplaces_ids = set()
    try:
        import mail_dit_ecriture as E
        if not E.ecriture_armee():
            return deplaces_ids
    except Exception:
        return deplaces_ids

    faits = []
    for m in msgs:
        exp = addr(m)
        subj = m.get("subject") or ""

        # UNE REPONSE SUR UN TICKET OUVERT APPARTIENT D'ABORD A SON
        # INTERVENANT. Le classement par metier s'execute AVANT le passage
        # des suivis : sans cette reserve, la reponse d'un client part dans
        # une boite metier et celui qui traite le ticket ne la voit jamais.
        # Le 2026-09-25, SEGETEX a repondu sur la DIT 100175420 (salle
        # serveurs en surchauffe, intervention a planifier au nom de Bilal
        # MESROUR) : le mail est parti dans les interventions terrain.
        # On rend donc la main au passage des suivis, qui sait a qui
        # l'envoyer — et qui relaie a Clifford s'il n'y a personne.
        _cite = RE_DIT.search(subj)
        if _cite and not etat_si_clos(_cite.group(1)):
            continue

        # Autre METIER : securite ou telephonie. Range dans la BAL de l'equipe,
        # qui la traite — au lieu de rester ici sans destinataire.
        dest = [((x.get("emailAddress") or {}).get("address") or "").lower()
                for x in ((m.get("toRecipients") or [])
                          + (m.get("ccRecipients") or []))]
        texte = subj + "\n" + body_utile(
            (m.get("body") or {}).get("content")
            or m.get("bodyPreview") or "")[:1500]
        # L'ORDRE, tel qu'Elliot l'a pose le 2026-09-22 : « c'est TOI, une IA
        # via cle API, qui analyses et traites ; le script est la pour
        # REMPLACER l'IA quand il n'y a plus de credit ». Ce qui precede reste
        # devant parce que c'est exact et gratuit — les familles d'alertes
        # reconnues, et le fil deja range chez un metier, qui est une preuve.
        # Tout le reste passe par la LECTURE, et les listes de mots-cles ne
        # servent plus que de secours.
        # 1. Les DESTINATAIRES : gratuit, exact, insensible au vocabulaire.
        # Une demande d'utilisateur remontee par le RMM ne se range pas :
        # elle doit suivre le chemin du ticket.
        if demande_relayee_par_rmm(m):
            dire("[mail-dit] %s : demande remontee par le RMM, on la laisse "
                 "devenir un ticket" % subj[:46])
            continue
        # UNE REGLE NOMMEE PASSE AVANT UNE LECTURE. Le 2026-09-25, deux
        # alertes identiques du meme routeur Vigor sont parties dans deux
        # dossiers differents — l'une en INFORMATIONS, l'autre en FIREWALL —
        # parce que le modele les lisait chacune de son cote. Et la regle
        # « ineo-telesurveillance-abc » ne servait jamais : la lecture rangeait
        # l'alarme dans BAL SECURITE, le dossier PARENT, avant qu'elle ne soit
        # consultee. Une regle exacte ne se discute pas ; on lui laisse la main.
        _regle_nommee = _regle_de_rangement(m, subj, exp)
        if _regle_nommee:
            metier, pourquoi = None, ""
        else:
            metier, pourquoi = metier_du_destinataire(dest, expediteur=exp, texte=texte)
        # DEUXIEME VERROU CONTRE LE RENVOI EN BOUCLE. Si notre propre phrase de
        # transfert figure dans le fil, ce mail a DEJA ete renvoye a ce service
        # une fois : ce qui revient n'est pas un mail mal adresse, c'est une
        # reponse. Le renvoyer une seconde fois fait tourner le dossier entre
        # deux services pendant que le client attend (BSA 4048, 2026-09-24).
        if metier and PHRASE_RELAIS in ((m.get("body") or {}).get("content")
                                        or m.get("bodyPreview") or ""):
            dire("[mail-dit] %s : deja transfere a %s dans ce fil - on garde"
                 % (subj[:40], metier))
            metier, pourquoi = None, ""
        # 2. Le fil : une preuve, gratuite.
        if not metier and m.get("conversationId"):
            metier = dossier_metier_du_fil(
                {"conversation": m.get("conversationId")}, tok=tok)
            if metier:
                pourquoi = "ce fil est deja range en %s" % metier
        # 3. La lecture, par le modele.
        lu_par_modele = False
        if not metier:
            metier = metier_par_modele(m, texte, log=dire)
            if metier:
                pourquoi = "lu par le modele : %s" % metier
                lu_par_modele = True
        # 4. SECOURS seulement : les mots-cles, quand le modele n'a pas pu
        #    repondre (credit epuise, cle absente, panne). Ils ne decident
        #    plus a sa place, ils prennent le relais.
        if not metier and not modele_disponible():
            metier, pourquoi = _metier_du_mail(m, texte, dest)
            if metier:
                pourquoi = "(secours, sans modele) " + pourquoi
        # 5. Une REPONSE partie de chez nous vers le client : le support n'en
        #    a qu'une copie. Ce n'est pas une demande qui arrive, c'est un
        #    echange en cours. Le 2026-09-23, « Re: FARGO INVEST - TEAMS »
        #    (Emmanuel MOUELLE vers la cliente) a ete classe « suivi » — donc
        #    aucune proposition — puis plus rien ne s'en occupait, faute de
        #    numero de ticket cite. Quatre conditions, toutes necessaires :
        #    l'expediteur est des notres, un destinataire au moins est
        #    externe, l'objet est une reponse, et aucun ticket n'est cite.
        if not metier and est_interne(exp) and RE_REPONSE.match(subj)                 and not RE_DIT.search(subj)                 and any(x and not est_interne(x) for x in dest):
            dest_id = E.id_dossier(tok, E.DOSSIERS["traite"])
            if dest_id:
                try:
                    r = requests.post("%s/me/messages/%s/move" % (GRAPH, m["id"]),
                                      headers={"Authorization": "Bearer " + tok,
                                               "Content-Type": "application/json"},
                                      data=json.dumps({"destinationId": dest_id}),
                                      timeout=40)
                    ok = r.status_code in (200, 201)
                except Exception as e:
                    ok, r = False, None
                    dire("[mail-dit] reponse sortante : %s" % str(e)[:120])
                faits.append({
                    "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                    "regle": "reponse-sortante-vers-le-client",
                    "autorise": "Elliot, 2026-09-22",
                    "de": exp, "objet": subj[:140],
                    "dossier": E.DOSSIERS["traite"],
                    "recu": m.get("receivedDateTime"),
                    "pourquoi": "reponse d'un collegue dans un echange en cours",
                    "ok": bool(ok), "http": getattr(r, "status_code", None)})
                dire("[mail-dit] %s -> 01 DIT ARTIS (reponse sortante)" % subj[:46])
                continue

        # 6. Un echange interne n'est classe « traite » que si personne
        #    d'autre ne le reclame.
        ei = None if metier else echange_interne_traite(m, texte)
        if ei:
            dest_id = E.id_dossier(tok, E.DOSSIERS["traite"])
            if dest_id:
                try:
                    requests.patch("%s/me/messages/%s" % (GRAPH, m["id"]),
                                   headers={"Authorization": "Bearer " + tok,
                                            "Content-Type": "application/json"},
                                   data=json.dumps({"isRead": True}), timeout=30)
                    r = requests.post("%s/me/messages/%s/move" % (GRAPH, m["id"]),
                                      headers={"Authorization": "Bearer " + tok,
                                               "Content-Type": "application/json"},
                                      data=json.dumps({"destinationId": dest_id}),
                                      timeout=40)
                    ok = r.status_code in (200, 201)
                except Exception as e:
                    ok, r = False, None
                    dire("[mail-dit] echange interne : %s" % str(e)[:120])
                faits.append({
                    "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                    "regle": "echange-interne-deja-traite",
                    "autorise": "Elliot, 2026-09-15",
                    "de": exp, "objet": subj[:140],
                    "dossier": E.DOSSIERS["traite"],
                    "recu": m.get("receivedDateTime"),
                    "pourquoi": ei["pourquoi"],
                    "ok": bool(ok), "http": getattr(r, "status_code", None)})
                dire("[mail-dit] %s -> 01 DIT ARTIS (%s)" % (subj[:46], ei["pourquoi"]))
                continue

        # La regle nommee reste maitresse JUSQU'AU BOUT. Le fil deja range
        # et la lecture du modele, plus haut, reposent `metier` : sans ce
        # rappel la priorite ne tenait que sur la premiere determination,
        # et l'alerte Cyclop du 2026-09-25 est partie « lue par le modele »
        # alors qu'une regle nommee la designait.
        if _regle_nommee:
            metier, pourquoi = None, ""

        if metier:
            dossier = E.DOSSIERS.get(metier, metier)
            # Certains métiers veulent le mail dans leur boîte ET dans leur
            # messagerie : ranger seul ne prévient personne. Le transfert part
            # AVANT le déplacement, qui change l'identifiant du message.
            relais = RELAIS_METIER.get(metier)
            if relais and est_interne(relais):
                try:
                    f = requests.post(
                        "%s/me/messages/%s/createForward" % (GRAPH, m["id"]),
                        headers={"Authorization": "Bearer " + tok,
                                 "Content-Type": "application/json"},
                        data=json.dumps({
                            "comment": "Bonjour,\n\n%s (%s) : je vous "
                                       "le transmets.\n"
                                       % (PHRASE_RELAIS, pourquoi),
                            "toRecipients": [
                                {"emailAddress": {"address": relais}}]}),
                        timeout=60)
                    if f.status_code in (200, 201):
                        requests.post("%s/me/messages/%s/send"
                                      % (GRAPH, (f.json() or {}).get("id")),
                                      headers={"Authorization": "Bearer " + tok},
                                      timeout=60)
                        dire("[mail-dit] %s transmis a %s" % (subj[:40], relais))
                except Exception as e:
                    dire("[mail-dit] relais %s : %s" % (relais, str(e)[:100]))
            dest_id = E.id_dossier(tok, dossier)
            if dest_id:
                try:
                    requests.patch("%s/me/messages/%s" % (GRAPH, m["id"]),
                                   headers={"Authorization": "Bearer " + tok,
                                            "Content-Type": "application/json"},
                                   data=json.dumps({"isRead": True}), timeout=30)
                    r = requests.post("%s/me/messages/%s/move" % (GRAPH, m["id"]),
                                      headers={"Authorization": "Bearer " + tok,
                                               "Content-Type": "application/json"},
                                      data=json.dumps({"destinationId": dest_id}),
                                      timeout=40)
                    ok = r.status_code in (200, 201)
                except Exception as e:
                    ok, r = False, None
                    dire("[mail-dit] rangement metier : %s" % str(e)[:120])
                faits.append({
                    "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                    "regle": "metier-" + metier, "autorise": "Elliot, 2026-09-15",
                    "de": exp, "objet": subj[:140], "dossier": dossier,
                    "recu": m.get("receivedDateTime"), "pourquoi": pourquoi,
                    "ok": bool(ok), "http": getattr(r, "status_code", None)})
                _journal_auto({
                    "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                    "message_id": m.get("internetMessageId"), "proposition": "",
                    "action": "ranger", "cible": metier, "objet": subj[:140],
                    "de": exp, "execute": bool(ok),
                    "resultat": dossier if ok else "échec HTTP %s"
                                                   % getattr(r, "status_code", "?"),
                    "pourquoi": pourquoi, "raison": pourquoi})
                if ok:
                    deplaces_ids.add(m.get("id"))
                dire("[mail-dit] %s -> %s (%s)" % (subj[:50], dossier, pourquoi))
                continue

        for regle in RANGEMENTS_AUTO:
            # `expediteur` a None : la regle ne tient qu'a l'objet. Reserve aux
            # libelles sans ambiguite (« Reponse automatique : ... »).
            if regle.get("expediteur") and exp != regle["expediteur"]:
                continue
            # `quand` : une regle qui ne se lit pas dans l'objet (un envoi de
            # masse se reconnait au pied de page, pas au sujet).
            # Une regle peut viser un DESTINATAIRE precis : c'est lui qui
            # distingue deux flux venus du meme expediteur.
            if regle.get("destinataire"):
                _dests = " ".join(
                    ((x.get("emailAddress") or {}).get("address") or "").lower()
                    for x in ((m.get("toRecipients") or [])
                              + (m.get("ccRecipients") or [])))
                if regle["destinataire"].lower() not in _dests:
                    continue
            if regle.get("quand"):
                if not regle["quand"](m):
                    continue
            elif not regle["objet"].match(subj):
                continue
            dossier = E.DOSSIERS.get(regle["dossier"], regle["dossier"])
            dest = E.id_dossier(tok, dossier)
            if not dest:
                dire("[mail-dit] rangement auto : dossier %s introuvable" % dossier)
                break
            try:
                requests.patch("%s/me/messages/%s" % (GRAPH, m["id"]),
                               headers={"Authorization": "Bearer " + tok,
                                        "Content-Type": "application/json"},
                               data=json.dumps({"isRead": True}), timeout=30)
                r = requests.post("%s/me/messages/%s/move" % (GRAPH, m["id"]),
                                  headers={"Authorization": "Bearer " + tok,
                                           "Content-Type": "application/json"},
                                  data=json.dumps({"destinationId": dest}),
                                  timeout=40)
                ok = r.status_code in (200, 201)
            except Exception as e:
                ok, r = False, None
                dire("[mail-dit] rangement auto : %s" % str(e)[:120])
            faits.append({
                "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                "regle": regle["nom"], "autorise": regle["autorise"],
                "de": exp, "objet": subj[:140], "dossier": dossier,
                "recu": m.get("receivedDateTime"),
                "ok": bool(ok), "http": getattr(r, "status_code", None)})
            # Et dans le journal que lit Elliot : sans cette ligne, le mail
            # disparaissait de la boite sans que rien ne dise OU il etait
            # parti (82 rangements sur 85 dans ce cas le 2026-09-17).
            pourquoi = "règle automatique « %s »" % regle["nom"]
            _journal_auto({
                "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                "message_id": m.get("internetMessageId"), "proposition": "",
                "action": "ranger", "cible": regle["dossier"],
                "objet": subj[:140], "de": exp, "execute": bool(ok),
                "resultat": dossier if ok else "échec HTTP %s"
                                               % getattr(r, "status_code", "?"),
                "pourquoi": pourquoi, "raison": pourquoi})
            if ok:
                deplaces_ids.add(m.get("id"))
            dire("[mail-dit] range automatiquement dans %s : %s"
                 % (dossier, subj[:70]))
            break

    if faits:
        try:
            j = []
            if JOURNAL_AUTO.exists():
                j = json.loads(JOURNAL_AUTO.read_text(encoding="utf-8"))
            j.extend(faits)
            JOURNAL_AUTO.write_text(json.dumps(j[-1000:], ensure_ascii=False,
                                               indent=1), encoding="utf-8")
        except Exception as e:
            dire("[mail-dit] journal des rangements : %s" % str(e)[:120])
    # On rend les IDENTIFIANTS des mails deplaces, pas leur nombre : l'appelant
    # doit pouvoir les retirer de sa liste. Avec un simple compteur il les
    # reconstituait a tatons, et le filtre etait faux pour les rangements
    # metier — d'ou le DIT 100174371, cree sur un mail deja parti en
    # BAL TELECOM (Elliot, 2026-09-17).
    return deplaces_ids



# Toute la correspondance qui part de support@dfm.fr est signee ainsi, jamais
# du nom d'Elliot : le mail part de la boite du support, et celui qui le recoit
# doit voir tout de suite que c'est le robot qui ecrit (Elliot, 2026-10-01). La
# regle etait dans le skill depuis ce jour-la, mais nulle part dans le code :
# les transferts partaient sans signature (constate le 2026-10-02).
SIGNATURE_MCO = chr(10) + chr(10) + "Service MCO - Mail automatique" + chr(10)


def _mot_du_transfert(dit, iv):
    """Le texte qui accompagne le transfert d'une reponse client.

    Trois situations, trois messages — les confondre fait perdre du temps a
    celui qui recoit le mail :

      1. l'intervenant du ticket : on le previent, il continue ;
      2. un intervenant existe mais on n'a pas son adresse : Clifford fait
         suivre ;
      3. AUCUN intervenant n'est affecte : Clifford ne fait pas suivre, il
         REDISPATCHE. Elliot, 2026-09-25 : « la tu mets "tu es le dernier
         intervenant", il faudrait lui dire qu'il n'y a aucun intervenant et
         qu'il doit redispatcher le ticket ».
    """
    saut = chr(10)
    if not iv.get("relais"):
        return ("Bonjour,%s%sRéponse du client sur le ticket %s, dont tu es le "
                "dernier intervenant.%s" % (saut, saut, dit, saut)) + SIGNATURE_MCO
    if iv.get("sans_intervenant"):
        return ("Bonjour,%s%sRéponse du client sur le ticket %s.%s%s"
                "Ce ticket n'a AUCUN intervenant affecté : personne ne le suit "
                "actuellement. Je te le transmets pour que tu le redispatches "
                "à la bonne personne.%s" % (saut, saut, dit, saut, saut, saut)
                ) + SIGNATURE_MCO
    return ("Bonjour,%s%sRéponse du client sur le ticket %s.%s%s%s%s%s"
            "Je te la transmets pour que tu la fasses suivre à l'intervenant "
            "concerné.%s" % (saut, saut, dit, saut, saut,
                             iv.get("raison") or
                             "l'intervenant du ticket est injoignable",
                             saut, saut, saut)) + SIGNATURE_MCO



# ───────────────────────────────── suivi d'un ticket -> dernier intervenant
# Elliot (2026-09-14) : « s'il y a une reponse sur un ticket, je veux qu'il soit
# transfere au dernier intervenant de la DIT ».
#
# Tant que ce drapeau est a False, on ECRIT la decision sans l'executer : le
# jeton n'a pas encore Mail.Send (verifie : createForward 201, send 403), et
# surtout la decision doit se lire avant de partir toute seule.
AUTO_TRANSFERT_SUIVI = True     # arme par Elliot le 2026-09-15
JOURNAL_SUIVIS = DATA_DIR / "mail_dit_suivis.json"


def est_interne(adresse):
    """Règle absolue d'Elliot (2026-09-14) : on ne transfère QU'EN INTERNE.

    Un transfert sortant depuis la boîte du support enverrait à un tiers le
    mail d'un client — avec son historique, ses adresses et ses pièces. Aucun
    cas de figure ne le justifie, donc aucun chemin de code ne le permet : les
    deux endroits qui transfèrent passent par ici.
    """
    a = (adresse or "").strip().lower()
    return "@" in a and a.split("@")[-1] in INTERNE

# Absence du bureau : ce n'est pas une reponse du client.
RE_AUTO_REPONSE = re.compile(
    r"^\s*(r[ée]ponse\s+automatique|automatic\s+reply|out\s+of\s+office"
    r"|absence\s+du\s+bureau|auto\s*:)", re.I)


def _dit_par_le_client(m, subj):
    """La DIT d'une reponse qui ne cite AUCUN numero.

    Beaucoup de clients repondent sur un fil sans jamais reprendre la
    reference : « RE: **URGENT** / 7 ARCHITECTES Code client 14664 ». Le code
    client, lui, est souvent la. On cherche donc chez ce client une DIT dont
    l'OBJET correspond — c'est le signal qui a fait ses preuves sur les
    echanges internes, le corps d'une reponse ne ressemblant jamais a la
    demande d'origine.

    Deux exigences, pour ne pas transferer au hasard : une correspondance
    d'objet forte (>= 70 %), et une DIT qui n'est pas annulee. Sans ca, on
    rend la main — le 2026-09-15, se rabattre sur « la seule DIT ouverte du
    client » a envoye deux mails chez des collegues sans rapport.
    """
    lib = _libelles_clients()
    code, _ = code_client_du_texte(subj, "", lib)
    if not code:
        code = next((x for x in re.findall(r"\b\d{4,8}\b", subj) if x in lib), None)
    # Le code client n'est presque jamais dans l'objet. L'ADRESSE de
    # l'expediteur, elle, le donne toujours. Le 2026-09-25, « RE: URGENT :
    # Problemes dispositif visio-conference » d'OPQIBI portait sur la DIT
    # 100174895 ouverte pour ce meme client : faute de code dans l'objet,
    # personne n'a ete prevenu, et le client a relance « je n'ai eu aucun
    # retour » pendant que son mail dormait en boite.
    if not code:
        try:
            code, _pid, _meth = trouver_client(m, _index_clients())
        except Exception:
            code = None
    if not code:
        return None
    try:
        proches = dits_proches({"client": code, "objet": subj,
                                "recu": m.get("receivedDateTime"),
                                "commentaire": ""}, jours=25)
    except Exception:
        return None
    for d in proches:
        if d.get("etat") != "Annulee" and d.get("ressemblance_objet", 0) >= 0.7:
            return d["numero"]
    return None


def _suivis_de_ticket(tok, msgs, log=None):
    """Une reponse sur un ticket existant : a qui doit-elle aller ?

    On ne s'interesse qu'aux mails VENUS DE L'EXTERIEUR dont l'objet cite un
    numero de DIT connu. Le destinataire est le dernier technicien affecte sur
    cette DIT — s'il n'y en a pas, on ne transfere rien : l'affectation n'a pas
    encore eu lieu, il n'y a personne a prevenir.
    """
    dire = log or (lambda *_a: None)
    try:
        import mail_dit_ecriture as E
    except Exception:
        return 0
    connus = _num_dits()
    if not connus:
        return 0
    # On ne saute QUE ce qui est parti. Avant, toute ligne deja journalisee
    # etait ignoree — y compris les « decision seule » ecrites quand le
    # transfert n'etait pas encore arme. Resultat : 39 reponses clients
    # stagnaient dans la boite, jamais rejouees (constate le 2026-09-15, la
    # boite etait montee a 86 mails).
    deja = {x.get("message_id") for x in _journal_suivis() if x.get("execute")}

    faits = []
    for m in msgs:
        mid = m.get("internetMessageId")
        if not mid or mid in deja:
            continue
        exp = addr(m)
        if not exp:
            continue
        # Un mail INTERNE qui cite un numero de ticket est un suivi, lui aussi :
        # un collegue qui repond sur « [IT 10017166103] » doit arriver chez
        # celui qui traite le ticket. La version precedente les jetait tous, et
        # ces mails restaient en boite sans raison affichee (Elliot,
        # 2026-09-18). On ne garde donc que ceux qui citent un ticket.
        if exp.split("@")[-1] in INTERNE and not RE_DIT.search(m.get("subject") or ""):
            continue
        subj = m.get("subject") or ""
        # Une absence du bureau n'est pas une reponse du client : la
        # transferer au technicien ne lui apprend rien.
        if RE_AUTO_REPONSE.match(subj):
            continue
        nums = [n for n in RE_DIT.findall(subj) if n in connus]
        if not nums:
            # le numero d'INTERVENTION porte celui de la DIT + 2 chiffres
            nums = [n[:-2] for n in RE_DIT.findall(subj) if n[:-2] in connus]
        if not nums:
            # La liste locale des numeros ne date que du cycle de 20 h : un
            # ticket ouvert aujourd'hui par un COLLEGUE n'y figure pas, et son
            # suivi restait ignore (« URGENT TR: [IT 10017452301] », 18/09).
            # On demande alors a Artis, qui sait, lui.
            for n in RE_DIT.findall(subj):
                for cand in (n, n[:-2]):
                    # `relire_dit` ne rend PAS le numero : elle rend l'etat, la
                    # date, l'intervention. Tester `numero` revenait a declarer
                    # tout ticket inconnu — le DIT 100174523 existait bel et
                    # bien (2026-09-18).
                    if len(cand) >= 8:
                        d = E.relire_dit(cand) or {}
                        if d.get("etat") and not d.get("erreur"):
                            nums = [cand]
                            break
                if nums:
                    break
        dit = nums[0] if nums else _dit_par_le_client(m, subj)
        if not dit:
            continue
        # UN TICKET CLOS N'A PLUS DE SUITE. Le client repond sur un vieux fil
        # parce que c'est le mail qu'il a sous la main — et le sujet porte
        # encore « [IT ...] ». Le prendre pour un suivi revient a renvoyer une
        # demande NEUVE au dernier intervenant d'une affaire reglee il y a des
        # mois, et a ne creer aucun ticket.
        # Le 2026-09-24, DIRECT IMPRESSION a ecrit « nous n'avons plus d'acces
        # a nos serveurs, intervention en urgence » sur le fil d'une DIT
        # cloturee en FEVRIER : quatre transferts a Denis TULGA en une heure,
        # aucun ticket, et Shannon a du ecrire « il faudrait creer un ticket
        # urgent pour ce client, svp » — sans que personne ne le lise.
        # Quand le ticket cite est clos, on ne transfere pas : le mail suit le
        # chemin normal et sera lu comme une demande.
        try:
            etat_dit = (E.relire_dit(dit) or {}).get("etat") or ""
        except Exception:
            etat_dit = ""
        if etat_dit and etat_dit not in ETATS_OUVERTS:
            faits.append({
                "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                "message_id": mid, "de": exp, "objet": subj[:140],
                "recu": m.get("receivedDateTime"), "dit": dit,
                "intervenant": None, "nom": None, "intervention": None,
                "raison": "DIT %s est %s : ce n'est pas un suivi, le mail "
                          "repart en demande" % (dit, etat_dit),
                "execute": False})
            dire("[mail-dit] %s : DIT %s %s — traite comme une demande neuve"
                 % (subj[:40], dit, etat_dit))
            continue
        try:
            iv = E.dernier_intervenant(dit)
        except Exception as e:
            iv = {"erreur": str(e)[:150]}
        ligne = {
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "message_id": mid, "de": exp, "objet": subj[:140],
            "recu": m.get("receivedDateTime"), "dit": dit,
            "intervenant": iv.get("mail"), "nom": iv.get("nom"),
            "intervention": iv.get("intervention"),
            "raison": iv.get("raison") or iv.get("erreur") or "",
            "execute": False,
        }
        # Verrou : jamais de transfert vers l'exterieur, meme si la fiche Artis
        # de l'intervenant porte une adresse externe.
        if iv.get("mail") and not est_interne(iv["mail"]):
            ligne["raison"] = ("adresse non interne (%s) : transfert refuse"
                               % iv["mail"])
            ligne["intervenant"] = None
        # Ne jamais renvoyer un mail a celui qui vient de l'ecrire. Un collegue
        # qui rend compte sur SON ticket est a la fois l'expediteur et le
        # dernier intervenant : le transfert ne lui apprend rien (constate le
        # 2026-09-16 sur le DIT 100173954, renvoye a Mohamed SOKHONA).
        if ligne.get("intervenant") and ligne["intervenant"].lower() == exp.lower():
            ligne["raison"] = ("l'expediteur est lui-meme le dernier "
                               "intervenant : rien a transferer")
            ligne["intervenant"] = None
        if ligne.get("intervenant") and AUTO_TRANSFERT_SUIVI and E.ecriture_armee():
            try:
                r = requests.post(
                    "%s/me/messages/%s/createForward" % (GRAPH, m["id"]),
                    headers={"Authorization": "Bearer " + tok,
                             "Content-Type": "application/json"},
                    data=json.dumps({
                        # Le mot change quand c'est un relais : annoncer à
                        # Clifford qu'il est « le dernier intervenant » d'un
                        # ticket où personne n'est affecté serait faux.
                        "comment": _mot_du_transfert(dit, iv),
                        "toRecipients": [{"emailAddress": {"address": iv["mail"]}}]}),
                    timeout=60)
                if r.status_code in (200, 201):
                    bid = (r.json() or {}).get("id")
                    e2 = requests.post("%s/me/messages/%s/send" % (GRAPH, bid),
                                       headers={"Authorization": "Bearer " + tok},
                                       timeout=60)
                    ligne["execute"] = e2.status_code in (200, 202, 204)
                    ligne["http"] = e2.status_code
                    # Rangement APRES le transfert, et seulement s'il est
                    # parti : sinon la reponse du client disparaitrait de la
                    # boite sans que personne ne l'ait recue.
                    if ligne["execute"]:
                        dest = E.id_dossier(tok, E.DOSSIERS["traite"])
                        if dest:
                            requests.patch(
                                "%s/me/messages/%s" % (GRAPH, m["id"]),
                                headers={"Authorization": "Bearer " + tok,
                                         "Content-Type": "application/json"},
                                data=json.dumps({"isRead": True}), timeout=30)
                            rr = requests.post(
                                "%s/me/messages/%s/move" % (GRAPH, m["id"]),
                                headers={"Authorization": "Bearer " + tok,
                                         "Content-Type": "application/json"},
                                data=json.dumps({"destinationId": dest}),
                                timeout=40)
                            ligne["range"] = rr.status_code in (200, 201)
                            ligne["dossier"] = DOSSIER_TRAITE
                else:
                    ligne["http"] = r.status_code
            except Exception as e:
                ligne["raison"] = str(e)[:150]
        # Personne à qui transmettre parce que l'expéditeur EST le dernier
        # intervenant : il sait déjà, mais son mail restait en boîte pour
        # toujours (Elliot, 2026-09-18 : « pourquoi je dois toujours te
        # rappeler pour que tu agisses »). Un « non » doit toujours avoir une
        # suite — ici, le rangement avec le ticket.
        if (not ligne["execute"] and not ligne.get("intervenant")
                and AUTO_TRANSFERT_SUIVI and E.ecriture_armee()):
            r = E.ranger_mail(m["id"], "traite", confirmer=True)
            if r.get("envoye"):
                ligne["execute"] = True
                ligne["raison"] = ((ligne.get("raison") or "")
                                   + " — rangé avec le ticket %s" % dit)
                _journal_auto({
                    "quand": datetime.datetime.now().isoformat(timespec="seconds"),
                    "message_id": mid, "proposition": "", "action": "ranger",
                    "cible": "traite", "objet": subj[:140], "de": exp,
                    "dit": dit, "execute": True,
                    "resultat": "rangé : %s" % (ligne.get("raison") or "")[:120],
                    "pourquoi": "suivi du ticket %s sans destinataire à qui "
                                "transmettre" % dit,
                    "raison": "suivi du ticket %s" % dit})
        faits.append(ligne)
        dire("[mail-dit] suivi ticket %s -> %s%s"
             % (dit, iv.get("mail") or "(aucun intervenant)",
                "" if ligne["execute"] else " (decision enregistree, non envoyee)"))

    if faits:
        try:
            j = _journal_suivis()
            # Une reponse rejouee MET A JOUR sa ligne au lieu d'en ajouter une
            # nouvelle : sinon le journal se remplit du meme mail a chaque
            # cycle tant qu'il n'est pas parti.
            par_mid = {x.get("message_id"): x for x in j if x.get("message_id")}
            for ligne in faits:
                ancienne = par_mid.get(ligne.get("message_id"))
                if ancienne is not None and not ancienne.get("execute"):
                    ancienne.update(ligne)
                else:
                    j.append(ligne)
            JOURNAL_SUIVIS.write_text(json.dumps(j[-1000:], ensure_ascii=False,
                                                 indent=1), encoding="utf-8")
        except Exception as e:
            dire("[mail-dit] journal des suivis : %s" % str(e)[:120])
    return len(faits)


def _journal_suivis():
    try:
        if JOURNAL_SUIVIS.exists():
            return json.loads(JOURNAL_SUIVIS.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def suivis_ticket(n=100):
    """Le journal des reponses sur ticket et de leur destinataire."""
    return list(reversed(_journal_suivis()))[:n]


def rangements_auto(n=50):
    """Le journal des mails rangés sans intervention humaine."""
    try:
        if JOURNAL_AUTO.exists():
            return list(reversed(json.loads(
                JOURNAL_AUTO.read_text(encoding="utf-8"))))[:n]
    except Exception:
        pass
    return []


def cycle():
    """Un passage : on lit la boite de reception en entier (une a deux requetes).

    - mail present et inconnu            -> nouvelle proposition
    - proposition dont le mail a disparu -> un humain l'a traitee

    Renvoie le nombre de propositions creees.
    """
    tok = access_token()
    if not tok:
        raise RuntimeError("pas de jeton mail — lance : python mail_auth.py")

    fid, total = _id_boite(tok)
    msgs = _boite(tok, fid)

    # Les familles qu'Elliot a autorisées à partir seules, AVANT tout le reste :
    # elles n'ont pas à traverser la file de validation. Les mails rangés sont
    # retirés de la liste — ils ne sont plus dans la boîte.
    try:
        deplaces = _ranger_automatiquement(tok, msgs, _etat.get("_log"))
        if deplaces:
            msgs = [m for m in msgs if m.get("id") not in deplaces]
    except Exception as e:
        # Ce bloc a longtemps ete muet, et il avalait une vraie erreur : la
        # reconstitution de la liste des mails deplaces appelait
        # `r["objet"].match` sur une regle dont `objet` vaut None. L'exception
        # partait au silence, AUCUN mail n'etait retire de la liste, et un
        # mail deja range continuait sa route jusqu'au ticket.
        (_etat.get("_log") or (lambda *_a: None))(
            "[mail-dit] rangement automatique : %s" % str(e)[:150])

    # Reponses sur un ticket existant : a qui vont-elles ? (decision
    # enregistree, execution seulement si AUTO_TRANSFERT_SUIVI est arme)
    try:
        _suivis_de_ticket(tok, msgs, _etat.get("_log"))
    except Exception:
        pass

    presents = {m.get("internetMessageId") for m in msgs if m.get("internetMessageId")}

    index = _index_clients()
    libelles = _libelles_clients()
    num_dits = _num_dits()
    attrs = _attrs_clients()
    sym = _symptomes()

    crees = 0
    nouveaux = []
    with _lock:
        d = _charger()
        convs = d["convs"]
        deja = {p.get("message_id") for p in d["props"]}

        # Le signal « fil deja vu » a besoin de l'historique des conversations.
        msgs.sort(key=lambda m: m.get("receivedDateTime") or "")
        for m in msgs:
            cid = m.get("conversationId")
            m["_deja_vu"] = bool(cid) and cid in convs and m.get("internetMessageId") not in deja
            if cid and cid not in convs:
                convs[cid] = (m.get("receivedDateTime") or "")[:10]

        for m in msgs:
            mid = m.get("internetMessageId")
            if not mid or mid in deja:
                continue
            prop, verdict, motif = batir(m, index, num_dits, libelles, attrs,
                                         sym, tok=tok)
            if not prop:
                continue
            prop["id"] = "P-%04d" % d["_next"]
            d["_next"] += 1
            d["props"].append(prop)
            deja.add(mid)
            nouveaux.append(prop["id"])
            crees += 1

        # Retour d'information : un mail sorti de la boite a ete traite par un
        # humain. Aucun besoin de scanner l'archive de 20 729 mails pour ca.
        sortis = 0
        for p in d["props"]:
            if p.get("deja_traite") or p.get("confirme_humain"):
                continue
            if p.get("message_id") and p["message_id"] not in presents:
                p["confirme_humain"] = True
                p["confirme_le"] = datetime.datetime.now().isoformat(timespec="seconds")
                sortis += 1

        # Et la symetrie, qui manquait : un mail REVENU dans la boite doit
        # reveiller sa proposition. Quand on sort un mail d'un dossier — parce
        # que le classement etait faux — sa proposition reste « ecartee » et
        # plus rien ne la regarde : le mail tourne en boite indefiniment.
        # Constate le 2026-09-22 sur « URGENT creation de compte M365 »
        # (P-1035, note « alerte rangee dans BAL TELECOM ») : remis en
        # reception apres correction de la regle, il y est reste.
        # Un mail dans la boite de reception n'a, par definition, pas ete
        # classe : sa proposition redevient a traiter.
        reveilles = 0
        # Le REGISTRE fait foi : il porte le message_id de chaque mail qui a
        # deja donne un ticket. Se fier au seul champ `dit_numero` de la
        # proposition ne suffit pas — il n'est pas toujours ecrit, et rouvrir
        # une proposition deja servie fait naitre un second ticket. C'est
        # arrive le 2026-09-22 : P-1035 avait produit la DIT 100174874, son
        # `dit_numero` etait vide, la reouverture a donne la 100174875.
        deja_ticketises = {l.get("message_id") for l in registre()
                           if l.get("message_id")}
        for p in d["props"]:
            if p.get("dit_numero") or not p.get("message_id"):
                continue
            if p["message_id"] in deja_ticketises:
                continue
            if p["message_id"] in presents and p.get("etat") not in (None, "nouveau"):
                p["etat"] = "nouveau"
                p["confirme_humain"] = False
                p.pop("ombre", None)
                p.pop("auto_tente", None)
                p.pop("auto_version", None)
                p["note"] = ((p.get("note") or "") + " | remis en boite le %s : "
                             "proposition rouverte"
                             % datetime.date.today().isoformat()).strip(" |")
                reveilles += 1
        if reveilles:
            (_etat.get("_log") or (lambda *_a: None))(
                "[mail-dit] %d proposition(s) rouverte(s) : le mail est "
                "revenu en boite" % reveilles)

        limite = (datetime.datetime.now() - datetime.timedelta(days=90)).isoformat()[:10]
        d["props"] = [p for p in d["props"] if (p.get("recu") or "") >= limite]
        d["convs"] = {k: v for k, v in convs.items() if v >= limite}
        _sauver(d)

    # Mode ombre : la decision se pose MAINTENANT, sur une proposition que
    # personne n'a encore regardee. La poser au re-tri arrivait trop tard —
    # le mail part de la boite des qu'un agent le traite, la proposition est
    # alors marquee `confirme_humain` et le re-tri ne la voit plus. Resultat
    # mesure : 1 decision d'ombre sur 39 propositions tranchees.
    if nouveaux:
        _poser_ombres(nouveaux)
    # Rattrapage : les propositions qui attendaient déjà quand le mode ombre a
    # été activé. Marquées `retro`, comptées à part.
    _poser_ombres(retro=True)

    # Le journal reflete la BOITE : une ligne par mail, meme sans action.
    try:
        journaliser_boite(msgs, _etat.get("_log"))
    except Exception:
        pass

    # Puis on AGIT sur ce qui vient d'être décidé. Après les ombres, jamais
    # avant : une décision doit exister et être écrite avant d'être exécutée.
    try:
        traiter_automatiquement(
            _etat.get("_log"),
            dans_la_boite={m.get("internetMessageId") for m in msgs})
    except Exception as e:
        (_etat.get("_log") or (lambda *_a: None))(
            "[mail-dit] traitement automatique interrompu : %s" % str(e)[:150])

    # En dernier : ce qu'aucune règle n'a su prendre est LU par le modèle.
    # Jamais avant — les règles sont gratuites, le modèle se paie.
    try:
        _avis_modele_sur_restes(msgs, _etat.get("_log"))
    except Exception as e:
        (_etat.get("_log") or (lambda *_a: None))(
            "[mail-modele] lecture interrompue : %s" % str(e)[:150])

    _etat.update({"dernier": datetime.datetime.now().isoformat(timespec="seconds"),
                  "cycles": _etat["cycles"] + 1,
                  "vus": _etat["vus"] + len(msgs),
                  "boite": total,
                  "confirmes": _etat.get("confirmes", 0) + sortis})
    _etat["erreur"] = None
    return crees


def _intervalle():
    """60 s en heures ouvrees, 10 min la nuit et le week-end : les demandes
    doivent partir vite en journee, mais rien n'arrive a 3 h du matin."""
    n = datetime.datetime.now()
    if n.weekday() >= 5 or n.hour < 7 or n.hour >= 20:
        return 600
    return 60


def boucle(log=print):
    """Thread de fond. A demarrer depuis updater.py."""
    _etat["actif"] = True
    # Le cycle a besoin de journaliser (rangements automatiques) : on lui
    # laisse le meme logger que la boucle.
    _etat["_log"] = log
    log("[mail-dit] surveillance demarree (delta Graph, lecture seule)")
    while True:
        try:
            # La file se nettoie d'elle-meme quand une regle a ete corrigee.
            r = retrier(log)
            if r:
                log("[mail-dit] %d proposition(s) ecartee(s) au re-tri" % r)
            n = cycle()
            try:
                completer_historique(access_token(), log)
            except Exception as e:            # jamais bloquant : c'est du confort
                log("[mail-dit] historique differe : %s" % str(e)[:120])
            _etat["erreur"] = None
            if n:
                log("[mail-dit] %d proposition(s) de DIT" % n)
        except Exception as e:
            _etat["erreur"] = str(e)[:300]
            log("[mail-dit] erreur : %s" % str(e)[:200])
            time.sleep(120)
            continue
        time.sleep(_intervalle())


def etat():
    e = dict(_etat)
    e.pop("_log", None)          # une fonction ne se sérialise pas en JSON
    # Ce que l'outil a rangé SANS demander : doit rester visible à l'écran.
    e["ranges_auto"] = len(rangements_auto(1000))
    e["intervalle"] = _intervalle()
    e["compteurs"] = compteurs()
    e["jeton"] = bool(access_token())
    e["numeros_dit"] = len(_num_dits())
    e["appris"] = {"clients_attributs": len(_attrs_clients()),
                   "clients_symptomes": len((_symptomes().get("par_client") or {}))}
    # La dépense du modèle doit se voir, pas se découvrir sur une facture.
    try:
        import mail_modele as MM
        m = MM.usage().get(MM._mois(), {})
        e["modele"] = {"nom": MM.MODELE, "disponible": MM.disponible(),
                       "plafond_eur": MM.PLAFOND_EUR,
                       "depense_eur": round(MM.depense_du_mois(), 3),
                       "restant_eur": round(MM.budget_restant(), 3),
                       "appels": m.get("appels", 0),
                       "erreurs": m.get("erreurs", 0),
                       "regles_apprises": len(
                           (MM.regles_apprises().get("expediteurs") or {}))}
    except Exception:
        e["modele"] = {"disponible": False}
    with _lock:
        d = _charger()
    props = [p for p in d["props"] if not p.get("deja_traite")]
    conf = sum(1 for p in props if p.get("confirme_humain"))
    e["mesure"] = {
        "propositions": len(props),
        # un mail sorti de la boite de reception a ete traite par un humain
        "traitees_par_un_humain": conf,
        "en_attente": sum(1 for p in props if p.get("etat") == "nouveau"
                          and not p.get("confirme_humain")),
    }
    e["boite"] = _etat.get("boite")
    return e
