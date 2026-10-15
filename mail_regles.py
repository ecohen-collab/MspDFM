"""
Phase 1 — apprentissage du pattern, 100% LOCAL (aucun appel reseau, aucune IA tierce).

Croise :
  data/mailcorpus/mails_<J>j.json      (mails support@dfm.fr, lecture seule)
  scratchpad/artis_dits_60j.json       (DIT reellement crees dans Artis)
  data/mailcorpus/client_index_*.json  (email/domaine -> codeClient + id interlocuteur)

Produit un rapport : quels mails deviennent un DIT, lesquels non, et avec quelle
regle on peut le predire. Sert a calibrer avant toute ecriture Artis.

Usage : python mail_regles.py [jours]
"""
import os, sys, json, re, pathlib, collections, difflib, datetime

# argv n'est lu que si c'est un entier : ce module est aussi IMPORTE
# (par mail_dit.py, _mail_eval.py, _mail_seed.py) avec d'autres arguments.
DAYS    = next((int(a) for a in sys.argv[1:] if a.isdigit()), 14)
SCRATCH = pathlib.Path(os.environ.get("SCRATCH",
    "C:/Users/ecohen/AppData/Local/Temp/claude/C--Users-ecohen-Desktop-Dashboard-IA/"
    "c98b99c5-c6ce-4841-a6c7-771856415dad/scratchpad"))
MC      = pathlib.Path("./data/mailcorpus")

# ---------------------------------------------------------------- normalisation
# Deux marqueurs coexistent dans les objets sortants DFM :
#   [DIT 100173050]     -> numero de DIT (9 chiffres)
#   [IT 10016884101]    -> numero d'INTERVENTION (DIT + index 2 chiffres)
# Vu aussi sans crochet ouvrant : "IT 10017297201] - client 2787"
# La reference d'un ticket dans un objet. Trois formes rencontrees :
#   « [IT 10017461201] », « [DIT 100174612] » — les accuses d'Artis,
#   « Demande d'intervention 100174612 » — ce qu'ecrit un technicien quand il
#   repond au client depuis Artis. Cette derniere n'etait pas reconnue : le
#   mail n'etait ni un suivi (aucune reference vue) ni une demande neuve, et
#   il restait en boite (2026-09-23, Pascal OLIVIER vers PARIS FC).
RE_DIT   = re.compile(
    r"(?:\[?\b(?:DIT|IT)\s*"
    r"|demandes?(?:\s+d.intervention)?\s+n?[°o]?\s*)"
    r"(\d{8,11})\]?", re.I)
# Code client parfois donne en clair dans l'objet : "client 9599",
# "REF CLIENT 00617", "- client 2787"
# Le code client dans le texte. Le NOM peut s'intercaler entre le mot
# « client » et le code : « Client COPRIMHA 29517 ». On tolere donc
# jusqu'a trois mots entre les deux. Le code trouve est toujours
# confronte au referentiel avant d'etre retenu, ce qui evite de prendre
# un numero de facture pour un client.
RE_CODECLI = re.compile(
    r"\b(?:ref\.?\s*)?client\s*:?\s*"
    r"(?:[A-Za-zÀ-ÿ][\w'&.\-]{1,24}\s+){0,3}"
    r"(\d{4,8})\b", re.I)
# Datto RMM ecrit le client en toutes lettres dans ses tickets :
#   « Site: 14939 - SDVO - AGENCE SHIVA »
# Mesure sur 1 181 mails : 8 occurrences, 8 codes valides, 0 faux. Elliot
# (P-0181) : « tu vois bien le client dans "site :" c'est SHIVA 14939 ».
RE_SITE_CLIENT = re.compile(r"^\s*site\s*:\s*(\d{4,8})\s*-", re.I | re.M)

# Messageries GRAND PUBLIC : le domaine ne dit rien du client. Un dirigeant
# ecrit souvent depuis son adresse personnelle.
DOMAINES_PUBLICS = {
    "gmail.com", "googlemail.com", "hotmail.fr", "hotmail.com", "outlook.fr",
    "outlook.com", "live.fr", "live.com", "msn.com", "orange.fr", "wanadoo.fr",
    "free.fr", "sfr.fr", "neuf.fr", "bbox.fr", "laposte.net", "yahoo.fr",
    "yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com",
    "protonmail.com", "proton.me", "gmx.fr", "numericable.fr", "9online.fr",
}


def code_client_du_texte(subj, corps, libelles):
    """Le code client ECRIT dans le mail, ou (None, "").

    Deux ecritures, dans l'ordre de fiabilite : « Site: 14939 - ... » des
    tickets Datto, puis « client 29517 » en clair. Dans les deux cas le code
    doit exister dans le referentiel, sinon on confond avec un numero de serie.
    """
    for m in RE_SITE_CLIENT.finditer((subj or "") + "\n" + (corps or "")):
        if m.group(1) in (libelles or {}):
            return m.group(1), "code client dans « Site: » (ticket Datto)"
    for source, ou in ((subj, "l'objet"), ((corps or "")[:600], "le corps")):
        for m in RE_CODECLI.finditer(source or ""):
            if m.group(1) in (libelles or {}):
                return m.group(1), "code client dans " + ou
    return None, ""


RE_NOREP = re.compile(r"(no[-_.]?reply|noreply|donotreply|ne[-_.]?pas[-_.]?repondre|"
                      r"mailer[-_.]?daemon|postmaster|notification|alerte?s?@|"
                      r"newsletter|marketing|abonnement|linkedin|facturation@)", re.I)
# Rapports automatiques de supervision : l'expediteur est un vrai support
# fournisseur (ex. support@proserve.fr), donc RE_NOREP ne les attrape pas.
# On les reconnait a l'objet.
RE_SUPERV = re.compile(r"(system checks? report|monitoring|backup report|"
                       r"rapport de sauvegarde|health check|idsbox|"
                       r"daily report|alert(e)? :|disk space)", re.I)
# Sujets qui ne relevent PAS du support technique : ADV / compta / commerce.
# Ne doivent pas produire de DIT hotline mais partir vers le bon service.
# Une adresse mail citee dans l'objet : ses mots ne decrivent pas le sujet.
# « Incident technique ... - facturation@eleneo.fr » parle d'une panne, pas de
# facturation. On la retire avant d'appliquer un vocabulaire metier.
RE_ADRESSE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")

# Metiers qui ne sont PAS les notres : DFM a des equipes dediees, elles font
# leurs propres tickets. Vocabulaire volontairement etroit et sans ambiguite :
# verifie sur les 408 DIT creees depuis cette boite, aucun de ces mots n'y
# apparait, donc aucune vraie demande informatique n'est perdue.
# L'impression n'y figure PAS a dessein : « imprimante » apparait dans 15
# tickets informatiques contre 5 des autres domaines — l'equipe informatique
# traite bel et bien les problemes d'impression.
# Niveau 1 — mots GENERIQUES, cherches dans l'OBJET SEUL. Dans un corps de
# mail ils apparaissent aussi dans de vraies demandes informatiques : mesure
# sur 1181 mails, « controle d'acces » touche 7 mails dont « URGENT SVP :
# Autorisation logiciel », et les 7 avaient ete traites par l'equipe.
RE_AUTRE_METIER = re.compile(
    r"(cam[ée]ra|vid[ée]o[- ]?surveillance|vid[ée]oprotection|t[ée]l[ée]surveillance"
    r"|alarme|centrale d.alarme|d[ée]tecteur de mouvement"
    r"|contr[ôo]le d.acc[èe]s|interphone|visiophone|digicode"
    r"|badgeuse|lecteur de badge)", re.I)

# Niveau 2 — tournures PRECISES, cherchees dans l'objet ET le corps. Elles ne
# peuvent designer qu'un equipement de securite ou de domotique : zero
# occurrence dans les 1181 mails du corpus, donc aucune vraie demande perdue.
# ATTENTION : ni « porte » seul (3 tickets informatiques), ni « portail »
# (3 tickets, tous des « Portail RH »).
RE_AUTRE_METIER_CORPS = re.compile(
    r"(porte\s+d.entr[ée]e|porte\s+(principale|arri[èe]re|de service)"
    r"|(?:acc[èe]s|ouverture|fermeture|bug|probl[èe]me|panne)\s*"
    r"(?:de\s+|de la\s+|a la\s+|à la\s+)?\bporte\b"
    r"|\bporte\b.{0,30}(verrouill|d[ée]verrouill|badge|hors ligne)"
    r"|domotique|serrure|g[âa]che\b|ventouse magn[ée]tique"
    # Videosurveillance nommee dans le CORPS. Elliot, 2026-09-17, sur
    # « UFOLEP77 / Demande d'ip fixe » : « si tu lis le mail tu vois bien que
    # c'est de la surveillance + camera, donc SECURITE ». L'objet ne disait
    # rien, tout etait dans le corps.
    # « camera » SEUL est exclu : c'est aussi une webcam, et le corpus le
    # prouve — « RE: Brassage baie informatique + postes », vraie demande
    # informatique, parle de ses cameras inaccessibles. Les tournures
    # ci-dessous, elles, ne peuvent designer qu'un equipement de surveillance :
    # zero occurrence sur les 1181 mails de 14 jours.
    r"|vid[ée]o[- ]?surveillance|vid[ée]oprotection|t[ée]l[ée]surveillance"
    r"|cam[ée]ras?\s+(?:de\s+)?(?:surveillance|s[ée]curit[ée]|ext[ée]rieures?|d[ôo]me)"
    r"|\bNVR\b|enregistreur\s+vid[ée]o"
    r"|cam[ée]ras?\b[^.!?\n]{0,60}\b(?:surveillance|s[ée]curit[ée]|parking|"
    r"entr[ée]e|couloir|site)\b)", re.I)

