# Migration du dashboard MSP vers Docker

## Pré-requis : installer Docker

Docker n'est pas installé sur ce serveur. Une fois et pour toutes :

```bash
# Installation officielle Docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sudo sh

# Permettre à elliot d'utiliser Docker sans sudo
sudo usermod -aG docker elliot

# Se déconnecter / reconnecter (ou newgrp docker) pour prendre en compte
newgrp docker

# Vérifier
docker --version
docker compose version
```

## Migration en deux phases — sans couper la prod

### Phase 1 : tester Docker côte-à-côte avec systemd

L'idée : Docker tourne sur le **port 8081** pendant que systemd reste sur 8080. La MAJ auto est désactivée dans Docker (sinon les deux écriraient `msp_data.json` en même temps → conflit). Tu valides que tout marche sur `:8081`, puis tu bascules.

```bash
cd "/home/elliot/Dashboard IA"

# 1. Build l'image (~2 min)
docker compose build

# 2. Démarrer le container en mode test
#    PORT=8081 et HEURE_MAJ=99 (= jamais)
DOCKER_PORT=8081 DOCKER_HEURE_MAJ=99 docker compose up -d

# 3. Vérifier
docker compose ps
docker compose logs -f --tail=30
```

Ouvre dans ton navigateur :
- `http://10.8.1.144:8080/dashboard.html`  ← l'actuel (systemd)
- `http://10.8.1.144:8081/dashboard.html`  ← le nouveau (Docker)

Les deux doivent afficher exactement la même chose (ils lisent le même `msp_data.json`).

Teste tout ce qui t'importe sur `:8081` :
- Vue Globale, recherche, tri, clic client → modal détail
- Onglets DATTO (machines), M365 (subscriptions), etc.
- Bouton 💾 Sauvegarder + onglet 📚 Historique
- Refac sur `http://10.8.1.144:8081/refac.html`

### Phase 2 : basculer sur Docker (une fois validé)

```bash
# 1. Stopper le container de test
docker compose down

# 2. Désactiver systemd
sudo systemctl stop msp-dashboard
sudo systemctl disable msp-dashboard

# 3. Démarrer Docker en prod (port 8080 + MAJ 20h00 = defaults)
docker compose up -d

# 4. Vérifier
docker compose ps
docker compose logs -f --tail=30
```

Le dashboard reste sur `http://10.8.1.144:8080` exactement comme avant.

## Commandes utiles

```bash
# Voir les logs en direct
docker compose logs -f

# Redémarrer après un changement de code (.py / .html)
docker compose restart

# Arrêter complètement
docker compose down

# Reconstruire l'image (uniquement si requirements.txt change)
docker compose build --no-cache

# Entrer dans le container pour debug
docker compose exec dashboard bash

# Voir l'usage CPU / RAM
docker stats msp-dashboard
```

## Comment c'est structuré

| Fichier | Rôle |
|---|---|
| `Dockerfile` | Recette de l'image : Python 3.12 + dépendances |
| `requirements.txt` | Liste exacte des libs Python (figées en versions) |
| `docker-compose.yml` | Configuration du container (volumes, réseau, restart) |
| `.dockerignore` | Fichiers exclus du build (`.venv/`, `*.xlsm`, etc.) |

## Pourquoi `network_mode: host` ?

Le container partage le réseau du serveur. Conséquences :
- ✅ Le tunnel SSH `localhost:1280` (VEEAM) reste accessible depuis dans le container
- ✅ Le dashboard est joignable directement sur `http://10.8.1.144:8080`
- ⚠️ Pas d'isolation réseau (acceptable pour un serveur dédié)

## Pourquoi tout le projet est monté en volume ?

```yaml
volumes:
  - .:/app
```

Ça veut dire qu'éditer `refac.py` / `dashboard.html` sur le serveur → restart du container → modification appliquée. **Pas besoin de rebuild l'image** sauf si tu changes les dépendances Python.

## Si la migration foire — rollback

```bash
docker compose down
sudo systemctl enable --now msp-dashboard
```

Tu retombes sur l'ancienne config systemd. Aucune donnée perdue (les fichiers `msp_data.json`, `snapshots/`, `.env` n'ont jamais bougé du dossier).

## Avantages obtenus

1. **Reproductible** : `git clone` + `docker compose up` → ça marche partout
2. **Versions figées** : Python 3.12, libs en versions exactes — pas de dérive
3. **Logs propres** : `docker compose logs` au lieu de `journalctl -u msp-dashboard`
4. **Restart automatique** si crash (déjà fait par systemd, mais Docker en plus)
5. **Migration serveur facilitée** : copier le dossier + Docker, c'est tout

## Limites / à savoir

- Le tunnel SSH VEEAM reste hébergé en dehors de Docker (sur ton poste Windows). Pour une config 100% propre il faudrait soit :
  - Demander à la DSI d'ouvrir le firewall serveur → VSPC directement
  - Faire tourner `autossh` dans un 2ᵉ container side-car
