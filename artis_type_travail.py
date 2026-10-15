# -*- coding: utf-8 -*-
"""Poser le type de travail d'une intervention Artis (Terrain / Hotline /
Atelier) — le seul champ que l'API REST refuse d'écrire.

Validé le 2026-09-10 sur la DIT 100173421 : `Terrain` → `Hotline`, avec
relecture REST prouvant qu'aucun autre champ n'a bougé.

Le chemin, reconstitué depuis le bundle JS d'Artis (`resources/js/all-min.js`
et `shared/js.action?composant=<cc…>`) :

  1. recherche SOLR du numéro de DIT → `data_long_id_dit` et `idBo`, les deux
     identifiants internes (DIT et intervention)
  2. GET `composants/commun/accueil/entreeVisualiser.action?<params>` → une
     page neutre qui porte un `mainForm`
  3. `submit.action` avec `userEvent=evt_genericLink` et
     `actionRedirectLink=ccIt`, `…Domaine=services`, `…Entree=modif`,
     `…Id=<idDit>;<idInter>` → **directement** l'intervention en mode saisie
     ⚠ les userEvent sont en **minuscule initiale** (`evt_…`), pas `Evt_…`
     comme dans le nom des fonctions `validate_Evt_…` du HTML
     ⚠ `submit.action` est relatif : l'URL dépend du domaine du composant
     d'où l'on part (`commun/accueil`, `services/ccIt`, …)
  4. `shared/arrays/expand/show.action` (arrayName=`ea_Tech`, rowKey) puis
     `shared/ajax-container/load.action` → le bloc déplié de l'intervention
  5. `shared/form/flush.action` puis `shared/form/save.action` avec
     `expandable_<rowKey>_cbo_typeTravail=<valeur>` → `Enregistrer_OK`
     ⚠ Artis rend ce `<select>` avec l'attribut `disabled` ; le serveur accepte
     malgré tout la valeur postée.
  6. `submit.action` avec `userEvent=oK_Enregistrer` → sort du mode saisie
     (ou `evtSortieChemin` pour abandonner : une saisie laissée en cours
     bloque ensuite toute la navigation de la session)

La navigation écran par écran (recherche rapide sur la loupe `itf_ditCode`,
`removeContextList`, `evt_ccIt_Visualiser_` puis `evt_ccIt_Modifier_`)
fonctionne aussi mais dépend de l'état du chemin de navigation courant, donc
elle est fragile. Le lien générique s'en affranchit.

⚠ PIÈGE QUI COÛTE UNE HEURE : `flush.action` et `save.action` attendent un
champ **`componentName`** qui n'existe PAS dans le formulaire — la fonction
`ajaxFormParams` du bundle l'ajoute à la main, avec `cKey`, `cStatus` et
`session`. Sans lui, les deux répondent la page « Erreur — ouvrir une nouvelle
session », alors que tout le reste de la session fonctionne parfaitement. Le
message d'erreur oriente vers un faux problème (la session) au lieu du vrai
(un paramètre manquant).

LICENCE — la contrainte qui décide de l'usage : ce chemin passe par
l'interface web, donc par une **session applicative**, et chaque connexion
consomme un siège de licence global à DFM (cf. mémoire
`feedback_artis_licence_sieges`).

D'où `Web.ouvrir()` : relit la session du compte dédié dans
`<DATA_DIR>/artis_mco_session.json`, et ne se reconnecte que si elle est morte.
**Un** siège pendant que l'outil travaille, pas un par ticket. Identifiants du
compte dédié (MCO, créé par Elliot le 2026-09-10) dans
`<DATA_DIR>/artis_mco.json` — répertoire ignoré par git.

Il n'y a **pas** de déconnexion utilisable : `shared/login/disconnect.action`
(le `sendBeacon` du navigateur) et
`composants/login/auth/PT.action?disconnect=<session>` (le lien « Déconnexion »
du menu) répondent tous deux 200 et laissent la session **pleinement
vivante** — vérifié le 2026-09-10 en rejouant une action complète après coup.
Le siège se libère donc à l'expiration, d'où l'intérêt de réutiliser la session
plutôt que d'en ouvrir une par correction.

Validé de bout en bout sous MCO le 2026-09-10 : aller-retour
`Hotline → Terrain → Hotline` sur la DIT 100173421, deux fois
`returnCode: Enregistrer_OK`, relecture REST sans aucun champ collatéral,
~3 s par passage.

Usage :
    python artis_type_travail.py 100173421              # constat, n'écrit pas
    python artis_type_travail.py 100173421 Hotline --go  # écrit
"""
import io, os, re, sys, json, html, pathlib, requests
from urllib.parse import urlencode

BASE = "https://artis.dfm.fr/ArtisWeb"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TYPES = ("Terrain", "Hotline", "Atelier")
# Le type de FACTURATION de l'intervention. Le formulaire l'ecrit « Non
# facturable » (avec une espace) ; l'API REST le relit « NonFacturable ». Les
# deux orthographes designent la meme chose, et c'est la seconde qui sert de
# preuve. Constate le 2026-09-22 en lisant le select du bloc deplie.
FACTURATIONS = {"Facturable": "Facturable",
                "Non facturable": "NonFacturable",
                "Automatique": "Automatique"}
TABLEAU = "ea_Tech"


def _fichier_session():
    return pathlib.Path(os.environ.get("DATA_DIR", "./data")) / "artis_web_session.json"


def _fichier_session_mco():
    return pathlib.Path(os.environ.get("DATA_DIR", "./data")) / "artis_mco_session.json"


class SessionAbsente(Exception):
    pass