# ATTENTION : ce motif ECARTE un mail. Chaque mot doit donc etre sans ambiguite.
# Deux pieges corriges le 2026-09-11, quand la regle a enfin ete branchee :
#   - `relance` seul ecartait « RELANCE -TR: DFM-DDE SCAN ORDI FIXE » — une
#     relance sur une demande informatique reste une demande ;
#   - `avoir` est d'abord un verbe (« avoir un souci avec... »).
RE_ADV   = re.compile(r"(demande de facture|facturation|impaye|"
                      r"reglement|bon de commande|commande \d|"
                      r"contrat .*(resiliation|reajustement)|tarif|prix"
                      r"|note d.avoir|avoir n\s*.\s*\d"
                      r"|relance\s+(?:de\s+)?(?:facture|paiement|reglement|impaye)"
                      r"|avis de virement|virement|proform|"
                      r"commande client|livraison de votre commande|"
                      r"bon de livraison|echeancier|avis d.echeance)", re.I)
# Notifications d'une plateforme de support TIERCE (Zendesk, Freshdesk et
# consorts) : ce sont des echanges sur un ticket qui existe deja ailleurs, pas
# des demandes neuves. Marqueurs dans le CORPS, tres stables d'un outil a
# l'autre. Sans cette regle, un seul ticket externe produisait 7 propositions,
# chaque notification etant un fil de conversation distinct.
RE_TICKET_TIERS = re.compile(
    r"(reply above this line|repondez au-dessus de cette ligne|"
    r"\bcommented\s*:|a commente\s*:|"
    r"view this ticket|voir ce ticket|"
    r"ticket\s*#?\s*\d+\s*(?:has been|a ete) (?:updated|mis a jour))", re.I)

# Numero de ticket cite en clair, sans crochets : "Ticket :100172863".
# Ne vaut que confronte a la liste des DIT existants, sinon trop de faux positifs
# (numeros de serie, references clients...).
RE_NUM_NU = re.compile(r"\b(\d{8,11})\b")
# Objet de reponse ou de transfert.
RE_REPONSE = re.compile(r"^\s*(re|rep|ref|tr|fw|fwd)\s*:", re.I)
# Un TRANSFERT porte une demande venue d'ailleurs ; une simple REPONSE non.
# Distinction critique pour les mails internes DFM (cf. regression 2026-09-08).
RE_TRANSFERT = re.compile(r"^\s*(tr|fw|fwd)\s*:", re.I)
# Adresse citee dans le corps d'un transfert : "De : Jean <jean@client.fr>"
RE_ADR_CORPS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Alertes de supervision : elles PRODUISENT des DIT (type 07 SUPERVISION),
# on ne les jette donc pas, on les etiquette.
RE_SUPERVISION = re.compile(
    r"(alerts?@|@rmm\.datto|monitoring|system checks? report|idsbox|"
    r"\[down\]|\[up\]|monitor automatically disabled|behavior monitoring|"
    r"expiration notification|disk space|backup report|rapport de sauvegarde)", re.I)
# Demandes de devis / avant-vente : DIT de nature 70.
RE_AVANT_VENTE = re.compile(r"(devis|avant[- ]vente|demande de prix|chiffrage|"
                            r"proposition commerciale|cotation)", re.I)
# ── journaux d'equipement reseau ────────────────────────────────────────────
# Un routeur, un firewall ou une sonde qui deverse son journal par mail n'ecrit
# pas une demande : il signale. La DIT 100175024 est nee comme ca — 11 mails du
# Vigor2927 de GAZDETECT, « [Mail Alert][IPFLOG] », collles les uns aux autres
# dans un ticket qui ne veut rien dire. Elliot, 2026-09-24 : « ce n'est pas une
# DIT, c'est juste une alerte ».
# Deux signaux independants, l'un sur l'objet, l'autre sur le corps : le second
# rattrape les equipements dont on ne connait pas encore le modele.
RE_ALERTE_EQUIPEMENT = re.compile(
    r"(\[mail alert\]|ipflog|vigor\s?\d|draytek|syslog|"
    r"\[dos\]\[block\]|port[_ ]scan|firewall log)", re.I)

# Une ligne de journal : un horodatage, puis des champs entre crochets ou
# separes par des tabulations. Ex. :
#   2026/09/23 10:57:48 -- [DOS][Block][port_scan][160.79.104.10:443->...]
RE_LIGNE_JOURNAL = re.compile(
    r"^\s*\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}(:\d{2})?.*"
    r"(\[[^\]]+\]\s*){2,}", re.M)


def corps_de_journal(texte, mini=4):
    """Vrai si le corps est un DEVERSEMENT DE JOURNAL et non une phrase.

    `mini` lignes de journal suffisent : un humain qui COLLE deux lignes de log
    dans sa demande en garde le texte autour, un equipement n'ecrit que ca.
    """
    return len(RE_LIGNE_JOURNAL.findall(texte or "")) >= mini


RE_NDR   = re.compile(r"(non remis|undeliverable|delivery has failed|"
                      r"echec de la remise|automatic reply|reponse automatique|"
                      r"absence du bureau|out of office)", re.I)

# ── envois de masse (newsletters, publicites) ───────────────────────────────
# Un mail qui propose de se desinscrire n'est pas une demande : personne ne met
# un lien de desabonnement au bas d'un appel au support. Elliot, 2026-09-16,
# devant le DIT 100174085 ne sur une newsletter Koban : « quel rapport, c'est
# de la pub, ca se voit quand meme ».
RE_DESABONNEMENT = re.compile(
    r"(se\s+d[ée]sinscrire|d[ée]sabonn|unsubscribe"
    r"|ne\s+plus\s+recevoir\s+(ces|nos)\s+(e?-?mails?|messages?)"
    r"|g[ée]rer\s+(mes|vos)\s+pr[ée]f[ée]rences"
    r"|voir\s+cet\s+e?-?mail\s+dans\s+(mon|votre)\s+navigateur"
    r"|voir\s+ce\s+message\s+(en\s+ligne|dans\s+(mon|votre)\s+navigateur)"
    r"|view\s+(this\s+e?-?mail|it)\s+in\s+(your\s+)?browser"
    r"|afficher\s+dans\s+le\s+navigateur)", re.I)


def envoi_de_masse(m):
    """Ce mail est-il un envoi de masse ? (newsletter, campagne, publicite)

    Trois conditions, et les trois comptent — mesure sur les 1181 mails de
    14 jours :
      * un lien de desabonnement          : seul, il attrape 7 candidats, dont
        4 vraies conversations ou la formule ne vient que du pied de page CITE ;
      * un objet qui n'est PAS une reponse : ecarte ces 4-la ;
      * pas un objet de supervision        : les alertes d'expiration ou de
        sauvegarde doivent continuer a produire leur DIT de type 07.
    Restent 2 mails sur 14 jours, tous deux des envois de masse averes.
    """
    subj = m.get("subject") or ""
    if RE_REPONSE.match(subj):
        return False
    if RE_SUPERVISION.search(subj + " " + addr(m)):
        return False
    corps = ((m.get("body") or {}).get("content") or m.get("bodyPreview") or "")
    return bool(RE_DESABONNEMENT.search(corps))
INTERNE  = {"dfm.fr"}

# Boîtes internes COLLECTIVES : elles ne désignent personne en particulier, donc
# transférer vers elles n'a aucun sens (support@ est la boîte d'où l'on part).
BAL_COLLECTIVES = {"support", "service.client", "accueil", "contact", "info",
                   "administration", "compta", "comptabilite", "adv", "sav",
                   "mco", "commercial", "facturation", "rh", "no-reply",
                   "noreply", "postmaster"}

# « Bonjour Valentin », « Hello Valentin, » — le prénom auquel le mail s'adresse.
RE_SALUTATION = re.compile(
    r"^\s*(?:bonjour|bonsoir|salut|hello|hi|coucou|re)\s+"
    r"([A-ZÀ-Ü][\wÀ-ÿ'’-]{1,20})", re.I | re.M)


def destinataire_interne(corps_brut, expediteur=""):
    """Le collègue à qui ce mail est en réalité destiné, ou None.

    Un client répond parfois à un agent nommément (« Hello Valentin, j'ai
    relancé DELL... ») sur une adresse qui arrive dans la boîte du support.
    Ce n'est pas une demande neuve à ticketer : c'est le mail de quelqu'un.

    On ne retient une adresse DFM que si elle est nominative (pas `support@`)
    ET, quand le mail commence par un prénom, que ce prénom correspond —
    sinon on rend le candidat sans certitude, et l'écran laisse choisir.
    """
    texte = corps_brut or ""
    vus, cands = set(), []
    for a in RE_ADR_CORPS.findall(texte):
        a = a.lower().strip(".,;:<>()[]")
        if a in vus or a.split("@")[-1] not in INTERNE:
            continue
        vus.add(a)
        local = a.split("@")[0]
        if local in BAL_COLLECTIVES:
            continue
        cands.append(a)
    if not cands:
        return None
    prenoms = [m.group(1).lower() for m in RE_SALUTATION.finditer(texte[:400])]
    for a in cands:
        local = a.split("@")[0]
        for p in prenoms:
            # « vgarcia » pour « Valentin GARCIA » : initiale + nom.
            if local.startswith(p) or (len(p) > 2 and local[:1] == p[:1]
                                       and p != local):
                return {"adresse": a, "prenom": p.capitalize(),
                        "certitude": "haute",
                        "indice": "le mail commence par « %s » et %s est la "
                                  "seule adresse DFM nominative du fil"
                                  % (p.capitalize(), a)}
    return {"adresse": cands[0], "prenom": None, "certitude": "basse",
            "indice": "adresse DFM nominative citée dans le fil : %s" % cands[0]}
