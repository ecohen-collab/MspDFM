---
name: Artis — pas d'écriture en prod sans accord explicite
description: Toute action d'écriture vers Artis API (POST DIT, PATCH saisirCrit, etc.) requiert un OK explicite d'Elliot avant exécution
type: feedback
originSessionId: decc76e7-3fd8-4af4-9cda-e2cefc2f50f1
---
Pour les API qui modifient de la donnée Artis en prod (création de DIT, saisie CRIT, annulation, etc.), **ne JAMAIS lancer la requête sans l'accord explicite d'Elliot pour ce numéro précis**, même pour un "probe" ou une exploration de schéma.

**Why:** Le 2026-05-05, en sondant POST /dit avec divers bodies pour découvrir les champs requis, j'ai créé 4 vrais DIT sur DFM OFFICE (100159282-285) que j'ai dû lui faire annuler à la main. L'API Artis n'a pas de dry-run et tout 200 OK = écriture effective. Elliot a explicitement dit "ne fait plus rien de ce genre sans me demander".

**How to apply:**
- Sondage de schéma uniquement sur des numéros que je sais inexistants (genre 11111111111) → l'API renvoie 404 sans rien créer
- Toute requête sur un IT/DIT réel doit avoir une instruction utilisateur explicite ("clôture l'IT X avec ces infos", "crée un DIT pour le client Y")
- En cas de doute, demander avant
- Vaut aussi pour DELETE / PATCH / PUT sur tout endpoint d'écriture
