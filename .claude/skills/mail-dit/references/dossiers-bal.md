# Dossiers de rangement — support@dfm.fr

Tous les folderId partagent le même préfixe :

```
AAMkAGRiODcwNTUyLTM1MWQtNGNmMS04YjY4LTVmOTlkZGE3MGU5ZQAuAAAAAAAh7Ne3QcqlSZzTxG2b4TAoAQCXsXH4B7bBQasIY1rwlVt2
```

| Dossier | Suffixe du folderId | Ce qu'on y range |
|---|---|---|
| `01 - DIT ARTIS SUPPORT` | `AACJJ9ETAAA=` | mail dont la DIT a été créée, ou suite d'un ticket existant |
| `04 - INFORMATIONS` | `AACJJ9ERAAA=` | alertes et rapports automatiques, notifications éditeur, factures, newsletters, ordres internes |
| `07 - FIREWALL` | `AAF-fqRhAAA=` | journaux et alertes firewall dédiés |
| `BAL IMPRESSION` | `AAF-fqRbAAA=` | copieurs, consommables, interventions machine |
| `BAL SECURITE` | `AAF-fqRcAAA=` | alarme, télésurveillance, contrôle d'accès, badgeuse, vidéo, incendie |
| `BAL TELECOM` | `AAF-fqRaAAA=` | téléphonie, standard, DECT, portabilité, lignes, fibre, 4G |

**Le préfixe ci-dessus vaut pour les DOSSIERS, jamais pour les MESSAGES.**
Un identifiant de message se reprend en entier depuis la recherche, jamais
reconstruit — voir `pieges-artis.md`.

## Cas de tri récurrents

- **Rapport de sauvegarde** (Iperius, Hybrid Backup Sync, Veeam) → 04 - INFORMATIONS.
- **Journal d'équipement réseau** (Vigor IPFLOG, WAN up/down, blocages DOS) →
  04 - INFORMATIONS. Jamais de DIT, même quand le libellé parle d'attaque.
- **Alerte RMM Datto / supervision** → 04 - INFORMATIONS.
- **Télésurveillance** (Cyclop, Telesure, INEO) → BAL SECURITE.
- **Réponse sortante d'un collègue DFM** : le dossier suit le métier du fil, pas
  l'expéditeur. Un échange sécurité reste en BAL SECURITE.
- **support@dfm.fr absent des destinataires** (mail arrivé par redirection depuis
  `qsl@avideon.fr`, `sav-abc@dfm.fr`, `sav@bcsystem.fr`) : ranger selon le
  métier, sans DIT — ce n'est pas une demande adressée au support.
- **Mail suspect / phishing** : BAL SECURITE et signalement à Elliot. Aucune
  action sur le contenu, aucun lien ouvert.