# Entites internes du groupe presentes dans le referentiel client Artis :
# DFM, DFM PACA (AGB), etc. Elles ne doivent JAMAIS etre proposees comme client
# d'un DIT -- un mail impute a l'une d'elles perdrait son vrai client final.
# Constate le 2026-09-09 : 4 propositions sur 20 imputees a « DFM PACA ».
CLIENTS_INTERNES = {"DFM", "AGB", "AVBU", "DNO", "DO", "GPE"}

DOMAINES_KW = {   # mots-cles -> codeDomaine Artis (calibrage initial, a affiner)
    "HOTLINE": ["imprimante", "impression", "copieur", "toner", "scan", "bizhub",
                "konica", "papercut", "uniflow", "bourrage", "kyocera"],
    "TEL":     ["3cx", "telephone", "telephonie", "casque", "poste fixe", "standard",
                "repondeur", "sda", "portable", "sim", "ligne"],
    "CNX":     ["vpn", "internet", "connexion", "wifi", "reseau", "fibre", "box",
                "ip fixe", "switch", "routeur"],
    "SEC":     ["sophos", "antivirus", "sentinelone", "phishing", "hamecon",
                "ransomware", "vade", "spam", "quarantaine", "virus"],
    "INFO":    ["mot de passe", "mdp", "password", "session", "outlook", "teams",
                "sharepoint", "onedrive", "licence", "m365", "office", "excel",
                "pc", "ordinateur", "sauvegarde", "veeam", "serveur", "compte"],
}
# ─────────────────────────────────────────────────────────────────────────────
# PRIORITE : regle metier donnee par Elliot le 2026-09-09.
#   personne n'est bloque        -> Niveau 1 Standard   = 03
#   une personne est bloquee     -> Niveau 2 Important  = 02
#   toute la societe est bloquee -> Niveau 3 Urgent     = 01
# Elle REMPLACE la lecture dans l'historique du client : la priorite depend de
# l'ampleur du blocage decrit dans le mail, pas des habitudes du client.
# A savoir : l'equipe met aujourd'hui 02 dans ~78 % des cas quelle que soit la
# situation. Appliquer la regle changera donc la repartition -- c'est voulu.
# ⚠ LES CODES SONT L'INVERSE DES NIVEAUX PARLÉS. Vérifié dans Artis le
# 2026-09-17 sur l'intervention 10017441401 :
#     code 01 = « Niveau 3 - Urgent »
#     code 02 = « Niveau 2 - Important »
#     code 03 = « Niveau 1 - Standard »
# Elliot dit « niveau 3 » pour le plus grave ; le code stocké, lui, est 01.
# Ne jamais déduire l'un de l'autre sans relire cette table.
PRIO_STANDARD, PRIO_IMPORTANT, PRIO_URGENT = "03", "02", "01"

RE_BLOQUE_TOUS = re.compile(
    r"(tout le monde|toute la societe|toute l entreprise|toute l equipe"
    r"|tous les postes|tous les utilisateurs|tous les salaries|tout le personnel"
    r"|l ensemble du personnel|plus personne|aucun poste|toute l agence"
    r"|tout le site|toutes les agences|plus aucun acces"
    r"|serveur (?:hs|down|ne repond plus|est tombe)"
    r"|coupure (?:generale|totale)"
    # Les CLIENTS du client, ce n'est pas « une personne » : c'est le service
    # entier. Elliot, 2026-09-17, sur le DIT 100174414 (hotel dont les clients
    # ne peuvent plus se connecter au wifi) : « c'est niveau 3, tous les
    # clients sont bloques ».
    r"|(?:nos|les|leurs)\s+(?:clients?|usagers?|utilisateurs?|residents?"
    r"|patients?|adherents?|visiteurs?|locataires?)"
    r"[^.!?\n]{0,70}(?:bloqu|ne (?:peuvent|parviennent|reussissent) "
    r"(?:toujours )?(?:plus|pas)|n.arrivent (?:toujours )?(?:plus|pas)"
    r"|impossible|sans acces|hors service))", re.I)
RE_BLOQUE_UN = re.compile(
    r"(bloque|bloquee|bloques|bloquees|plus acces|plus d acces"
    r"|impossible de (?:se connecter|travailler|acceder|ouvrir|imprimer"
    r"|envoyer|recevoir)|ne peut plus|ne parvient plus|n arrive plus a"
    r"|ne fonctionne plus|hors service|en panne|plus rien ne"
    r"|ne demarre plus|ne peut pas travailler)", re.I)


def strip_acc(s):
    tbl = str.maketrans("\u00e0\u00e2\u00e4\u00e9\u00e8\u00ea\u00eb\u00ee\u00ef\u00f4\u00f6\u00f9\u00fb\u00fc\u00e7"
                        "\u00c0\u00c2\u00c4\u00c9\u00c8\u00ca\u00cb\u00ce\u00cf\u00d4\u00d6\u00d9\u00db\u00dc\u00c7",
                        "aaaeeeeiioouuuc" "AAAEEEEIIOOUUUC")
    return (s or "").translate(tbl)


def norm(s):
    """Texte comparable : sans accents, minuscules, espaces tasses."""
    s = strip_acc(s or "").lower()
    return re.sub(r"\s+", " ", s).strip()


# Banniere de securite inseree par Outlook devant les mails d'un expediteur
# inhabituel. Elle n'a rien a faire dans le commentaire d'un DIT.
RE_BANNIERE = re.compile(
    r"(?:vous\s+n.obtenez\s+pas\s+souvent.{0,120}?"
    r"pourquoi\s+c.est\s+important"
    r"|you\s+don.t\s+often\s+get\s+email\s+from.{0,120}?"
    r"learn\s+why\s+this\s+is\s+important)",
    re.I | re.S)
# References d'images inlinees et annotations de liens laissees par la
# conversion HTML -> texte de Graph.
RE_CID   = re.compile(r"\[cid:[^\]]+\]")
RE_LIENA = re.compile(r"<https?://[^>]*>")
RE_BALISE = re.compile(r"<[^>\n]{1,200}>")
RE_VIDES = re.compile(r"\n{3,}")
# Lignes de signature / pied de page a retirer : elles n'apportent rien au
# technicien et polluent le commentaire du DIT.
RE_BRUIT_LIGNE = re.compile(
    r"^\s*(?:"
    r"\[?(?:logo|cid|image|signature)[^\]]*\]?"              # [Logo], [cid:...]
    # Jetons du gabarit de signature Outlook restes non substitues. Liste
    # FERMEE a dessein : un motif generique de crochets, ou pire une
    # adresse mail seule sur sa ligne, mangerait du contenu utile — une
    # demande support dit souvent « creez la boite xxx@client.fr ».
    # « [X] » est le marqueur d'image inline que Graph laisse dans le texte.
    r"|\[[Xx]\]"
    # Une ligne faite UNIQUEMENT de jetons entre crochets est le texte de
    # remplacement d'images : « [img] », « [instagram] », « [youtube] »,
    # « [John Taylor Corporate] », « [Magazine JOHN TAYLOR 2026] ». Elliot,
    # 2026-09-22, sur le commentaire de la DIT 100174835 : « vire les balises
    # entre crochets ». La reference d'un ticket, elle, est protegee : voir
    # `_ligne_de_crochets`, qui epargne « [IT 10017426202] ».
    r"|\[(?:mobilePhone|emailAddress|website|address|jobTitle|company"
    r"|officePhone|businessPhone|homePhone|phone|fax|department|title"
    r"|displayName|givenName|surname|linkedIn|twitter|facebook)\]"
    r"|(?:https?://|www\.)\S+"                               # une URL seule
    # « Tél : », mais aussi « Ligne directe : », « Poste : », « Portable : ».
    r"|(?:tel|tél|t[ée]l[ée]phone|mob|mobile|fax|std|standard|port|portable"
    # La ligne se termine souvent par autre chose que le numero :
    # « Tel: +377 92 16 53 65 - ahenry@john-taylor.com ». Tout ce qui suit un
    # numero de telephone annonce comme tel appartient a la signature.
    r"|ligne\s+directe|ligne\s+fixe|poste|gsm)\s*[.:]?\s*[\d ().+-]{6,}.*"
    r"|[\d ().+-]{9,}"                                        # un numero seul
    # Etiquette d'une lettre ou deux, comme dans les signatures modernes :
    # « M: 07 68 07 25 66 », « T: +33 1 43 96 80 08 ». On exige huit
    # caracteres de numero, sinon « Le: 15 mars » serait pris pour un telephone.
    r"|[a-zA-Z]{1,2}\s*[.:]\s*[\d ().+-]{8,}.*"
    # « 14, rue du 8 mai 1945 » : la virgule apres le numero est la norme.
    r"|\d{1,4}\s*(?:bis|ter)?\s*,?\s*(?:rue|avenue|av\.|bd|boulevard|impasse"
    r"|chemin|place|quai|allée|allee|route|zone|za|zi|parc)\b.*"
    # Meme adresse, mais sans numero de rue : « Place des Moulins, Europa
    # Residence, 98000 Monaco ». On exige alors un code postal sur la ligne,
    # sinon « Place de la formation » (une vraie phrase) disparaitrait.
    r"|(?:rue|avenue|av\.|bd|boulevard|impasse|chemin|place|quai|allée|allee"
    r"|route)\s+[^\n]{3,60}\d{2}\s?\d{3}\b.*"
    # code postal + ville, ecrit « 75010 » ou « 75 010 »
    r"|\d{2}\s?\d{3}\s+[A-ZÀ-Ü][\w'\- ]+"
    r"|(?:préservons|preservons)\s+l.environnement.*"
    r"|n.imprimez\s+ce\s+mail.*"
    r"|suivez[- ]nous\s+sur.*"
    r"|(?:une\s+question\s*\?).*"
    # clause de confidentialite, dans ses nombreuses variantes
    r"|ce\s+(?:message|mail|courriel|e-?mail)\b.{0,120}?confidentiel.*"
    r"|this\s+e?-?mail\b.{0,120}?confidential.*"
    r"|.{0,40}(?:destinataire|recipient)s?\s+(?:seuls?|only)\b.*"
    r")\s*$", re.I)


