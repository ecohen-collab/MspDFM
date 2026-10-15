# Journal de bord — chaîne mail → DIT

But : **repartir d'une conversation neuve chaque jour** sans rescanner le
projet. Le plus récent en haut. Une entrée par journée de travail, écrite en
fin de session.

Ce fichier ne remplace ni la mémoire (`memory/`, les faits durables), ni le
skill (`.claude/skills/mail-dit/`, les règles métier). Il porte ce qu'aucun
des deux ne garde : **l'état courant du chantier et ce qui attend une
décision**.

---

## 2026-10-15

### Où on en est

La chaîne `/mail-dit` tourne désormais sur une **routine Cloud de Claude**
(environnement « Mail DIT », Sonnet puis Haiku 5.5, horaire). Le réseau est
ouvert vers `artis.dfm.fr` et le relais. Elle crée, complète, range et remonte
le journal toute seule — démonstration faite à 11h43 (DIT 100177760) et 12h36
(DIT 100177773).

### Décidé aujourd'hui

- **Le robot à règles du conteneur est ÉTEINT, et il le reste** — décision
  définitive, pas un réglage de test (`MAIL_DIT_ROBOT=0`, révision 0000896),
  ainsi que l'agent (`AGENT_MAIL=0`). Elliot, le 2026-10-15 : « PAS DE PUTAIN
  DE ROBOT QUI TOURNE LA NUIT », après l'avoir déjà dit plusieurs fois le même
  jour. **Ne jamais reproposer de le rallumer**, ni comme filet, ni « juste la
  nuit », ni parce qu'il ne coûte que trois euros. La nuit, il ne se passe
  rien : la routine reprend à 6h.
- **Mon cron de 15 min est supprimé** — il tournait sur Opus, 96 passages/jour.
- Le drapeau `/data/mail_dit_ecriture.txt` est à **OUI**.

### Corrigé (10 commits, tous poussés)

| | |
|---|---|
| `3c824ec` | le veto du drapeau couvre enfin le commentaire Artis et le transfert |
| `293e90a` | le journal horodate en heure de Paris quelle que soit la machine |
| `e6575f2` | l'accusé de réception n'est pas un envoi interdit |
| `f7d25b8` | helpers `bin/*.py` portables + interrupteur du robot |
| `0dd4c64` | doctrine de la session web Artis |
| `085efad` | **règles de priorité et d'UO restées sur le poste pendant des jours** |
| `43a07ea` | `publier.py --verifier` : plus aucune règle ne dort sur le disque |
| `12cda67` | le jeton Graph ne se cherche pas sur le disque |

Outil créé : **`publier.py`** — une commande qui committe fichier par fichier,
pousse, et republie la consigne sur `/data`. `--verifier` dit ce qui traîne.

### En attente d'Elliot

1. **Poser `git pull --ff-only` en tête des Instructions de la routine.** Sans
   lui, elle reste sur le dépôt de son clone initial : les 10 commits ci-dessus
   ne la concernent pas. C'est le point bloquant.
2. Décider de la **fréquence** : 24 passages/jour coûtent ~528 k jetons rien
   qu'en consigne. Tranché : **passages de 6h à 19h, rien la nuit.**
3. Les secrets collés en clair dans la conversation du jour (PAT GitHub, mot de
   passe Artis MCO, clé du relais, token d'auth Artis) — à faire tourner ou non.

### Laissé en boîte par le passage bloqué de 14h35

Trois réponses clients **urgentes** : suspension d'opération SentinelOne
(Jacqueson, `[IT 10017710501]`), réclamation CFE sur ticket clos
(`[IT 10017605801]`), extraction d'appels CCPSC relayée par Cédric Kudlak
(`[IT 10017685401]`). Plus deux DIT à créer : licence SOMAFRAC (tag
`VALIDATION`) et compte LE BISTROT (demandeuse sans adresse mail).

### Ce qui s'est mal passé, pour ne pas le refaire

- `fusion.py` affichait une fusion **sans jamais téléverser** : 29 lignes de
  journal perdues pendant une matinée.
- J'ai **écrasé le commentaire de la DIT 100177620** en testant un faux veto
  sur un ticket réel. Restauré à l'identique dans la minute (851 caractères).
- J'ai donné deux fois un classement de modèles en m'appuyant sur ma liste
  interne, périmée — `claude-haiku-5-5` existe et tourne déjà dans la chaîne.
- Deux passages de la routine perdus sur le même faux diagnostic : « pas de
  jeton Graph » parce que `mail_tokens.json` n'était pas sur le disque.
