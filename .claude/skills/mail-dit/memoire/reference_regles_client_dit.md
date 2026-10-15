---
name: reference-regles-client-dit
description: Table REGLES_CLIENT — demandeur impose et personne a contacter, par client
metadata:
  type: reference
---

Certains clients imposent qui figure en **demandeur** d'une DIT, quelle que soit
la personne qui ecrit. C'est dicte par le client, jamais deductible : la table
`REGLES_CLIENT` dans `mail_dit_ecriture.py` l'ecrit noir sur blanc.

Deux leviers par client :
- `demandeur_impose` — adresse du contact mis SYSTEMATIQUEMENT en demandeur ;
- `contact_personne_du_mail` — la personne reellement concernee va dans le champ
  Artis **`contact`** (« personne a contacter »), distinct de `demandeur`.
  Les deux acceptent un `{"id": N}` a la creation (schema `Personne`).

**13401 Me STEPHANE BONIFASSI** (demande d'Elliot, 2026-09-25) : demandeur
toujours **Delphine PRIGENT** `d.prigent@bonifassi-avocats.com` (contact 190156).
La personne a contacter est celle qui ecrit — ou, quand Delphine ecrit pour
quelqu'un d'autre, la personne nommee dans le texte.

**How to apply:** `appliquer_regles_client(prop)` est appelee en tete de
`creer_dit`, donc elle ecrase un demandeur deja pose par la proposition. Pour
ajouter un client, une ligne dans la table suffit. Voir
[[reference-artis-swagger]] pour le schema du corps de creation.