# Balise HTML : un nom de balise, puis des attributs ou la fermeture. Une
# adresse entre chevrons (« <jean@client.fr> ») n'y correspond pas, parce que
# le caractere qui suit le nom n'est ni un espace, ni « / », ni « > ».
RE_BALISE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]{0,14}(?:\s[^<>]{0,400})?/?>")
RE_COMMENT_HTML = re.compile(r"<!--.*?-->", re.S)
RE_BLOC_HTML = re.compile(r"<(style|script|head)\b.*?</\1>", re.S | re.I)
# Entites les plus courantes. On ne decode pas tout : seulement ce qui apparait
# vraiment dans des mails professionnels.
ENTITES = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
           "&apos;": "'", "&#39;": "'", "&#160;": " ", "&rsquo;": "'",
           "&eacute;": "é", "&egrave;": "è", "&agrave;": "à", "&ccedil;": "ç",
           "&ocirc;": "ô", "&ugrave;": "ù", "&icirc;": "î", "&ecirc;": "ê"}


def sans_html(txt):
    """Retire les balises et decode les entites courantes."""
    if not txt or "<" not in txt and "&" not in txt:
        return txt
    t = RE_BLOC_HTML.sub(" ", txt)
    t = RE_COMMENT_HTML.sub(" ", t)
    t = RE_BALISE.sub("", t)
    for k, v in ENTITES.items():
        if k in t:
            t = t.replace(k, v)
    return t


# Formule de politesse finale : ce qui suit est la signature.
RE_FORMULE_FIN = re.compile(
    r"^\s*(cordialement|cdlt|cdt|bien\s+(a|à)\s+vous|bien\s+cordialement"
    r"|sinc[eè]rement|salutations|respectueusement|bonne\s+(journ[ée]e|r[ée]ception|soir[ée]e)"
    r"|tr[eè]s\s+belle\s+journ[ée]e|best\s+regards|kind\s+regards|regards"
    r"|thanks?\s*(and|&)?\s*regards|merci\s+d.avance|dans\s+l.attente)\b",
    re.I)

# Formule reduite a un mot, SEULE sur sa ligne. On exige la ligne entiere :
# « Merci de creer la boite... » est une demande, « Merci » tout court est la
# fin du message (P-0177 finissait par « Best, » puis la signature).
RE_FORMULE_SEULE = re.compile(
    r"^\s*(merci|thanks?|thank\s+you|best|bien\s+[àa]\s+vous|amicalement"
    r"|bonne\s+journ[ée]e)\s*[,.!]?\s*$", re.I)


def couper_signature(txt, garder_apres=1):
    """Coupe a la formule de politesse, en gardant `garder_apres` lignes
    non vides apres elle (le nom du demandeur).

    Ne coupe pas si la formule est trop haut dans le mail : un message qui
    commence par « Merci d'avance » perdrait tout.
    """
    lignes = (txt or "").split("\n")
    for n, l in enumerate(lignes):
        if not (RE_FORMULE_FIN.match(l) or RE_FORMULE_SEULE.match(l)):
            continue
        avant = "\n".join(lignes[:n]).strip()
        if len(avant) < 40:          # rien de substantiel avant : on ne coupe pas
            continue
        # le contenu, PUIS la formule, PUIS le nom — et rien apres
        gardees = list(lignes[:n])
        reste = garder_apres
        for suivante in lignes[n:]:
            gardees.append(suivante)
            if suivante.strip():
                if reste <= 0:
                    break
                reste -= 1
        return "\n".join(gardees).rstrip()
    return txt


# Une ligne de bas de signature : nom, fonction, societe, mention d'image.
# PAS de re.I global : la regle « societe en capitales » n'a de sens que
# sensible a la casse — avec re.I elle avalait « M Stephane AQUINO -
# 0603799733 », c'est-a-dire le contact a rappeler.
RE_PIED = re.compile(
    r"^\s*(?:"
    r"\[[^\]]{1,60}\]"                                   # [IMG_3274.jpeg]
    r"|[A-ZÀ-Ü][A-ZÀ-Ü0-9'&.\- ]{4,}"                    # SOCIETE EN CAPITALES
    r"|(?i:directeur|directrice|responsable|assistante?|gérante?|gerante?"
    r"|président|president|office\s+manager|chargée?\s+de|consultante?"
    r"|technicien|ingénieur|ingenieur|comptable|secrétaire|secretaire"
    r"|key\s+account|sales\s+manager|business\s+manager).*"
    r")\s*$")


# Marqueur d'image incrustée laissé par Graph dans la version texte :
#   [cid:image001.png@01DD4121.27F8DCD0]
RE_CID_NOM = re.compile(r"\[cid:([^\]@\s]+)[^\]]*\]", re.I)


# Texte alternatif d'une image de signature : l'expéditeur le dit lui-même.
RE_ALT_SIGNATURE = re.compile(
    r"(signature|logo|bandeau|banner|banni[èe]re|pied\s*de\s*page|footer"
    r"|facebook|linkedin|instagram|twitter|youtube|cid:)", re.I)
RE_IMG_HTML = re.compile(r"<img\b[^>]*>", re.I)


def images_du_html(html):
    """{nom de fichier: texte alternatif} pour chaque image incrustée.

    Le texte alternatif est le signal le plus honnête dont on dispose : c'est
    l'expéditeur qui l'écrit. Sur le mail de la DIT 100173882, la capture
    d'écran n'a pas d'`alt` (Outlook rend alors `[cid:image001.png]`) tandis
    que la signature porte `alt="Laugier - signature mail"`. Ni la taille ni la
    position ne les séparaient : la capture pèse 86 ko, la signature 45 ko, et
    les deux sont placées après « Bien cordialement » dans le HTML.
    """
    out = {}
    for balise in RE_IMG_HTML.findall(html or ""):
        src = re.search(r'src\s*=\s*["\']cid:([^"\']+)["\']', balise, re.I)
        if not src:
            continue
        nom = src.group(1).split("@")[0].strip().lower()
        alt = re.search(r'alt\s*=\s*["\']([^"\']*)["\']', balise, re.I)
        out[nom] = (alt.group(1) if alt else "").strip()
    return out


def image_de_contenu(nom, alt):
    """L'image porte-t-elle du contenu, ou décore-t-elle la signature ?"""
    if RE_ALT_SIGNATURE.search(alt or ""):
        return False
    return True


def images_du_corps(txt):
    """Les images incrustées AVANT la formule de politesse.

    C'est le seul signal fiable trouvé pour séparer une capture d'écran d'un
    logo de signature, et il ne coûte aucune requête : l'image que le client
    commente (« voir capture d'écran : ») est référencée dans le corps, celles
    de la signature viennent après « Bien Cordialement ».

    Ni la taille ni le nom ne suffisaient : la capture de P-0225 pèse 5,9 ko et
    Outlook l'a nommée `image001.png`, exactement comme un logo.
    """
    t = (txt or "")
    fin = len(t)
    for n, l in enumerate(t.split("\n")):
        if RE_FORMULE_FIN.match(l) or RE_FORMULE_SEULE.match(l):
            pos = t.find(l)
            if pos > 40:
                fin = pos
                break
    return {m.group(1).lower() for m in RE_CID_NOM.finditer(t[:fin])}