class Web(object):
    """Client HTTP sur une session Artis déjà ouverte (aucune connexion)."""

    def __init__(self, conf=None):
        if conf is None:
            f = _fichier_session()
            if not f.exists():
                raise SessionAbsente(
                    "session web absente (%s). Ce module ne se connecte pas : "
                    "chaque connexion coûte un siège de licence." % f)
            conf = json.loads(f.read_text(encoding="utf-8"))
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA,
                               "X-Requested-With": "XMLHttpRequest",
                               "Origin": "https://artis.dfm.fr",
                               "Accept-Language": "fr-FR,fr;q=0.9"})
        for k, v in conf["cookies"].items():
            self.s.cookies.set(k, v, domain="artis.dfm.fr")
        self.p = {"cKey": conf.get("cKey", "1"),
                  "cStatus": conf.get("cStatus", "P"),
                  "session": conf["session"],
                  "typeLicence": conf.get("typeLicence", "PT")}

    # -- briques ----------------------------------------------------------
    def _ref(self, composant):
        return "%s/composants/services/%s/entreeVisualiser.action?%s" % (
            BASE, composant, urlencode(self.p))

    def get(self, chemin, composant="ccIt", **params):
        q = dict(self.p, componentName=composant, viewName=composant, **params)
        return self.s.get(BASE + "/" + chemin.lstrip("/"), params=q, timeout=60,
                          headers={"Referer": self._ref(composant)})

    def post(self, chemin, donnees, composant="ccIt"):
        d = dict(self.p, componentName=composant, viewName=composant)
        d.update(donnees)
        return self.s.post(BASE + "/" + chemin.lstrip("/"), data=d, timeout=90,
                           headers={"Referer": self._ref(composant),
                                    "Content-Type": "application/x-www-form-"
                                    "urlencoded; charset=UTF-8"})

    def solr(self, terme):
        q = dict(self.p, componentName="ccDit", viewName="ccDit")
        q.update({"q": terme, "q.op": "OR", "wt": "json", "start": "0",
                  "rows": "20", "indent": "true",
                  "fq": "typeBo:(INTERVENTION) AND data_bool_premiere_it:(true)",
                  "sort": "data_ts_date_inter desc"})
        return self.s.get(BASE + "/services/solr/global-search/select", params=q,
                          timeout=40, headers={"Referer": self._ref("ccDit")})

    def soumettre(self, composant, page, userEvent, remplacer=None,
                  domaine="services"):
        """Rejoue le mainForm rendu par le serveur, avec un userEvent.

        Le formulaire poste sur `submit.action` en relatif : l'URL dépend donc
        du domaine du composant (`services/ccIt`, `commun/accueil`, …).
        """
        rep = dict(remplacer or {})
        rep["userEvent"] = userEvent
        corps = _encoder(champs_du_form(page), rep)
        return self.s.post(
            "%s/composants/%s/%s/submit.action" % (BASE, domaine, composant),
            data=corps, timeout=90,
            headers={"Referer": self._ref(composant),
                     "Content-Type": "application/x-www-form-urlencoded"})

    def aller_a(self, page, composant, domaine_cible, entree, ident,
                depuis=("commun", "accueil")):
        """Le lien générique d'Artis : atteindre n'importe quel objet métier.

        Reproduit `actionRedirect_link(target, comp, domaine, entree, id)` du
        bundle JS, dont les pages rendues donnent des exemples réels :
        `actionRedirect_link(this, 'ccIt','services','modif','723701;924780')`
        — soit « ouvrir l'intervention 924780 de la DIT 723701 en
        modification ». C'est le raccourci qui remplace toute la navigation
        écran par écran.
        """
        return self.soumettre(depuis[1], page, "evt_genericLink", {
            "actionRedirectLink": composant,
            "actionRedirectLinkDomaine": domaine_cible,
            "actionRedirectLinkEntree": entree,
            "actionRedirectLinkId": ident,
        }, domaine=depuis[0])



    # -- ouverture et fermeture de session --------------------------------
    @classmethod
    def connecter(cls, login=None, password=None):
        """Ouvre une session web. CONSOMME UN SIÈGE DE LICENCE.

        À n'utiliser qu'avec `session_dediee()`, qui garantit la déconnexion :
        un siège gardé jusqu'à expiration (30 min à 1 h) est un siège volé à
        l'équipe. Identifiants dans `<DATA_DIR>/artis_mco.json`, ou via les
        variables ARTIS_WEB_LOGIN / ARTIS_WEB_PASSWORD.
        """
        if not login or not password:
            login, password = _identifiants()
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Origin": "https://artis.dfm.fr",
                          "Accept-Language": "fr-FR,fr;q=0.9"})
        r = s.get(BASE + "/composants/login/auth/PT.action", timeout=40,
                  allow_redirects=True)
        if r.status_code != 200:
            raise RuntimeError("page de login : HTTP %s" % r.status_code)

        def champ(n):
            m = re.search(r'name="%s"[^>]*value=["\']([^"\']*)["\']' % n, r.text)
            return m.group(1) if m else ""

        p = s.post(r.url.rsplit("/", 1)[0] + "/connect.action", timeout=40,
                   data={"idPoste": champ("idPoste"),
                         "typeLicence": champ("typeLicence") or "PT",
                         "login": login, "password": password,
                         "resterConnecter": "false",
                         "idOrgInt": champ("idOrgInt")},
                   headers={"X-Requested-With": "XMLHttpRequest",
                            "Referer": r.url})
        try:
            j = p.json()
        except ValueError:
            raise RuntimeError("connexion : réponse inattendue (HTTP %s)"
                               % p.status_code)
        cin = j.get("cinematicParams") or ""
        if not cin:
            # inclut « le nombre d'accès simultanés global est atteint »
            raise RuntimeError("connexion refusée : %s"
                               % (j.get("errorMessage") or str(j)[:200]))
        params = dict(kv.split("=", 1) for kv in cin.lstrip("?").split("&")
                      if "=" in kv)
        w = cls(conf={"cookies": {}, "session": params.get("session"),
                      "cKey": params.get("cKey", "1"),
                      "cStatus": params.get("cStatus", "P"),
                      "typeLicence": params.get("typeLicence", "PT")})
        w.s = s                      # on garde les cookies de la connexion
        w.s.headers["X-Requested-With"] = "XMLHttpRequest"
        w.login = j.get("login") or login
        return w


    # -- la bonne façon d'obtenir une session -----------------------------
    @classmethod
    def ouvrir(cls, trace=None):
        """Session MCO réutilisable : on relit celle du disque, on ne se
        reconnecte que si elle est morte.

        C'est le compromis qui tient compte de la licence : un siège occupé
        pendant que l'outil travaille (puis libéré par expiration), au lieu
        d'un siège par ticket corrigé. `deconnecter()` n'aide pas : Artis
        répond 200 et laisse la session vivante (constaté le 2026-09-10).
        """
        dire = trace or (lambda *_a: None)
        f = _fichier_session_mco()
        if f.exists():
            try:
                w = cls(conf=json.loads(f.read_text(encoding="utf-8")))
                if w.vivante():
                    dire("session MCO réutilisée")
                    return w
            except Exception:
                pass
        w = cls.connecter()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"cookies": w.s.cookies.get_dict(),
                                 "session": w.p["session"],
                                 "cKey": w.p["cKey"], "cStatus": w.p["cStatus"],
                                 "typeLicence": w.p["typeLicence"],
                                 "login": getattr(w, "login", "")},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        dire("nouvelle session %s (1 siège de licence)" % getattr(w, "login", "?"))
        return w

    def vivante(self):
        """La session sert-elle encore a NAVIGUER ?

        Premiere version : une requete SOLR. Trop faible — SOLR a continue de
        repondre alors que la session applicative etait morte, l'accueil
        renvoyant l'ecran d'erreur et `removeContextList` un HTTP 410. On
        reutilisait donc une session inutilisable.

        On teste desormais ce dont on a reellement besoin : l'accueil doit
        rendre un `mainForm`. Si une saisie traine, on l'abandonne avant de
        conclure — sinon on jetterait une session parfaitement bonne (et un
        siege de licence avec elle, puisqu'aucune deconnexion ne marche).
        """
        from urllib.parse import urlencode as _u
        url = ("%s/composants/commun/accueil/entreeVisualiser.action?%s"
               % (BASE, _u(self.p)))
        try:
            if 'id="mainForm"' in self.s.get(url, timeout=40).text:
                return True
            liberer(self)
            return 'id="mainForm"' in self.s.get(url, timeout=40).text
        except Exception:
            return False

    def deconnecter(self):
        """Rend le siège de licence.

        C'est ce que fait le lien « Déconnexion » du menu :
        `GET composants/login/auth/PT.action?…&disconnect=<session>`.
        Attention : `shared/login/disconnect.action` (le `sendBeacon` que le
        navigateur envoie en quittant la page) répond 200 mais **ne ferme pas**
        la session — vérifié le 2026-09-10.
        """
        try:
            r = self.s.get(BASE + "/composants/login/auth/PT.action", timeout=40,
                           params={"sso": "false", "autoConnect": "false",
                                   "disconnect": self.p.get("session")})
            return r.status_code
        except Exception:
            return None


class session_dediee(object):
    """`with session_dediee() as w:` — ouvre, puis ferme quoi qu'il arrive.

    Le seul usage acceptable de la connexion automatique : le siège est rendu
    à la sortie du bloc, y compris en cas d'exception.
    """

    def __init__(self, login=None, password=None):
        self._id = (login, password)
        self.w = None

    def __enter__(self):
        self.w = Web.connecter(*self._id)
        return self.w

    def __exit__(self, *_exc):
        if self.w is not None:
            self.w.deconnecter()
        return False


