---
name: feedback-pieces-jointes-jugement
description: "Une pièce jointe se juge sur ce que le TEXTE en dit ; des pièces sans phrase répondent à une demande, elles n'en ouvrent pas une"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c98b99c5-c6ce-4841-a6c7-771856415dad
  modified: 2026-09-17T13:13:09.863Z
---

Deux erreurs commises le 2026-09-17, corrigées par Elliot, même famille :

**DIT 100174112** — la signature du client a été jointe à la place de la vraie
pièce. Le nom (`img-<uuid>.png`), la taille (26 Ko) et le `cid` ne disaient
rien. Le signal utile est **le texte** : une vraie photo s'annonce (« ci-joint »,
« voici la capture », « je vous joins les photos »), une signature jamais.
→ Une image incrustée **petite**, **non annoncée**, dans un mail qui a par
ailleurs un vrai message = signature, on ne la joint pas. Restent jointes :
l'image annoncée, l'image ≥ 40 Ko, et le mail sans texte où l'image EST le
message.

**DIT 100174363** — trois photos, aucune phrase, objet `CHLOÉ - DET GROUPE VIC`
(un nom et un site). Elliot : « je pense qu'un tech a demandé d'envoyer des
photos et du coup ça a créé ce ticket ».
→ **Des pièces sans une phrase sont une RÉPONSE à une demande**, faite par
téléphone ou depuis une autre boîte. Elles rejoignent un ticket, elles n'en
ouvrent pas un.

**Le piège technique commun :** « Envoyé à partir de Outlook pour iOS » fait
sept mots, donc `_ressemble_a_une_phrase` le prenait pour le message. Les pieds
de page mobiles se coupent désormais **même en première ligne** — contrairement
aux formules de politesse, qui ne se coupent qu'à partir de la deuxième (sinon
on vide un mail commençant par « Merci de bien vouloir… »).

**Why:** « c'est surtout pour que tu apprennes de tes erreurs ». Les tickets
fautifs ne sont pas annulés quand ils sont déjà clôturés ; la règle, elle,
change.

Voir [[project_mail_to_dit_artis]] et [[feedback_metier_lire_le_corps]].

**Suite, 2026-09-18.** Des photos sans texte ne restent pas en attente : elles
**rejoignent le ticket concerné**, puis le mail **part chez le dernier
intervenant** de ce ticket (« tu ajoutes la photo à la DIT, et ensuite tu
transfères le mail au dernier intervenant »). Quel ticket : le seul ouvert du
client ; s'il y en a plusieurs, celui qui parle de photos ; sinon on laisse
choisir un humain. `dit_pour_les_pieces()` dans `mail_dit.py`.

**Découverte utile :** une DIT verrouillée en écriture (« ne s'ouvre pas en
modification ») **accepte quand même des documents** — le commentaire passe par
le formulaire web, les pièces par la route REST `/documents`. Ne plus renoncer
aux deux quand l'un échoue.
