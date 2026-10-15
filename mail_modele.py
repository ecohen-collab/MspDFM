# -*- coding: utf-8 -*-
"""Le jugement, quand les règles ne suffisent pas.

Le moteur de `mail_dit.py` est une mécanique de motifs : il ne sait que ce qui
est écrit. Devant « traces noires sur les impressions » aucune règle ne dit
« copieur », et le mail reste en boîte. Ce module donne au moteur la lecture
qui lui manque, en demandant son avis à un modèle — et il RETIENT la réponse
sous forme de règle, pour que le même expéditeur soit tranché gratuitement la
fois suivante.

Trois principes, posés par Elliot le 2026-09-17 :
  * « ça auto-remplit le robot » — chaque avis devient une règle apprise ;
  * « dès que les crédits sont épuisés, ça repart sur le robot » — toute panne
    (plus de crédit, clé absente, erreur réseau) rend `None`, et le moteur se
    comporte exactement comme avant ;
  * « 10 € par mois maximum » — plafond dur, vérifié AVANT chaque appel.
"""
import datetime
import json
import os
import pathlib
import threading

DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR") or "./data")
USAGE_FICHIER = DATA_DIR / "mail_modele_usage.json"
APPRIS_FICHIER = DATA_DIR / "mail_regles_apprises.json"

MODELE = os.environ.get("MAIL_MODELE", "claude-haiku-5-5")
# Plafond mensuel, en euros. Elliot, 2026-09-17 : « avec une limitation de
# conso de 10 € par mois max ».
PLAFOND_EUR = float(os.environ.get("MAIL_MODELE_PLAFOND_EUR", "10"))
# Tarifs publiés de Haiku 4.5, par million de jetons, et le change retenu.
# Les trois sont des constantes à corriger si les tarifs bougent.
PRIX_ENTREE_USD = 0.10   # Haiku 5.5, prompts < 100k jetons
PRIX_SORTIE_USD = 0.50
USD_VERS_EUR = 0.92

_verrou = threading.Lock()


# ─────────────────────────────────────────────────────────── budget
def _mois():
    return datetime.date.today().strftime("%Y-%m")


def _lire(chemin, defaut):
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except Exception:
        return defaut


