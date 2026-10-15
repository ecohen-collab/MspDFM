---
name: reference-artis-msc-info-seulement
description: Une MSC Artis hors is_relevant_msc est invisible — de l'infogérance facturée peut ne figurer nulle part (incident SEGETEX 2026-10-12)
metadata:
  type: reference
---

`is_relevant_msc()` filtre les MSC **sur leur libellé**. Une MSC dont le libellé ne contient
aucun des mots cherchés est écartée dès le premier étage : ses lignes RFC ne sont jamais
lues, et sa facturation **n'existe nulle part dans le dashboard, sans aucune erreur**.

**Incident du 2026-10-12.** Elliot repère dans Artis que SEGETEX facture
« Infogérance - postes et serveurs » **313,75 €/mois** sous une MSC intitulée
**« Services 1/2 journées »**. Aucun mot du filtre ne correspond. Résultat : SEGETEX
apparaissait avec 0 € d'infogérance facturée et figurait dans la liste des sites Datto à
supprimer — 45 postes. Deux autres cas trouvés dans le même scan : **AU POIDS LEGAL**
(« CARNET DE TICKET », 1 449 €/mois) et **FÉDÉRATION NATIONALE DES CHASSEURS**
(« Services 1/2 journées planifiées », 1 077 €/mois).

**Correctif (rev 0871).** Le filtre de libellé est **supprimé de la lecture** : toutes les
MSC vivantes (hors Fermé / Résilié.Soldé) sont récupérées. `is_relevant_msc()` ne décide
plus de ce qu'on VOIT, seulement de ce qui COMPTE : une MSC qui n'y passe pas est marquée
`info_seulement`, s'affiche avec sa quantité et son prix (badge `INFO` gris dans le détail
RFC) et ne touche ni `billed` ni les écarts. `reclassify_rfc_lines()` a son propre
garde-fou sur `info_seulement`, sinon le recalcul leur rendait un service.
Coût mesuré : lire les 10 139 MSC des 3 126 contrats du périmètre prend ~40 s.

**Balayage complet du 12/10/2026** : 4 642 MSC vivantes étaient ignorées (387 libellés —
pour l'essentiel impression, VOIP, FTTH, télésurveillance, à juste titre). Seules **10
lignes** portaient un service suivi, **7 042 €/mois invisibles** chez 10 clients. Les 7 cas
d'infogérance (OPQIBI 1 917 €, AU POIDS LEGAL 1 449 €, ORTHOFIX 1 095 €, FNC 1 078 €,
INNOVIE 496 €, SEGETEX 314 €, IMPRIM 2000 279 €) ont été déclarés **au forfait** dans
`datto_forfait.json` — ce sont des forfaits (qté 1 pour 53 machines chez SEGETEX), les
compter donnerait un écart tout aussi faux. Piège de lecture : COLLÈGE COUSTEAU remonte sur
le code `INFGPS-1` mais sa ligne est « LICENCE ChatGPT ».

**How to apply:** avant de conclure qu'un client ne paie pas un service, relire **toutes**
ses MSC dans Artis (`GET /contrat/{c}/msc`, sans filtre), pas le cache du dashboard. Et ne
jamais bâtir une liste de suppression sur la seule absence de facturation vue par le
dashboard. Voir [[reference-artis-is-relevant-msc]].
