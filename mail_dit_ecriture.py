# -*- coding: utf-8 -*-
"""
ÉCRITURES du moteur mail -> DIT : création du DIT dans Artis, puis déplacement
du mail traité vers « 01 - DIT ARTIS SUPPORT ».

╔══════════════════════════════════════════════════════════════════════════════╗
║  RIEN N'EST ÉCRIT SANS DEUX CONDITIONS RÉUNIES                               ║
║                                                                              ║
║  1. le fichier <DATA_DIR>/mail_dit_ecriture.txt existe et contient OUI       ║
║  2. l'appel passe explicitement confirmer=True                                ║
║                                                                              ║
║  Sans les deux, toute fonction d'écriture retourne la SIMULATION de ce        ║
║  qu'elle aurait envoyé, et n'émet aucune requête.                            ║
║  Le fichier est sous le contrôle d'Elliot : le supprimer désarme l'outil.     ║
╚══════════════════════════════════════════════════════════════════════════════╝

Ordre imposé : le DIT d'abord, le déplacement du mail ENSUITE et seulement si la
création a réussi. Dans l'autre sens, un mail sortirait de la boîte sans ticket
derrière et plus personne ne le retrouverait.

Cf. MAIL_TO_DIT.md et les mémoires feedback_bal_support_dfm /
feedback_artis_pas_decriture_sans_accord.
"""
import io, os, re, json, time, datetime, pathlib, unicodedata, requests

import mail_dit as M

DRAPEAU = M.DATA_DIR / "mail_dit_ecriture.txt"
GRAPH = M.GRAPH

# État posé à la création. « SaisieExterne » (le défaut d'Artis pour une
# création via API) laisse la DIT sans UO et la fait basculer en Terrain.
ETAT_CREATION = "EnCours"


# ═══════════════════════════════════════════════════════ garde-fou
# Le drapeau d'ecriture a trois sources, et l'ordre compte.
#
# Le fichier de `DATA_DIR` est l'original : sur le poste d'Elliot et dans le
# conteneur, il est monte et `mail_dit_ecriture.txt` y vit. Une session
# distante n'a ni l'un ni l'autre — d'ou `MAIL_DIT_ECRITURE=OUI`, qu'on pose
# sur la routine Cloud pour l'armer.
#
# Mais une variable posee dans une routine ne se retire pas vite : il faut
# ouvrir l'interface, editer, enregistrer. D'ou le VETO : quand la chaine
# tourne en distant et que le fichier de `/data` existe sans dire OUI, rien
# ne s'ecrit, quelle que soit la variable. Elliot arrete la chaine en
# remplacant un mot dans un fichier, et au plus tard une minute apres.
#
# Sans le veto, « OUI » dans la routine aurait gagne contre « NON » sur
# `/data` — c'est-a-dire qu'il n'y aurait pas eu de bouton d'arret du tout.
_DRAPEAU_DISTANT = {"vu": 0.0, "valeur": None}
_DRAPEAU_FRAIS = 60                      # secondes


def _arme_texte(t):
    return bool(t) and t.strip().upper().startswith("OUI")


def _drapeau_distant():
    """OUI / NON tel qu'il est sur /data, ou None s'il n'y a rien a lire."""
    try:
        import azure_files
        if not azure_files.disponible():
            return None
        if (time.time() - _DRAPEAU_DISTANT["vu"]) < _DRAPEAU_FRAIS:
            return _DRAPEAU_DISTANT["valeur"]
        brut = azure_files.lire("mail_dit_ecriture.txt")
        _DRAPEAU_DISTANT["valeur"] = (None if brut is None
                                      else _arme_texte(brut.decode("utf-8", "replace")))
        _DRAPEAU_DISTANT["vu"] = time.time()
        return _DRAPEAU_DISTANT["valeur"]
    except Exception:                    # noqa: BLE001
        return None


def ecriture_armee():
    """Le drapeau d'autorisation est-il posé ? (fichier, variable, ou /data)"""
    distant = _drapeau_distant()
    if distant is False:                 # veto : /data dit non, on n'ecrit pas
        return False
    try:
        if DRAPEAU.exists() and _arme_texte(
                DRAPEAU.read_text(encoding="utf-8")):
            return True
    except Exception:
        pass
    if _arme_texte(os.environ.get("MAIL_DIT_ECRITURE")):
        return True
    return bool(distant)


def _autorise(confirmer):
    return bool(confirmer) and ecriture_armee()


# ═══════════════════════════════════════════════════════ Artis
def _conf(cle):
    """Variables d'environnement d'abord, fichier `.env` ensuite.

    En production il n'y a pas de `.env` : la configuration vient des secrets
    de la Container App, exposes en variables. Ne lire que le fichier faisait
    echouer toute la chaine d'ecriture dans le conteneur, avec un
    FileNotFoundError que `relire_dit` avalait au passage.
    """
    v = os.environ.get(cle)
    if v:
        return v
    if not _ENV_FICHIER:
        try:
            # Chemin ancre sur CE fichier : les outils de bin/ tournent
            # depuis leur propre repertoire, ou un ".env" relatif
            # n'existe pas (2026-10-01).
            _env = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".env")
            for l in io.open(_env, encoding="utf-8-sig"):
                if "=" in l and not l.startswith("#"):
                    k, val = l.split("=", 1)
                    _ENV_FICHIER[k.strip()] = val.strip().strip('"')
        except IOError:
            pass
        _ENV_FICHIER.setdefault("__lu__", "1")
    v = _ENV_FICHIER.get(cle)
    if not v:
        raise RuntimeError("configuration Artis absente : %s" % cle)
    return v


_ENV_FICHIER = {}


