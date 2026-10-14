# -*- coding: utf-8 -*-
"""L'agent : le jugement d'une session Claude Code, dans un conteneur Azure.

Pourquoi ce fichier existe
--------------------------
`robot_mail_dit.py` fait tourner les REGLES (`mail_dit.decider_seul`). Elles
rangent bien, elles creent prudemment, et elles ne savent pas lire. Elliot, le
2026-10-02 : « je veux que ce soit TOI sur Azure en auto sans mon PC ».

Ce qu'une session interactive fait et que les regles ne font pas, c'est une
boucle : lire le mail, aller chercher ce qui manque dans Artis, revenir sur sa
decision, recommencer. C'est exactement ce qu'on appelle un agent. Ce module
l'implemente avec l'API Anthropic : la consigne du skill `mail-dit` en systeme,
les memes outils que j'utilise exposes comme `tools`, et une boucle qui s'arrete
quand l'agent rend son rapport.

Ce qu'il ne fait PAS
--------------------
Il ne se donne aucun pouvoir qu'une session interactive n'a pas. Tous les
garde-fous sont EN PYTHON, pas dans la consigne — une consigne se contourne par
une tournure de phrase, pas un `if` :

* le drapeau `/data/mail_dit_ecriture.txt` commande toujours l'ecriture ;
* 10 creations par passage, comptees ici, pas promises par le modele ;
* 60 s entre deux DIT du meme client, par une vraie attente ;
* l'anti-doublon reste celui de `mail_dit_ecriture.creer_dit`, inchange, et
  l'agent n'a aucun moyen de le forcer ;
* **aucun outil d'envoi de mail libre.** `transferer` prend un MOTIF, pas une
  adresse : les trois destinations autorisees sont resolues ici. Le modele ne
  peut pas ecrire a un client, meme s'il le decide ;
* la signature « Service MCO - Mail automatique » est ajoutee ici ;
* le type de travail Hotline / Non facturable est pose automatiquement apres le
  rapport, en UNE session web — le modele ne peut pas l'oublier.

Modes
-----
`AGENT_MODE=observation` (defaut) : tous les outils de lecture, et les outils
d'ecriture rendent la SIMULATION sans rien ecrire. `creer_dit` passe par le vrai
`E.creer_dit(confirmer=False)`, donc l'anti-doublon et la construction du corps
sont bel et bien joues. C'est le mode pour comparer avant d'armer.

`AGENT_MODE=actif` : les outils ecrivent, sous reserve du drapeau.

Budget
------
Un agent se paie au jeton et une boucle peut s'emballer. Le compteur vit sur
`/data`, le plafond est mensuel, et `disponible()` ferme la porte quand il est
atteint. Le defaut de 30 EUR n'est pas une decision budgetaire : c'est un
arret, le temps qu'Elliot fixe le vrai chiffre (AGENT_PLAFOND_EUR).

Usage :
    python agent_mail_dit.py                  # un passage
    python agent_mail_dit.py --consigne       # ecrit la consigne pour /data
    python agent_mail_dit.py --cout           # etat du budget
"""
import datetime
import json
import os
import pathlib
import re
import sys
import time

RACINE = pathlib.Path(__file__).resolve().parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

DATA_DIR = pathlib.Path(os.environ.get("DATA_DIR") or RACINE)

MODELE = os.environ.get("AGENT_MODELE", "claude-sonnet-5-5")
MODE = (os.environ.get("AGENT_MODE") or "observation").strip().lower()
MAX_TOURS = int(os.environ.get("AGENT_MAX_TOURS") or 120)
MAX_CREATIONS = int(os.environ.get("AGENT_MAX_CREATIONS") or 10)
DELAI_MEME_CLIENT = 60                      # secondes, regle Elliot
PERIODE = int(os.environ.get("AGENT_PERIODE") or 900)
# Un passage coute. La nuit, la boite ne se remplit pas : le robot a regles
# suffit a ranger les alertes automatiques, et l'agent reprend le matin.
HEURES = os.environ.get("AGENT_HEURES", "7-20")

USAGE = DATA_DIR / "agent_mail_dit_usage.json"
BATTEMENT = DATA_DIR / "agent_mail_dit_battement.json"
CONSIGNE_DATA = DATA_DIR / "agent_consigne.md"

# Plafond mensuel en euros. Voir l'en-tete : c'est un arret, pas un budget.
PLAFOND_EUR = float(os.environ.get("AGENT_PLAFOND_EUR") or 30)
# Tarifs par million de jetons, en dollars. Constantes a corriger si les tarifs
# bougent — elles ne sont lues nulle part ailleurs.
PRIX_ENTREE = float(os.environ.get("AGENT_PRIX_ENTREE") or 2.00)   # Sonnet 5.5
PRIX_SORTIE = float(os.environ.get("AGENT_PRIX_SORTIE") or 10.00)
PRIX_CACHE_ECRIT = float(os.environ.get("AGENT_PRIX_CACHE_ECRIT") or 2.50)
PRIX_CACHE_LU = float(os.environ.get("AGENT_PRIX_CACHE_LU") or 0.10)   # 0,05x sur Sonnet 5.5
USD_VERS_EUR = 0.92

SIGNATURE = "\n\nService MCO - Mail automatique\n"


