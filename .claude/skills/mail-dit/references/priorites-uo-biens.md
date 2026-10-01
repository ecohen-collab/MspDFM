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

## UO

- Symptôme commençant par `ADM-` → **HLN3** (administration, niveau 3).
- Tout le reste → **HLI** (hotline informatique).
- Onboarding (`ADM-ON`) → **HLN3**.
- Posée par `poser_uo(numero_dit + "01", code)`, avec reprise sur 404
  (le ticket met parfois quelques secondes à être indexé).

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
