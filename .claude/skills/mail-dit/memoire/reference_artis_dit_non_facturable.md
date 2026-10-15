---
name: Workflow Artis — passer une DIT en NonFacturable
description: Séquence API validée pour basculer une DIT clôturée Facturable → NonFacturable (3-4 calls + pauses critiques). Validé en prod le 2026-05-06 sur DIT 100155693 et 09402870.
type: reference
originSessionId: decc76e7-3fd8-4af4-9cda-e2cefc2f50f1
---
# Workflow API : passer une DIT clôturée en NonFacturable

## À la CRÉATION, c'est le formulaire web, pas l'API (2026-09-22)

`POST /dit` **n'accepte pas** `typeFacturation`, et aucune route PATCH ne
l'écrit sur une intervention ouverte — la seule qui l'écrive est `saisirCrit`,
donc à la clôture. Une DIT créée par l'API sort en `Automatique`.

Le champ existe dans le formulaire web : `expandable_{rowKey}_cb_typeFactu`,
dans le MÊME bloc déplié que le type de travail (`cbo_typeTravail`), options
`Facturable` / `Non facturable` / `Automatique`. Le formulaire écrit
« Non facturable » (avec une espace), l'API relit `NonFacturable`.

`artis_type_travail.poser_type_travail(..., aussi={"cb_typeFactu": "Non facturable"})`
pose les deux champs dans une seule session — une session = un siège de licence.
Le moteur mail le fait à chaque création depuis la rev 0650 (demande d'Elliot :
« je veux que ce soit créé à partir de maintenant en typeFacturation non
facturable »). Validé sur la DIT 100174728 : `champs_modifies: ["facturation"]`,
`collateral: []`.

Voir [[reference_artis_formulaire_web]] pour les pièges du pilotage du
formulaire, et [[feedback_crit_format]] pour le CRIT de clôture.

## Sur une DIT DÉJÀ CLÔTURÉE : reouvrir + saisirCrit

## Pré-conditions
- DIT en état `Cloturee`
- Actuellement `typeFacturation = Facturable` (skip si déjà NonFacturable)

## Séquence (3-4 calls)

```
1. PATCH /dit/{numeroDit}/reouvrir          → 204, crée auto une nouvelle IT en APlanifier
2. PAUSE 15-60s (latence variable)
3. (optionnel) PATCH /it/{newIT}/intervenant/{matricule}   → set intervenant
4. PATCH /it/{newIT}/saisirCrit  avec body :
   {
     "dateDebut": "<now ISO>",
     "dateFin":   "<now+1min ISO>",
     "commentaireClient": "Passage en non facturable — validation E. Cohen <date>",
     "lignes": [
       {"prestation": {"code": "INF"}, "qteUtilisee": 0.01, "typeFacturation": "NonFacturable"}
     ]
   }
   → 204. La DIT repasse auto en Cloturee (pas besoin d'appeler /cloturer)
```

⚠️ **Format du body saisirCrit DIFFÉRENT** du workflow clôture standard (qui utilise `{libelle, code, duree}`). Ici c'est `{prestation: {code}, qteUtilisee, typeFacturation}`.

## Pièges (incidents constatés)

| Symptôme | Cause | Fix |
|---|---|---|
| 404 sur saisirCrit ou intervenant juste après reouvrir | L'IT existe dans le détail DIT mais n'est pas encore indexée pour les sub-resources | Attendre 30-60s. Pas d'auto-retry agressif. |
| 500 "Il faut saisir au moins une ligne de prestation" | Body avec `article: {code: "MOIN"}` au lieu de `prestation: {code: "INF"}` | Utiliser `prestation: {code: "INF"}` |
| 500 "qte" rejeté à 0 | `qteUtilisee: 0.0` refusé | Mettre `0.01` minimum |
| 500 "Un crit a déjà été saisi" | `saisirCrit` est create-only (pas update) | Impossible de modifier un CRIT existant via l'API. Le bouton "Modifier" de l'UI Artis utilise un autre endpoint, **non exposé en swagger**. |
| Code `INF` → `article.code = DPIN` dans la DIT renvoyée | Mapping interne Artis | Normal, ignorer côté code |

## Constantes utiles

- Matricule Elliot Cohen : `ELCOHEN` (cohérent avec `ARTIS_USERNAME` env)
- Format général matricule : majuscules courtes (`MTOUATI`, `SBARCELO`) — quelques exceptions email-format (`vgarcia@dfm.fr`)
- Numéros DIT à 8 ou 9 chiffres : fonctionnent pareil (ex `09402870` ou `100155693`)
- Timeout recommandé sur les PATCH : `60s` (30s parfois insuffisant)
- Base URL v2 HTTPS obligatoire pour les PATCH (sinon 302 → requests dégrade en GET → 405)

## Helper Python (canonical avec dry-run)

```python
def artis_passer_dit_non_facturable(numero_dit, dry_run=True, pause_s=30, intervenant_matricule="ELCOHEN"):
    """
    Bascule une DIT clôturée Facturable → NonFacturable.
    Séquence : reouvrir → pause → set intervenant → saisirCrit.
    Retourne dict avec status de chaque étape.
    """
    # 1. Vérifier état initial (lecture)
    # 2. Si dry_run : retourner payload prévisionnel
    # 3. Sinon :
    #    - PATCH /dit/{n}/reouvrir
    #    - sleep(pause_s)
    #    - PATCH /it/{newIt}/intervenant/{matricule} (optionnel)
    #    - PATCH /it/{newIt}/saisirCrit avec lignes[0].typeFacturation=NonFacturable
    # 4. Logger numéro + payload + status
```

## Limitation importante

L'API Artis **ne permet pas** de modifier directement le `typeFacturation` d'un CRIT existant. La seule façon documentée pour passer en NonFacturable une DIT déjà clôturée est ce workflow `reouvrir + nouveau saisirCrit`.

## DIT traitées le 2026-05-06 (référence)

- 100155693 (MEKONG CONSEIL) ✅ via API
- 09402870 ✅ via API
- 100156164, 100157195, 100158518, 100158273 → manuellement dans Artis Web (latence API trop variable)
- 100156222 → skippée (déjà NonFacturable)
