# Me transférer sur la VM — document de reprise

Écrit le **2026-10-15** pour qu'un Claude Code installé sur une VM reprenne le
travail **immédiatement**, sans rien redemander à Elliot.

Lis-le en entier avant d'agir. Il dit ce que je sais, ce que j'ai le droit de
faire, où sont les accès, et ce qui est en cours.

---

## 1. Qui est Elliot, et comment on travaille

**Elliot Cohen** (`ecohen@dfm.fr`) — owner produit du MSP Dashboard DFM.
**Benjamin Ozon** (`bozon@dfm.fr`) — DSI, pour les escalades Azure / RBAC.

Trois choses à savoir avant la première réponse :

- **Il a déjà tranché beaucoup de choses.** Ne redemande pas une autorisation
  qu'il a donnée : elle est dans la mémoire. Une question dont la réponse y
  figure lui fait perdre son temps, et il le dit.
- **Il veut du travail fini, pas des options.** « À faire à la main » ou « à
  décider par Elliot » sur un sujet déjà tranché, c'est un échec.
- **Il corrige franchement, et il a presque toujours raison.** Vérifier avant
  de le contredire — c'est l'objet du skill
  `arrete-de-croire-que-t-es-le-main-character`.

---

## 2. Installer la VM

```bash
python3 --version     # 3.12 (testé en 3.12.10)
git --version         # 2.55
az version            # 2.90

pip install -r requirements.txt    # `requests` suffit à la chaîne mail
```

Puis **Claude Code**, connecté au compte Anthropic d'Elliot.

---

## 3. Les trois choses à transférer

Le dépôt ne suffit pas : il manque la mémoire et les secrets, qui vivent hors
git. C'est exactement ce qui a fait échouer la routine Cloud toute la journée
du 15/10.

### a. Le dépôt

```bash
git clone https://github.com/ecohen-collab/MspDFM.git
```

Il porte le code, les 6 skills, et une copie consultable de la mémoire.

### b. La mémoire — le point le plus important

Je ne redemande rien à Elliot parce que je relis **97 fiches** à chaque
session. Elles vivent **hors du dépôt** :

```
~/.claude/projects/<slug>/memory/
```

Copier ce dossier entier depuis le poste d'Elliot.

> **⚠ Le `<slug>` est dérivé du CHEMIN du projet.** Sur son poste,
> `C:\Users\ecohen\OneDrive - DFM\Bureau\Dashboard IA` donne
> `C--Users-ecohen-OneDrive---DFM-Bureau-Dashboard-IA`. Si le projet est
> ailleurs sur la VM, **le slug change et la mémoire n'est pas trouvée**.
> Vérifier le nom réel du dossier que Claude Code crée, et y déposer les
> fiches. Rien ne signale une mémoire absente : l'échec est silencieux, et il
> se manifeste seulement par un Claude qui redemande tout.

Une copie est aussi dans le dépôt (`.claude/skills/mail-dit/memoire/`,
49 fiches), mais elle est **consultable, pas chargée** : elle dépanne, elle ne
remplace pas la vraie. `exporter_memoire.py` la régénère.

### c. Les secrets, hors git

| Fichier | Contenu |
|---|---|
| `.env` | `ARTIS_AUTH_URL`, `ARTIS_USERNAME`, `ARTIS_PASSWORD`, `ARTIS_BASE_URL` |
| `data/artis_mco.json` | identifiants du compte web MCO |
| `data/mail_tokens.json` | jeton Graph — sinon il se lit par le relais |
| `mail_dit_journal.jsonl` | journal local (~1,1 Mo) |

`.gitignore` ne couvre pas tout : **ne jamais faire `git add .`** dans ce dépôt
(il contient `yooz_refresh.txt` et des PDF clients). Publier avec
`python publier.py "<message>" <fichiers>`, qui committe fichier par fichier.

Enfin : `az login --use-device-code`, avec le compte d'Elliot.

---

## 4. Vérifier que tout répond

```bash
python azure_files.py                                               # stockage par relais : OK
python -c "import mail_auth; print(bool(mail_auth.access_token()))" # True
python .claude/skills/mail-dit/bin/artis.py client 9599             # une fiche client
python finir_passage.py --retard                                    # rien à rattraper
python publier.py --verifier                                        # rien en attente
ls ~/.claude/projects/*/memory/*.md | wc -l                         # ~97
```

Les six doivent passer. Le dernier est celui qu'on oublie, et c'est le plus
important.

---

## 5. L'infrastructure

**Azure**, abonnement `a619cbcf-13e3-4d19-ac1e-5a3ce6cc7886`, resource group
`GRP-RES-CONTENEURS` :

| Ressource | Nom |
|---|---|
| Container App | `msp-dashboard` |
| ACR | `dfmplateformeprocess` |
| Storage | `dfmmspdashboardstor`, share `msp-data` monté sur `/data` |

Déploiement : `.\deploy.ps1` — **jamais `-Commit`**. Détail dans le skill
`deployer` et dans `msp-dashboard-elliot-runbook.md`.

**Le relais** permet aux sessions sans `/data` monté d'atteindre les fichiers
persistants :

```
https://msp-dashboard.gentlesand-80781e11.francecentral.azurecontainerapps.io/api/relais/<fichier>
```

Gardé par le secret `RELAIS_CLE`, **quatre** fichiers en liste blanche :
`mail_tokens.json`, `mail_dit_journal.jsonl`, `mail_dit_ecriture.txt`,
`artis_mco_session.json`. Chaque chemin est exclu d'Easy Auth **un par un**
(jamais `/api/relais/*`). Un **401 JSON** est normal — la route attend la clé.
Un **401 vide** veut dire qu'Entra bloque : le chemin n'est pas exclu.