def _ecrire(chemin, valeur):
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(json.dumps(valeur, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    except Exception:
        pass


def usage():
    """{mois: {appels, cout_eur}, ...} + le mois courant, prêt à afficher."""
    d = _lire(USAGE_FICHIER, {})
    m = _mois()
    d.setdefault(m, {"appels": 0, "cout_eur": 0.0, "erreurs": 0})
    return d


def depense_du_mois():
    return float(usage().get(_mois(), {}).get("cout_eur") or 0.0)


def budget_restant():
    return max(0.0, PLAFOND_EUR - depense_du_mois())


def disponible():
    """Le modèle peut-il être appelé ? Sinon, le moteur continue sans lui."""
    if PLAFOND_EUR <= 0:
        return False
    if budget_restant() <= 0:
        return False
    return bool(_cle())


def _cle():
    k = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if k:
        return k
    try:
        return (DATA_DIR / "anthropic_key.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _workspace():
    w = (os.environ.get("ANTHROPIC_WORKSPACE_ID") or "").strip()
    if w:
        return w
    try:
        f = DATA_DIR / "anthropic_workspace.txt"
        return f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except Exception:
        return ""


def _compter(entree, sortie, erreur=False):
    cout = ((entree / 1e6) * PRIX_ENTREE_USD
            + (sortie / 1e6) * PRIX_SORTIE_USD) * USD_VERS_EUR
    with _verrou:
        d = _lire(USAGE_FICHIER, {})
        m = d.setdefault(_mois(), {"appels": 0, "cout_eur": 0.0, "erreurs": 0})
        m["appels"] += 1
        m["cout_eur"] = round(float(m.get("cout_eur") or 0.0) + cout, 4)
        if erreur:
            m["erreurs"] = int(m.get("erreurs") or 0) + 1
        _ecrire(USAGE_FICHIER, d)
    return cout


# ─────────────────────────────────────────── ce que le modèle a appris
def regles_apprises():
    """{expediteurs: {adresse: {dossier, pourquoi, quand}}}"""
    d = _lire(APPRIS_FICHIER, {})
    d.setdefault("expediteurs", {})
    return d


def regle_apprise(adresse):
    return (regles_apprises().get("expediteurs") or {}).get(
        (adresse or "").lower().strip())


def apprendre(adresse, dossier, pourquoi):
    """Retient « cet expéditeur va dans ce dossier ».

    C'est le « ça auto-remplit le robot » d'Elliot : le deuxième mail du même
    expéditeur ne coûte plus rien.
    """
    adresse = (adresse or "").lower().strip()
    if not adresse or not dossier:
        return
    with _verrou:
        d = _lire(APPRIS_FICHIER, {"expediteurs": {}})
        d.setdefault("expediteurs", {})[adresse] = {
            "dossier": dossier, "pourquoi": (pourquoi or "")[:200],
            "quand": datetime.datetime.now().isoformat(timespec="seconds"),
            "modele": MODELE}
        _ecrire(APPRIS_FICHIER, d)


# ── Ce que l'IA apprend au script ──────────────────────────────────────────
# Elliot, 2026-09-22 : « l'IA nourrit la database du script pour que, lorsque
# l'IA n'est pas dispo, le script soit aussi fiable que possible ». Les regles
# par expediteur ne valent que pour les machines ; ce qui manque au secours,
# c'est le VOCABULAIRE : quels mots, dans un vrai mail, ont designe quel
# metier. On enregistre donc, a chaque lecture, les mots rares du mail et le
# dossier retenu. Le script pourra s'en servir quand le credit sera epuise.
VOCAB_FICHIER = DATA_DIR / "mail_modele_vocabulaire.json"

# Mots trop courants pour designer quoi que ce soit.
_BANALS = set("""
le la les un une des du de au aux et ou mais donc or ni car que qui quoi dont
pour par sur sous dans avec sans vers chez entre est sont ete etre avoir
bonjour madame monsieur cordialement merci vous nous je il elle on ce cet
cette ces son sa ses leur leurs mon ma mes notre nos votre vos plus moins tres
bien tout tous toute toutes fait faire dit dire ete suite demande client
mail mails message messages objet re tr fw fwd svp merci avance
""".split())


def _mots_rares(texte, maxi=12):
    """Les mots significatifs d'un texte : assez longs, pas banals, pas des
    nombres. Ce sont eux qui portent le metier."""
    import re as _re
    vus, out = set(), []
    for mot in _re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-]{3,}", (texte or "").lower()):
        mot = mot.strip("'-")
        if len(mot) < 4 or mot in _BANALS or mot in vus:
            continue
        vus.add(mot)
        out.append(mot)
        if len(out) >= maxi:
            break
    return out


def apprendre_vocabulaire(texte, dossier):
    """Compte, par dossier, les mots qui y ont mene. Rend le nombre de mots
    retenus. Aucune decision n'est prise ici : on accumule de la matiere."""
    dossier = (dossier or "").strip()
    mots = _mots_rares(texte)
    if not dossier or not mots:
        return 0
    with _verrou:
        d = _lire(VOCAB_FICHIER, {"dossiers": {}, "lus": 0})
        par = d.setdefault("dossiers", {}).setdefault(dossier, {})
        for mot in mots:
            par[mot] = par.get(mot, 0) + 1
        d["lus"] = d.get("lus", 0) + 1
        d["maj"] = datetime.datetime.now().isoformat(timespec="seconds")
        _ecrire(VOCAB_FICHIER, d)
    return len(mots)


def vocabulaire_du_dossier(mini=3):
    """{dossier: [mots]} — les mots qui ont mene au moins `mini` fois a ce
    dossier ET qui n'en designent pas un autre aussi souvent. C'est la base de
    secours : elle ne sert que si le modele est indisponible."""
    d = _lire(VOCAB_FICHIER, {"dossiers": {}})
    par = d.get("dossiers") or {}
    total = {}
    for dossier, mots in par.items():
        for mot, n in mots.items():
            total.setdefault(mot, {})[dossier] = n
    out = {}
    for mot, rep in total.items():
        dossier = max(rep, key=rep.get)
        n = rep[dossier]
        autres = sum(v for k, v in rep.items() if k != dossier)
        if n >= mini and n > autres * 2:
            out.setdefault(dossier, []).append(mot)
    return {k: sorted(v) for k, v in out.items()}


VUS_FICHIER = DATA_DIR / "mail_modele_vus.json"
AVIS_FICHIER = DATA_DIR / "mail_modele_avis.json"


def avis_memorise(mid):
    """L'avis déjà rendu sur ce mail, s'il existe.

    On garde la RÉPONSE, pas seulement « déjà vu ». La différence est
    apparue le 2026-09-17 : douze mails avaient été lus par le modèle avant
    que le code sache exploiter sa réponse ; marqués « vus », ils ne
    repassaient plus, et leur avis était perdu pour toujours. Mémoriser la
    réponse rend le système réparable — on change la façon de s'en servir
    sans repayer une lecture.
    """
    # Normalise A LA LECTURE aussi : les avis retenus avant le correctif du
    # 2026-09-21 portent encore les valeurs abimees, et ils sont rejoues
    # gratuitement a chaque passage. Les nettoyer ici les rend tous
    # exploitables sans repayer une seule lecture.
    return _normaliser((_lire(AVIS_FICHIER, {}) or {}).get(mid or ""))


def memoriser_avis(mid, avis):
    if not mid:
        return
    with _verrou:
        d = _lire(AVIS_FICHIER, {}) or {}
        d[mid] = dict(avis or {}, quand=datetime.datetime.now().isoformat(
            timespec="seconds"))
        if len(d) > 4000:                 # on ne garde que les plus récents
            for k in list(d)[:len(d) - 4000]:
                d.pop(k, None)
        _ecrire(AVIS_FICHIER, d)


def deja_vu(mid):
    """Ce mail a-t-il déjà été soumis au modèle ? On ne paie jamais deux fois
    pour le même message."""
    if avis_memorise(mid):
        return True
    return (mid or "") in set(_lire(VUS_FICHIER, []) or [])


def marquer_vu(mid):
    if not mid:
        return
    with _verrou:
        v = _lire(VUS_FICHIER, []) or []
        if mid not in v:
            v.append(mid)
        _ecrire(VUS_FICHIER, v[-5000:])


# ─────────────────────────────────────────────────────────── l'avis
OUTIL = {
    "name": "decider",
    "description": "Donne la décision pour ce mail reçu sur la boîte du "
                   "support informatique d'une société de services.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["ranger", "creer", "laisser"],
                "description": "ranger : ce mail ne donne pas de ticket "
                               "informatique, il part dans un dossier. "
                               "creer : c'est une demande d'un client pour le "
                               "support informatique. laisser : dans le doute, "
                               "ne rien faire.",
            },
            "dossier": {
                "type": "string",
                "enum": ["telecom", "impression", "securite", "terrain",
                         "devis", "informations", "sauvegarde", "firewall",
                         "antivirus", "corbeille", "traite"],
                "description": "Si action=ranger. telecom : téléphonie, lignes, "
                               "IPBX, opérateur. impression : copieurs, "
                               "cartouches, traces à l'impression. securite : "
                               "alarme, vidéosurveillance, contrôle d'accès, "
                               "télésurveillance, mail suspect. terrain : "
                               "intervention sur site. devis : demande de prix, "
                               "commerce, facturation. informations : "
                               "notification d'éditeur sans demande. corbeille : "
                               "publicité, prospection. traite : suite d'un "
                               "échange déjà réglé.",
            },
            "client": {
                "type": "string",
                "description": "Si action=creer et qu'un code client de la "
                               "liste proposée correspond, son code. Sinon "
                               "chaîne vide.",
            },
            "confiance": {"type": "string", "enum": ["haute", "moyenne", "basse"]},
            "pourquoi": {
                "type": "string",
                "description": "Une phrase, en français, qui dit ce qui a été "
                               "compris du mail. Elle sera lue par un humain.",
            },
        },
        "required": ["action", "confiance", "pourquoi"],
    },
}