def _identifiants():
    """Compte dédié à l'automatisation.

    Trois sources, dans l'ordre : les variables ARTIS_WEB_*, le fichier
    `artis_mco.json` (poste local), puis ARTIS_USERNAME / ARTIS_PASSWORD — qui
    portent MCO depuis la bascule du 2026-09-10, donc rien à déployer de plus
    pour que le conteneur sache se connecter.
    """
    for a, b in (("ARTIS_WEB_LOGIN", "ARTIS_WEB_PASSWORD"),):
        if os.environ.get(a) and os.environ.get(b):
            return os.environ[a], os.environ[b]
    f = pathlib.Path(os.environ.get("DATA_DIR", "./data")) / "artis_mco.json"
    if f.exists():
        d = json.loads(f.read_text(encoding="utf-8"))
        return d["login"], d["password"]
    if os.environ.get("ARTIS_USERNAME") and os.environ.get("ARTIS_PASSWORD"):
        return os.environ["ARTIS_USERNAME"], os.environ["ARTIS_PASSWORD"]
    raise SessionAbsente("identifiants Artis web absents (ni ARTIS_WEB_*, ni %s, "
                         "ni ARTIS_USERNAME/PASSWORD)" % f)

# -- sérialisation de formulaire, à l'identique du navigateur ---------------
def champs_du_form(texte):
    """Champs du `mainForm` : (nom, valeur), dans l'ordre du document.

    Le navigateur n'envoie pas les boutons ; les cases non cochées non plus.
    Les champs `disabled` non non plus — mais on les garde volontairement,
    parce que c'est précisément le cas de `cbo_typeTravail` et que le serveur
    les accepte.
    """
    i = texte.find('id="mainForm"')
    if i < 0:
        raise ValueError("mainForm absent de la page")
    seg = texte[i:]
    out = []
    for m in re.finditer(r"<input\b([^>]*)>", seg):
        a = m.group(1)
        nom = re.search(r'name="([^"]*)"', a)
        if not nom:
            continue
        typ = (re.search(r'type="([^"]*)"', a) or [None, "text"])[1]
        if typ in ("submit", "button", "file"):
            continue
        if typ in ("checkbox", "radio") and "checked" not in a:
            continue
        val = re.search(r'value="([^"]*)"', a)
        out.append((html.unescape(nom.group(1)),
                    html.unescape(val.group(1)) if val else ""))
    for m in re.finditer(r"<select\b([^>]*)>(.*?)</select>", seg, re.S):
        nom = re.search(r'name="([^"]*)"', m.group(1))
        if not nom:
            continue
        sel = re.search(r'<option[^>]*\bselected\b[^>]*value="([^"]*)"', m.group(2)) \
            or re.search(r'value="([^"]*)"[^>]*\bselected\b', m.group(2))
        out.append((html.unescape(nom.group(1)),
                    html.unescape(sel.group(1)) if sel else ""))
    for m in re.finditer(r"<textarea\b([^>]*)>(.*?)</textarea>", seg, re.S):
        nom = re.search(r'name="([^"]*)"', m.group(1))
        if nom:
            out.append((html.unescape(nom.group(1)), html.unescape(m.group(2))))
    return out


# Deux chemins, deux encodages — et rien dans la page ne le dit :
#   - `submit.action` est une vraie soumission de formulaire, qui respecte
#     l'attribut `accept-charset="ISO-8859-15"` du mainForm ;
#   - `shared/form/flush.action` et `save.action` sont appeles en ajax par
#     jQuery, qui encode TOUJOURS en UTF-8 et annonce `charset=UTF-8`.
# Poster du latin-1 sur save.action fait qu'Artis le decode comme de l'UTF-8,
# n'y reconnait rien, et supprime tous les accents : « debloquer » devient
# « dbloquer ». Invisible tant qu'on n'ecrit que de l'ASCII (« Hotline »).
FORMULAIRE = "iso-8859-15"   # submit.action
AJAX = "utf-8"               # shared/form/*.action


def _encoder(champs, remplacer=None, charset=FORMULAIRE):
    remplacer = dict(remplacer or {})
    paires, vus = [], set()
    for n, v in champs:
        if n in remplacer:
            v, _ = remplacer[n], vus.add(n)
        paires.append((n, v))
    paires += [(n, v) for n, v in remplacer.items() if n not in vus]
    return urlencode(paires, encoding=charset,
                     errors="replace").encode(charset, "replace")


# -- le chemin complet -------------------------------------------------------
def liberer(w, note=None):
    """Abandonne toute saisie en cours, sur l'ecran DIT comme sur celui des
    interventions.

    Tant qu'un ecran est en mode (M), Artis refuse de naviguer : meme l'accueil
    renvoie « Une erreur est survenue ». Un script qui ouvre un ecran en
    modification et se termine sans en sortir bloque donc la session entiere —
    et la suivante, puisqu'on reutilise le meme jeton.
    """
    dire = note or (lambda *_a: None)
    for composant, vue in (("ccIt", "show_ccIt"), ("ccDit", "show_ccDit")):
        for _ in range(3):
            p = w.s.get("%s/composants/services/%s/%s.action?%s"
                        % (BASE, composant, vue, urlencode(w.p)), timeout=60).text
            if 'id="mainForm"' not in p or "(M)" not in _fil(p):
                break
            dire("saisie en cours sur %s" % composant, "abandon")
            w.soumettre(composant, p, "evtSortieChemin")

    # Vider la pile de contexte : tant qu'on est DANS un chemin, l'accueil
    # repond l'ecran d'erreur, meme sans aucune saisie en cours. C'est ce que
    # fait `goToAccueil()` cote interface — `removeContextList` sans `count`.
    a = w.s.get("%s/composants/commun/accueil/entreeVisualiser.action?%s"
                % (BASE, urlencode(w.p)), timeout=60).text
    if 'id="mainForm"' not in a:
        r = w.get("composants/commun/navigation/removeContextList.action",
                  composant="ccDit")
        try:
            loc = (r.json() or {}).get("location") or ""
        except ValueError:
            loc = ""
        if loc:
            w.s.get(BASE + "/composants/" + loc.lstrip("/"), timeout=60)
        dire("chemin de navigation", "vide")


