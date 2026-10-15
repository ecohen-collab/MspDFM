---
name: reference-artis-bien-cle-globale
description: "identifiantFabricant est une cle GLOBALE chez Artis et POST /dit suit le site du BIEN, pas le codeSite : un libelle nu met la DIT chez un autre client"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-09T13:02:42.297Z
---

**`identifiantFabricantBien` n'est pas scope au client, et `POST /dit` garde le
site du BIEN en ignorant le `codeSite` qu'on envoie.** On transmet pourtant
`codeClient`, `codeSite` et `identifiantFabricantBien` ensemble : c'est le bien
qui gagne.

Consequence : un identifiant qui appartient a un AUTRE client emmene son site
avec lui, en silence. `GET /bien/fabricant/INFOGERANCE` (le libelle nu, sans
prefixe) rend le bien du client **14630 XILAM**, contrat ferme le 19/08/2024.

Le 2026-10-07, **sept DIT** sont parties ainsi : 100176674 (EKILIBRE), 100176675
(MOBILITE MUTUELLE), 100176677 (GALYO), 100176688 (TP 2000), 100176693 et
100176726 (MYMOBILITY 9599), 100176714 (ACJE). Toutes portent « XILAM, 57 bd de
la Villette, Paris 10e ». Elliot, le 2026-10-09 : « comment il a fait pour mettre
XILAM ? c'est meme pas un site du client ?? ».

**Cause : nos propres outils.** L'outil `client` rendait ses biens par
**libelle** (`["INFOGERANCE", ...]`) et le schema de `creer_dit` disait
« libelle exact rendu par l'outil client ». Le bon champ, `bien_pour_la_dit`,
etait juste au-dessus. Le modele a fait ce qu'on lui demandait.

**Garde-fous (agent_mail_dit.py, 2026-10-09) :** l'outil `client` rend
`identifiant | libelle` ; `_bien_du_client(E, code, valide=…)` verifie tout
bien passe par l'appelant contre la fiche du client et le remplace s'il n'y est
pas ; apres creation la DIT est relue et le site compare a la **liste des sites
du client** — jamais a son libelle, un client ayant souvent des sites nommes
autrement (ARCOTEL 13392 : « HOTEL MAGDA »).

**Rien ne se repare apres coup** : ni site ni bien dans une route d'ecriture, et
l'intervention n'expose que `annuler`/`planifier`/`debuter`/`terminer`. Voir
[[feedback-dit-bien-et-onboarding]] et [[feedback-cause-puis-garde-fou]].