CONSIGNE = """Tu classes les mails d'une boîte de support informatique (société DFM).

DFM a plusieurs métiers, et le support informatique n'en est qu'un :
la téléphonie, l'impression (copieurs), la sécurité électronique (alarme,
vidéosurveillance, télésurveillance) et le commerce ont chacun leur équipe.
Un mail qui relève d'un autre métier ne donne PAS de ticket informatique : il
part dans le dossier de ce métier.

Tu ne crées un ticket que pour une VRAIE demande d'un client sur son
informatique (poste, serveur, réseau, messagerie, comptes, logiciels).
Une publicité, une newsletter, une notification automatique d'éditeur, un
accusé de réception ne donnent jamais de ticket.

On te donne le dernier message ET les messages précédents du même échange.
LIS TOUT. Un mail qui dit seulement « pouvez-vous revenir vers le client
rapidement ? » ne veut rien dire seul : c'est l'historique qui apprend qu'un
Mac est en réparation depuis août, ou qu'un rendez-vous attend depuis huit
jours. Une suite d'échange ne donne pas un nouveau ticket — elle va chez la
personne qui suit déjà le dossier, ou dans le dossier du métier concerné.

Dans le doute, réponds laisser : un humain tranchera. Mieux vaut laisser un
mail en boîte qu'ouvrir un ticket faux ou le ranger au mauvais endroit."""