Depuis la VM, si `/data` n'est pas monté, poser `RELAIS_URL` et `RELAIS_CLE` en
variables d'environnement suffit : `azure_files`, `mail_auth`, `journal_sync` et
`artis_type_travail` basculent seuls.

---

## 6. État au 15/10/2026, 18h

- **Robot à règles du conteneur : ÉTEINT** (`MAIL_DIT_ROBOT=0`). Décision
  définitive d'Elliot — **ne jamais proposer de le rallumer**, pas même comme
  filet de nuit.
- **Agent du conteneur : éteint** (`AGENT_MAIL=0`).
- **Drapeau `/data/mail_dit_ecriture.txt` : `OUI`** — la chaîne écrit.
- **Routine Cloud** : abandonnée au profit de cette VM.
- **Aucun cron actif.**
- Journal `/data/mail_dit_journal.jsonl` : ~2 900 lignes.

**Conséquence : plus rien ne traite la boîte support tant que la VM ne tourne
pas.** C'est le premier travail à reprendre.

---

## 7. Faire un passage mail → DIT

Le skill `mail-dit` fait foi (`.claude/skills/mail-dit/SKILL.md`, ~70 Ko, plus
4 références). Sa section de tête donne les six commandes du passage.

```python
import json, agent_mail_dit as A, mail_dit_ecriture as E
import mail_dit_journal as J, mail_dit as M
ctx = A.Contexte(actif=True)
A.executer('boite', {}, ctx, M, E, J)      # puis lire_mail, creer_dit, …
```

Puis `python finir_passage.py --retard`, et `python journal_sync.py`.

**Tout passe par `A.executer(...)`** — 17 outils : `boite`, `lire_mail`, `fil`,
`resoudre_client`, `chercher_client`, `client`, `dits_client`, `dit`,
`symptomes`, `annuaire`, `creer_dit`, `completer_dit`, `ranger`, `transferer`,
`signaler`, `journaliser`, `rapport`.

Ne jamais utiliser les outils `outlook_*` d'un connecteur : quota Graph serré,
et ils laissent sans transfert ni journal. Ne jamais appeler
`mail_dit_ecriture` en direct non plus : ses fonctions attendent un `pid` de
proposition qui n'existe pas hors du robot à règles.

---

## 8. Les pièges déjà payés — tous constatés le 15/10

- **Session web Artis** : chaque connexion prend un siège de licence, et Artis
  n'a **pas** de déconnexion qui le rende (`disconnect.action` répond 200 et
  laisse la session vivante). Une session par passage, à la fin, jamais plus.
  Elle se réutilise via `/data/artis_mco_session.json`. Saturer les sièges
  bloque toute l'équipe DFM.
- **Le jeton Graph n'est pas sur le disque** d'une session distante : il se lit
  par le relais. Ne jamais conclure d'un `ls` qu'il manque — appeler
  `mail_auth.access_token()`.
- **`Mail.Send` est accordé.** Une docstring a longtemps dit le contraire et a
  coûté des passages entiers.
- **La skill `ecriture-vendeur`** impose un accord explicite pour toute écriture
  Artis. **La chaîne mail → DIT en est l'exception**, écrite noir sur blanc :
  l'accord est matérialisé par le drapeau, pas donné mail par mail.
- **Une DIT non finie ne se signale pas** : elle reste en
  `Terrain` / `Automatique` et part en facturation, où un dispatcher la
  planifie. D'où `finir_passage.py --retard`.
- **Rien ne se corrige après création** : ni le bien, ni le site, ni le
  demandeur, ni la priorité. Vérifier avant de créer.
- **`identifiantFabricant` est une clé globale**, pas une clé du client : un
  libellé nu met la DIT chez un autre client, avec son site.

---

## 9. Ce qui reste ouvert

1. **Les secrets collés en clair** dans la conversation du 15/10 (PAT GitHub,
   mot de passe Artis MCO, clé du relais, token Artis) — à faire tourner ou
   non : décision d'Elliot.
2. **Fréquence des passages** : la charge réelle est entre 6h et 19h, la nuit
   est vide (cron retenu : `7 6-19 * * *`).
3. **Taille de la consigne** : ~100 Ko, en hausse de 25 % le 15/10. Si elle
   paralyse, séparer les règles des récits — mais chaque récit vient d'une
   erreur réelle, et les retirer, c'est risquer de la refaire.
4. **LOGIVOLT / Pierre EYMARD** : entre-t-il dans la table des valideurs ?
5. **DIT 100177554** : UO à repasser en HLN3 à la main.

---

## 10. Les autres skills

| Skill | Quand s'en servir |
|---|---|
| `mail-dit` | la boîte support, les DIT Artis |
| `deployer` | déploiement, secrets, `/data`, rollback |
| `ecriture-vendeur` | avant tout POST vendeur — **sauf** mail → DIT |
| `sources-donnees` | ce que contiennent Artis, Datto, Sophos, VEEAM, TD SYNNEX |
| `widget-dashboard` | ajouter ou modifier un outil du dashboard |
| `arrete-de-croire-que-t-es-le-main-character` | vérifier avant de dire non |

Et `JOURNAL_DE_BORD.md` : l'état courant du chantier, à tenir à jour chaque
soir — c'est lui qui permet de repartir d'une conversation neuve le lendemain
sans rescanner le projet.

---

## 11. Premier geste sur la VM

1. les six vérifications du §4 ;
2. lire `JOURNAL_DE_BORD.md` ;
3. `python finir_passage.py --retard` — rattraper ce qui traîne ;
4. un passage `/mail-dit` : la boîte n'est plus traitée depuis ce matin ;
5. dire à Elliot, en trois lignes, ce qui a été fait et ce qui reste.