def _artis():
    env = {c: _conf(c) for c in ("ARTIS_AUTH_URL", "ARTIS_USERNAME",
                                 "ARTIS_PASSWORD", "ARTIS_BASE_URL")}
    r = requests.post(env["ARTIS_AUTH_URL"], timeout=30,
        headers={"accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data="userName=%s&password=%s" % (env["ARTIS_USERNAME"], env["ARTIS_PASSWORD"]))
    if r.status_code != 200:
        raise RuntimeError("auth Artis HTTP %s" % r.status_code)
    return env["ARTIS_BASE_URL"].replace("http://", "https://", 1), r.json().get("token")


# Artis stocke en ISO-8859-15 et SUPPRIME silencieusement ce qu'il ne sait pas
# encoder — sans marqueur, en collant les mots voisins. Constate sur la DIT
# 100173591 : « s’il vous plait » y est arrive « sil vous plait ».
# On normalise donc en amont vers des equivalents stockables. Les lettres
# accentuees appartiennent a ISO-8859-15 : elles restent intactes.
_TYPO = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u00ab": '"',
    "\u00bb": '"', "\u2032": "'", "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2026": "...", "\u00a0": " ", "\u202f": " ", "\u2009": " ",
    "\u200b": "", "\ufeff": "", "\u2022": "-", "\u25cf": "-",
    "\u25aa": "-", "\u00b7": "-", "\u2192": "->", "\u21d2": "=>",
    "\u2764": "", "\u2019": "'",
}


def _stockable(txt):
    """Rend `txt` tel qu'Artis saura le stocker sans rien perdre."""
    if not isinstance(txt, str):
        return txt
    for a, b in _TYPO.items():
        if a in txt:
            txt = txt.replace(a, b)
    # Ce qui resterait hors ISO-8859-15 (emoji, alphabets non latins) : on tente
    # une equivalence latine, sinon on remplace par « ? » plutot que de laisser
    # Artis coller les mots.
    sortie = []
    for c in txt:
        try:
            c.encode("iso-8859-15")
            sortie.append(c)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", c)
            base = "".join(x for x in base if not unicodedata.combining(x))
            try:
                base.encode("iso-8859-15")
                sortie.append(base)
            except UnicodeEncodeError:
                sortie.append("?")
    return "".join(sortie)


# Formule de politesse finale : tout ce qui suit est de la signature.
# ATTENTION : ce motif coupe le corps du mail. « merci » et « bien » y étaient
# en début de ligne sans condition, et emportaient la DEMANDE elle-même :
#   « Merci de bien vouloir procéder aux actions suivantes concernant… »
# Trois offboardings complets (P-0248, P-0249, P-0251) ont ainsi été jugés
# « corps illisible » et refusés par le traitement automatique le 2026-09-15,
# alors que le texte faisait 720 caractères parfaitement clairs.
# On n'accepte donc « merci » et « bien » que dans une formule de CLÔTURE :
# seuls sur leur ligne, ou suivis de la tournure consacrée.
_RE_FIN = re.compile(
    r"^\s*(?:cordialement|cdt|sinc|best\s+regards|regards"
    r"|thanks|salutations|tr[eè]s\s+belle|bonne\s+(?:journ|r[eé]cep|soir)"
    r"|bien\s+(?:cordialement|[àa]\s+vous)"
    r"|merci\s*(?:d.avance|par\s+avance|beaucoup|encore|bien)?\s*[,.!]?\s*$"
    # Pied de page des mobiles. « Envoyé à partir de Outlook pour iOS » fait
    # sept mots : il passait pour une phrase, et un mail qui ne contenait QUE
    # cette ligne et trois photos a donné le DIT 100174363 (Elliot,
    # 2026-09-17). Aucune demande ne commence par là.
    r")",
    re.I)
# Pied de page des mobiles. « Envoyé à partir de Outlook pour iOS » fait sept
# mots : il passait pour une phrase, et un mail qui ne contenait QUE cette
# ligne et trois photos a donné le DIT 100174363 (Elliot, 2026-09-17).
# Contrairement aux formules de politesse, celui-ci se coupe même s'il est la
# PREMIÈRE ligne : aucune demande ne commence par là.
_RE_PIED_MOBILE = re.compile(
    r"^\s*(?:envoy[ée]\s+(?:[àa]\s+partir\s+de|de\s+mon|depuis)\b"
    r"|sent\s+from\s+my\b|obtenir\s+outlook\s+pour\b|get\s+outlook\s+for\b"
    r"|t[ée]l[ée]charger\s+outlook\s+pour\b)", re.I)
# Formule d'ouverture : n'apporte rien au ticket.
_RE_DEBUT = re.compile(r"^(?:bonjour|bonsoir|hello|hi|madame|monsieur)[\s,!.]*$", re.I)


def _ressemble_a_une_phrase(ligne):
    """Une ligne de contenu finit par une ponctuation ou developpe une idee.

    Les lignes de signature sont des groupes nominaux courts : « Laure
    Fraolini », « Key account manager France », « SteamOne ». Elles echouent
    aux deux tests, et c'est exactement ce qu'on veut.
    """
    l = ligne.rstrip()
    return l.endswith((".", "?", "!", ":")) or len(l.split()) >= 6


def _corps_utile(corps):
    """Les lignes du mail qui portent la demande, signature retiree."""
    lignes = [l.strip() for l in (corps or "").split("\n") if l.strip()]
    coupe = len(lignes)
    for n, l in enumerate(lignes):
        if _RE_PIED_MOBILE.match(l) or (n > 0 and _RE_FIN.match(l)):
            coupe = n
            break
    return [l for l in lignes[:coupe] if not _RE_DEBUT.match(l)]


# Symptome de repli quand aucune regle ne tranche. Filet pose par Elliot le
# 2026-09-14 — corrige le 15 : le code « AUTRES » appartient a la TELEPHONIE
# dans Artis, il stampillait nos tickets informatiques « TELEPHONIE - Autres ».
# `symptome_de_repli` choisit desormais entre les deux « Autres » du domaine
# INFO selon que le mail demande quelque chose ou signale une panne.
from mail_regles import symptome_de_repli

# Garde-fou de cadence, pose par Elliot le 2026-09-14 : « ne fais pas de
# creation en masse ou de boucle ; au moindre doute tu ne crees rien ».
# Trois verrous independants :
#   - jamais deux DIT pour la MEME proposition ;
#   - jamais deux DIT pour le meme client dans la meme minute ;
#   - un plafond horaire, au-dela duquel tout s'arrete et il faut un humain.
# Releve a 50 par Elliot le 2026-09-15 : a 12, le plafond bloquait un
# rattrapage legitime de fin de journee. Il reste un coupe-circuit contre
# l'emballement, pas une limite de cadence normale.
PLAFOND_PAR_HEURE = 50
DELAI_MEME_CLIENT = 60          # secondes


def _cadence_ok(prop):
    """Rend (True, "") si la creation peut avoir lieu, sinon (False, raison).

    Lit le registre des DIT deja creees : c'est la seule trace qui survit a un
    redemarrage, donc le seul compteur digne de confiance pour un garde-fou.
    """
    try:
        lignes = M.registre()
    except Exception:
        return True, ""            # pas de registre lisible : on ne bloque pas
    # Une proposition ne donne JAMAIS deux DIT, quelle que soit l'anciennete :
    # ce verrou-la n'a pas de fenetre de temps.
    # L'identite d'un mail, c'est son `message_id` : il ne se reutilise jamais.
    # Le numero de proposition, lui, l'a ete — la file a ete recreee le
    # 2026-09-17 et la numerotation est repartie a P-0001. Resultat : « Dossier
    # partager pour deux postes » (nouveau P-0076) s'est vu refuser la creation
    # parce qu'un ANCIEN P-0076, sans rapport, avait donne la DIT 100173696.
    mid = prop.get("message_id")
    for l in lignes:
        if mid and l.get("message_id") and l["message_id"] == mid:
            return False, ("ce mail a deja donne la DIT %s" % l.get("dit"))
    for l in lignes:
        if (l.get("proposition") and l.get("proposition") == prop.get("id")
                # On ne se fie au numero de proposition que si le registre ne
                # porte pas de message_id : sinon la comparaison ci-dessus fait
                # foi, et celle-ci ne peut plus produire de faux blocage.
                and not l.get("message_id")
                # Et seulement pour le MEME client. Les lignes anciennes n'ont
                # pas de message_id, et la numerotation ayant ete rejouee, un
                # numero peut designer deux mails sans rapport : « Dossier
                # partager pour deux postes » (client 30703) heritait ainsi
                # d'une DIT du client d'un autre P-0076. Deux demandes du meme
                # client sous le meme numero, elles, restent bien bloquees.
                and str(l.get("client") or "") == str(prop.get("client") or "")):
            return False, ("la proposition %s a deja donne la DIT %s"
                           % (prop.get("id"), l.get("dit")))
    maintenant = datetime.datetime.now()
    recentes = []
    for l in lignes:
        try:
            q = datetime.datetime.fromisoformat(l.get("cree_le") or "")
        except Exception:
            continue
        ecart = (maintenant - q).total_seconds()
        if ecart <= 3600:
            recentes.append((ecart, l))
    if len(recentes) >= PLAFOND_PAR_HEURE:
        return False, ("%d DIT deja creees dans l'heure : plafond de securite "
                       "atteint, plus rien ne part sans intervention humaine"
                       % len(recentes))
    for ecart, l in recentes:
        if (ecart <= DELAI_MEME_CLIENT and prop.get("client")
                and l.get("client") == prop.get("client")):
            return False, ("une DIT vient d'etre creee pour le client %s il y "
                           "a %d s (DIT %s) : doublon probable"
                           % (prop.get("client"), ecart, l.get("dit")))
    return True, ""


def _commentaire(prop):
    """Le commentaire du DIT — jamais creux, et lisible par un agent.

    Trois cas :

    1. le corps porte la demande  -> objet en premiere ligne, puis le corps ;
    2. l'objet est deja dans le corps -> le corps seul, sans repetition ;
    3. le corps ne dit rien (image, signature seule) -> on n'y colle pas la
       signature : on ecrit l'objet, puis d'ou vient la demande et le fait que
       le message n'avait pas de texte. L'agent sait alors quoi faire et
       pourquoi il n'y a rien d'autre a lire.

    Le cas 3 est celui de la DIT 100173591. Aucun texte n'est invente : tout
    vient du mail (objet, expediteur, date).
    """
    corps = (prop.get("commentaire") or "").strip()
    objet = (prop.get("objet") or "").strip()

    if not _corps_utile(corps) or not any(
            _ressemble_a_une_phrase(l) for l in _corps_utile(corps)):
        lignes = [objet or "(objet vide)", ""]
        qui = " ".join(x for x in (prop.get("de_nom") or "",) if x).strip()
        adr = prop.get("de") or ""
        de = ("%s <%s>" % (qui, adr)) if qui and adr else (qui or adr or "?")
        quand = _quand(prop.get("date_dit") or prop.get("recu"))
        lignes.append("Demande reçue par mail%s, de %s."
                      % ((" le " + quand) if quand else "", de))
        lignes.append("Le corps du message ne contenait pas de texte : "
                      "la demande est dans l'objet.")
        return _avec_pieces("\n".join(lignes).strip(), prop)

    corps = _sans_images_de_signature(corps)
    if objet and objet.lower() not in corps.lower():
        corps = (objet + "\n\n" + corps).strip()
    return _avec_pieces(corps, prop)


def _avec_pieces(texte, prop):
    """Annonce les pieces jointes DANS le commentaire.

    Elliot (2026-09-14) : « ajouter les photos en PJ et preciser dans la DIT
    que les photos sont en PJ ». Sans cette ligne, l'agent qui lit la DIT ne
    sait pas qu'il doit aller ouvrir la GED — surtout quand le mail n'avait pas
    de texte et que la photo EST la demande.
    """
    noms = []
    for src in [prop] + list(prop.get("_regroupees") or []):
        for p in (src.get("pieces") or []):
            if p.get("nom") and p["nom"] not in noms:
                noms.append(p["nom"])
    if not noms:
        return texte
    photos = all(str(n).lower().endswith((".jpg", ".jpeg", ".png", ".heic",
                                          ".heif", ".webp")) for n in noms)
    quoi = "photo" if photos else "pièce"
    return (texte + "\n\n%s%s jointe%s à la DIT : %s"
            % (quoi[0].upper() + quoi[1:], "s" if len(noms) > 1 else "",
               "s" if len(noms) > 1 else "", ", ".join(noms))).strip()


def _sans_images_de_signature(corps):
    """Retire les lignes entre crochets situees APRES la formule de politesse.

    Ce sont les images de la signature (« [Marketing] », « [instagram] »,
    « [Rentree DFM - Septembre 2026] »), que Graph laisse sous forme de texte
    alternatif. On ne peut pas filtrer ce motif partout : l'objet de certaines
    demandes est lui-meme entre crochets — « [Acces a avant-venteit@dfm.fr] ».
    D'ou la restriction a la zone de signature, ou rien d'utile ne se trouve.
    """
    lignes = corps.split("\n")
    debut_sig = None
    for n, l in enumerate(lignes):
        if n > 0 and _RE_FIN.match(l.strip()):
            debut_sig = n
            break
    if debut_sig is None:
        return corps
    gardees = lignes[:debut_sig] + [
        l for l in lignes[debut_sig:]
        if not re.match(r"^\s*\[[^\]]*\]\s*$", l)]
    return "\n".join(gardees)


def _quand(iso):
    """« 2026-09-10T17:10:20 » -> « 10/09/2026 à 17h10 »."""
    if not iso:
        return ""
    try:
        d = datetime.datetime.fromisoformat(str(iso)[:19].replace("Z", ""))
        return d.strftime("%d/%m/%Y à %Hh%M")
    except Exception:
        return ""


def corps_dit(prop, numero_externe=True, identification="tout"):
    """Construit le corps exact de POST /dit à partir d'une proposition.

    Champs Artis obligatoires : `commentaire`, plus `codeClient`+`codeSite` OU
    `identifiantFabricantBien`.

    Le demandeur est TOUJOURS passé par son `id`. Avec titre+nom+prénom, Artis
    CRÉE la personne si elle n'est pas retrouvée — des doublons existent déjà en
    base (ex. MACAQUI présent 2× chez le client 27011).

    `numeroExterne` porte l'identifiant du mail : le DIT reste rattachable au
    message d'origine, dans les deux sens.
    """
    b = {"commentaire": _commentaire(prop)}

    # `identification` : comment Artis doit retrouver le client et le site.
    #   "tout"   -> client + site + bien (ce qu'on faisait)
    #   "bien"   -> le bien seul, presente par leur doc comme suffisant
    #   "client" -> client + site, sans bien
    # On teste ces variantes parce que la table de decision aboutit peut-etre a
    # « Terrain » quand on lui donne deux chemins d'identification au lieu d'un.
    champs = [("codeTypeDit", "type_dit"), ("codeNature", "nature"),
              ("codePriorite", "priorite"), ("codeDomaine", "domaine"),
              ("codeSymptome", "symptome"), ("codeOrgInt", "org_int")]
    if identification in ("tout", "client"):
        champs += [("codeClient", "client"), ("codeSite", "site")]
    if identification in ("tout", "bien"):
        champs += [("identifiantFabricantBien", "bien")]
    for cle, champ in champs:
        v = prop.get(champ)
        # Filet : plutot que de renoncer au ticket, on pose le symptome
        # « AUTRES ». La demande du client existe, elle ne doit pas se perdre
        # parce que l'outil n'a pas su la ranger (Elliot, 2026-09-14).
        if not v and champ == "symptome":
            v = symptome_de_repli(prop.get("objet"), prop.get("commentaire"))
        if v:
            b[cle] = str(v)

    if prop.get("pid_interlocuteur"):
        b["demandeur"] = {"id": int(prop["pid_interlocuteur"])}
    # « Personne a contacter » : un champ distinct du demandeur chez Artis.
    # Il sert quand la demande passe par une assistante mais que l'intervenant
    # doit joindre quelqu'un d'autre (cf. REGLES_CLIENT).
    if prop.get("pid_contact"):
        b["contact"] = {"id": int(prop["pid_contact"])}

    # Date du DIT = date du MAIL (demande d'Elliot) : le ticket doit refléter
    # le moment où le client a fait sa demande, pas celui du traitement.
    if prop.get("date_dit"):
        b["dateDit"] = prop["date_dit"]

    # État interne dès la création, et non « SaisieExterne ».
    # Mesuré : les DIT en saisie externe n'ont aucune UO et se retrouvent en
    # typeGpTravail « Terrain » dans 90 % des cas, contre 28 % ailleurs. Artis
    # ne recalcule pas ce champ quand on pose l'UO après coup — il faut donc
    # créer directement dans un état interne. (Hypothèse d'Elliot, vérifiée.)
    b["etatDit"] = ETAT_CREATION

    # `numeroExterne` garde le lien DIT -> mail, mais il declenche peut-etre le
    # drapeau « Importee » d'Artis, qui semble pousser le type de travail vers
    # « Terrain » (correlation constatee sur 4 DIT le 2026-09-09). D'ou le
    # commutateur : on peut creer sans, pour le verifier.
    ref = prop.get("id") or ""
    if numero_externe and ref:
        b["numeroExterne"] = ref[:20]
    # Un seul point de passage pour la normalisation : aucun champ texte ne
    # part sans etre stockable par Artis.
    return {k: _stockable(v) for k, v in b.items()}


def valider_dit(numero, confirmer=False):
    """PATCH /dit/{numero}/validate — fait passer de « SaisieExterne » à un état
    interne. Sans ça le ticket apparaît comme une saisie externe et non comme
    une demande enregistrée par un agent (retour d'Elliot, 2026-09-09)."""
    out = {"envoye": False, "arme": ecriture_armee(), "dit": numero}
    if not numero:
        out["erreur"] = "numéro manquant"
        return out
    if not _autorise(confirmer):
        out["simulation"] = "aucune requête émise. PATCH /dit/%s/validate" % numero
        return out
    base, tok = _artis()
    r = requests.patch("%s/dit/%s/validate" % (base, numero),
                       params={"token": tok},
                       headers={"accept": "application/json"}, timeout=40)
    out["http"] = r.status_code
    out["envoye"] = r.status_code in (200, 204)
    if not out["envoye"]:
        out["erreur"] = r.text[:300]
    return out


def verifier(prop):
    """Liste les problèmes bloquants et les avertissements, sans rien envoyer."""
    bloquants, avertis = [], []
    # Cadence AVANT tout le reste : un emballement doit s'arrêter net, pas se
    # discuter champ par champ.
    ok_cadence, pourquoi = _cadence_ok(prop)
    if not ok_cadence:
        bloquants.append("garde-fou de cadence : %s" % pourquoi)
    if not (prop.get("commentaire") or "").strip():
        bloquants.append("commentaire vide (seul champ obligatoire d'Artis)")
    if not prop.get("client"):
        bloquants.append("client non identifié")
    elif not prop.get("site") and not prop.get("bien"):
        bloquants.append("il faut un site OU un bien : Artis refuse sans l'un des deux")
    # Le symptôme ne bloque plus : filet de sécurité posé par Elliot le
    # 2026-09-14 — « si tu n'arrives pas à trouver le symptôme, plutôt que de
    # ne pas créer le ticket, mets AUTRES, et mentionne-le dans la liste des
    # DIT créées ». Un ticket imparfait vaut mieux qu'une demande client
    # perdue ; le signalement rend la correction possible.
    if not prop.get("symptome"):
        avertis.append("symptôme non trouvé : le DIT sera créé en « %s » et "
                       "signalé comme tel dans l'historique"
                       % symptome_de_repli(prop.get("objet"),
                                           prop.get("commentaire")))
    if prop.get("doute_client"):
        avertis.append("client déduit d'un domaine partagé : deux fois moins "
                       "fiable que la normale (mesuré)")
    if not prop.get("pid_interlocuteur"):
        avertis.append("aucun interlocuteur : le demandeur du DIT restera vide "
                       "(ne JAMAIS le passer en texte, Artis créerait un doublon)")
    if prop.get("piece_jointe"):
        avertis.append("le mail a une pièce jointe : elle n'est pas reprise, "
                       "à joindre à la main")
    if prop.get("etat") == "valide" and prop.get("dit_numero"):
        # Garde-fou anti-doublon — mais un DIT ANNULE ne fait pas doublon, et
        # bloquer laisserait la demande du client sans ticket. Constate le
        # 2026-09-11 : la DIT 100173675, creee par erreur en domaine
        # Impression, a du etre annulee puis refaite ; ce garde-fou a bloque
        # la recreation et il a fallu detacher le numero a la main.
        etat_dit = (relire_dit(prop["dit_numero"]) or {}).get("etat")
        if etat_dit != "Annulee":
            bloquants.append("un DIT %s est déjà rattaché à cette proposition"
                             % prop["dit_numero"])
        else:
            avertis.append("le DIT %s rattaché est annulé : on en recrée un"
                           % prop["dit_numero"])
    return bloquants, avertis


def _empreinte(txt):
    """Le texte reduit a ce qui identifie l'incident : lettres et chiffres,
    sans accents ni ponctuation ni casse. Deux redactions du meme mail — l'une
    passee par Artis en ISO-8859-15, l'autre fraiche — doivent se ressembler."""
    t = unicodedata.normalize("NFD", txt or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def dit_jumelle(prop, heures=24):
    """Numero d'une DIT deja posee chez ce client pour le MEME incident, ou "".

    Compare le debut du commentaire : c'est la phrase du client, elle est
    reprise telle quelle par qui saisit le ticket, humain ou moteur. 60
    caracteres significatifs suffisent a distinguer deux demandes reelles et
    resistent aux ecarts de mise en forme.

    En cas de doute technique — Artis muet, reponse illisible — on renvoie ""
    plutot que de bloquer : ce garde-fou evite un doublon, il ne doit jamais
    empecher un ticket legitime. Mais ce "" la est INDISCERNABLE d'un "aucun
    jumeau", et c'est ce qui a laisse passer le doublon GREENWICH du
    2026-09-29 : 100175707 a 11h18, 100175713 a 11h28, textes identiques, et
    aucune trace de la raison. `dit_jumelle_detail` dit desormais si la
    verification a REELLEMENT eu lieu ; `creer_dit` le porte dans ses
    avertissements et dans le registre.
    """
    d = dit_jumelle_detail(prop, heures)
    return d["numero"]


def dit_jumelle_detail(prop, heures=24):
    """Comme `dit_jumelle`, mais rend {numero, verifie, raison}.

    `verifie` a False veut dire : on n'a PAS pu regarder. Creer dans ce cas
    reste le bon choix — perdre la demande d'un client coute plus cher qu'un
    doublon — mais il faut que ca se voie.
    """
    code = prop.get("client")
    # Le texte BRUT du mail, pas le commentaire mis en forme : selon qui saisit,
    # la meme demande arrive precedee de l'objet, d'un entete ou de rien. On
    # compare donc par INCLUSION, dans les deux sens — l'un des deux textes
    # contient l'autre des que c'est le meme incident.
    nous = _empreinte(prop.get("commentaire") or "")
    ref = nous[:60]
    if not code:
        return {"numero": "", "verifie": False, "raison": "client inconnu"}
    if len(ref) < 25:
        return {"numero": "", "verifie": False,
                "raison": "commentaire trop court pour une empreinte fiable"}
    # DEUX tentatives : un hoquet d'Artis ne doit pas desarmer le garde-fou
    # sans bruit. C'est le seul endroit du passage ou une panne reseau se
    # traduit par un ticket en trop.
    lst, souci = None, ""
    for essai in range(2):
        try:
            base, tok = _artis()
            jour = datetime.datetime.now()
            r = requests.get("%s/client/%s/dit" % (base, code), params={
                "token": tok,
                "dateDebut": (jour - datetime.timedelta(hours=heures)).strftime("%Y-%m-%d"),
                "dateFin": (jour + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
                "maxResults": 50}, headers={"accept": "application/json"},
                timeout=45)
            if r.status_code != 200:
                souci = "Artis a repondu HTTP %s" % r.status_code
            else:
                j = r.json()
                lst = j if isinstance(j, list) else (j.get("items")
                                                    or j.get("dit") or [])
                souci = ""
                break
        except Exception as exc:                   # noqa: BLE001
            souci = "appel Artis en echec : %s" % str(exc)[:120]
        if essai == 0:
            time.sleep(3)
    if lst is None:
        return {"numero": "", "verifie": False,
                "raison": souci or "liste des DIT du client illisible"}
    limite = datetime.datetime.now() - datetime.timedelta(hours=heures)
    for d in lst:
        try:
            quand = datetime.datetime.fromisoformat(str(d.get("date"))[:19])
        except Exception:
            quand = None
        if quand and quand < limite:
            continue
        # Une DIT ANNULEE n'est pas un doublon : elle a justement ete retiree.
        # Sans cette reserve, annuler un ticket puis le recreer correctement
        # devient impossible — constate le 2026-09-25 en corrigeant le domaine
        # de la DIT 100175330. Une DIT CLOTUREE, elle, reste un vrai doublon :
        # le probleme a bien ete pris en charge.
        if str(d.get("etat") or "").lower().startswith("annul"):
            continue
        autre = _empreinte(d.get("commentaire") or "")
        if len(autre) < 25:
            continue
        if ref in autre or autre[:60] in nous:
            return {"numero": str(d.get("numero") or ""), "verifie": True,
                    "raison": ""}
    return {"numero": "", "verifie": True, "raison": ""}



# ══════════════════════════════════════════════ regles propres a un client
# Certains clients imposent qui doit figurer en demandeur, quelle que soit la
# personne qui ecrit. La regle ne se devine pas : elle est dictee par le
# client, on l'ecrit donc noir sur blanc plutot que de la deduire.
#
#   demandeur_impose        adresse du contact a mettre SYSTEMATIQUEMENT en
#                           demandeur de la DIT ;
#   contact_personne_du_mail  la personne reellement concernee — celle qui ecrit,
#                           ou celle POUR QUI la demande est faite — va dans le
#                           champ « personne a contacter » d'Artis ;
#   valideur                le circuit de validation client — voir plus bas.
#
# 13401 Me STEPHANE BONIFASSI — demande d'Elliot du 2026-09-25 : toutes les
# demandes passent par Delphine PRIGENT, l'assistante ; la personne concernee
# reste jointe en personne a contacter pour que l'intervenant sache qui appeler.
#
# 9599 / 9600 MYMOBILITY (JL INTERNATIONAL et ST2S) — sa fiche Artis porte :
# « Pour demande de droits, ouverture de sites - Validation par M. DUCLAUX ou
# M. JIMENEZ, email : dsi@mymobility.fr ». Ce commentaire de fiche n'est PAS
# expose par l'API : la regle est donc recopiee ici.
# ───────────────────────────────────────────────────────────────────────────
# Le contrat d'infogerance porte par une AUTRE societe du groupe
# ───────────────────────────────────────────────────────────────────────────
# `bien_dit` a None ne prouve pas l'absence de contrat : il prouve l'absence de
# contrat DANS CETTE FICHE. Un groupe peut faire porter l'infogerance de toutes
# ses filiales par une seule entite, et c'est alors chez elle que vivent les
# biens.
#
# Toutes les CLINIQUE DES CHAMPS-ELYSEES sont dans ce cas : ELITE CENTER
# (12930) porte un trio INFOGERANCE / BAL EXCHANGE 365 / PROTECT par ville,
# chacun sur son propre site et PREFIXE du code client de la filiale
# (« 14288 - INFOGERANCE RENNES », site 006). Elliot, le 2026-10-12, apres que
# le mail de la clinique de Rennes est parti au commercial : « c'est faux car
# toutes les cliniques des champs elysees sont couvertes par ELITE CENTER qui
# lui a bien un contrat ».
#
# Verifie dans Artis le 2026-10-12 sur les 474 biens de 12930.
CONTRAT_PORTE_PAR = {
    "5535": "12930",    # CLINIQUE DU ROND POINT DES CHAMPS ELYSEES (PARIS)
    "10721": "12930",   # LILLE - BORDEAUX ; porte aussi DIJON, MARSEILLE,
                        # STRASBOURG, TOULOUSE, AIX, REIMS, CANNES, REUILLY
    "12769": "12930",   # NICE ; porte aussi CAEN et LEVALLOIS
    "13573": "12930",   # BORDEAUX
    "14232": "12930",   # LYON
    "14287": "12930",   # TOURS
    "14288": "12930",   # RENNES
    "14323": "12930",   # LA BAULE
    "15327": "12930",   # PARIS 08 (site SEBASTOPOL)
    "19002": "12930",   # MONTPELLIER
}

# Les trois modeles qui valent « contrat d'infogerance informatique », dans
# l'ordre de preference pose par Elliot le 2026-10-09.
MODELES_CONTRAT = ("INFOGERANCE", "BAL EXCHANGE 365", "PROTECT")

# Mots d'une raison sociale qui ne designent jamais une ville.
_MOTS_NEUTRES = {"CLINIQUE", "CLINIQUES", "CENTRE", "CABINET", "SARL", "SAS",
                 "SASU", "SCI", "SA", "GROUPE", "DES", "DU", "LES", "LE",
                 "LA", "CHAMPS", "ELYSEES", "ELYSEE", "ROND", "POINT"}


def _sans_accent(txt):
    s = unicodedata.normalize("NFD", (txt or "").upper())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _mots_de_ville(raison_sociale):
    """Les mots d'une raison sociale susceptibles de nommer une ville."""
    mots = [m for m in re.split(r"[^A-Z0-9]+", _sans_accent(raison_sociale))
            if len(m) >= 3 and not m.isdigit()]
    return [m for m in mots if m not in _MOTS_NEUTRES]


def biens_portes(code, biens_du_porteur=None):
    """Les biens que le porteur detient POUR cette filiale, ou [].

    Chez le porteur, chaque bien de filiale est prefixe du code client de
    celle-ci. On ne garde que ceux-la : sans ce filtre on poserait le bien
    d'une autre clinique, et `identifiantFabricant` etant une cle GLOBALE,
    Artis collerait a la DIT le site de cette autre ville.
    """
    cible = str(code or "").strip()
    porteur = CONTRAT_PORTE_PAR.get(cible)
    if not porteur:
        return []
    liste = biens_du_porteur
    if liste is None:
        base, tok = _artis()
        try:
            r = requests.get("%s/client/%s/bien" % (base, porteur),
                             params={"token": tok, "maxResults": 5000},
                             headers={"accept": "application/json"}, timeout=120)
            liste = r.json() if r.status_code == 200 else []
        except Exception:                      # noqa: BLE001
            liste = []
    pref = re.compile(r"^\s*%s\s*-" % re.escape(cible))
    return [b for b in (liste or [])
            if pref.match((b.get("identifiantFabricant") or ""))]


def bien_porte(code, trace=None):
    """(identifiantFabricant, codeSite, codeOrgInt) du bien pris chez le porteur.

    Elliot a tranche le 2026-10-12, entre trois options : « DIT avec le bien
    d'ELITE CENTER » — bien et site exacts, contrat juste pour la facturation,
    au prix d'un ticket qui s'affiche au nom du porteur et non de la clinique.

    Les sites du porteur n'ont rien a voir avec ceux de la filiale : la regle
    « site principal d'abord » n'a donc pas de sens ici. Le seul repere est la
    VILLE, que la raison sociale de la filiale nomme presque toujours. Deux
    codes en portent plusieurs — 10721 (LILLE, BORDEAUX, DIJON, MARSEILLE,
    STRASBOURG, TOULOUSE, AIX, REIMS, CANNES, REUILLY) et 12769 (NICE, CAEN,
    LEVALLOIS) : sans ville reconnue, le premier du modele gagne, et `trace`
    le signale pour qu'on corrige AVANT de creer. Apres, rien ne se repare.
    """
    cible = str(code or "").strip()
    porteur = CONTRAT_PORTE_PAR.get(cible)
    if not porteur:
        return (None, None, None)
    biens = biens_portes(cible)
    if trace is not None:
        trace["porteur"] = porteur
    if not biens:
        return (None, None, None)

    base, tok = _artis()
    raison = ""
    try:
        r = requests.get("%s/client/%s" % (base, cible), params={"token": tok},
                         headers={"accept": "application/json"}, timeout=40)
        if r.status_code == 200:
            f = r.json() or {}
            raison = f.get("raisonSociale") or f.get("libelle") or ""
    except Exception:                          # noqa: BLE001
        raison = ""
    villes = _mots_de_ville(raison)

    def _triplet(b):
        return (b.get("identifiantFabricant"),
                (b.get("site") or {}).get("code"),
                (b.get("orgInt") or {}).get("code"))

    for modele in MODELES_CONTRAT:
        lot = [b for b in biens
               if ((b.get("modele") or {}).get("code") or "").strip().upper()
               == modele]
        if not lot:
            continue
        if trace is not None:
            trace["modele"] = modele
            trace["candidats"] = len(lot)
        for v in villes:
            motif = re.compile(r"\b%s\b" % re.escape(v))
            for b in lot:
                txt = _sans_accent("%s %s" % (b.get("identifiantFabricant") or "",
                                              b.get("libelle") or ""))
                if motif.search(txt):
                    if trace is not None:
                        trace["ville"] = v
                    return _triplet(b)
        if trace is not None and len(lot) > 1:
            trace["ambigu"] = True
        return _triplet(lot[0])
    return (None, None, None)


REGLES_CLIENT = {
    "13401": {"demandeur_impose": "d.prigent@bonifassi-avocats.com",
              "contact_personne_du_mail": True},
    "9599": {"valideur": "mymobility"},
    "9600": {"valideur": "mymobility"},
    "8280": {"valideur": "sfaf"},
    "12390": {"valideur": "freitas"},
    # RUNIMMO — les quatre societes du groupe qui ont un contrat Info et dont
    # la fiche porte Elodie CORJON. 20226 (« runimmo gestion ») est une fiche
    # sans contrat et sans elle : elle n'est pas du circuit.
    "14329": {"valideur": "runimmo"},
    "15042": {"valideur": "runimmo"},
    "15043": {"valideur": "runimmo"},
    "17038": {"valideur": "runimmo"},
}


# ═══════════════════════════════════════════════ le circuit de validation
# Certains clients exigent qu'un tiers de chez eux valide avant qu'on touche a
# quoi que ce soit — des droits, un acces, l'ouverture d'un site. Artis le
# materialise par un Tag sur la DIT, pose a la main jusqu'ici. Demande
# d'Elliot le 2026-10-06, sur la DIT 100176480 (acces SAGE PAIE chez
# MYMOBILITY) : poser le Tag « En attente de validation client » a la
# CREATION, et le passer a « Demande validee » quand le valideur repond.
#
# Trois garde-fous, poses par Elliot le meme jour :
#   - on ne touche PAS aux anciens tickets : la regle ne vaut que pour les
#     DIT creees a partir de maintenant, jamais retroactivement ;
#   - on ne touche PAS a un ticket cloture — ni pour poser le tag, ni pour le
#     changer ; c'est fini, on laisse ;
#   - le mail du valideur se transfere a l'intervenant et se pose mot pour mot
#     en commentaire : AUCUNE phrase de nous entre parentheses pour expliquer
#     qui a valide quoi et quand. Le mail transfere suffit.
VALIDEURS = {
    "mymobility": {
        # qui valide — on reconnait sa reponse a son adresse
        "adresses": ("dsi@mymobility.fr", "lm.jimenez@orange.fr"),
        "qui": "M. DUCLAUX ou M. JIMENEZ (dsi@mymobility.fr)",
        # ce qui doit etre valide : une demande de droits, d'acces, ou
        # l'ouverture d'un site. Une creation ou un depart de compte que le
        # valideur envoie LUI-MEME n'a pas a etre valide par lui.
        "motifs": ("droit", "droits", "acces", "accès", "habilitation",
                   "ouverture de site", "ouverture de sites", "autorisation"),
    },
    # SFAF — ajoute le 2026-10-09. Le circuit existait deja dans les faits :
    # Clifford envoie la demande de validation a Karine DOGNIN SAUZE, qui
    # repond. Mais le client n'etait pas dans la table, donc
    # `valideur_du_client("8280")` rendait None et AUCUN tag n'etait pose — ni
    # « En attente », ni « Demande validee ». Elliot, sur la DIT 100177064 :
    # « ta pas mis le tag demande validée (ca devrait etre automatique quand
    # c'est validé pourquoi tu la pas fait) ». La cause n'etait pas un oubli
    # de geste, c'etait une table incomplete : un client absent d'ici est
    # invisible pour tout le circuit.
    "sfaf": {
        "adresses": ("kdognin-sauze.ext@sfaf.com",),
        "qui": "Mme Karine DOGNIN SAUZE (kdognin-sauze.ext@sfaf.com)",
        "motifs": ("droit", "droits", "acces", "accès", "habilitation",
                   "ouverture de site", "ouverture de sites", "autorisation",
                   "boite mail", "boîte mail", "drive", "onedrive",
                   "sharepoint"),
    },
    # FREITAS LEVAGE — ajoute le 2026-10-13. Stephanie FREITAS est la DG :
    # quand c'est ELLE qui demande une creation de compte et des
    # sous-delegations, il n'y a personne a qui soumettre la demande, elle
    # autorise. La DIT 100177181 (arrivee de Mme ANTUNES) est pourtant partie
    # en « En attente de validation client » faute d'entree ici — Elliot,
    # le 2026-10-13 : « il aurait du arriver en valide c'est le dg qui fait la
    # demande ».
    # RUNIMMO — ajoute le 2026-10-14. Elodie CORJON ecrit pour les quatre
    # societes du groupe et c'est elle qui autorise : sa demande n'a personne a
    # qui etre soumise. La DIT 100177533 (mise en boite partagee de
    # m.schuller@runimmo.re sur celle de c.cassar@runimmo.re, plus changement
    # de mot de passe) est pourtant partie « En attente de validation client »,
    # faute d'entree ici — Elliot, le 2026-10-14 : « ce ticket doit etre valide
    # a la creation 100177533, le ticket a ete fait directement par le
    # valideur ». Le tag a ete repasse en « Demande validee » a la main.
    "runimmo": {
        "adresses": ("e.corjon@runimmo.fr",),
        "qui": "Mme Elodie CORJON (e.corjon@runimmo.fr)",
        "motifs": ("droit", "droits", "acces", "accès", "habilitation",
                   "autorisation", "delegation", "délégation", "partage",
                   "boite mail", "boîte mail", "boite partagee",
                   "boîte partagée", "drive", "onedrive", "sharepoint",
                   "licence", "licences"),
    },
    "freitas": {
        "adresses": ("stephanie_freitas@freitas.fr",),
        "qui": "Mme Stephanie FREITAS, DG (stephanie_freitas@freitas.fr)",
        "motifs": ("droit", "droits", "acces", "accès", "habilitation",
                   "autorisation", "delegation", "délégation",
                   "sous delegation", "sous-delegation", "boite mail",
                   "boîte mail", "drive", "onedrive", "sharepoint"),
    },
}


def valideur_du_client(code):
    """Rend la fiche du valideur du client, ou None s'il n'en a pas."""
    nom = (REGLES_CLIENT.get(str(code or "")) or {}).get("valideur")
    return VALIDEURS.get(nom) if nom else None


# Les motifs qui demandent une validation, pour TOUS les clients — Elliot,
# le 2026-10-09 : « tout ce qui est demande de droit, delegation, acces
# sharepoint etc. doit faire l'objet d'une validation, donc faut mettre le tag
# en attente de validation comme pour JLI (pour tous les clients) ».
#
# Avant, le circuit ne valait que pour les clients inscrits dans REGLES_CLIENT
# (MYMOBILITY, puis SFAF) : un client absent de la table etait invisible pour
# tout le circuit, et sa demande de droits partait sans tag. C'est ce qui est
# arrive a la DIT 100177064 (SFAF). Desormais, c'est le MOTIF qui declenche,
# pas l'appartenance a une table ; la table ne sert plus qu'a nommer QUI
# valide chez les clients qui ont designe quelqu'un.
MOTIFS_A_VALIDER = (
    "droit", "droits", "acces", "accès", "habilitation", "autorisation",
    "ouverture de site", "ouverture de sites", "delegation", "délégation",
    "sharepoint", "onedrive", "drive", "boite mail", "boîte mail",
    "boite aux lettres", "boîte aux lettres", "partage", "privilege",
    "privilège", "administrateur", "admin ", "mfa",
    # Une licence se paye : l'ajouter engage le client, donc elle se valide
    # comme un droit. Elliot, le 2026-10-13, sur la DIT 100177444 (5 licences
    # Kiosk sur le tenant mymob.fr) : « demande d'ajout de licence : faut une
    # validation ». Et comme le motif rejoint ceux du valideur, la demande
    # faite par le valideur LUI-MEME part en « Demande validee » tout de
    # suite — c'etait le cas ici, Luis JIMENEZ etant le valideur de JLI.
    "licence", "licences",
)

# Ce qui, dans la reponse d'un client, vaut accord. Volontairement etroit :
# une question ou une relance ne doit pas faire passer le tag en « validee ».
# Des ACTES DE PAROLE, compares avec des frontieres de mot — jamais des
# sous-chaines. L'ancienne liste contenait « ok. » et « valide » nus : le
# 2026-10-09, un mail de BOUMA qui transmettait une procedure technique
# (« Installer le complement Out*look.* », « le certificat SSL est valide »,
# « les autorisations Microsoft Graph ») a ete lu comme une validation du
# client, range en 14 - VALIDATION CLIENT et allait faire passer la DIT
# 100175418 en « Demande validee ».
#
# Un faux positif ici fait lancer a l'equipe un travail que personne n'a
# autorise : en cas de doute, on ne valide pas — le tag reste « en attente »
# et un humain tranche.
MARQUES_ACCORD = (
    r"\bje valide\b", r"\bnous validons\b", r"\bje la valide\b",
    r"\bc'?est valid[ée]e?\b", r"\bdemande valid[ée]e\b",
    r"\bvalid[ée]e? par\b", r"\bbon pour accord\b",
    r"\bje vous confirme\b", r"\bje confirme\b", r"\bnous confirmons\b",
    r"\bd'?accord\b", r"\b(mon|notre|votre) accord\b",
    r"\bdonne[zs]? (mon|notre) accord\b",
    r"\bfeu vert\b",
    # « Vous pouvez … » : la liste des verbes etait trop courte. Mohammed
    # KASSAINI (ALIGN AERO) a repondu « Vous pouvez traiter cette demande » sur
    # le fil de validation de la DIT 100176803, le 2026-10-14 : aucune marque
    # ne l'a reconnu, le ticket est reste « En attente de validation client »
    # et le mail est parti en 01 au lieu de 14. Le tag a ete repasse a la main.
    r"\bvous pouvez (proc[ée]der|y aller|le faire|traiter|lancer|"
    r"continuer|intervenir|effectuer|realiser|r[ée]aliser|faire le "
    r"n[ée]cessaire)\b",
    r"\bvous avez (mon|notre) (feu vert|accord)\b",
    r"\bj'?autorise\b", r"\bnous autorisons\b",
    r"\bok pour\b", r"\bc'?est ok\b", r"\bc est ok\b",
)

# « Ok » tout seul vaut accord — mais seulement dans un message court, pas
# perdu au milieu d'une procedure. « Oui » compte pareil, et seulement
# en TETE du message : la DIT 100177359 (PRESENCE CONSEIL) est restee en
# « En attente de validation client » alors que M. Neuville avait repondu
# « Oui, merci » le 12/10 — un accord qu'aucune marque ne reconnaissait,
# et qu'Elliot a du signaler a la main le 13/10.
_OK_SEUL = re.compile(r"\bok\b", re.I)
_OUI_SEUL = re.compile(r"^\W*oui\b", re.I)
_ACCORDS = tuple(re.compile(m, re.I) for m in MARQUES_ACCORD)


def demande_a_valider(code, de, texte):
    """La demande doit-elle partir en « En attente de validation client » ?

    Vrai des que la demande porte sur des droits, une delegation ou un acces —
    pour TOUS les clients, pas seulement ceux qui ont un valideur nomme.

    Une exception : quand le client a designe un valideur et que c'est LUI qui
    ecrit, il n'y a rien a valider, il decide.
    """
    v = valideur_du_client(code)
    if v and (de or "").strip().lower() in [a.lower() for a in v["adresses"]]:
        return False
    motifs = ((v or {}).get("motifs") or ()) + MOTIFS_A_VALIDER
    return _motif_de_validation(texte, motifs)


# « acces » est le mot le plus large de MOTIFS_A_VALIDER, et il attrape des
# demandes qui n'ont rien a voir avec des droits : la migration d'un lien
# d'ACCES INTERNET, un POINT D'ACCES wifi. Constate le 2026-10-09 sur la DIT
# 100177089 (FACE B, « migration du lien d'acces internet »), qui est partie
# avec un tag « En attente de validation client » absurde — personne n'a a
# valider le remplacement d'une fibre. Ces tournures annulent le motif.
_ACCES_INFRA = ("acces internet", "accès internet", "lien d'acces",
                "lien d'accès", "point d'acces", "point d'accès",
                "acces ftth", "accès ftth", "acces fibre", "accès fibre",
                "acces adsl", "accès adsl", "acces wan", "accès wan")


# Un SIGNALEMENT de mail frauduleux n'est pas une demande de droits, meme
# quand il parle d'acces et de mot de passe. Le 2026-10-14, la DIT 100177663
# (FACTORHY, « pourriez-vous m'indiquer s'il s'agit d'un mail frauduleux ? »,
# avec « Votre adresse IP doit etre autorisee avant l'acces » recopie du faux
# site) est partie « En attente de validation client ». Elliot : « phishing
# potentiel, il n'y avait pas besoin de demande de validation ». Le client ne
# demande rien a autoriser : il demande une analyse. Ces mots-la coupent le
# circuit avant tout motif.
_SIGNALEMENT_FRAUDE = (
    "frauduleux", "frauduleuse", "phishing", "hameconnage", "hameçonnage",
    "faux mail", "mail suspect", "mail douteux", "courriel suspect",
    "tentative d'arnaque", "arnaque", "usurpation", "usurpe", "usurpé",
    "spam suspect", "mail de phishing", "escroquerie",
)


def _motif_de_validation(texte, motifs):
    """Le texte porte-t-il un motif de validation, hors faux amis ?"""
    bas = (texte or "").lower()
    if any(f in bas for f in _SIGNALEMENT_FRAUDE):
        return False
    for infra in _ACCES_INFRA:                # on neutralise les faux amis
        bas = bas.replace(infra, " ")
    return any(m in bas for m in motifs)


def tag_a_la_creation(code, de, texte):
    """Quel Tag poser a la creation : "attente", "validee", ou None.

    - La demande porte sur des droits / un acces / une delegation, et c'est un
      tiers qui la fait    -> « En attente de validation client ».
    - La meme demande faite par le VALIDEUR LUI-MEME -> « Demande validee »
      tout de suite : il n'a personne a qui demander, c'est lui qui autorise.
      Elliot, le 2026-10-09 : « pour JLI si jamais le demandeur c'est LUIS mets
      directement le tag demande validee ». Avant, ce cas ne posait aucun tag,
      et le ticket partait nu — l'equipe ne pouvait pas distinguer « autorise »
      de « pas encore demande ».
    - Rien a valider -> None.
    """
    v = valideur_du_client(code)
    motifs = ((v or {}).get("motifs") or ()) + MOTIFS_A_VALIDER
    if not _motif_de_validation(texte, motifs):
        return None
    if v and (de or "").strip().lower() in [a.lower() for a in v["adresses"]]:
        return "validee"
    return "attente"


def validation_recue(code, de, texte=None):
    """Ce mail vaut-il validation du client ?

    Deux cas, dans cet ordre :

    - le client a **designe un valideur** et c'est lui qui ecrit : sa reponse
      vaut accord, quel qu'en soit le texte — c'est le cas MYMOBILITY, ou la
      reponse de `dsi@mymobility.fr` sur le fil est l'accord lui-meme ;
    - le client n'a designe personne : n'importe lequel de ses interlocuteurs
      peut confirmer, mais alors le TEXTE doit porter un accord. Sans cette
      seconde condition, une simple question du client ferait passer le tag en
      « Demande validee », et l'equipe lancerait un travail non autorise.

    `texte` est optionnel pour rester compatible avec les appels existants ;
    sans lui, seul le premier cas peut repondre vrai.
    """
    adr = (de or "").strip().lower()
    if not adr:
        return False
    # Un REFUS ne vaut jamais validation, meme ecrit par le valideur designe.
    # Sans ce test, la reponse de Karine DOGNIN SAUZE « je vous confirme que
    # cette demande est refusee » aurait fait passer le tag de la DIT 100175753
    # a « Demande validee » — et l'equipe aurait lance un travail que la
    # cliente venait d'interdire.
    bas = (texte or "").lower()
    if texte is not None and any(n in bas for n in (
            "refus", "refuse", "refusé", "pas d'accord", "je n'autorise",
            "ne valide pas", "non valide", "nous n'autorisons pas")):
        return False
    v = valideur_du_client(code)
    if v and adr in [a.lower() for a in v["adresses"]]:
        return True
    if texte is None:
        return False
    if M.est_interne(adr) or _est_expediteur_automatique(adr):
        return False
    if any(r.search(bas) for r in _ACCORDS):
        return True
    # « Ok », « OK pour moi », « Ok merci » : un accord tient en une ligne.
    corps = bas.split(chr(10), 1)[-1].strip() if chr(10) in bas else bas.strip()
    return len(corps) <= 60 and bool(_OK_SEUL.search(corps) or _OUI_SEUL.search(corps))


def client_du_dit(numero):
    """Code client d'une DIT. `relire_dit` ne le rend pas, l'API si."""
    try:
        base, tok = _artis()
        r = requests.get("%s/dit/%s" % (base, numero), params={"token": tok},
                         headers={"accept": "application/json"}, timeout=30)
        if r.status_code != 200:
            return ""
        c = (r.json() or {}).get("client") or {}
        return str(c.get("code") or c.get("codeClient") or "")
    except Exception:                        # noqa: BLE001
        return ""


def appliquer_regles_client(prop):
    """Applique la regle propre au client, s'il y en a une. Sinon rend `prop`.

    Rien n'est ecrit dans Artis ici : on ne fait que choisir les identifiants
    qui partiront dans le corps de la DIT.
    """
    code = str(prop.get("client") or "")
    regle = REGLES_CLIENT.get(code)
    if not regle:
        return prop

    # Qui est la personne concernee, d'apres le mail ? C'est celle que la
    # recherche habituelle aurait mise en demandeur.
    pid_personne = prop.get("pid_interlocuteur")
    if not pid_personne:
        try:
            t = trouver_demandeur(code, prop.get("de_nom") or "",
                                  prop.get("de") or "")
        except Exception:
            t = None
        if not (t and t.get("id")):
            try:
                t = demandeur_du_texte(code, (prop.get("objet") or "") + chr(10)
                                       + (prop.get("commentaire") or ""))
            except Exception:
                t = None
        pid_personne = (t or {}).get("id")

    prop = dict(prop)
    pid_impose = None
    if regle.get("demandeur_impose"):
        try:
            d = trouver_demandeur(code, "", regle["demandeur_impose"])
        except Exception:
            d = None
        pid_impose = (d or {}).get("id")

    # Quand c'est l'assistante elle-meme qui ecrit, la personne a contacter
    # n'est pas elle : c'est celle POUR QUI la demande est faite, nommee dans
    # le texte. Sans cette reprise, l'intervenant rappellerait l'assistante
    # pour un probleme qu'elle n'a pas.
    if pid_impose and pid_personne == pid_impose:
        try:
            t2 = demandeur_du_texte(code, (prop.get("objet") or "") + chr(10)
                                    + (prop.get("commentaire") or ""))
        except Exception:
            t2 = None
        if t2 and t2.get("id") and t2["id"] != pid_impose:
            pid_personne = t2["id"]

    if regle.get("contact_personne_du_mail") and pid_personne:
        prop["pid_contact"] = pid_personne
    if pid_impose:
        prop["pid_interlocuteur"] = pid_impose
        prop["demandeur_impose_par_regle"] = regle["demandeur_impose"]
    return prop



def creer_dit(prop, confirmer=False, numero_externe=True, identification="tout",
              preaffectation=False, matricule_intervenant=None):
    """Crée le DIT. Sans autorisation, renvoie la SIMULATION sans rien envoyer.

    Retour : {envoye, corps, bloquants, avertissements, numero?, http?, erreur?}
    """
    # Dernier filet sur le demandeur. `traiter()` le cherche déjà, mais tout
    # appelant ne passe pas par lui — un lot écrit à la main, par exemple — et
    # le DIT partait alors sans demandeur alors que le contact EXISTE chez le
    # client. Cas d'Elliot le 2026-09-16 sur le DIT 100173769 : « je vois
    # Laurent Gak dans la signature ». Recherche seule, aucune écriture dans la
    # fiche client : la création de contact reste le travail de `traiter()`.
    prop = appliquer_regles_client(prop)
    if prop.get("client") and not prop.get("pid_interlocuteur"):
        try:
            t = trouver_demandeur(prop["client"], prop.get("de_nom") or "",
                                  prop.get("de") or "")
        except Exception:
            t = None
        if t and t.get("id"):
            prop = dict(prop, pid_interlocuteur=t["id"])
    # Même logique pour le site : Artis refuse une DIT sans site NI bien, et
    # le cache des attributs clients est parfois vide des deux. Le client a
    # pourtant toujours au moins un site dans Artis — on va le chercher plutôt
    # que de renoncer à la demande (2026-09-17, clients 13075 et 29211 bloqués
    # ainsi, l'un après l'autre).
    # On cherche le site meme quand un BIEN est deja connu. Artis documente
    # « codeClient+codeSite OU identifiantFabricantBien », mais chez certains
    # clients le bien seul ne suffit pas : `POST /dit` repond « le site
    # n'existe pas ». JACQUESON AUTOCARS (30703) a bloque 12 passages de suite
    # le 2026-10-09 pour cette raison — tous ses biens sont pourtant valides.
    # Envoyer les deux est sans risque et leve le blocage.
    if prop.get("client") and not prop.get("site"):
        try:
            base, tok = _artis()
            r = requests.get("%s/client/%s/site" % (base, prop["client"]),
                             params={"token": tok},
                             headers={"accept": "application/json"}, timeout=30)
            sites = r.json() if r.status_code == 200 else []
        except Exception:
            sites = []
        # Le site principal d'abord, à défaut le premier de la liste.
        choisi = next((s for s in sites if s.get("principal")),
                      sites[0] if sites else None)
        if choisi and choisi.get("code"):
            prop = dict(prop, site=choisi["code"])
    # Le symptôme, enfin. Sans règle, la création retombe sur « PC/MAC -
    # Autres » : c'est ce qui rendait la moitié des tickets illisibles. On fait
    # LIRE le mail plutôt que d'accepter ce défaut. Si le modèle n'est pas là
    # ou n'est pas sûr, le repli d'origine s'applique comme avant.
    if not prop.get("symptome"):
        try:
            d = M.symptome_par_modele(prop)
        except Exception:
            d = None
        if d and d.get("code"):
            prop = dict(prop, symptome=d["code"],
                        symptome_par_modele=d.get("pourquoi"))
            if not prop.get("uo"):
                prop["uo"] = M.uo_pour(d["code"])
    # Nature et type de DIT. Sans eux, Artis applique SON défaut — et la DIT
    # 100174517 est sortie en « Maintenance / Curatif », ce qu'Elliot a
    # interdit le 2026-09-15 : « ARRÊTE de mettre la nature maintenance
    # curatif, c'est TOUJOURS HOTLINE ». Les propositions bâties à la main
    # (pont du modèle, scripts de lot) ne les portaient pas.
    if not prop.get("nature") or not prop.get("type_dit"):
        from mail_regles import CATEGORIES as _CAT
        defaut = _CAT.get(prop.get("categorie") or "hotline", _CAT["hotline"])
        prop = dict(prop,
                    nature=prop.get("nature") or defaut["nature"],
                    type_dit=prop.get("type_dit") or defaut["type_dit"])
    # LE DOMAINE. Sans lui, Artis applique le domaine par defaut DU CLIENT.
    # Le 2026-09-25, la demande Exchange de CHT INGENIERIE est nee en domaine
    # « Telephonie » parce que l'historique du client l'est : dans cette
    # activite, le type de travail « Hotline » n'existe pas, et la finition a
    # echoue en laissant un ticket Terrain / Automatique. Or cet outil ne
    # produit QUE des demandes informatiques — les autres metiers sont ranges
    # dans leur boite et ne deviennent jamais des tickets ici.
    if not prop.get("domaine"):
        prop = dict(prop, domaine="INFO")
    bloquants, avertis = verifier(prop)
    corps = corps_dit(prop, numero_externe, identification)
    if preaffectation:
        corps["preaffectationIntervenant"] = True
    # Affecter l'intervenant DES LA CREATION. Mesure sur 7 284 DIT : le type de
    # travail suit l'intervenant (ELCOHEN, MTOUATI, SHANNON -> Hotline 100 % ;
    # SAICHOUR, AISSA -> Terrain ~100 % ; sans intervenant -> Terrain 54 %).
    # Artis ne le recalcule pas apres coup, d'ou l'interet de le poser tout de suite.
    if matricule_intervenant:
        corps["matriculeIntervenant"] = matricule_intervenant
    out = {"envoye": False, "corps": corps, "bloquants": bloquants,
           "avertissements": avertis, "arme": ecriture_armee()}

    if bloquants:
        out["erreur"] = "proposition incomplète"
        return out
    if not _autorise(confirmer):
        out["simulation"] = ("aucune requête émise. Pour écrire réellement : "
                             "confirmer=True ET %s contenant OUI" % DRAPEAU)
        return out

    # DERNIER VERROU CONTRE LE DOUBLON. Le registre ne voit que NOS creations :
    # il ne sait rien d'un ticket ouvert a la main par un collegue dans la
    # minute. Le 2026-09-24, le client DIRECT IMPRESSION a perdu l'acces a ses
    # serveurs ; Clifford a saisi la DIT 100175233 a 14h50 pendant que le mail
    # attendait encore en boite, et la remise en circulation du mail aurait
    # produit un second ticket sur le meme incident. On interroge donc Artis
    # lui-meme, qui voit TOUT le monde : meme client, meme jour, meme debut de
    # texte -> c'est le meme incident, on ne recree pas.
    garde = dit_jumelle_detail(prop)
    if garde["numero"]:
        out["erreur"] = "doublon evite"
        out["doublon_de"] = garde["numero"]
        out["numero"] = garde["numero"]
        return out
    if not garde["verifie"]:
        # On cree quand meme — perdre la demande d'un client coute plus cher
        # qu'un doublon — mais ca ne part plus en silence.
        out.setdefault("avertissements", [])
        out["avertissements"] = list(out.get("avertissements") or []) + [
            "ANTI-DOUBLON NON VERIFIE : %s. Le ticket est cree sans ce "
            "controle." % garde["raison"]]
        out["doublon_non_verifie"] = garde["raison"]

    base, tok = _artis()
    r = requests.post("%s/dit" % base, params={"token": tok},
                      headers={"accept": "application/json",
                               "Content-Type": "application/json"},
                      data=json.dumps(corps), timeout=60)
    out["http"] = r.status_code
    out["envoye"] = r.status_code in (200, 201)
    try:
        j = r.json()
    except Exception:
        j = {}
        out["erreur"] = r.text[:300]
    if out["envoye"]:
        out["numero"] = j.get("numero")
        out["reponse"] = j
        # tracer la creation dans le registre, pour le suivi dans le dashboard
        try:
            it0 = ((j.get("inter") or [{}])[0] or {})
            M.registre_ajouter({
                "dit": j.get("numero"),
                "intervention": it0.get("numero"),
                # L'identifiant du MAIL, qui ne se réutilise jamais : c'est lui
                # qui empêchera un second ticket sur la même demande, même si
                # la numérotation des propositions repart de zéro.
                "message_id": prop.get("message_id"),
                # L'identifiant du FIL. Une relance porte un autre message_id
                # mais le MEME conversationId : c'est le seul lien exact entre
                # « RE: ... » et le ticket deja ouvert. Sans lui, le 2026-09-21,
                # la relance HOLDING HBL sur le serveur SAGE a cree la DIT
                # 100174638 alors que la 100174492 etait ouverte sur ce fil.
                "conversation": prop.get("conversation"),
                "client": prop.get("client"),
                "client_lib": prop.get("client_lib"),
                "sujet": prop.get("objet"),
                "proposition": prop.get("id"),
                "symptome": (prop.get("symptome")
                             or symptome_de_repli(prop.get("objet"),
                                                  prop.get("commentaire"))),
                # Signalements demandes par Elliot : ce qui manquait a la
                # creation doit se voir dans l'historique, sinon le filet de
                # securite devient un tapis sous lequel on balaie.
                "symptome_par_defaut": not prop.get("symptome"),
                # Quand le garde-fou n'a pas pu regarder, l'historique doit le
                # dire : c'est la seule facon de retrouver, trois jours plus
                # tard, pourquoi un doublon est passe (GREENWICH, 2026-09-29).
                "doublon_non_verifie": out.get("doublon_non_verifie") or "",
                "sans_demandeur": not prop.get("pid_interlocuteur"),
                "uo_voulue": prop.get("uo"),
                # Demande d'Elliot : un DIT cree pour un client SANS contrat
                # informatique doit etre signale dans l'historique. Cas reel :
                # SRP CONSTRUCTION, dont tous les contrats sont de l'impression
                # — on cree quand meme, mais il veut le savoir.
                "sans_contrat_info": bool(
                    prop.get("client")
                    and prop["client"] not in M.clients_informatiques()),
                # Un contact CRÉÉ dans la fiche du client doit être visible :
                # c'est une écriture qu'Elliot veut voir passer (2026-09-14).
                "demandeur_cree": (prop.get("_demandeur") or {}).get("comment")
                                  if (prop.get("_demandeur") or {}).get("cree") else "",
                "bien": prop.get("bien") or "",
                "variante": ("bien seul" if identification == "bien" else
                             "client+site" if identification == "client" else "tout")
                            + ("" if numero_externe else " · sans n° externe")
                            + (" · préaffectation" if preaffectation else ""),
                "type_a_la_creation": it0.get("typeGpTravail"),
            })
        except Exception:
            pass
    else:
        out["erreur"] = (j.get("message") or j.get("error") or r.text[:300])
    return out


# ═══════════════════════════════════════════════════════ UO de l'intervention
# `POST /dit` ne sait pas fixer l'UO : Artis la calcule seul. On la corrige donc
# juste après création, via PATCH sur l'intervention.
#
# Règle (Elliot, 2026-09-09) : une tâche d'ADMINISTRATION relève du niveau 3.
# L'UO traduit en réalité QUI traite le ticket — HLN3 est l'équipe niveau 3,
# HLI la hotline de premier niveau — donc elle n'est pas déductible du mail.
# Le défaut ci-dessous est une convention, pas une prédiction : l'écran doit
# permettre de le changer.
UO_ADMIN   = "HLN3"       # HotLine Informatique Niv3
UO_HOTLINE = "HLI"        # HotLine Informatique


def uo_pour(prop):
    """UO à poser d'après le symptôme retenu. La règle vit dans mail_dit pour
    que l'écran et l'écriture ne puissent pas diverger."""
    return M.uo_pour(prop.get("symptome"))


def annuler_intervention(numero_it, confirmer=False):
    """PATCH /it/{numeroIt}/annuler — annule une intervention créée à tort.

    PIÈGE : sans `Content-Type: application/json` ET un corps `{}`, Artis
    répond **415 Unsupported Media Type**, alors que l'URL ne prend aucun
    paramètre. Constaté le 2026-09-15 en annulant la DIT 100173967.
    """
    out = {"envoye": False, "arme": ecriture_armee(), "intervention": numero_it}
    if not numero_it:
        out["erreur"] = "intervention manquante"
        return out
    if not _autorise(confirmer):
        out["simulation"] = "PATCH /it/%s/annuler" % numero_it
        return out
    base, tok = _artis()
    r = requests.patch("%s/it/%s/annuler" % (base, numero_it),
                       params={"token": tok},
                       headers={"accept": "application/json",
                                "Content-Type": "application/json"},
                       data="{}", timeout=40)
    out["http"] = r.status_code
    out["envoye"] = r.status_code in (200, 204)
    if not out["envoye"]:
        out["erreur"] = r.text[:200]
    return out


def poser_uo(numero_it, code_uo, confirmer=False):
    """PATCH /it/{numeroIt}/uo/{codeUO}. Simulation si non autorisé."""
    out = {"envoye": False, "arme": ecriture_armee(),
           "intervention": numero_it, "uo": code_uo}
    if not numero_it or not code_uo:
        out["erreur"] = "intervention ou UO manquante"
        return out
    if not _autorise(confirmer):
        out["simulation"] = "aucune requête émise. PATCH /it/%s/uo/%s" % (
            numero_it, code_uo)
        return out
    base, tok = _artis()
    # L'intervention n'est pas interrogeable dans l'instant qui suit la creation
    # du DIT : constate le 2026-09-09, un PATCH immediat renvoie 404. On reessaie.
    import time
    for essai in range(4):
        r = requests.patch("%s/it/%s/uo/%s" % (base, numero_it, code_uo),
                           params={"token": tok},
                           headers={"accept": "application/json"}, timeout=40)
        out["http"] = r.status_code
        out["essais"] = essai + 1
        if r.status_code in (200, 204):
            out["envoye"] = True
            return out
        if r.status_code != 404:
            break
        time.sleep(1.5)
    out["erreur"] = r.text[:300]
    return out


def relire_dit(numero):
    """Relit une DIT pour constater ce qu'Artis a réellement posé."""
    try:
        base, tok = _artis()
        r = requests.get("%s/dit/%s" % (base, numero), params={"token": tok},
                         headers={"accept": "application/json"}, timeout=30)
        if r.status_code != 200:
            return {"erreur": "HTTP %s" % r.status_code}
        d = r.json()
        it = (d.get("inter") or [{}])[0] or {}
        def code(v):
            return v.get("code") if isinstance(v, dict) else v
        return {"etat": d.get("etat"), "date": d.get("date"),
                "commentaire": d.get("commentaire"),
                "intervention": it.get("numero"),
                "type_travail": it.get("typeGpTravail"),
                # Le type de facturation vit sur l'INTERVENTION, pas sur la DIT.
                "facturation": it.get("typeFacturation"),
                "uo": code(it.get("uo")), "priorite": code(it.get("priorite")),
                "domaine": code(it.get("domaine")),
                "symptome": code(d.get("symptome"))}
    except Exception as e:
        return {"erreur": str(e)[:200]}


# Le type de travail attendu selon l'UO : les UO de hotline donnent « Hotline »
# dans 98 à 100 % des cas dans l'historique.
def type_travail_attendu(uo):
    return "Hotline" if (uo or "").upper().startswith(("HL", "HOT", "IMP-HTL")) \
        else None


def registre_enrichi():
    """Registre local + état réel lu dans Artis, ligne par ligne.

    Chaque ligne porte `type_attendu` (déduit de l'UO) et `a_corriger`, pour que
    le tableau signale de lui-même ce qui reste à faire — y compris après une
    correction manuelle, puisque la lecture est faite en direct.
    """
    lignes = M.registre()
    out = []
    for l in lignes:
        r = dict(l)
        reel = relire_dit(l.get("dit")) if l.get("dit") else {"erreur": "sans numéro"}
        r["reel"] = reel
        # Les lignes creees avant ce drapeau n'en ont pas : on le calcule a la
        # lecture, sinon l'historique ne signalerait que les DIT futures.
        if "sans_contrat_info" not in r and l.get("client"):
            r["sans_contrat_info"] = l["client"] not in M.clients_informatiques()
        att = type_travail_attendu(reel.get("uo") or l.get("uo_voulue"))
        r["type_attendu"] = att
        r["a_corriger"] = bool(
            att and reel.get("type_travail") and reel["type_travail"] != att
            and reel.get("etat") not in ("Annulee",))
        out.append(r)
    return out


def bilan_registre(lignes):
    """Compteurs pour l'en-tête du tableau."""
    return {
        "total": len(lignes),
        "en_cours": sum(1 for l in lignes
                        if (l.get("reel") or {}).get("etat") not in ("Annulee", None)),
        "annulees": sum(1 for l in lignes
                        if (l.get("reel") or {}).get("etat") == "Annulee"),
        "a_corriger": sum(1 for l in lignes if l.get("a_corriger")),
        "conformes": sum(1 for l in lignes
                         if not l.get("a_corriger")
                         and (l.get("reel") or {}).get("etat") not in ("Annulee", None)),
    }


# ═══════════════════════════════════════════════════════ boîte mail
# Les dossiers de classement, sous la boite de reception. Le chemin est
# indispensable : trois dossiers portent le nom « 01 - DIT ARTIS SUPPORT »
# (BAL INFO, BAL SECURITE, BAL TELECOM) et la recherche par nom seul tombait
# juste par chance.
DOSSIERS = {
    "traite":       "BAL INFO/01 - DIT ARTIS SUPPORT",
    "informations": "BAL INFO/04 - INFORMATIONS",
    "sauvegarde":   "BAL INFO/06 - SAUVEGARDE",
    "firewall":     "BAL INFO/07 - FIREWALL",
    "antispam":     "BAL INFO/08 - ANTISPAM",
    "rmm":          "BAL INFO/09 - RMM",
    "antivirus":    "BAL INFO/11 - ANTIVIRUS",
    # Alertes SOC déjà traitées par l'équipe sécurité : elles n'ont rien à
    # faire dans la boîte du support (Elliot, 2026-09-14).
    "corbeille":    "deleteditems",
    # Les boîtes des AUTRES métiers, au même niveau que BAL INFO. Un mail de
    # téléphonie y va au lieu de rester dans la file (Elliot, 2026-09-15,
    # P-0266 : « demande téléphonie, ne pas créer, à déplacer dans la
    # BAL TELECOM »).
    "telecom":      "BAL TELECOM",
    "impression":   "BAL IMPRESSION",
    "securite":     "BAL SECURITE",
    "devis":        "BAL INFO/03 - DEVIS",
    "terrain":      "BAL INFO/02 - DIT ARTIS TERRAIN",
    # La reponse du valideur du client a sa place a elle : le dossier ou
    # l'equipe va chercher les accords (Elliot, 2026-10-06, « range les
    # mails de validation client dans 14 - Validation clients au lieu de
    # ARTIS DIT SUPPORT »).
    "validation":   "BAL INFO/14 - VALIDATION CLIENT",
    # Une COPIE de chaque reponse de client a une DIT, pour que l'equipe
    # suive ces echanges sans ouvrir Artis (Elliot, le 2026-10-13 : «
    # lorsque le client repond a une DIT et que tu transfere au dernier
    # intervenant faudrait aussi que tu transfere une copie du mail dans le
    # dossier 16 - REPONSE DIT pour qu'on puisse le suivre »). L'original
    # continue sa route vers 01 ou 14 : c'est une copie, pas un detour.
    "reponse_dit":  "BAL INFO/16 - REPONSE DIT",
}

_CACHE_DOSSIER = {}


def id_dossier(tok, chemin):
    """Identifiant d'un dossier designe par son CHEMIN sous la boite de
    reception, par exemple « BAL INFO/04 - INFORMATIONS »."""
    # La corbeille n'est pas sous la boîte de réception : c'est un dossier
    # standard, désigné par son nom réservé.
    if chemin in ("deleteditems", "corbeille"):
        return "deleteditems"
    if chemin in _CACHE_DOSSIER:
        return _CACHE_DOSSIER[chemin]
    courant, _ = M._id_boite(tok)
    for morceau in [x.strip() for x in chemin.split("/") if x.strip()]:
        r = M._get(tok, "%s/me/mailFolders/%s/childFolders" % (GRAPH, courant),
                   {"$top": "100", "$select": "id,displayName"})
        if r.status_code != 200:
            return None
        suivant = None
        for f in (r.json().get("value") or []):
            if (f.get("displayName") or "").strip().lower() == morceau.lower():
                suivant = f["id"]
                break
        if not suivant:
            return None
        courant = suivant
    _CACHE_DOSSIER[chemin] = courant
    return courant


def ranger_mail(prop, cle_ou_chemin, confirmer=False, marquer_lu=True):
    """Marque le mail comme lu puis le classe dans le dossier demande.

    Sert aux mails qui ne donnent PAS de ticket : alertes, informations. Le
    marquage precede le deplacement, qui change l'identifiant du message.
    """
    out = {"envoye": False, "arme": ecriture_armee()}
    chemin = DOSSIERS.get(cle_ou_chemin, cle_ou_chemin)
    out["destination"] = chemin
    mail_id = prop.get("mail_id") if isinstance(prop, dict) else prop
    if not mail_id:
        out["erreur"] = "pas d'identifiant de mail"
        return out
    tok = M.access_token()
    if not tok:
        out["erreur"] = "pas de jeton mail"
        return out
    fid = id_dossier(tok, chemin)
    if not fid:
        out["erreur"] = "dossier « %s » introuvable" % chemin
        return out
    if not _autorise(confirmer):
        out["simulation"] = "PATCH isRead + POST move vers %s" % chemin
        return out
    if marquer_lu:
        try:
            lu = requests.patch("%s/me/messages/%s" % (GRAPH, mail_id),
                                headers={"Authorization": "Bearer " + tok,
                                         "Content-Type": "application/json"},
                                data=json.dumps({"isRead": True}), timeout=30)
            out["marque_lu"] = lu.status_code in (200, 201)
        except Exception as e:
            out["marque_lu"] = False
            out["erreur_lu"] = str(e)[:120]
    r = requests.post("%s/me/messages/%s/move" % (GRAPH, mail_id),
                      headers={"Authorization": "Bearer " + tok,
                               "Content-Type": "application/json"},
                      data=json.dumps({"destinationId": fid}), timeout=40)
    out["http"] = r.status_code
    out["envoye"] = r.status_code in (200, 201)
    if not out["envoye"]:
        out["erreur"] = r.text[:300]
    return out


def copier_mail(mail_id, cle_ou_chemin, confirmer=False):
    """Depose une COPIE du mail dans un dossier, sans toucher a l'original.

    `ranger_mail` DEPLACE : apres lui le message a change d'identifiant et
    n'est plus la ou on l'attend. Pour qu'un mail vive a deux endroits — sa
    place de rangement et un dossier de suivi — il faut copier AVANT de
    deplacer. Graph expose `/copy` exactement pour ca.
    """
    out = {"envoye": False, "arme": ecriture_armee()}
    chemin = DOSSIERS.get(cle_ou_chemin, cle_ou_chemin)
    out["destination"] = chemin
    if not mail_id:
        out["erreur"] = "pas d'identifiant de mail"
        return out
    tok = M.access_token()
    if not tok:
        out["erreur"] = "pas de jeton mail"
        return out
    fid = id_dossier(tok, chemin)
    if not fid:
        out["erreur"] = "dossier " + " %s " % chemin + "introuvable"
        return out
    if not _autorise(confirmer):
        out["simulation"] = "POST copy vers %s" % chemin
        return out
    r = requests.post("%s/me/messages/%s/copy" % (GRAPH, mail_id),
                      headers={"Authorization": "Bearer " + tok,
                               "Content-Type": "application/json"},
                      data=json.dumps({"destinationId": fid}), timeout=40)
    out["http"] = r.status_code
    out["envoye"] = r.status_code in (200, 201)
    if not out["envoye"]:
        out["erreur"] = r.text[:300]
    return out


def deplacer_mail(prop, confirmer=False):
    """Déplace le mail vers « 01 - DIT ARTIS SUPPORT ».

    À n'appeler QU'APRÈS création réussie du DIT : dans l'autre sens, le mail
    quitterait la boîte sans ticket derrière.

    Marque `deplace_par_outil` — et non `confirme_humain` : sinon l'outil
    interpréterait son propre déplacement comme un traitement humain et
    fausserait sa propre mesure.
    """
    out = {"envoye": False, "arme": ecriture_armee()}
    if not prop.get("dit_numero"):
        out["erreur"] = ("aucun DIT rattaché : on ne sort pas un mail de la "
                         "boîte sans ticket derrière")
        return out

    tok = M.access_token()
    if not tok:
        out["erreur"] = "pas de jeton mail"
        return out

    fid = id_dossier(tok, DOSSIERS["traite"]) or _id_dossier_traite(tok)
    if not fid:
        out["erreur"] = "dossier « %s » introuvable" % DOSSIERS["traite"]
        return out
    out["destination"] = {"nom": M.DOSSIER_TRAITE, "id": fid}
    out["mail_id"] = prop.get("mail_id")

    if not _autorise(confirmer):
        out["simulation"] = ("aucune requête émise. POST /me/messages/%s/move "
                             "vers %s" % (prop.get("mail_id"), M.DOSSIER_TRAITE))
        return out

    # Marquer comme lu AVANT le deplacement : `move` rend un NOUVEL identifiant
    # de message, donc le faire apres obligerait a le recuperer. Un echec ici
    # ne doit pas empecher le deplacement, qui est l'essentiel.
    try:
        lu = requests.patch("%s/me/messages/%s" % (GRAPH, prop["mail_id"]),
                            headers={"Authorization": "Bearer " + tok,
                                     "Content-Type": "application/json"},
                            data=json.dumps({"isRead": True}), timeout=30)
        out["marque_lu"] = lu.status_code in (200, 201)
    except Exception as e:
        out["marque_lu"] = False
        out["erreur_lu"] = str(e)[:120]

    def _bouger(ident):
        return requests.post("%s/me/messages/%s/move" % (GRAPH, ident),
                             headers={"Authorization": "Bearer " + tok,
                                      "Content-Type": "application/json"},
                             data=json.dumps({"destinationId": fid}), timeout=40)

    r = _bouger(prop["mail_id"])
    if r.status_code == 404 and prop.get("message_id"):
        # L'identifiant Graph d'un message CHANGE a chaque deplacement ;
        # `internetMessageId`, lui, ne change jamais. Une proposition dont le
        # mail a ete deplace une fois (a la main, ou par une correction de
        # classement) porte donc un identifiant mort, et le rangement echouait
        # en silence : le ticket existait, son mail restait en boite. Constate
        # le 2026-09-22 sur P-1035 / DIT 100174874.
        q = M._get(tok, "%s/me/messages" % GRAPH,
                   {"$filter": "internetMessageId eq '%s'" % prop["message_id"],
                    "$select": "id", "$top": "1"})
        vals = (q.json() or {}).get("value") if q.status_code == 200 else None
        if vals:
            out["mail_id_retrouve"] = True
            r = _bouger(vals[0]["id"])
    out["http"] = r.status_code
    out["envoye"] = r.status_code in (200, 201)
    if not out["envoye"]:
        out["erreur"] = r.text[:300]
    return out


def _id_dossier_traite(tok):
    """Identifiant du dossier des mails traités (recherche dans l'arborescence)."""
    cible = M.DOSSIER_TRAITE.strip().lower()
    file = [GRAPH + "/me/mailFolders?$top=100&$select=id,displayName,childFolderCount"]
    while file:
        r = M._get(tok, file.pop(0))
        if r.status_code != 200:
            return None
        j = r.json()
        for f in j.get("value", []):
            if (f.get("displayName") or "").strip().lower() == cible:
                return f["id"]
            if f.get("childFolderCount", 0):
                file.append("%s/me/mailFolders/%s/childFolders"
                            "?$top=100&$select=id,displayName,childFolderCount"
                            % (GRAPH, f["id"]))
        if j.get("@odata.nextLink"):
            file.append(j["@odata.nextLink"])
    return None


# ═══════════════════════════════════════════════════════ enchaînement
def joindre_fichier(numero, nom, contenu, type_mime="application/octet-stream",
                    confirmer=False):
    """Attache UN fichier au DIT, sans passer par les pièces d'un mail.

    Sert au cas de la quarantaine : Elliot, 2026-09-16 — « si tu peux mettre le
    mail en question en PJ directement dans la DIT, ce serait parfait ». Le
    mail bloqué n'est pas une pièce jointe du message reçu, c'est le message
    lui-même, récupéré en MIME brut.
    """
    out = {"nom": nom, "envoye": False}
    if not _autorise(confirmer):
        out["erreur"] = "écriture non armée : %s" % DRAPEAU
        return out
    if not contenu:
        out["erreur"] = "contenu vide"
        return out
    base, art = _artis()
    try:
        r = requests.post("%s/documents/DIT/%s" % (base, numero),
                          params={"token": art},
                          files={"content": (nom, contenu, type_mime)},
                          timeout=120)
        out["http"] = r.status_code
        out["envoye"] = r.status_code in (200, 201, 204)
        if not out["envoye"]:
            out["erreur"] = r.text[:200]
    except Exception as e:
        out["erreur"] = str(e)[:200]
    return out


def joindre_pieces(numero, prop, confirmer=False, aussi=None):
    """Attache au DIT les pieces jointes du mail d'origine.

    Soumis aux memes deux verrous que toute ecriture. Tolerant a l'echec : une
    piece qui ne passe pas ne doit pas faire echouer la creation du DIT, qui
    est deja faite a ce stade — on remonte l'erreur, l'agent la verra.
    """
    out = {"envoye": False, "pieces": []}
    if not _autorise(confirmer):
        out["erreur"] = "ecriture non armée : %s" % DRAPEAU
        return out
    tok = M.access_token()
    if not tok:
        out["erreur"] = "pas de jeton mail"
        return out
    # Les mails regroupes font partie de la MEME demande : leurs photos aussi.
    sources = [prop] + list(aussi or [])
    pieces = []
    for src in sources:
        # Le fil sert a APPRENDRE les tailles de logos qui reviennent d'un
        # message a l'autre : sans lui, le filtre des signatures recurrentes ne
        # dispose d'aucune memoire (constate le 2026-09-21 sur HOLDING HBL).
        for p in M.pieces_du_mail(tok, src.get("mail_id"),
                                  sans_texte=M.corps_muet(src.get("commentaire")),
                                  conversation=src.get("conversation")):
            p = dict(p)
            p["mail_id"] = src.get("mail_id")
            p["proposition"] = src.get("id")
            pieces.append(p)
    if not pieces:
        out["inutile"] = "aucune piece a joindre (logos de signature ecartes)"
        return out

    base, art = _artis()
    for p in pieces:
        contenu = M.contenu_piece(tok, p["mail_id"], p["id"])
        if not contenu:
            out["pieces"].append({"nom": p["nom"], "envoye": False,
                                  "erreur": "contenu illisible"})
            continue
        try:
            r = requests.post("%s/documents/DIT/%s" % (base, numero),
                              params={"token": art},
                              files={"content": (p["nom"], contenu,
                                                 p.get("type") or "application/octet-stream")},
                              timeout=120)
            ok = r.status_code in (200, 201, 204)
            out["pieces"].append({"nom": p["nom"], "taille": p.get("taille"),
                                  "envoye": ok, "http": r.status_code,
                                  "erreur": None if ok else r.text[:200]})
        except Exception as e:
            out["pieces"].append({"nom": p["nom"], "envoye": False,
                                  "erreur": str(e)[:200]})
    out["envoye"] = any(x.get("envoye") for x in out["pieces"])
    return out


# Ce que doit porter une DIT nee d'un mail. Elliot, 2026-09-22 : « je veux que
# ce soit cree a partir de maintenant en typeFacturation non facturable ».
# L'API REST ne sait pas ecrire ce champ (`POST /dit` ne l'accepte pas, et
# aucune route PATCH ne le touche sur une intervention ouverte : la seule qui
# l'ecrive est `saisirCrit`, le compte rendu de cloture). Le formulaire web,
# lui, le porte — `cb_typeFactu`, dans le meme bloc deplie que le type de
# travail. On pose donc les deux dans la MEME session.
FACTURATION_VOULUE = "Non facturable"


def corriger_type_travail(numero, attendu, confirmer=False, facturation=None):
    """Pose le type de travail attendu via le formulaire web Artis.

    L'API REST ne sait pas écrire ce champ (21 hypothèses au journal). Le
    formulaire, si — cf. `artis_type_travail.py`, qui vérifie par relecture
    REST qu'aucun autre champ ne bouge.

    Soumis aux mêmes deux verrous que toute écriture : `confirmer` ET le
    drapeau sur disque. Et tolérant à l'échec : une correction impossible
    (session absente, DIT clôturée) ne doit jamais faire échouer la création du
    DIT, qui est déjà faite à ce stade. Le signalement à l'écran reprend alors
    la main.
    """
    if not _autorise(confirmer):
        return {"ok": False, "envoye": False,
                "erreur": "écriture non armée : %s" % DRAPEAU}
    try:
        import artis_type_travail as ATT
    except Exception as e:
        return {"ok": False, "erreur": "module indisponible : %s" % str(e)[:120]}
    try:
        w = ATT.Web.ouvrir()
        r = ATT.poser_type_travail(numero, attendu, confirmer=True, web=w,
                                   aussi=({"cb_typeFactu": facturation}
                                          if facturation else None))
        r["envoye"] = True
        return r
    except Exception as e:
        return {"ok": False, "envoye": False, "erreur": str(e)[:200]}


def _mots_nom(txt):
    """Les mots significatifs d'un nom, sans accents ni ponctuation."""
    import unicodedata
    t = unicodedata.normalize("NFD", (txt or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    petits = {"de", "du", "des", "le", "la", "les", "d", "l", "van", "von"}
    return [w for w in t.split() if len(w) > 1 and w not in petits]


def interlocuteurs_artis(code_client):
    """Les contacts du client chez Artis, avec leur mail quand il existe."""
    base, tok = _artis()
    r = requests.get("%s/client/%s/interlocuteur" % (base, code_client),
                     params={"token": tok},
                     headers={"accept": "application/json"}, timeout=40)
    if r.status_code != 200:
        return []
    out = []
    for x in (r.json() or []):
        a = x.get("adresse") or {}
        out.append({"id": x.get("id"), "nom": x.get("nom") or "",
                    "prenom": x.get("prenom") or "", "titre": x.get("titre") or "",
                    "mail": (a.get("mail1") or a.get("mail2") or "").strip().lower()})
    return out


def trouver_demandeur(code_client, de_nom="", de_adresse=""):
    """Retrouve le demandeur chez le client, MÊME si son domaine mail diffère.

    Elliot (2026-09-14, P-0186) : « c'est un ticket RAISE, le demandeur est
    romain.broutier@bfpatrimoine.fr, ça porte à confusion car domaine
    différent ; regarde sur le client RAISE s'il y a aussi un Romain Broutier,
    sinon ajoute l'adresse bfpatrimoine comme nouveau demandeur. »

    Trois passes, de la plus sûre à la moins sûre :
      1. l'adresse mail exacte ;
      2. le NOM de famille, pris dans le nom d'affichage de l'expéditeur — un
         prestataire comptable garde son nom, pas le domaine de son client ;
      3. rien : à l'appelant de créer le contact.
    """
    gens = interlocuteurs_artis(code_client)
    adr = (de_adresse or "").strip().lower()
    for g in gens:
        if adr and g["mail"] == adr:
            return {"id": g["id"], "contact": g, "comment": "adresse mail exacte"}
    mots = set(_mots_nom(de_nom))
    if not mots:
        # à défaut du nom d'affichage, la partie locale : « romain.broutier »
        mots = set(_mots_nom(adr.split("@")[0].replace(".", " ")))
    candidats = []
    for g in gens:
        n = set(_mots_nom(g["nom"] + " " + g["prenom"]))
        if n and n & mots:
            candidats.append((len(n & mots), g))
    if not candidats:
        return None
    candidats.sort(key=lambda x: -x[0])
    if len(candidats) > 1 and candidats[0][0] == candidats[1][0]:
        # Deux homonymes : on ne tranche pas à la place de l'agent.
        return {"id": None, "ambigu": [c[1] for c in candidats[:4]],
                "comment": "plusieurs contacts portent ce nom"}
    g = candidats[0][1]
    return {"id": g["id"], "contact": g,
            "comment": "même nom chez ce client (%s), domaine mail différent"
                       % (" ".join(x for x in (g["titre"], g["prenom"], g["nom"]) if x).strip())}


_RE_MOT_TEXTE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-]{3,}")


def demandeur_du_texte(code_client, texte):
    """Le demandeur NOMMÉ dans le mail, quand ce n'est pas l'expéditeur.

    Elliot, 2026-09-18, sur le DIT 100174583 : « il n'a pas mis le contact
    alors qu'il y a bien rfitoussi dans le message ». L'objet disait
    « MDP SESSION LA SENI RFITOUSSI » et le client a un contact
    `Rfitoussi@laseni.fr` — Roger FITOUSSI. La recherche ne regardait que
    l'expéditeur.

    Deux passes, de la plus sûre à la moins sûre :
      1. la PARTIE LOCALE d'une adresse de contact citée telle quelle
         (« rfitoussi ») : c'est un identifiant, il ne désigne qu'une personne ;
      2. le NOM de famille, et seulement s'il ne désigne qu'un seul contact —
         ce client compte cinq FITOUSSI, le nom seul ne tranche pas.
    """
    if not code_client or not texte:
        return None
    mots = {w.lower() for w in _RE_MOT_TEXTE.findall(texte)}
    if not mots:
        return None
    gens = interlocuteurs_artis(code_client)
    for g in gens:
        local = (g.get("mail") or "").split("@")[0].lower()
        if len(local) >= 5 and local in mots:
            return {"id": g["id"], "contact": g,
                    "comment": "identifiant « %s » cité dans le mail" % local}
    par_nom = {}
    for g in gens:
        n = (g.get("nom") or "").strip().lower()
        if len(n) >= 4:
            par_nom.setdefault(n, []).append(g)
    for n, lot in par_nom.items():
        if n in mots and len(lot) == 1:
            return {"id": lot[0]["id"], "contact": lot[0],
                    "comment": "nom « %s » cité dans le mail" % n.upper()}
    return None


def creer_interlocuteur(code_client, nom, prenom="", mail="", titre="M.",
                        confirmer=False):
    """Crée le contact chez le client. ÉCRIT dans la fiche client Artis.

    Autorisé par Elliot le 2026-09-14 (« si le demandeur existe pas, crée-le »)
    après l'avoir refusé le 11 — la différence : on ne crée QUE si la recherche
    par nom n'a rien donné, et la création est tracée dans le registre pour
    qu'il la voie.
    """
    if not confirmer or not _autorise(confirmer):
        return {"erreur": "écriture non armée : %s" % DRAPEAU}
    if not (nom or "").strip():
        return {"erreur": "nom manquant"}
    base, tok = _artis()
    corps = {"titre": titre, "nom": _stockable(nom.strip().upper()),
             "prenom": _stockable((prenom or "").strip().title()),
             "adresse": {"mail1": (mail or "").strip()}}
    r = requests.post("%s/client/%s/interlocuteur" % (base, code_client),
                      params={"token": tok},
                      headers={"accept": "application/json",
                               "Content-Type": "application/json"},
                      data=json.dumps(corps, ensure_ascii=False).encode("utf-8"),
                      timeout=60)
    if r.status_code not in (200, 201):
        return {"erreur": "HTTP %s : %s" % (r.status_code, r.text[:200])}
    # Artis rend l'identifiant du contact cree en ENTIER NU, pas en objet JSON.
    # Constate le 2026-09-15 : `j.get("id")` levait « 'int' object has no
    # attribute 'get' » et faisait echouer toute la creation de la DIT.
    try:
        j = r.json()
    except ValueError:
        j = None
    if isinstance(j, dict):
        ident = j.get("id")
    elif isinstance(j, int):
        ident = j
    else:
        ident = None
        m = re.search(r"\d+", (r.text or "").strip()[:40])
        if m:
            ident = int(m.group(0))
    return {"id": ident, "cree": True, "reponse": j if j is not None else r.text[:200]}


_ROBOTS = ("no-reply", "noreply", "donotreply", "ne-pas-repondre", "nepasrepondre",
           "alerts@", "alert@", "alerte@", "notification", "notifications@",
           "mailer@", "mailauto@", "jira@", "postmaster@", "bounce", "automat",
           "@rmm.", "@notifications.", "scanbcs", "no.reply")


def _est_expediteur_automatique(adr):
    """L'adresse est-elle celle d'un robot plutot que d'une personne ?

    Garde-fou pose le 2026-10-09 : la DIT 100177035 (UNILET 12124) est partie
    avec `alerts@rmm.datto.com` ecrit sur la fiche de Benedicte LESUEUR. Le
    ticket venait d'une notification Datto RMM qui PORTAIT la demande de la
    cliente, donc le demandeur etait bien elle — mais `creer_dit` pose
    l'adresse de l'EXPEDITEUR, et l'expediteur etait le robot. Une fiche client
    polluee de la sorte ne se voit pas : Artis affiche simplement un mauvais
    mail de demandeur sur toutes les DIT suivantes.

    On refuse donc d'ecrire une adresse qui porte une marque de robot, comme on
    refuse deja les adresses internes. Quand le demandeur est une vraie personne
    dont on n'a pas l'adresse, mieux vaut une fiche sans mail qu'une fiche
    fausse.
    """
    a = (adr or "").strip().lower()
    return any(m in a for m in _ROBOTS)


def assurer_mail_interlocuteur(code_client, pid, mail, confirmer=False):
    """Pose l'adresse du mail sur la fiche du contact quand elle est VIDE.

    Artis n'affiche pas l'adresse du demandeur depuis le message : il la lit
    dans la fiche de l'interlocuteur. Un contact saisi a la main sans adresse
    donne donc une DIT sans mail de demandeur — constate le 2026-10-06 sur la
    DIT 100176656 (Alexandre Therrouin, CAB'S INDUSTRIES, fiche avec le seul
    telephone). Elliot : « il y a un mail avec une demande et le mail du
    demandeur, juste tu l'ajoutes, et s'il existe pas tu le crees ».

    On COMPLETE, on ne remplace jamais : une fiche qui porte deja une adresse
    garde la sienne, meme differente de celle du message — c'est peut-etre
    l'adresse nominative, et le message peut venir d'une boite collective.
    Jamais d'adresse interne @dfm.fr dans la fiche d'un client.

    Rend {deja, pose, erreur} ; `pose` porte l'adresse ecrite.
    """
    adr = (mail or "").strip()
    if not adr or "@" not in adr:
        return {"erreur": "adresse absente ou invalide"}
    if M.est_interne(adr):
        return {"erreur": "adresse interne, jamais dans la fiche d'un client"}
    if _est_expediteur_automatique(adr):
        return {"erreur": "expediteur automatique, jamais dans la fiche d'un"
                          " client : %s" % adr}
    base, tok = _artis()
    url = "%s/client/%s/interlocuteur/%s" % (base, code_client, pid)
    r = requests.get(url, params={"token": tok}, timeout=60)
    if r.status_code != 200:
        return {"erreur": "lecture HTTP %s" % r.status_code}
    fiche = r.json() or {}
    deja = ((fiche.get("adresse") or {}).get("mail1") or "").strip()
    if deja:
        return {"deja": deja}
    if not confirmer or not _autorise(confirmer):
        return {"erreur": "ecriture non armee : %s" % DRAPEAU}
    # Le PUT remplace la fiche : on renvoie TOUT ce qu'on a lu, sinon le
    # telephone et la fonction disparaissent.
    corps = {k: v for k, v in fiche.items() if k != "links"}
    corps["adresse"] = dict(corps.get("adresse") or {}, mail1=_stockable(adr))
    w = requests.put(url, params={"token": tok},
                     headers={"Content-Type": "application/json;charset=UTF-8"},
                     data=json.dumps(corps, ensure_ascii=False).encode("utf-8"),
                     timeout=60)
    if w.status_code not in (200, 201, 204):
        return {"erreur": "HTTP %s : %s" % (w.status_code, w.text[:200])}
    return {"pose": adr}


def _adresse_nominative(iv):
    """L'adresse PERSONNELLE d'un intervenant, jamais une boîte collective.

    Artis renseigne souvent `support@dfm.fr` comme mail de fiche : transférer
    là-dessus depuis la boîte du support serait une boucle.

    INCIDENT DU 2026-09-14 — la première version sautait la PERSONNE quand son
    adresse de fiche était collective, et l'appelant remontait alors à
    l'intervenant précédent. Sur la DIT 100172447, la dernière intervenante est
    Shannon COHEN (fiche : `support@dfm.fr`) ; le transfert est parti chez
    Valentin GARCIA, qui n'avait rien à voir avec les deux dernières
    interventions. On ne saute plus jamais quelqu'un : on cherche son adresse,
    et si on ne la trouve pas, on ne transfère pas.

    Quatre sources, de la plus sûre à la moins sûre, la dernière étant
    l'annuaire du tenant interrogé par le NOM (`Shannon COHEN` ->
    `scohen@dfm.fr`). Une adresse déduite n'est retenue que si elle existe.
    """
    from mail_regles import BAL_COLLECTIVES

    def nominative(a):
        a = (a or "").strip().lower()
        return a if ("@" in a and a.split("@")[0] not in BAL_COLLECTIVES) else ""

    a = nominative((iv.get("adresse") or {}).get("mail1"))
    if a:
        return a
    mat = (iv.get("matricule") or "").strip().lower()
    a = nominative(mat)
    if a:
        return a
    if mat and mat.isalnum():
        devinee = "%s@dfm.fr" % mat
        if _existe_dans_annuaire(devinee):
            return devinee
    nom = " ".join(x for x in (iv.get("prenom"), iv.get("nom")) if x).strip()
    return _adresse_par_nom(nom) if nom else ""


_CACHE_NOMS = {}


def _adresse_par_nom(nom):
    """Cherche une personne dans l'annuaire du tenant par son nom affiché.

    Ne rend une adresse que si UNE SEULE personne correspond : deux homonymes,
    c'est un mail envoyé au hasard.
    """
    if nom in _CACHE_NOMS:
        return _CACHE_NOMS[nom]
    trouve = ""
    try:
        tok = M.access_token()
        if tok:
            r = requests.get(
                "%s/users" % M.GRAPH,
                headers={"Authorization": "Bearer " + tok,
                         "ConsistencyLevel": "eventual"},
                params={"$search": '"displayName:%s"' % nom,
                        "$select": "displayName,mail,userPrincipalName",
                        "$top": "10"}, timeout=30)
            if r.status_code == 200:
                cible = _mots_nom(nom)
                exacts = [u for u in (r.json().get("value") or [])
                          if _mots_nom(u.get("displayName")) == cible]
                if len(exacts) == 1:
                    u = exacts[0]
                    trouve = (u.get("mail") or u.get("userPrincipalName") or "").lower()
    except Exception:
        trouve = ""
    _CACHE_NOMS[nom] = trouve
    return trouve


_CACHE_ANNUAIRE = {}


def _existe_dans_annuaire(upn):
    """L'adresse existe-t-elle dans le tenant ? (évite d'écrire dans le vide)"""
    if upn in _CACHE_ANNUAIRE:
        return _CACHE_ANNUAIRE[upn]
    ok = False
    try:
        tok = M.access_token()
        if tok:
            r = requests.get("%s/users/%s" % (M.GRAPH, upn),
                             headers={"Authorization": "Bearer " + tok},
                             params={"$select": "id"}, timeout=25)
            ok = r.status_code == 200
    except Exception:
        ok = False
    _CACHE_ANNUAIRE[upn] = ok
    return ok


# Quand un DIT n'a personne à qui répondre, le mail ne reste pas en boîte :
# il part chez Clifford. Règle d'Elliot du 2026-09-16, après la relance
# « TRES URGENT » de NEURAXPHARM sur le DIT 100174012, qui n'avait aucun
# intervenant affecté : « quand pas d'intervenant tu envoies à Clifford et tu
# ranges le mail ».
RELAIS = {"mail": "cyapnewkpin@dfm.fr", "nom": "Clifford YAP NEWK PIN"}

# Le commercial a qui revient un client sans contrat d'infogerance. Elliot,
# 2026-10-02 : « il faudrait plutot transferer au commercial qui est assigne au
# client ». Celine d'Amato reste le repli quand la fiche n'en designe aucun.
COMMERCE_REPLI = {"mail": "cdamato@dfm.fr", "nom": "Celine D'AMATO"}


def commercial_informatique(code_client):
    """Le commercial informatique du client, d'apres sa fiche Artis.

    La donnee est dans les CLASSIFICATIONS de la fiche (onglet « Classifications »
    de l'ecran client) : `categ[].classification.abrev == "COMINF"`, et l'`abrev`
    de la categorie EST le matricule — JKTORZA, SVOCI, CGALLOT...

    Du matricule a l'adresse : `matricule.lower() + "@dfm.fr"` est la convention
    du tenant, mais on ne la DEVINE pas — on la verifie dans l'annuaire, et on
    exige un compte actif. Un commercial parti garde sa ligne dans Artis des
    annees ; lui ecrire, c'est ecrire dans le vide.

    Un client peut porter plusieurs COMINF quand il appartient a plusieurs
    organisations internes (AREAL : CGALLOT pour DFM, AELKAIM pour MOO). On ne
    tranche pas : le premier est le destinataire, les autres passent en copie.

    Rend {principal, copies, matricules, raison} — `principal` est None quand la
    fiche ne designe personne de joignable.
    """
    out = {"principal": None, "copies": [], "matricules": [], "raison": ""}
    try:
        base, tok = _artis()
        r = requests.get("%s/client/%s" % (base, code_client),
                         params={"token": tok},
                         headers={"accept": "application/json"}, timeout=40)
        if r.status_code != 200:
            out["raison"] = "fiche client %s : HTTP %s" % (code_client, r.status_code)
            return out
        fiche = r.json() or {}
    except Exception as exc:                       # noqa: BLE001
        out["raison"] = "fiche client illisible : %s" % str(exc)[:120]
        return out

    vus, mats = set(), []
    for c in (fiche.get("categ") or []):
        if ((c.get("classification") or {}).get("abrev") or "").upper() != "COMINF":
            continue
        m = (c.get("abrev") or "").strip()
        if m and m.upper() not in vus:
            vus.add(m.upper())
            mats.append(m)
    out["matricules"] = mats
    if not mats:
        out["raison"] = "aucun commercial informatique sur la fiche"
        return out

    joignables = []
    for m in mats:
        upn = m.lower() + "@dfm.fr"
        qui = _annuaire_actif(upn)
        if qui:
            joignables.append(qui)
    if not joignables:
        out["raison"] = ("commercial(aux) %s : aucun compte actif dans l'annuaire"
                         % ", ".join(mats))
        return out
    out["principal"] = joignables[0]
    out["copies"] = joignables[1:]
    return out


_CACHE_ACTIF = {}


def _annuaire_actif(upn):
    """{mail, nom} si le compte existe ET est actif, sinon None."""
    if upn in _CACHE_ACTIF:
        return _CACHE_ACTIF[upn]
    qui = None
    try:
        tok = M.access_token()
        if tok:
            r = requests.get("%s/users/%s" % (M.GRAPH, upn),
                             headers={"Authorization": "Bearer " + tok},
                             params={"$select": "displayName,mail,"
                                                "userPrincipalName,accountEnabled"},
                             timeout=25)
            if r.status_code == 200:
                j = r.json() or {}
                if j.get("accountEnabled"):
                    qui = {"mail": (j.get("mail") or j.get("userPrincipalName")
                                    or upn).lower(),
                           "nom": j.get("displayName") or upn}
    except Exception:
        qui = None
    _CACHE_ACTIF[upn] = qui
    return qui


def dernier_intervenant(numero_dit):
    """Le dernier technicien affecté sur cette DIT, avec son mail.

    À défaut d'intervenant joignable, `RELAIS` (Clifford) — jamais rien.

    Introuvable par le chemin évident : `GET /dit/{n}` ne renvoie PAS
    l'intervenant, et `GET /it/{n}/intervenant` répond 405 (le verbe n'existe
    qu'en écriture). Il est en revanche présent dans les LISTES —
    `GET /client/{code}/dit` rend `inter[].intervenant` avec `adresse.mail1`.
    Deux appels donc : le DIT pour connaître son client et sa date, puis la
    liste de ce client autour de cette date.
    """
    base, tok = _artis()
    r = requests.get("%s/dit/%s" % (base, numero_dit), params={"token": tok},
                     headers={"accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        return {"erreur": "DIT %s : HTTP %s" % (numero_dit, r.status_code)}
    d = r.json() or {}
    code = ((d.get("client") or {}).get("code") or "").strip()
    jour = (d.get("date") or "")[:10]
    if not code or not jour:
        return {"erreur": "client ou date absents du DIT %s" % numero_dit}
    j0 = datetime.date.fromisoformat(jour)
    r2 = requests.get("%s/client/%s/dit" % (base, code),
                      params={"token": tok,
                              "dateDebut": (j0 - datetime.timedelta(days=1)).isoformat(),
                              "dateFin": (j0 + datetime.timedelta(days=1)).isoformat(),
                              "maxResults": 200},
                      headers={"accept": "application/json"}, timeout=40)
    if r2.status_code != 200:
        return {"erreur": "liste client %s : HTTP %s" % (code, r2.status_code)}
    for dit in (r2.json() or []):
        if str(dit.get("numero")) != str(numero_dit):
            continue
        # LA dernière intervention qui porte un intervenant — on s'arrête là,
        # même si son adresse est introuvable. Remonter à l'intervenant
        # précédent enverrait le mail à quelqu'un qui n'est plus sur le dossier
        # (incident du 2026-09-14, cf. `_adresse_nominative`).
        for it in sorted(dit.get("inter") or [],
                         key=lambda x: (x.get("dateDebut") or "", x.get("numero") or ""),
                         reverse=True):
            iv = it.get("intervenant") or {}
            nom = " ".join(x for x in (iv.get("prenom"), iv.get("nom")) if x).strip()
            if not nom:
                continue
            mail = _adresse_nominative(iv)
            if mail:
                return {"mail": mail, "matricule": iv.get("matricule"),
                        "nom": nom, "intervention": it.get("numero"),
                        "etat": it.get("etat")}
            return dict(RELAIS, relais=True, intervention=it.get("numero"),
                        raison="adresse introuvable pour %s (fiche : %s, "
                               "matricule : %s) — relayé à %s"
                               % (nom, (iv.get("adresse") or {}).get("mail1") or "-",
                                  iv.get("matricule") or "-", RELAIS["nom"]))
        # Personne n'est affecte : ce n'est pas la meme chose qu'un intervenant
        # injoignable. Clifford ne doit pas faire suivre, il doit REDISPATCHER.
        # Le drapeau porte la difference jusqu'au texte du transfert.
        return dict(RELAIS, relais=True, sans_intervenant=True,
                    raison="aucune intervention de ce DIT n'a d'intervenant "
                           "affecté — relayé à %s" % RELAIS["nom"])
    return {"erreur": "DIT %s absent de la liste du client %s" % (numero_dit, code)}


def assurer_demandeur(pid, confirmer=False, mail=None, nom=None, forcer=False):
    """Garantit que la proposition porte un demandeur : trouvé, ou créé.

    `mail` / `nom` : saisis à l'écran. Un expéditeur n'est pas toujours le
    demandeur — une assistante écrit pour son directeur, un prestataire pour
    son client (Elliot, 2026-09-14). Quand ils sont fournis, ils remplacent
    l'expéditeur du mail pour la recherche ET pour la création.
    `forcer` : refaire la recherche même si un demandeur est déjà posé.

    Rend le détail de ce qui a été fait — l'écran et le registre doivent
    pouvoir dire « ce contact a été CRÉÉ », c'est une écriture dans la fiche
    du client.
    """
    prop = M.par_id(pid)
    if not prop or not prop.get("client"):
        return {"erreur": "client inconnu"}
    if prop.get("pid_interlocuteur") and not (forcer or mail or nom):
        return {"deja": True, "id": prop["pid_interlocuteur"]}
    adr = (mail or prop.get("de") or "").strip().lower()
    nom_aff = (nom or prop.get("de_nom") or "").strip()
    # JAMAIS un collegue comme demandeur. Quand un DFM ecrit au support pour un
    # client, il n'est pas le demandeur — le demandeur est la personne du
    # client, nommee dans le mail ou en copie. Elliot, 2026-09-23, sur la DIT
    # 100174874 : « il a mis un membre de DFM en demandeur ». On ecarte donc
    # l'adresse interne et on cherche ailleurs ; si l'on ne trouve personne, la
    # DIT se cree SANS demandeur, ce qui est la consigne (2026-09-14) — et
    # jamais avec un nom de chez nous.
    if not mail and M.est_interne(adr):
        interne_ecarte = adr
        adr, nom_aff = "", ""
    else:
        interne_ecarte = ""
    if mail and "@" not in adr:
        return {"erreur": "adresse invalide : %r" % mail}
    try:
        t = trouver_demandeur(prop["client"], nom_aff, adr) if adr or nom_aff else None
    except Exception as e:
        return {"erreur": "recherche impossible : %s" % str(e)[:150]}

    if t and t.get("id"):
        M.corriger(pid, {"pid_interlocuteur": t["id"]}, "outil")
        return {"trouve": True, "id": t["id"], "comment": t.get("comment"),
                "contact": t.get("contact")}
    # L'expéditeur n'est pas un contact connu : la personne concernée est
    # peut-être NOMMÉE dans le mail. Un collègue qui écrit « M FITOUSSI me
    # signale un souci » désigne le demandeur aussi clairement qu'une adresse.
    if not mail:
        t2 = demandeur_du_texte(prop.get("client"),
                                (prop.get("objet") or "") + "\n"
                                + (prop.get("commentaire") or "")[:2000])
        if t2 and t2.get("id"):
            M.corriger(pid, {"pid_interlocuteur": t2["id"]}, "outil")
            return {"trouve": True, "id": t2["id"], "comment": t2.get("comment"),
                    "contact": t2.get("contact")}
    if t and t.get("ambigu"):
        return {"ambigu": t["ambigu"], "comment": t.get("comment")}

    # Personne de ce nom chez ce client : on le crée. Deux refus nets d'abord —
    # une fiche client ne doit pas se remplir de robots. Mesure sur la file :
    # les seuls candidats à la création étaient « Sophos »
    # <sophos.notif.acces@gmail.com> et <firewall@crpce.com>.
    from mail_regles import RE_NOREP
    if RE_NOREP.search(adr + " " + nom_aff):
        return {"erreur": "expéditeur automatique (%s) : on ne crée pas de "
                          "contact pour un robot" % adr}
    if interne_ecarte:
        return {"erreur": "l'expediteur est un collegue (%s) : la DIT se cree "
                          "sans demandeur plutot qu'avec quelqu'un de chez nous"
                          % interne_ecarte, "interne_ecarte": interne_ecarte}
    nom_complet = nom_aff
    if not nom_complet:
        local = adr.split("@")[0].replace(".", " ")
        nom_complet = local.title()
    morceaux = nom_complet.split()
    if len(morceaux) < 2:
        return {"erreur": "nom d'expéditeur trop pauvre pour créer un contact "
                          "(« %s ») — à saisir à la main" % nom_complet}
    prenom, nom = morceaux[0], " ".join(morceaux[1:])
    c = creer_interlocuteur(prop["client"], nom, prenom, adr,
                            confirmer=confirmer)
    if c.get("erreur") or not c.get("id"):
        return {"erreur": c.get("erreur") or "création sans identifiant"}
    M.corriger(pid, {"pid_interlocuteur": c["id"]}, "outil")
    return {"cree": True, "id": c["id"],
            "comment": "contact créé chez le client %s : %s %s <%s>"
                       % (prop["client"], prenom, nom.upper(), adr)}


def mettre_corbeille(pid, motif="", confirmer=False):
    """Met le mail à la corbeille et sort la proposition de la file.

    Demandé par Elliot le 2026-09-14 : « un bouton pour mettre à la corbeille
    un mail directement, si jamais tu attrapes un spam ».

    Le mail va dans la corbeille d'Exchange, pas dans le néant : il reste
    récupérable pendant la durée de rétention si le geste était de trop.
    """
    prop = M.par_id(pid)
    if not prop:
        return {"erreur": "proposition %s inconnue" % pid}
    res = ranger_mail(prop, "corbeille", confirmer=confirmer)
    if not res.get("envoye"):
        return {"erreur": res.get("erreur") or "déplacement refusé",
                "detail": res}
    M.statuer(pid, "ecarte", "outil",
              motif.strip() or "mis à la corbeille (indésirable)")
    with M._lock:
        d = M._charger()
        for p in d["props"]:
            if p.get("id") == pid:
                p["range_par_outil"] = "corbeille"
        M._sauver(d)
    return {"ok": True, "destination": res.get("destination"),
            "proposition": M.par_id(pid)}


def transferer_mail(pid, a="", mot="", confirmer=False, deplacer=True):
    """Transfère le mail à un collègue : il lui était adressé, pas au support.

    Elliot (2026-09-14, P-0217) : « on voit bien que c'est un mail dirigé pour
    un membre de l'équipe interne, Valentin, tu peux ajouter un bouton pour
    transférer ? »

    Deux temps, imposés par les permissions : `createForward` prépare le
    message (permission `Mail.ReadWrite`, que nous avons), `send` l'envoie
    (permission `Mail.Send`, que nous n'avons PAS). Tant qu'elle n'est pas
    accordée, on laisse un brouillon prêt dans la boîte et on le dit — on
    n'annonce jamais un envoi qui n'a pas eu lieu.
    """
    prop = M.par_id(pid)
    if not prop:
        return {"erreur": "proposition %s inconnue" % pid}
    a = (a or (prop.get("destinataire_interne") or {}).get("adresse") or "").strip()
    if "@" not in a:
        return {"erreur": "destinataire manquant"}
    # Règle absolue d'Elliot : on ne transfère QU'EN INTERNE. Un transfert
    # sortant enverrait à un tiers le mail d'un client, son historique et ses
    # pièces jointes.
    if not M.est_interne(a):
        return {"erreur": "transfert refusé : %s n'est pas une adresse interne "
                          "(%s)" % (a, ", ".join(sorted(M.INTERNE)))}
    if not _autorise(confirmer):
        return {"erreur": "écriture non armée : %s" % DRAPEAU}
    tok = M.access_token()
    if not tok:
        return {"erreur": "pas de jeton mail"}

    mot = (mot or ("Bonjour,\n\nCe message est arrivé sur la boîte du support "
                   "mais t'est adressé — je te le transfère.\n")) + M.SIGNATURE_MCO
    r = requests.post("%s/me/messages/%s/createForward" % (M.GRAPH, prop["mail_id"]),
                      headers={"Authorization": "Bearer " + tok,
                               "Content-Type": "application/json"},
                      data=json.dumps({"comment": mot,
                                       "toRecipients": [{"emailAddress": {"address": a}}]}),
                      timeout=60)
    if r.status_code not in (200, 201):
        return {"erreur": "createForward HTTP %s : %s" % (r.status_code, r.text[:200])}
    brouillon = (r.json() or {}).get("id")
    out = {"a": a, "brouillon": brouillon, "envoye": False}

    e = requests.post("%s/me/messages/%s/send" % (M.GRAPH, brouillon),
                      headers={"Authorization": "Bearer " + tok}, timeout=60)
    if e.status_code in (202, 200, 204):
        out["envoye"] = True
    else:
        out["erreur_envoi"] = "send HTTP %s" % e.status_code
        out["remarque"] = ("brouillon prêt dans « Brouillons » : l'envoi exige "
                           "la permission Mail.Send, absente de notre jeton")

    M.statuer(pid, "ecarte", "outil",
              "transféré à %s : le mail lui était adressé%s"
              % (a, "" if out["envoye"] else " (brouillon à envoyer à la main)"))
    # `deplacer_mail` refuse tant qu'aucun DIT n'est rattaché — garde-fou juste
    # pour une création, faux ici : le mail est parti chez un collègue, il n'a
    # plus rien à faire en boîte de réception. Constaté le 2026-09-16 : tous
    # les mails transférés y restaient, l'envoi ayant pourtant réussi.
    if deplacer and out["envoye"]:
        out["mail"] = ranger_mail(M.par_id(pid), "traite", confirmer)
    return out


def finaliser_dit(numero, prop, confirmer=False):
    """Ce qu'il reste a poser APRES la creation : type de travail et
    facturation. Rend (constat_relu, resultat_du_formulaire).

    Extrait de `traiter` pour etre appele aussi par le pont du modele. Les deux
    chemins de creation faisaient un travail DIFFERENT : celui des propositions
    posait « Hotline » et « NonFacturable », celui du modele ne posait rien —
    ses tickets sortaient donc en « Terrain / Automatique », ce qui touche la
    facturation. Constate le 2026-09-23 sur les DIT 100174964 et 100175021.
    Une seule fonction, deux appelants : ils ne peuvent plus diverger.
    """
    reel = relire_dit(numero)
    if reel.get("erreur"):
        return reel, {}
    # « nature TOUJOURS Hotline » est une consigne d'Elliot, sans
    # condition. `type_travail_attendu` ne la rend que si l'UO commence
    # par HL ; quand la DIT sort SANS UO, elle rendait None et le ticket
    # restait en « Terrain ». C'est ce qui est arrive a la DIT 07221097
    # le 2026-09-28 : facturation bien posee en NonFacturable, type de
    # travail laisse en Terrain, faute d'UO affectee. Le defaut, c'est
    # Hotline.
    attendu = type_travail_attendu(reel.get("uo") or prop.get("uo")) or "Hotline"
    travail_a_corriger = bool(attendu and reel.get("type_travail")
                              and reel["type_travail"] != attendu)
    # Artis laisse « Automatique » sur toute DIT creee par l'API ; Elliot veut
    # « Non facturable » des la creation (2026-09-22). Le champ vit dans le
    # meme bloc que le type de travail : une seule session web pose les deux.
    factu_a_poser = (reel.get("facturation") or "") != "NonFacturable"
    res_tt = {}
    if travail_a_corriger or factu_a_poser:
        valeur = attendu or reel.get("type_travail") or "Hotline"
        res_tt = corriger_type_travail(
            numero, valeur, confirmer,
            facturation=FACTURATION_VOULUE if factu_a_poser else None)
        # UNE seconde tentative apres une pause. Le formulaire retrouve la DIT
        # par l'index SOLR d'Artis, qui n'est pas instantane : appelee dans la
        # foulee de la creation, la finition echoue une fois sur deux et le
        # ticket reste en « Terrain / Automatique ». Constate le 2026-09-24 sur
        # la DIT 100175153, qui a pris la bonne valeur au premier rejeu.
        if confirmer and not res_tt.get("ok") and not res_tt.get("inchange"):
            import time as _t
            _t.sleep(12)
            res_tt = corriger_type_travail(
                numero, valeur, confirmer,
                facturation=FACTURATION_VOULUE if factu_a_poser else None)
        if res_tt.get("ok"):
            reel = relire_dit(numero)
        # ON RELIT, ON NE CROIT PAS. Le formulaire peut repondre « ok » en
        # n'ayant pose que le type de travail : le 2026-09-25, la DIT 04216828
        # est sortie « Hotline » mais restee « Automatique », sans erreur ni
        # trace. Le seul juge est le ticket relu. Une seule reprise, et sur la
        # facturation seule — le type de travail, lui, est deja bon.
        if (confirmer and res_tt.get("ok")
                and (reel or {}).get("facturation") != "NonFacturable"):
            import time as _t
            _t.sleep(12)
            res_tt = corriger_type_travail(
                numero, (reel or {}).get("type_travail") or valeur, confirmer,
                facturation=FACTURATION_VOULUE)
            reel = relire_dit(numero)
    return reel, res_tt


def entete_ajout(quand):
    """La banniere qui annonce un AJOUT du client dans le commentaire d'une DIT.

    Elliot, 2026-09-23, sur la DIT 100174942 : « c'est bien fait mais l'ajout
    n'est pas clairement identifiable, on pourrait le louper dans la masse ».
    Une ligne de texte se noie dans un commentaire de trois ecrans ; une barre
    pleine largeur, elle, se voit au premier coup d'oeil — c'est le meme
    traitement que le separateur d'historique, et pour la meme raison.
    """
    barre = "=" * 58
    return (barre + chr(10)
            + ">>> AJOUT DU CLIENT - mail du "
            + (quand or "date inconnue") + chr(10) + barre)


def completer_dit_direct(numero, prop, log=None, dossier="traite"):
    """Ajoute à un DIT le texte et les pièces d'un mail SANS proposition.

    `completer_dit` travaille sur une proposition enregistrée ; le pont du
    modèle, lui, bâtit sa demande en mémoire. Rend {ok, resume}. Le mail est
    rangé s'il s'est passé quelque chose, puis transmis au dernier
    intervenant — la suite d'une demande doit arriver chez celui qui la traite.
    """
    dire = log or (lambda *_a: None)
    # Le veto vaut pour TOUT ce qui suit, pas seulement pour le rangement.
    # Le transfert au dernier intervenant est un POST direct a Graph, plus bas,
    # qui ne passe pas par `_autorise` : il partait donc sous veto. Constate le
    # 2026-10-15 sur la DIT 100177620 (Pompes Funebres Treportaises), commentee
    # et transmise a Samir HIRECHE alors que /data disait NON.
    if not ecriture_armee():
        return {"ok": False, "prevenu": "", "resume":
                "DIT %s : ecriture non armee (%s), ni commentaire ni transfert"
                % (numero, DRAPEAU)}
    faits = []
    fiche = relire_dit(numero) or {}
    ancien = fiche.get("commentaire") or ""
    # UN COMMENTAIRE VIDE SUR UN TICKET QUI EXISTE, C'EST UNE LECTURE RATEE.
    # Le commentaire est le SEUL champ obligatoire d'Artis : une DIT en a
    # toujours un. S'il revient vide, c'est qu'Artis n'a pas repondu — et
    # ecrire par-dessus EFFACE la demande du client. C'est ce qui est arrive
    # a la DIT 100176099 le 2026-10-02 : le texte de Mme Sun a disparu,
    # remplace par le seul ajout. On refuse d'ecrire plutot que de perdre.
    if fiche and not ancien.strip():
        return {"ok": False, "prevenu": "",
                "resume": "DIT %s : commentaire relu VIDE alors que le ticket "
                          "existe — lecture Artis douteuse, rien n'a ete ecrit "
                          "pour ne pas effacer la demande du client" % numero}
    ajout = _stockable((prop.get("commentaire") or "").strip())
    if ajout and ajout[:120] not in ancien:
        import artis_type_travail as W
        entete = entete_ajout(_quand(prop.get("recu")))
        r = W.poser_commentaire(numero,
                                _stockable(ancien.rstrip() + "\n\n" + entete
                                           + "\n" + ajout), confirmer=True)
        faits.append("commentaire " + ("ajouté" if r.get("ok") else "refusé"))
    pj = joindre_pieces(numero, prop, True)
    if pj.get("envoye"):
        faits.append("%d pièce(s) jointe(s)" % len(pj.get("pieces") or []))
    if not faits:
        # Rien à ajouter — une invitation Teams, un accusé sans texte — mais le
        # mail appartient bien à ce ticket. Il se range quand même : sinon il
        # reste en boîte à chaque passage, refusé par une porte et attendu par
        # personne (« Sanaz Courdkhalag Laptop Set-up », 2026-09-18).
        res = ranger_mail(prop["mail_id"], dossier, confirmer=True)
        return {"ok": bool(res.get("envoye")), "prevenu": "",
                "resume": "rien à ajouter au DIT %s ; mail rangé" % numero}

    # QUI a ete prevenu : c'est la partie qu'on oublie, et son oubli est
    # silencieux. Elliot, le 2026-10-02, sur la DIT 100175793 : « tu as
    # ajoute le commentaire du client et tu l'as rouverte, mais tu as
    # prevenu personne, comment Shannon est censee le savoir ? ». Le
    # retour porte desormais `prevenu` : vide veut dire que personne ne sait.
    prevenu = ""
    iv = dernier_intervenant(numero)
    if iv.get("mail") and M.est_interne(iv["mail"]) \
            and iv["mail"].lower() != (prop.get("de") or "").lower():
        import requests as _rq
        tok = M.access_token()
        f = _rq.post("%s/me/messages/%s/createForward" % (M.GRAPH, prop["mail_id"]),
                     headers={"Authorization": "Bearer " + tok,
                              "Content-Type": "application/json"},
                     data=json.dumps({
                         # `_mot_du_transfert` distingue les trois cas —
                         # l'intervenant, l'adresse introuvable, et SURTOUT le
                         # ticket sans aucun intervenant, ou Clifford doit
                         # REDISPATCHER et non faire suivre. Ce chemin-ci
                         # ecrivait « tu es le dernier intervenant » meme a
                         # Clifford, ce qu'Elliot avait fait corriger ailleurs
                         # le 2026-09-25 (constate ici le 2026-10-02).
                         "comment": M._mot_du_transfert(numero, iv),
                         "toRecipients": [
                             {"emailAddress": {"address": iv["mail"]}}]}),
                     timeout=60)
        if f.status_code in (200, 201):
            e = _rq.post("%s/me/messages/%s/send" % (M.GRAPH, (f.json() or {}).get("id")),
                         headers={"Authorization": "Bearer " + tok}, timeout=60)
            if e.status_code in (200, 202, 204):
                faits.append("transmis à %s" % iv["mail"])
                prevenu = iv["mail"]
    if not prevenu:
        faits.append("PERSONNE N'A ETE PREVENU (%s)"
                     % (iv.get("raison") or iv.get("erreur")
                        or "intervenant injoignable"))
    # Une COPIE dans « 16 - REPONSE DIT », pour que l'equipe suive les
    # reponses des clients sans ouvrir Artis (Elliot, le 2026-10-13). Elle se
    # prend AVANT le rangement : `ranger_mail` deplace le message, et apres
    # lui l'identifiant d'origine ne designe plus rien. Posee aussi quand le
    # transfert a echoue — c'est justement la que le suivi manque le plus.
    try:
        cp = copier_mail(prop["mail_id"], "reponse_dit", confirmer=True)
        if cp.get("envoye"):
            faits.append("copie en %s" % DOSSIERS["reponse_dit"])
        else:
            faits.append("COPIE EN 16 - REPONSE DIT MANQUEE (%s)"
                         % (cp.get("erreur") or cp.get("http") or "?"))
    except Exception as exc:                 # noqa: BLE001
        faits.append("COPIE EN 16 - REPONSE DIT MANQUEE (%s)" % str(exc)[:80])
    res = ranger_mail(prop["mail_id"], dossier, confirmer=True)
    if res.get("envoye"):
        faits.append("rangé en %s" % DOSSIERS.get(dossier, dossier))
    dire("[mail-dit] DIT %s : %s" % (numero, ", ".join(faits)))
    return {"ok": True, "prevenu": prevenu, "intervenant": iv,
            "resume": "DIT %s — %s" % (numero, ", ".join(faits))}


def completer_dit(pid, numero, texte="", confirmer=False, deplacer=True):
    """Ajoute le texte d'un mail à un DIT qui EXISTE déjà.

    Le cas est courant : le client envoie sa demande, puis deux minutes plus
    tard « Ajout : il faut aussi... ». Créer un second ticket serait un
    doublon ; il faut compléter le premier. Aucune route REST ne réécrit le
    commentaire d'un DIT — on passe par le formulaire web, comme pour le type
    de travail.

    Les pièces jointes du mail suivent, et le mail est rangé comme pour une
    création. Rien n'est effacé : le texte est AJOUTÉ à la suite.
    """
    prop = M.par_id(pid)
    if not prop:
        return {"erreur": "proposition %s inconnue" % pid}
    numero = str(numero or "").strip()
    if not numero.isdigit():
        return {"erreur": "numéro de DIT invalide : %r" % numero}
    if not _autorise(confirmer):
        return {"erreur": "écriture non armée : %s" % DRAPEAU}

    ancien = (relire_dit(numero) or {}).get("commentaire") or ""
    ajout = _stockable((texte or prop.get("commentaire") or "").strip())
    if not ajout and (prop.get("pieces") or prop.get("photos")):
        # Un mail de PHOTOS n'a pas de texte, et c'est normal : le client
        # répond à « envoyez-moi des photos ». Le commentaire doit quand même
        # dire que quelque chose est arrivé, sinon le technicien ne saura pas
        # pourquoi des fichiers ont surgi dans son ticket.
        noms = [x.get("nom") for x in (prop.get("pieces") or []) if x.get("nom")]
        ajout = _stockable(
            "Photos envoyées par le client (mail du %s)%s"
            % (_quand(prop.get("recu")) or "date inconnue",
               " : " + ", ".join(noms[:6]) if noms else ""))
    if not ajout or (ajout[:120] and ajout[:120] in ancien):
        # Rien de neuf à écrire — soit le mail ne porte aucun texte, soit son
        # texte est DÉJÀ dans le ticket. Ce second cas est la preuve la plus
        # forte qui soit que le mail appartient à ce ticket : il n'y a rien à
        # ajouter, mais tout à ranger. Jusqu'ici la fonction rendait une erreur
        # et s'arrêtait là ; le mail restait en boîte à chaque passage, refusé
        # par une porte et attendu par personne. Constaté le 2026-09-21 sur
        # « paramétrage PC » (SDVO), dont le texte figurait mot pour mot dans
        # le DIT 100174624 — la preuve même du rattachement le rendait
        # intraitable. `completer_dit_direct` faisait déjà le bon geste.
        deja = bool(ajout)
        res = {"proposition": pid, "dit": numero, "deja": deja,
               "commentaire": {"ok": True, "deja": deja,
                               "note": "texte déjà présent" if deja
                                       else "aucun texte à ajouter"}}
        res["pieces_jointes"] = joindre_pieces(numero, prop, confirmer)
        M.statuer(pid, "valide", "outil",
                  ("texte déjà présent dans le DIT %s" if deja
                   else "rien à ajouter au DIT %s") % numero,
                  dit_numero=numero)
        if deplacer:
            res["mail"] = deplacer_mail(M.par_id(pid), confirmer)
        return res

    entete = entete_ajout(_quand(prop.get("date_dit") or prop.get("recu")))
    nouveau = _stockable(ancien.rstrip() + "\n\n" + entete + "\n" + ajout)

    import artis_type_travail as W
    res = {"proposition": pid, "dit": numero,
           "commentaire": W.poser_commentaire(numero, nouveau, confirmer=True)}
    if not res["commentaire"].get("ok"):
        # Le commentaire passe par le formulaire web, qui refuse une DIT
        # verrouillée (« ne s'ouvre pas en modification »). Les PIÈCES, elles,
        # passent par la route REST /documents, qui n'a pas cette limite —
        # vérifié le 2026-09-18 sur le DIT 100174414, dont le commentaire était
        # bloqué et où la photo de 3,6 Mo est pourtant arrivée. On ne renonce
        # donc plus aux deux parce que l'une échoue.
        res["pieces_jointes"] = joindre_pieces(numero, prop, confirmer)
        if (res["pieces_jointes"] or {}).get("envoye"):
            M.statuer(pid, "valide", "outil",
                      "pièces ajoutées au DIT %s (commentaire verrouillé)"
                      % numero, dit_numero=numero)
            if deplacer:
                res["mail"] = deplacer_mail(M.par_id(pid), confirmer)
        return res

    res["pieces_jointes"] = joindre_pieces(numero, prop, confirmer)
    M.statuer(pid, "valide", "outil",
              "texte ajouté au DIT %s (demande complétée par le client)" % numero,
              dit_numero=numero)
    if deplacer:
        res["mail"] = deplacer_mail(M.par_id(pid), confirmer)
    return res


def traiter(pid, confirmer=False, deplacer=True, groupe=None):
    """Crée le DIT puis, si ça a marché, déplace le mail. Ordre non négociable.

    `groupe` : les autres propositions qui sont la MÊME demande (les photos
    envoyées une par une, cf. P-0182/P-0183). Elles ne donnent pas de second
    DIT : leurs pièces rejoignent celui-ci, et elles sont closes et rangées
    avec le même numéro.
    """
    prop = M.par_id(pid)
    if not prop:
        return {"erreur": "proposition %s inconnue" % pid}

    # Le demandeur, avant tout le reste. Un contact peut écrire depuis un autre
    # domaine que celui du client (un comptable externe, un prestataire) : on
    # le cherche par son NOM chez ce client, et on ne le crée que si ce nom n'y
    # est pas. Règle donnée par Elliot le 2026-09-14 (P-0186), à appliquer
    # « pour tous les cas futurs ».
    res_dem = None
    if prop.get("client") and not prop.get("pid_interlocuteur"):
        res_dem = assurer_demandeur(pid, confirmer)
        prop = M.par_id(pid) or prop
        prop = dict(prop, _demandeur=res_dem)

    jointes = [M.par_id(x) for x in (groupe or []) if x and x != pid]
    jointes = [p for p in jointes if p and p.get("etat") == "nouveau"]
    # Le commentaire doit annoncer les photos des mails regroupés, lui aussi.
    prop = dict(prop, _regroupees=jointes) if jointes else prop

    res = {"proposition": pid, "dit": creer_dit(prop, confirmer)}
    if res_dem:
        res["demandeur"] = res_dem
    if jointes:
        res["regroupees"] = [p.get("id") for p in jointes]
    # DOUBLON EVITE : le ticket existe deja, pose par quelqu'un d'autre. Ce
    # n'est pas un echec — c'est le bon resultat, et il doit CONCLURE. Un
    # refus sans suite laisse le mail en boite, invisible, pret a revenir au
    # cycle suivant. On classe la proposition sur le ticket existant et on
    # range le mail, sans rien reecrire dans Artis : le texte y est deja.
    jum = res["dit"].get("doublon_de")
    if jum:
        M.statuer(pid, "valide", "outil", dit_numero=jum)
        try:
            M.journal_auto_corriger(pid, jum)
        except Exception:
            pass
        if deplacer:
            prop = M.par_id(pid)
            res["mail"] = deplacer_mail(prop, confirmer)
        res["doublon_de"] = jum
        return res

    if not res["dit"].get("envoye"):
        res["mail"] = {"envoye": False,
                       "erreur": "DIT non créé : le mail reste dans la boîte"}
        return res

    M.statuer(pid, "valide", "outil", dit_numero=res["dit"].get("numero"))
    # Si le traitement automatique avait REFUSE cette proposition, la ligne du
    # journal doit dire ce qui s'est passe ensuite — sinon Elliot lit « non
    # fait » sur un ticket qui existe.
    try:
        M.journal_auto_corriger(pid, res["dit"].get("numero"))
    except Exception:
        pass

    # Pieces jointes : elles font partie de la demande. Apres la creation, et
    # sans jamais la remettre en cause si elles echouent.
    res["pieces_jointes"] = joindre_pieces(res["dit"].get("numero"), prop,
                                           confirmer, aussi=jointes)

    # LE MAIL SORT ICI, juste après les pièces jointes et AVANT tout le reste.
    # Les étapes suivantes (UO, type de travail, relecture) passent par le
    # formulaire web d'Artis, qui est fragile : le 2026-09-17 l'une d'elles a
    # levé une exception après la création du DIT 100174398, et le mail est
    # resté en boîte — un ticket existait, son mail attendait encore, prêt à
    # produire un doublon. Les pièces, elles, doivent être jointes AVANT le
    # déplacement : il change l'identifiant du message.
    if deplacer:
        prop = M.par_id(pid)
        res["mail"] = deplacer_mail(prop, confirmer)
        if res["mail"].get("envoye"):
            M.corriger(pid, {"note": "mail déplacé vers %s" % M.DOSSIER_TRAITE},
                       "outil")
            with M._lock:
                d = M._charger()
                for p in d["props"]:
                    if p.get("id") == pid:
                        p["deplace_par_outil"] = True
                        p["deplace_le"] = datetime.datetime.now().isoformat(
                            timespec="seconds")
                M._sauver(d)
        deplacer = False               # fait : on ne le refera pas plus bas

    # UO : Artis en a posé une automatiquement, on applique la convention.
    it = ((res["dit"].get("reponse") or {}).get("inter") or [{}])[0].get("numero")
    if it:
        res["uo"] = poser_uo(it, prop.get("uo") or uo_pour(prop), confirmer)
        res["uo"]["calculee_par_artis"] = (
            ((res["dit"].get("reponse") or {}).get("inter") or [{}])[0]
            .get("uo", {}).get("code") if isinstance(
                (((res["dit"].get("reponse") or {}).get("inter") or [{}])[0]
                 .get("uo")), dict) else None)

    # Filet : si Artis a tout de même laissé la DIT en « SaisieExterne »
    # malgré etatDit, on la valide.
    etat_obtenu = (res["dit"].get("reponse") or {}).get("etat")
    if etat_obtenu == "SaisieExterne":
        res["validation"] = valider_dit(res["dit"].get("numero"), confirmer)
    else:
        res["validation"] = {"envoye": False, "inutile": True,
                             "etat": etat_obtenu}

    # Constat après création : le type de travail n'est pas pilotable par l'API
    # (cf. ARTIS_DEMANDE_EDITEUR.md) et il impacte la facturation. On relit la
    # DIT et on trace l'écart, pour que l'écran l'exige au lieu de l'espérer.
    reel, res_tt = finaliser_dit(res["dit"].get("numero"), prop, confirmer)
    res["constat"] = reel
    # Meme defaut qu'au-dessus : sans UO, on attend quand meme Hotline.
    attendu = type_travail_attendu(reel.get("uo") or prop.get("uo")) or "Hotline"
    if res_tt:
        res["type_travail"] = res_tt
    # La facturation est signalée comme le type de travail : si elle n'est pas
    # posée, l'écran doit le dire au lieu de l'espérer.
    if (reel.get("facturation") or "") != "NonFacturable":
        res["facturation_a_corriger"] = reel.get("facturation") or "inconnue"

    if attendu and reel.get("type_travail") and reel["type_travail"] != attendu:
        M.corriger(pid, {}, "outil")
        with M._lock:
            d = M._charger()
            for p_ in d["props"]:
                if p_.get("id") == pid:
                    p_["a_corriger_dans_artis"] = (
                        "type de travail = %s, attendu %s — la correction "
                        "automatique par le formulaire web a échoué (%s), "
                        "à changer à la main dans Artis : ce champ impacte "
                        "la facturation"
                        % (reel["type_travail"], attendu,
                           (res.get("type_travail") or {}).get("erreur")
                           or "écriture non armée"))
                    p_["type_travail_reel"] = reel["type_travail"]
            M._sauver(d)
        res["a_corriger"] = True

    if deplacer:
        prop = M.par_id(pid)
        res["mail"] = deplacer_mail(prop, confirmer)
        if res["mail"].get("envoye"):
            M.corriger(pid, {"note": "mail déplacé vers %s" % M.DOSSIER_TRAITE},
                       "outil")
            with M._lock:
                d = M._charger()
                for p in d["props"]:
                    if p.get("id") == pid:
                        p["deplace_par_outil"] = True
                        p["deplace_le"] = datetime.datetime.now().isoformat(
                            timespec="seconds")
                M._sauver(d)

    # Les propositions regroupées : closes avec le MÊME numéro de DIT, et leur
    # mail rangé comme celui de la demande principale. Elles ne doivent plus
    # apparaître dans la file : c'est une seule demande, elle est traitée.
    if jointes:
        res["regroupees_traitees"] = []
        for p in jointes:
            num = res["dit"].get("numero")
            M.statuer(p["id"], "valide", "outil",
                      "regroupée avec %s dans le DIT %s (même demande, "
                      "photos jointes au même ticket)" % (pid, num),
                      dit_numero=num)
            r2 = {"id": p["id"]}
            if deplacer:
                r2["mail"] = deplacer_mail(M.par_id(p["id"]), confirmer)
            res["regroupees_traitees"].append(r2)
    return res


# ═══════════════════════════════════════════════════════ simulation lisible
if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pid = args[0] if args else None

    # --go : on cree pour de vrai. Sans lui, tout ce qui suit est une
    # simulation. Le drapeau sur disque reste exige en plus.
    if "--go" in sys.argv:
        if not pid:
            raise SystemExit("usage : mail_dit_ecriture.py <P-xxxx> --go")
        r = traiter(pid, confirmer=True, deplacer=True)
        if r.get("erreur"):
            print("ERREUR   : %s" % r["erreur"])
            raise SystemExit(1)
        d = r.get("dit") or {}
        print("DIT      : envoye=%s numero=%s %s"
              % (d.get("envoye"), d.get("numero"), (d.get("erreur") or "")[:150]))
        if d.get("envoye"):
            print("UO       : %s" % json.dumps(r.get("uo"), ensure_ascii=False,
                                               default=str)[:200])
            print("TYPE TRAV: %s" % json.dumps(r.get("type_travail"),
                                               ensure_ascii=False, default=str)[:260])
            print("CONSTAT  : %s" % json.dumps(r.get("constat"),
                                               ensure_ascii=False, default=str)[:260])
            print("MAIL     : %s" % json.dumps(r.get("mail"), ensure_ascii=False,
                                               default=str)[:200])
            print("A CORRIGER: %s" % bool(r.get("a_corriger")))
        raise SystemExit(0 if d.get("envoye") else 1)

    if not pid:
        props = M.propositions("nouveau")
        pid = props[0]["id"] if props else None
        if not pid:
            raise SystemExit("aucune proposition en attente")

    prop = M.par_id(pid)
    print("=" * 74)
    print("SIMULATION — proposition %s" % pid)
    print("=" * 74)
    print("  écriture armée : %s  (%s)"
          % ("OUI" if ecriture_armee() else "NON", DRAPEAU))
    print("\n  mail   : %s" % prop.get("de"))
    print("  objet  : %s" % (prop.get("objet") or ""))
    print("  client : %s %s" % (prop.get("client"), prop.get("client_lib") or ""))

    bloq, av = verifier(prop)
    print("\n  BLOQUANTS :")
    print("\n".join("    - " + x for x in bloq) or "    (aucun)")
    print("\n  AVERTISSEMENTS :")
    print("\n".join("    - " + x for x in av) or "    (aucun)")

    print("\n  CORPS QUI SERAIT ENVOYÉ À POST /dit :")
    print(json.dumps(corps_dit(prop), ensure_ascii=False, indent=4))

    r = creer_dit(prop, confirmer=False)
    print("\n  résultat de l'appel : envoye=%s  %s"
          % (r["envoye"], r.get("simulation") or r.get("erreur") or ""))
    print("\n  puis, en cas de succès : POST /me/messages/<id>/move vers « %s »"
          % M.DOSSIER_TRAITE)