OUTIL_SYMPTOME = {
    "name": "choisir_symptome",
    "description": "Choisit le symptôme Artis qui décrit le mieux la demande.",
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Le code exact d'un symptôme de la liste "
                               "fournie, ou chaîne vide si aucun ne convient.",
            },
            "pourquoi": {
                "type": "string",
                "description": "Une courte phrase en français : ce qui, dans "
                               "le mail, désigne ce symptôme.",
            },
        },
        "required": ["code", "pourquoi"],
    },
}

CONSIGNE_SYMPTOME = """Tu choisis le symptôme d'un ticket informatique chez DFM.

On te donne la demande d'un client et la liste des symptômes possibles, avec
le nombre de fois où l'équipe les a réellement employés. Choisis celui qui
décrit le mieux la demande.

Deux repères :
  * « Incident » = quelque chose ne marche plus ; « Demande » = on veut
    quelque chose de nouveau ou un changement.
  * Si vraiment aucun ne convient, rends une chaîne vide plutôt que d'en
    forcer un : un symptôme faux est pire qu'un symptôme par défaut."""


def avis_symptome(objet, corps, symptomes, log=None):
    """Le code symptôme le mieux adapté, ou None (le moteur garde son défaut).

    `symptomes` : [(code, libelle, nombre_d_emplois)].
    """
    dire = log or (lambda *_a: None)
    if not disponible() or not symptomes:
        return None
    try:
        import anthropic
    except Exception:
        return None
    liste = "\n".join("%-18s %-56s (%s fois)" % (c, (l or "")[:56], n)
                      for c, l, n in symptomes)
    message = ("Demande du client\nObjet : %s\n\n%s\n\n"
               "Symptômes possibles :\n%s"
               % (objet or "(vide)", (corps or "")[:2000], liste))
    try:
        cli = anthropic.Anthropic(
            api_key=_cle(),
            default_headers={"anthropic-workspace-id": _workspace()}
            if _workspace() else None)
        r = cli.messages.create(
            model=MODELE, max_tokens=300, system=CONSIGNE_SYMPTOME,
            tools=[OUTIL_SYMPTOME],
            tool_choice={"type": "tool", "name": "choisir_symptome"},
            messages=[{"role": "user", "content": message}])
    except Exception as e:
        _compter(0, 0, erreur=True)
        dire("[mail-modele] symptome indisponible : %s" % str(e)[:140])
        return None
    u = getattr(r, "usage", None)
    cout = _compter(getattr(u, "input_tokens", 0) or 0,
                    getattr(u, "output_tokens", 0) or 0)
    codes = {c for c, _l, _n in symptomes}
    for bloc in (r.content or []):
        if getattr(bloc, "type", "") == "tool_use":
            d = dict(bloc.input or {})
            code = (d.get("code") or "").strip()
            # Jamais un code invente : il doit venir de la liste donnee.
            if code and code in codes:
                return {"code": code, "pourquoi": d.get("pourquoi") or "",
                        "cout_eur": round(cout, 5)}
            return None
    return None


