---
name: feedback_pas_de_contrat_info_pas_de_dit
description: "Client sans contrat d'infogerance : jamais de DIT — transferer le mail a cdamato@dfm.fr avec Clifford en copie, puis ranger en 01 DIT"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-01T07:58:08.634Z
---

Elliot, le **2026-10-01**, sur la DIT **100175462** (AREAL 29449) : « pas de
contrat info chez nous. Ta cree ca sans contrat info chez le client donc annule
pour l'instant et ajoute ce nouveau skill : si un ticket doit etre cree en info
mais que le client n'a pas de contrat Info, transfert le mail dans DIT ARTIS
SUPPORT apres l'avoir transfere a cdamato@dfm.fr avec Clifford en copie. »

**Le test** : `bien_dit(code)` (skill mail-dit, `bin/artis.py`) rend `None`
quand le client n'a **ni** bien INFOGERANCE **ni** BAL EXCHANGE 365. C'est le
signe qu'il n'est pas sous contrat d'infogerance. AREAL n'avait que 4 biens :
un ecran Samsung et trois copieurs Canon.

**Why** : une DIT en domaine INFO engage le contrat d'infogerance. Pour un
client qui n'en a pas, le ticket n'a pas de support contractuel : il fausse les
compteurs, et la demande n'arrive pas chez ceux qui peuvent la chiffrer. La
bonne porte est le commercial.

**How to apply**

1. `bien_dit(code)` -> `None` : **ne pas creer**.
2. **Transferer** le mail a `cdamato@dfm.fr`, Clifford (`cyapnewkpin@dfm.fr`)
   en copie, avec une phrase disant que le client n'a pas de contrat Info.
   C'est la **seule exception** a l'interdit d'envoi de mail — transfert
   interne, ces deux destinataires, jamais ailleurs.
3. **Ranger** ensuite le mail en `01 - DIT ARTIS SUPPORT`.
4. Le dire dans le rapport.
5. Si une DIT a deja ete creee a tort : l'annuler
   (`annuler_intervention(numero + "01")`), puis faire 2 et 3.

**Pas de retroactivite.** Elliot, le 2026-10-01 : « non pas retroactivement ».
Les DIT deja ouvertes pour un client hors contrat (100175935 ACT BEAUTY,
100175766 et 100175839 SPAT) suivent leur cours : on les complete normalement
quand le client ecrit, on ne les annule pas, on ne transfere pas leur fil. La
regle ne vaut que pour les creations a venir.

**Une DIT sans bien est donc une alerte, pas une variante acceptable.** Je
l'avais traitee comme un simple cas a signaler (SPAT 7968, ACT BEAUTY 11160) —
c'etait deja le symptome de ce probleme.

Voir [[project_skill_mail_dit]], [[feedback_dit_bien_et_onboarding]],
[[reference_artis_bien_pagination]].