def log(*a):
    print(datetime.datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def _ecrire_json(chemin, valeur):
    """Ecriture atomique, avec repli en ecriture directe.

    `os.replace` echoue par intermittence sur Azure Files (SMB) : le 2026-10-02
    un `agent_mail_dit_usage.tmp` est reste sur le partage et le compteur du
    mois a perdu un appel. Mieux vaut un fichier brievement incomplet qu'un
    compteur de depense muet.
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        texte = json.dumps(valeur, ensure_ascii=False, indent=1, default=str)
        tmp = chemin.with_suffix(".tmp")
        try:
            tmp.write_text(texte, encoding="utf-8")
            tmp.replace(chemin)
        except Exception:
            chemin.write_text(texte, encoding="utf-8")
            try:
                tmp.unlink()
            except Exception:
                pass
        return True
    except Exception as exc:                 # noqa: BLE001
        log("ecriture %s impossible : %s" % (chemin.name, str(exc)[:120]))
        return False


# ═════════════════════════════════════════════════════════════════ budget
def _mois():
    return datetime.date.today().strftime("%Y-%m")


def usage():
    try:
        d = json.loads(USAGE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault(_mois(), {"passages": 0, "appels": 0, "cout_eur": 0.0,
                           "jetons_entree": 0, "jetons_sortie": 0})
    return d


def depense_du_mois():
    return float(usage()[_mois()].get("cout_eur") or 0.0)


def budget_restant():
    return max(0.0, PLAFOND_EUR - depense_du_mois())


def cle():
    k = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if k:
        return k
    try:
        return (DATA_DIR / "anthropic_key.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def disponible():
    """Trois conditions, dans l'ordre ou elles coutent a verifier."""
    if PLAFOND_EUR <= 0 or budget_restant() <= 0:
        return False
    if not cle():
        return False
    try:
        import anthropic                     # noqa: F401
    except Exception:
        return False
    return True


def _compter(u, passage=False):
    """Ajoute la consommation d'un appel au compteur du mois.

    Les jetons de cache ont leur propre tarif : les compter comme des jetons
    d'entree ordinaires fausserait la facture dans les deux sens — a la hausse
    sur les relectures, a la baisse sur les ecritures de cache.
    """
    e = int(getattr(u, "input_tokens", 0) or 0)
    s = int(getattr(u, "output_tokens", 0) or 0)
    ce = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    cl = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    cout = ((e / 1e6) * PRIX_ENTREE + (s / 1e6) * PRIX_SORTIE
            + (ce / 1e6) * PRIX_CACHE_ECRIT
            + (cl / 1e6) * PRIX_CACHE_LU) * USD_VERS_EUR
    d = usage()
    m = d[_mois()]
    m["appels"] = int(m.get("appels") or 0) + 1
    m["jetons_entree"] = int(m.get("jetons_entree") or 0) + e + ce + cl
    m["jetons_sortie"] = int(m.get("jetons_sortie") or 0) + s
    # Le detail du cache, parce que c'est lui qui decide de la facture : un
    # cache qui ne prend pas multiplie le cout d'une boucle par dix, et
    # l'agregat seul ne permet pas de le voir.
    m["cache_ecrit"] = int(m.get("cache_ecrit") or 0) + ce
    m["cache_lu"] = int(m.get("cache_lu") or 0) + cl
    m["entree_pleine"] = int(m.get("entree_pleine") or 0) + e
    m["cout_eur"] = round(float(m.get("cout_eur") or 0.0) + cout, 4)
    if passage:
        m["passages"] = int(m.get("passages") or 0) + 1
    _ecrire_json(USAGE, d)
    return cout


# ═══════════════════════════════════════════════════════════════ consigne
_FICHIERS_SKILL = (
    "SKILL.md",
    "references/priorites-uo-biens.md",
    "references/pieges-artis.md",
    "references/dossiers-bal.md",
    "references/clients-hors-index.md",
)


def assembler_consigne():
    """Le skill `mail-dit` et ses references, concatenes.

    Une seule source de verite : quand la regle change, c'est le skill qu'on
    modifie et l'agent suit. Rend "" si le skill n'est pas la — c'est le cas EN
    PRODUCTION, ou `.dockerignore` exclut `.claude/`. Le deploiement pose alors
    le resultat de cette fonction sur /data (voir `--consigne`).
    """
    base = RACINE / ".claude" / "skills" / "mail-dit"
    morceaux = []
    for nom in _FICHIERS_SKILL:
        f = base / nom
        if f.exists():
            morceaux.append("<<< %s >>>\n\n%s"
                            % (nom, f.read_text(encoding="utf-8")))
    return "\n\n\n".join(morceaux)


PREAMBULE = """Tu es l'agent de traitement de la boite support@dfm.fr de DFM.
Tu fais, seul et sans personne devant l'ecran, le travail qu'une session Claude
Code fait d'habitude avec Elliot Cohen : lire la boite, decider pour chaque
mail, creer ou completer dans Artis, ranger, rendre un rapport.

La procedure complete suit. Elle fait foi : chaque regle y est datee de
l'incident qui l'a causee.

Comment travailler
------------------
1. Commence par `boite`. Vide -> `rapport` avec quatre listes vides, c'est fini.
2. Pour CHAQUE mail : `lire_mail` d'abord. Le resume de `boite` est tronque, il
   ne sert qu'a trier. Lis aussi le `fil` quand l'objet commence par RE: ou TR:.
3. Ne devine jamais un client par son libelle : passe par `chercher_client`,
   puis verifie avec `client` qu'il a un bien INFOGERANCE ou BAL EXCHANGE 365.
   Pas de bien -> pas de DIT : `transferer` avec le motif `sans_contrat`.
4. Avant de creer, regarde `dits_client` : une reponse a un ticket se complete,
   elle ne se recree pas.
5. Traite les mails un par un, jusqu'au bout de chacun, avant de passer au
   suivant. Ne laisse pas dix dossiers ouverts en meme temps.
6. Termine TOUJOURS par `rapport`. C'est lui qui clot le passage.

Ce que tu ne peux pas faire
---------------------------
Tu n'as aucun outil pour envoyer un mail. `transferer` ne prend pas d'adresse :
il prend un motif, et les trois destinations autorisees sont choisies hors de
ta portee. N'essaie pas de contourner — `signaler` a la place.

Tu ne peux pas forcer l'anti-doublon. S'il refuse, c'est non : tu le dis dans
le rapport, tu laisses le mail en boite, tu passes au suivant.

Le type de travail Hotline / Non facturable est pose automatiquement apres ton
rapport, sur toutes les DIT que tu as creees. Ne t'en occupe pas.

Ce qui est sensible (mot de passe demande, identifiants, destinataire grand
public en copie) : `signaler`, et rien d'autre.

Ecris en francais. Sois bref dans ton raisonnement : chaque mot coute.
"""


def consigne():
    """La consigne systeme, de la source la plus fraiche a la plus lointaine."""
    chemin = (os.environ.get("AGENT_CONSIGNE") or "").strip()
    if chemin and pathlib.Path(chemin).exists():
        return pathlib.Path(chemin).read_text(encoding="utf-8")
    corps = assembler_consigne()
    source = "skill local"
    if not corps and CONSIGNE_DATA.exists():
        corps = CONSIGNE_DATA.read_text(encoding="utf-8")
        source = str(CONSIGNE_DATA)
    if not corps:
        raise RuntimeError(
            "consigne introuvable : ni .claude/skills/mail-dit/ (exclu de "
            "l'image Docker) ni %s. Deposer sur le partage le resultat de "
            "`python agent_mail_dit.py --consigne`." % CONSIGNE_DATA)
    return (PREAMBULE + "\n\n" + "=" * 70 + "\n\n" + corps), source


# ════════════════════════════════════════════════════════════════ outils
#
# Chaque outil est un verbe que j'emploie moi-meme pendant un passage. Les
# schemas sont volontairement etroits : un champ en moins, c'est une facon de
# se tromper en moins. Les outils d'ecriture portent tous `_ECRITURE` dans
# OUTILS_ECRITURE, ce qui suffit a les neutraliser en mode observation.

DOSSIERS_PERMIS = ("traite", "informations", "sauvegarde", "firewall",
                   "antispam", "rmm", "antivirus", "telecom", "impression",
                   "securite", "devis", "terrain", "validation")
# `corbeille` est volontairement absent : la consigne permanente d'Elliot
# interdit toute suppression dans cette boite.

MOTIFS_TRANSFERT = ("intervenant", "sans_contrat", "relais_humain")

# La table des contrats portes par une autre societe du groupe (les
# CLINIQUE DES CHAMPS-ELYSEES chez ELITE CENTER) vit dans
# `mail_dit_ecriture.CONTRAT_PORTE_PAR` : le robot a regles la lit aussi,
# et deux copies finiraient par diverger.

OUTILS = [
    {"name": "boite",
     "description": "Les mails de la boite de reception, du plus recent au plus "
                    "ancien. Objet et expediteur seulement : le corps se lit "
                    "avec lire_mail.",
     "input_schema": {"type": "object", "properties": {}}},

    {"name": "lire_mail",
     "description": "Corps texte complet d'un mail, ses destinataires et la "
                    "liste de ses pieces jointes.",
     "input_schema": {"type": "object", "required": ["mail_id"],
                      "properties": {"mail_id": {"type": "string"}}}},

    {"name": "fil",
     "description": "Les messages precedents du meme fil de discussion, du plus "
                    "ancien au plus recent (extraits). A lire des que l'objet "
                    "commence par RE: ou TR:.",
     "input_schema": {"type": "object", "required": ["mail_id"],
                      "properties": {"mail_id": {"type": "string"}}}},

    {"name": "resoudre_client",
     "description": "LE moyen normal de trouver le client d'un mail : son "
                    "adresse, puis son domaine, dans l'index construit sur "
                    "l'historique des DIT. Rend des candidats ponderes, jamais "
                    "une certitude — a verifier avec `client`. Ne resous "
                    "JAMAIS un client par ressemblance de libelle.",
     "input_schema": {"type": "object", "required": ["adresse"],
                      "properties": {"adresse": {"type": "string"}}}},

    {"name": "chercher_client",
     "description": "Recherche Artis par fragment de raison sociale. Sortie de "
                    "secours quand resoudre_client ne rend rien : l'index est "
                    "fige au 10/09 et quelques clients y manquent.",
     "input_schema": {"type": "object", "required": ["texte"],
                      "properties": {"texte": {"type": "string"}}}},

    {"name": "client",
     "description": "Fiche d'un client : sa raison sociale, ses biens, le bien "
                    "a porter sur une DIT (INFOGERANCE ou BAL EXCHANGE 365, "
                    "null si aucun des deux = pas de contrat Info), et ses "
                    "interlocuteurs.",
     "input_schema": {"type": "object", "required": ["code"],
                      "properties": {"code": {"type": "string"},
                                     "chercher_contact": {
                                         "type": "string",
                                         "description": "nom ou adresse a "
                                                        "retrouver parmi les "
                                                        "interlocuteurs"}}}},

    {"name": "dits_client",
     "description": "Les DIT recentes d'un client, avec leur etat et leurs "
                    "intervenants. A consulter avant toute creation.",
     "input_schema": {"type": "object", "required": ["code"],
                      "properties": {"code": {"type": "string"},
                                     "jours": {"type": "integer",
                                               "description": "defaut 15"}}}},

    {"name": "dit",
     "description": "Detail d'une DIT : client, etat, commentaire, "
                    "intervenants.",
     "input_schema": {"type": "object", "required": ["numero"],
                      "properties": {"numero": {"type": "string"}}}},

    {"name": "symptomes",
     "description": "La liste officielle Artis des symptomes du domaine INFO, "
                    "filtree sur un motif.",
     "input_schema": {"type": "object",
                      "properties": {"motif": {"type": "string"}}}},

    {"name": "annuaire",
     "description": "Adresse interne DFM d'un collegue a partir de son nom.",
     "input_schema": {"type": "object", "required": ["nom"],
                      "properties": {"nom": {"type": "string"}}}},

    # ────────────────────────────────────────────────────────── ecriture
    {"name": "creer_dit",
     "description": "Cree une DIT Artis depuis un mail, pose l'UO, joint les "
                    "pieces du mail, journalise et range le mail en "
                    "01 - DIT ARTIS SUPPORT. L'anti-doublon peut refuser : "
                    "sa reponse fait foi.",
     "input_schema": {
         "type": "object",
         "required": ["mail_id", "client", "objet", "priorite", "symptome",
                      "uo", "commentaire"],
         "properties": {
             "mail_id": {"type": "string"},
             "client": {"type": "string", "description": "code Artis"},
             "bien": {"type": "string",
                      "description": "identifiantFabricant du bien, tel que "
                                     "l'outil client le rend dans "
                                     "`bien_pour_la_dit` (forme « 9599 - "
                                     "INFOGERANCE »). JAMAIS un libelle nu "
                                     "comme « INFOGERANCE » : c'est une cle "
                                     "GLOBALE qui designe le bien d'un AUTRE "
                                     "client. Le mieux est de ne rien passer : "
                                     "l'outil va chercher le bon tout seul."},
             "pid_interlocuteur": {
                 "type": "integer",
                 "description": "identifiant du demandeur chez le client. "
                                "Quand un collegue @dfm.fr relaie la demande, "
                                "ce n'est pas lui le demandeur : chercher dans "
                                "l'historique du fil la personne du client qui "
                                "a ecrit, et passer SON id. Omettre seulement "
                                "si le fil ne nomme aucun client."},
             "objet": {"type": "string"},
             "priorite": {"type": "string", "enum": ["01", "02", "03"],
                          "description": "03 personne bloquee / 02 une "
                                         "personne / 01 toute la societe"},
             "symptome": {"type": "string"},
             "uo": {"type": "string", "enum": ["HLI", "HLN3"]},
             "commentaire": {"type": "string",
                             "description": "objet puis texte du client MOT "
                                            "POUR MOT, signature exclue"}}}},

    {"name": "completer_dit",
     "description": "Ajoute le mail du client a une DIT existante : pose le "
                    "commentaire sous banniere, joint les pieces, previent le "
                    "dernier intervenant, range le mail. C'est la reponse a "
                    "tout objet portant [IT ...] ou [DIT ...]. Une DIT CLOSE "
                    "N'EST JAMAIS ROUVERTE : le commentaire se pose quand meme "
                    "et le mail part au dernier intervenant, a qui il revient "
                    "de rouvrir ou d'ouvrir un ticket neuf. Vrai aussi quand le "
                    "client redemande du travail.",
     "input_schema": {"type": "object",
                      "required": ["mail_id", "numero", "commentaire"],
                      "properties": {"mail_id": {"type": "string"},
                                     "numero": {"type": "string"},
                                     "commentaire": {"type": "string"}}}},

    {"name": "ranger",
     "description": "Classe un mail qui ne donne pas de ticket dans le dossier "
                    "voulu, et le marque lu.",
     "input_schema": {"type": "object",
                      "required": ["mail_id", "dossier", "raison"],
                      "properties": {
                          "mail_id": {"type": "string"},
                          "dossier": {"type": "string",
                                      "enum": list(DOSSIERS_PERMIS)},
                          "raison": {"type": "string"}}}},

    {"name": "transferer",
     "description": "Transfere un mail en interne. Trois motifs seulement : "
                    "`intervenant` (le dernier technicien de la DIT, numero "
                    "obligatoire), `sans_contrat` (client sans contrat Info -> "
                    "son commercial informatique, code client obligatoire), "
                    "`relais_humain` (Clifford, quand le client attend une "
                    "reponse humaine qu'aucun ticket ne produit). L'adresse "
                    "est choisie par l'outil, pas par toi.",
     "input_schema": {"type": "object",
                      "required": ["mail_id", "motif", "mot"],
                      "properties": {
                          "mail_id": {"type": "string"},
                          "motif": {"type": "string",
                                    "enum": list(MOTIFS_TRANSFERT)},
                          "dit": {"type": "string",
                                  "description": "numero de DIT, obligatoire "
                                                 "pour le motif intervenant"},
                          "client": {"type": "string",
                                     "description": "code client Artis, "
                                                    "obligatoire pour le motif "
                                                    "sans_contrat"},
                          "mot": {"type": "string",
                                  "description": "une ou deux phrases : ce qui "
                                                 "est demande et pourquoi on "
                                                 "transfere. La signature est "
                                                 "ajoutee automatiquement."}}}},

    {"name": "signaler",
     "description": "Laisse le mail en boite et le porte a la connaissance "
                    "d'Elliot. Pour tout ce qui est sensible ou qu'on ne sait "
                    "pas trancher.",
     "input_schema": {"type": "object", "required": ["mail_id", "raison"],
                      "properties": {"mail_id": {"type": "string"},
                                     "raison": {"type": "string"}}}},

    {"name": "journaliser",
     "description": "Ajoute une ligne au journal mail->DIT. Les creations, "
                    "complements, rangements et transferts sont deja "
                    "journalises par leurs outils : n'utilise celui-ci que "
                    "pour ce qu'ils ne couvrent pas.",
     "input_schema": {"type": "object", "required": ["action", "detail"],
                      "properties": {
                          "action": {"type": "string",
                                     "enum": ["creation", "complement",
                                              "rangement", "transfert",
                                              "annulation", "laisse", "piece"]},
                          "detail": {"type": "string"},
                          "mail_de": {"type": "string"},
                          "mail_objet": {"type": "string"},
                          "dit": {"type": "string"},
                          "client_code": {"type": "string"}}}},

    {"name": "rapport",
     "description": "Clot le passage. Une ligne par mail traite, courte, sans "
                    "conclusion ni synthese.",
     "input_schema": {
         "type": "object",
         "required": ["cree", "complete", "range", "laisse"],
         "properties": {
             "cree": {"type": "array", "items": {"type": "string"}},
             "complete": {"type": "array", "items": {"type": "string"}},
             "range": {"type": "array", "items": {"type": "string"}},
             "laisse": {"type": "array", "items": {"type": "string"},
                        "description": "chaque mail laisse en boite AVEC sa "
                                       "raison : on ne laisse rien sans "
                                       "raison ecrite"}}}},
]

OUTILS_ECRITURE = {"creer_dit", "completer_dit", "ranger", "transferer"}


# ═════════════════════════════════════════════════ contexte d'un passage
class Contexte(object):
    """Ce que le passage accumule et que le modele ne doit pas pouvoir mentir.

    Les compteurs de creation et la cadence vivent ici, pas dans la consigne :
    c'est la difference entre un garde-fou et une recommandation.
    """

    def __init__(self, actif):
        self.actif = actif
        self.crees = []             # [{numero, client, uo, objet, a_valider}]
        self.completes = []
        self.tags_validation = []   # DIT dont le valideur client vient de repondre
        self.ranges = []
        self.transferts = []
        self.signales = []
        self.dernier_client = None
        self.dernier_client_t = 0.0
        self.rapport = None
        self.erreurs = []
        self.mails_initiaux = []

    def peut_creer(self):
        return len(self.crees) < MAX_CREATIONS

    def cadencer(self, client):
        """Pas deux DIT du meme client a moins de 60 s. L'attente est reelle :
        c'est elle qui empeche Artis de fabriquer deux tickets jumeaux."""
        if self.dernier_client == client:
            reste = DELAI_MEME_CLIENT - (time.time() - self.dernier_client_t)
            if reste > 0:
                log("meme client que la DIT precedente : attente %d s" % reste)
                time.sleep(reste)
        self.dernier_client = client
        self.dernier_client_t = time.time()


# ════════════════════════════════════════════════════════ acces aux mails
_RE_BALISE = re.compile(r"<[^>]+>")
_RE_BLANCS = re.compile(r"\n{3,}")


def _texte(corps):
    """Un corps Graph en texte lisible. Avec l'en-tete `Prefer: text`, Graph
    rend deja du texte ; le repli ne sert qu'aux rares mails qui passent a
    travers."""
    contenu = (corps or {}).get("content") or ""
    if ((corps or {}).get("contentType") or "").lower() == "html":
        contenu = contenu.replace("</p>", "\n").replace("<br>", "\n")
        contenu = _RE_BALISE.sub("", contenu)
        for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"')):
            contenu = contenu.replace(a, b)
    return _RE_BLANCS.sub("\n\n", contenu).strip()


def _adr(x):
    return ((x or {}).get("emailAddress") or {}).get("address") or ""


def _nom(x):
    return ((x or {}).get("emailAddress") or {}).get("name") or ""


def _resume_mail(m):
    return {"mail_id": m.get("id"),
            "objet": m.get("subject"),
            "de": _adr(m.get("from")),
            "de_nom": _nom(m.get("from")),
            "recu": m.get("receivedDateTime"),
            "lu": m.get("isRead"),
            "pieces": bool(m.get("hasAttachments")),
            "pour": [_adr(x) for x in (m.get("toRecipients") or [])],
            "copie": [_adr(x) for x in (m.get("ccRecipients") or [])]}


# ════════════════════════════════════════════════════ execution des outils
def executer(nom, args, ctx, M, E, J):
    """Un outil, son resultat. Ne leve jamais : une panne d'Artis doit revenir
    au modele sous forme de message, pas casser le passage."""
    try:
        return _executer(nom, args, ctx, M, E, J)
    except Exception as exc:                 # noqa: BLE001
        ctx.erreurs.append("%s : %s" % (nom, repr(exc)[:200]))
        return {"erreur": repr(exc)[:300]}


def _tok(M):
    tok = M.access_token()
    if not tok:
        raise RuntimeError("pas de jeton mail")
    return tok


def _message(M, mail_id, complet=True):
    import requests
    tok = _tok(M)
    champs = ("id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
              "isRead,hasAttachments,conversationId"
              + (",body,internetMessageId" if complet else ""))
    r = M._get(tok, "%s/me/messages/%s" % (M.GRAPH, mail_id),
               {"$select": champs})
    if r.status_code != 200:
        raise RuntimeError("mail %s : HTTP %s" % (mail_id[:12], r.status_code))
    return r.json()


def _nom_prenom_expediteur(m, adresse):
    """(NOM, Prenom) de l'expediteur, ou (None, None) si ce n'est pas une
    personne qu'on puisse inscrire dans la fiche d'un client.

    Trois refus, repris des regles du skill :
    - un expediteur INTERNE n'est jamais le demandeur, il est le facteur ;
    - un expediteur AUTOMATIQUE n'est pas une personne (`alerts@`, `jira@`…) ;
    - un nom d'UN SEUL MOT ne suffit pas — « Compta », « Direction » ou une
      boite collective ne sont pas des interlocuteurs nominatifs.
    """
    import mail_dit as _M
    adr = (adresse or "").strip()
    if not adr or _M.est_interne(adr):
        return None, None
    import mail_dit_ecriture as _E
    if _E._est_expediteur_automatique(adr):
        return None, None
    brut = ((m.get("from") or {}).get("emailAddress") or {}).get("name") or ""
    brut = brut.strip()
    if "@" in brut or "," in brut:               # « nom, prenom » ou une adresse
        return None, None
    # Jamais couper sur le tiret : « Souci-Guedj » et « LE GAUDU » sont des
    # noms entiers, les casser donne un demandeur faux dans la fiche client.
    mots = [p for p in brut.split() if len(p) > 1]
    if len(mots) < 2:
        return None, None
    # Convention des signatures : le NOM est en capitales, le reste est le
    # prenom — « Olivier HOSATTE », « Margerie LE GAUDU ». Faute de capitales,
    # usage francais : le dernier mot est le nom.
    hauts = [p for p in mots if p.isupper()]
    bas = [p for p in mots if not p.isupper()]
    if hauts and bas:
        return " ".join(hauts), " ".join(bas)
    return mots[-1], " ".join(mots[:-1])


# Les trois modeles qui portent un contrat d'infogerance informatique, dans
# l'ordre de preference voulu par Elliot (2026-10-09). Compares a
# `modele.code` EXACTEMENT : « INFOGERANCE AUDIO » n'est pas de l'infogerance
# informatique, et « 17661 - Materiel Videosurveillance » non plus.
MODELES_BIEN = ("INFOGERANCE", "BAL EXCHANGE 365", "PROTECT")


def _bien_et_site(E, code, valide=None, trace=None):
    """(identifiantFabricant, codeSite) du bien a poser dans la DIT, ou (None, None).

    Regle posee par Elliot le 2026-10-09 : « tu mets toujours le bien du SITE
    principal (et evidemment toujours du meme client) INFOGERANCE, si tu
    trouves pas BAL EXCHANGE, sinon PROTECT, et si toujours rien tu passes au
    site suivant ».

    Donc on balaye les SITES, principal d'abord, et dans chaque site les trois
    modeles dans cet ordre. Le premier qui repond gagne, et c'est SON site
    qu'on posera — jamais un site calcule a part.

    Le tri se fait sur `modele.code`, EXACTEMENT, jamais sur « le libelle
    contient INFOG » : Constantia Jeanne d'Arc (17661) n'a que de la securite
    et un bien « INFOGERANCE **AUDIO** », que l'ancien filtre prenait pour un
    contrat d'infogerance informatique.

    `maxResults` est INDISPENSABLE : l'API pagine a 20 et le bien
    d'infogerance est souvent au-dela (client 9599 : 166 biens).

    Avec `valide=<identifiant>`, la fonction ne cherche plus : elle repond si
    cet identifiant appartient bien a ce client. Necessaire parce que
    `identifiantFabricant` est une cle GLOBALE chez Artis — « INFOGERANCE »
    tout court est le bien du client 14630 (XILAM), et `POST /dit` suit le
    site du bien, pas le `codeClient` qu'on lui donne.
    """
    import requests
    base, tok = E._artis()

    def _get(url, **params):
        try:
            p = {"token": tok}
            p.update(params)
            r = requests.get(url, params=p,
                             headers={"accept": "application/json"}, timeout=90)
            return r.json() if r.status_code == 200 else []
        except Exception:                    # noqa: BLE001
            return []

    cible = str(code).strip()
    porteur = E.CONTRAT_PORTE_PAR.get(cible)
    if porteur:
        # Le contrat est porte par une autre societe du groupe : les biens de
        # cette filiale vivent dans la fiche du porteur, prefixes de son code.
        # Elliot a tranche le 2026-10-12 : on pose le bien du porteur, bien et
        # site exacts — le ticket s'affichera donc a son nom.
        biens = E.biens_portes(cible)
        if valide is None:
            return E.bien_porte(cible, trace=trace)
        if trace is not None:
            trace["porteur"] = porteur
    else:
        biens = _get("%s/client/%s/bien" % (base, cible), maxResults=5000)
    if valide is not None:
        v = str(valide).strip().upper()
        for b in biens:
            if (b.get("identifiantFabricant") or "").strip().upper() == v:
                return (b.get("identifiantFabricant"),
                        (b.get("site") or {}).get("code"),
                        (b.get("orgInt") or {}).get("code"))
        return (None, None, None)

    sites = _get("%s/client/%s/site" % (base, cible))
    ordre = ([s for s in sites if s.get("principal")]
             + [s for s in sites if not s.get("principal")])
    # Un client sans site expose quand meme ses biens : on finit par une passe
    # « tous sites confondus » plutot que de renoncer au ticket.
    ordre = ordre or [None]
    for site in ordre:
        sid = (site or {}).get("id")
        for modele in MODELES_BIEN:
            for b in biens:
                if (b.get("modele") or {}).get("code", "").strip().upper() != modele:
                    continue
                if sid is not None and (b.get("site") or {}).get("id") != sid:
                    continue
                return (b.get("identifiantFabricant"),
                        (b.get("site") or {}).get("code"),
                        (b.get("orgInt") or {}).get("code"))
    return (None, None, None)


def _bien_du_client(E, code, valide=None):
    """Le seul identifiant du bien — voir `_bien_et_site`."""
    return _bien_et_site(E, code, valide=valide)[0]


def _prop_depuis_mail(M, m, **extra):
    """Le dict `prop` qu'attendent `mail_dit_ecriture` et le journal.

    Il est fabrique ICI, a partir du mail reel, et non par le modele : le
    `message_id` et le `conversation` sont ce qui empeche un second ticket sur
    la meme demande, et on ne confie pas ces deux-la a un texte genere.
    """
    prop = {"mail_id": m.get("id"),
            "message_id": m.get("internetMessageId"),
            "conversation": m.get("conversationId"),
            "objet": m.get("subject"),
            "de": _adr(m.get("from")),
            "de_nom": _nom(m.get("from")),
            "recu": m.get("receivedDateTime"),
            "date_dit": M._date_locale(m.get("receivedDateTime")),
            "categorie": "hotline"}
    prop.update(extra)
    return prop


def _executer(nom, args, ctx, M, E, J):
    # ─────────────────────────────────────────────────────────── lecture
    if nom == "boite":
        tok = _tok(M)
        fid, total = M._id_boite(tok)
        msgs = M._boite(tok, fid)
        msgs.sort(key=lambda m: m.get("receivedDateTime") or "", reverse=True)
        out = [_resume_mail(m) for m in msgs]
        if not ctx.mails_initiaux:
            ctx.mails_initiaux = [m["mail_id"] for m in out]
        return {"total": len(out), "mails": out}

    if nom == "lire_mail":
        m = _message(M, args["mail_id"])
        tok = _tok(M)
        try:
            pieces = M.pieces_du_mail(tok, m["id"],
                                      conversation=m.get("conversationId"))
        except Exception:
            pieces = []
        return {"objet": m.get("subject"), "de": _adr(m.get("from")),
                "de_nom": _nom(m.get("from")),
                "recu": m.get("receivedDateTime"),
                "date_pour_la_dit": M._date_locale(m.get("receivedDateTime")),
                "pour": [_adr(x) for x in (m.get("toRecipients") or [])],
                "copie": [_adr(x) for x in (m.get("ccRecipients") or [])],
                "corps": _texte(m.get("body"))[:20000],
                "pieces": [{"nom": p.get("nom"), "type": p.get("type"),
                            "taille": p.get("taille"),
                            "incrustee": p.get("incrustee")} for p in pieces]}

    if nom == "fil":
        m = _message(M, args["mail_id"], complet=False)
        tok = _tok(M)
        msgs = M.fil_de_discussion(tok, m.get("conversationId"))
        out = []
        for x in msgs:
            if x.get("id") == m.get("id"):
                continue
            out.append({"objet": x.get("subject"), "de": _adr(x.get("from")),
                        "recu": x.get("receivedDateTime"),
                        "dossier": x.get("_folder"),
                        "extrait": _texte(x.get("body"))[:1500]})
        return {"messages": out[-10:], "total": len(out)}

    if nom == "resoudre_client":
        adresse = (args["adresse"] or "").strip().lower()
        idx = M._index_clients()
        libelles = M._libelles_clients()
        domaine = adresse.split("@", 1)[1] if "@" in adresse else adresse
        # `emails` rend des dicts {client, id, nom} : le `id` est le
        # pid_interlocuteur du demandeur, deja connu d'Artis. C'est la
        # meilleure piste possible — adresse exacte ET contact existant.
        exact = [{"client": x.get("client"),
                  "libelle": libelles.get(x.get("client")),
                  "pid_interlocuteur": x.get("id"), "nom": x.get("nom")}
                 for x in ((idx.get("emails") or {}).get(adresse) or [])
                 if isinstance(x, dict)]
        par_dom = [{"client": c, "libelle": libelles.get(c), "poids": n}
                   for c, n in ((idx.get("domaines") or {}).get(domaine) or [])]
        return {"par_adresse_exacte": exact, "par_domaine": par_dom[:8],
                "domaine": domaine,
                "rappel": "verifier avec `client` (biens, interlocuteurs) "
                          "avant de creer. Rien ici ? essaie chercher_client."}

    if nom == "chercher_client":
        import requests
        base, tok = E._artis()
        # `chaineRecherchee` est le parametre qui filtre vraiment (`libelle`
        # rend toute la base), et la raison sociale s'appelle `raisonSociale`
        # — `nom` existe mais reste vide sur les fiches clients.
        r = requests.get("%s/client" % base,
                         params={"token": tok, "chaineRecherchee": args["texte"],
                                 "maxResults": 20},
                         headers={"accept": "application/json"}, timeout=40)
        if r.status_code != 200:
            return {"erreur": "recherche client HTTP %s" % r.status_code}
        out = []
        for c in (r.json() or []):
            if not isinstance(c, dict):
                continue
            out.append({"code": (c.get("code") or "").strip(),
                        "nom": c.get("raisonSociale") or c.get("libelle")})
        return {"clients": out[:20]}

    if nom == "client":
        import requests
        code = str(args["code"]).strip()
        base, tok = E._artis()
        fiche = {}
        try:
            rf = requests.get("%s/client/%s" % (base, code),
                              params={"token": tok},
                              headers={"accept": "application/json"}, timeout=40)
            fiche = rf.json() if rf.status_code == 200 else {}
        except Exception:
            fiche = {}
        # `maxResults` est INDISPENSABLE : l'API pagine a 20 et le bien
        # d'infogerance est souvent au-dela (client 9599 : 166 biens).
        liste = []
        try:
            rb = requests.get("%s/client/%s/bien" % (base, code),
                              params={"token": tok, "maxResults": 5000},
                              headers={"accept": "application/json"}, timeout=90)
            liste = rb.json() if rb.status_code == 200 else []
        except Exception:
            liste = []
        bien = _bien_du_client(E, code)
        inters = []
        try:
            inters = [{"id": i.get("id"),
                       "nom": (i.get("prenom") + " " + i.get("nom")).strip(),
                       "mail": i.get("mail")}
                      for i in E.interlocuteurs_artis(code)]
        except Exception as exc:             # noqa: BLE001
            inters = [{"erreur": str(exc)[:120]}]
        cherche = (args.get("chercher_contact") or "").strip().lower()
        if cherche:
            filtres = [i for i in inters
                       if cherche in ((i.get("nom") or "") + " "
                                      + (i.get("mail") or "")).lower()]
            inters = filtres or inters
        return {"code": code,
                "raison_sociale": (fiche.get("raisonSociale")
                                   or fiche.get("libelle")),
                "bien_pour_la_dit": bien,
                "contrat_info": bool(bien),
                # On rend l'IDENTIFIANT, pas le libelle. Le libelle nu
                # (« INFOGERANCE ») est un identifiant valide chez un AUTRE
                # client : `identifiantFabricant` est une cle GLOBALE dans
                # Artis. La DIT 100176726 (MYMOBILITY 9599) est partie avec le
                # bien « INFOGERANCE » du client 14630 XILAM, et Artis lui a
                # colle le site de XILAM — releve par Elliot le 2026-10-09 :
                # « comment il a fait pour mettre XILAM ? c'est meme pas un
                # site du client ».
                "biens": ["%s | %s" % (b.get("identifiantFabricant") or "",
                                       b.get("libelle") or "")
                          for b in liste][:40],
                "nb_biens": len(liste),
                "interlocuteurs": inters[:40]}

    if nom == "dits_client":
        import requests
        base, tok = E._artis()
        jours = int(args.get("jours") or 15)
        fin = datetime.date.today()
        deb = fin - datetime.timedelta(days=jours)
        r = requests.get("%s/client/%s/dit" % (base, str(args["code"]).strip()),
                         params={"token": tok, "dateDebut": deb.isoformat(),
                                 "dateFin": fin.isoformat(), "maxResults": 200},
                         headers={"accept": "application/json"}, timeout=60)
        if r.status_code != 200:
            return {"erreur": "DIT client HTTP %s" % r.status_code}
        out = []
        for d in (r.json() or []):
            ivs = []
            for it in (d.get("inter") or []):
                iv = it.get("intervenant") or {}
                ivs.append({"it": it.get("numero"), "etat": it.get("etat"),
                            "qui": " ".join(x for x in (iv.get("prenom"),
                                                        iv.get("nom")) if x)})
            out.append({"numero": d.get("numero"), "date": d.get("date"),
                        "etat": d.get("etat"), "objet": d.get("libelle")
                        or d.get("numeroExterne"), "interventions": ivs})
        out.sort(key=lambda x: x.get("date") or "", reverse=True)
        return {"dits": out[:40]}

    if nom == "dit":
        d = E.relire_dit(str(args["numero"]).strip()) or {}
        iv = E.dernier_intervenant(str(args["numero"]).strip())
        return {"dit": {k: v for k, v in d.items() if k != "commentaire"},
                "commentaire": (d.get("commentaire") or "")[:6000],
                "dernier_intervenant": iv}

    if nom == "symptomes":
        motif = (args.get("motif") or "").strip().lower()
        liste = M.symptomes_officiels("INFO")
        if motif:
            liste = [s for s in liste
                     if motif in (s.get("code") or "").lower()
                     or motif in (s.get("libelle") or "").lower()]
        return {"symptomes": liste[:60], "total": len(liste)}

    if nom == "annuaire":
        return {"adresse": E._adresse_par_nom(args["nom"]) or None}

    # ────────────────────────────────────────────────────────── ecriture
    if nom == "creer_dit":
        if not ctx.peut_creer():
            return {"refus": "plafond de %d creations atteint pour ce passage. "
                             "Laisse le mail en boite et dis-le dans le "
                             "rapport." % MAX_CREATIONS}
        m = _message(M, args["mail_id"])
        # Le Bien ne se rattrape pas apres coup : il n'est ni dans
        # `GET /dit/{n}` ni dans aucune route d'ecriture hors creation. Et
        # sans lui la DIT part avec le seul site, donc « Pas de bien » a
        # l'ecran (DIT 100176820, NMCG 30706, relevee par Elliot le
        # 2026-10-08). On va donc le chercher quand l'appelant ne l'a pas
        # passe, au lieu de compter sur sa vigilance.
        bien = (args.get("bien") or "").strip()
        bien_cherche = False
        bien_rejete = None
        site = org = None
        trace_bien = {}
        if not bien:
            bien, site, org = _bien_et_site(E, args["client"],
                                            trace=trace_bien)
            bien = bien or ""
            bien_cherche = True
        else:
            # Le bien passe par l'appelant n'est JAMAIS cru sur parole :
            # `identifiantFabricant` est une cle GLOBALE dans Artis, pas une
            # cle du client. Un libelle nu (« INFOGERANCE ») designe donc le
            # bien d'un autre client -- et Artis attache alors a la DIT le
            # SITE de cet autre client, en ignorant le codeSite qu'on envoie.
            # C'est ce qui a mis le site « XILAM » (client 14630) sur la DIT
            # 100176726 de MYMOBILITY (9599) le 2026-10-07. Le champ ne se
            # rattrape pas apres coup : on verifie AVANT.
            bien, site, org = _bien_et_site(E, args["client"], valide=bien,
                                            trace=trace_bien)
            if not bien:
                bien_rejete = args["bien"]
                bien, site, org = _bien_et_site(E, args["client"],
                                                trace=trace_bien)
                bien = bien or ""
                bien_cherche = True
        prop = _prop_depuis_mail(
            M, m,
            client=str(args["client"]).strip(),
            bien=bien,
            site=site,
            # L'organisation interne vient du BIEN : tous nos clients ne sont
            # pas chez DFM (GALYO 12579 est chez AVBU), et une DIT creee dans
            # la mauvaise organisation ne peut pas porter leur bien.
            org_int=org or "DFM",
            objet=args.get("objet") or m.get("subject"),
            priorite=args["priorite"],
            symptome=args["symptome"],
            uo=args["uo"],
            commentaire=args["commentaire"])
        # Garde-fou que la consigne ne peut pas tenir seule : un expediteur
        # interne n'est jamais le demandeur, il est le facteur. On jette donc
        # le demandeur DEDUIT DE L'EXPEDITEUR -- mais pas celui que l'appelant
        # a trouve dans l'historique du fil : c'est un interlocuteur de la
        # fiche client, donc jamais un collegue. Elliot, 2026-10-05, sur la
        # DIT 100176368 (HYLA, relayee par service.client) : « le ticket cree
        # mais le contact n'a pas ete mis, il aurait fallu mettre le mail
        # compta@, c'est ce mail qui signale le mail frauduleux a DFM ».
        if prop["de"].lower().endswith("@dfm.fr"):
            prop.pop("pid_interlocuteur", None)
        if args.get("pid_interlocuteur"):
            prop["pid_interlocuteur"] = int(args["pid_interlocuteur"])

        # Le demandeur ne se rattrape PAS apres coup : aucune route REST ne
        # l'ecrit hors creation. Donc quand l'expediteur est une vraie personne
        # du client et qu'elle n'est pas encore dans la fiche, on CREE le
        # contact ici plutot que de laisser partir une DIT anonyme — Elliot,
        # 2026-10-06 : « juste tu l'ajoutes, et s'il existe pas tu le crees ».
        # Sans ce garde-fou, la 100176991 (JEAN-LOUIS DENIOT, Piraveena
        # YOGANATHAN) et la 100176990 (NMCG, Charlotte SOUCI-GUEDJ) sont
        # parties sans demandeur le 2026-10-08, et il a fallu corriger a la
        # main dans Artis. Elliot, le 2026-10-09 : « Aucun demandeur, pourquoi ? »
        contact_cree = None
        # CHERCHER AVANT DE CREER. `E.creer_dit` resout le demandeur tout seul,
        # mais plus loin : si on cree ici sans avoir cherche, on fabrique un
        # doublon a chaque mail d'un contact deja connu. C'est arrive le
        # 2026-10-09 chez FREITAS LEVAGE (12390) : « Stephanie FREITAS » pid
        # 225069 cree alors que « STEPHANIE FREITAS » pid 118627 portait deja
        # la meme adresse. Une fiche client polluee ne se nettoie qu'a la main.
        if ctx.actif and not prop.get("pid_interlocuteur"):
            try:
                deja = E.trouver_demandeur(prop["client"],
                                           prop.get("de_nom") or "",
                                           prop.get("de") or "")
            except Exception:                     # noqa: BLE001
                deja = None
            if deja and deja.get("id"):
                prop["pid_interlocuteur"] = int(deja["id"])
        if ctx.actif and not prop.get("pid_interlocuteur"):
            nom, prenom = _nom_prenom_expediteur(m, prop["de"])
            if nom and prenom:
                try:
                    c = E.creer_interlocuteur(prop["client"], nom, prenom,
                                              prop["de"], titre="M.",
                                              confirmer=True)
                except Exception:                 # noqa: BLE001
                    c = {}
                if c.get("id"):
                    prop["pid_interlocuteur"] = int(c["id"])
                    contact_cree = "%s %s (%s)" % (prenom, nom, prop["de"])

        if not ctx.actif:
            r = E.creer_dit(prop, confirmer=False)
            return {"simulation": True, "mode": "observation",
                    "bloquants": r.get("bloquants"),
                    "avertissements": r.get("avertissements"),
                    "doublon_de": r.get("doublon_de"),
                    "corps": {k: v for k, v in (r.get("corps") or {}).items()
                              if k != "commentaire"}}

        # L'adresse du demandeur ne vient PAS du message : Artis la lit
        # dans la fiche du contact. Si elle y manque, on l'y pose avant
        # de creer, pour que la DIT la porte (Elliot, 2026-10-06, sur la
        # DIT 100176656 : « juste tu l'ajoutes, et s'il existe pas tu le
        # crees »). Une fiche qui a deja une adresse garde la sienne.
        mail_pose = None
        if prop.get("pid_interlocuteur"):
            try:
                a = E.assurer_mail_interlocuteur(
                    prop["client"], prop["pid_interlocuteur"],
                    prop.get("de") or "", confirmer=True)
                mail_pose = a.get("pose")
            except Exception:            # noqa: BLE001
                mail_pose = None
        ctx.cadencer(prop["client"])
        r = E.creer_dit(prop, confirmer=True)
        if r.get("erreur") == "doublon evite":
            return {"refus": "doublon : la DIT %s couvre deja cet incident. Ne "
                             "pas contourner." % r.get("doublon_de"),
                    "doublon_de": r.get("doublon_de")}
        if not r.get("envoye"):
            return {"erreur": str(r.get("erreur"))[:300],
                    "bloquants": r.get("bloquants")}
        numero = str(r["numero"])
        faits = ["DIT %s creee" % numero]
        if bien_rejete:
            faits.append("bien « %s » REFUSE : il n'appartient pas au client "
                         "%s. `identifiantFabricant` est une cle globale chez "
                         "Artis et la DIT serait partie avec le site de son "
                         "vrai proprietaire — a signaler dans le rapport"
                         % (bien_rejete, prop["client"]))
        if bien_cherche and bien:
            faits.append("bien %s pose (non fourni par l'appelant), site %s, "
                         "organisation interne %s" % (bien, site, org or "DFM"))
        if trace_bien.get("porteur") and bien:
            faits.append("CONTRAT PORTE : le bien vient de la fiche du client "
                         "%s, qui porte l'infogerance de celui-ci. La DIT "
                         "s'affichera au nom de %s — choix d'Elliot du "
                         "2026-10-12. A dire dans le rapport."
                         % (trace_bien["porteur"], trace_bien["porteur"]))
            if (trace_bien.get("candidats") or 0) > 1:
                faits.append("VILLE A VERIFIER : ce code porte %d biens « %s » "
                             "chez %s (plusieurs villes sous le meme code). "
                             "Retenu : %s%s. Rien ne se corrige apres creation."
                             % (trace_bien["candidats"],
                                trace_bien.get("modele") or "",
                                trace_bien["porteur"], bien,
                                (" (ville %s)" % trace_bien["ville"])
                                if trace_bien.get("ville") else
                                " — AUCUNE ville reconnue dans la raison sociale"))
        elif not bien:
            faits.append("SANS BIEN : le client n'a ni INFOGERANCE ni BAL "
                         "EXCHANGE 365 — a signaler dans le rapport")
        if contact_cree:
            faits.append("contact CREE dans la fiche client et pose comme "
                         "demandeur : %s — a signaler dans le rapport"
                         % contact_cree)
        elif not prop.get("pid_interlocuteur"):
            faits.append("SANS DEMANDEUR : aucun contact resolu et l'expediteur "
                         "n'est pas une personne inscriptible — a signaler, le "
                         "champ ne se rattrape pas apres coup")
        if mail_pose:
            faits.append("adresse %s posee sur la fiche du demandeur"
                         % mail_pose)
        try:
            J.ajouter("creation", mail_de=prop["de"], mail_objet=prop["objet"],
                      mail_recu=prop.get("date_dit"), client_code=prop["client"],
                      dit=numero, dossier="01 - DIT ARTIS SUPPORT",
                      par="agent-azure",
                      detail="%s / %s / priorite %s" % (prop["symptome"],
                                                        prop["uo"],
                                                        prop["priorite"]))
        except Exception as exc:             # noqa: BLE001
            faits.append("journal non ecrit (%s)" % str(exc)[:60])
        # L'UO se pose sur le numero d'INTERVENTION = numero de DIT + 01.
        intervention = numero + "01"
        for essai in range(4):
            u = E.poser_uo(intervention, prop["uo"], confirmer=True)
            if u.get("envoye"):
                faits.append("UO %s posee" % prop["uo"])
                break
            time.sleep(6)
        else:
            faits.append("UO %s NON posee : %s"
                         % (prop["uo"], str(u.get("erreur"))[:120]))
        pj = E.joindre_pieces(numero, prop, True)
        if pj.get("envoye"):
            faits.append("%d piece(s) jointe(s)" % len(pj.get("pieces") or []))
        # Relecture : le SITE que `POST /dit` a reellement pose. Artis suit le
        # bien, pas le `codeClient` — un bien etranger emmene son site avec
        # lui, en silence. Le champ ne se rattrape pas apres coup, donc la
        # seule chose utile est de le DIRE tout de suite (DIT 100176726,
        # site « XILAM » sur un ticket MYMOBILITY, 2026-10-07).
        try:
            import requests
            base, tok = E._artis()
            d = requests.get("%s/dit/%s" % (base, numero),
                             params={"token": tok},
                             headers={"accept": "application/json"},
                             timeout=30).json()
            it0 = (d.get("inter") or [{}])[0]
            sid = (it0.get("site") or {}).get("id")
            snom = (it0.get("site") or {}).get("nom") or ""
            # On compare a la LISTE des sites du client, pas a son libelle :
            # un client a souvent des sites qui portent un autre nom
            # (ARCOTEL 13392 a « HOTEL MAGDA » et « HOTEL ACACIAS »), et un
            # rapprochement sur les mots crierait au loup pour rien.
            # Un client dont le contrat est porte par une autre societe du
            # groupe recoit forcement le site du PORTEUR : c'est le choix
            # d'Elliot du 2026-10-12, pas une anomalie. On compare donc a la
            # liste de sites de celui qui porte reellement le bien.
            code_sites = (E.CONTRAT_PORTE_PAR.get(prop["client"])
                          or prop["client"])
            sl = requests.get("%s/client/%s/site" % (base, code_sites),
                              params={"token": tok},
                              headers={"accept": "application/json"},
                              timeout=30).json()
            siens = {x.get("id") for x in (sl or [])}
            if sid and siens and sid not in siens:
                faits.append("SITE ETRANGER : la DIT porte le site « %s » "
                             "(id %s), qui n'est pas un site du client %s. "
                             "A signaler : ni le site ni le bien ne se "
                             "corrigent par l'API."
                             % (snom, sid, code_sites))
        except Exception:                    # noqa: BLE001
            pass
        rg = E.ranger_mail(prop, "traite", confirmer=True)
        if rg.get("envoye"):
            faits.append("mail range en 01 - DIT ARTIS SUPPORT")
        # Circuit de validation client : chez un client qui l'exige, une
        # demande de droits part « En attente de validation client ». Le Tag
        # n'est pas dans l'API — il se pose par le formulaire web, donc dans
        # la session unique de fin de passage, avec le type de travail.
        tag = None
        try:
            tag = E.tag_a_la_creation(
                prop["client"], prop.get("de") or "",
                (prop.get("objet") or "") + chr(10) + (prop.get("commentaire") or ""))
        except Exception:                    # noqa: BLE001
            tag = None
        if tag == "attente":
            faits.append("tag « En attente de validation client » a poser")
        elif tag == "validee":
            faits.append("tag « Demande validee » a poser : la demande vient "
                         "du valideur du client lui-meme")
        ctx.crees.append({"numero": numero, "client": prop["client"],
                          "uo": prop["uo"], "objet": prop["objet"],
                          "de": prop.get("de"),
                          "a_valider": tag == "attente", "tag": tag})
        log("DIT %s creee (%s)" % (numero, prop["client"]))
        return {"numero": numero, "faits": faits}

    if nom == "completer_dit":
        numero = str(args["numero"]).strip()
        m = _message(M, args["mail_id"])
        prop = _prop_depuis_mail(M, m, commentaire=args["commentaire"])
        # UNE DIT CLOSE NE SE ROUVRE JAMAIS ICI. Deux reprises d'Elliot le
        # 2026-10-05, sur 04216844 (VILLA NOAILLES) puis sur 100176087
        # (DENIOT, ou la cliente demandait un droit d'ecriture en plus de
        # l'acces deja ouvert) : « il ne faut pas rouvrir tous les tickets dont
        # on a une reponse » et « dans ces cas la transfert juste le mail au
        # dernier intervenant, ne reouvre pas ». Y compris quand le mail
        # ressemble a une demande de travail : c'est le technicien qui decide
        # de rouvrir ou d'ouvrir un ticket neuf, pas nous. On pose le
        # commentaire -- la trace reste sur le ticket -- et on lui transfere le
        # mail. `completer_dit_direct` fait les deux.
        if not ctx.actif:
            etat = (E.relire_dit(numero) or {}).get("etat")
            return {"simulation": True, "mode": "observation",
                    "etat_actuel": etat,
                    "close": etat == "Cloturee",
                    "reouverture": "jamais, par construction",
                    "intervenant": E.dernier_intervenant(numero)}
        reouvert = None
        # La reponse du valideur ne se range PAS avec les suites de
        # ticket ordinaires : elle va dans « 14 - VALIDATION CLIENT »,
        # la ou l'equipe va chercher les accords (Elliot, 2026-10-06).
        # La decision se prend AVANT, parce que c'est
        # `completer_dit_direct` qui range le mail.
        validation = False
        try:
            validation = bool(E.validation_recue(
                E.client_du_dit(numero), prop.get("de") or "",
                (prop.get("objet") or "") + chr(10) + (prop.get("commentaire") or "")))
        except Exception:                # noqa: BLE001
            validation = False
        r = E.completer_dit_direct(
            numero, prop, log=log,
            dossier="validation" if validation else "traite")
        if r.get("ok"):
            ctx.completes.append({"numero": numero, "objet": prop["objet"],
                                  "prevenu": r.get("prevenu") or ""})
            # Le valideur du client repond : le Tag passe a « Demande validee ».
            # Jamais sur un ticket cloture — c'est fini, on n'y revient pas
            # (Elliot, 2026-10-06). Le tag se pose dans la session de fin de
            # passage, avec le type de travail.
            try:
                fiche = E.relire_dit(numero) or {}
                if validation and fiche.get("etat") != "Cloturee":
                    ctx.tags_validation.append(numero)
                    out_tag = "tag « Demande validee » a poser"
                    r["resume"] = (r.get("resume") or "") + " ; " + out_tag
            except Exception:                # noqa: BLE001
                pass
            try:
                # `prevenu` est journalise a part : un complement qui n'a
                # prevenu personne doit se voir dans l'onglet, pas se deviner
                # en relisant un resume (DIT 100175793, 2026-10-02).
                J.ajouter("complement", mail_de=prop["de"],
                          mail_objet=prop["objet"], dit=numero,
                          par="agent-azure", destinataire=r.get("prevenu") or "",
                          detail=r.get("resume") or "")
            except Exception:
                pass
        out = {"ok": r.get("ok"), "resume": r.get("resume"),
               "reouverte": reouvert, "prevenu": r.get("prevenu") or ""}
        if not r.get("prevenu"):
            out["alerte"] = ("PERSONNE N'A ETE PREVENU de cet ajout. Dis-le "
                             "dans le rapport : l'intervenant ne saura pas que "
                             "le client a ecrit.")
        return out

    if nom == "ranger":
        dossier = args["dossier"]
        if dossier not in DOSSIERS_PERMIS:
            return {"refus": "dossier %s non autorise" % dossier}
        m = _message(M, args["mail_id"], complet=False)
        if not ctx.actif:
            return {"simulation": True, "mode": "observation",
                    "aurait_range": E.DOSSIERS[dossier]}
        r = E.ranger_mail({"mail_id": m["id"]}, dossier, confirmer=True)
        if r.get("envoye"):
            ctx.ranges.append({"objet": m.get("subject"),
                               "dossier": E.DOSSIERS[dossier]})
            try:
                J.ajouter("rangement", mail_de=_adr(m.get("from")),
                          mail_objet=m.get("subject"),
                          dossier=E.DOSSIERS[dossier], par="agent-azure",
                          detail=args["raison"])
            except Exception:
                pass
        return {"range": bool(r.get("envoye")),
                "destination": r.get("destination"),
                "erreur": r.get("erreur")}

    if nom == "transferer":
        return _transferer(args, ctx, M, E, J)

    # ───────────────────────────────────────────────────── trace et fin
    if nom == "signaler":
        m = _message(M, args["mail_id"], complet=False)
        ctx.signales.append({"objet": m.get("subject"),
                             "de": _adr(m.get("from")),
                             "raison": args["raison"]})
        try:
            J.ajouter("laisse", mail_de=_adr(m.get("from")),
                      mail_objet=m.get("subject"), par="agent-azure",
                      detail="SIGNALE : " + args["raison"])
        except Exception:
            pass
        return {"signale": True,
                "rappel": "le mail reste en boite ; redis-le dans le rapport"}

    if nom == "journaliser":
        J.ajouter(args["action"], par="agent-azure",
                  **{k: v for k, v in args.items() if k != "action"})
        return {"journalise": True}

    if nom == "rapport":
        ctx.rapport = args
        return {"recu": True}

    return {"erreur": "outil inconnu : %s" % nom}


def _transferer(args, ctx, M, E, J):
    """Les trois seuls transferts autorises. L'adresse n'est JAMAIS celle que
    le modele a ecrite : il choisit un motif, ce code choisit le destinataire.

    C'est la difference entre « on lui a dit de ne pas ecrire aux clients » et
    « il ne peut pas ».
    """
    import requests
    motif = args["motif"]
    m = _message(M, args["mail_id"], complet=False)
    copie = []
    note = ""
    if motif == "intervenant":
        if not args.get("dit"):
            return {"refus": "motif intervenant : le numero de DIT est "
                             "obligatoire"}
        iv = E.dernier_intervenant(str(args["dit"]).strip())
        a = iv.get("mail")
        if not a:
            return {"erreur": iv.get("erreur") or iv.get("raison")}
    elif motif == "sans_contrat":
        # Le client sans contrat d'infogerance revient a SON commercial
        # informatique, pas a l'ADV (Elliot, 2026-10-02). Celine d'Amato ne
        # garde que les fiches qui ne designent personne de joignable.
        if not args.get("client"):
            return {"refus": "motif sans_contrat : le code client est "
                             "obligatoire, c'est lui qui donne le commercial"}
        porteur = E.CONTRAT_PORTE_PAR.get(str(args["client"]).strip())
        if porteur:
            return {"refus": "le client %s EST sous contrat : il est porte par "
                             "le client %s. Creer la DIT normalement, chez le "
                             "client qui ecrit." % (str(args["client"]).strip(),
                                                    porteur)}
        com = E.commercial_informatique(str(args["client"]).strip())
        if com.get("principal"):
            a = com["principal"]["mail"]
            copie = [c["mail"] for c in com["copies"]] + [E.RELAIS["mail"]]
            note = "commercial informatique du client : %s" % com["principal"]["nom"]
            if com["copies"]:
                note += (" (la fiche en designe %d, les autres sont en copie)"
                         % len(com["matricules"]))
        else:
            a, copie = E.COMMERCE_REPLI["mail"], [E.RELAIS["mail"]]
            note = "repli sur %s : %s" % (E.COMMERCE_REPLI["nom"],
                                          com.get("raison") or "commercial introuvable")
    elif motif == "relais_humain":
        a = E.RELAIS["mail"]
    else:
        return {"refus": "motif inconnu"}

    if not M.est_interne(a):
        return {"refus": "%s n'est pas une adresse interne" % a}

    mot = (args.get("mot") or "").strip() + SIGNATURE
    if not ctx.actif:
        return {"simulation": True, "mode": "observation", "a": a,
                "copie": copie, "note": note, "mot": mot}
    if not E.ecriture_armee():
        return {"refus": "ecriture non armee : %s" % E.DRAPEAU}

    tok = _tok(M)
    dest = [{"emailAddress": {"address": a}}]
    corps = {"comment": mot, "toRecipients": dest}
    if copie:
        corps["ccRecipients"] = [{"emailAddress": {"address": c}} for c in copie]
    f = requests.post("%s/me/messages/%s/createForward" % (M.GRAPH, m["id"]),
                      headers={"Authorization": "Bearer " + tok,
                               "Content-Type": "application/json"},
                      data=json.dumps(corps), timeout=60)
    if f.status_code not in (200, 201):
        return {"erreur": "createForward HTTP %s" % f.status_code}
    bid = (f.json() or {}).get("id")
    e = requests.post("%s/me/messages/%s/send" % (M.GRAPH, bid),
                      headers={"Authorization": "Bearer " + tok}, timeout=60)
    envoye = e.status_code in (200, 202, 204)
    out = {"envoye": envoye, "a": a, "copie": copie, "note": note}
    if not envoye:
        out["erreur"] = "send HTTP %s (brouillon laisse en Brouillons)" % e.status_code
        return out
    ctx.transferts.append({"a": a, "motif": motif, "note": note,
                           "objet": m.get("subject")})
    try:
        J.ajouter("transfert", mail_de=_adr(m.get("from")),
                  mail_objet=m.get("subject"), destinataire=a,
                  dit=args.get("dit") or "", par="agent-azure",
                  detail="%s — %s%s" % (motif, (note + " — ") if note else "",
                                      (args.get("mot") or "")[:160]))
    except Exception:
        pass
    # Le mail est parti chez un collegue : il n'a plus rien a faire en boite.
    r = E.ranger_mail({"mail_id": m["id"]}, "traite", confirmer=True)
    out["range"] = bool(r.get("envoye"))
    return out


# ════════════════════════════════════════════════════════════ la boucle
def _poser_cache(systeme, messages):
    """Deux points de cache : la consigne (35 ko, identique a chaque passage)
    et la fin de la conversation en cours.

    Sans eux, chaque tour relit toute la consigne au tarif plein — sur 40 tours,
    c'est l'essentiel de la facture.
    """
    for msg in messages:
        if isinstance(msg.get("content"), list):
            for bloc in msg["content"]:
                if isinstance(bloc, dict):
                    bloc.pop("cache_control", None)
    for msg in reversed(messages):
        if msg["role"] == "user" and isinstance(msg.get("content"), list) \
                and msg["content"] and isinstance(msg["content"][-1], dict):
            msg["content"][-1]["cache_control"] = {"type": "ephemeral"}
            break
    return systeme, messages


def passage():
    """Un passage complet. Rend un resume ; ne leve pas."""
    resume = {"ok": False, "mode": MODE, "modele": MODELE}
    debut = time.time()

    if not disponible():
        raison = ("plafond mensuel atteint (%.2f / %.2f EUR)"
                  % (depense_du_mois(), PLAFOND_EUR)) if cle() \
            else "aucune cle ANTHROPIC_API_KEY"
        log("agent indisponible :", raison)
        resume["erreur"] = raison
        _battre(resume)
        return resume

    import anthropic
    # Authentification APPLICATIVE si elle est configuree : sans elle, un
    # conteneur depend d'un jeton delegue que personne ne renouvellera a 3 h du
    # matin. Idempotent — `updater.py` l'a deja fait au demarrage.
    try:
        import mail_bal_apponly as APP
        if APP.configure():
            APP.activer(log=log)
    except Exception as exc:                 # noqa: BLE001
        log("mode applicatif indisponible :", str(exc)[:160])

    import mail_dit as M
    import mail_dit_ecriture as E
    import mail_dit_journal as J

    actif = (MODE == "actif") and E.ecriture_armee()
    if MODE == "actif" and not actif:
        log("mode actif demande mais le drapeau d'ecriture est absent — "
            "le passage se fera en observation")
    ctx = Contexte(actif)
    sys_txt, source = consigne()
    log("consigne : %d caracteres (%s) | mode %s | budget restant %.2f EUR"
        % (len(sys_txt), source, "actif" if actif else "observation",
           budget_restant()))

    cli = anthropic.Anthropic(api_key=cle())
    systeme = [{"type": "text", "text": sys_txt,
                "cache_control": {"type": "ephemeral"}}]
    messages = [{"role": "user", "content": [{"type": "text", "text":
                 "Nouveau passage. Traite la boite support@dfm.fr maintenant. "
                 "Nous sommes le %s. Mode : %s."
                 % (datetime.date.today().isoformat(),
                    "ECRITURE ACTIVE" if actif else
                    "OBSERVATION (les outils d'ecriture simulent)")}]}]

    tours = 0
    while tours < MAX_TOURS:
        tours += 1
        if budget_restant() <= 0:
            resume["erreur"] = "plafond atteint en cours de passage"
            log(resume["erreur"])
            break
        _poser_cache(systeme, messages)
        try:
            rep = cli.messages.create(model=MODELE, max_tokens=4096,
                                      system=systeme, tools=OUTILS,
                                      messages=messages)
        except Exception as exc:             # noqa: BLE001
            log("appel modele en echec :", repr(exc)[:240])
            resume["erreur"] = repr(exc)[:300]
            break
        _compter(rep.usage)

        messages.append({"role": "assistant",
                         "content": [b.model_dump(exclude_none=True)
                                     for b in rep.content]})
        appels = [b for b in rep.content if b.type == "tool_use"]
        for b in rep.content:
            if b.type == "text" and b.text.strip():
                log("…", b.text.strip().replace("\n", " ")[:220])
        if not appels:
            log("le modele n'appelle plus d'outil : fin du passage")
            break

        resultats = []
        for b in appels:
            log("→", b.name, json.dumps(
                {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                 for k, v in (b.input or {}).items()
                 if k not in ("commentaire", "mot")}, ensure_ascii=False)[:200])
            res = executer(b.name, b.input or {}, ctx, M, E, J)
            resultats.append({"type": "tool_result", "tool_use_id": b.id,
                              "content": json.dumps(res, ensure_ascii=False,
                                                    default=str)[:30000]})
        messages.append({"role": "user", "content": resultats})
        if ctx.rapport is not None:
            break
    else:
        log("plafond de %d tours atteint" % MAX_TOURS)
        resume["erreur"] = "plafond de tours"

    # Le type de travail, hors de portee du modele : il ne peut pas l'oublier.
    resume["type_travail"] = _poser_type_travail(ctx)

    resume.update(
        ok=ctx.rapport is not None,
        tours=tours,
        crees=[c["numero"] for c in ctx.crees],
        completes=[c["numero"] for c in ctx.completes],
        ranges=len(ctx.ranges),
        transferts=ctx.transferts,
        signales=ctx.signales,
        rapport=ctx.rapport,
        erreurs=ctx.erreurs[:20],
        cout_du_mois=round(depense_du_mois(), 3),
        secondes=int(time.time() - debut))
    _compter_passage()
    _battre(resume)
    _afficher(resume)
    return resume


def _journaliser_confirmation(ctx, numero, resultat):
    """Trace l'accuse de reception : c'est la preuve qu'il est parti UNE fois."""
    fiche = next((c for c in ctx.crees if c["numero"] == numero), {})
    apercu = resultat.get("apercu") or {}
    try:
        import mail_dit_journal as J
        J.ajouter("confirmation", dit=numero,
                  mail_de=fiche.get("de"), mail_objet=fiche.get("objet"),
                  client_code=fiche.get("client"),
                  destinataire=" ; ".join(apercu.get("a") or []),
                  detail="accuse de reception Artis au demandeur")
    except Exception as exc:                 # noqa: BLE001
        log("journal confirmation impossible :", str(exc)[:120])


def _poser_type_travail(ctx):
    """Hotline / Non facturable sur toutes les DIT creees, UNE seule session.

    Chaque connexion web consomme un siege de licence Artis : c'est pour ca
    qu'elle est unique et qu'elle vient a la fin, pas apres chaque creation.
    Et c'est pour ca qu'elle est ici et non dans un outil : une session oubliee
    laisse les tickets en Terrain / Automatique, et un dispatcher les planifie.
    """
    numeros = [c["numero"] for c in ctx.crees]
    a_taguer = [c["numero"] for c in ctx.crees
                if c.get("tag") == "attente" or
                (c.get("tag") is None and c.get("a_valider"))]
    # « Demande validee » des la creation : le valideur du client a fait la
    # demande lui-meme, il n'a personne a qui la soumettre (Elliot, 2026-10-09).
    a_valider_apres = ([c["numero"] for c in ctx.crees if c.get("tag") == "validee"]
                       + list(getattr(ctx, "tags_validation", []) or []))
    if not numeros and not a_valider_apres:
        return {"a_poser": numeros, "fait": False, "raison": "aucune creation"}
    if not ctx.actif:
        return {"a_poser": numeros, "fait": False, "raison": "observation"}
    out = {"a_poser": numeros, "poses": [], "echecs": [],
           "tags": {"attente": [], "validee": [], "echecs": []},
           "confirmations": [], "confirmations_echecs": []}
    try:
        import artis_type_travail as T
        with T.session_dediee() as w:
            for n in numeros:
                try:
                    r = T.poser_type_travail(
                        n, "Hotline", confirmer=True, web=w,
                        aussi={"cb_typeFactu": "Non facturable"})
                    (out["poses"] if r.get("ok") else out["echecs"]).append(n)
                except Exception as exc:     # noqa: BLE001
                    out["echecs"].append("%s : %s" % (n, str(exc)[:100]))
            # Les Tags du circuit de validation, dans la MEME session.
            for n, ajout, retrait, ou in (
                    [(n, T.TAG_ATTENTE_VALIDATION, None, "attente")
                     for n in a_taguer]
                    + [(n, T.TAG_DEMANDE_VALIDEE, T.TAG_ATTENTE_VALIDATION,
                        "validee") for n in a_valider_apres]):
                try:
                    r = T.poser_tag(n, ajouter=ajout, retirer=retrait,
                                    confirmer=True, web=w)
                    if r.get("ok"):
                        out["tags"][ou].append(n)
                    else:
                        out["tags"]["echecs"].append(
                            "%s : %s" % (n, str(r.get("erreur"))[:100]))
                except Exception as exc:     # noqa: BLE001
                    out["tags"]["echecs"].append("%s : %s" % (n, str(exc)[:100]))
            # L'accuse de reception au client, dans la MEME session.
            # Demande par Elliot le 2026-10-13 : « j'ai beaucoup de clients qui
            # demandent si leur demande a bien ete prise en compte ». Sur
            # TOUTES les creations (son choix), jamais sur un complement.
            for n in numeros:
                try:
                    fiche = next((c for c in ctx.crees
                                  if c["numero"] == n), {})
                    r = T.envoyer_confirmation_reception(
                        n, confirmer=True, web=w,
                        destinataire_si_absent=fiche.get("de"))
                    if r.get("ok"):
                        out["confirmations"].append(n)
                        _journaliser_confirmation(ctx, n, r)
                    else:
                        out["confirmations_echecs"].append(
                            "%s : %s" % (n, str(r.get("erreur"))[:100]))
                except Exception as exc:     # noqa: BLE001
                    out["confirmations_echecs"].append(
                        "%s : %s" % (n, str(exc)[:100]))
        out["fait"] = True
        log("type de travail : %d pose(s), %d echec(s) ; tags : %d attente, "
            "%d validee, %d echec(s)"
            % (len(out["poses"]), len(out["echecs"]),
               len(out["tags"]["attente"]), len(out["tags"]["validee"]),
               len(out["tags"]["echecs"])))
        log("confirmations client : %d envoyee(s), %d echec(s)"
            % (len(out["confirmations"]), len(out["confirmations_echecs"])))
    except Exception as exc:                 # noqa: BLE001
        out["fait"] = False
        out["erreur"] = str(exc)[:200]
        log("session web impossible :", str(exc)[:160])
    return out


def _compter_passage():
    d = usage()
    d[_mois()]["passages"] = int(d[_mois()].get("passages") or 0) + 1
    _ecrire_json(USAGE, d)


ETAT = DATA_DIR / "agent_mail_dit_etat.json"


def etat_demarrage(demarre, raison=""):
    """Ce que l'agent a decide AU DEMARRAGE, pose sur /data.

    Les logs du conteneur sont illisibles depuis le poste d'Elliot : `az
    containerapp logs show` plante sur le premier caractere hors cp1252 (une
    fleche suffit, 2026-10-02). Un fichier sur le partage, lui, se telecharge
    et se lit. C'est la meme raison que le battement : un composant muet doit
    quand meme pouvoir dire s'il est la.
    """
    etat = {"demarre": bool(demarre), "raison": raison, "mode": MODE,
            "modele": MODELE, "plafond_eur": PLAFOND_EUR,
            "restant_eur": round(budget_restant(), 3),
            "cle_presente": bool(cle()),
            "periode_s": PERIODE, "heures": HEURES,
            "consigne": "ok" if (CONSIGNE_DATA.exists()
                                 or assembler_consigne()) else "ABSENTE",
            "quand": datetime.datetime.now().astimezone()
                     .isoformat(timespec="seconds")}
    _ecrire_json(ETAT, etat)
    return etat


def _battre(resume):
    """Un robot qui s'arrete ne previent pas : le fichier qui vieillit est la
    seule alerte. Meme raison que dans `robot_mail_dit.py`."""
    _ecrire_json(BATTEMENT, dict(
        resume, quand=datetime.datetime.now().astimezone()
        .isoformat(timespec="seconds")))


def _afficher(r):
    rap = r.get("rapport") or {}
    print()
    for cle_, titre in (("cree", "Créé"), ("complete", "Complété"),
                        ("range", "Rangé"), ("laisse", "Laissé")):
        lignes = rap.get(cle_) or []
        if lignes:
            print("%s :" % titre)
            for l in lignes:
                print("  -", l)
    if r.get("signales"):
        print("À regarder :")
        for s in r["signales"]:
            print("  - %s — %s" % (s.get("objet"), s.get("raison")))
    print("\n%d tour(s) · %d s · %.3f EUR ce mois-ci sur %.0f"
          % (r.get("tours") or 0, r.get("secondes") or 0,
             r.get("cout_du_mois") or 0, PLAFOND_EUR))


# ══════════════════════════════════════════════════════════════ la boucle
def _heures_ouvrees(maintenant=None):
    """`AGENT_HEURES=7-20` : passages entre 7 h et 20 h, du lundi au samedi.
    Mettre `0-24` pour tourner en continu."""
    t = maintenant or datetime.datetime.now()
    try:
        deb, fin = (int(x) for x in HEURES.split("-", 1))
    except Exception:
        deb, fin = 0, 24
    if t.weekday() == 6:                     # dimanche
        return False
    return deb <= t.hour < fin


VUE = DATA_DIR / "agent_mail_dit_vue.json"


def _boite_inchangee():
    """La boite contient-elle exactement les memes mails qu'au dernier passage ?

    C'est LE garde-fou de depense. Un passage coute environ 0,90 EUR ; a raison
    d'un toutes les 15 min, le plafond mensuel tombe en moins d'une journee.
    Or la boite est le plus souvent identique d'un quart d'heure a l'autre, et
    surtout : un mail volontairement LAISSE en boite (sensible, client
    introuvable) serait sinon re-analyse, et repaye, toutes les 15 minutes
    jusqu'a ce qu'un humain y touche.

    On ne paie donc que ce qui a bouge. Rend (inchangee, ids).
    """
    import mail_dit as M
    tok = M.access_token()
    if not tok:
        return False, []
    fid, _ = M._id_boite(tok)
    ids = sorted(m.get("id") for m in M._boite(tok, fid))
    try:
        avant = json.loads(VUE.read_text(encoding="utf-8")).get("ids") or []
    except Exception:
        avant = []
    return (ids == sorted(avant)), ids


def _memoriser_vue(ids):
    _ecrire_json(VUE, {"ids": ids,
                       "quand": datetime.datetime.now().astimezone()
                       .isoformat(timespec="seconds")})


def boucle():
    """Un passage toutes les AGENT_PERIODE secondes, SI la boite a bouge.
    Ne s'arrete jamais d'elle-meme : c'est le conteneur qui vit ou meurt."""
    log("boucle agent : periode %d s, heures %s, mode %s"
        % (PERIODE, HEURES, MODE))
    while True:
        try:
            if not _heures_ouvrees():
                log("hors heures (%s) : pas de passage" % HEURES)
            elif not disponible():
                log("indisponible (cle ou budget) : pas de passage")
            else:
                # Authentification applicative AVANT de regarder la boite :
                # sinon le coup d'oeil gratuit echoue et on paie un passage
                # pour rien.
                try:
                    import mail_bal_apponly as APP
                    if APP.configure():
                        APP.activer()
                except Exception:
                    pass
                inchangee, ids = _boite_inchangee()
                if not ids:
                    log("boite vide : pas de passage")
                    _memoriser_vue(ids)
                elif inchangee:
                    log("boite inchangee (%d mail(s)) : pas de passage" % len(ids))
                else:
                    log("boite modifiee (%d mail(s)) : passage" % len(ids))
                    try:
                        passage()
                    finally:
                        # Dans un `finally`, et c'est voulu : un passage qui
                        # echoue ne doit PAS etre rejoue a l'identique toutes
                        # les 15 minutes. Le mail qui l'a fait tomber le ferait
                        # tomber encore, et chaque tentative se paie. Le
                        # battement garde la trace de l'echec ; un mail de plus
                        # dans la boite relancera un passage de toute facon.
                        #
                        # On memorise l'etat d'APRES, pas celui d'avant : un
                        # passage actif range des mails, et comparer au depart
                        # ferait croire a un changement au tour suivant.
                        try:
                            _memoriser_vue(_boite_inchangee()[1])
                        except Exception:
                            _memoriser_vue(ids)
        except Exception as exc:             # noqa: BLE001
            log("passage en echec :", repr(exc)[:240])
        time.sleep(PERIODE)


# ══════════════════════════════════════════════════════════════════ CLI
def main():
    if "--consigne" in sys.argv:
        corps = assembler_consigne()
        if not corps:
            print("skill introuvable : lancer depuis le repo", file=sys.stderr)
            return 1
        cible = pathlib.Path(sys.argv[sys.argv.index("--consigne") + 1]) \
            if len(sys.argv) > sys.argv.index("--consigne") + 1 else None
        if cible:
            cible.write_text(corps, encoding="utf-8")
            print("%s — %d caracteres" % (cible, len(corps)))
        else:
            sys.stdout.write(corps)
        return 0
    if "--cout" in sys.argv:
        print(json.dumps(usage(), ensure_ascii=False, indent=1))
        print("plafond %.2f EUR · restant %.2f EUR · cle : %s · modele %s"
              % (PLAFOND_EUR, budget_restant(), "oui" if cle() else "NON",
                 MODELE))
        return 0
    log("agent mail -> DIT | modele %s | mode %s | data %s"
        % (MODELE, MODE, DATA_DIR))
    if "--boucle" in sys.argv:
        boucle()
        return 0
    r = passage()
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
