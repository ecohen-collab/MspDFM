---
name: feedback-telecom-jamais-de-dit
description: Aucune création de DIT pour la téléphonie — on range le mail en BAL TELECOM, rien de plus
metadata:
  type: feedback
---

Un mail de **téléphonie** ne donne JAMAIS lieu à une création de DIT. Notre rôle
s'arrête à le poser devant l'équipe télécom : rangement en `BAL TELECOM`, et
rien d'autre. L'équipe ouvre ses propres affaires.

**Why:** Elliot, 2026-09-25 : « on ne fait aucune création pour le telecom, donc
tu deplace juste dans les bonnes BAL ». Question posée après le dossier BSA
(4048), où une cliente relançait sur des coupures de ligne sans qu'aucun ticket
n'existe — la tentation était de créer en domaine TEL. C'est aussi ce que la DIT
CHT 100175330 avait montré à l'envers : ouverte hors du domaine INFO, elle est
sortie en Terrain/Automatique et le type « Hotline » n'y existait même pas.

**How to apply:** `decider_seul` dans `mail_dit.py` renvoie `ranger / telecom`
AVANT toute autre règle dès que `RE_TELEPHONIE` reconnaît le texte ou qu'un
référent télécom est destinataire — un mail de téléphonie ne peut donc plus
devenir un ticket par un autre chemin. Les référents sont dans `REFERENTS`.
Voir [[feedback-metier-qui-demande-de-laide]] et [[feedback-metier-lire-le-corps]].
