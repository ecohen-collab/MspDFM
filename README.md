# Dashboard Artis — Guide d'installation

## Ce que tu as reçu

```
artis-dashboard/
├── dashboard.html       ← Ouvre ça dans ton navigateur
├── updater.py           ← Script de mise à jour automatique
├── LANCER_UPDATER.bat   ← Double-clique pour démarrer la mise à jour auto
└── README.md            ← Ce fichier
```

---

## Installation (5 minutes, une seule fois)

### Étape 1 — Installer Python
Si tu n'as pas Python sur ton ordi :
1. Va sur https://www.python.org/downloads/
2. Télécharge la dernière version Windows
3. ⚠️ Coche bien "Add Python to PATH" pendant l'installation

### Étape 2 — Lancer la mise à jour automatique
1. Double-clique sur **LANCER_UPDATER.bat**
2. Une fenêtre noire s'ouvre → c'est normal, laisse-la ouverte (ou minimise-la)
3. Les données sont téléchargées immédiatement au démarrage, puis tous les soirs à 20h

### Étape 3 — Ouvrir le dashboard
1. Double-clique sur **dashboard.html**
2. Il s'ouvre dans ton navigateur
3. Clique sur "Actualiser" pour forcer un rechargement immédiat

---

## Utilisation quotidienne

- **Chaque soir à 20h** : le script récupère automatiquement les données d'Artis
- **Pour voir les données du jour** : ouvre dashboard.html dans le navigateur
- Le dashboard fonctionne même hors ligne (données du dernier chargement)
- En bas à gauche : la date et l'heure de la dernière mise à jour

---

## Pour que ça tourne au démarrage de Windows (optionnel)

1. Appuie sur `Windows + R`, tape `shell:startup`, Entrée
2. Copie un raccourci de **LANCER_UPDATER.bat** dans ce dossier
3. Le script se lancera automatiquement à chaque démarrage de l'ordi

---

## En cas de problème

- Vérifie que ton ordi est connecté à internet
- Ouvre **artis_update.log** pour voir les erreurs détaillées
- Si "Token introuvable" : le mot de passe Artis a peut-être changé → modifie `updater.py` ligne 18
