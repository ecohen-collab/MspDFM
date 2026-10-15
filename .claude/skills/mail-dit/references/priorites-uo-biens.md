# Priorités, UO, biens, symptômes

## Priorité — les codes sont inversés

La règle d'Elliot, dans ses mots :
« si personne est bloqué niveau 1, si une personne est bloquée niveau 2,
et si tout le monde est bloqué ou arrêt de prod niveau 3 ».

| Situation | Niveau | **Code Artis** |
|---|---|---|
| Personne n'est bloqué (demande, administration, confort) | Niveau 1 Standard | **03** |
| Une personne est bloquée | Niveau 2 | **02** |
| Toute la société bloquée, ou arrêt de production | Niveau 3 Urgent | **01** |

Le code `01` est le plus urgent, `03` le moins. Raisonner en **libellé** puis
traduire, jamais l'inverse.

**La priorité ne se corrige pas après création** : le champ n'est pas modifiable
par le formulaire. Une erreur de priorité se répare en annulant et recréant —
donc réfléchir avant.

### Un incident de sécurité est URGENT, quel que soit le nombre de bloqués

**Hameçonnage, arnaque au président, compte compromis, rançongiciel, fuite de
données : Niveau 3 Urgent, code `01`.** La grille « combien de personnes sont
bloquées » ne s'y applique pas — personne n'est bloqué pendant qu'un attaquant
travaille, et c'est précisément le problème : le temps joue contre le client.

