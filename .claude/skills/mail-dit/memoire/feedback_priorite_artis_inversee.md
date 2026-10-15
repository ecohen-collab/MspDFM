---
name: feedback_priorite_artis_inversee
description: "Priorite d'une DIT : compter QUI est bloque. Personne=Niveau 1 (03), une personne=Niveau 2 (02), tout le monde ou arret de prod=Niveau 3 (01)"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-09-29T10:33:20.914Z
---

**La regle d'Elliot, redonnee le 2026-09-29 :**

| Situation                                   | Niveau              | Code Artis |
|---------------------------------------------|---------------------|------------|
| **personne** n'est bloque                    | Niveau 1 - Standard | `03`       |
| **une personne** est bloquee                 | Niveau 2 - Important| `02`       |
| **tout le monde** bloque, ou arret de prod   | Niveau 3 - Urgent   | `01`       |

Table Artis verifiee sur `GET /parametrage/priorites` :
`01 = Niveau 3 - Urgent`, `02 = Niveau 2 - Important`, `03 = Niveau 1 - Standard`.
Le code monte quand l'urgence descend.

**L'erreur a ne pas refaire** : la consigne de la boucle dit « toute la societe
01 - une personne 02 - **personne bloquee 03** ». « Personne bloquee » veut dire
**PERSONNE n'est bloque**, pas « une personne qui se trouve bloquee ». J'ai lu
l'inverse le 2026-09-29 et sorti en Niveau 1 - Standard trois incidents ou des
gens etaient bloques : ALIGN AEROSPACE 100175691 (serveur injoignable en RDP),
FILMETAL 100175658 (ERP inaccessible, « URGENT »), CENTRE DENTAIRE DAUMESNIL
100175683 (poste d'une salle de soins qui ne demarre plus). Le niveau 3 l'a
remonte : « mail auto en niveau 2 au lieu de 3 ».

**How to apply** : se poser une seule question — *qui est bloque ?* — puis
traduire. Ne jamais raisonner sur le chiffre du code.

**La priorite ne se corrige pas apres coup avec les outils actuels** :
- aucun endpoint REST (les seuls PATCH sur `/it/{n}` sont uo, intervenant,
  bien, planifier, debuter, terminer, deplanifier, annuler, saisirCrit) ;
- `artis_type_travail.poser_type_travail` ne voit que le bloc deplie de
  l'intervention — type de travail et `cb_typeFactu` — et **pas** la priorite
  (sonde du 2026-09-29 : `cb_priorite`, `cbo_priorite`, `cb_prioriteIt`,
  `cbo_prioriteIt`, `cbo_codePriorite`... tous absents du bloc).
Elle vit sur un autre ecran. **La poser juste a la creation est la seule voie
sure** ; sinon c'est une reprise a la main dans Artis.

**Piege de lecture associe** : « niveau 2 / niveau 3 » dans un retour d'equipe
designe la **priorite**, pas l'UO. J'avais compris l'UO et passe deux tickets en
HLN3 sans que ce soit demande — annule, retour a HLI.

Voir [[project_mail_to_dit_artis]] et [[project_robot_jour_nuit]].