def poser_type_travail(numero_dit, valeur="Hotline", confirmer=False, web=None,
                       trace=None, forcer=False, aussi=None):
    """Pose `valeur` comme type de travail de l'intervention 01 de `numero_dit`.

    `aussi` : autres combos du MÊME bloc à poser dans la même session, sous la
    forme {nom_court_du_champ: valeur}. Une session web coûte un siège de
    licence Artis (cf. mémoire `feedback_artis_licence_sieges`) : poser deux
    champs en deux sessions serait payer deux fois pour le même écran.
    Aujourd'hui employé pour `cb_typeFactu` — le type de facturation.

    Sans `confirmer`, s'arrête juste avant l'écriture et rend le constat.
    Rend un dict : `ok`, `avant`, `apres`, `etapes`, et `erreur` s'il y a lieu.
    """
    dire = trace or (lambda *_a: None)
    if valeur not in TYPES:
        return {"ok": False, "erreur": "type inconnu : %s (attendu %s)"
                % (valeur, "/".join(TYPES))}
    aussi = dict(aussi or {})
    for k, v in aussi.items():
        if k == "cb_typeFactu" and v not in FACTURATIONS:
            return {"ok": False, "erreur": "facturation inconnue : %s (attendu %s)"
                    % (v, "/".join(FACTURATIONS))}
    # `Web()` lirait l'ANCIENNE session (celle qu'Elliot avait pretee), morte
    # depuis longtemps. `ouvrir()` prend celle du compte dedie et la renouvelle
    # si besoin.
    w = web or Web.ouvrir()
    etapes = []

    def note(quoi, detail=""):
        etapes.append(quoi + ((" — " + detail) if detail else ""))
        dire("   %s %s" % (quoi, detail))

    # 0. Point de départ : l'accueil, la seule page toujours atteignable et qui
    #    porte un `mainForm`. Si elle ne répond pas, c'est en général qu'une
    #    saisie est restée en cours — tant qu'une modification est pendante,
    #    Artis refuse de dépiler le chemin de navigation et tout ce qui suit
    #    part de travers. On l'abandonne, puis on redemande l'accueil.
    def accueil():
        return w.s.get("%s/composants/commun/accueil/entreeVisualiser.action?%s"
                       % (BASE, urlencode(w.p)), timeout=60).text

    depart = accueil()
    if 'id="mainForm"' not in depart:
        liberer(w, note)
        depart = accueil()
    if 'id="mainForm"' not in depart:
        return {"ok": False, "erreur": "impossible de repartir de l'accueil "
                "Artis (session bloquee ou expiree)", "etapes": etapes}
    if 'id="mainForm"' not in depart:
        return {"ok": False, "erreur": "l'accueil Artis ne répond pas "
                "(session expirée ?)", "etapes": etapes}

    # 1. Identifiants internes. L'index SOLR donne les deux clés dont le lien
    #    générique a besoin : l'id de la DIT et celui de l'intervention.
    docs = ((w.solr(numero_dit).json().get("response") or {}).get("docs") or [])
    docs = [d for d in docs if str(d.get("code", "")).startswith(numero_dit)]
    if not docs:
        return {"ok": False, "erreur": "DIT %s introuvable dans l'index"
                % numero_dit, "etapes": etapes}
    id_dit, id_inter = docs[0].get("data_long_id_dit"), docs[0].get("idBo")
    note("SOLR", "id_dit=%s id_inter=%s intervention=%s"
         % (id_dit, id_inter, docs[0].get("code")))

    # 2. Saut direct sur l'intervention, en mode saisie.
    page = w.aller_a(depart, "ccIt", "services", "modif",
                     "%s;%s" % (id_dit, id_inter)).text
    if "(M)" not in _fil(page):
        return {"ok": False, "erreur": "l'intervention ne s'ouvre pas en "
                "modification (DIT annulée ou clôturée ?) — écran : %s"
                % (_fil(page) or "inconnu"), "etapes": etapes}
    rk = sorted(set(re.findall(r'"unique-key":"(\d+)"', page)))
    if len(rk) != 1:
        _abandonner(w, page, note)
        return {"ok": False, "erreur": "attendu 1 ligne d'intervention, vu %d"
                % len(rk), "etapes": etapes}
    rowkey = rk[0]
    note("intervention en modification", "rowKey=%s" % rowkey)

    # 4. Bloc déplié de la ligne : c'est là que vit le combo.
    r = w.get("shared/arrays/expand/show.action", arrayName=TABLEAU,
              rowKey=rowkey)
    conteneurs = (r.json() or {}).get("ajaxContainerTypeParName") or {}
    if not conteneurs:
        _abandonner(w, page, note)
        return {"ok": False, "erreur": "dépliage impossible", "etapes": etapes}
    charge = [{"ajaxContainerId": n, "ajaxContainerType": t,
               "currentMaxTabIndex": 1} for n, t in conteneurs.items()]
    bloc = w.post("shared/ajax-container/load.action",
                  {"containersToLoadParam": json.dumps(charge)}).text

    champ = "expandable_%s_cbo_typeTravail" % rowkey
    if champ not in bloc:
        _abandonner(w, page, note)
        return {"ok": False, "erreur": "%s absent du bloc déplié" % champ,
                "etapes": etapes}
    # Les champs en plus : absents du bloc, on les OUBLIE plutôt que d'échouer.
    # Le type de travail, lui, reste la raison d'être de cet appel.
    remplacements = {}
    ignores = []
    for court, val in aussi.items():
        nom = "expandable_%s_%s" % (rowkey, court)
        if nom in bloc:
            remplacements[nom] = val
        else:
            ignores.append(court)
    if ignores:
        note("champs absents du bloc", ", ".join(ignores))

    paires = champs_du_form(page) + champs_du_form('id="mainForm"' + bloc)
    actuel = ([v for n, v in paires if n == champ] or [None])[0]
    vu = ([v for n, v in paires if n.endswith("_t_numeroIT")] or [""])[0]
    if not vu.startswith(numero_dit):
        _abandonner(w, page, note)
        return {"ok": False, "erreur": "l'écran affiche l'intervention %s, "
                "pas celle de %s" % (vu, numero_dit), "etapes": etapes}
    note("bloc déplié", "%s : %s" % (vu, actuel))
    if not actuel:
        # Cas observé sur une DIT annulée : le combo est rendu vide et
        # l'enregistrement renvoie une page d'erreur au lieu du JSON.
        _abandonner(w, page, note)
        # Deux causes distinctes, et le message doit les separer : une
        # intervention annulee n'a plus de valeur dans le combo, mais une
        # intervention d'une AUTRE ACTIVITE (Impression, Securite...) n'a pas
        # le champ du tout — il n'y a alors rien a corriger, c'est le domaine
        # du DIT qui est faux.
        activite = (re.search(r'name="expandable_\d+_t_activite"[^>]*value="([^"]*)"',
                              bloc) or [None, "?"])[1]
        if champ not in bloc or activite.lower() not in ("informatique", "?"):
            return {"ok": False, "erreur": "l'intervention est d'activité « %s » : "
                    "le type de travail n'existe pas pour cette activité. "
                    "C'est le DOMAINE du DIT qui est à corriger." % activite,
                    "activite": activite, "etapes": etapes}
        return {"ok": False, "erreur": "le combo du type de travail est vide : "
                "l'intervention n'est pas modifiable (annulée ou clôturée ?)",
                "etapes": etapes}

    avant = _rest(numero_dit)
    # Les champs en plus sont-ils DÉJÀ à la valeur voulue ? Sinon il reste du
    # travail, même quand le type de travail est bon.
    reste = [n2 for n2, v2 in remplacements.items()
             if ([v3 for n3, v3 in paires if n3 == n2] or [None])[0] != v2]
    if (actuel == valeur and (avant or {}).get("type_travail") == valeur
            and not reste and not forcer):
        # `forcer` sert au test de bout en bout : réécrire la même valeur
        # prouve la chaîne d'écriture sans rien modifier.
        _abandonner(w, page, note)
        return {"ok": True, "inchange": True, "avant": avant, "apres": avant,
                "etapes": etapes}

    if not confirmer:
        _abandonner(w, page, note)
        note("ARRÊT", "mode constat : rien n'a été écrit")
        return {"ok": True, "simule": True, "champ": champ, "actuel": actuel,
                "voulu": valeur, "avant": avant, "etapes": etapes}

    # 5. flush puis save, comme le fait le bouton Enregistrer.
    #    `componentName` n'est PAS un champ du formulaire : la fonction
    #    `ajaxFormParams` du bundle l'ajoute à la main, avec cKey/cStatus/
    #    session. Sans lui, flush et save renvoient la page « Erreur — ouvrir
    #    une nouvelle session », alors que tout le reste de la session marche.
    a_poser = {champ: valeur, "componentName": "ccIt",
               "cKey": w.p["cKey"], "cStatus": w.p["cStatus"],
               "session": w.p["session"]}
    a_poser.update(remplacements)
    corps = _encoder(paires, a_poser, charset=AJAX)
    entetes = {"Referer": w._ref("ccIt"), "X-Requested-With": "XMLHttpRequest",
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    w.s.post(BASE + "/shared/form/flush.action", data=corps, timeout=90,
             headers=entetes)
    r = w.s.post(BASE + "/shared/form/save.action", data=corps, timeout=90,
                 headers=entetes)
    try:
        code = (r.json() or {}).get("returnCode")
    except ValueError:
        code = "réponse non JSON (HTTP %s)" % r.status_code
    note("enregistrement", str(code))

    # 6. Quitter le mode saisie dans tous les cas.
    if code == "Enregistrer_OK":
        try:
            w.soumettre("ccIt", page, "oK_Enregistrer")
            note("sortie", "oK_Enregistrer")
        except Exception as e:
            note("sortie", "échec : %s" % str(e)[:80])
    else:
        _abandonner(w, page, note)

    apres = _rest(numero_dit)
    bouge = sorted(k for k in set(avant or {}) | set(apres or {})
                   if (avant or {}).get(k) != (apres or {}).get(k))
    ok = code == "Enregistrer_OK" and (apres or {}).get("type_travail") == valeur
    # Le type de facturation se relit « NonFacturable » quand le formulaire a
    # ecrit « Non facturable » : on compare les valeurs REST, pas les libelles.
    attendus = {}
    for court, val in aussi.items():
        if court == "cb_typeFactu":
            attendus["facturation"] = FACTURATIONS[val]
    for cle, att in attendus.items():
        if (apres or {}).get(cle) != att:
            ok = False
    return {"ok": ok, "returnCode": code, "avant": avant, "apres": apres,
            "champs_modifies": bouge, "attendus": attendus,
            "collateral": [x for x in bouge
                           if x != "type_travail" and x not in attendus],
            "etapes": etapes}


CHAMP_COMMENTAIRE = "ita_ditProbleme"


def poser_commentaire(numero_dit, texte, confirmer=False, web=None, trace=None):
    """Réécrit le commentaire (« problème ») d'une DIT.

    Aucune route REST ne le permet : `POST /dit` l'écrit à la création, et
    plus rien ensuite. L'écran `ccDit` en modification, lui, expose le champ
    `ita_ditProbleme`, et le bouton Enregistrer passe par le même couple
    flush/save que le type de travail.

    Sert quand un client complète sa demande par un second mail : le ticket
    existe déjà, il ne faut pas en créer un autre mais compléter celui-là
    (demandé par Elliot le 2026-09-14 sur la DIT 100173837).

    Sans `confirmer`, rend le constat sans rien écrire.
    """
    dire = trace or (lambda *_a: None)
    texte = (texte or "").strip()
    if not texte:
        return {"ok": False, "erreur": "commentaire vide"}
    # LE DRAPEAU D'ECRITURE VAUT AUSSI ICI. Ce module ne passe pas par
    # `mail_dit_ecriture._autorise` : `confirmer=True` suffisait donc a ecrire
    # dans Artis alors que /data disait NON. Le 2026-10-15, la routine Cloud a
    # ainsi commente la DIT 100177620 sous veto, puis s'est arretee au
    # rangement -- laissant un dossier a moitie fait. On verifie AVANT
    # d'ouvrir la session web : une connexion coute un siege de licence.
    if confirmer:
        try:
            import mail_dit_ecriture as _E   # differe : cycle a l'import sinon
            if not _E.ecriture_armee():
                return {"ok": False, "arme": False, "erreur":
                        "ecriture non armee : le drapeau mail_dit_ecriture.txt "
                        "ne dit pas OUI"}
        except ImportError:                  # module seul, hors chaine
            pass
    w = web or Web.ouvrir(trace=trace)
    etapes = []

    def note(quoi, detail=""):
        etapes.append(quoi + ((" — " + detail) if detail else ""))
        dire("   %s %s" % (quoi, detail))

    def accueil():
        return w.s.get("%s/composants/commun/accueil/entreeVisualiser.action?%s"
                       % (BASE, urlencode(w.p)), timeout=60).text

    depart = accueil()
    if 'id="mainForm"' not in depart:
        liberer(w, note)
        depart = accueil()
    if 'id="mainForm"' not in depart:
        return {"ok": False, "erreur": "impossible de repartir de l'accueil "
                "Artis (session bloquée ou expirée)", "etapes": etapes}

    docs = ((w.solr(numero_dit).json().get("response") or {}).get("docs") or [])
    docs = [d for d in docs if str(d.get("code", "")).startswith(numero_dit)]
    if not docs:
        return {"ok": False, "erreur": "DIT %s introuvable dans l'index"
                % numero_dit, "etapes": etapes}
    id_dit = docs[0].get("data_long_id_dit")
    note("SOLR", "id_dit=%s" % id_dit)

    page = w.aller_a(depart, "ccDit", "services", "modif", str(id_dit)).text
    fil = _fil(page)
    if "(M)" not in fil or numero_dit not in fil:
        _sortir(w, page, note, "ccDit")
        return {"ok": False, "erreur": "la DIT ne s'ouvre pas en modification "
                "(clôturée ou annulée ?) — écran : %s" % (fil or "inconnu"),
                "etapes": etapes}

    paires = champs_du_form(page)
    avant = ([v for n, v in paires if n == CHAMP_COMMENTAIRE] or [None])[0]
    if avant is None:
        _sortir(w, page, note, "ccDit")
        return {"ok": False, "erreur": "%s absent de l'écran" % CHAMP_COMMENTAIRE,
                "etapes": etapes}
    note("commentaire actuel", "%d caractères" % len(avant))

    if not confirmer:
        _sortir(w, page, note, "ccDit")
        note("ARRÊT", "mode constat : rien n'a été écrit")
        return {"ok": True, "simule": True, "avant": avant, "voulu": texte,
                "etapes": etapes}

    corps = _encoder(paires, {CHAMP_COMMENTAIRE: texte, "componentName": "ccDit",
                              "cKey": w.p["cKey"], "cStatus": w.p["cStatus"],
                              "session": w.p["session"]}, charset=AJAX)
    entetes = {"Referer": w._ref("ccDit"), "X-Requested-With": "XMLHttpRequest",
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    w.s.post(BASE + "/shared/form/flush.action", data=corps, timeout=90,
             headers=entetes)
    r = w.s.post(BASE + "/shared/form/save.action", data=corps, timeout=90,
                 headers=entetes)
    try:
        code = (r.json() or {}).get("returnCode")
    except ValueError:
        code = "réponse non JSON (HTTP %s)" % r.status_code
    note("enregistrement", str(code))

    if code == "Enregistrer_OK":
        try:
            w.soumettre("ccDit", page, "oK_Enregistrer")
            note("sortie", "oK_Enregistrer")
        except Exception as e:
            note("sortie", "échec : %s" % str(e)[:80])
    else:
        _sortir(w, page, note, "ccDit")

    apres = (_rest(numero_dit) or {}).get("commentaire")
    return {"ok": code == "Enregistrer_OK", "returnCode": code,
            "avant": avant, "apres": apres, "etapes": etapes}


CHAMP_TAGS = "dt_tagsIt"

# Les Tags d'une DIT ne sont PAS dans l'API : ni dans `GET /dit/{n}`, ni dans
# le corps de `POST /dit`, et aucune route `/tag` n'existe (swagger v2 verifie
# le 2026-10-06). Ils ne vivent que sur l'ecran `ccDit`, dans un `<select
# multiple name="dt_tagsIt">` double d'un `dt_tagsIt_multi=true` cache.
#
# Deux valeurs servent au circuit de validation client, demande par Elliot le
# 2026-10-06 sur la DIT 100176480 (acces SAGE PAIE chez MYMOBILITY, ou la
# fiche client impose une validation par M. DUCLAUX ou M. JIMENEZ) :
#   VALIDATION         -> « En attente de validation client »  (a la creation)
#   VALIDATION CLIENT  -> « Demande validee »                  (a la reponse)
TAG_ATTENTE_VALIDATION = "VALIDATION"
TAG_DEMANDE_VALIDEE = "VALIDATION CLIENT"


def _tags_du_form(page):
    """Tous les tags coches du multi-select — `champs_du_form` n'en rend qu'un.

    La lecture generique prend le PREMIER `selected` de chaque `<select>` ;
    sur un `multiple` elle perdrait donc les autres tags deja poses. Ici on
    les releve tous, pour ne jamais en effacer un par inadvertance.
    """
    m = re.search(r'<select[^>]*name="%s".*?</select>' % CHAMP_TAGS, page, re.S)
    if not m:
        return None
    return [o.group(1) for o in
            re.finditer(r'<option[^>]*\bselected\b[^>]*>', m.group(0))
            for o in [re.search(r'value="([^"]*)"', o.group(0))] if o]


def poser_tag(numero_dit, ajouter=None, retirer=None, confirmer=False,
              web=None, trace=None):
    """Pose et retire des Tags sur une DIT, sans toucher aux autres.

    `ajouter` et `retirer` prennent un code ou une liste de codes — ceux du
    `<option value=...>`, pas les libelles. Les tags deja presents et non
    cites sont conserves.

    Sans `confirmer`, rend l'etat actuel sans rien ecrire.
    """
    dire = trace or (lambda *_a: None)
    def _liste(x):
        if not x:
            return []
        return [x] if isinstance(x, str) else list(x)
    ajouter, retirer = _liste(ajouter), _liste(retirer)
    if not ajouter and not retirer:
        return {"ok": False, "erreur": "ni ajout ni retrait demande"}

    w = web or Web.ouvrir(trace=trace)
    etapes = []

    def note(quoi, detail=""):
        etapes.append(quoi + ((" — " + detail) if detail else ""))
        dire("   %s %s" % (quoi, detail))

    def accueil():
        return w.s.get("%s/composants/commun/accueil/entreeVisualiser.action?%s"
                       % (BASE, urlencode(w.p)), timeout=60).text

    depart = accueil()
    if 'id="mainForm"' not in depart:
        liberer(w, note)
        depart = accueil()
    if 'id="mainForm"' not in depart:
        return {"ok": False, "erreur": "impossible de repartir de l'accueil "
                "Artis (session bloquee ou expiree)", "etapes": etapes}

    docs = ((w.solr(numero_dit).json().get("response") or {}).get("docs") or [])
    docs = [d for d in docs if str(d.get("code", "")).startswith(numero_dit)]
    if not docs:
        return {"ok": False, "erreur": "DIT %s introuvable dans l'index"
                % numero_dit, "etapes": etapes}
    id_dit = docs[0].get("data_long_id_dit")
    note("SOLR", "id_dit=%s" % id_dit)

    page = w.aller_a(depart, "ccDit", "services", "modif", str(id_dit)).text
    fil = _fil(page)
    if "(M)" not in fil or numero_dit not in fil:
        _sortir(w, page, note, "ccDit")
        return {"ok": False, "erreur": "la DIT ne s'ouvre pas en modification "
                "(cloturee ou annulee ?) — ecran : %s" % (fil or "inconnu"),
                "etapes": etapes}

    avant = _tags_du_form(page)
    if avant is None:
        _sortir(w, page, note, "ccDit")
        return {"ok": False, "erreur": "%s absent de l'ecran" % CHAMP_TAGS,
                "etapes": etapes}
    note("tags actuels", ", ".join(avant) or "(aucun)")

    voulu = [t for t in avant if t not in retirer]
    voulu += [t for t in ajouter if t not in voulu]
    if voulu == avant:
        _sortir(w, page, note, "ccDit")
        return {"ok": True, "inchange": True, "avant": avant, "etapes": etapes}

    if not confirmer:
        _sortir(w, page, note, "ccDit")
        note("ARRET", "mode constat : rien n'a ete ecrit")
        return {"ok": True, "simule": True, "avant": avant, "voulu": voulu,
                "etapes": etapes}

    # Un multi-select se poste en paires repetees : on retire la paire que la
    # lecture generique a produite, et on remet une paire par tag voulu.
    paires = [(n, v) for n, v in champs_du_form(page) if n != CHAMP_TAGS]
    paires += [(CHAMP_TAGS, t) for t in voulu]
    corps = _encoder(paires, {"componentName": "ccDit", "cKey": w.p["cKey"],
                              "cStatus": w.p["cStatus"], "session": w.p["session"]},
                     charset=AJAX)
    entetes = {"Referer": w._ref("ccDit"), "X-Requested-With": "XMLHttpRequest",
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}
    w.s.post(BASE + "/shared/form/flush.action", data=corps, timeout=90,
             headers=entetes)
    r = w.s.post(BASE + "/shared/form/save.action", data=corps, timeout=90,
                 headers=entetes)
    try:
        code = (r.json() or {}).get("returnCode")
    except ValueError:
        code = "reponse non JSON (HTTP %s)" % r.status_code
    note("enregistrement", str(code))

    if code == "Enregistrer_OK":
        try:
            w.soumettre("ccDit", page, "oK_Enregistrer")
            note("sortie", "oK_Enregistrer")
        except Exception as e:
            note("sortie", "echec : %s" % str(e)[:80])
    else:
        _sortir(w, page, note, "ccDit")

    return {"ok": code == "Enregistrer_OK", "returnCode": code,
            "avant": avant, "voulu": voulu, "etapes": etapes}


def _sortir(w, page, note, composant="ccIt"):
    try:
        w.soumettre(composant, page, "evtSortieChemin")
        note("abandon", "mode saisie quitté")
    except Exception as e:
        note("abandon", "échec : %s" % str(e)[:80])


def _abandonner(w, page, note):
    """Ne jamais laisser la session bloquée en mode saisie."""
    try:
        w.soumettre("ccIt", page, "evtSortieChemin")
        note("abandon", "mode saisie quitté")
    except Exception as e:
        note("abandon", "échec : %s" % str(e)[:80])


def _fil(page):
    """Le fil d'Ariane, où Artis marque le mode de chaque niveau : (V) ou (M)."""
    return " / ".join(
        x.strip() for x in
        re.findall(r"breadcrumb-item[^>]*>(?:<a[^>]*>)?(.*?)<", page)
        if x.strip())


def _rest(numero):
    """Relecture par l'API REST : la seule preuve de ce qui a été écrit."""
    try:
        import mail_dit_ecriture as E
        return E.relire_dit(numero)
    except Exception as e:
        return {"erreur": str(e)[:120]}


# ── Confirmation de réception au client ─────────────────────────────────────
# Artis sait envoyer lui-même l'accusé de réception d'une DIT : c'est le bouton
# enveloppe de la visu (`an_Email`), qui appelle
#   $.mail.echange.create({classBo, idBo, isMessageEntrant:false,
#                          typeMessage:"Confirmation réception"})
# → `shared/mails/show.action` rend une modale DÉJÀ remplie (destinataire =
# le demandeur, objet `[DIT n] Confirmation de réception`, corps reprenant le
# bien, le site, le demandeur et le texte de la demande), et le bouton vert
# poste le formulaire sur `shared/mails/send.action`.
#
# Rien de tout cela n'existe dans l'API REST : `POST /dit` n'a aucun champ de
# confirmation (swagger v2 vérifié le 2026-10-13). On rejoue donc la modale.
#
# Deux pièges :
#   - le corps n'est pas un `<textarea>` mais un `<div class="editor-artis">`,
#     que TinyMCE double d'un input caché portant le même nom — il faut donc
#     le relever à la main (`_div_contenu`) ;
#   - `it_mailTo` est un multi-select : la lecture générique n'en garderait
#     qu'une valeur.
CLASS_BO_DIT = "com.artis.business.services.DmdeInter"
TYPE_CONFIRMATION = "Confirmation réception"

# Le bouton de la visu ouvre la modale sur « Confirmation réception », qui part
# de `service.client@dfm.fr`. Elliot, le 2026-10-13, après l'essai : « ça a
# envoyé depuis service.client@dfm.fr, il faut absolument que ce soit
# support@dfm.fr ». La liste `cbo_mailFrom` ne propose pas support ; c'est le
# TYPE d'échange qui porte l'adresse — DFM en a un fait pour ça,
# « 'Confirmation de réception' avec mail SUPPORT », qui rend en prime l'objet
# au format maison (« DFM - Informatique - Demande d'intervention N - … »),
# celui auquel les clients répondent déjà.
#
# Ce type ne s'obtient pas par `echangeData` (le serveur n'y reconnaît que ses
# libellés internes, tout autre valeur retombe sur « Sollicitation ») : il se
# choisit dans le select, comme le ferait un humain, puis
# `mails/changeTypeEchange.action` rend l'objet et le corps correspondants.
TYPE_CONFIRMATION_SUPPORT = "84895d14-5b56-4869-9a80-3f4fe38adc80"

# L'adresse d'envoi ne vient NI du type d'échange NI d'un réglage du mail :
# `cbo_mailFrom` liste deux boîtes communes (factures-fournisseurs,
# service.client) plus **celle du compte Artis connecté**. Sous le compte
# d'Elliot la troisième ligne est « Elliot COHEN (support@dfm.fr) » ; sous le
# compte MCO, qui porte nos sessions, c'est « Service MCO (ecohen@dfm.fr) ».
# D'où les essais du 2026-10-13, partis de service.client@dfm.fr malgré le
# type « avec mail SUPPORT » : « service.client@dfm.fr, il est encore arrivé
# avec ça ». Tant que l'adresse du compte MCO n'est pas `support@dfm.fr` dans
# Artis, aucune confirmation ne doit partir — un client qui répond à
# service.client n'atterrit pas dans la BAL support.
EXPEDITEUR_ATTENDU = "support@dfm.fr"


def _div_contenu(seg, nom):
    """Contenu d'un `<div name="...">`, balises comprises, divs imbriquées OK."""
    m = re.search(r'<div\b[^>]*(?<![-\w])name="%s"[^>]*>' % re.escape(nom), seg)
    if not m:
        return None
    i = m.end()
    prof = 1
    for t in re.finditer(r"<(/?)div\b", seg[i:]):
        prof += -1 if t.group(1) else 1
        if prof == 0:
            return seg[i:i + t.start()]
    return None


def _champs_modale(page):
    """Ce que le navigateur posterait depuis `#sendMailModal-form`."""
    i = page.find('id="sendMailModal-form"')
    if i < 0:
        raise ValueError("formulaire de la modale absent de la reponse")
    seg = page[i:]
    out = []
    for m in re.finditer(r"<input\b([^>]*)>", seg):
        a = m.group(1)
        nom = re.search(r'(?<![-\w])name="([^"]*)"', a)
        if not nom:
            continue
        typ = (re.search(r'type="([^"]*)"', a) or [None, "text"])[1]
        if typ in ("submit", "button", "file"):
            continue
        if typ in ("checkbox", "radio") and "checked" not in a:
            continue
        val = re.search(r'value="([^"]*)"', a)
        out.append((html.unescape(nom.group(1)),
                    html.unescape(val.group(1)) if val else ""))
    for m in re.finditer(r"<select\b([^>]*)>(.*?)</select>", seg, re.S):
        nom = re.search(r'(?<![-\w])name="([^"]*)"', m.group(1))
        if not nom:
            continue
        for o in re.finditer(r"<option\b([^>]*)>", m.group(2)):
            if "selected" not in o.group(1):
                continue
            v = re.search(r'value="([^"]*)"', o.group(1))
            out.append((html.unescape(nom.group(1)),
                        html.unescape(v.group(1)) if v else ""))
    corps = _div_contenu(seg, "ita_mailContent")
    if corps is not None:
        out.append(("ita_mailContent", corps))
    return out


def _options(page, champ):
    """Les libellés des `<option>` d'un select, pour pouvoir dire ce qu'il y a."""
    m = re.search(r'<select\b[^>]*(?<![-\w])name="%s".*?</select>' % champ,
                  page, re.S)
    if not m:
        return []
    return [html.unescape(re.sub(r"\s+", " ", o).strip())
            for o in re.findall(r"<option[^>]*>(.*?)</option>", m.group(0), re.S)
            if o.strip()]


def _option_contenant(page, champ, texte):
    """La `value` de la première option dont le libellé contient `texte`."""
    m = re.search(r'<select\b[^>]*(?<![-\w])name="%s".*?</select>' % champ,
                  page, re.S)
    if not m:
        return None
    for o in re.finditer(r"<option([^>]*)>(.*?)</option>", m.group(0), re.S):
        if texte.lower() in html.unescape(o.group(2)).lower():
            v = re.search(r'value="([^"]*)"', o.group(1))
            return html.unescape(v.group(1)) if v else None
    return None


def _valeur(paires, nom):
    return next((v for n, v in paires if n == nom), None)


def _libelle_option(page, champ, valeur):
    m = re.search(r'<select\b[^>]*(?<![-\w])name="%s".*?</select>' % champ,
                  page, re.S)
    if not m:
        return ""
    o = re.search(r'<option[^>]*value="%s"[^>]*>(.*?)</option>'
                  % re.escape(valeur), m.group(0), re.S)
    return html.unescape(re.sub(r"\s+", " ", o.group(1)).strip()) if o else ""


def envoyer_confirmation_reception(numero_dit, confirmer=False, web=None,
                                   trace=None, destinataire=None,
                                   type_echange=TYPE_CONFIRMATION_SUPPORT,
                                   expediteur=EXPEDITEUR_ATTENDU,
                                   destinataire_si_absent=None):
    """Envoie au demandeur l'accusé de réception d'une DIT, comme la visu.

    Sans `confirmer`, rend l'aperçu de ce qui partirait (expéditeur, à, objet,
    corps) sans rien envoyer. `destinataire` force l'adresse — le select
    accepte la saisie libre (`data-tags="true"`), ce qui permet de s'envoyer
    un essai. `type_echange` est la valeur d'une `<option>` de `cbo_typeEchange` : par
    défaut le type « avec mail SUPPORT », pour que le mail parte de
    `support@dfm.fr` et non de `service.client@dfm.fr`. `None` gardent celui du
    bouton de la visu. `destinataire_si_absent` ne sert que si la modale ne
    présélectionne personne — voir plus bas. `expediteur` est l'adresse exigée au départ : elle est
    cherchée dans `cbo_mailFrom`, et si elle n'y est pas rien ne part.
    """
    dire = trace or (lambda *_a: None)
    w = web or Web.ouvrir(trace=trace)
    etapes = []

    def note(quoi, detail=""):
        etapes.append(quoi + ((" — " + detail) if detail else ""))
        dire("   %s %s" % (quoi, detail))

    def accueil():
        return w.s.get("%s/composants/commun/accueil/entreeVisualiser.action?%s"
                       % (BASE, urlencode(w.p)), timeout=60).text

    depart = accueil()
    if 'id="mainForm"' not in depart:
        liberer(w, note)
        depart = accueil()
    if 'id="mainForm"' not in depart:
        return {"ok": False, "erreur": "impossible de repartir de l'accueil "
                "Artis (session bloquee ou expiree)", "etapes": etapes}

    docs = ((w.solr(numero_dit).json().get("response") or {}).get("docs") or [])
    docs = [d for d in docs if str(d.get("code", "")).startswith(numero_dit)]
    if not docs:
        return {"ok": False, "erreur": "DIT %s introuvable dans l'index"
                % numero_dit, "etapes": etapes}
    id_dit = docs[0].get("data_long_id_dit")
    note("SOLR", "id_dit=%s" % id_dit)

    # La visu pose le contexte du composant ; elle ne verrouille rien.
    w.aller_a(depart, "ccDit", "services", "visu", str(id_dit))

    ed = {"classBo": CLASS_BO_DIT, "idBo": int(id_dit),
          "isMessageEntrant": False, "typeMessage": TYPE_CONFIRMATION,
          "messageEntrant": False}
    r = w.get("shared/mails/show.action", composant="ccDit",
              echangeData=json.dumps(ed, ensure_ascii=False))
    r.encoding = r.encoding or "ISO-8859-15"
    page = r.text
    if 'id="sendMailModal-form"' not in page:
        return {"ok": False, "erreur": "la modale mail ne s'ouvre pas "
                "(HTTP %s, %d octets)" % (r.status_code, len(r.content)),
                "etapes": etapes}
    note("modale", "%d octets" % len(r.content))

    paires = _champs_modale(page)
    if type_echange and type_echange != _valeur(paires, "cbo_typeEchange"):
        paires = [(n, v) for n, v in paires if n != "cbo_typeEchange"]
        paires.append(("cbo_typeEchange", type_echange))
        chg = w.s.post(
            BASE + "/shared/mails/changeTypeEchange.action",
            data=_encoder(paires, {"cKey": w.p["cKey"], "cStatus": w.p["cStatus"],
                                   "session": w.p["session"],
                                   "componentName": "ccDit"}, charset=AJAX),
            timeout=60,
            headers={"Referer": w._ref("ccDit"), "X-Requested-With": "XMLHttpRequest",
                     "Content-Type": "application/x-www-form-urlencoded; "
                     "charset=UTF-8"})
        try:
            d = chg.json() or {}
        except ValueError:
            d = {}
        if not (d.get("valContent") or "").strip():
            return {"ok": False, "erreur": "le type d'echange %s ne rend aucun "
                    "modele (HTTP %s)" % (type_echange, chg.status_code),
                    "etapes": etapes}
        remplace = {"it_mailObjet": d.get("valSubject") or "",
                    "ita_mailContent": d.get("valContent") or ""}
        if d.get("valSignature"):
            remplace["cbo_signature"] = d["valSignature"]
        paires = [(n, remplace.get(n, v)) for n, v in paires]
        note("type d'echange", _libelle_option(page, "cbo_typeEchange",
                                               type_echange) or type_echange)
    if expediteur:
        opt = _option_contenant(page, "cbo_mailFrom", expediteur)
        if opt is None:
            return {"ok": False, "erreur": "le compte Artis de cette session ne "
                    "peut pas ecrire depuis %s : la liste « De » ne propose que "
                    "%s. A regler dans Artis (adresse du compte), pas ici."
                    % (expediteur, ", ".join(_options(page, "cbo_mailFrom"))),
                    "etapes": etapes}
        paires = [(n, v) for n, v in paires if n != "cbo_mailFrom"]
        paires.append(("cbo_mailFrom", opt))
    if destinataire:
        paires = [(n, v) for n, v in paires if n != "it_mailTo"]
        paires.append(("it_mailTo", destinataire))
    index = {}
    for n, v in paires:
        index.setdefault(n, []).append(v)

    # `it_mailTo` est alimenté par un index des interlocuteurs qu'Artis ne
    # rafraîchit pas tout de suite : un contact créé dans la minute n'y est pas
    # encore, et la modale s'ouvre sans destinataire alors que la DIT a bien
    # son demandeur (le corps le nomme : « Bonjour M. Jules BRONNER »).
    # Constaté le 2026-10-13 sur les DIT 100177429 et 100177434, dont les
    # contacts venaient d'être créés. Le select accepte la saisie libre
    # (`data-tags="true"`), donc on retombe sur l'adresse du mail d'origine —
    # celle de la personne qui a écrit, c'est-à-dire exactement celle qui
    # attend l'accusé de réception.
    if destinataire_si_absent and not [x for x in (index.get("it_mailTo") or [])
                                       if x.strip()]:
        paires.append(("it_mailTo", destinataire_si_absent))
        index.setdefault("it_mailTo", []).append(destinataire_si_absent)
        note("destinataire de repli", destinataire_si_absent)
    a = [x for x in (index.get("it_mailTo") or []) if x.strip()]
    objet = (index.get("it_mailObjet") or [""])[0]
    corps = (index.get("ita_mailContent") or [""])[0]
    de = (index.get("cbo_mailFrom") or [""])[0]
    apercu = {"de": _libelle_option(page, "cbo_mailFrom", de) or de,
              "a": [x if "@" in x else (_libelle_option(page, "it_mailTo", x) or x)
                    for x in a],
              "type": _libelle_option(page, "cbo_typeEchange",
                                      (index.get("cbo_typeEchange") or [""])[0])
              or (index.get("cbo_typeEchange") or [""])[0],
              "objet": objet,
              "corps": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", corps)).strip()}
    note("destinataire", " ; ".join(apercu["a"]) or "(aucun)")
    note("objet", objet)

    if not a:
        return {"ok": False, "erreur": "aucun destinataire : la DIT n'a pas de "
                "demandeur avec une adresse mail", "apercu": apercu,
                "etapes": etapes}
    if not corps.strip():
        return {"ok": False, "erreur": "corps vide : Artis n'a pas rendu le "
                "modele de confirmation", "apercu": apercu, "etapes": etapes}
    if not confirmer:
        note("ARRET", "mode constat : aucun mail n'est parti")
        return {"ok": True, "simule": True, "apercu": apercu, "etapes": etapes}

    corps_post = _encoder(paires, {"cKey": w.p["cKey"], "cStatus": w.p["cStatus"],
                                   "session": w.p["session"],
                                   "componentName": "ccDit"}, charset=AJAX)
    rep = w.s.post(BASE + "/shared/mails/send.action", data=corps_post,
                   timeout=120,
                   headers={"Referer": w._ref("ccDit"),
                            "X-Requested-With": "XMLHttpRequest",
                            "Content-Type": "application/x-www-form-urlencoded; "
                            "charset=UTF-8"})
    try:
        rj = rep.json() or {}
    except ValueError:
        rj = {"brut": rep.text[:200]}
    envoye = rep.status_code == 200 and "brut" not in rj and not rj.get("error")
    note("envoi", "HTTP %s %s" % (rep.status_code,
                                  json.dumps(rj, ensure_ascii=False)[:120]))
    return {"ok": envoye, "apercu": apercu, "reponse": rj,
            "http": rep.status_code, "etapes": etapes}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    num = args[0]
    val = args[1] if len(args) > 1 else "Hotline"
    res = poser_type_travail(num, val, confirmer="--go" in sys.argv,
                             trace=lambda s: print(s))
    print()
    print(json.dumps({k: v for k, v in res.items() if k != "etapes"},
                     ensure_ascii=False, indent=1))
    if res.get("collateral"):
        print("\n⚠ AUTRES CHAMPS MODIFIÉS : %s" % res["collateral"])