Elliot, le 2026-10-13, sur la DIT **100176790** (DUONEXT, tentative d'arnaque
au président reçue par la RAF, que j'avais posée en Niveau 1 parce que personne
n'était empêché de travailler) : « 100176790 niveau 1 par l'IA au lieu de 3 ».

Le signal est le **sujet**, pas la gêne constatée : un mail d'hameçonnage
signalé par le client, un `SEC-INC-*`, un compte dont on soupçonne qu'il est
utilisé par un tiers. Le ticket déjà parti ne se corrige pas — la règle vaut
pour les créations suivantes.

## UO

**Ce n'est pas le préfixe du symptôme qui décide, c'est le niveau d'expertise
que le geste demande.** Elliot, le 2026-10-01, sur la DIT 100176005 : « demande
simple de reset de mot de passe = hotline N1/N2, t'as dû mettre HLN3 ».

| | Ce qui y va |
|---|---|
| **HLI** — hotline informatique N1/N2 | ce qu'un technicien de premier niveau fait seul : **reset ou oubli de mot de passe**, configuration d'un poste ou d'un mobile, accès à un dossier partagé, imprimante, VPN, session Windows, application métier, réseau |
| **HLN3** — niveau 3 | ce qui demande la console d'administration et un vrai pouvoir : création / suppression de compte M365, licences, délégations Exchange, boîtes partagées, SharePoint, MFA et accès conditionnel, antispam, antivirus, onboarding complet d'un salarié |

Le piège : `ADM-365-GES` (« mot de passe, informations utilisateur M365 ») porte
le préfixe `ADM-` mais désigne précisément le geste de premier niveau — il va en
**HLI**. La règle mécanique « `ADM-` → HLN3 » a fait partir en HLN3 deux resets
de mot de passe (100176005 SRP, 100176090 PARC ESPACE), corrigés depuis.

- Dans le doute, demander : *est-ce qu'un N1 peut le faire avec ses droits ?*
  Oui → HLI. Non → HLN3.
- **Un INCIDENT se diagnostique en N1/N2, même s'il faut ouvrir la console.**
  La console d'administration est un outil, pas un niveau : un N1 qui regarde un
  suivi de messages ou l'état d'une boîte reste un N1. Ce qui fait le N3, c'est
  le **geste d'administration** (créer, supprimer, licencier, déléguer), pas le
  fait de se connecter quelque part. Elliot, le 2026-10-05, sur la DIT
  **100176481** (NEURAXPHARM, deux boîtes qui n'envoient ni ne reçoivent, que
  j'avais mise en HLN3) : « N1/N2, pas N3 ». Corrigée en HLI, comme **100176485**
  (session TSE chez UFER) et **04216938** (session « Qualité » chez MONDIAL
  FRUITS SECS), partis en HLN3 pour la même raison le même jour — alors que la
  table ci-dessus range explicitement « session Windows » en HLI. Un dispatcheur
  avait déjà corrigé **100176463** (NMCG) avant moi.
  Autrement dit : `PC-INC-*` et `PC-DEM-*` vont en **HLI** par défaut, et il
  faut une raison nommée pour les envoyer en HLN3 — par exemple le collègue qui
  écrit « pour le N3 » (DIT 100176473), ou un précédent identique déjà classé
  HLN3 (DIT 100176480, calquée sur 100176022).
- **Un incident SERVEUR est l'exception : `SRV-*` part en HLN3.** La phrase
  précédente parle des postes ; un serveur, lui, n'est pas un poste. Elliot, le
  2026-10-14, sur la DIT **100177554** (ETTB / HOLDING HBL, VM Hyper-V
  injoignable, ping et RDP muets, que j'avais mise en HLI parce que c'était un
  incident) : « pour ce ticket il aurait dû être créé en Niveau 3 ». Relancer
  une machine virtuelle de production, regarder l'espace disque d'un hôte,
  toucher à l'hyperviseur : ce n'est pas ce qu'un N1 fait seul, même quand le
  mail décrit une panne et non une demande.
  **Et ça ne se rattrape pas par l'API** : `PATCH /it/{n}/uo/HLN3` rend
  `HTTP 500 — « L'intervenant n'a pas de rôle pour l'UO de l'IT »`, le compte
  de service n'ayant pas ce rôle. Comme le bien et le demandeur, l'UO N3 se
  pose **à la création**, sinon c'est une correction à l'écran.
- Onboarding (`ADM-ON`) et offboarding (`ADM-OFF`) → **HLN3** : il faut créer ou
  retirer le compte, les licences et les accès.
- Posée par `poser_uo(numero_dit + "01", code)`, avec reprise sur 404
  (le ticket met parfois quelques secondes à être indexé).
- **L'UO peut être changée après coup par un dispatcheur** : c'est normal, et
  ce n'est pas une erreur à corriger. Une DIT déjà reprise (`Planifiee`,
  `Terminee`) ne se retouche pas.

## Bien

Ordre de préférence, avec `?maxResults=5000` sur `/client/{code}/bien` :

1. le bien dont le libellé contient `INFOGERANCE`
2. à défaut, celui qui contient `BAL EXCHANGE 365`
3. **si le client n'a ni l'un ni l'autre, il n'est pas sous contrat
   d'infogérance : PAS de DIT.** On transfère le mail à `cdamato@dfm.fr` avec
   Clifford en copie, puis on le range en 01 - DIT (voir le SKILL.md). Une DIT
   sans bien est le signe qu'on a créé un ticket pour un client hors contrat —
   c'est l'erreur de la DIT 100175462 (AREAL 29449), annulée le 2026-10-01.

L'`identifiantFabricant` varie d'un client à l'autre : `9599 - INFOGERANCE`,
`5581-INFOGERANCE`, `20431-INFOGERANCE`, `INFOGERANCE-20431`… C'est cette
chaîne exacte qui part dans `identifiantFabricantBien`, pas une forme
reconstruite.

## Symptômes les plus utilisés

| Code | Quand |
|---|---|
| `ADM-365-CRE` | création de compte M365 |
| `ADM-365-SUP` | suppression / désactivation de compte |
| `ADM-365-LIC` | attribution ou retrait de licence |
| `ADM-365-EXC-BAL` | Exchange : droits, boîtes partagées, délégations, groupes, listes de diffusion |
| `ADM-365-SHA-ACC` | SharePoint : droits, sites, fichiers |
| `ADM-365-AUT-ADM` | Teams, OneDrive, Copilot, domaines |
| `ADM-365-SEC-ADM` | MFA, accès conditionnel |
| `ADM-365-GES` | mot de passe, informations utilisateur M365 |
| `ADM-ANT` | antispam : blocage d'adresse, mails en indésirables |
| `ADM-AV` | antivirus : exclusion, autorisation, déploiement |
| `ADM-ON` | onboarding d'un nouveau salarié |
| `PC-INC-SES` / `PC-DEM-SES` | session Windows, compte local, mot de passe réseau |
| `PC-INC-MES` / `PC-DEM-MES` | Outlook côté poste : boîte pleine, routage, profil |
| `PC-INC-OFF` / `PC-DEM-OFF` | Word, Excel, PowerPoint, Teams |
| `PC-INC-ONE` / `PC-DEM-ONE` | OneDrive, SharePoint synchronisé (dossier local indisponible) |
| `PC-INC-MAT` / `PC-DEM-MAT` | matériel : écran, casque, périphérique |
| `PC-INC-APP` / `PC-DEM-APP` | application métier |
| `PC-INC-IMP` / `PC-DEM-IMP` | imprimante côté poste (pilote, file d'attente) |
| `PC-INC-VPN` / `PC-DEM-VPN` | VPN côté poste |
| `SRV-DEM-*` | serveur : AD, GPO, sauvegarde, Hyper-V, NAS, applications |
| `NET-DEM-FW` | firewall, VPN site à site, WAN |
| `NET-DEM-LAN` | LAN, wifi |
| `SEC-INC-MES` / `SEC-INC-365` | incident de sécurité avéré |

La liste complète et à jour se lit par `mail_dit.symptomes_officiels()` —
80 entrées. Ne jamais inventer un code : s'il n'y a pas de correspondance,
prendre `PC-DEM-AUT` ou `PC-INC-AUT` et le signaler dans le rapport.

## Type de travail et facturation

Après création, par le formulaire web, dans la session unique du passage :
type de travail **Hotline**, facturation **Non facturable**.
Le défaut à la création est `Terrain` / `Automatique` — donc l'étape n'est pas
optionnelle.

**Ce n'est pas facultatif, et ça ne se reporte pas au lendemain.** Elliot, le
2026-10-01, sur les DIT 100175991 / 100176022 / 100176051 : « il a créé les
tickets avec intervention terrain au lieu de hotline, comment ça se fait ???
je t'ai dit que du HOTLINE ». Les DIT étaient parties en `Terrain` parce que la
consigne de passage interdisait d'ouvrir le formulaire web — l'interdit portait
sur le coût d'une licence, pas sur le résultat. **Une DIT laissée en Terrain est
une DIT fausse** : on groupe donc tous les numéros du passage et on les pose
dans la session unique, à la fin.

**Fenêtre étroite** : tant que l'intervention est `APlanifier`, l'enregistrement
passe. Dès qu'un dispatcheur l'a **planifiée**, Artis refuse avec
`Enregistrer_KO_SYSTEM` et ne change rien — le type de travail reste alors tel
quel, et seul un humain peut le corriger dans Artis. D'où l'urgence de le poser
dans le passage même.
