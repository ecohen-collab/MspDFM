# Résoudre le client

## Jamais par le libellé

Le libellé ne fait pas foi : deux clients peuvent porter des noms proches
(incident 9600 retenu au lieu de 9599). La résolution passe par l'index des
domaines, pondéré par le nombre de DIT :

```python
import mail_dit as M
idx = M._index_clients()
idx["emails"]["prenom.nom@client.fr"]   # [{'client': '9599', 'id': 158057, 'nom': '...'}]
idx["domaines"]["client.fr"]            # [('9599', 43), ('27170', 2)] -> le poids compte
```

Puis **vérifier dans Artis** : la raison sociale, la présence de l'interlocuteur
dans la fiche, et l'existence d'un bien INFOGERANCE. Les trois doivent concorder.

L'index peut aussi rendre la **mauvaise personne** pour le bon client : il a
proposé « Mélie Courtois » (216030) là où l'expéditeur était « AURELIE VALENTIN »
(128013). Toujours recouper l'adresse exacte avec
`E.interlocuteurs_artis(code)`.

## L'index est figé au 10/09/2026

Il est construit depuis le corpus des DIT et n'est jamais reconstruit. Un client
récent, ou un domaine peu utilisé, en est absent — ce n'est pas un signe qu'il
n'est pas client.

## Méthode quand le domaine est inconnu

Dans l'ordre :

1. **Chercher le domaine dans la BAL** (`outlook_email_search`, requête plein
   texte). Un mail Microsoft « X approved your granular admin relationship »
   porte le code client en clair (`32911 SALAMANDRE`). Un ancien fil
   `[IT 100174112xx]` donne un numéro de DIT.
2. **Remonter du numéro de DIT au client** : `GET /dit/{numero}` rend
   `client.code` et `client.libelle`. C'est ainsi qu'on a identifié
   ADP COMPTABILITE (8907) depuis la DIT 100174112.
3. **Vérifier** : biens du client, interlocuteurs, cohérence de la raison
   sociale avec la signature du mail.
4. Si rien ne concorde : **laisser le mail en boîte** et le signaler. Ne jamais
   créer une DIT sur un client supposé.

Cas déjà résolus par cette méthode :
- `neris.legal` → SALAMANDRE **32911** (via le mail Microsoft GDAP du 18/09)
- `adpcomptabilite.fr` → AUDIT DEONTOLOGIE PREVENTION COMPTABILITE **8907**
  (via la DIT 100174112)

## Créer un contact

Seulement si l'adresse et le nom de famille ne donnent rien dans la fiche, et
seulement pour une vraie personne : prénom + nom. Jamais un expéditeur
automatique, jamais un nom d'un seul mot. Le nom d'affichage Graph
(`from.emailAddress.name`) donne le prénom complet quand l'adresse ne porte que
des initiales (`mc.laurens@` → « Marie-Cécile LAURENS »).

```python
E.creer_interlocuteur(code_client, "NOM", "Prenom", "adresse@client.fr",
                      titre="Mme", confirmer=True)   # rend {'id': 224638, ...}
```

Toute création de contact est signalée dans le rapport, avec le pid.

**Expéditeur interne @dfm.fr : jamais de contact.** La DIT part sans demandeur
et le rapport le mentionne.
