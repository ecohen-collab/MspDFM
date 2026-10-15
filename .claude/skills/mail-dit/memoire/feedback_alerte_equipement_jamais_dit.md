---
name: feedback-alerte-equipement-jamais-dit
description: Un journal d'equipement reseau (routeur, firewall, sonde) ne doit jamais devenir une DIT
metadata:
  type: feedback
---

Un mail qui n'est qu'un **deversement de journal** d'equipement reseau (routeur
DrayTek/Vigor, firewall, sonde IDS : `[Mail Alert][IPFLOG]`, `[DOS][Block][port_scan]`,
syslog) n'est pas une demande. Il se range en `04 - INFORMATIONS`, il ne produit
jamais de ticket.

**Why:** le 2026-09-24, Elliot sur la DIT 100175024 : « c'est quoi ce bordel ? il
n'a aucun sens, ce n'est pas une DIT c'est juste une alerte ». 11 mails du Vigor2927
de GAZDETECT (client 29683) avaient produit un ticket, puis chaque alerte suivante
etait collee dedans en « ajout du client ». L'expediteur `scanbcs2023@bcsystemit.fr`
ne ressemble pas a une adresse automatique : le garde-fou `RE_NOREP` ne le voyait pas.

**How to apply:** `mail_regles.classer` renvoie `ignore` sur deux signaux
independants — `RE_ALERTE_EQUIPEMENT` sur l'objet, et `corps_de_journal()` qui
compte les lignes horodatees a champs entre crochets (>= 4). Deux lignes de log
citees par un humain dans sa phrase ne declenchent pas la regle. Voir
[[feedback-soc-sans-mco]] pour les alertes SOC, qui elles se transferent a MCO.