def couper_pied(txt, mini=30):
    """Retire le bloc de signature qui reste EN FIN de texte.

    `couper_signature` coupe a « Cordialement », mais tous les mails n'en ont
    pas : celui de P-0182 enchaine directement le nom, la societe, l'adresse et
    le telephone. Elliot l'a signale trois fois (P-0165, P-0175, P-0182) :
    « ta encore gardé la signature, la met pas ».

    On remonte donc depuis la FIN et on jette tant que la ligne ressemble a un
    element de signature, en s'arretant a la premiere vraie phrase. Garde-fou :
    si la coupe ne laisse presque rien, on rend le texte d'origine — mieux vaut
    une signature en trop qu'une DIT vide.
    """
    lignes = (txt or "").rstrip().split("\n")
    n = len(lignes)
    while n > 0:
        l = lignes[n - 1]
        if not l.strip():
            n -= 1
            continue
        phrase = l.rstrip().endswith((".", "?", "!")) or len(l.split()) >= 8
        if phrase and not RE_PIED.match(l):
            break
        if RE_PIED.match(l) or RE_BRUIT_LIGNE.match(l) or len(l.split()) <= 5:
            n -= 1
            continue
        break
    # Deuxieme condition, decisive : le bloc retire doit contenir au moins UN
    # element de signature avere (societe en capitales, fonction, adresse,
    # telephone etiquete, mentions legales). Sans elle, la regle « ligne
    # courte » emportait la fin utile d'un mail — sur P-0161 elle supprimait
    # « M Stephane AQUINO - 0603799733 », le contact a rappeler.
    retire = [l for l in lignes[n:] if l.strip()]
    if not any(RE_PIED.match(l) or RE_BRUIT_LIGNE.match(l) for l in retire):
        return (txt or "").rstrip()
    coupe = "\n".join(lignes[:n]).rstrip()
    return coupe if len(coupe) >= mini else (txt or "").rstrip()


# Une ligne faite seulement de jetons entre crochets, et la reference de
# ticket qu'il ne faut SURTOUT pas manger avec.
RE_LIGNE_CROCHETS = re.compile(r"^\s*(?:\[[^\]\n]{1,80}\]\s*)+$")
RE_REF_TICKET = re.compile(r"\[\s*(?:IT|DIT)\b|\d{7,}")


def _ligne_de_crochets(l):
    """Cette ligne n'est-elle QUE du texte de remplacement d'images ?

    « [img] », « [instagram] », « [John Taylor Corporate] » : rien de tout
    cela n'a sa place dans le commentaire d'un ticket. Mais
    « [IT 10017426202] », seul sur sa ligne, est une reference que le
    technicien doit garder — d'ou l'exception sur les references et sur les
    longs numeros. Demande d'Elliot le 2026-09-22, sur la DIT 100174835.
    """
    return bool(RE_LIGNE_CROCHETS.match(l or "")
                and not RE_REF_TICKET.search(l or ""))


def _sans_signature(txt):
    """Retire les lignes de signature et de pied de page, en gardant la
    formule de politesse et le nom du demandeur."""
    gardees = []
    for l in txt.split("\n"):
        if RE_BRUIT_LIGNE.match(l) or _ligne_de_crochets(l):
            continue
        gardees.append(l)
    # une ligne ne contenant que des points ou tirets ne sert a rien
    gardees = [l for l in gardees if not re.match(r"^\s*[.\-_=*]{2,}\s*$", l)]
    return "\n".join(gardees)


# Les listes a puces arrivent en « * » apres la conversion HTML -> texte de
# Graph, parfois seules sur leur ligne. Elliot veut des tirets (2026-09-09).
RE_PUCE_SEULE = re.compile(r"^[ \t]*\*[ \t]*$", re.M)
RE_PUCE = re.compile(r"^[ \t]*[\*\u2022\u00b7\u25cf\u25aa][ \t]*", re.M)


def _puces(t):
    """« * » -> « - ». Une puce seule sur sa ligne est recollee au texte
    qui la suit, sinon on obtient une ligne de tiret orpheline."""
    t = RE_PUCE_SEULE.sub("\x00", t)
    t = re.sub(r"\x00\n+", "- ", t)
    t = RE_PUCE.sub("- ", t)
    return t.replace("\x00", "- ")


def nettoyer(txt):
    """Retire le bruit ajoute par les clients de messagerie."""
    t = (txt or "").replace("\r\n", "\n")
    t = _puces(t)
    t = RE_BANNIERE.sub("", t)
    t = RE_CID.sub("", t)
    t = RE_LIENA.sub("", t)
    t = RE_BALISE.sub("", t)
    t = _sans_signature(t)
    t = RE_VIDES.sub("\n\n", t)
    return t.strip()


def body_utile(txt):
    """Corps exploitable comme `commentaire` de DIT : bruit retire, citation du
    fil et signature coupees, pour ne garder que la demande."""
    # Le HTML d'abord : un mail transfere peut embarquer la source d'un
    # autre message, et les balises se retrouvaient telles quelles dans
    # la DIT (signale par Elliot sur deux tickets le 2026-09-14).
    t = couper_pied(couper_signature(nettoyer(sans_html(txt))))
    for marq in ("\n-----Message d'origine", "\nDe : ", "\nDe: ", "\n________",
                 "\nEnvoye de mon", "\nEnvoye depuis", "\n> "):
        i = t.find(marq)
        if i > 40:
            t = t[:i]
    return RE_VIDES.sub("\n\n", t).strip()


# Debut d'un message cite : en-tete Outlook, separateur, ou chevrons.
MARQUES_CITATION = ("\n-----Message d'origine", "\n-----Original Message",
                    "\nDe : ", "\nDe: ", "\nFrom: ", "\nFrom : ",
                    "\n________", "\n> ")


def _debut_citation(t):
    """Position du premier message cite, ou -1. Le seuil de 40 caracteres
    evite de prendre pour une citation un mail qui commence par « De : »."""
    trouve = -1
    for marq in MARQUES_CITATION:
        i = t.find(marq)
        if i > 40 and (trouve < 0 or i < trouve):
            trouve = i
    return trouve


