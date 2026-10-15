---
name: feedback-bien-site-principal
description: "Le bien d'une DIT se cherche site par site, principal d'abord : INFOGERANCE puis BAL EXCHANGE 365 puis PROTECT, sur modele.code exact, et le site du bien retenu est celui qu'on pose"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 839132cb-cb04-4d5b-8f11-b0013c373e0f
  modified: 2026-10-09T13:14:28.829Z
---

**Regle posee par Elliot le 2026-10-09** : « tu mets toujours le bien du SITE
principal (et evidemment toujours du meme client) INFOGERANCE, si tu trouves
pas BAL EXCHANGE, sinon PROTECT, et si toujours rien tu passes au site
suivant ».

Donc on balaye les **sites**, principal d'abord, puis les autres ; dans chaque
site on essaie les trois modeles dans cet ordre ; le premier qui repond gagne —
**et c'est SON site qu'on pose dans la DIT**, jamais un site calcule a part.

**Le tri porte sur `modele.code`, EXACTEMENT.** L'ancien filtre cherchait
« INFOG » dans le libelle : Constantia Jeanne d'Arc (17661), qui n'a que de la
securite (centrales VERSO+, NVR, telesurveillance, controle d'acces) plus un
bien « INFOGERANCE **AUDIO** », passait pour un client sous contrat
d'infogerance informatique. Il rend `None` desormais.

**Why:** deux pannes du meme jour. Le libelle approximatif declarait un contrat
Info la ou il n'y en a pas ; et faire voyager le site separement du bien
laissait Artis coller le site du bien a la DIT — d'ou sept tickets au nom de
XILAM ([[reference-artis-bien-cle-globale]]). Lier les deux rend le couple
incoherent impossible.

**How to apply:** `_bien_et_site(E, code, valide=None)` dans
`agent_mail_dit.py` rend `(identifiantFabricant, codeSite)` ; `creer_dit` passe
les deux. Meme regle dans `mail_dit.bien_informatique` pour le robot a regles.
Un client qui rend `(None, None)` est sans contrat Info :
[[feedback-pas-de-contrat-info-pas-de-dit]].
