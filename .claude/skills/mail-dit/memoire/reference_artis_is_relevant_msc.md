---
name: reference_artis_is_relevant_msc
description: "Une MSC Artis absente de is_relevant_msc n'existe nulle part dans le dashboard : le filtre est appliqué AVANT toute classification"
metadata: 
  node_type: memory
  type: reference
  originSessionId: c890794f-6180-4b66-b231-c6852b570cf3
  modified: 2026-09-28T13:55:57.661Z
---

`artis_get_rfc_details` filtre les MSC dès le **stage 1** avec `is_relevant_msc` (updater.py,
~ligne 1849) : seules celles dont le libellé contient sauvegarde / 365 / antivirus / firewall /
infog / maintenance / antispam / promail / cybersecurit / packag / pack — et depuis le
2026-09-28 : cloud / azure / virtuel / héberg / datacenter — sont suivies.

**Conséquence à connaître** : une prestation dont la MSC ne matche aucun de ces mots n'est
jamais récupérée, donc elle n'apparaît ni dans `rfc_lines`, ni dans `artis.*`, ni dans aucun
écart. Elle est invisible, sans message d'erreur. C'est ce qui masquait toute la facturation
d'hébergement : MSC « Serveur Virtuel Cloud » (ALIGN AEROSPACE 20227) — 0 occurrence dans
`msp_data.json` alors que la ligne existe bien dans Artis.

**Quand un service semble « non facturé » pour tout le monde, vérifier ce filtre AVANT de
suspecter `_classify_rfc`.** `_classify_rfc` ne voit que ce que le filtre a laissé passer, et
`/api/maj/reclassify` ne répare rien ici : il re-classe les lignes déjà stockées, il ne va pas
les rechercher. Seul un cycle MAJ complet (fetch Artis, ~25 min, 20h) les fait entrer.

Élargir ce filtre augmente le nombre d'appels Artis (stages 2 et 3) : rester ciblé.

Voir [[project_azure_conso_widget]].