def historique_cite(txt, max_car=2500):
    """Les messages cites sous la demande, nettoyes et bornes.

    Sur un transfert (« TR: »), c'est la que le probleme est ecrit. On rend le
    texte tel quel, en-tetes « De :/Envoye :/Objet : » compris : ils situent
    qui a dit quoi, ce qu'un agent recopie aussi dans la DIT.
    """
    t = nettoyer(sans_html(txt))
    i = _debut_citation(t)
    if i < 0:
        return ""
    h = RE_VIDES.sub("\n\n", t[i:]).strip()
    if len(h) > max_car:
        # coupe sur une fin de ligne pour ne pas trancher un mot
        coupe = h.rfind("\n", 0, max_car)
        h = h[:coupe if coupe > max_car // 2 else max_car].rstrip() + "\n[...]"
    return h


def addr(m, champ="from"):
    return ((m.get(champ) or {}).get("emailAddress") or {}).get("address", "").lower()


# ---------------------------------------------------------------- classification
def prepare_fils(mails):
    """Marque `_deja_vu` sur tout mail dont le fil (conversationId) a deja ete
    vu plus tot dans le corpus. Mesure : un fil deja vu ne donne un DIT au texte
    ressemblant que 1 fois sur 126 -> c'est une suite de conversation.
    A appeler AVANT classer()."""
    mails.sort(key=lambda m: m.get("receivedDateTime") or "")
    vus = set()
    for m in mails:
        cid = m.get("conversationId")
        m["_deja_vu"] = bool(cid) and cid in vus
        if cid:
            vus.add(cid)
    return mails


def classer(m, num_dits=None, etat_dit=None):
    """Renvoie (verdict, motif).

    verdict :
      'suivi'           -> reponse sur un ticket existant, jamais de nouveau DIT
      'ignore'          -> automatique, interne, supervision
      'adv'             -> ADV / compta / commerce, pas du support technique
      'autre_metier'    -> securite, etc. : une autre equipe DFM s'en charge
      'candidat_fort'   -> demande neuve, aucun signal de suivi   (precision ~73%)
      'candidat_faible' -> demande portant un signal de suivi     (precision ~64%)

    `num_dits` : ensemble des numeros de DIT connus. Permet de reconnaitre un
    numero cite en clair sans crochets. Sans cet argument le signal est ignore.
    """
    subj = m.get("subject") or ""
    exp  = addr(m)
    dom  = exp.split("@")[-1]

    # UN TICKET CLOS N'A PLUS DE SUITE. Le numero dans l'objet ne prouve qu'une
    # chose : le client a repondu sur le fil qu'il avait sous la main. Si le
    # ticket cite est cloture ou annule, ce mail porte une demande NEUVE et doit
    # suivre le chemin normal — etre lu, classe, et devenir un ticket.
    # Le 2026-09-24, DIRECT IMPRESSION a ecrit « nous n'avons plus d'acces a nos
    # serveurs, intervention en urgence » sur un fil clos depuis fevrier : le
    # verdict « suivi » tombait ici, en premiere ligne, et le mail est reste
    # invisible en boite pendant cinq heures pendant que le moteur le
    # retransferait a l'intervenant d'une affaire reglee sept mois plus tot.
    # `etat_dit` est une fonction numero -> etat, et ne repond QUE pour un
    # ticket qui n'est plus ouvert. Absente, le comportement est celui d'avant.
    def _clos(n):
        if not etat_dit:
            return ""
        try:
            return etat_dit(n) or ""
        except Exception:
            return ""

    md = RE_DIT.search(subj)
    if md:
        if not _clos(md.group(1)):
            return "suivi", "reponse sur DIT " + md.group(1)
        m["_dit_clos"] = (md.group(1), _clos(md.group(1)))
    # numero de DIT cite en clair, sans crochets
    if num_dits:
        for n in RE_NUM_NU.findall(subj):
            if n in num_dits:
                if not _clos(n):
                    return "suivi", "numero de DIT en clair " + n
                m["_dit_clos"] = (n, _clos(n))
    if RE_NDR.search(subj):
        return "ignore", "reponse auto / non remis"
    # Un echange sur un ticket tiers : suivi, jamais une demande neuve.
    corps_brut = ((m.get("body") or {}).get("content")
                  or m.get("bodyPreview") or "")[:4000]
    # Un equipement qui deverse son journal ne demande rien. Il se range.
    if RE_ALERTE_EQUIPEMENT.search(subj) or corps_de_journal(corps_brut):
        return "ignore", "journal d'equipement reseau : une alerte, pas une demande"
    if RE_TICKET_TIERS.search(corps_brut) or RE_TICKET_TIERS.search(subj):
        return "suivi", "echange sur un ticket d'une plateforme tierce"
    # Une newsletter n'est jamais une demande, quel que soit le client que son
    # domaine designe. C'est exactement ce qui a produit le DIT 100174085 :
    # koban-crm.com etait le « domaine unique » du client 30223.
    if envoi_de_masse(m):
        return "ignore", "envoi de masse (lien de desinscription)"
    # On ne cherche les marqueurs d'envoi automatique que dans l'ADRESSE.
    # Les chercher dans l'objet jetait de vraies demandes clients (un mail
    # « Verification de la connexion de mon LinkedIn » a ete perdu ainsi).
    # Et un expediteur automatique n'est pas forcement a jeter : les alertes de
    # supervision produisent des DIT de type 07.
    auto = bool(RE_NOREP.search(exp))
    # L'exception se juge sur l'objet ET l'adresse. Tentative du 2026-09-11 de
    # la restreindre a l'objet : ANNULEE, le corpus l'a refutee. Un robot
    # s'appelle « alerts@... » mais peut RELAYER de vraies demandes — le
    # portail Datto en est l'exemple (« Datto RMM Support Ticket #... - pas de
    # reception de mails - New »). Restreindre coutait 6 vraies demandes sur
    # 14 jours.
    if auto and not RE_SUPERVISION.search(subj + " " + exp):
        return "ignore", "expediteur automatique"
    interne = False
    if dom in INTERNE:
        # ATTENTION : un mail interne n'est pas forcement a jeter. Les agents DFM
        # font suivre (TR:) de vraies demandes clients vers la boite support.
        # On ne l'ignore que s'il n'a pas l'allure d'un transfert de demande.
        if RE_REPONSE.match(subj) and not RE_TRANSFERT.match(subj):
            # Un agent DFM qui repond dans un fil : suite de conversation, pas
            # une demande neuve. Verdict 'suivi' (et non 'ignore') : meme effet,
            # mais l'etiquette dit la verite.
            return "suivi", "reponse d'un agent DFM dans un fil"
        # Un agent DFM qui ecrit SANS RE: porte une vraie demande, pour un client
        # ou pour l'interne (« ACT LEGAL / Creation d'utilisateur », « URGENT -
        # TECHNICONTROL - 2757 »). Mesure : 35 de ces mails etaient jetes a tort.
        # Le client sera cherche dans le corps, jamais via le domaine dfm.fr.
        interne = True
        # Un transfert est par construction un objet de reponse : il tombera donc
        # en 'candidat_faible' plus bas, ce qui est le bon niveau de prudence.
        interne = True
    if not exp:
        return "ignore", "expediteur illisible"

    # ADV / compta / commerce : ce n'est pas du support technique. La regle
    # existait depuis le debut mais n'etait JAMAIS appelee — le verdict `adv`
    # etait documente et jamais produit. On la juge sur l'OBJET seul : le corps
    # d'une vraie demande informatique peut mentionner une facture sans que la
    # demande releve de l'ADV.
    # L'avant-vente PRIME : `devis`, `chiffrage` et `cotation` appartiennent
    # aux deux vocabulaires, et une demande de devis pour une intervention est
    # une vraie demande (DIT nature 70), pas de l'ADV. Sans cet ordre, brancher
    # l'ADV ecartait des demandes d'avant-vente — mesure sur 14 jours.
    subj_hors_adresses = RE_ADRESSE.sub(" ", subj)
    # Securite, et plus largement les metiers qui ont leur propre equipe.
    if RE_AUTRE_METIER.search(subj_hors_adresses):
        return "autre_metier", "releve d'un autre metier que l'informatique"
    corps_brut2 = ((m.get("body") or {}).get("content")
                   or m.get("bodyPreview") or "")[:4000]
    if RE_AUTRE_METIER_CORPS.search(subj + "\n" + corps_brut2):
        return "autre_metier", "acces / domotique : releve de la securite"
    if (RE_ADV.search(subj_hors_adresses)
            and not RE_AVANT_VENTE.search(subj_hors_adresses)):
        return "adv", "objet ADV / compta / commerce"

    # Signaux de suivi : la demande n'a pas l'air neuve. On ne jette pas (64% de
    # ces mails ont quand meme donne un ticket), on descend d'un cran de
    # confiance pour que la file de validation les traite a part.
    sg = []
    if m.get("_deja_vu"):
        sg.append("fil deja vu")
    if RE_REPONSE.match(subj):
        sg.append("objet de reponse")
    if interne:
        sg.append("transfert interne DFM")
    if sg:
        return "candidat_faible", "suite probable (" + ", ".join(sg) + ")"
    return "candidat_fort", "demande client neuve"



# ── Symptome : regles metier explicites ────────────────────────────────────
# `part` = proportion de ce symptome parmi les DIT historiques qui declenchent
# la meme regle. C'est une indication de fiabilite affichee a l'agent, PAS un
# seuil de decision : l'historique est bruite (les agents ne s'accordent pas),
# la regle metier prime.
SYMPTOMES_REGLES = [
    # (code, motif, part mesuree sur l'historique, pourquoi)
    ("ADM-365-GES",
     r"(mot de passe|mdp\b|password)"
     r"(?!.{0,40}(session|windows|ordinateur|poste))"
     r".{0,90}(bo[iî]te|messagerie|adresse mail|@)"
     r"|(bo[iî]te|messagerie).{0,90}(mot de passe|mdp\b|password)",
     50, "mot de passe d'une boite mail = administration 365"),
    # Le francais met des mots entre les deux et accorde au pluriel : « suite a
    # l'arrivee de deux nouvelles collaboratrices », « pour nos 4 nouveaux
    # collaborateurs ». Le motif colle d'origine ratait tout cela — Elliot,
    # 2026-09-17, DIT 100174308 : « c'est un onboarding ». Mesure sur les 1181
    # mails de 14 jours : 21 -> 31 mails reconnus, dont 8 vrais onboardings de
    # plus et 2 formulations qui parlent d'arrivants sans en etre une.
    ("ADM-ON",
     r"(arriv[ée]e?s?|nouveau|nouveaux|nouvelles?|int[ée]gration)\b"
     r"[^.!?\n]{0,40}?"
     r"(collaborat|salari|employ|arrivant|utilisateur|coll[èe]gue)",
     75, "arrivee d'un collaborateur"),
    ("ADM-ON",
     r"(cr[ée]ation|cr[ée]er|ouvrir|ouverture)\w*\s+(d[eu\']\s*)?(un\s+|une\s+)?"
     r"(compte|utilisateur|user|acc[èe]s)",
     100, "creation d'un compte"),
    ("ADM-OFF",
     r"(d[ée]part|sortie|quitt\w+|sortant)\s+(de\s+)?"
     r"(collaborat|salari|employ|m\.|mme|utilisateur)"
     r"|supprimer\s+(le\s+)?(compte|utilisateur)",
     100, "depart d'un collaborateur"),
    ("ADM-365-LIC",
     r"licence\s*(office|365|microsoft)|attribuer une licence|ajouter une licence",
     50, "licence 365"),
    # Deux regles ajoutees le 2026-09-15, les SEULES qui aient passe la mesure
    # sur les 5 718 DIT reelles. Vingt-trois motifs ont ete essayes : la
    # plupart plafonnent entre 20 et 35 % de precision, parce que le meme
    # vocabulaire sert a plusieurs symptomes (« outlook » -> 36 %, « vpn » ->
    # 31 %, « licence » -> 10 %). On ne garde que ce qui tient.
    ("NET-DEM-FW",
     r"(pare[- ]?feu|firewall|ouvrir\s+(un\s+)?port|r[èe]gle\s+de\s+filtrage)",
     62, "pare-feu : 62 % des DIT contenant ces mots"),
    ("ADM-365-EXC-BAL",
     r"(acc[èe]s|droits?|d[ée]l[ée]gation)\s+.{0,30}bo[iî]te\s+(mail|aux lettres)",
     56, "droits sur une boite mail : 56 % des DIT contenant cette tournure"),
]

# Symptome de repli. PAS « AUTRES » : ce code appartient a la TELEPHONIE dans
# Artis — verifie sur les DIT reelles, les 6 dernieres portant ce code sont
# toutes en domaine TEL, libelle « TELEPHONIE - Autres ». C'est ce qu'Elliot a
# vu apparaitre sur nos tickets informatiques le 2026-09-15.
# Les equivalents du domaine INFO sont « PC/MAC - Demande - Autres » (99 DIT)
# et « PC/MAC - Incident - Autres » (35 DIT) : on choisit selon que le mail
# demande quelque chose ou signale une panne.
RE_DEMANDE = re.compile(
    r"(pouvez[- ]vous|pourriez[- ]vous|merci\s+de\s+(bien\s+vouloir\s+)?"
    r"(cr[ée]er|ajouter|installer|param[ée]trer|donner|mettre)"
    r"|je\s+souhaite|nous\s+souhaitons|demande\s+de\s+cr[ée]ation"
    r"|serait[- ]il\s+possible)", re.I)


def symptome_de_repli(subj, corps):
    """Le symptome a poser quand aucune regle ne tranche."""
    texte = "%s\n%s" % (subj or "", corps or "")
    return "PC-DEM-AUT" if RE_DEMANDE.search(texte) else "PC-INC-AUT"


def deviner_symptome(subj, corps):
    """(code, pourquoi, part) — ou (None, '', 0) si aucune regle ne s'applique.

    On lit l'objet ET le corps : la demande d'Elliot (« CHANGEMENT MOT DE PASSE
    BOITE MAIL ») est dans l'objet, mais beaucoup d'autres ne le sont que dans
    le corps.
    """
    texte = "%s\n%s" % (subj or "", corps or "")
    for code, motif, part, pourquoi in SYMPTOMES_REGLES:
        if re.search(motif, texte, re.I):
            return code, pourquoi, part
    return None, "", 0


# Codes Artis par categorie de demande. Sources : /parametrage/typeDit et
# /parametrage/natures. Defauts arretes avec Elliot le 2026-09-08.
CATEGORIES = {
    "hotline":     {"type_dit": "05", "nature": "11"},   # EMAIL CLIENT / Hotline
    "avant_vente": {"type_dit": "05", "nature": "70"},   # EMAIL CLIENT / Avant Vente
    # La nature est TOUJOURS « 11 Hotline », y compris pour une alerte de
    # supervision : Elliot, 2026-09-15. Seule l'avant-vente garde sa nature
    # propre (« 70 Avant Vente »), qui est sa regle a lui depuis le debut.
    "supervision": {"type_dit": "07", "nature": "11"},   # SUPERVISION / Curatif
}


def categorie(m):
    """'hotline' | 'avant_vente' | 'supervision' — determine le type et la
    nature du DIT a proposer. Mesure : ces deux familles representaient 26 des
    196 mails que les regles jetaient a tort."""
    subj = m.get("subject") or ""
    exp = addr(m)
    if RE_SUPERVISION.search(subj + " " + exp):
        return "supervision"
    if RE_AVANT_VENTE.search(RE_ADRESSE.sub(" ", subj)):
        return "avant_vente"
    return "hotline"


def _prefere_informatique(rows, index):
    """Reordonne les comptes candidats : ceux qui ont un contrat informatique
    d'abord.

    Une meme adresse mail peut etre enregistree sur plusieurs comptes du meme
    client — chez DFM, typiquement un compte impression et un compte
    informatique. Prendre le premier venu donnait le compte impression, et
    avec lui son domaine et ses copieurs (DIT 100173686).
    """
    if not rows:
        return rows
    csi = index.get("csi") or set()
    if not csi:
        return rows
    avec = [r for r in rows if r.get("client") in csi]
    return avec or rows


def _racine(txt):
    """Un nom reduit a ses lettres et chiffres, sans accents ni ponctuation.
    « FARGO INVEST » et « fargo-invest.fr » rendent tous deux « fargoinvest »."""
    import unicodedata
    t = (txt or "").split("@")[-1]
    for suf in (".fr", ".com", ".net", ".org", ".eu", ".io", ".biz"):
        if t.lower().endswith(suf):
            t = t[:-len(suf)]
            break
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return "".join(c for c in t.lower() if c.isalnum())


def trouver_client(m, index):
    """(codeClient, idInterlocuteur, methode).

    Ordre : adresse de l'expediteur, puis -- s'il est interne DFM -- adresses
    citees dans le corps (transfert), puis domaine. Le domaine dfm.fr n'est
    jamais utilise : il correspond a des dizaines de clients.
    """
    exp = addr(m)
    dom = exp.split("@")[-1]

    # Un expediteur interne DFM ne renseigne PAS le client : ces adresses sont
    # enregistrees comme interlocuteurs d'entites du groupe (service.client@dfm.fr
    # -> « DFM PACA »). On saute donc la correspondance par email et on cherche
    # le vrai demandeur dans le corps du transfert.
    if dom not in INTERNE:
        rows = _prefere_informatique(index["emails"].get(exp), index)
        if rows and rows[0]["client"] not in CLIENTS_INTERNES:
            return rows[0]["client"], rows[0]["id"], "email exact"

    if dom in INTERNE:
        # transfert par un agent : le vrai demandeur est cite dans le corps
        corps = (m.get("body") or {}).get("content") or m.get("bodyPreview") or ""
        for a in RE_ADR_CORPS.findall(corps):
            a = a.lower()
            if a.split("@")[-1] in INTERNE:
                continue
            r2 = _prefere_informatique(index["emails"].get(a), index)
            if r2 and r2[0]["client"] not in CLIENTS_INTERNES:
                return r2[0]["client"], r2[0]["id"], "email cite dans le transfert"
        for a in RE_ADR_CORPS.findall(corps):
            d2 = a.lower().split("@")[-1]
            if d2 in INTERNE:
                continue
            c2 = [c for c, _ in (index["domaines"].get(d2) or [])
                  if c not in CLIENTS_INTERNES]
            if len(c2) == 1:
                return c2[0], None, "domaine cite dans le transfert"
        # Le DESTINATAIRE, enfin : quand un technicien ecrit AU client et met
        # le support en copie, le client n'est cite nulle part dans le texte —
        # il est dans le champ « A ». On ne regardait que le corps, et ces
        # mails restaient sans client, donc sans issue. Constate le 2026-09-21
        # sur « probleme de connexion VPN » (Pascal OLIVIER -> metroneurope.fr).
        dests = [((x.get("emailAddress") or {}).get("address") or "").lower()
                 for x in ((m.get("toRecipients") or [])
                           + (m.get("ccRecipients") or []))]
        dests = [a for a in dests if a and a.split("@")[-1] not in INTERNE]
        for a in dests:
            r3 = _prefere_informatique(index["emails"].get(a), index)
            if r3 and r3[0]["client"] not in CLIENTS_INTERNES:
                return r3[0]["client"], r3[0]["id"], "email du destinataire"
        for a in dests:
            d3 = a.split("@")[-1]
            if d3 in DOMAINES_PUBLICS:
                continue
            c3 = [c for c, _ in (index["domaines"].get(d3) or [])
                  if c not in CLIENTS_INTERNES]
            if len(c3) == 1:
                return c3[0], None, "domaine du destinataire"
        return None, None, "transfert interne, demandeur introuvable"
    # Une adresse personnelle ne designe AUCUN client : @gmail.com pointe sur
    # 326 clients differents dans notre index, @orange.fr sur 133. Prendre le
    # plus frequent revient a tirer au sort — c'est ce qui a impute la demande
    # d'un nouvel arrivant d'ELEVIO GROUP a « NATAL BOULOGNE » (P-0177).
    # L'adresse EXACTE, elle, reste valable : elle est testee plus haut.
    if dom in DOMAINES_PUBLICS:
        return None, None, "adresse personnelle : le client n'est pas déductible"
    # Le DOMAINE est inconnu, mais la PARTIE LOCALE peut suffire. Une adresse
    # comme « al.terrine » est un identifiant : elle ne designe qu'une personne.
    # Le 2026-09-25, Anne-Laure TERRINE a ecrit depuis « al.terrine@wsn-events.com »
    # alors qu'Artis la connait en « al.terrine@wsn.community » — meme personne,
    # l'entreprise a change de nom de domaine. Le mail restait sans client.
    # On n'accepte QUE si cette partie locale est unique dans tout l'index :
    # « contact » ou « compta » designent des centaines de gens, pas quelqu'un.
    local = (exp or "").split("@")[0]
    if local and len(local) >= 5 and "." in local:
        memes = [v for a, v in (index.get("emails") or {}).items()
                 if str(a).split("@")[0] == local]
        gens = [g for v in memes for g in (v or [])
                if g.get("client") not in CLIENTS_INTERNES]
        clients = {g["client"] for g in gens}
        if len(memes) == 1 and len(clients) == 1:
            g = gens[0]
            return g["client"], g.get("id"), "partie locale unique (%s)" % local

    cands = [(c, n) for c, n in (index["domaines"].get(dom) or [])
             if c not in CLIENTS_INTERNES]
    # Un meme client peut avoir plusieurs comptes (impression, informatique...).
    # On ne garde que ceux qui ont un contrat informatique, sauf si aucun n'en
    # a — auquel cas on laisse la liste telle quelle et le doute s'affichera.
    csi = index.get("csi") or set()
    if csi:
        avec = [(c, n) for c, n in cands if c in csi]
        if avec:
            cands = avec
    if len(cands) == 1:
        return cands[0][0], None, "domaine unique"
    if len(cands) > 1:
        # Le NOM du client departage avant la frequence : « fargo-invest.fr »
        # designe FARGO INVEST, pas l'agence du groupe qui partage sa
        # messagerie. Le 2026-09-22, ce domaine pointait sur trois comptes et
        # le plus frequent (SDVO - AGENCE SHIVA, 6 contacts) l'emportait sur
        # FARGO INVEST (5) — le mail restait bloque « client non etabli »
        # alors que la reponse etait dans le nom de domaine.
        racine = _racine(dom)
        libelles = index.get("libelles") or {}
        exacts = [c for c, _ in cands if _racine(libelles.get(c, "")) == racine]
        if len(exacts) == 1:
            return exacts[0], None, "nom du client = nom de domaine"
        # Decision d'Elliot (2026-09-08) : on propose le client le plus frequent
        # du domaine MAIS le doute doit etre visible dans la file de validation.
        # Cas typique : les agences d'un groupe partagent un domaine (@shiva.fr).
        #
        # SAUF quand la majorite est ECRASANTE. Le 2026-09-25, « lvld.fr » se
        # partageait entre NORTH COMMUNICATION (3 contacts) et COM'OVER (1) —
        # deux societes d'un meme groupe. Le doute bloquait une demande simple
        # (installation d'Acrobat) alors que le rapport de 3 a 1 ne laisse pas
        # vraiment de place a l'hesitation. Un rapport de 2 a 2, ou de 3 a 2,
        # reste douteux et le restera : c'est l'ecart qui tranche, pas le rang.
        if len(cands) > 1 and cands[0][1] >= 3 and cands[0][1] >= 3 * cands[1][1]:
            return cands[0][0], None, "domaine partage, majorite nette (%d contre %d)" % (
                cands[0][1], cands[1][1])
        return cands[0][0], None, "domaine ambigu (%d clients)" % len(cands)
    return None, None, "inconnu"


def deviner_domaine(txt):
    t = norm(txt)
    best, score = "INFO", 0
    for code, kws in DOMAINES_KW.items():
        n = sum(1 for k in kws if k in t)
        if n > score:
            best, score = code, n
    return best, score


def deviner_priorite(txt):
    """(code, explication). 02 par defaut, 01 si toute la societe est bloquee.

    Elliot, 2026-09-16, sur le DIT 100174261 : « tu l'avais mis en priorite 3,
    je l'ai passe en 2 ». Mesure sur les 392 DIT du corpus qui portent une
    priorite : l'equipe met 02 dans 79,4 % des cas, 03 dans 14,7 %, 01 dans
    2,0 %. L'ancienne regle annoncait 03 pour 352 d'entre eux et se trompait
    290 fois ; 02 par defaut en vise 324 sur 392, soit 83 %.

    Le 03 n'a plus de branche : rien dans le texte d'un mail ne le predit —
    56 bons contre 290 faux. Mieux vaut le laisser a l'agent qui relit.
    """
    t = norm(txt)
    if RE_BLOQUE_TOUS.search(t):
        return PRIO_URGENT, "toute la societe semble bloquee"
    if RE_BLOQUE_UN.search(t):
        return PRIO_IMPORTANT, "une personne semble bloquee"
    return PRIO_IMPORTANT, "priorite courante de l'equipe"


# ---------------------------------------------------------------- rapprochement
def rapprocher(mails, dits, fenetre_h=48):
    """Pour chaque mail, cherche le DIT cree juste apres dont le commentaire
    ressemble au corps du mail.

    Le diff complet (SequenceMatcher.ratio) coute cher : ~500k comparaisons sur
    14 jours. On prefiltre donc en deux passes de plus en plus chere :
      1. recouvrement de vocabulaire (mots de 5+ lettres) -> tres rapide
      2. quick_ratio (borne superieure du ratio) -> ecarte sans calculer le diff
      3. ratio complet seulement sur les rares survivants
    """
    par_jour = collections.defaultdict(list)
    for d in dits:
        try:
            t = datetime.datetime.fromisoformat(d["date"])
        except Exception:
            continue
        com = norm(d.get("commentaire"))[:600]
        if not com:
            continue
        mots = {w for w in re.findall(r"[a-z]{5,}", com)}
        par_jour[t.date()].append((t, d, com, mots))

    res = []
    for m in mails:
        try:
            tm = datetime.datetime.fromisoformat(
                m["receivedDateTime"].replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            res.append((m, None, 0.0))
            continue
        corps = norm(body_utile((m.get("body") or {}).get("content")
                                or m.get("bodyPreview") or ""))[:600]
        if not corps:
            res.append((m, None, 0.0))
            continue
        mots_m = {w for w in re.findall(r"[a-z]{5,}", corps)}
        if not mots_m:
            res.append((m, None, 0.0))
            continue

        sm = difflib.SequenceMatcher()
        sm.set_seq2(corps)
        best, bs = None, 0.0
        for delta in range(0, int(fenetre_h / 24) + 2):
            jour = (tm + datetime.timedelta(days=delta)).date()
            for t, d, com, mots_d in par_jour.get(jour, []):
                if not (tm - datetime.timedelta(hours=2) <= t
                        <= tm + datetime.timedelta(hours=fenetre_h)):
                    continue
                # passe 1 : vocabulaire commun
                inter = len(mots_m & mots_d)
                if inter < 2 or inter / float(min(len(mots_m), len(mots_d))) < 0.25:
                    continue
                # passe 2 : borne superieure pas chere
                sm.set_seq1(com)
                if sm.quick_ratio() <= bs:
                    continue
                # passe 3 : diff complet
                s = sm.ratio()
                if s > bs:
                    best, bs = d, s
        res.append((m, best, bs))
    return res


if __name__ == "__main__":
    fm = MC / ("mails_%dj.json" % DAYS)
    if not fm.exists():
        raise SystemExit("corpus mails absent : %s\nlance d'abord : python _mail_fetch.py %d"
                         % (fm, DAYS))
    mails = json.load(open(fm, encoding="utf-8"))
    dits  = json.load(open(SCRATCH / "artis_dits_60j.json", encoding="utf-8"))
    idxf  = MC / "client_index_all.json"
    if not idxf.exists():
        idxf = MC / "client_index_email.json"
    index = json.load(open(idxf, encoding="utf-8"))
    print("mails=%d  dits(60j)=%d  index=%s (%d emails)"
          % (len(mails), len(dits), idxf.name, len(index["emails"])))

    num_dits = {d["numero"] for d in dits}
    prepare_fils(mails)
    verdicts, motifs, cand = collections.Counter(), collections.Counter(), []
    for m in mails:
        v, mot = classer(m, num_dits)
        verdicts[v] += 1
        motifs["%s: %s" % (v, mot.split("(")[0].strip())] += 1
        if v.startswith("candidat"):
            m["_tier"] = v
            cand.append(m)

    print("\n=== TRI DES MAILS ===")
    for k, v in verdicts.most_common():
        print("  %5d  %s" % (v, k))
    print("\n-- motifs --")
    for k, v in motifs.most_common(12):
        print("  %5d  %s" % (v, k))

    print("\n=== MATCHING CLIENT (sur %d candidats) ===" % len(cand))
    meth, sans = collections.Counter(), []
    for m in cand:
        c, pid, how = trouver_client(m, index)
        m["_client"], m["_pid"], m["_how"] = c, pid, how
        meth[how.split("(")[0].strip()] += 1
        if not c:
            sans.append(addr(m))
    for k, v in meth.most_common():
        print("  %5d  %s" % (v, k))
    if sans:
        doms = sorted({s.split("@")[-1] for s in sans})
        print("  -> %d domaines inconnus, ex: %s" % (len(doms), ", ".join(doms[:8])))

    print("\n=== RAPPROCHEMENT MAIL -> DIT REEL ===")
    pairs = rapprocher(cand, dits)
    for s in (0.75, 0.55, 0.40):
        n = sum(1 for _, d, sc in pairs if d and sc >= s)
        print("  similarite >= %.2f : %d/%d mails retrouves dans Artis" % (s, n, len(pairs)))
    ok = [(m, d, sc) for m, d, sc in pairs if d and sc >= 0.55]
    print("\n-- exemples de paires retenues --")
    for m, d, sc in ok[:6]:
        print("  [%.2f] %-34s -> DIT %s %-8s | client devine: %s (%s)"
              % (sc, addr(m)[:34], d["numero"], (d.get("client") or {}).get("code", "?"),
                 m.get("_client"), m.get("_how")))
    if ok:
        just = sum(1 for m, d, sc in ok
                   if m.get("_client") == (d.get("client") or {}).get("code"))
        print("\n  client correctement devine : %d/%d (%.0f%%)"
              % (just, len(ok), 100.0 * just / len(ok)))

    print("\n=== CLASSIFICATION DOMAINE / PRIORITE (candidats) ===")
    dm, pr = collections.Counter(), collections.Counter()
    for m in cand:
        txt = (m.get("subject") or "") + " " + body_utile(
            (m.get("body") or {}).get("content") or m.get("bodyPreview") or "")
        d, sc = deviner_domaine(txt)
        dm[d + (" (faible)" if sc == 0 else "")] += 1
        pr[deviner_priorite(txt)] += 1
    for k, v in dm.most_common():
        print("  %5d  domaine %s" % (v, k))
    for k, v in sorted(pr.items()):
        print("  %5d  priorite %s" % (v, k))

    out = MC / ("rapport_%dj.json" % DAYS)
    json.dump({"verdicts": dict(verdicts), "motifs": dict(motifs),
               "matching_client": dict(meth),
               "paires": [{"mail": addr(m), "subject": m.get("subject"),
                           "recu": m.get("receivedDateTime"), "score": round(sc, 3),
                           "dit": (d or {}).get("numero"),
                           "dit_client": ((d or {}).get("client") or {}).get("code"),
                           "client_devine": m.get("_client"), "methode": m.get("_how")}
                          for m, d, sc in pairs]},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n-> %s" % out)