ACTIONS = ("ranger", "creer", "laisser")
DOSSIERS_PERMIS = tuple(
    OUTIL["input_schema"]["properties"]["dossier"]["enum"])
CONFIANCES = ("haute", "moyenne", "basse")


def _propre(valeur, permises):
    """Ramene une valeur d'enum a ce qu'elle devait etre, ou "" si elle ne
    correspond a rien.

    Le modele rend d'ordinaire exactement un des mots permis, mais pas
    toujours : le 2026-09-21 il a rendu `confiance` =
    « haute</aniance>
<parameter name="pourquoi">Suite a une demande... »,
    c'est-a-dire sa propre balise de fin abimee suivie du champ suivant. La
    comparaison `confiance == "haute"` echouait donc en silence, et le mail
    d'AQC — un client qui attendait un rappel — restait en boite. On ne
    compare plus jamais une valeur brute : on la ramene d'abord a son mot.
    """
    v = (valeur or "")
    if not isinstance(v, str):
        return ""
    v = v.split("<")[0].strip()
    v = v.strip('"').strip("'").lower()
    for mot in permises:
        if v == mot or v.startswith(mot):
            return mot
    return ""


def _normaliser(d):
    """Nettoie les champs contraints d'un avis. Rend le dictionnaire modifie."""
    if not isinstance(d, dict):
        return d
    d["action"] = _propre(d.get("action"), ACTIONS) or d.get("action")
    d["confiance"] = _propre(d.get("confiance"), CONFIANCES) or ""
    dossier = _propre(d.get("dossier"), DOSSIERS_PERMIS)
    if dossier:
        d["dossier"] = dossier
    cli = d.get("client")
    if isinstance(cli, str):
        # Un code client est un nombre : tout le reste est du bruit.
        import re as _re
        m = _re.match(r"\s*(\d{1,6})", cli)
        d["client"] = m.group(1) if m else ""
    # Le motif est lu par un humain : on coupe ce qui suit une balise.
    pq = d.get("pourquoi")
    if isinstance(pq, str) and "<" in pq:
        d["pourquoi"] = pq.split("<")[0].strip()
    return d


def avis(objet, corps, expediteur, destinataires="", clients_possibles=None,
         log=None):
    """Décision du modèle pour un mail, ou None si le modèle n'est pas là.

    `None` n'est jamais une erreur : c'est le signal que le moteur continue
    seul, exactement comme avant ce module.
    """
    dire = log or (lambda *_a: None)
    if not disponible():
        return None
    try:
        import anthropic
    except Exception:
        return None

    liste = ""
    if clients_possibles:
        liste = "\n\nClients possibles (code — nom) :\n" + "\n".join(
            "%s — %s" % (c, n) for c, n in list(clients_possibles)[:15])
    message = ("Expéditeur : %s\nDestinataires : %s\nObjet : %s\n\n%s%s"
               % (expediteur or "?", destinataires or "?", objet or "(vide)",
                  (corps or "")[:2500], liste))
    try:
        cli = anthropic.Anthropic(
            api_key=_cle(),
            default_headers={"anthropic-workspace-id": _workspace()}
            if _workspace() else None)
        r = cli.messages.create(
            model=MODELE, max_tokens=500, system=CONSIGNE,
            tools=[OUTIL], tool_choice={"type": "tool", "name": "decider"},
            messages=[{"role": "user", "content": message}])
    except Exception as e:
        # Crédit épuisé, clé invalide, réseau : on retombe sur le robot seul.
        _compter(0, 0, erreur=True)
        dire("[mail-modele] indisponible : %s" % str(e)[:140])
        return None

    u = getattr(r, "usage", None)
    cout = _compter(getattr(u, "input_tokens", 0) or 0,
                    getattr(u, "output_tokens", 0) or 0)
    for bloc in (r.content or []):
        if getattr(bloc, "type", "") == "tool_use":
            d = _normaliser(dict(bloc.input or {}))
            d["cout_eur"] = round(cout, 5)
            d["modele"] = MODELE
            return d
    return None
